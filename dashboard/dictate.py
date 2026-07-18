# dashboard/dictate.py — the Deepgram side of web dictation (docs/dashboard.md,
# *Web dictation*). The ONE owner of the dictation vocabulary: the key/keyterms
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
# them per-test): CLAUDE_DICTATE_KEY_FILE / CLAUDE_DICTATE_KEYTERMS_FILE
# override the file locations; CLAUDE_DICTATE_GRANT_URL points the grant call
# at a fake server in tests (and is why grant() is testable hermetically).
import json
import os
import urllib.request
from urllib.parse import quote

DEFAULT_KEY_FILE = "~/.config/deepgram/api-key"
DEFAULT_KEYTERMS_FILE = "~/.config/deepgram/keyterms"
DEEPGRAM_GRANT_URL = "https://api.deepgram.com/v1/auth/grant"
DEEPGRAM_LISTEN_URL = "wss://api.deepgram.com/v1/listen"

GRANT_TIMEOUT_S = 5.0    # the grant is one small HTTPS POST; fail fast so a
#                          Deepgram outage can't hold a server thread long
MODEL = "nova-3"         # keyterm prompting requires nova-3
LANGUAGE = "en"
# The browser sends its AudioContext's NATIVE rate (no client-side resampling
# — Float32→Int16 is the only transform); anything outside hardware reality
# is a bogus request, not a config to honor.
SAMPLE_RATE_MIN, SAMPLE_RATE_MAX = 8000, 384000
KEYTERMS_MAX = 100       # keep the URL sane; Deepgram tolerates ~100s of terms


def key_file():
    return os.path.expanduser(
        os.environ.get("CLAUDE_DICTATE_KEY_FILE") or DEFAULT_KEY_FILE)


def available():
    """Feature probe: a readable, non-empty key file. The mic button renders
    iff this is true — no key means the feature is invisible, never broken."""
    try:
        return bool(_read(key_file()))
    except OSError:
        return False


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read().strip()


def keyterms():
    """The user's dictation vocabulary (~/.config/deepgram/keyterms, one term
    per line, #-comments and blanks dropped) — jargon Nova-3 should bias
    toward ("scorebar", "tailer", …). Missing file = no keyterms, never an
    error; capped at KEYTERMS_MAX to keep the listen URL bounded."""
    try:
        raw = _read(os.path.expanduser(
            os.environ.get("CLAUDE_DICTATE_KEYTERMS_FILE")
            or DEFAULT_KEYTERMS_FILE))
    except OSError:
        return []
    terms = []
    for line in raw.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            terms.append(line)
    return terms[:KEYTERMS_MAX]


def grant(ttl_s=None):
    """Trade the on-disk API key for a short-lived browser token: Deepgram's
    POST /v1/auth/grant → {"access_token", "expires_in"}. Raises on any
    failure (no key, HTTP error, malformed response) — the route turns that
    into a JSON error + audit rows; nothing here writes state."""
    key = _read(key_file())
    url = os.environ.get("CLAUDE_DICTATE_GRANT_URL") or DEEPGRAM_GRANT_URL
    body = json.dumps({"ttl_seconds": ttl_s}).encode() if ttl_s else b"{}"
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Authorization": "Token " + key,
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=GRANT_TIMEOUT_S) as r:
        out = json.loads(r.read().decode("utf-8"))
    if not isinstance(out, dict) or not out.get("access_token"):
        raise ValueError("grant response missing access_token")
    return out


def ws_url(sample_rate):
    """The full live-listen URL the browser connects to, every parameter
    server-decided: nova-3 + interim results (the whole point — text lands in
    the textarea as you speak), smart_format for punctuation, raw linear16
    PCM at the client's native rate (AudioWorklet output — MediaRecorder is
    rejected in docs/dashboard.md: iPad Safari emits mp4/AAC, which Deepgram
    streaming refuses), one keyterm= per vocabulary line."""
    base = os.environ.get("CLAUDE_DICTATE_LISTEN_URL") or DEEPGRAM_LISTEN_URL
    params = [
        ("model", MODEL),
        ("language", LANGUAGE),
        ("smart_format", "true"),
        ("interim_results", "true"),
        ("encoding", "linear16"),
        ("sample_rate", str(int(sample_rate))),
        ("channels", "1"),
    ] + [("keyterm", t) for t in keyterms()]
    return base + "?" + "&".join(
        "%s=%s" % (k, quote(v, safe="")) for k, v in params)
