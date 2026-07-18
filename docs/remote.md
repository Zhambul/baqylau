# Remote access — the dashboard over the internet (dash.zhambyl.top)

The workflow: laptop stays at home running kitty + the dashboard; you open
`https://dash.zhambyl.top` in any browser (the iPad) and get the full
dashboard — live mirrors, drill-downs, toasts, and (when enabled) the control
plane. This doc is the runbook and the reasoning; the server-side knobs live
in `dashboard/server.py` (`extra_origins`/`READONLY`).

## The threat model decides the architecture

**The control plane is authenticated by network position, not credentials.**
Anyone who can POST to the port can type into your terminal sessions
(`Frontend.send_text`) and launch `claude` (which the login-shell wrapper
expands through your alias — including `--dangerously-skip-permissions`).
Reaching the port IS remote code execution on the laptop. Therefore:

- The server's bind NEVER leaves `127.0.0.1` — there is deliberately no
  "listen on 0.0.0.0" knob, and `CLAUDE_DASH_ORIGINS` is not an exposure
  switch (it only widens the Origin check for a proxy already in front).
- Internet reachability comes from an OUTBOUND connector on the laptop
  (`cloudflared`), so no port is ever forwarded/open.
- Authentication happens at the edge (Cloudflare Access) BEFORE a request
  reaches the tunnel. The public URL alone must never be enough.

Rejected: router port-forward / ngrok / binding a routable interface — all
make the RCE port reachable ahead of (or level with) a single credential.
Tailscale (`tailscale serve`) is the *safer* shape (nothing publicly
addressable at all) and works with the same `CLAUDE_DASH_ORIGINS` knob — it
was passed over here only because the requirement was "any browser, just a
domain name, no client app".

## One-time setup

1. **Domain**: `zhambyl.top` at Porkbun (~$1.63 first year, ~$4.63/yr after;
   WHOIS privacy on by default).
2. **DNS to Cloudflare**: free Cloudflare account → Add a domain →
   `zhambyl.top` → Free plan → set the two assigned nameservers at Porkbun
   (Domain → Nameservers). Tunnel + Access require Cloudflare DNS; the
   registrar does not matter.
3. **cloudflared** on the laptop:

   ```sh
   brew install cloudflared
   cloudflared tunnel login                 # browser auth, picks zhambyl.top
   cloudflared tunnel create claude-dash
   cloudflared tunnel route dns claude-dash dash.zhambyl.top
   ```

   `~/.cloudflared/config.yml`:

   ```yaml
   tunnel: claude-dash
   credentials-file: /Users/z.yermagambet/.cloudflared/<tunnel-id>.json
   ingress:
     - hostname: dash.zhambyl.top
       service: http://127.0.0.1:8377
     - service: http_status:404
   ```

   Persistence (as deployed): a user LaunchAgent at
   `~/Library/LaunchAgents/top.zhambyl.dash-tunnel.plist` running
   `cloudflared tunnel run claude-dash` (`RunAtLoad` + `KeepAlive`, log at
   `~/Library/Logs/dash-tunnel.log`) — starts at login, restarts on crash.
   Why not the alternatives: `brew services start cloudflared` runs the
   binary with NO arguments (it prints "use cloudflared tunnel run" and
   exits — the service shows Loaded but never Running), and
   `sudo cloudflared service install` (a root LaunchDaemon) buys nothing
   here since the dashboard itself needs the user session anyway.
4. **Cloudflare Access** (the non-negotiable part): Zero Trust dashboard →
   Access → Applications → Add self-hosted app for `dash.zhambyl.top`,
   policy = Allow, include = your email(s), one-time PIN (or Google) as the
   login method, session ~30 days. Until this exists the tunnel serves the
   dashboard to ANYONE with the URL — create the Access app before (or
   immediately after) the first `cloudflared` run, never "later".
5. **The Origin knob**: the control-plane guard rejects any Origin that is
   not the local one, so through the proxy the composer/new-session would
   403. Add to `~/.zshenv` (the dashboard inherits the spawning shell's env;
   an autostarted dashboard inherits it via the hook chain the same way):

   ```sh
   export CLAUDE_DASH_ORIGINS="https://dash.zhambyl.top"
   ```

   Restart the dashboard once after setting it.

**Deployed 2026-07-17**: tunnel `claude-dash`
(id `0d364fb6-12b7-4fe5-a611-f0a3b44d5f1b`), hostname `dash.zhambyl.top`,
Access team `fancy-sound-68a3`, one Allow policy (email OTP). Fail-closed
verified from outside: `/`, `/api/sessions`, `/events`, and a POST with the
app's own headers all 302 to the Access login with zero rows reaching the
local server; Universal SSL took ~9 min to issue after the zone went active
(handshake failures until then are normal for a fresh zone).

## Day-to-day

- iPad: Safari → `https://dash.zhambyl.top` → Access login (first time per
  ~30 days) → bookmark. **Add to Home Screen** to make OS notifications
  possible (iPadOS only grants the Notification API to installed web apps;
  in-page toasts work regardless).
- Read-only days: `CLAUDE_DASH_READONLY=1` in the environment kills the
  whole control plane (every POST is 403, reads/SSE untouched) — remote
  eyes, no remote hands.
- Dictation (docs/dashboard.md *Web dictation*) works remotely as-is: the
  HTTPS origin is a secure context (so `getUserMedia` is allowed — a
  plain-http non-localhost origin would refuse the mic), and the audio
  goes iPad → Deepgram directly over wss, never through the tunnel; only
  the tiny token POST rides it (and READONLY days kill that POST exactly
  like the composer it feeds).
- The laptop must stay awake with kitty running: power + lid open +
  "prevent sleep on power" (or Amphetamine/`caffeinate -s`). The dashboard
  reads the live state DBs and the control plane needs the kitty socket.
- Escape hatch: if the dashboard dies while you're out, the tunnel is still
  up — add an Access-protected SSH hostname to the same tunnel (`ssh.zhambyl
  .top` → `ssh://127.0.0.1:22`, browser-rendered terminal in Zero Trust) so
  you can restart it. Without something like this, a wedged server ends the
  remote day.

## Traps

- **SSE through Cloudflare works** (the 15s heartbeat keeps intermediaries
  from timing the streams out) — don't "fix" idle streams by disabling the
  heartbeat.
- **Access covers the browser, not curl scripts**: service tokens exist if
  automation ever needs the API remotely; don't weaken the app policy.
- **`.top` reputation**: some corporate networks flag the TLD. Irrelevant
  for a personal dashboard; would matter only for sending email from the
  domain.
- The `web-send`/`web-launch` audit rows record every control-plane write —
  if a remote action ever looks unfamiliar, the audit DB is the first stop
  (and the Access logs in Zero Trust are the second).
