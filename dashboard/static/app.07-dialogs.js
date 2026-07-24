"use strict";
// Part of the dashboard SPA — split from the former single app.js into ordered,
// cohesive files (classic scripts share one global scope; load order is set in
// index.html). See app.12-init.js for the boot/init sequence.

function buildAskCard() {
  const wrap = el("div", "askwrap");
  S.ses.askEl = wrap;
  renderAsk();
  return wrap;
}

// A preview-layout question (any option carries a `preview`) renders the TUI's
// side-by-side dialog, which OMITS the numbered "Type something" free-text row
// — so a TYPED answer can't be driven (askdialog._require_type_row). The card
// routes typed answers on such asks through "Chat about this" instead
// (docs/dashboard.md, *Web ask*).
function askHasPreview(ask) {
  return (ask && ask.questions || []).some(
    q => (q.options || []).some(o => o && o.preview));
}

function renderAsk() {
  const ses = S.ses;
  if (!ses || !ses.askEl) return;
  const wrap = ses.askEl;
  wrap.textContent = "";
  const ask = ses.meta && ses.meta.ask;
  wrap.hidden = !ask;
  if (!ask) return;
  // an optimistic answer is in flight — show the card greyed until the SSE
  // `ask` reconcile drops the stash (or a failure clears askPend and rebuilds
  // the interactive card). Reasserted on every render so a stray draft/rebuild
  // can't resurrect the live controls mid-submit.
  if (ses.askPend && ses.askPend.live) {
    wrap.append(pendingCard("askcard", "submitting answer…", ses.askPend.note));
    return;
  }
  const qs = ask.questions || [];
  const preview = askHasPreview(ask);
  // per-ask draft state, keyed by tool_use_id so a NEW ask resets it —
  // SEEDED from the persisted `ask-draft` (ses.meta.ask_draft) so a device
  // switch / reopen restores whatever selections were made but not submitted
  if (!ses.askState || ses.askState.id !== ask.tool_use_id)
    ses.askState = { id: ask.tool_use_id,
                     answers: seedAskAnswers(qs, ses.meta && ses.meta.ask_draft,
                                             ask.tool_use_id) };
  const st = ses.askState;
  const card = el("div", "askcard");
  const head = el("div", "askhead");
  head.append(el("span", "asktitle",
                 "claude is asking" + (qs.length > 1 ? " — " + qs.length + " questions" : "")));
  const chatB = el("button", "askchat", "chat about this");
  chatB.title = "dismiss the questions and discuss in the chat instead";
  chatB.onclick = () => submitAsk(ask, null, true);
  head.append(chatB);
  card.append(head);
  // Claude's prose lead-in to the question (server-rendered md_html, escape-
  // first like op HTML) — the "why", which the terse dialog omits; shown
  // above the questions so the context rides ON the card, not just as a
  // detached stream bubble (docs/dashboard.md, *Web ask*). Empty when Claude
  // asked with no framing text.
  if (ask.preamble_html) {
    const pre = el("div", "askpreamble md");
    pre.innerHTML = ask.preamble_html;
    card.append(pre);
  }
  const sub = el("button", "asksubmit",
                 qs.length > 1 ? "submit answers" : "submit answer");
  const syncSubmit = () => {
    sub.disabled = !st.answers.every(a => a.selected.length || a.other.trim());
  };
  qs.forEach((q, qi) => {
    const qbox = el("div", "askq");
    const qhead = el("div", "askqhead");
    if (q.header) qhead.append(el("span", "askhdr", q.header));
    qhead.append(el("span", "askqtext", q.question || ""));
    qhead.append(el("span", "askpick" + (q.multiSelect ? " multi" : ""),
                    q.multiSelect ? "pick any" : "pick one"));
    qbox.append(qhead);
    const opts = el("div", "askopts" + (q.multiSelect ? " multi" : ""));
    const paintAll = () => [...opts.children].forEach(c =>
      c.classList.toggle("on", st.answers[qi].selected.includes(c.dataset.label)));
    (q.options || []).forEach(o => {
      const b = el("button", "askopt");
      b.dataset.label = o.label || "";
      b.append(el("span", "amark"));
      const txt = el("span", "aotxt");
      txt.append(el("span", "aol", o.label || ""));
      if (o.description) txt.append(el("span", "aod", o.description));
      b.append(txt);
      b.onclick = () => {
        const a = st.answers[qi];
        if (q.multiSelect) {
          a.selected = a.selected.includes(o.label)
            ? a.selected.filter(x => x !== o.label)
            : [...a.selected, o.label];
        } else {
          // single-select: the option becomes the answer, but KEEP any typed
          // custom text (it stays in the field, re-selectable on focus) — the
          // old clear here was silent data loss. Submit sends other:"" while an
          // option is selected, so the lingering text never hijacks the answer.
          a.selected = [o.label];
        }
        paintAll();
        paintOther();
        syncSubmit();
        saveAskDraft(ask, st);
      };
      opts.append(b);
    });
    qbox.append(opts);
    const other = el("input", "askother");
    other.type = "text";
    other.spellcheck = false;
    // A PREVIEW-layout question's TUI dialog has no free-text row, so a typed
    // answer can't be delivered as an option — but "Chat about this" IS
    // reachable (the _cursor_to two-❯ fix, 2026-07-20), so a typed answer here
    // is ROUTED through chat and delivered as a follow-up message (submitAsk).
    other.placeholder = preview
      ? "type a custom answer → sent via “chat about this”"
      : q.multiSelect
        ? "add your own answer…" : "or type your own answer…";
    other.value = st.answers[qi].other;
    // red border == this custom text IS the active answer. multiSelect: whenever
    // it holds text (additive to any checked options). single-select: only while
    // no option is selected (a clicked option is the answer then; the text stays
    // but sits dormant, borderless — derived, no extra state).
    const otherIsAnswer = () => !!other.value.trim()
      && (q.multiSelect || !st.answers[qi].selected.length);
    const paintOther = () => other.classList.toggle("on", otherIsAnswer());
    other.oninput = () => {
      st.answers[qi].other = other.value;
      if (!q.multiSelect && other.value.trim()) {
        st.answers[qi].selected = [];         // typing (re)claims the answer
        paintAll();
      }
      paintOther();
      syncSubmit();
      saveAskDraft(ask, st);
    };
    // clicking BACK into a non-empty custom field reclaims it as the answer
    // (deselects the option) — no retype needed, and the text was never lost
    other.onfocus = () => {
      if (!q.multiSelect && other.value.trim() && st.answers[qi].selected.length) {
        st.answers[qi].selected = [];
        paintAll();
        paintOther();
        syncSubmit();
        saveAskDraft(ask, st);
      }
    };
    other.onkeydown = (e) => {
      e.stopPropagation();                  // keep Esc/gestures out of typing
      if (e.key === "Enter" && other.value.trim() && !sub.disabled)
        submitAsk(ask, st.answers, false);
    };
    qbox.append(other);
    paintAll();
    paintOther();
    card.append(qbox);
  });
  const foot = el("div", "askfoot");
  foot.append(sub);
  sub.onclick = () => submitAsk(ask, st.answers, false);
  card.append(foot);
  syncSubmit();
  wrap.append(card);
}

// Build the per-question answer array, seeding from a persisted draft when it
// belongs to THIS ask (tool_use_id + question count match) — otherwise fresh.
function seedAskAnswers(qs, draft, tuid) {
  const blank = () => qs.map(() => ({ selected: [], other: "" }));
  if (!draft || draft.tool_use_id !== tuid
      || !Array.isArray(draft.answers) || draft.answers.length !== qs.length)
    return blank();
  return draft.answers.map(a => ({
    selected: Array.isArray(a && a.selected) ? a.selected.slice() : [],
    other: (a && a.other) || "",
  }));
}

// Persist the unsubmitted selections to the server (debounced) so a reopen on
// any device restores them. Best-effort — a failed save just retries on the
// next edit; the local card keeps its state regardless.
function saveAskDraft(ask, st) {
  const ses = S.ses;
  if (!ses || !S.cur || !ask || !ask.tool_use_id) return;
  const answers = st.answers.map(a =>
    ({ selected: a.selected.slice(), other: a.other || "" }));
  // keep meta in sync so a tab-switch rebuild seeds from what we just typed,
  // and so our own SSE echo (same origin) is a no-op against current state
  if (ses.meta)
    ses.meta.ask_draft = { tool_use_id: ask.tool_use_id, origin: CLIENT_ID,
                           answers };
  clearTimeout(ses._askDraftTimer);
  ses._askDraftTimer = setTimeout(() => {
    postJSON("/api/session/" + encodeURIComponent(S.cur) + "/ask-draft",
             { tool_use_id: ask.tool_use_id, origin: CLIENT_ID, answers })
      .catch(() => {});                       // draft save is best-effort
  }, ASK_DRAFT_DEBOUNCE_MS);
}

// A peer device's draft update arrived over SSE. Adopt it and repaint the card
// — but ignore our OWN echo (same origin), and stale drafts (wrong ask).
function applyAskDraft(draft) {
  const ses = S.ses;
  if (!ses) return;
  if (ses.meta) ses.meta.ask_draft = draft || null;   // for a later rebuild
  if (ses.askPend && ses.askPend.live) return;   // don't un-grey a submitting card
  const ask = ses.meta && ses.meta.ask;
  if (!draft || !ask || draft.tool_use_id !== ask.tool_use_id) return;
  if (draft.origin && draft.origin === CLIENT_ID) return;   // our own write
  if (!ses.askState || ses.askState.id !== ask.tool_use_id) return;
  // don't yank the card out from under an ACTIVE local edit: renderAsk()
  // rebuilds the DOM (wrap.textContent = ""), which would drop focus + caret
  // mid-keystroke on the device that's typing. Skip while the card holds
  // focus — ses.meta.ask_draft is already updated above, so the next remote
  // change (or a manual rebuild) applies it once the field blurs.
  if (ses.askEl && ses.askEl.contains(document.activeElement)) return;
  ses.askState.answers = (draft.answers || []).map(a =>
    ({ selected: Array.isArray(a && a.selected) ? a.selected.slice() : [],
       other: (a && a.other) || "" }));
  renderAsk();
}

function submitAsk(ask, answers, chat) {
  const ses = S.ses;
  if (!ses || !S.cur) return;
  // A TYPED answer on a preview-layout question has no free-text row in the TUI
  // dialog, so route it through "Chat about this" (now keyboard-reachable — the
  // _cursor_to two-❯ fix) and ride the typed text as `message`: the server
  // presses chat, waits for the dialog to close, then delivers the text as a
  // message so the custom answer reaches the session (docs/dashboard.md, *Web
  // ask*). Explicit "chat about this" (answers == null) is untouched.
  // the custom text counts as the answer only when it's ACTIVE: multiSelect
  // (additive) or single-select with no option chosen. A single-select option
  // wins → send other:"" so the (preserved-but-dormant) text can't override it
  // (askdialog._answer_question gives `other` precedence over `selected`).
  const qs = ask.questions || [];
  const effOther = (a, i) => {
    const t = (a.other || "").trim();
    return t && ((qs[i] && qs[i].multiSelect) || !(a.selected || []).length)
      ? t : "";
  };
  let message = "";
  if (!chat && answers && askHasPreview(ask)) {
    const typed = answers.map(effOther).filter(Boolean);
    if (typed.length) { chat = true; message = typed.join("\n"); }
  }
  const body = { tool_use_id: ask.tool_use_id || "" };
  if (chat) { body.chat = true; if (message) body.message = message; }
  else body.answers = (answers || []).map((a, i) =>
    ({ selected: a.selected, other: effOther(a, i) }));
  // optimistic: grey the card immediately (renderAsk shows the pending stand-in
  // while askPend is live) and keep it until the SSE `ask` reconcile drops the
  // stash — NOT the old hide-on-POST-return, which claimed done before the
  // answer had actually landed. The lifecycle is beaconed as `web-hint` op=answer.
  const note = chat
    ? (message ? "delivering your answer via chat…" : "dismissing the questions…")
    : "answer submitted — waiting for the session…";
  ses.askPend = optPending(S.cur, "answer", ask.tool_use_id || "", note);
  renderAsk();
  postJSON("/api/session/" + encodeURIComponent(S.cur) + "/answer", body,
           { audit: "answer" })
    .then(() => {
      if (chat) {
        if (message)
          toast("done", "answer sent via chat",
                "your typed answer was delivered as a message");
        else
          toast("done", "over to chat",
                "questions dismissed — type your message below");
        if (!message && ses.composer) ses.composer.focus();
      } else {
        toast("done", "answered", "answers submitted to the session");
      }
      // stay greyed — the SSE `ask` event (stash cleared by the answer's
      // PostToolUse) is the real confirmation that swaps the card away
    })
    .catch(e => {
      // a cursor/type bail means the driver couldn't steer the TUI dialog (a
      // Claude Code layout change, or a rare mis-read) — suggest the fallback
      // so the user isn't stuck on a bare "failed"
      const step = e && e.step;
      const hint = (step === "cursor" || step === "type")
        ? "couldn't drive the dialog — pick an option, or answer in the terminal"
        : (e && e.error) || "";
      if (ses.askPend) {
        ses.askPend.settle("dropped", { reason: step || "failed" });
        ses.askPend = null;
      }
      toast("ask", "answer failed", hint);
      renderAsk();                           // rebuild the interactive card to retry
    });
}

/* ---------- the plan card (ExitPlanMode approval from the web) ---------- */
// While Claude's plan-approval dialog is up in the terminal, the session SSE
// carries the pending plan (the PreToolUse stash — plan markdown rendered
// server-side as plan_html) and this card mirrors it above the composer.
// The DECISION buttons come from the live screen (POST /plan-options —
// their labels vary with the session's permission mode), a feedback box
// mirrors the dialog's "Tell Claude what to change" row, and "keep planning"
// is the dialog's own Esc. Decisions POST /plan-decision, where the server
// drives the real dialog screen-verified (dashboard/plandialog.py).

function buildPlanCard() {
  const wrap = el("div", "planwrap");
  S.ses.planEl = wrap;
  renderPlan();
  return wrap;
}

function renderPlan() {
  const ses = S.ses;
  if (!ses || !ses.planEl) return;
  const wrap = ses.planEl;
  wrap.textContent = "";
  const plan = ses.meta && ses.meta.plan;
  wrap.hidden = !plan;
  if (!plan) return;
  // an optimistic decision is in flight — grey the card until the SSE `plan`
  // reconcile drops the stash (or a failure clears planPend and rebuilds it)
  if (ses.planPend && ses.planPend.live) {
    wrap.append(pendingCard("plancard", "sending decision…", ses.planPend.note));
    return;
  }
  const card = el("div", "plancard");
  const head = el("div", "askhead");
  head.append(el("span", "plantitle", "claude has a plan — proceed?"));
  const dis = el("button", "askchat", "keep planning");
  dis.title = "reject the plan and stay in plan mode (the dialog's Esc)";
  dis.onclick = () => submitPlan(plan, { dismiss: true }, "plan dismissed",
                                 "Claude keeps planning");
  head.append(dis);
  card.append(head);
  const body = el("div", "planbody md");
  body.innerHTML = plan.plan_html || "";
  card.append(body);
  const btns = el("div", "planbtns");
  btns.append(el("span", "plandim", "loading options…"));
  card.append(btns);
  const fb = el("div", "planfb");
  const fbIn = el("input", "askother");
  fbIn.type = "text";
  fbIn.spellcheck = false;
  fbIn.placeholder = "tell Claude what to change…";
  fbIn.onkeydown = (e) => {
    e.stopPropagation();
    if (e.key === "Enter" && fbIn.value.trim())
      submitPlan(plan, { feedback: fbIn.value.trim() }, "feedback sent",
                 "Claude will revise the plan");
  };
  const fbB = el("button", "askchat", "send feedback");
  fbB.onclick = () => {
    if (fbIn.value.trim())
      submitPlan(plan, { feedback: fbIn.value.trim() }, "feedback sent",
                 "Claude will revise the plan");
  };
  fb.append(fbIn, fbB);
  card.append(fb);
  wrap.append(card);
  // the decision buttons come from the LIVE dialog (labels vary with the
  // session's permission mode) — fetched once per card render
  postJSON("/api/session/" + encodeURIComponent(S.cur) + "/plan-options",
           { tool_use_id: plan.tool_use_id || "" })
    .then(r => {
      btns.textContent = "";
      (r.options || []).forEach(o => {
        if (o.feedback) return;            // the feedback row is the box above
        const b = el("button", "planopt", o.label);
        b.onclick = () => submitPlan(plan, { digit: o.digit, label: o.label },
                                     "decided", o.label);
        btns.append(b);
      });
    })
    .catch(e => {
      btns.textContent = "";
      btns.append(el("span", "plandim",
                     "options unavailable — " + ((e && e.error) || "") +
                     " (decide in the terminal, or send feedback below)"));
    });
}

function submitPlan(plan, body, okTitle, okDetail) {
  const ses = S.ses;
  if (!ses || !S.cur) return;
  body.tool_use_id = plan.tool_use_id || "";
  // optimistic: grey the card immediately and keep it until the SSE `plan`
  // reconcile drops the stash — not the old hide-on-POST-return. Beaconed as
  // `web-hint` op=plan.
  ses.planPend = optPending(S.cur, "plan", plan.tool_use_id || "", okDetail);
  renderPlan();
  postJSON("/api/session/" + encodeURIComponent(S.cur) + "/plan-decision", body,
           { audit: "plan" })
    .then(() => {
      toast("done", okTitle, okDetail);
      // stay greyed — the SSE `plan` event is the real confirmation
    })
    .catch(e => {
      if (ses.planPend) {
        ses.planPend.settle("dropped", { reason: (e && e.step) || "failed" });
        ses.planPend = null;
      }
      toast("ask", "plan decision failed", (e && e.error) || "");
      renderPlan();                          // rebuild the interactive card to retry
    });
}

/* ---------- dictation (mic → Deepgram → the textarea, live) ---------- */
// docs/dashboard.md *Web dictation*. Mic buttons render only when the server
// reports a configured Deepgram key (GET /api/dictate — probed once, cached).
// Flow: POST /api/dictate/token → ~30s grant JWT + the fully-assembled listen
// URL → the BROWSER opens wss straight to Deepgram (the stdlib server can't
// speak WebSocket and must never see audio) → an AudioWorklet ships
// Float32→Int16 PCM at the AudioContext's native rate (MediaRecorder is
// rejected: iPad Safari emits mp4/AAC, which Deepgram streaming refuses) →
// interim results splice into the textarea LIVE (visual validation is the
// point) and firm up in place when Deepgram finalizes them. One mic at a
// time, page-wide; view/modal teardown stops it (a mic must never outlive
// the box it feeds).

let dictProbe = null;              // the one /api/dictate probe (Promise<bool>)
function dictAvailable() {
  if (!dictProbe)
    dictProbe = fetch("/api/dictate").then(r => r.json())
      .then(d => !!(d && d.available)).catch(() => false);
  return dictProbe;
}

// Float32 → Int16 in the audio thread, batched to 4096-sample (~85ms @48k)
// chunks — bare 128-sample process() quanta would be ~375 tiny ws messages/s.
const DICT_WORKLET = `
class DictatePCM extends AudioWorkletProcessor {
  constructor() { super(); this.buf = new Int16Array(4096); this.n = 0; }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (ch) for (let i = 0; i < ch.length; i++) {
      const s = Math.max(-1, Math.min(1, ch[i]));
      this.buf[this.n++] = s < 0 ? s * 0x8000 : s * 0x7fff;
      if (this.n === this.buf.length) {
        this.port.postMessage(this.buf.slice(0).buffer);
        this.n = 0;
      }
    }
    return true;
  }
}
registerProcessor("dictate-pcm", DictatePCM);`;
let dictWorkletURL = null;

function micIcon() {
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "2");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  [["M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"],
   ["M19 10v2a7 7 0 0 1-14 0v-2"], ["M12 19v4"]].forEach(([d]) => {
    const p = document.createElementNS(NS, "path");
    p.setAttribute("d", d);
    svg.append(p);
  });
  return svg;
}

let dictActive = null;             // the page-wide single live dictation
function stopDictation() { if (dictActive) dictActive.stop(); }

