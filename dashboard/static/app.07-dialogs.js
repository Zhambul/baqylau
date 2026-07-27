"use strict";
// Part of the dashboard SPA — split from the former single app.js into ordered,
// cohesive files (classic scripts share one global scope; load order is set in
// index.html). See app.13-init.js for the boot/init sequence.

/* ---------- the ask card (AskUserQuestion from the web) ---------- */
// While Claude's question dialog is up in the terminal, the session SSE
// carries the pending ask (the PreToolUse stash — plugins/claude_code/
// ask_fmt.py) and this card mirrors it above the composer: option buttons
// (radio marks + "pick one" for single-select, checkbox marks + "pick any"
// for multiSelect — visually distinct so the mode is legible at a glance),
// a free-text "type your own" per question (the dialog's "Type something"
// row), a submit row (ALWAYS explicit — no auto-submit on a lone
// single-select click; the web card favors review-before-send over the
// TUI's one-keystroke feel), and "chat about this" (the dialog's own
// decline-and-discuss).
// Answers POST /answer, where the server drives the REAL dialog with
// screen-verified key events (dashboard/askdialog.py). The card clears via
// the SSE `ask` event when the answer's PostToolUse drops the stash.

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
// Does THIS question use the preview layout? Its options' previews are what
// costs the TUI dialog its free-text row, so this is the per-question test
// submitAsk escalates on. The ask-WIDE askHasPreview below is only for asking
// "does the card need the preview treatment at all".
function qHasPreview(q) {
  return (q && q.options || []).some(o => o && o.preview);
}
function askHasPreview(ask) {
  return (ask && ask.questions || []).some(qHasPreview);
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
  if (!chat && answers) {
    // Escalate PER QUESTION, not per ask. Only a question whose OWN options
    // carry previews lacks a free-text row in the TUI dialog; a typed answer on
    // an ordinary question rides as `other` exactly as it always could. The
    // test used to be askHasPreview(ask) — true if ANY option ANYWHERE in the
    // ask had a preview — so one preview on question 1 hijacked typed text on
    // question 4.
    const typed = answers
      .map((a, i) => (qHasPreview(qs[i]) ? effOther(a, i) : ""))
      .filter(Boolean);
    if (typed.length) {
      chat = true;
      // …and carry the PICKED answers into the message alongside the typed
      // ones. A chat escalation sends no `answers` array at all, so everything
      // selected on the other questions used to be discarded in silence: one
      // typed word beat every option chosen, and the tool saw "no answer
      // provided" (observed 2026-07-26 — four rounds of answers lost).
      // Escalating is unavoidable (the dialog cannot take that text), losing
      // the rest is not, so the message states the whole submission.
      message = answers.map((a, i) => {
        const q = qs[i] || {};
        const vals = (a.selected || []).slice();
        const t = effOther(a, i);         // already knows if the text counts
        if (t) vals.push(t);
        if (!vals.length) return "";      // unanswered questions say nothing
        return (q.header || q.question || ("q" + (i + 1))) + ": " + vals.join(", ");
      }).filter(Boolean).join("\n");
    }
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

// The audio thread's whole job: mic Float32 at the AudioContext's NATIVE rate
// → 16 kHz linear16 PCM, batched into ~64ms chunks (bare 128-sample process()
// quanta would be ~375 tiny ws messages/s).
//
// Why resample at all (2026-07-27): we used to declare the native rate and ship
// it untouched — 48000 × 2 bytes = 768 kbps of sustained uplink, silence
// included (continuous PCM is deliberate: it keeps Deepgram's no-audio timeout
// from ever firing). An iPad over the tunnel could not hold that up, so the ws
// send queue grew without bound and the transcript fell FURTHER behind the
// longer you spoke — the tell of a saturated uplink, not a slow API (a slow API
// is a CONSTANT delay). Deepgram's models are 16 kHz; every sample above that
// was pure upstream cost for zero accuracy. 16k is 256 kbps, 3× less.
//
// Why here and not `new AudioContext({sampleRate: 16000})`, which needs no DSP
// at all: the first-class client is iPad Safari (docs/remote.md), and a
// non-native-rate context feeding createMediaStreamSource is exactly where
// Safari's own resampler has a history of misbehaving. A SILENT mic is a far
// worse bug than a laggy one, so the conversion is ours and behaves identically
// on every browser.
//
// Decimation without an anti-alias filter would FOLD everything above 8 kHz
// back into the speech band (sibilant energy landing on top of vowels — worse
// for ASR than the HF being dropped), hence the 4th-order Butterworth low-pass
// ahead of the resampler. The read head is FRACTIONAL (linear interpolation),
// not a decimate-by-N, because 44100 devices are real and 44100/16000 is 2.756.
const DICT_RATE = 16000;      // Deepgram's model rate — above it is upstream cost
const DICT_CHUNK = 1024;      // 64ms @16k, inside Deepgram's 20–250ms window
const DICT_WORKLET = `
const CHUNK = ${DICT_CHUNK};
const LP_HZ = 0.425;             // cutoff as a fraction of the OUTPUT rate
const LP_Q = [0.5412, 1.3065];   // the 4th-order Butterworth section Qs

class Biquad {                   // one RBJ low-pass section
  constructor(rate, hz, q) {
    const w = 2 * Math.PI * hz / rate, c = Math.cos(w);
    const al = Math.sin(w) / (2 * q), a0 = 1 + al;
    this.b0 = (1 - c) / 2 / a0; this.b1 = (1 - c) / a0; this.b2 = this.b0;
    this.a1 = -2 * c / a0; this.a2 = (1 - al) / a0;
    this.x1 = this.x2 = this.y1 = this.y2 = 0;
  }
  step(x) {
    const y = this.b0 * x + this.b1 * this.x1 + this.b2 * this.x2
              - this.a1 * this.y1 - this.a2 * this.y2;
    this.x2 = this.x1; this.x1 = x; this.y2 = this.y1; this.y1 = y;
    return y;
  }
}

class DictatePCM extends AudioWorkletProcessor {
  constructor(opt) {
    super();
    // outRate is the main thread's decision because the TOKEN is minted (and
    // the listen URL's sample_rate= baked) before this processor exists — it
    // must not be re-derived here or the two could disagree.
    const out = (opt && opt.processorOptions && opt.processorOptions.outRate)
                || sampleRate;
    this.step = sampleRate / out;   // input samples consumed per output sample
    // a device already at or below the target (8k/16k hardware) is a
    // PASSTHROUGH — filtering and interpolating a 1:1 stream only degrades it
    this.lp = this.step > 1.0001
      ? LP_Q.map(q => new Biquad(sampleRate, LP_HZ * out, q)) : [];
    this.pos = 0;    // fractional read head, relative to this block's start
    this.prev = 0;   // last filtered sample of the PREVIOUS block (index -1)
    this.f = null;   // filtered scratch, sized to the render quantum
    this.buf = new Int16Array(CHUNK);
    this.n = 0;
  }
  emit(v) {
    const s = v < -1 ? -1 : v > 1 ? 1 : v;
    this.buf[this.n++] = s < 0 ? s * 0x8000 : s * 0x7fff;
    if (this.n === this.buf.length) {
      this.port.postMessage(this.buf.slice(0).buffer);
      this.n = 0;
    }
  }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    if (!this.lp.length) {
      for (let i = 0; i < ch.length; i++) this.emit(ch[i]);
      return true;
    }
    if (!this.f || this.f.length < ch.length)
      this.f = new Float32Array(ch.length);
    const f = this.f;
    for (let i = 0; i < ch.length; i++) {
      let v = ch[i];
      for (let k = 0; k < this.lp.length; k++) v = this.lp[k].step(v);
      f[i] = v;
    }
    // interpolate between f[i] and f[i+1]; i === -1 reaches back across the
    // block boundary to the previous block's last sample, so the phase is
    // continuous and no output sample is lost at a quantum edge
    while (this.pos < ch.length - 1) {
      const i = Math.floor(this.pos), t = this.pos - i;
      const a = i < 0 ? this.prev : f[i];
      this.emit(a + (f[i + 1] - a) * t);
      this.pos += this.step;
    }
    this.prev = f[ch.length - 1];
    this.pos -= ch.length;
    return true;
  }
}
registerProcessor("dictate-pcm", DictatePCM);`;
let dictWorkletURL = null;

// Dictation lag telemetry (docs/dashboard.md *Dictation lag*). "It's slow" was
// unanswerable from the DB: the server mints a token and never sees the stream,
// so nothing on this machine knew whether the words were stuck in OUR socket or
// still inside Deepgram. These sample the two separately onto the clientlog
// channel, which is the only place the distinction can be observed at all.
const DICT_LAG_MS = 5000;         // one lag sample per 5s of dictation
const DICT_BACKLOG_WARN_S = 3;    // queued audio that earns the one-shot toast

// Instant-on (docs/dashboard.md *Instant-on mic*): capture starts the moment
// the mic and the worklet are ready and everything said before the socket
// opens is held here, so the press-to-speak gap stops being a gap.
const DICT_PREROLL_MAX_S = 60;    // held audio cap — a safety valve, not a budget
const DICT_FLUSH_MS = 2000;       // failsafe close after CloseStream
const DICT_STOP_GRACE_MS = 6000;  // stop-before-open: how long we still deliver

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


// A greyed "…" stand-in shown in place of the interactive ask/plan card while
// an optimistic decision is in flight — the card analog of the composer's
// greyed prompt bubble. Cleared when the SSE reconcile drops the stash (or on
// failure, which re-renders the live card). `cls` = askcard | plancard.
function pendingCard(cls, title, note) {
  const card = el("div", cls + " pending");
  const head = el("div", "askhead");
  head.append(el("span", cls === "plancard" ? "plantitle" : "asktitle", title));
  card.append(head);
  card.append(el("div", "plandim", note));
  return card;
}
