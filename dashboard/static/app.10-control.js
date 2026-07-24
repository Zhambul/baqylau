"use strict";
// Part of the dashboard SPA — split from the former single app.js into ordered,
// cohesive files (classic scripts share one global scope; load order is set in
// index.html). See app.12-init.js for the boot/init sequence.

function migrateSession() {
  if (!S.cur) return Promise.resolve();
  return postJSON("/api/session/" + encodeURIComponent(S.cur) + "/migrate", {},
                  { audit: "migrate" })
    .then(r => toast("done", "migrating",
                     "resuming on " + ((r && r.to) || "another account")))
    .catch(e => toast("ask", "migrate failed", (e && e.error) || ""));
}

// Returns the POST promise for the same in-flight button lock (a double-tap
// mid round-trip would send Escape to the terminal twice).
function interruptSession() {
  const meta = (S.ses && S.ses.meta) || {};
  if (!S.cur || !meta.live || !meta.kitty_window_id) return Promise.resolve();
  // a red "asking you" tab means a MODAL DIALOG is open (ask/plan/permission).
  // An Esc there DECLINES the dialog, it doesn't interrupt a turn — sending one
  // once killed the answer the user was giving via the ask card. Respond
  // through the card instead (the server 409s as the backstop, but the toast is
  // the honest UX; docs/tab-colors.md).
  if (liveTab() === "awaiting-command") {
    toast("done", "a question is waiting",
          "answer it in the card above — Esc would decline it");
    return Promise.resolve();
  }
  return postJSON("/api/session/" + encodeURIComponent(S.cur) + "/interrupt", {},
                  { audit: "interrupt" })
    .then(r => {
      if (BUSY_TABS.includes(r && r.tab))
        toast("done", "interrupted", "Esc sent to the session");
      else
        toast("done", "Esc sent", "double-press Esc for rewind");
      // an interrupt ENDS the turn → it's your turn now. But Claude Code fires
      // NO hook on interrupt, so the tab can sit stale-busy (from EXECUTING not
      // even the escape-recheck spawns), leaving the composer button stuck on
      // "queue" when a plain "send" is what actually happens. Flip send/cancel/
      // quick out of the busy state NOW; a real tab change — the escape-recheck's
      // green, or the next prompt — reconciles, and if the turn somehow kept
      // going that next tab event flips it right back to "queue".
      const ses = S.ses;
      if (ses && BUSY_TABS.includes(r && r.tab)) {
        const yourTurn = "awaiting-response";   // green, not a QUEUE_TAB
        if (ses.composerMode) ses.composerMode(yourTurn);
        if (ses.cancelMode) ses.cancelMode(yourTurn);
        if (ses.stopMode) ses.stopMode(yourTurn);
        if (ses.quickMode) ses.quickMode(yourTurn);
      }
    })
    .catch(e => toast("ask", "interrupt failed", (e && e.error) || ""));
}

// The double-Esc gesture — its MEANING is the TUI's, decided by the tab
// state at gesture time: mid-turn it cancels the work and restores the last
// message for editing (the cancelEdit POST — two real Escapes server-side);
// idle it means REWIND, which the web now does fully itself (rewind picking
// mode below — no more "go open the kitty tab").
function rewindSession() {
  const meta = (S.ses && S.ses.meta) || {};
  if (!S.cur || !meta.live || !meta.kitty_window_id) return;
  // red "asking you" tab: a dialog is open — neither cancel-edit (Esc-Esc) nor
  // rewind (/rewind) belongs here; both would land in the dialog and dismiss or
  // corrupt it. Answer via the card instead.
  if (liveTab() === "awaiting-command") {
    toast("done", "a question is waiting",
          "answer it in the card above first");
    return;
  }
  if (CANCEL_TABS.includes(liveTab())) return cancelEdit();
  rewindPickMode(true);
}

// The live tab state of the open session (the SSE `tab` event patches the
// row; meta.tab is the initial fallback).
function liveTab() {
  return ((S.sessions.find(r => r.sid === S.cur) || {}).tab)
      || ((S.ses && S.ses.meta && S.ses.meta.tab) || "");
}

// Tab states in which a turn is running, so Claude Code's mid-turn double-Esc
// means CANCEL (not the rewind menu). Matches the server's BUSY_TABS — the
// cancel/stop buttons gate on this so an idle click never opens the rewind menu.
// awaiting-command (red) is DELIBERATELY excluded: red means a modal dialog is
// open (ask/plan/permission), where an Esc DECLINES the dialog rather than
// cancelling a turn — the stop/cancel buttons disable there and the gestures
// bail (see interruptSession/rewindSession), so the ask card stays the response
// path (docs/tab-colors.md; the "User declined to answer questions" fix).
const CANCEL_TABS = ["thinking", "working", "executing", "awaiting-bg"];

// The Cancel button: Claude Code's mid-turn double-Esc — cancel the running
// turn and restore your message into the composer for editing. Distinct from
// ■ stop (a plain interrupt that keeps the partial work) and ↶ rewind (the
// checkpoint menu). Only meaningful mid-turn; the button disables when idle,
// and this guard is the belt-and-braces (an idle /rewind would type the
// rewind command, not cancel).
function cancelEdit() {
  const meta = (S.ses && S.ses.meta) || {};
  if (!S.cur || !meta.live || !meta.kitty_window_id) return Promise.resolve();
  if (!CANCEL_TABS.includes(liveTab())) {
    toast("done", "nothing to cancel", "no turn is running");
    return Promise.resolve();
  }
  return postJSON("/api/session/" + encodeURIComponent(S.cur) + "/rewind", {},
                  { audit: "rewind" })
    .then(r => {
      if (r && r.mode === "cancel-edit") {
        applyCancelEdit(r.restored || "");
        toast("done", "cancelled", "message restored below — edit and resend");
      } else {
        toast("done", "nothing to cancel", "no turn is running");
      }
    })
    .catch(e => toast("ask", "cancel failed", (e && e.error) || ""));
}

// Mirror Claude Code's mid-turn cancel-edit fully on the web — no jumping to
// the kitty tab: the restored message (the last user prompt) goes into the
// composer for editing, the cancelled prompt bubble is dropped from the feed
// (abandoned — kitty un-renders it too), and the NEXT composer send clears the
// TUI's restored draft and resends as an atomic paste (clear_draft → the
// server's Ctrl+U/K + bracketed paste, which is the ONLY reliable way to
// replace the draft: a raw send drops leading bytes after a cancel). Prefill
// only an EMPTY composer — never clobber text you were already typing.
function applyCancelEdit(restored) {
  const ses = S.ses;
  if (!ses) return;
  // drop the cancelled prompt bubble (newest .msg.prompt — items prepend, so
  // it's the FIRST in the feed). Optimistic: the transcript keeps the record
  // (verified — a mid-turn cancel doesn't rewrite the file), so a full reload
  // re-shows it; within this view the append-only feed keeps it hidden.
  const feed = ses.stream;
  const bubble = feed && feed.querySelector(".msg.prompt");
  if (bubble) bubble.remove();
  prefillComposer(restored);
}

// The shared tail of cancel-edit and web rewind: the TUI now holds the
// restored prompt as its input draft, so prefill OUR composer with the same
// text (only when empty — never clobber what you were typing) and make the
// next send replace the TUI draft (clear_draft) instead of appending to it.
function prefillComposer(restored) {
  const ses = S.ses;
  if (!ses) return;
  const ta = ses.composer;
  if (ta && restored && !ta.value.trim()) {
    ta.value = restored;
    autoGrow(ta);
    ta.focus();
    ta.setSelectionRange(ta.value.length, ta.value.length);
  }
  ses.clearDraftNext = true;
}

/* ---------- quick commands (docs/dashboard.md, *Web quick commands*) ----------
   The scoreboard's SECOND action row (under stop/cancel/rewind/close):
   compact + the model and effort pickers. Each sends one of the TUI's OWN
   slash commands through POST /command (fixed vocabulary server-side, never
   free text); mid-turn it queues like any typed input (`queued` in the
   reply), and a red asking-you tab disables the row — pasted text would land
   in the open dialog (the server 409s as the backstop). */

// The picker choices. Model aliases match the new-session form's list (the
// CLI resolves them); effort matches the server's EFFORTS levels.
const MODEL_CHOICES = [["fable", "fable"], ["opus", "opus"],
                       ["sonnet", "sonnet"], ["haiku", "haiku"]];
const EFFORT_CHOICES = [["low", "low"], ["medium", "medium"],
                        ["high", "high"], ["xhigh", "xhigh"], ["max", "max"]];

function closeQuickMenu() {
  document.querySelectorAll(".qcmenu").forEach(m => m.remove());
}

function sendQuickCmd(cmd, arg) {
  if (!S.cur) return;
  const label = "/" + cmd + (arg ? " " + arg : "");
  postJSON("/api/session/" + encodeURIComponent(S.cur) + "/command",
           arg ? { cmd, arg } : { cmd }, { audit: "command", auditData: { cmd } })
    .then(r => {
      // `confirm`: the server auto-answers the TUI's switch-confirm menu
      // when /model // /effort opens one (the prompt-cache warning)
      const sub = r.queued ? "queued — runs when the turn ends"
        : r.confirm === "failed"
          ? "sent — answer the confirm dialog in the terminal"
          : r.confirm === "confirmed" ? "switched (dialog confirmed)" : "sent";
      toast(r.confirm === "failed" ? "ask" : "done", label, sub);
      if (!r.queued && r.confirm !== "failed") applyQuickSwitch(cmd, arg);
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
  const ses = S.ses;
  if (!ses) return;
  if (cmd === "model") {
    ses.pendingModel = arg.replace("[1m]", "");
    if (ses.modelBtn) setModelBtn(ses.modelBtn);
  } else if (cmd === "effort") {
    if (ses.meta) ses.meta.effort = arg;
    if (ses.effortBtn) setEffortBtn(ses.effortBtn);
  }
}

// The session's current model FAMILY (a MODEL_CHOICES value) when the ctx
// probe knows it — shortModel's leading word ("opus-4.8" → "opus").
function curModelFamily() {
  const ses = S.ses;
  if (ses && ses.pendingModel) return ses.pendingModel;
  const cx = (ses && (ses.ctx || (ses.meta && ses.meta.ctx))) || null;
  return (shortModel(cx && cx.model) || "").split("-")[0];
}

// The model button's label carries the session's CURRENT model when the ctx
// probe knows it (meta/SSE `ctx` — the transcript tail's last assistant
// record), so the row doubles as a live model indicator. A just-switched
// model shows as pendingModel until the probe's family confirms it (the
// ctx model stays stale until the next assistant turn).
function setModelBtn(btn) {
  const ses = S.ses;
  const cx = (ses && (ses.ctx || (ses.meta && ses.meta.ctx))) || null;
  const m = shortModel(cx && cx.model);
  if (ses && ses.pendingModel) {
    if ((m || "").split("-")[0] === ses.pendingModel) ses.pendingModel = null;
    else { btn.textContent = "✦ " + ses.pendingModel + " ▾"; return; }
  }
  btn.textContent = "✦ " + (m || "model") + " ▾";
}

// The effort button's label carries the SAVED effort level (meta/SSE
// `effort` — the settings' effortLevel, which every applied /effort writes
// through); bare "effort" when unknown.
function setEffortBtn(btn) {
  const meta = (S.ses && S.ses.meta) || {};
  btn.textContent = "✧ " + (meta.effort || "effort") + " ▾";
}

/* ---------- full web rewind (docs/dashboard.md, *Web rewind*) ---------- */
// The feed's prompt bubbles ARE the checkpoint list: every user prompt is a
// checkpoint in Claude Code, so "rewind to a specific message" is a click on
// its bubble — a ↶ button each .msg.prompt carries (hover-revealed; pick mode
// reveals them all). The chosen mode POSTs /rewind-to, where the server
// drives Claude Code's own rewind menu in the session's window with screen-
// verified key events (dashboard/rewindmenu.py) — nothing to do in kitty.

// Picking mode: the idle meaning of ↶ rewind / double-Esc. Reveals every
// bubble's ↶ and waits for a click; Esc or a second toggle leaves.
function rewindPickMode(on) {
  const ses = S.ses;
  if (!ses || !ses.stream) return;
  const want = on === undefined ? !ses.stream.classList.contains("rwpick") : !!on;
  ses.stream.classList.toggle("rwpick", want);
  if (want)
    toast("done", "rewind", "pick a message to rewind to (Esc to leave)");
  else closeRewindMenu();
}

function inRewindPick() {
  const st = S.ses && S.ses.stream;
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

// The per-message mode menu — Claude Code's own confirm options, minus the
// summarize pair (a web summarize would need the composer anyway). The
// labels match rewindmenu.MODE_LABELS server-side.
const RW_MODES = [
  ["both", "restore code and conversation"],
  ["conversation", "restore conversation"],
  ["code", "restore code"],
];

function openRewindMenu(bubble) {
  closeRewindMenu();
  const menu = el("div", "rwmenu");
  menu.append(el("div", "rwhead", "rewind to before this message?"));
  for (const [mode, label] of RW_MODES) {
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
  const meta = (S.ses && S.ses.meta) || {};
  if (!S.cur || !meta.live || !meta.kitty_window_id) return;
  if (CANCEL_TABS.includes(liveTab())) {
    toast("ask", "session is busy", "stop or cancel the turn first");
    return;
  }
  const text = bubble.dataset.txt || "";
  if (!text.trim()) return;
  // the jump hint: the target's `up`-press distance from the menu's
  // "(current)" cursor start = newer prompts + 1. Newer bubbles precede it
  // in the feed (newest-first); a stale count only slows the server's
  // text-verified scan, never mis-selects.
  let ups = 1;
  for (let n = bubble.previousElementSibling; n; n = n.previousElementSibling)
    if (n.classList && n.classList.contains("prompt")) ups++;
  menu.querySelectorAll("button").forEach(b => b.disabled = true);
  toast("done", "rewinding…", "driving the checkpoint menu");
  postJSON("/api/session/" + encodeURIComponent(S.cur) + "/rewind-to",
           { text, mode, ups }, { audit: "rewind-to", auditData: { mode, ups } })
    .then(r => {
      rewindPickMode(false);
      if (r && r.restored) {
        applyRewind(bubble, r.restored);
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
// kitty's TUI does the same. Optimistic like applyCancelEdit: the transcript
// still holds the dead branch (a rewind writes nothing until the next send
// forks it), so a full reload re-shows it; this view matches the terminal.
function applyRewind(bubble, restored) {
  while (bubble.previousElementSibling) bubble.previousElementSibling.remove();
  bubble.remove();
  prefillComposer(restored);
}

// Feed delegation: ↶ on a prompt bubble (hover or pick mode) opens the mode
// menu; in pick mode the whole bubble is a target.
document.addEventListener("click", (e) => {
  const rw = e.target.closest && e.target.closest(".msg.prompt .rw");
  if (rw) { e.preventDefault(); return openRewindMenu(rw.closest(".msg.prompt")); }
  if (e.target.closest && e.target.closest(".rwmenu")) return;
  if (inRewindPick()) {
    const bubble = e.target.closest && e.target.closest(".msg.prompt");
    if (bubble) return openRewindMenu(bubble);
    rewindPickMode(false);            // clicked elsewhere — leave pick mode
  } else closeRewindMenu();           // click-away closes a hover-opened menu
});

// The Esc GESTURE is atomic — hold a lone press for ESC_DOUBLE_MS, then
// classify: single press → one /interrupt (an Escape key event), rapid
// double → ONLY /rewind (the typed command), with NO Escape sent at all.
// Streaming the first press immediately shipped and corrupted the rewind:
// its in-flight Escape raced the /rewind text through two server threads
// and once landed MID-TEXT — the input cleared after "/rewi", the "nd"
// tail re-typed into the empty box, and the Enter submitted "nd" into the
// chat. Nothing streams until the gesture is decided, so nothing can
// interleave. The hold delays a real interrupt by 450ms — imperceptible
// next to the HTTP+kitten pipeline. Residual accepted mismatch: a SLOW
// double-press (>450ms) is two interrupts to us, but the TUI's own flaky
// double-Esc detection may still open the panel on those two Escapes.
const ESC_DOUBLE_MS = 450;
let escHold = null;
const BUSY_TABS = ["thinking", "working", "executing", "awaiting-bg"];
function escGesture() {
  const meta = (S.ses && S.ses.meta) || {};
  if (!S.cur || !meta.live || !meta.kitty_window_id) return;
  // a modal dialog is open (red asking-you tab) — an Esc here would DECLINE the
  // ask/plan/permission dialog, not interrupt or rewind a turn. Swallow the
  // gesture entirely (no interrupt hold-timer, no rewind) so a stray keypress
  // can't kill the answer the user is composing in the card.
  if (liveTab() === "awaiting-command") {
    clearTimeout(escHold);
    escHold = null;
    toast("done", "a question is waiting",
          "answer it in the card above — Esc would decline it");
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
  // replies the stored title, which also rides the `title` SSE push.
  const ses = S.ses;
  if (!ses || !ses.projEl || ses.projEl.querySelector("input")) return;
  const span = ses.projEl;
  const old = span.textContent;
  const inp = el("input", "renamein");
  inp.value = (ses.meta && ses.meta.title) || "";
  inp.maxLength = 120;                  // mirrors the server's RENAME_MAX
  let done = false;
  const restore = (txt) => span.replaceChildren(tnode(txt));
  const cancel = () => { if (!done) { done = true; restore(old); } };
  const submit = () => {
    if (done) return;
    const name = inp.value.trim();
    if (!name) return cancel();
    done = true;
    inp.disabled = true;
    postJSON("/api/session/" + encodeURIComponent(S.cur) + "/rename", {name},
             { audit: "rename" })
      .then((d) => {
        if (ses.meta) ses.meta.title = d.title || name;
        restore(d.title || name);
        toast("done", "renamed",
              d.tab_retitled ? "picker + tab" : "picker (tab on next resume)");
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
  span.replaceChildren(inp);
  inp.focus();
  inp.select();
}

