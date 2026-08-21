# dashboard/dictate.py — the Deepgram side of web dictation. The ONE owner of the dictation vocabulary: the key/keyterms
# file locations, the grant call, and the fully-assembled live-listen URL.
#
# The browser talks to Deepgram DIRECTLY over WebSocket — the stdlib dashboard
# server can't speak WS in either direction and must never see audio (it stays
# a read-only thing that mints tokens). So the server's whole job here is:
# read the long-lived API key from disk, trade it for a ~30s single-purpose
# grant JWT (POST /v1/auth/grant), and hand the page that JWT plus the listen
# URL with every server-decided parameter baked in (model, formatting,
# keyterms) — the client contributes only its AudioContext sample rate. The
# API key itself never leaves this process and never appears in a response,
# an audit row, or an error detail.
#
# Env knobs (read at CALL time, not import — the in-process test server flips
# them per-test): BAQYLAU_DICTATION_KEY_FILE / BAQYLAU_DICTATION_KEYTERMS_FILE
# override the file locations; BAQYLAU_DICTATION_GRANT_URL points the grant call
# at a fake server in tests (and is why grant() is testable hermetically).
import json
import os
import urllib.request
from typing import TypedDict, cast
from urllib.parse import quote

DEFAULT_KEY_FILE = "~/.config/deepgram/api-key"
DEFAULT_KEYTERMS_FILE = "~/.config/deepgram/keyterms"
DEEPGRAM_GRANT_URL = "https://api.deepgram.com/v1/auth/grant"
DEEPGRAM_LISTEN_URL = "wss://api.deepgram.com/v1/listen"

GRANT_TIMEOUT_SECONDS = 5.0    # the grant is one small HTTPS POST; fail fast so a
#                          Deepgram outage can't hold a server thread long
MODEL = "nova-3"         # keyterm prompting requires nova-3
LANGUAGE = "en"
# The browser sends the rate it will actually SEND AT THE HTTP BOUNDARY, which since
# 2026-07-27 is Deepgram's own 16 kHz model rate, not the AudioContext's native
# one: the worklet resamples (hardware already at or below 16k passes through),
# because native-rate PCM is 768 kbps of sustained uplink and an iPad over the
# tunnel could not hold that up — the send queue backed up and the native history
# fell further behind with every sentence.
# The range stays a sanity bound, not a config: the client is trusted to
# declare what it sends, but anything outside hardware reality is a bogus
# request. The rate is audited on every mint, so a regression to native-rate
# audio is visible in the `web-dictate` rows.
SAMPLE_RATE_MIN, SAMPLE_RATE_MAX = 8000, 384000
KEYTERMS_MAX = 100       # keep the URL sane; Deepgram tolerates ~100s of terms


class GrantResponse(TypedDict):
    """Deepgram's POST /v1/auth/grant body: the browser token and its lifetime."""
    access_token: str
    expires_in: int


def key_file() -> str:
    return os.path.expanduser(
        os.environ.get("BAQYLAU_DICTATION_KEY_FILE") or DEFAULT_KEY_FILE)


def available() -> bool:
    """Feature probe: a readable, non-empty key file. The mic button renders
    iff this is true — no key means the feature is invisible, never broken."""
    try:
        return bool(_read(key_file()))
    except Exception:
        # not just OSError: a non-UTF-8 key file raises UnicodeDecodeError
        # (a ValueError) out of _read, and the contract is "never broken" —
        # a malformed key file hides the mic button, it doesn't 500 the probe.
        return False


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read().strip()


def keyterms() -> list[str]:
    """The user-global dictation vocabulary."""
    files = [os.path.expanduser(
        os.environ.get("BAQYLAU_DICTATION_KEYTERMS_FILE")
        or DEFAULT_KEYTERMS_FILE)]
    terms: list[str] = []
    seen: set[str] = set()
    for path in files:
        try:
            raw = _read(path)
        except OSError:
            continue
        for line in raw.splitlines():
            line = line.strip()
            if line and not line.startswith("#") and line not in seen:
                seen.add(line)
                terms.append(line)
    return terms[:KEYTERMS_MAX]


def grant(lifetime_seconds: int | None = None) -> GrantResponse:
    """Trade the on-disk API key for a short-lived browser token: Deepgram's
    POST /v1/auth/grant → {"access_token", "expires_in"}. Raises on any
    failure (no key, HTTP error, malformed response) — the route turns that
    into a JSON error + audit rows; nothing here writes state."""
    key = _read(key_file())
    url = os.environ.get("BAQYLAU_DICTATION_GRANT_URL") or DEEPGRAM_GRANT_URL
    body = (
        json.dumps({"ttl_seconds": lifetime_seconds}).encode()
        if lifetime_seconds
        else b"{}"
    )
    request = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Authorization": "Token " + key,
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=GRANT_TIMEOUT_SECONDS) as response:
        document = json.loads(response.read().decode("utf-8"))
    if not isinstance(document, dict) or not document.get("access_token"):
        raise ValueError("grant response missing access_token")
    return cast(GrantResponse, document)


def ws_url(sample_rate: int, terms: tuple[str, ...] | list[str] = ()) -> str:
    """The full live-listen URL the browser connects to, every parameter
    server-decided: nova-3 + interim results (the whole point — text lands in
    the textarea as you speak), smart_format for punctuation, raw linear16
    PCM at the rate the client says it will SEND, one keyterm= per vocabulary term — the caller passes
    the keyterms() result so the merged list is read once and the audit
    count matches what actually rode the URL."""
    base = os.environ.get("BAQYLAU_DICTATION_LISTEN_URL") or DEEPGRAM_LISTEN_URL
    params = [
        ("model", MODEL),
        ("language", LANGUAGE),
        ("smart_format", "true"),
        ("interim_results", "true"),
        ("encoding", "linear16"),
        ("sample_rate", str(int(sample_rate))),
        ("channels", "1"),
    ] + [("keyterm", t) for t in terms]
    return base + "?" + "&".join(
        "%s=%s" % (k, quote(v, safe="")) for k, v in params)
