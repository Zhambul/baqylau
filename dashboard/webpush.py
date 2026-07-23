# dashboard/webpush.py — the Web Push transport (docs/dashboard.md, *Web push*).
#
# The ON-DEVICE analog of the deferred Telegram alert: an installed iOS
# home-screen web app (or a desktop Chrome/Firefox page) can receive a real
# system notification — lock screen, banner, badge — even when the dashboard
# isn't the foreground tab, but ONLY via Web Push. iOS does not support the
# desktop `new Notification()` constructor for an installed web app; the sole
# path is a service worker woken by a push the SERVER sends to the browser's
# push service (Apple's for iOS). This module is that send side: the VAPID
# identity (RFC 8292) and the aes128gcm payload encryption (RFC 8291 over
# RFC 8188) that a push message needs, built on the stdlib + `cryptography`
# (already present — no new pip dependency; `pywebpush` is NOT available here).
#
# Everything degrades to a no-op if `cryptography` is missing (`enabled()` is
# False, the server hides the feature), and nothing raises into the Notifier's
# 1 s watcher loop — a send failure is audited and swallowed like the Telegram
# path. The VAPID keypair is generated ONCE and persisted in the durable prefs
# store (keyed `vapid-keypair`), so every browser stays subscribed to the same
# application-server key across restarts.
import base64
import json
import os
import struct
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from core.noaudit import load_audit

from . import prefs

A = load_audit()   # always-on audit trail; inert stub if it can't import

try:                                   # cryptography is the ONE hard dependency;
    from cryptography.hazmat.primitives import hashes            # absent → feature
    from cryptography.hazmat.primitives.asymmetric import ec     # off, not a crash
    from cryptography.hazmat.primitives.asymmetric.utils import (
        decode_dss_signature)
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from cryptography.hazmat.primitives.serialization import (
        Encoding, NoEncryption, PrivateFormat, PublicFormat,
        load_pem_private_key)
    _HAVE_CRYPTO = True
except Exception:                      # pragma: no cover - environment-dependent
    _HAVE_CRYPTO = False

# The VAPID `sub` claim — a contact for the push service to reach if the app
# misbehaves (RFC 8292 §2.1). A mailto/URL; overridable, defaults to the repo
# owner's address. Not a secret.
VAPID_SUB = os.environ.get("CLAUDE_DASH_VAPID_SUB") or "mailto:e.zhambul@gmail.com"
VAPID_KEY = "vapid-keypair"            # prefs kv key: {"priv": pkcs8-pem, "pub": b64u-point}
TTL_S = 86400                          # how long the push service holds an undelivered message
JWT_TTL_S = 12 * 3600                  # VAPID token lifetime (Apple caps aud-JWTs at 24h)
RECORD_SIZE = 4096                     # aes128gcm record size (rs) — our payloads are tiny


def enabled():
    """Whether Web Push can be sent at all (the crypto backend is importable).
    False makes the whole feature invisible: `/api/push/config` reports it off
    and the Notifier never tries to send."""
    return _HAVE_CRYPTO


def _b64u(b):
    """base64url without padding (the JOSE / RFC 8291 wire form)."""
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _b64u_dec(s):
    """Decode pad-stripped base64url (a subscription's p256dh/auth keys)."""
    s = s + "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s.encode("ascii"))


def _load_keypair():
    """The persisted VAPID keypair as (private_key_obj, public_b64u), generated
    and stored on first use. One P-256 keypair per machine, stable across
    restarts so already-subscribed browsers keep matching — a rotated key would
    silently orphan every existing subscription. Returns (None, None) if crypto
    is unavailable / the store is unwritable (feature degrades off)."""
    if not _HAVE_CRYPTO:
        return None, None
    rec = prefs.get(VAPID_KEY, None)
    if isinstance(rec, dict) and rec.get("priv") and rec.get("pub"):
        try:
            priv = load_pem_private_key(rec["priv"].encode("ascii"), password=None)
            return priv, rec["pub"]
        except Exception:
            pass                       # corrupt record — regenerate below
    try:
        priv = ec.generate_private_key(ec.SECP256R1())
        pub_point = priv.public_key().public_bytes(
            Encoding.X962, PublicFormat.UncompressedPoint)   # 65 bytes, 0x04||X||Y
        pub_b64u = _b64u(pub_point)
        pem = priv.private_bytes(
            Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode("ascii")
        prefs.set(VAPID_KEY, {"priv": pem, "pub": pub_b64u})
        return priv, pub_b64u
    except Exception:
        A.error("", "webpush keygen")
        return None, None


def public_key():
    """The VAPID public key (base64url uncompressed point) the browser passes as
    `applicationServerKey` when it subscribes — '' when the feature is off."""
    _, pub = _load_keypair()
    return pub or ""


def _vapid_header(endpoint):
    """The `Authorization: vapid t=<jwt>, k=<pubkey>` header proving this server
    is the application server the subscription trusts (RFC 8292). The JWT's
    `aud` is the push service ORIGIN (scheme://host of the endpoint), signed
    ES256 with the VAPID private key — JOSE wants the raw r||s signature, so the
    DER the backend returns is unpacked here."""
    priv, pub = _load_keypair()
    if not priv:
        return None
    u = urlparse(endpoint)
    aud = "%s://%s" % (u.scheme, u.netloc)
    header = _b64u(json.dumps({"typ": "JWT", "alg": "ES256"},
                              separators=(",", ":")).encode())
    claims = _b64u(json.dumps(
        {"aud": aud, "exp": int(time.time()) + JWT_TTL_S, "sub": VAPID_SUB},
        separators=(",", ":")).encode())
    signing_input = ("%s.%s" % (header, claims)).encode("ascii")
    der = priv.sign(signing_input, ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    sig = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    token = "%s.%s" % (header + "." + claims, _b64u(sig))
    return "vapid t=%s, k=%s" % (token, pub)


def _encrypt(payload, p256dh_b64u, auth_b64u):
    """Encrypt `payload` (bytes) for a subscription under the aes128gcm content
    encoding (RFC 8188) with the ECDH key agreement of RFC 8291. Returns the
    full message body (its own header carries the salt + our ephemeral public
    key, so the browser can derive the same key). Raises on bad key material —
    the caller audits + swallows."""
    ua_public = _b64u_dec(p256dh_b64u)           # the browser's public key, 65 bytes
    auth_secret = _b64u_dec(auth_b64u)           # the browser's 16-byte auth secret
    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(
        ec.SECP256R1(), ua_public)
    as_priv = ec.generate_private_key(ec.SECP256R1())   # ephemeral, one per message
    as_public = as_priv.public_key().public_bytes(
        Encoding.X962, PublicFormat.UncompressedPoint)
    shared = as_priv.exchange(ec.ECDH(), ua_key)        # 32-byte ECDH secret

    # RFC 8291 §3.4: mix the ECDH secret with the auth secret and BOTH public
    # keys to get the input keying material, then RFC 8188 derives the content
    # key + nonce from a fresh random salt.
    ikm = HKDF(algorithm=hashes.SHA256(), length=32, salt=auth_secret,
               info=b"WebPush: info\x00" + ua_public + as_public).derive(shared)
    salt = os.urandom(16)
    cek = HKDF(algorithm=hashes.SHA256(), length=16, salt=salt,
               info=b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(algorithm=hashes.SHA256(), length=12, salt=salt,
                 info=b"Content-Encoding: nonce\x00").derive(ikm)
    # single record: plaintext then the 0x02 last-record delimiter, AES-128-GCM
    ciphertext = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    header = salt + struct.pack("!L", RECORD_SIZE) + bytes([len(as_public)]) + as_public
    return header + ciphertext


class Result:
    """A single send outcome the caller acts on: `ok` (delivered/accepted),
    `gone` (404/410 — the subscription is dead, prune it), else a soft failure
    (audited, kept — the push service may just be transiently unhappy)."""
    __slots__ = ("ok", "gone", "status", "error")

    def __init__(self, ok=False, gone=False, status=0, error=""):
        self.ok, self.gone, self.status, self.error = ok, gone, status, error


def send(subscription, payload, ttl=TTL_S):
    """Deliver `payload` (a dict, JSON-encoded) to one `subscription` (its wire
    JSON: {endpoint, keys:{p256dh, auth}}). Never raises — returns a Result.
    Synchronous network I/O, so callers run it OFF the watcher thread."""
    if not _HAVE_CRYPTO:
        return Result(error="no crypto")
    try:
        endpoint = subscription["endpoint"]
        keys = subscription.get("keys") or {}
        body = _encrypt(json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                        keys["p256dh"], keys["auth"])
        auth = _vapid_header(endpoint)
        if not auth:
            return Result(error="no vapid")
        req = urllib.request.Request(endpoint, data=body, method="POST", headers={
            "Content-Encoding": "aes128gcm",
            "Content-Type": "application/octet-stream",
            "TTL": str(ttl),
            "Urgency": "high",
            "Authorization": auth,
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            return Result(ok=True, status=resp.status)
    except urllib.error.HTTPError as e:
        # 404/410 = the browser dropped the subscription (uninstalled, cleared,
        # rotated) — the canonical prune signal. 413/429/5xx are transient.
        return Result(gone=e.code in (404, 410), status=e.code, error=str(e))
    except Exception as e:
        return Result(error=str(e))
