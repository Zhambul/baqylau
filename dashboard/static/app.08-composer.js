"use strict";
// Part of the dashboard SPA — split from the former single app.js into ordered,
// cohesive files (classic scripts share one global scope; load order is set in
// index.html). See app.13-init.js for the boot/init sequence.

function dictation(ta, getCwd, sid) {
  // Per-textarea controller — returns {btn, stop}; callers place the button.
  // getCwd (optional, zero-arg): the directory that keys the PROJECT
  // vocabulary layer — read at mic-press time, so the new-session form's
  // typed dir is honored as-typed and a keyterms edit lands next press.
  // sid (optional): only for attributing the lag telemetry below — the
  // new-session form dictates with no session to name, exactly like the
  // server-side `web-dictate` audit row.
  const btn = el("button", "micbtn");
  btn.type = "button";
  btn.title = "dictate";
  btn.hidden = true;               // shown only when the server has a key
  btn.append(micIcon());
  dictAvailable().then(ok => { btn.hidden = !ok; });
  let live = null;
  // `live` only exists once we are CAPTURING, and getting there is async — so
  // it cannot be the whole re-entrancy guard: two quick presses would other-
  // wise run two pipelines, and the second would orphan the first's mic.
  let starting = false;

  async function start() {
    if (ta.disabled || live || starting) return;
    starting = true;
    stopDictation();               // one mic page-wide
    btn.classList.add("wait");
    const t0 = performance.now();  // the press — every timing below is from it
    const since = () => Math.round(performance.now() - t0);
    // AudioContext FIRST, synchronously in the click's gesture chain — iOS
    // Safari creates gesture-less contexts suspended and keeps them so
    const ctx = new AudioContext();
    if (ctx.state === "suspended") ctx.resume();
    // What we will actually PUT ON THE WIRE — not what the hardware runs at.
    // The worklet resamples to DICT_RATE (see DICT_WORKLET: native-rate PCM
    // saturated an iPad's uplink and the lag then grew with every sentence);
    // hardware already at or below it passes through untouched. This is the
    // rate baked into the listen URL, so it must be decided BEFORE the mint
    // and handed to the worklet unchanged — one decision, two consumers.
    const outRate = Math.min(DICT_RATE, Math.round(ctx.sampleRate));
    const tokBody = { sample_rate: outRate };
    const cwd = getCwd && getCwd();
    if (cwd) tokBody.cwd = cwd;    // keys the project keyterms layer

    // THREE independent legs, all started here inside the click's gesture
    // chain, none waiting on another (docs/dashboard.md *Instant-on mic*).
    // What used to be a CHAIN — mic+token, then the socket handshake, then the
    // worklet compile — meant the mic went live only after a tunnel round trip,
    // our server's own call to Deepgram, AND a wss handshake from the device
    // had all completed in series. Nothing about compiling a worklet or asking
    // for the microphone depends on any of that. Capture now begins the moment
    // the first two land, and the token/socket finish on their own time with
    // the preroll covering the difference.
    if (!dictWorkletURL)
      dictWorkletURL = URL.createObjectURL(
        new Blob([DICT_WORKLET], { type: "text/javascript" }));
    const micP = navigator.mediaDevices.getUserMedia(
      { audio: { echoCancellation: true, noiseSuppression: true } });
    const modP = ctx.audioWorklet.addModule(dictWorkletURL);
    const tokP = postJSON("/api/dictate/token", tokBody);
    // A leg that fails cancels nothing, so a stream granted AFTER some other
    // leg failed must still be released or the tab's mic indicator sticks on.
    // (First-ever use can sit >30s in the permission prompt and outlive the
    // JWT — the ws then fails its handshake and toasts; the retry has a warm
    // permission.) The bare .catch()es keep an unobserved rejection from
    // reaching window.onunhandledrejection while its real handler is below.
    micP.catch(() => {});
    tokP.catch(() => {});
    modP.catch(() => {});
    const abort = (title, detail) => {     // failed BEFORE we ever captured
      micP.then(s => s.getTracks().forEach(t => t.stop()), () => {});
      if (ctx.state !== "closed") ctx.close();
      btn.classList.remove("wait");
      starting = false;
      toast("ask", title, detail);
    };

    // The splice: everything before/after the caret at mic-start stays put;
    // dictated text grows between them as committed (finalized) + interim
    // (still firming up — REPLACED on every partial, so the box always shows
    // Deepgram's current best guess and corrections happen before your eyes).
    const at = ta.selectionStart != null ? ta.selectionStart : ta.value.length;
    const st = {
      prefix: ta.value.slice(0, at), suffix: ta.value.slice(at),
      committed: "", interim: "", skipFinal: false, painting: false,
      stopping: false, closed: false, lastPainted: null,
      // lag accounting (docs/dashboard.md *Dictation lag*): `sent` counts the
      // audio HANDED TO the socket, `proc` the audio Deepgram has told us it
      // transcribed (its Results carry the segment's own audio clock). The
      // difference splits cleanly in two, which is the whole point — see lag().
      sent: 0, proc: 0, maxQueue: 0, maxSvc: 0, warned: false, timer: null,
      // instant-on bookkeeping: when capture began and when the socket opened
      // (both ms from the press), and whether stop() beat the socket
      armed: 0, opened: 0, closeOnOpen: false,
    };
    const bps = outRate * 2;   // linear16 mono: bytes of wire per second of audio

    // THE PREROLL. Audio captured before the socket is ready waits here instead
    // of being dropped on the floor, and goes out in one burst the instant the
    // socket opens — Deepgram consumes a stream, and does not care that its
    // first seconds arrived faster than real time. This is what makes the mic
    // usable the moment it turns red: the gap between pressing and connecting
    // stops costing you the words you said during it.
    let ws = null;
    const preroll = [];
    let held = 0;                                  // bytes currently held
    const HOLD_MAX = DICT_PREROLL_MAX_S * bps;
    const push = (buf) => { ws.send(buf); st.sent += buf.byteLength / 2; };
    const pump = (buf) => {
      // a stopped or torn-down mic takes no new audio — the graph can deliver
      // one more message after ctx.close() is asked for, and a send() on a
      // closed socket throws
      if (st.stopping || st.closed) return;
      if (ws && ws.readyState === 1) return push(buf);
      preroll.push(buf);
      held += buf.byteLength;
      // A safety valve, not a policy: if the socket is still not up after a
      // MINUTE the dictation is already failing (the JWT itself is ~30s). The
      // oldest goes first only because something has to.
      while (held > HOLD_MAX) held -= preroll.shift().byteLength;
    };
    const flush = () => {
      while (preroll.length) push(preroll.shift());
      held = 0;
    };
    // WHERE the delay lives, measured rather than guessed:
    //   queue   — audio sitting in OUR WebSocket send buffer, i.e. uplink we
    //             cannot keep up with. This is the one WE can fix.
    //   service — audio the network has taken but Deepgram hasn't accounted
    //             for yet. This one is theirs.
    // Both are in seconds-of-audio, so they add up to the delay you SEE.
    const lag = () => {
      const queue = ws && ws.readyState === 1 ? ws.bufferedAmount / bps : 0;
      const sent = st.sent / outRate;
      return { queue, sent, svc: Math.max(0, sent - queue - st.proc) };
    };
    const paint = () => {
      // Once we're STOPPING, never RESURRECT text into a box that something
      // else rewrote after our last paint: `dic.stop()` sends CloseStream and
      // Deepgram flushes its final transcript ASYNC (~1s later, over the wire),
      // which lands after the composer's send already cleared the box — a bare
      // paint would refill the just-sent box AND (via the input event below)
      // re-persist the draft. Same guard finish() uses; scoped to stopping so
      // live-dictation edits (handled by onEdit) still paint normally.
      if (st.stopping && st.lastPainted != null && ta.value !== st.lastPainted)
        return;
      const head = st.prefix + st.committed + st.interim;
      st.painting = true;
      ta.value = st.lastPainted = head + st.suffix;
      ta.setSelectionRange(head.length, head.length);
      ta.dispatchEvent(new Event("input", { bubbles: true }));  // autoGrow &co
      st.painting = false;
    };
    // Typing mid-dictation re-anchors the splice to wherever the caret is:
    // any shown interim becomes plain text (and the final that would repeat
    // it is dropped), then dictation continues from the new anchor.
    const onEdit = () => {
      if (st.painting) return;
      const p = ta.selectionStart != null ? ta.selectionStart : ta.value.length;
      st.skipFinal = !!st.interim;
      st.prefix = ta.value.slice(0, p);
      st.suffix = ta.value.slice(p);
      st.committed = ""; st.interim = "";
    };

    let stream = null;
    const finish = () => {         // the ws is done, clean or not
      if (st.closed) return;
      st.closed = true;
      if (st.timer) { clearInterval(st.timer); st.timer = null; }
      // the summary is what makes a session comparable to the next one — the
      // WORST backlog we ever reached, not whatever the last sample caught.
      // arm_ms/open_ms are the press-to-ready pair: how long until we were
      // HEARING you, and how long until the socket could carry it.
      clog(sid || "", "dictate.stop", {
        rate: outRate, spoke_s: +(st.sent / outRate).toFixed(1),
        max_queue_s: +st.maxQueue.toFixed(2), max_svc_s: +st.maxSvc.toFixed(2),
        arm_ms: st.armed, open_ms: st.opened,
      });
      if (stream) stream.getTracks().forEach(t => t.stop());  // mic light OFF
      if (ctx.state !== "closed") ctx.close();
      // finish() can now run while a socket is still CONNECTING (stop before
      // it came up, or the stop grace expiring), so it owns the close — a
      // socket left to open after we're done would stream into the void
      if (ws) { try { ws.close(); } catch (e) { /* already closed */ } }
      // commit a dangling interim — but never resurrect text into a box
      // something else (the composer's post-send clear) rewrote meanwhile
      if (st.interim && ta.value === st.lastPainted) {
        st.committed += st.interim; st.interim = "";
        paint();
      }
      ta.removeEventListener("input", onEdit);
      btn.classList.remove("rec", "wait", "pre");
      if (dictActive === live) dictActive = null;
      live = null;
    };
    // CloseStream makes Deepgram flush the last partial as a final (painted by
    // onmessage) and close; the timer is the failsafe.
    const closeStream = () => {
      try { ws.send('{"type":"CloseStream"}'); } catch (e) { return finish(); }
      setTimeout(() => {
        try { ws.close(); } catch (e) { /* already closed */ }
        finish();
      }, DICT_FLUSH_MS);
    };

    /* ---- ARM: the mic goes live here, with no socket in sight ------------ */
    try {
      stream = await micP;
    } catch (e) {
      abort("microphone blocked", "allow mic access for this site and retry");
      return;
    }
    try {
      await modP;
      const src = ctx.createMediaStreamSource(stream);
      const sink = new AudioWorkletNode(ctx, "dictate-pcm",
                                        { processorOptions: { outRate } });
      sink.port.onmessage = (e) => pump(e.data);
      src.connect(sink);
      sink.connect(ctx.destination);   // pull the graph; outputs are silence
    } catch (e) {
      abort("dictation failed", "audio pipeline error");
      return;
    }
    st.armed = since();
    // the re-anchor listener belongs to a RUNNING dictation — registered here
    // rather than at the top so a press that never armed (mic denied, worklet
    // failed) leaves nothing attached to the textarea
    ta.addEventListener("input", onEdit);
    // .rec now means what it says — YOUR VOICE IS BEING KEPT. `.pre` rides
    // along until the socket is up, so the button can be honest about the one
    // thing still missing without pretending it isn't listening.
    btn.classList.remove("wait");
    btn.classList.add("rec", "pre");
    // live from the moment we are capturing, not from the moment we connect:
    // the button must toggle to stop, and stopDictation() must be able to
    // reach this mic, during the whole pre-socket window
    starting = false;
    live = {
      stop() {
        if (st.stopping || st.closed) return;
        st.stopping = true;
        btn.classList.remove("rec", "pre");
        if (ws && ws.readyState === 1) return closeStream();
        // Stop beat the socket. If anything was SAID, the preroll is holding
        // it and the connection is still coming — let it land and deliver the
        // words rather than throwing away a sentence you already spoke. If
        // nothing was said, there is nothing to wait for (and no reason to pay
        // for a connection).
        if (!preroll.length) return finish();
        st.closeOnOpen = true;
        setTimeout(finish, DICT_STOP_GRACE_MS);    // it never came up
      },
    };
    dictActive = live;

    /* ---- CONNECT: on its own time, with the preroll covering the gap ----- */
    let tok;
    try {
      tok = await tokP;
    } catch (e) {
      finish();
      toast("ask", "dictation unavailable",
            (e && e.error) || "token mint failed");
      return;
    }
    if (st.closed) return;         // stopped (or gave up) while minting
    try {
      ws = new WebSocket(tok.ws_url, ["bearer", tok.token]);
    } catch (e) {
      finish();
      toast("ask", "dictation failed", "could not reach Deepgram");
      return;
    }
    ws.onmessage = (ev) => {
      let d;
      try { d = JSON.parse(ev.data); } catch (e) { return; }
      if (d.type !== "Results") return;
      // Deepgram stamps every Results with the segment's position in the audio
      // it has consumed — the only clock that says how far it has actually got
      if (typeof d.start === "number" && typeof d.duration === "number")
        st.proc = d.start + d.duration;
      const alt = d.channel && d.channel.alternatives
        && d.channel.alternatives[0];
      const text = (alt && alt.transcript) || "";
      if (d.is_final) {
        if (st.skipFinal) st.skipFinal = false;
        else if (text) st.committed += text + " ";
        st.interim = "";
      } else if (!st.skipFinal) {
        st.interim = text;
      }
      paint();
    };
    ws.onclose = () => {
      const dropped = !st.stopping && !st.closed;
      finish();
      if (dropped)
        toast("ask", "dictation ended", "connection to Deepgram closed");
    };
    ws.onopen = () => {
      // the stop grace can expire before a slow handshake lands: we are done,
      // and this socket has nothing left to carry
      if (st.closed) { try { ws.close(); } catch (e) { /* racing */ } return; }
      const heldS = held / bps;      // what the preroll saved, in seconds
      flush();                       // everything said so far, in one burst
      st.opened = since();
      btn.classList.remove("pre");
      // arm_ms vs open_ms IS the instant-on measurement: the first is the wait
      // you feel, the second the wait you used to feel. preroll_s is the speech
      // that would have been lost between them.
      clog(sid || "", "dictate.start", {
        rate: outRate, native: Math.round(ctx.sampleRate),
        arm_ms: st.armed, open_ms: st.opened, preroll_s: +heldS.toFixed(2),
      });
      if (st.closeOnOpen) return closeStream();   // stop() got here first
      st.timer = setInterval(() => {
        const l = lag();
        if (l.queue > st.maxQueue) st.maxQueue = l.queue;
        if (l.svc > st.maxSvc) st.maxSvc = l.svc;
        clog(sid || "", "dictate.lag", {
          queue_s: +l.queue.toFixed(2), svc_s: +l.svc.toFixed(2),
          sent_s: +l.sent.toFixed(1), buffered: ws.bufferedAmount,
        });
        // Say it out loud, once. A backlog this deep means the text you are
        // watching is seconds behind your mouth and will only fall further —
        // that is worth knowing WHILE you speak, not after you send.
        if (!st.warned && l.queue > DICT_BACKLOG_WARN_S) {
          st.warned = true;
          clog(sid || "", "dictate.backlog", { queue_s: +l.queue.toFixed(2) });
          toast("ask", "dictation lagging",
                "slow upload — the text is behind your voice");
        }
      }, DICT_LAG_MS);
    };
  }

  btn.onclick = () => { if (live) live.stop(); else start(); };
  ta.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && live) { e.stopPropagation(); live.stop(); }
  });
  return { btn, stop: () => { if (live) live.stop(); } };
}

/* ---------- control plane: the message composer ---------- */
// A textarea above the mirror feed that types a message into the session's
// kitty window (POST /message). Enter sends, Shift+Enter is a newline — except
// on an iPad (IS_IPAD), where Enter is a newline and only the button sends. Disabled
// with a hint when the session isn't live or has no window (a headless/daemon
// session — the /message endpoint would 409). The sent text surfaces in the
// stream on its own via the conversation tail, so we only clear + toast —
// unless the response says it QUEUED (see above), which pins a ⧗ queued bubble.

// Both message boxes (the composer and the form's first prompt) grow with
// their content, capped at a viewport fraction so a long paste can't swallow
// the page (the CSS max-height mirrors this cap as 40vh).
const GROW_CAP = 0.4;
function autoGrow(ta) {
  ta.style.height = "auto";
  ta.style.height = Math.min(ta.scrollHeight, Math.round(innerHeight * GROW_CAP)) + "px";
  // re-place the "/command" tint mirror (cmdHighlight): autoGrow is the one call
  // every value change goes through, including the PROGRAMMATIC ones that fire
  // no `input` event (draft restore, an SSE draft, a cleared send)
  if (ta.cmdPaint) ta.cmdPaint();
}

// Persist the unsent composer text to the server (debounced) so a reopen on any
// device — or a return to this session from another — restores it. Best-effort:
// a failed save just retries on the next edit. `ses`/`sid` are captured by the
// composer so a debounce that fires after a view switch still targets the right
// session (S.cur may have moved on). An empty box deletes the stash server-side.
function saveComposerDraft(ses, sid) {
  const ta = ses.composer;
  if (!ta) return;
  // never save while the box is disabled — that is the send-in-flight window
  // (send() disables it, then clearComposerDraft removes the stash): a trailing
  // dictation-final input event landing here would re-persist the just-sent
  // text before `.then` clears the box (the pre-clear half of the resurrection
  // race the paint() guard covers on the box side)
  if (ta.disabled) return;
  const text = ta.value;
  // keep meta in sync so a tab-switch rebuild seeds from what we just typed,
  // and so our own SSE echo (same origin) is a no-op against current state
  if (ses.meta)
    ses.meta.composer_draft = text.trim() ? { text, origin: CLIENT_ID } : null;
  clearTimeout(ses._composerDraftTimer);
  ses._composerDraftTimer = setTimeout(() => {
    // seq (wall-clock at DISPATCH) orders concurrent writes: a debounced save
    // in flight when send() fires its clear must NOT overwrite the clear if it
    // arrives later over the tunnel (the "draft didn't clear after send"
    // reorder, 2026-07-19). The server keeps only the highest seq.
    postJSON("/api/session/" + encodeURIComponent(sid) + "/composer-draft",
             { text, origin: CLIENT_ID, seq: Date.now() }).catch(() => {});
  }, ASK_DRAFT_DEBOUNCE_MS);
}

// Sending consumes the draft — clear it immediately (not debounced), both the
// cache and the server stash, so it never reappears after the message is on its
// way (and, on the resume path, so the adopted session doesn't re-show it).
function clearComposerDraft(ses, sid) {
  clearTimeout(ses._composerDraftTimer);
  if (ses.meta) ses.meta.composer_draft = null;
  // a later seq than any in-flight save, so the clear always wins the race
  // even if an earlier save's POST lands after it (see saveComposerDraft)
  postJSON("/api/session/" + encodeURIComponent(sid) + "/composer-draft",
           { text: "", origin: CLIENT_ID, seq: Date.now() }).catch(() => {});
}

// A peer device's composer draft arrived over SSE. Adopt it into the box — but
// ignore our OWN echo (same origin), and never yank text out from under an
// ACTIVE local edit (the box holding focus is being typed into; ses.meta is
// still updated so the next remote change applies once it blurs).
function applyComposerDraft(draft) {
  const ses = S.ses;
  if (!ses) return;
  const was = (ses.meta && ses.meta.composer_draft) || null;   // what we showed
  if (ses.meta) ses.meta.composer_draft = draft || null;   // for a later rebuild
  if (draft && draft.origin && draft.origin === CLIENT_ID) return;   // our write
  const ta = ses.composer;
  if (!ta) return;
  const text = (draft && draft.text) || "";
  if (ta.value === text) return;
  // Focus normally means "the user is typing here, don't yank it". But a box
  // that still holds EXACTLY the draft we last showed has not been touched —
  // merely clicked into — and refusing the update there left a message sent
  // from the kitty tab sitting in the composer forever, because the clear
  // arrived while the box happened to have focus (reported 2026-07-25).
  const untouched = ta.value === ((was && was.text) || "");
  if (ta === document.activeElement && !untouched) return;
  ta.value = text;
  autoGrow(ta);
  syncSuggestion(ta);   // draft filled/emptied the box → toggle the ghost placeholder
}

// A live input-box ghost suggestion arrived over SSE — the faint "suggested
// answer" Claude Code pre-fills when a turn settles (docs/dashboard.md, *Web
// ghost suggestion*). We surface it as the composer's grey placeholder, shown
// only while the box is empty, accepted with → / Tab (the composer keydown), the
// iPad "use hint" button (no → / Tab on the on-screen keyboard), or replaced the
// instant the user types (a non-empty textarea hides its placeholder natively).
// Mirror only: accepting fills the WEB box; nothing is written back to the TUI.
function applySuggestion(text) {
  const ses = S.ses;
  if (!ses) return;
  if (ses.meta) ses.meta.suggestion = text || null;
  if (ses.composer) syncSuggestion(ses.composer);
}

// Accept the live ghost suggestion INTO the box (the shared body behind the
// → / Tab keydown and the iPad "use hint" button). No-op unless the box is empty
// and a suggestion is live; returns whether it filled the box.
function acceptSuggestion(ses, ta, sid) {
  if (ta.value || !(ses.meta && ses.meta.suggestion)) return false;
  ta.value = ses.meta.suggestion;
  autoGrow(ta); saveComposerDraft(ses, sid); syncSuggestion(ta);
  return true;
}

// Borrow the placeholder slot for the ghost suggestion while the box is empty;
// restore the composer's own default placeholder otherwise (or when there's no
// suggestion). Also toggles the iPad "use hint" button, shown only while a ghost
// is live. Idempotent — safe to call on every input/build/SSE update.
function syncSuggestion(ta) {
  const ses = S.ses;
  const sug = ses && ses.meta && ses.meta.suggestion;
  const ghost = !!(sug && !ta.value);
  ta.placeholder = ghost ? sug : (ta.dataset.defph || "");
  ta.classList.toggle("hasghost", ghost);
  if (ses && ses.hintBtn) ses.hintBtn.hidden = !ghost;
}

/* ---------- composer attachments (images/screenshots + files) ----------
   The browser captures a file (paste of a screenshot, a drag-drop, or the attach
   picker), uploads its bytes to /api/upload, and the server stages it on disk
   and hands back an absolute path. On send, those paths ride the message as
   leading `@path` mentions — the TUI-native way to attach a file — so Claude
   Code itself reads/attaches them (docs/dashboard.md, *Web attachments*). */
// The upload cap is the SERVER's (config.UPLOAD_MAX, served via /api/limits into
// LIMITS) — read at attach time, not captured at load, so the fetched value wins.

// A File → base64 (no data: prefix), the JSON-transport shape /api/upload wants.
function fileToB64(file) {
  return new Promise((res, rej) => {
    const r = new FileReader();
    r.onerror = () => rej(r.error || new Error("read failed"));
    r.onload = () => {
      const s = String(r.result || "");
      const i = s.indexOf(",");          // "data:<mime>;base64,<data>"
      res(i >= 0 ? s.slice(i + 1) : s);
    };
    r.readAsDataURL(file);
  });
}

// The pending-attachment strip + upload plumbing, shared by the live composer
// and the new-session form. getSid() names the session to stage under ("" for
// the form → the server's shared "staging" bucket). onChange() fires whenever
// the set changes so the host can re-evaluate its send/launch button. Returns
// { strip, addFiles, paths, pending, count, clear }.
function attachTray(getSid, onChange) {
  const items = [];                       // {name, is_image, url, path, failed}
  const strip = el("div", "attach-strip");
  const notify = () => {
    strip.classList.toggle("has", items.length > 0);
    if (onChange) onChange();
  };
  const remove = (it) => {
    const i = items.indexOf(it);
    if (i < 0) return;
    if (it.url) URL.revokeObjectURL(it.url);
    items.splice(i, 1);
    draw(); notify();
  };
  function draw() {
    strip.textContent = "";
    for (const it of items) {
      const chip = el("div", "attach-chip"
        + (it.path ? "" : it.failed ? " failed" : " pending"));
      if (it.is_image && it.url) {
        const img = el("img", "attach-thumb");
        img.src = it.url;
        chip.append(img);
      } else {
        chip.append(el("span", "attach-icon", "▤"));
      }
      chip.append(el("span", "attach-name", it.name));
      const x = el("button", "attach-x", "✕");
      x.type = "button";
      x.title = "remove attachment";
      x.onclick = () => remove(it);
      chip.append(x);
      strip.append(chip);
    }
  }
  const add = (file) => {
    if (!file) return;
    // Zero bytes never survives the server (`web-upload` rejects an empty
    // file), so refuse it here rather than parading a failed chip. An empty
    // `__init__.py` package marker is the real-world case, and the red chip it
    // produced is what the "paste is broken" report was originally about.
    if (!file.size) {
      return toast("ask", "empty file",
                   (file.name || "that file") + " has no content to attach");
    }
    if (file.size > LIMITS.upload_max) {
      return toast("ask", "file too large",
                   (file.name || "file") + " exceeds the upload limit");
    }
    const is_image = /^image\//.test(file.type || "");
    const it = {
      name: file.name || (is_image ? "screenshot.png" : "attachment"),
      is_image, url: is_image ? URL.createObjectURL(file) : "",
      path: null, failed: false,
    };
    items.push(it);
    draw(); notify();
    fileToB64(file)
      .then((data) => postJSON("/api/upload", {
        sid: getSid() || "", name: it.name,
        mime: file.type || "application/octet-stream", data }))
      .then((d) => { it.path = d.path; it.is_image = !!d.is_image; draw(); notify(); })
      .catch((e) => {
        it.failed = true; draw(); notify();
        toast("ask", "attachment upload failed", (e && e.error) || "");
      });
  };
  return {
    strip,
    sid: () => getSid() || "",
    addFiles: (files) => { for (const f of files || []) add(f); },
    paths: () => items.filter((it) => it.path).map((it) => it.path),
    pending: () => items.some((it) => !it.path && !it.failed),
    count: () => items.filter((it) => it.path).length,
    clear: () => {
      for (const it of items) if (it.url) URL.revokeObjectURL(it.url);
      items.length = 0; draw(); notify();
    },
  };
}

// Wire the attach picker, clipboard paste (screenshots), and drag-drop onto a tray.
// `ta` is the paste target (textarea); `zone` the drop target (composer wrap /
// prompt box); enabled() gates every path (a parked/headless box takes none).
// Returns the picker button to place in the UI (the hidden <input> rides with
// it in a fragment).
// a paperclip glyph as an inline SVG (not an emoji): the emoji's own
// line-box metrics made the button a different height than the SVG mic beside
// it, so its icon sat misaligned — an SVG sized exactly like `.micbtn svg`
// (15px) lines up and matches the mic's monochrome style.
const CLIP_SVG =
  "<svg viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2'"
  + " stroke-linecap='round' stroke-linejoin='round'><path d='M21.44 11.05l-9.19"
  + " 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1"
  + "-2.83-2.83l8.49-8.48'/></svg>";

/* ---------- pasting a copied FILE: its PATH, like kitty ----------
   Copy a file in Finder or an IDE, paste it here, and the composer used to
   UPLOAD it — the bytes landed in ~/.claude/baqylau-uploads and the message
   went out as `@/…/baqylau-uploads/064783b1-glab.py`. The kitty TUI does the
   opposite and it is what you actually want when you copy a file out of a
   project: it pastes the file's PATH, which Claude Code resolves in place, at
   its real location, still attached to its repo.

   Deciding which gesture happened is the whole problem, and the page CANNOT do
   it. A pasted screenshot and a pasted file both arrive as a `File` on
   `clipboardData`; the `File` carries a basename and bytes and nothing more.
   The one distinguishing fact — is there a real file on disk behind this? — is
   a pasteboard flavor the browser never exposes:

     public.utf8-plain-text   "glab.py"                  ← the BARE NAME
     NSFilenamesPboardType    ["/Users/…/glab.py"]       ← the full path
     public.file-url          "file:///Users/…/glab.py"  ← the full path

   The web platform does not hand a filesystem path to script, by design; no
   clipboard API (`getData`, `navigator.clipboard.read`) surfaces those. So the
   browser reports WHICH files it was given (their basenames) and the SERVER —
   which shares the pasteboard with kitty — answers whether they are real files
   and where (`/api/clipboard/files`, dashboard/clipboard.py). Resolved ⇒ paste
   the paths. Not resolved ⇒ there is no file behind these bytes (a screenshot,
   a copied image region) or we are not on the host (a phone over the tunnel),
   and uploading is both correct and the only option.

   Drag-drop and the paperclip picker deliberately keep uploading: they are the
   "attach this" gestures, and a drag carries its OWN pasteboard, so the
   general (copy) pasteboard would answer about whatever was last copied. */

// A file PASTE: the path when the host's clipboard says these are real files,
// an upload otherwise. Async by necessity (a server round-trip), which is why
// the caller preventDefault'd — the browser would otherwise have pasted its
// own text flavor (the bare NAME) synchronously and we would have to un-write
// it. A failed/refused probe falls through to the upload, so the worst case is
// exactly the old behavior.
function pasteFiles(tray, ta, files) {
  const names = files.map((f) => (f && f.name) || "");
  postJSON("/api/clipboard/files", { sid: tray.sid(), names })
    .then((d) => (d && d.paths) || [], () => [])
    .then((paths) => {
      clog(tray.sid(), "attach.paste",
           { n: files.length, resolved: paths.length });
      if (paths.length) insertAtCaret(ta, paths.join(" "));
      else tray.addFiles(files);
    });
}

// Splice text at the caret and fire `input` so autoGrow / the draft save / the
// ghost suggestion see the edit (the dictation splice's shape, one-shot).
function insertAtCaret(ta, text) {
  const at = ta.selectionStart != null ? ta.selectionStart : ta.value.length;
  const end = ta.selectionEnd != null ? ta.selectionEnd : at;
  const head = ta.value.slice(0, at) + text;
  ta.value = head + ta.value.slice(end);
  ta.setSelectionRange(head.length, head.length);
  ta.focus();
  ta.dispatchEvent(new Event("input", { bubbles: true }));
}

function wireAttach(tray, ta, zone, enabled) {
  const btn = el("button", "cattach");
  btn.type = "button";
  btn.innerHTML = CLIP_SVG;
  btn.title = "attach image or file";
  const input = el("input", "attach-input");
  input.type = "file";
  input.multiple = true;
  input.onchange = () => { tray.addFiles(input.files); input.value = ""; };
  btn.onclick = () => { if (enabled()) input.click(); };
  ta.addEventListener("paste", (e) => {
    if (!enabled()) return;
    const files = [];
    for (const it of (e.clipboardData && e.clipboardData.items) || [])
      if (it.kind === "file") { const f = it.getAsFile(); if (f) files.push(f); }
    if (!files.length) return;          // an ordinary text paste
    e.preventDefault();                 // see pasteFiles: path or upload, async
    pasteFiles(tray, ta, files);
  });
  zone.addEventListener("dragover", (e) => {
    if (!enabled() || !e.dataTransfer || e.dataTransfer.types.indexOf("Files") < 0)
      return;
    e.preventDefault(); zone.classList.add("dropping");
  });
  zone.addEventListener("dragleave", (e) => {
    if (e.target === zone) zone.classList.remove("dropping");
  });
  zone.addEventListener("drop", (e) => {
    zone.classList.remove("dropping");
    if (!enabled()) return;
    // Always an UPLOAD — unlike a paste (pasteFiles). A drop is the explicit
    // "attach this" gesture, and a drag carries its own pasteboard, so asking
    // /api/clipboard/files would answer about whatever was last COPIED.
    const files = (e.dataTransfer && e.dataTransfer.files) || [];
    if (files.length) { e.preventDefault(); tray.addFiles(files); }
  });
  return frag(btn, input);
}

// ↑/↓ history recall for the composer — Claude Code's TUI up-arrow affordance
// (press ↑ on an empty/edge box to pull back a previously-sent message). The
// recall list is the session's REAL delivered prompts: every `.msg.prompt`
// bubble in the feed carries the raw text in data-txt (opshtml.msg_html, the
// same source the rewind picker POSTs), so history survives reloads / device
// switches / a return to the session with no client bookkeeping, and it always
// reflects exactly what was sent — from the composer OR the terminal. We read
// it live off the feed on each navigation so a just-sent message is included
// the moment its bubble lands. The feed is newest-TOP (appendItems inserts
// `afterbegin`), so document order is newest→oldest: index 0 is the MOST RECENT
// prompt, n-1 the oldest. `ses.histIdx` is the cursor: null = the live draft
// line (not navigating), -1 stashed as the pre-nav sentinel; 0..n-1 = a history
// entry. ↑ walks toward older (higher index), ↓ toward newer (lower); ↓ below 0
// returns to the live draft (stashed in `ses.histBase`). Recall is EPHEMERAL —
// deliberately not persisted as a draft (saveComposerDraft) until the user
// actually edits (oninput) or sends. Every move drops a `composer.recall`
// clog beacon (a `web-client` audit row) so the feature is fully audit-covered.
// Returns true when it consumed the key (the caller then preventDefaults).
function recallHistory(ses, ta, up) {
  const navigating = ses.histIdx != null;
  // Enter navigation only from an EDGE — ↑ with the caret at the very start —
  // so the arrow keeps moving the caret inside a multi-line draft otherwise.
  // ↓ from the live line does nothing (we're already at the newest). Once
  // navigating, either arrow keeps navigating regardless of caret position.
  if (!navigating) {
    if (!up) return false;
    if (ta.selectionStart !== 0 || ta.selectionEnd !== 0) return false;
  }
  const hist = [];
  ses.stream.querySelectorAll(".msg.prompt[data-txt]").forEach(n => {
    const t = n.getAttribute("data-txt");
    if (t) hist.push(t);
  });
  if (!hist.length) return navigating;   // nothing to recall (swallow mid-nav)
  let idx = ses.histIdx;
  if (idx == null) { ses.histBase = ta.value; idx = -1; }  // -1 = the live draft line
  idx += up ? 1 : -1;                     // ↑ = older (higher index), ↓ = newer
  if (idx >= hist.length) idx = hist.length - 1;   // clamp at the oldest
  let entry;
  if (idx < 0) {                          // ↓ below the newest → back to the live draft
    ses.histIdx = null;
    ta.value = ses.histBase || "";
    entry = "draft";
  } else {
    ses.histIdx = idx;
    ta.value = hist[idx];
    entry = idx;
  }
  ta.selectionStart = ta.selectionEnd = ta.value.length;   // caret to end
  autoGrow(ta); syncSuggestion(ta);
  clog(S.cur || "", "composer.recall",
       { dir: up ? "up" : "down", idx: entry, n: hist.length });
  return true;
}

// The PARKED composer's send — "resume & send" (docs/dashboard.md *Resume &
// send*): relaunch the conversation through /api/sessions/new with the message
// riding the launch ARGV, under the session's own account. Never typed into a
// half-started TUI, so there is no readiness race.
function composerResume(C, text, atts) {
  const { ses, sid, meta, ta, btn, tray } = C;
  const body = { cwd: meta.cwd, resume: S.cur, prompt: text };
  if (atts.length) body.attachments = atts;
  const slug = meta.account && meta.account.slug;
  if (slug) body.account = slug;   // wake it under ITS account, silently
  postJSON("/api/sessions/new", body, { audit: "resume-send", sid: S.cur })
    .then(() => {
      // the revived session appears via its own SessionStart (then forks
      // sids — adopt); the armed jump follows it, same as a form resume.
      // But the launch POST succeeding is not the session arriving — if it
      // never boots, the composer stays disabled forever (the success path
      // has no finally). onfail revives it when the watch times out so the
      // typed message isn't trapped behind a dead box.
      armJump(meta.cwd, S.cur, { onfail: () => {
        if (S.ses !== ses || ses.composer !== ta) return;   // moved on
        ta.disabled = false; btn.disabled = false;
        saveComposerDraft(ses, sid);   // re-stash (send-start cleared it)
        toast("ask", "resume timed out",
              "the session never came back — your message is kept; try again");
      } });
      tray.clear();
      toast("done", "resuming session", "your message starts the revived turn");
    })
    .catch(e => {
      // the draft survives in the box — nothing is lost on a failed wake
      // (re-persist it: send-start cleared the stash optimistically)
      toast("ask", "resume failed", (e && e.error) || "");
      clientFail(sid, "resume", e, text.length);
      ta.disabled = false; btn.disabled = false; ta.focus();
      saveComposerDraft(ses, sid);
    });
}


// The LIVE composer's send: paste the message into the session's window, with
// an optimistic greyed stand-in bubble until the real transcript prompt lands
// over SSE.
function composerSend(C, text, atts) {
  const { ses, sid, ta, btn, tray, canSend } = C;
  // After an interrupt took the message back (or a rewind restored one) the
  // TUI holds it as a draft and this send must REPLACE it — but that fact is
  // the SERVER's now (launch.tui_draft): a page variable didn't survive a
  // reload while the TUI's draft did, and the next send pasted after the
  // leftover ("testingtesting2", 2026-07-25). The flag stays as an immediate
  // same-page hint; the server ORs its own record in either way.
  const msg = { text };
  if (atts.length) msg.attachments = atts;
  if (ses.clearDraftNext) { msg.clear_draft = true; ses.clearDraftNext = false; }
  // optimistic: show the message immediately (greyed) so there's no gap
  // before its real transcript prompt arrives over SSE — drainPending swaps
  // in the real bubble when it lands (see the optimistic-bubbles section).
  // Only for typed text (empty send = attachments only: nothing to preview).
  const pend = text ? addPending(ses, text) : null;
  postJSON("/api/session/" + encodeURIComponent(S.cur) + "/message", msg,
           { audit: "send", auditData: { chars: (text || "").length } })
    .then(d => {
      ta.value = ""; autoGrow(ta); tray.clear(); ses.histIdx = null;
      if (d && d.queued) {
        // queued mid-turn — the pinned ⧗ queued bubble owns this until
        // delivery; drop the stand-in so the two representations don't double up
        if (pend) settlePending(pend, "dropped", { reason: "queued" });
        ses.queue.push({ text });
        renderQueue();
        saveQueue(ses);
        toast("done", "message queued", "delivers when this turn ends");
      } else {
        toast("done", "message sent", "");
      }
    })
    .catch(e => {
      // send-start cleared the stash optimistically; the box keeps its text,
      // so re-persist it — a reload mustn't lose an unsent message
      if (pend) settlePending(pend, "dropped", { reason: "send-failed" });
      toast("ask", "send failed", (e && e.error) || "");
      clientFail(sid, "send", e, text.length);
      saveComposerDraft(ses, sid);
    })
    .finally(() => {
      // refocus for the next message — except on an iPad, where it would
      // yank the on-screen keyboard back up after a button-tap send
      if (ses.composer === ta) {
        ta.disabled = !canSend; btn.disabled = !canSend;
        if (!IS_IPAD) ta.focus();
      }
    });
}


function buildComposer() {
  const ses = S.ses;
  const meta = ses.meta || {};
  const sid = S.cur;   // the session this composer is bound to (draft target)
  const wrap = el("div", "composer");
  const ta = el("textarea", "cinput");
  ta.rows = 1;
  ta.spellcheck = false;
  const canSend = !!(meta.live && meta.kitty_window_id);
  // RESUME MODE (docs/dashboard.md *Resume & send*): a parked session's
  // composer stays fully usable — typing, "/" menu, dictation — and the one
  // send button (relabeled "resume & send") is the single door from parked
  // to live: it relaunches the conversation through the existing
  // /api/sessions/new resume+prompt path, the message riding the LAUNCH
  // ARGV (never typed into a half-started TUI — no readiness race), under
  // the session's own account. Headless-live stays disabled — those aren't
  // asleep, they just have no window; resume is the wrong medicine.
  // a parked session whose transcript .jsonl is gone can't be resumed —
  // `claude --resume` would find nothing and the tab would die at once, so the
  // server 410s it. Disable the door and say why, don't offer a dead button.
  const gone = !meta.live && !!meta.cwd && !!meta.transcript_missing;
  const canResume = !meta.live && !!meta.cwd && !gone;
  const usable = canSend || canResume;
  ta.disabled = !usable;
  ta.placeholder = canSend
    ? (IS_IPAD ? "message this session…"
               : "message this session…  (Enter to send · Shift+Enter for newline)")
    : canResume
      ? (IS_IPAD ? "message this parked session — sending resumes it"
                 : "message this parked session — sending resumes it  "
                   + "(Enter to resume & send)")
      : gone ? "this session's transcript is gone — it can't be resumed"
      : (meta.live ? "no terminal window — can't message a headless session"
                   : "session is not live");
  // remember the composer's OWN placeholder — a live ghost suggestion borrows
  // the placeholder slot while the box is empty, and this is what it restores
  ta.dataset.defph = ta.placeholder;
  const btn = el("button", "csend", canResume ? "resume & send" : "send");
  btn.disabled = !usable;
  ses.composer = ta;
  ses.histIdx = null;   // a fresh composer starts outside history navigation
  // restore the persisted draft (a device switch / reopen / return-to-session
  // brings back the half-typed message) — only into a usable box. rAF the grow:
  // scrollHeight needs the textarea mounted, which the caller does after this.
  if (usable && meta.composer_draft && meta.composer_draft.text) {
    ta.value = meta.composer_draft.text;
    requestAnimationFrame(() => { if (ses.composer === ta) autoGrow(ta); });
  }
  // iPad "use hint" button — the on-screen keyboard has no → / Tab, so this is
  // the ONLY way to accept a ghost suggestion there. Desktop keeps → / Tab and
  // never builds it. Hidden until syncSuggestion sees a live ghost + empty box.
  const hintBtn = IS_IPAD ? el("button", "chint", "use hint") : null;
  if (hintBtn) {
    hintBtn.type = "button";
    hintBtn.hidden = true;
    hintBtn.title = "insert the suggested reply";
    hintBtn.onclick = () => { if (acceptSuggestion(ses, ta, sid)) ta.focus(); };
    ses.hintBtn = hintBtn;
  }
  syncSuggestion(ta);   // show a live ghost suggestion (if any) into the empty box
  const dic = dictation(ta, () => meta.cwd || "", sid);
  dic.btn.disabled = !usable;    // an honest dead mic beats one that ignores you
  // attachments: staged under this session's id (live) or its own id for a
  // parked resume (the bytes are read once the revived session boots)
  const tray = attachTray(() => S.cur);
  const attachBtn = usable
    ? wireAttach(tray, ta, wrap, () => usable && !ta.disabled)
    : null;
  // The two SEND PATHS were one 89-line closure whose parked branch bailed out
  // of the middle of it. They share only the guards above, so each is its own
  // function now and neither has to be read past to reach the other.
  //
  // C is the composer's context — the widgets and session facts both paths
  // need. Same shape as the new-session form's phases: the closure scope was
  // doing that passing implicitly, and only a reader could tell what each half
  // actually touched.
  const C = { ses, sid, meta, ta, btn, tray, canSend, canResume };

  const send = () => {
    dic.stop();          // the visible (validated) text is what sends
    const text = ta.value.trim();
    const atts = tray.paths();
    if ((!text && !atts.length) || ta.disabled) return;
    if (tray.pending())    // an upload is still in flight — don't drop it
      return toast("ask", "attachment still uploading", "one moment…");
    ta.disabled = true; btn.disabled = true;
    clearComposerDraft(ses, sid);   // sending consumes the draft (both paths)
    (canResume ? composerResume : composerSend)(C, text, atts);
  };
  // cosmetic busy hint: the send button reads "queue" while a turn is running
  // (kept fresh by the `tab` SSE event; the server's verdict stays authoritative)
  ses.composerMode = (tab) => {
    if (!canSend) return;          // the parked/headless labels are fixed
    btn.textContent = QUEUE_TABS.includes(tab) ? "queue" : "send";
  };
  ses.composerMode(((S.sessions.find(r => r.sid === S.cur) || {}).tab)
                   || (meta.tab || ""));
  // the "/" menu — commands for THIS session's cwd, fetched once per view
  const sm = slashMenu(ta, wrap,
    () => cmdsFor(meta.cwd, ses, "cmds", sid),
    { enterSends: !IS_IPAD });
  ta.oninput = () => {
    ses.histIdx = null;   // typing leaves history navigation (see recallHistory)
    autoGrow(ta); saveComposerDraft(ses, sid); syncSuggestion(ta);
  };
  ta.onkeydown = (e) => {
    if (sm.key(e)) return;
    // → / Tab on an EMPTY box accepts the ghost suggestion as real input (the
    // native "right-arrow to accept" affordance) — fills the WEB box only, then
    // send works normally. Typing instead just replaces it (a non-empty box
    // hides the placeholder). Skipped once the box holds text, so it never
    // steals → from caret movement / Tab from the "/" menu (both non-empty).
    if ((e.key === "ArrowRight" || e.key === "Tab") && !ta.value
        && ses.meta && ses.meta.suggestion) {
      e.preventDefault();
      acceptSuggestion(ses, ta, sid);
      return;
    }
    // ↑/↓ recall previously-sent prompts into the box (Claude Code's TUI
    // history affordance). Only kicks in at the top/bottom edge of the box so
    // it never steals arrows from caret movement inside a multi-line draft.
    if ((e.key === "ArrowUp" || e.key === "ArrowDown")
        && recallHistory(ses, ta, e.key === "ArrowUp")) {
      e.preventDefault();
      return;
    }
    if (!IS_IPAD && e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
  };
  btn.onclick = send;
  // order: [attachment strip (full-width, wraps to top)], textarea, attach,
  // mic, [use hint · iPad only], send — the attach sits next to the mic, not
  // stranded past send; the hint button rides just before send when present
  wrap.append(tray.strip, ta);
  if (attachBtn) wrap.append(attachBtn);
  wrap.append(dic.btn);
  if (hintBtn) wrap.append(hintBtn);
  wrap.append(btn);
  return wrap;
}

/* ---------- jump to a freshly launched session ---------- */
// A web launch can't know its session id up front — the server deliberately
// returns no synthetic row; the session appears through its own SessionStart.
// So the launch stashes what we already know and every following global
// snapshot is checked — the first match navigates there. What counts as the
// launched session depends on the start mode:
//   fresh     — a sid we've never seen, live, in the launched cwd;
//   resume    — THAT sid coming (back) to life: SessionStart fires under the
//               OLD sid and restores its parked DB (the fork to a new sid
//               only happens at the first event after, so "new sid" alone
//               never matches — this shipped broken once);
//   continue  — some already-known sid in that cwd flipping parked→live
//               (which one the CLI picks is its own history's business).
// Hence the liveAtArm set: a hit is a live cwd-row that is either brand-new
// OR wasn't live when we armed. Cancelled when the user opens any session
// themselves (route() clears the watch on user navigation) or by the
// timeout: a launch that never produces a session (claude failed to start)
// must not yank the browser somewhere minutes later.
const JUMP_TIMEOUT_MS = 120000;


/* ---------- optimistic prompt bubbles (this composer's own send) ---------- */
// The .md body of a not-yet-delivered prompt bubble (the optimistic stand-in and
// the pinned queued bubble): the text with hard line breaks, textContent only —
// never innerHTML, since an undelivered prompt must never interpret markup.
// The leading "/command" of a message, when it NAMES a real command — the
// deliberate cross-language twin of the server's `_lead_cmd`
// (dashboard/opshtml/tools.py), which tints the transcript bubbles IT renders.
// This one serves the two bubbles the page builds itself and the server never
// sees: the optimistic stand-in and the ⧗ queued chip. The name list is the
// SERVER's (meta.commands, from the same plugins.slash_commands the "/" menu
// uses), so the two renderers can't disagree about what a real command is.
function leadCmd(text) {
  const names = (S.ses && S.ses.meta && S.ses.meta.commands) || null;
  if (!names || !names.length || !(text || "").startsWith("/")) return "";
  const m = /^\/(\S+)(?=\s|$)/.exec(text);
  return m && names.indexOf(m[1]) >= 0 ? m[0] : "";
}

function promptMd(text) {
  const md = el("div", "md");
  const p = el("p");
  const tok = leadCmd(text);
  (text || "").split("\n").forEach((line, i) => {
    if (i) p.append(el("br"));
    if (!i && tok)                       // the token can only lead the FIRST line
      p.append(el("span", "cmdtok", tok), tnode(line.slice(tok.length)));
    else
      p.append(tnode(line));
  });
  md.append(p);
  return md;
}

// Plain-text bubble mirroring opshtml.msg_html's .msg.prompt shape, minus the
// rewind ↶ (a not-yet-delivered prompt isn't re-runnable).
function pendingBubble(text) {
  const d = el("div", "msg prompt pending");
  d.append(el("span", "who", "you"));
  d.append(promptMd(text));
  return d;
}

// Create + track the optimistic stand-in for a send; returns its pend handle.
function addPending(ses, text) {
  const node = pendingBubble(text);
  const w = ses.stream.querySelector(".waiting");
  if (w) w.remove();
  ses.stream.insertBefore(node, ses.stream.firstChild);
  const pend = { text, node, ses, sid: S.cur, t0: performance.now(), timer: null };
  ses.pending.push(pend);
  hintAudit(pend, "shown");
  // watchdog: a stand-in still unreconciled after STALE_HINT_MS is a stuck
  // grey bubble — the failure this audit exists to catch. Fire the beacon once
  // and KEEP the node (the user is still staring at grey; the row is the
  // breadcrumb). Cleared by settlePending / leaveSession on a clean outcome.
  pend.timer = setTimeout(() => {
    pend.timer = null;
    if (pend.ses.pending.indexOf(pend) >= 0) hintAudit(pend, "stale");
  }, STALE_HINT_MS);
  return pend;
}

// Tear a stand-in down (matched | queued | send-failed) and audit the outcome.
function settlePending(pend, phase, extra) {
  if (!pend) return;
  if (pend.timer) { clearTimeout(pend.timer); pend.timer = null; }
  const i = pend.ses.pending.indexOf(pend);
  if (i >= 0) pend.ses.pending.splice(i, 1);
  if (pend.node) pend.node.remove();
  hintAudit(pend, phase, extra);
}

function drainPending(items) {
  const ses = S.ses;
  if (!ses || !ses.pending || !ses.pending.length) return;
  for (const it of items) {
    if (it.t !== "msg" || it.kind !== "prompt") continue;
    const real = (it.text || "").trim();
    // suffix match (promptMatches — the one rule, shared with drainQueue and
    // mirrored server-side): the delivered prompt may carry attachment mentions
    // OR a terminal-restored draft in front of what we sent.
    const i = ses.pending.findIndex(p => promptMatches(real, p.text));
    if (i >= 0) settlePending(ses.pending[i], "reconciled");
  }
}
