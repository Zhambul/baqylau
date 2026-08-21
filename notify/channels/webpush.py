# notify/channels/webpush.py — the Web Push channel, whole: the transport
# and the alert it carries.
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
# path. The VAPID keypair is generated ONCE and persisted in the durable
# store (keyed `vapid-keypair`), so every browser stays subscribed to the same
# application-server key across restarts.
from __future__ import annotations

import base64
import dataclasses
import json
import os
import struct
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

import threading

from audit import record as A
from dashboard import config
from domain.ids import SessionId
from domain.preferences import PushSigningKeypair
from notify.channels.alert import NOTHING, OK, alert_text, push_tag
from notify.presence import RoutedSubscription
from repository.contract.preferences import (
    PushSigningKeyRepository,
    PushSubscriptionRepository,
)


@dataclass(frozen=True)
class WebPushAlertPayload:
    """The push body for a fresh alert — `static/sw.js` shows it verbatim
    (title/body/badge), and reads `session_id`/`kind` back into its own
    click-through and the resolve push's tag."""

    title: str
    body: str
    session_id: SessionId
    kind: str | None
    url: str
    badge: int


@dataclass(frozen=True)
class WebPushResolvePayload:
    """The push body that closes a delivered alert — `type` is what
    `static/sw.js` branches on to resolve rather than show a notification."""

    session_id: SessionId
    kind: str | None
    tag: str
    badge: int
    type: Literal["resolve"] = "resolve"


WebPushPayload = WebPushAlertPayload | WebPushResolvePayload


@dataclass
class WebPushHandle:
    """The retraction handle `send_alert` hands back: the subscriptions the
    alert actually went to (a resolve push must reach those, never whichever
    device is most-recently-used by the time it fires) and the tag they were
    shown under."""

    ch: Literal["webpush"] = "webpush"
    session_id: SessionId = SessionId("")
    kind: str | None = None
    subs: list[RoutedSubscription] = dataclasses.field(default_factory=list)
    tag: str = ""


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
VAPID_SUB = os.environ.get("BAQYLAU_DASHBOARD_VAPID_SUB") or "mailto:e.zhambul@gmail.com"
DELIVERY_LIFETIME_SECONDS = 86400                          # how long the push service holds an undelivered message
TOKEN_LIFETIME_SECONDS = 12 * 3600                  # VAPID token lifetime (Apple caps aud-JWTs at 24h)
RECORD_SIZE = 4096                     # aes128gcm record size (rs) — our payloads are tiny


def enabled() -> bool:
    """Whether Web Push can be sent at all (the crypto backend is importable).
    False makes the whole feature invisible: `/api/push/config` reports it off
    and the Notifier never tries to send."""
    return _HAVE_CRYPTO


def _b64u(b: bytes) -> str:
    """base64url without padding (the JOSE / RFC 8291 byte form)."""
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _b64u_dec(s: str) -> bytes:
    """Decode pad-stripped base64url (a subscription's p256dh/auth keys)."""
    s = s + "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s.encode("ascii"))


def _load_keypair(
    push_signing_key_repository: PushSigningKeyRepository | None,
) -> tuple[ec.EllipticCurvePrivateKey | None, str | None]:
    """The persisted VAPID keypair as (private_key_obj, public_b64u), generated
    and stored on first use. One P-256 keypair per machine, stable across
    restarts so already-subscribed browsers keep matching — a rotated key would
    silently orphan every existing subscription. Returns (None, None) if crypto
    is unavailable / the store is unwritable (feature degrades off)."""
    if not _HAVE_CRYPTO or push_signing_key_repository is None:
        return None, None
    stored = push_signing_key_repository.keypair()
    if stored is not None:
        try:
            priv = load_pem_private_key(stored.private_key_pem.encode("ascii"), password=None)
            if not isinstance(priv, ec.EllipticCurvePrivateKey):
                raise TypeError("stored key is not an EC private key")
            return priv, stored.public_key
        except Exception:
            # Corrupt stored record — regenerate below, but NOT silently: the
            # docstring's own warning is that a new key orphans every existing
            # subscription, so every already-subscribed browser goes quiet at
            # once with nothing to point at. This row is the only thing that
            # explains it afterwards.
            A.error("", "webpush keypair (corrupt record — regenerating)", {})
    try:
        priv = ec.generate_private_key(ec.SECP256R1())
        pub_point = priv.public_key().public_bytes(
            Encoding.X962, PublicFormat.UncompressedPoint)   # 65 bytes, 0x04||X||Y
        pub_b64u = _b64u(pub_point)
        pem = priv.private_bytes(
            Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode("ascii")
        push_signing_key_repository.save_keypair(PushSigningKeypair(pem, pub_b64u))
        return priv, pub_b64u
    except Exception:
        A.error("", "webpush keygen")
        return None, None


def public_key(push_signing_key_repository: PushSigningKeyRepository) -> str:
    """The VAPID public key (base64url uncompressed point) the browser passes as
    `applicationServerKey` when it subscribes — '' when the feature is off."""
    _, pub = _load_keypair(push_signing_key_repository)
    return pub or ""


def _vapid_header(endpoint: str, push_signing_key_repository: PushSigningKeyRepository | None) -> str | None:
    """The `Authorization: vapid t=<jwt>, k=<pubkey>` header proving this server
    is the application server the subscription trusts (RFC 8292). The JWT's
    `aud` is the push service ORIGIN (scheme://host of the endpoint), signed
    ES256 with the VAPID private key — JOSE wants the raw r||s signature, so the
    DER the backend returns is unpacked here."""
    priv, pub = _load_keypair(push_signing_key_repository)
    if not priv:
        return None
    u = urlparse(endpoint)
    aud = "%s://%s" % (u.scheme, u.netloc)
    header = _b64u(json.dumps({"typ": "JWT", "alg": "ES256"},
                              separators=(",", ":")).encode())
    claims = _b64u(json.dumps(
        {"aud": aud, "exp": int(time.time()) + TOKEN_LIFETIME_SECONDS, "sub": VAPID_SUB},
        separators=(",", ":")).encode())
    signing_input = ("%s.%s" % (header, claims)).encode("ascii")
    der = priv.sign(signing_input, ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    sig = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    token = "%s.%s" % (header + "." + claims, _b64u(sig))
    return "vapid t=%s, k=%s" % (token, pub)


def _encrypt(payload: bytes, p256dh_b64u: str, auth_b64u: str) -> bytes:
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

    def __init__(self, ok: bool = False, gone: bool = False, status: int = 0,
                 error: str = "") -> None:
        self.ok, self.gone, self.status, self.error = ok, gone, status, error


def deliver(
    routed_subscription: RoutedSubscription,
    payload: WebPushPayload,
    push_signing_key_repository: PushSigningKeyRepository | None,
    ttl: int = DELIVERY_LIFETIME_SECONDS,
) -> Result:
    """Deliver `payload`, JSON-encoded, to one `subscription` (its JSON
    document: {endpoint, keys:{p256dh, auth}}). Never raises — returns a Result.
    Synchronous network I/O, so callers run it OFF the watcher thread."""
    if not _HAVE_CRYPTO:
        return Result(error="no crypto")
    try:
        endpoint = routed_subscription["endpoint"]
        subscription_keys = routed_subscription["keys"]
        body = _encrypt(json.dumps(dataclasses.asdict(payload), ensure_ascii=False).encode("utf-8"),
                        subscription_keys["p256dh"], subscription_keys["auth"])
        auth = _vapid_header(endpoint, push_signing_key_repository)
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
        # rotated) — the canonical prune signal. Apple's 400
        # VapidPkHashMismatch is the same durable condition: this endpoint was
        # subscribed under a different application-server key and can never
        # accept a push from the current installation. Keep the response reason
        # (without subscription/key material) so the audit says more than 400.
        try:
            response_body = e.read().decode("utf-8", "replace")
            response_reason = str(json.loads(response_body).get("reason") or "")
        except Exception:
            response_reason = ""
        gone = e.code in (404, 410) or (
            e.code == 400 and response_reason == "VapidPkHashMismatch"
        )
        return Result(
            gone=gone,
            status=e.code,
            error=response_reason or str(e),
        )
    except Exception as e:
        return Result(error=str(e))


# ------------------------------------------------ the alert this channel carries

def send_alert(
    entry: dict[str, str],
    subs: list[RoutedSubscription],
    badge: int = 0,
    *,
    push_signing_key_repository: PushSigningKeyRepository | None = None,
    push_subscription_repository: PushSubscriptionRepository | None = None,
) -> WebPushHandle | None:
    """Send the on-device alert as a Web Push to `subs` — the subscriptions of
    the ONE device the caller routed to (`presence.route`), NOT every
    subscription, so a session going done/asking buzzes the device you're
    working on, not your iPad and Mac at once. Dispatched on a detached daemon thread: the crypto +
    network round-trips must never stall the 1 s watcher. Best-effort + audited;
    a subscription the push service reports GONE (404/410) is pruned. No-op when
    the crypto backend is missing or `subs` is empty.

    The ROUTING is deliberately not decided here — a transport that picked its
    own destination could not be reused by the retraction, which must reach the
    devices the alert ACTUALLY went to rather than whichever is most-recently-
    used by then. The caller passes the targets and audits `notify-route`.

    Returns a handle (the alert is out on these subscriptions, and a resolve
    push can close it) or None — which the caller reads as "no device to push
    to", the signal that holds Telegram back to the escalation nudge."""
    if not (enabled() and subs):
        return None
    session_id = SessionId(entry.get("session_id") or "")
    title, body, url = alert_text(entry)
    payload = WebPushAlertPayload(title=title, body=body, session_id=session_id,
                                  kind=entry.get("kind"), url=url, badge=badge)
    threading.Thread(target=_webpush_fanout,
                     args=(subs, payload, "send", push_signing_key_repository, push_subscription_repository),
                     daemon=True).start()
    # The subscriptions are the handle: a resolve push has to reach the devices
    # the alert actually went to, NOT whichever device is most-recently-used by
    # then — the banner is on the former.
    return WebPushHandle(session_id=session_id, kind=entry.get("kind"),
                         subs=subs, tag=push_tag(session_id))


def _webpush_fanout(
    subs: list[RoutedSubscription],
    payload: WebPushPayload,
    action: str,
    push_signing_key_repository: PushSigningKeyRepository | None,
    push_subscription_repository: PushSubscriptionRepository | None,
) -> None:
    """The detached fan-out body, shared by the alert and its retraction:
    deliver `payload` to each subscription, audit the outcome (with the target
    `device` — the on-device analog of the route decision), and prune the dead
    ones. Runs off the watcher thread; never raises."""
    for sub in subs:
        try:
            res = deliver(sub, payload, push_signing_key_repository)
        except Exception:
            A.error("", "dashboard webpush %s" % action,
                    {"session_id": payload.session_id})
            continue
        ep = sub.get("endpoint", "") if isinstance(sub, dict) else ""
        dev = sub.get("device") if isinstance(sub, dict) else None
        if res.gone and push_subscription_repository is not None:
            push_subscription_repository.remove(ep)
        A.state_file("", "", "web-push",
                     {"session_id": payload.session_id, "kind": payload.kind,
                      "action": action, "status": res.status,
                      "ok": res.ok, "gone": res.gone,
                      "error": res.error,
                      "badge": payload.badge,
                      "device": dev, "endpoint": ep[:80]})


def retract_alert(
    web_push_handle: WebPushHandle,
    reason: str,
    badge: int = 0,
    *,
    push_signing_key_repository: PushSigningKeyRepository | None = None,
    push_subscription_repository: PushSubscriptionRepository | None = None,
) -> str:
    """Close the delivered banner by pushing a RESOLVE message to the same
    subscriptions; sw.js closes everything under the tag and shows nothing.

    That "shows nothing" is the load-bearing risk of this whole path: an iOS
    subscription is `userVisibleOnly`, and WebKit may answer a push that raises
    no notification with a generic placeholder banner — or, if it keeps
    happening, revoke the subscription. What keeps that survivable is the
    BUDGET: exactly one resolve per delivered alert (the notifier forgets the
    record either way), so the silent:visible ratio is bounded at 1:1 rather
    than being a background chatter channel. BAQYLAU_DASHBOARD_RESOLVE_PUSH=0 turns it
    off, and the page's own foreground sweep (app.01-attention.js) still clears
    stale banners on open — so a refused or dropped resolve degrades to "cleared
    a bit later", never to a wrong badge."""
    if not config.RESOLVE_PUSH:
        return NOTHING
    subs = web_push_handle.subs
    if not subs:
        return NOTHING
    payload = WebPushResolvePayload(session_id=web_push_handle.session_id, kind=web_push_handle.kind,
                                    tag=web_push_handle.tag, badge=badge)
    threading.Thread(target=_webpush_fanout,
                     args=(subs, payload, "resolve", push_signing_key_repository, push_subscription_repository),
                     daemon=True).start()
    return OK                              # dispatched; the thread audits the send
