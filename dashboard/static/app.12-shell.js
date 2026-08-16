"use strict";
// dashboard/static/app.12-shell.js — the APP SHELL: the header's own buttons
// and the page-wide keyboard.
//
// Part of the dashboard SPA — split from the former single app.js into ordered,
// cohesive files (classic scripts share one global scope; load order is set in
// index.html). See app.13-init.js for the boot/init sequence.
//
// Everything here is app-level rather than view-level: it belongs to the page
// frame, not to the list, a session, or a dialog. It lived at the tail of
// app.09-newsession.js — 120 lines of alerts toggle, fullscreen, readline keys
// and session cycling under a filename that says "new session" — because that
// is simply where the header wiring happened to be when the monolith was split.
// A reader looking for the ⌃⇧←/→ handler had no reason to open that file.

$newbtn.onclick = () => openNewSession("");
$statsbtn.onclick = () => { location.hash = "#/stats"; };

/* ---------- global alerts toggle (header ◉/○, next to "+ session") ---------- */
// The ONE master switch over EVERY dashboard notification — the cross-session
// toasts / OS notifs AND the deferred Telegram / web-push alerts
// (docs/dashboard.md *Global alerts toggle*). The state is server-side + durable
// (dashboard/prefs.py `notify-enabled`), so it is cross-device / cross-session
// and covers git worktrees; default ON. OFF overrides the per-session mutes.
// Seeded and kept in sync by the complete global application snapshot.
let notifyOn = true;
function paintNotify() {
  $notifytoggle.textContent = notifyOn ? "◉ alerts" : "○ alerts off";
  $notifytoggle.classList.toggle("off", !notifyOn);
  $notifytoggle.title = notifyOn
    ? "All dashboard alerts ON — click to silence every session"
    : "All dashboard alerts OFF — click to re-enable";
}
paintNotify();
$notifytoggle.onclick = () => {
  const next = !notifyOn;
  postJSON("/api/application/notifications", { enabled: next })
    .then(() => {
      notifyOn = next;
      paintNotify();
      toast("done", next ? "alerts on" : "alerts off",
            next ? "every session can notify" : "all sessions silenced");
    })
    .catch(e => toast("ask", "alerts toggle failed", (e && e.error) || ""));
};

/* ---------- fullscreen toggle ---------- */
// Header ⛶ button: browser Fullscreen API on the whole document, with the
// WebKit-prefixed fallback (iPadOS Safari ships only webkitRequestFullscreen).
// Hidden where neither exists (iPhone Safari). State syncs on the
// fullscreenchange event, not in the click handler, so Esc / the browser's
// own exit path keeps the button honest.
{
  const $fsbtn = document.getElementById("fsbtn");
  const root = document.documentElement;
  const req = root.requestFullscreen || root.webkitRequestFullscreen;
  const exit = document.exitFullscreen || document.webkitExitFullscreen;
  const cur = () => document.fullscreenElement || document.webkitFullscreenElement;
  if (!req) {
    $fsbtn.hidden = true;
  } else {
    $fsbtn.onclick = () => {
      const p = cur() ? exit.call(document) : req.call(root);
      if (p && p.catch) p.catch(() => {});   // e.g. permission denied — no-op
    };
    // `fs-on` is what keeps the ⛶ reachable inside a session view, where the
    // list-page header buttons are hidden (style.css): while fullscreen is
    // ENGAGED this button is the exit, and hiding an exit strands you.
    const sync = () => {
      const on = !!cur();
      $fsbtn.title = on ? "exit fullscreen" : "fullscreen";
      document.body.classList.toggle("fs-on", on);
    };
    sync();
    document.addEventListener("fullscreenchange", sync);
    document.addEventListener("webkitfullscreenchange", sync);
  }
}

/* ---------- readline-style editing keys (terminal-like) ---------- */
// ⌃W deletes the word left of the cursor, ⌃A jumps to line start, ⌃E to line
// end — the terminal/shell editing keys, in every dashboard text box (composer,
// first prompt, directory, filter). One delegated listener: element handlers
// (slash menu, suggest, filter-Esc) run first and none of them claim ⌃-keys.
// Safe to preventDefault on macOS — the browser's own accelerators live on
// ⌘, not ⌃ (and this beats the Cocoa text bindings only where behavior
// differs anyway). Match on e.code so a non-QWERTY layout can't move the
// keys. ⌃W dispatches an input event so autoGrow / the suggest and filter
// oninput hooks see the edit.
document.addEventListener("keydown", (e) => {
  if (!e.ctrlKey || e.altKey || e.metaKey || e.shiftKey) return;
  const t = e.target;
  if (!t || (t.tagName !== "TEXTAREA" &&
             !(t.tagName === "INPUT" && t.type === "text"))) return;
  const v = t.value, s = t.selectionStart, se = t.selectionEnd;
  if (e.code === "KeyW") {          // delete word (or the selection) leftward
    let a = s, b = se;
    if (a === b) {
      while (a > 0 && /\s/.test(v[a - 1])) a--;
      while (a > 0 && !/\s/.test(v[a - 1])) a--;
    }
    t.value = v.slice(0, a) + v.slice(b);
    t.setSelectionRange(a, a);
    t.dispatchEvent(new Event("input"));
  } else if (e.code === "KeyA") {   // start of the current line
    const p = s === 0 ? 0 : v.lastIndexOf("\n", s - 1) + 1;
    t.setSelectionRange(p, p);
  } else if (e.code === "KeyE") {   // end of the current line
    const p = v.indexOf("\n", se);
    t.setSelectionRange(p < 0 ? v.length : p, p < 0 ? v.length : p);
  } else return;
  e.preventDefault();
});

/* ---------- ⌃⇧←/→ cycle through live sessions (the terminal's tab keys) ---------- */
// Mirrors the terminal's next/previous-tab shortcuts: step through the LIVE sessions
// oldest-first (creation order, like the tab bar), wrapping at the ends. From
// the list view or a parked session — nowhere in the cycle — → enters at the
// first (oldest) live session and ← at the last. Deliberately not gated on
// input focus: macOS claims ⌃←/→ (Spaces) but nothing claims ⌃⇧←/→, and in a
// text box it shadows only a selection gesture that already lives on ⌥⇧/⌘⇧.
document.addEventListener("keydown", (e) => {
  if (!e.ctrlKey || !e.shiftKey || e.altKey || e.metaKey) return;
  const dir = e.code === "ArrowRight" ? 1 : e.code === "ArrowLeft" ? -1 : 0;
  if (!dir) return;
  e.preventDefault();
  const live = S.sessions.filter(sessionIsLive)
    .sort((a, b) => orderKey(a) - orderKey(b));
  if (!live.length) return;
  const at = live.findIndex(row => sessionId(row) === S.currentSessionId);
  const to = at < 0 ? (dir > 0 ? 0 : live.length - 1)
                    : (at + dir + live.length) % live.length;
  if (sessionId(live[to]) !== S.currentSessionId)
    location.hash = "#/s/" + encodeURIComponent(sessionId(live[to]));
});
