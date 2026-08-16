"use strict";
// Part of the dashboard SPA — split from the former single app.js into ordered,
// cohesive files (classic scripts share one global scope; load order is set in
// index.html). See app.13-init.js for the boot/init sequence.

let canonicalRequestSequence = 0;
function sessionControl(sessionId, controlName, fields, options) {
  if (!sessionId) return Promise.reject({ error: "no session selected" });
  const body = Object.assign({
    control_name: controlName,
    request_id: String(Date.now()) + "-" + String(++canonicalRequestSequence),
  }, fields || {});
  const tag = (options || {}).audit;
  return postJSON(
    "/api/sessions/" + encodeURIComponent(sessionId) + "/controls",
    body,
    options || {}
  ).then(result => {
    // An `indeterminate` outcome is a FAILURE the transport cannot see: the
    // request arrived and the gesture was attempted, but the harness never
    // confirmed it — a screen driver that bailed, a paste the TUI refused. It is
    // served as HTTP 202, which `r.ok` calls success, so every gesture used to
    // resolve happily while nothing had changed at the terminal (measured, codex
    // session 01a0037d: a model switch left the picker open on screen and the
    // page said it worked). 202 stays correct at the transport layer and is used
    // by other endpoints, so the verdict is read from the OUTCOME here rather
    // than from the status code in postJSON.
    if (result && result.status === "indeterminate") {
      if (tag) clog(sessionId, tag + ".unconfirmed", { reason: result.reason || "" });
      return Promise.reject(Object.assign({}, result,
        { error: result.reason || "the session did not confirm it" }));
    }
    return result;
  });
}

function canonicalControl(controlName, fields, options) {
  return sessionControl(S.currentSessionId, controlName, fields, options);
}

function migrateSession() {
  if (!S.currentSessionId) return Promise.resolve();
  return canonicalControl("migrate_account", {},
                  { audit: "migrate" })
    .then(r => toast("done", "migrating",
                     "resuming on " + ((r && r.to) || "another account")))
    .catch(e => toast("ask", "migrate failed", (e && e.error) || ""));
}

// Returns the POST promise for the same in-flight button lock (a double-tap
// mid round-trip would send Escape to the terminal twice).
function interruptSession() {
  const meta = (S.sessionView && S.sessionView.meta) || {};
  if (!S.currentSessionId || !meta.live || !meta.kitty_window_id) return Promise.resolve();
  // a red "asking you" tab means a MODAL DIALOG is open (ask/plan/permission).
  // An Esc there DECLINES the dialog, it doesn't interrupt a turn — sending one
  // once killed the answer the user was giving via the ask card. Respond
  // through the card instead (the server 409s as the backstop, but the toast is
  // the honest UX; docs/tab-colors.md).
  if (liveTab() === "awaiting_attention") {
    toast("done", "a question is waiting",
          "answer it in the card above — Esc would decline it");
    return Promise.resolve();
  }
  return canonicalControl("interrupt", {},
                  { audit: "interrupt" })
    .then(r => {
      // `restored` = Claude Code handed the message back to its input box
      // (an early-enough interrupt discards the prompt instead of keeping
      // partial work — the terminal decides, the server READ the box). Mirror
      // it into the composer so the web side doesn't lose the text.
      if (r && r.restored_text) {
        applyTakeBack(r.restored_text);
        toast("done", "took it back", "message restored below — edit and resend");
      } else if (r && r.queued) {
        // the stop handed the turn over to the message you had queued — Claude
        // Code delivers it the instant the Esc lands (the terminal's own
        // behavior). The session is BUSY again, so no your-turn flip below.
        toast("done", "interrupted", "your queued message is running now");
      } else if (BUSY_TABS.includes(r && r.tab)) {
        toast("done", "interrupted", "Esc sent to the session");
      } else {
        toast("done", "Esc sent", "double-press Esc for rewind");
      }
      // an interrupt ENDS the turn → it's your turn now. But Claude Code fires
      // NO hook on interrupt, so the tab can sit stale-busy (from EXECUTING not
      // even the escape-recheck spawns), leaving the composer button stuck on
      // "queue" when a plain "send" is what actually happens. Flip send/stop/
      // quick out of the busy state NOW; a real tab change — the escape-recheck's
      // green, or the next prompt — reconciles, and if the turn somehow kept
      // going that next tab event flips it right back to "queue".
      const sessionView = S.sessionView;
      if (sessionView && !(r && r.queued) && BUSY_TABS.includes(r && r.tab)) {
        const yourTurn = "awaiting_response";   // green, not a QUEUE_TAB
        if (sessionView.composerMode) sessionView.composerMode(yourTurn);
        if (sessionView.stopMode) sessionView.stopMode(yourTurn);
        if (sessionView.quickMode) sessionView.quickMode(yourTurn);
      }
    })
    .catch(e => toast("ask", "interrupt failed", (e && e.error) || ""));
}

// REWIND: enter picking mode (click a message below, choose what to restore).
// Idle only — mid-turn there is nothing to rewind TO yet, and the server 409s.
function rewindSession() {
  const meta = (S.sessionView && S.sessionView.meta) || {};
  if (!S.currentSessionId || !meta.live || !meta.kitty_window_id) return;
  // red "asking you" tab: a dialog is open — a rewind (/rewind) would land in
  // it and dismiss or corrupt it. Answer via the card instead.
  if (liveTab() === "awaiting_attention") {
    toast("done", "a question is waiting",
          "answer it in the card above first");
    return;
  }
  if (BUSY_TABS.includes(liveTab())) {
    toast("done", "a turn is running", "stop it first, then rewind");
    return;
  }
  rewindPickMode(true);
}

// The live tab state of the open session (the SSE `tab` event patches the
// row; meta.tab is the initial fallback).
function liveTab() {
  const row = S.sessions.find(item => sessionId(item) === S.currentSessionId);
  return (row ? sessionTabState(row) : "")
      || ((S.sessionView && S.sessionView.meta && S.sessionView.meta.tab) || "");
}

// The web side of an interrupt that TOOK THE MESSAGE BACK: the restored text
// goes into the composer for editing and the discarded prompt bubble leaves the
// feed. Claude Code un-renders it in kitty the same way, and it is genuinely
// gone from the conversation — it stays in the transcript FILE, but orphaned
// (re-parented around), which transcript._dead_uuids prunes on the next full
// read, so this removal is what the server would say anyway, just sooner. The
// NEXT composer send clears the TUI's restored draft and resends as an atomic
// paste (clear_draft → the server's Ctrl+U/K + bracketed paste, the only
// reliable way to replace the draft: a raw send drops leading bytes after it).
function applyTakeBack(restored) {
  const sessionView = S.sessionView;
  if (!sessionView) return;
  // the taken-back bubble is the newest prompt YOU sent (items prepend, so it is
  // the FIRST in the feed) — `:not(.sys)` because an INJECTED turn can be newer
  // than it (teammate mail, a Stop hook's feedback: they land as prompt-shaped
  // bubbles too) and removing that one would delete the wrong thing
  const feed = sessionView.stream;
  const bubble = feed && feed.querySelector(".msg.prompt:not(.sys)");
  if (bubble) bubble.remove();
  prefillComposer(restored);
}

// The shared tail of an interrupt's take-back and web rewind: the TUI now holds the
// restored prompt as its input draft, so prefill OUR composer with the same
// text (only when empty — never clobber what you were typing) and make the
// next send replace the TUI draft (clear_draft) instead of appending to it.
function prefillComposer(restored) {
  const sessionView = S.sessionView;
  if (!sessionView) return;
  const ta = sessionView.composer;
  if (ta && restored && !ta.value.trim()) {
    ta.value = restored;
    autoGrow(ta);
    ta.focus();
    ta.setSelectionRange(ta.value.length, ta.value.length);
    // PERSIST it. Setting .value in code fires no `input` event, so the
    // composer's own draft save never ran and a reload dropped the restored
    // message on the floor — while the transcript, which had not yet forked,
    // showed it again ("it disappeared from the box and reappeared in the
    // chat", 2026-07-25). Same stash a typed character would have written.
    saveComposerDraft(sessionView, S.currentSessionId);
  }
  sessionView.clearDraftNext = true;
}

/* ---------- quick commands (docs/dashboard.md, *Web quick commands*) ----------
   The scoreboard's SECOND action row (under stop/cancel/rewind/close):
   compact + the model and effort pickers. Each sends one of the TUI's OWN
   slash commands through POST /command (fixed vocabulary server-side, never
   free text); mid-turn it queues like any typed input (`queued` in the
   reply), and a red asking-you tab disables the row — pasted text would land
   in the open dialog (the server 409s as the backstop). */

// The ✦/✧ menu choices for the CURRENT session — the OWNING host's own list
// (meta.model_choices / effort_choices, the flat lists the server derives from
// that host's HostControl). So a codex session's pickers offer codex
// models/levels without a codex-specific menu here — and a host that declares
// none gets an EMPTY menu rather than Claude Code's, which is what the
// hardcoded fallback pair that used to sit here would have handed it.
function hostChoices(kind) {
  const meta = S.sessionView && S.sessionView.meta;
  if (!meta) return [];
  // The ✧ list is the CURRENT model's, since efforts are a field of ModelOption.
  // meta.effort_choices is the default model's list, used only until the running
  // model is known.
  if (kind === "effort") {
    const perModel = (meta.model_efforts || {})[curModelFamily()];
    const list = Array.isArray(perModel) ? perModel : meta.effort_choices;
    return Array.isArray(list) ? list.map(v => [v, v]) : [];
  }
  const list = Array.isArray(meta[kind + "_choices"]) ? meta[kind + "_choices"] : null;
  return list ? list.map(v => [v, v]) : [];
}

function closeQuickMenu() {
  document.querySelectorAll(".qcmenu").forEach(m => m.remove());
}

function sendQuickCmd(cmd, arg) {
  if (!S.currentSessionId) return;
  const label = "/" + cmd + (arg ? " " + arg : "");
  const controlName = cmd === "model" ? "select_model"
    : cmd === "effort" ? "select_effort" : "compact";
  const fields = controlName === "select_model" ? { model_id: arg }
    : controlName === "select_effort" ? { effort: arg } : {};
  canonicalControl(controlName, fields,
                   { audit: "command", auditData: { cmd } })
    .then(r => {
      // `confirm`: the server auto-answers the TUI's switch-confirm menu
      // when /model // /effort opens one (the prompt-cache warning)
      const confirmation = r.confirmation || null;
      const sub = r.queued ? "queued — runs when the turn ends"
        : confirmation === "failed"
          ? "sent — answer the confirm dialog in the terminal"
          : confirmation === "confirmed" ? "switched (dialog confirmed)" : "sent";
      toast(confirmation === "failed" ? "ask" : "done", label, sub);
      if (!r.queued && confirmation !== "failed") applyQuickSwitch(cmd, arg);
    })
    .catch(e => toast("ask", label + " failed", (e && e.error) || ""));
}

// A dropdown anchored inside the button's .qcwrap, in the SAME visual
// language as the new-session form's dropdown() (.nsdropmenu/.nsdropitem —
// the dashboard's one picker look); `cur` marks the current value's row .sel
// like dropdown() does. A second click on the same button toggles it closed;
// the document click-away handler below closes it from anywhere outside the
// wrap, Esc via the document keydown handler.
function openQuickMenu(wrap, cmd, choices, cur) {
  const again = wrap.querySelector(".qcmenu");
  closeQuickMenu();
  if (again) return;
  const menu = el("div", "nsdropmenu qcmenu");
  for (const [val, label] of choices) {
    const row = el("div", "nsdropitem" + (val === cur ? " sel" : ""), label);
    row.onclick = () => { closeQuickMenu(); sendQuickCmd(cmd, val); };
    menu.append(row);
  }
  wrap.append(menu);
}
document.addEventListener("click", (e) => {
  if (!e.target.closest(".qcwrap")) closeQuickMenu();
});

// Optimistic button refresh after an APPLIED switch (not queued, confirm
// menu not stuck): the ctx probe only learns a model change on the next
// assistant turn, and a settings write reaches the SSE `effort` push on the
// slow cadence — the successful click itself is the freshest signal.
function applyQuickSwitch(cmd, arg) {
  const sessionView = S.sessionView;
  if (!sessionView) return;
  if (cmd === "model") {
    // `arg` is a catalog model id. The next canonical model reference confirms
    // it through selection_id; the browser never parses the native id.
    sessionView.pendingModel = arg;
    if (sessionView.modelBtn) setModelBtn(sessionView.modelBtn);
  } else if (cmd === "effort") {
    if (sessionView.meta) sessionView.meta.effort = arg;
    if (sessionView.effortBtn) setEffortBtn(sessionView.effortBtn);
  }
}

// The menu row the session is currently on is translated by its plugin and
// carried as canonical model_selection. Presentation never parses native ids.
function curModelFamily() {
  const sessionView = S.sessionView;
  if (sessionView && sessionView.pendingModel) return sessionView.pendingModel;
  return curModelRef(sessionView).selection || "";
}

// The session's current model for the ✦ button: the last `model.changed` (meta,
// refreshed by the SSE `activity` snapshot), falling back to the ctx probe.
//
// The session model is preferred because it moves AT THE MOMENT of the switch: a
// `/model opus` turn emits `model.changed reason="selected"` from the command
// itself. The ctx probe cannot — its model describes the window the token figure
// was MEASURED against, so it only moves on the next assistant record, which is
// why this button used to sit on the old model until the session next replied
// (reported: "the model card did not change after model was changed").
//
// The fallback still matters: a `selected` event carries the selection ALIAS the
// transcript had ("opus"), and the probe's later `reported_by_harness` event
// carries the resolved id ("opus-5"). So the label sharpens on the next turn
// rather than waiting for it.
function curModelRef(sessionView) {
  const meta = (sessionView && sessionView.meta) || null;
  if (meta && meta.model_selection)
    return { short: meta.model_short || meta.model, selection: meta.model_selection };
  const contextWindow = (sessionView && (sessionView.contextWindow || (meta && meta.contextWindow))) || null;
  return contextWindow
    ? { short: contextWindow.model_short, selection: contextWindow.model_selection }
    : { short: "", selection: null };
}

// The model button's label carries the session's CURRENT model (curModelRef —
// the last `model.changed`, ctx probe as fallback), so the row doubles as a live
// model indicator. A just-switched model shows as pendingModel until the session
// model's family confirms it, which now happens on the switch itself rather than
// on the next assistant turn.
// A `model_refusal_fallback` (meta/SSE `fallback` — a safeguard refusal
// rerouted the session to a fallback model, no hook fires) appends a ⚠ whose
// own native title carries Claude Code's full notice; the server serves the
// record only while the ctx model still IS the fallback model, so a /model
// switch (away or back) retires the icon on the next probe. A pendingModel
// (just-clicked switch) hides it optimistically for the same reason.
function setModelBtn(btn) {
  const sessionView = S.sessionView;
  const current = curModelRef(sessionView);
  const m = shortModel(current.short);
  if (sessionView && sessionView.pendingModel) {
    if (current.selection === sessionView.pendingModel)
      sessionView.pendingModel = null;
    else { btn.textContent = "✦ " + sessionView.pendingModel + " ▾"; return; }
  }
  btn.textContent = "✦ " + (m || "model") + " ▾";
  const fb = sessionView && sessionView.meta && sessionView.meta.fallback;
  if (fb) {
    const w = el("span", "fbwarn", "⚠");
    // both ids in the OWNING host's spelling, served beside the raw ones
    w.title = "fell back " + (fb.from_short || fb.from) + " → "
      + (fb.to_short || fb.to)
      + (fb.category ? " (" + fb.category + ")" : "")
      + (fb.reason ? "\n\n" + fb.reason : "");
    btn.append(w);
  }
}

// The effort button's label carries the session's CURRENT effort (meta/SSE
// `effort` — the summary's last `effort.changed`: a launch-time --effort or
// an applied /effort); bare "effort" when the session never selected one.
function setEffortBtn(btn) {
  const meta = (S.sessionView && S.sessionView.meta) || {};
  btn.textContent = "✧ " + (meta.effort || "effort") + " ▾";
}

/* ---------- full web rewind (docs/dashboard.md, *Web rewind*) ---------- */
// The feed's prompt bubbles ARE the checkpoint list: every user prompt is a
// checkpoint in Claude Code, so "rewind to a specific message" is a click on
// its bubble — a ↶ button each .msg.prompt carries (hover-revealed; pick mode
// reveals them all). The chosen mode POSTs /rewind-to, where the server
// drives Claude Code's own rewind menu in the session's window with screen-
// verified key events (plugins/claude_code/rewindmenu.py) — nothing to do in kitty.

// Picking mode: the idle meaning of ↶ rewind / double-Esc. Reveals every
// bubble's ↶ and waits for a click; Esc or a second toggle leaves.
function rewindPickMode(on) {
  const sessionView = S.sessionView;
  if (!sessionView || !sessionView.stream) return;
  // the ↶ header button is cap-gated; so are the two ways INTO pick mode that
  // bypass it (the Esc gesture, a bubble's own ↶) — a host with no rewind must
  // not be able to open a menu whose every row the server 409s
  if (!capOk(sessionView.meta, "rewind")) return;
  const want = on === undefined ? !sessionView.stream.classList.contains("rwpick") : !!on;
  sessionView.stream.classList.toggle("rwpick", want);
  if (want)
    toast("done", "rewind", "pick a message to rewind to (Esc to leave)");
  else closeRewindMenu();
}

function inRewindPick() {
  const st = S.sessionView && S.sessionView.stream;
  return !!(st && st.classList.contains("rwpick"));
}

function closeRewindMenu() {
  // :not(.qcmenu) — the quick-command pickers once reused the .rwmenu class
  // and the feed delegation handler below (which calls this on ANY click)
  // removed them in the same click that opened them; they are .nsdropmenu-
  // styled now, but keep the exclusion so a future .rwmenu-classed menu with
  // its own lifecycle can't regress the same way
  document.querySelectorAll(".rwmenu:not(.qcmenu)").forEach(m => m.remove());
}

// The per-message mode menu — the OWNING host's own restore modes and the words
// for them (meta.rewind_modes: [{mode, label}], HostControl.rewind_modes +
// rewind_mode_label, whose single owner is the table the on-screen menu is
// matched against). Claude Code's three, minus the summarize pair (a web
// summarize would need the composer anyway), used to be spelled here.
function rwModes() {
  const list = S.sessionView && S.sessionView.meta && S.sessionView.meta.rewind_modes;
  return Array.isArray(list) ? list.map(r => [r.mode, r.label || r.mode]) : [];
}

function openRewindMenu(bubble) {
  closeRewindMenu();
  // same cap gate as the header button and pick mode: no rewind, no menu (and
  // with no modes served there would be nothing in it but "never mind")
  if (!capOk(S.sessionView && S.sessionView.meta, "rewind")) return;
  const menu = el("div", "rwmenu");
  menu.append(el("div", "rwhead", "rewind to before this message?"));
  for (const [mode, label] of rwModes()) {
    const b = el("button", "rwopt", label);
    b.onclick = (e) => { e.stopPropagation(); doRewindTo(bubble, mode, menu); };
    menu.append(b);
  }
  const x = el("button", "rwopt rwx", "never mind");
  x.onclick = (e) => { e.stopPropagation(); closeRewindMenu(); };
  menu.append(x);
  bubble.append(menu);
}

function doRewindTo(bubble, mode, menu) {
  const meta = (S.sessionView && S.sessionView.meta) || {};
  if (!S.currentSessionId || !meta.live || !meta.kitty_window_id) return;
  if (BUSY_TABS.includes(liveTab())) {
    toast("ask", "session is busy", "stop the turn first");
    return;
  }
  const text = bubble.dataset.txt || "";
  if (!text.trim()) return;
  if (!bubble.dataset.messageId) {
    toast("ask", "rewind unavailable", "this message has no canonical identity");
    return;
  }
  // the jump hint: the target's `up`-press distance from the menu's
  // "(current)" cursor start = newer prompts + 1. Newer bubbles precede it
  // in the feed (newest-first); a stale count only slows the server's
  // text-verified scan, never mis-selects.
  let ups = 1;
  for (let n = bubble.previousElementSibling; n; n = n.previousElementSibling)
    if (n.classList && n.classList.contains("prompt")) ups++;
  menu.querySelectorAll("button").forEach(b => b.disabled = true);
  toast("done", "rewinding…", "driving the checkpoint menu");
  canonicalControl("apply_rewind", {
    target_message_id: bubble.dataset.messageId,
    target_text: text,
    newer_prompt_count: Math.max(0, ups - 1),
    mode,
  }, { audit: "rewind-to", auditData: { mode, ups } })
    .then(r => {
      rewindPickMode(false);
      if (r && r.restored_text) {
        applyRewind(bubble, r.restored_text);
        // degraded: "both" at a no-code-change checkpoint — the code was
        // already in that state, so only the conversation had to move
        toast("done", "rewound", r.degraded
              ? "no code changes there — conversation restored, edit below"
              : mode === "both"
              ? "code + conversation restored — edit and resend below"
              : "conversation restored — edit and resend below");
      } else {
        closeRewindMenu();
        toast("done", "code restored", "conversation kept");
      }
    })
    .catch(e => {
      menu.querySelectorAll("button").forEach(b => b.disabled = false);
      toast("ask", "rewind failed", (e && e.error) || "");
    });
}

// A conversation restore un-renders everything from the target prompt on —
// kitty's TUI does the same. Optimistic like applyTakeBack: the transcript
// still holds the dead branch (a rewind writes nothing until the next send
// forks it), so a full reload re-shows it; this view matches the terminal.
function applyRewind(bubble, restored) {
  while (bubble.previousElementSibling) bubble.previousElementSibling.remove();
  bubble.remove();
  prefillComposer(restored);
}

// Feed delegation: ↶ on a prompt bubble (hover or pick mode) opens the mode
// menu; in pick mode the whole bubble is a target — a ⚙ SYSTEM bubble excluded,
// since an injected turn is nothing to restore to (it carries neither the ↶ nor
// the data-txt the menu POSTs).
document.addEventListener("click", (e) => {
  const rw = e.target.closest && e.target.closest(".msg.prompt .rw");
  if (rw) { e.preventDefault(); return openRewindMenu(rw.closest(".msg.prompt")); }
  if (e.target.closest && e.target.closest(".rwmenu")) return;
  if (inRewindPick()) {
    const bubble = e.target.closest && e.target.closest(".msg.prompt:not(.sys)");
    if (bubble) return openRewindMenu(bubble);
    rewindPickMode(false);            // clicked elsewhere — leave pick mode
  } else closeRewindMenu();           // click-away closes a hover-opened menu
});

// The Esc GESTURE. MID-TURN there is only ONE meaning left — stop the turn —
// so a busy tab fires IMMEDIATELY and a rapid second press inside the window is
// swallowed (a habitual double-tap must not send two Escapes). That fast path
// is what unifying stop and cancel buys: the 450ms below used to delay every
// real interrupt just to tell "interrupt" from "cancel", and those turned out
// to be the same gesture.
//
// IDLE the gesture is still atomic — hold a lone press for ESC_DOUBLE_MS, then
// classify: single press → one /interrupt (an Escape key event), rapid double →
// ONLY the rewind picker, with NO Escape sent at all. Streaming the first press
// immediately shipped and corrupted the rewind: its in-flight Escape raced the
// /rewind text through two server threads and once landed MID-TEXT — the input
// cleared after "/rewi", the "nd" tail re-typed into the empty box, and the
// Enter submitted "nd" into the chat. Nothing streams until the gesture is
// decided, so nothing can interleave.
const ESC_DOUBLE_MS = 450;
let escHold = null;
let escFired = 0;                    // when the busy fast path last fired
const BUSY_TABS = ["thinking", "working", "executing", "awaiting_background"];
function escGesture() {
  const meta = (S.sessionView && S.sessionView.meta) || {};
  if (!S.currentSessionId || !meta.live || !meta.kitty_window_id) return;
  // a modal dialog is open (red asking-you tab) — an Esc here would DECLINE the
  // ask/plan/permission dialog, not interrupt or rewind a turn. Swallow the
  // gesture entirely (no interrupt hold-timer, no rewind) so a stray keypress
  // can't kill the answer the user is composing in the card.
  if (liveTab() === "awaiting_attention") {
    clearTimeout(escHold);
    escHold = null;
    toast("done", "a question is waiting",
          "answer it in the card above — Esc would decline it");
    return;
  }
  if (BUSY_TABS.includes(liveTab())) {
    const now = Date.now();
    if (now - escFired < ESC_DOUBLE_MS) return;   // double-tap → one stop
    escFired = now;
    interruptSession();
    return;
  }
  if (escHold) {
    clearTimeout(escHold);
    escHold = null;           // a third rapid press starts a fresh gesture
    rewindSession();
    return;
  }
  escHold = setTimeout(() => {
    escHold = null;
    interruptSession();
  }, ESC_DOUBLE_MS);
}
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (!$modal.hidden) return closeNewSession();
  if (document.querySelector(".qcmenu")) return closeQuickMenu();
  if (document.querySelector(".rwmenu")) return closeRewindMenu();
  if (inRewindPick()) return rewindPickMode(false);
  escGesture();
});

function setBadge(badge, tab) {
  badge.removeAttribute("data-st");     // drop any focused-subagent status stamp
  badge.dataset.tab = tab;
  badge.replaceChildren(el("span", "st"),
                        tnode(TAB_LABEL[tab] || tab || "no tab"));
  // the whole session header (the web scoreboard) washes with the state hue
  const head = badge.closest(".shead");
  if (head) { head.removeAttribute("data-st"); head.dataset.tab = tab; }
}

/* The header badge + .shead wash for a drilled-into subagent. A subagent has no
   tab of its own, so the pill text, its dot, and the header tint follow the
   agent STATUS (data-st from agentStatus) instead of the session tab — the CSS
   mirrors the agent cards. Symmetric with setBadge, which clears data-st (and
   renderSessionChrome rebuilds the header outright) on the way back. */
function setBadgeAgent(badge, sttxt, stcls) {
  if (!badge) return;
  badge.removeAttribute("data-tab");
  badge.dataset.st = stcls;
  badge.replaceChildren(el("span", "st"), tnode(sttxt));
  const head = badge.closest(".shead");
  if (head) { head.removeAttribute("data-tab"); head.dataset.st = stcls; }
}

function startRenameHeader() {
  // inline rename: swap the header title span for an input; Enter submits,
  // Esc/blur cancels. The server cleans the name (control-strip + cap) and
  // replies the stored title, which also rides the `title` SSE push. Beside
  // the input, ✦ auto types the TUI's own bare `/rename` instead — Claude
  // Code GENERATES the title itself (the quick-command channel, so it needs
  // a live window and inherits the red-tab refusal).
  //
  // A NAMED rename takes the same channel when the session is LIVE (the
  // server replies `channel: "tui"`): Claude Code owns the name, so the title
  // arrives on the `title` SSE once it applies — which is at the TURN BOUNDARY
  // when the reply says `queued`, so there is nothing to show optimistically
  // then. A PARKED rename (`channel: "transcript"`) lands immediately.
  const sessionView = S.sessionView;
  if (!sessionView || !sessionView.projEl || sessionView.projEl.querySelector("input")) return;
  const span = sessionView.projEl;
  const old = span.textContent;
  const inp = el("input", "renamein");
  inp.value = (sessionView.meta && sessionView.meta.title) || "";
  if (LIMITS.rename_max !== null) inp.maxLength = LIMITS.rename_max;
  let done = false;
  const restore = (txt) => span.replaceChildren(tnode(txt));
  const cancel = () => { if (!done) { done = true; restore(old); } };
  const submit = () => {
    if (done) return;
    const name = inp.value.trim();
    if (!name) return cancel();
    done = true;
    inp.disabled = true;
    canonicalControl("rename_session", { name }, { audit: "rename" })
      .then((d) => {
        if (d.queued) {
          // Claude Code has it in its message queue — showing the new name now
          // would assert a rename it has not made yet (and may never, if the
          // queue is Escaped out); the `title` SSE repaints when it lands
          restore(old);
          toast("done", "/rename",
                "queued — applies when the turn ends");
          return;
        }
        if (sessionView.meta) sessionView.meta.title = name;
        restore(name);
        toast("done", "renamed", "sent to "
              + ((sessionView.meta && sessionView.meta.host_label) || "the agent"));
      })
      .catch((e) => {
        restore(old);
        toast("ask", "rename failed", (e && e.error) || "");
      });
  };
  inp.onkeydown = (e) => {
    // stopPropagation is load-bearing: the document-level keydown handler
    // reads Escape as the interrupt/rewind-pick gesture — typing in the
    // rename box must never leak there
    e.stopPropagation();
    if (e.key === "Enter") submit();
    else if (e.key === "Escape") cancel();
  };
  inp.onblur = () => cancel();          // a stray click = cancel, same as Esc
  // ✦ auto — the argless rename, a terminal-typing action on the quick-command
  // channel, so it carries that channel's gates: the owning host must OFFER it
  // (`rename` in meta.quick_commands — codex renames but cannot name a session
  // itself, and its `autoname` gesture 409s) and hold the `rename` cap, there
  // must be a window to type into (parked/headless), no modal dialog may be up,
  // and the conversation must clear the host's own floor for the command — bare
  // `/rename` bounces with "Could not generate a name: no conversation context
  // yet" on an empty one, the ⊜ compact bounce class. Like that gate, an UNKNOWN
  // prompt count never greys.
  const auto = el("button", "sstop renameauto", "✦ auto");
  const hostLbl = (sessionView.meta && sessionView.meta.host_label) || "the agent";
  auto.dataset.tip = "let " + hostLbl + " name this session (/rename)";
  const meta = sessionView.meta || {};
  const windowed = !!(meta.live && meta.kitty_window_id);
  const cap = capOk(meta, "rename") && cmdOffered(meta, "rename");
  const empty = tooThin(meta, "rename");
  gate(auto, cap && windowed && !empty && liveTab() !== "awaiting_attention",
       !cap ? CAP_OFF
         : !windowed ? NO_WINDOW
         : empty ? "nothing to name yet — the conversation is empty"
                 : "a question is waiting — answer it in the card");
  // preventDefault keeps the click from stealing the input's focus first —
  // the blur-cancel above would tear the button out of the DOM mid-click
  auto.onmousedown = (e) => e.preventDefault();
  auto.onclick = () => {
    if (done) return;
    done = true;
    restore(old);              // the `title` SSE repaints when the name lands
    canonicalControl("auto_name_session", {},
                     { audit: "command", auditData: { cmd: "rename" } })
      .then((r) => toast("done", "/rename",
                         r.queued ? "queued — " + hostLbl + " names it when the turn ends"
                                  : "sent — " + hostLbl + " is picking a name"))
      .catch((e) => toast("ask", "auto-rename failed", (e && e.error) || ""));
  };
  span.replaceChildren(inp, auto);
  inp.focus();
  inp.select();
}


// Lock an immediate (no-confirm) control-plane action button for the duration
// of its POST so a double-tap can't fire the terminal write twice — ⇆ migrate
// would spawn two racing migrators, ■ stop would double-send Escape.
// `run` returns the POST promise; `rest` restores the button's resting state
// once it settles (default: re-enable; cancel re-derives from the tab). This
// lives on the buttons, not the functions, because the Esc-key gesture has its
// own escHold debounce and the functions are shared by both entry points.
function lockDuring(btn, run, rest) {
  btn.disabled = true;
  run().finally(rest || (() => { btn.disabled = false; }));
}


// The close POST rides the plain-fetch channel (postJSON — X-Baqylau header,
// JSON body, a CLOSE_POST_MS timeout), tagged `audit:"close"` so its whole
// transport lifecycle lands in the frontend audit (close.begin/ok/fail). This is
// the transport PROVEN to traverse the tunnel (baqylau/dash.zhambyl.top): the
// click's own /hint-audit beacon and the composer /message ride it and always
// land, and every morning-era close (plain fetch) succeeded. navigator.sendBeacon
// was tried instead and REGRESSED close — it returns true (queued) so we resolved
// ok optimistically, but the queued beacon was then silently dropped by the
// tunnel: no `web-stop`, no `web-reject`, just the 20s `web-hint … stale`. The
// timeout turns a genuine upstream stall into a VISIBLE, retryable, audited
// failure (close.fail transport + web-clientfail) instead of a silent hang.
function closeSession(sessionId, via) {
  return sessionControl(sessionId, "close_session", {},
                        { timeout: CLOSE_POST_MS, audit: "close", sessionId,
                          auditData: { via: via || "" } });
}
