"use strict";
// Part of the dashboard SPA — split from the former single app.js into ordered,
// cohesive files (classic scripts share one global scope; load order is set in
// index.html). See app.13-init.js for the boot/init sequence.

// Re-open the per-session stream after a transport disconnect.
// A menu that closes on blur must let the CLICK land first: the mousedown blurs
// the textarea before the click event reaches the row, so closing synchronously
// would swallow every pick. (The rows themselves preventDefault on mousedown;
// this delay is what catches a click AWAY from the menu.)
const MENU_BLUR_MS = 150;
const HISTORY_FETCH = 40;

/* The session view, optionally SCOPED to one agent (docs/dashboard.md *Agent
   scope*). `agent` re-points the mirror, monitors and jobs at that agent — the
   same tabs, the same components, a different `?agent=` on every read — while
   errors stay session-wide (they have no agent dimension). Entering
   or leaving scope on the SAME session rebuilds the stream, because the feed's
   cursors and painted blocks belong to whichever scope produced them. */
function showSession(sessionId, tab, agent) {
  agent = agent || "";
  // Unknown or retired tabs return to the mirror.
  if (!["mirror", "agents", "monitors", "jobs", "errors"].includes(tab))
    tab = "mirror";
  if (S.currentSessionId !== sessionId) {
    leaveSession();
    S.currentSessionId = sessionId;
    S.sessionView = { agent: agent,
              lastId: 0, mpos: 0, oldest: 0, stream: el("div", "stream"), stats: {},
              agents: [], costs: null, contextWindow: null, compacting: null,
              running: {}, meta: null, es: null,
              timer: null, poll: null, itemNodes: new Map(), moreEl: null,
              monitors: null, monitorFocus: null, monPoll: null,
              jobs: null, jobFocus: null, jobPoll: null,
              // the section engine's repaint-skip signatures + the job
              // drill-down's output cache/box (app.11-chrome.js loadSection)
              jobOut: null,
              loadingOlder: false, queue: [], pending: [],
              askPend: null, planPend: null,   // in-flight optimistic ask/plan decisions
              // the view mode + its derived state: `view` is seeded from the
              // session's durable pref when meta lands, and until then from the
              // same default the server would serve — so the first paint doesn't
              // flash the wrong density. `viewOpen` holds the runs the user
              // expanded, `viewSeq` names items, `viewFill` bounds the auto-load
              view: VIEW_DEFAULT, viewOpen: new Set(), viewSeq: 0,
              viewTimer: null, viewFill: 0 };
    loadCanonicalSession(sessionId);
  } else if ((S.sessionView.agent || "") !== agent) {
    // SCOPE CHANGE on the same session (into an agent, between agents, or back
    // out). The feed's cursors and painted blocks belong to the scope that
    // produced them, so the stream is torn down and refetched rather than
    // filtered client-side — the server decides what is in scope, once.
    S.sessionView.agent = agent;
    resetStream();
    resetScopedSections();
    loadCanonicalSession(sessionId);
  }
  S.sessionView.tab = tab;
  renderSessionChrome(tab);
}

/* Drop everything the stream holds so a fresh scope can repaint from zero:
   the SSE (its cursors are scope-relative), the rendered blocks, the lazy
   cursors, and the view-mode bookkeeping that names items by sequence. */
function resetStream() {
  const sessionView = S.sessionView;
  if (sessionView.es) { try { sessionView.es.close(); } catch (e) { /* already closed */ } }
  sessionView.es = null;
  sessionView.lastId = 0; sessionView.mpos = 0; sessionView.oldest = 0;
  sessionView.itemNodes = new Map();
  sessionView.moreEl = null;
  sessionView.loadingOlder = false;
  sessionView.viewOpen = new Set(); sessionView.viewSeq = 0; sessionView.viewFill = 0;
  sessionView.stream.textContent = "";
}

function canonicalActorRow(actor) {
  return {
    id: actor.actor_id,
    agent_id: actor.actor_id,
    parent: actor.parent_actor_id || "",
    kind: actor.role,
    name: actor.name || actor.actor_id,
    desc: actor.description || actor.name || "",
    description: actor.description || "",
    model: actor.model ? actor.model.native_id : "",
    effort: actor.effort || "",
    state: actor.state,
    active: actor.state === "running",
    done: actor.state === "finished",
    started_at: actor.started_at || null,
    ended_at: actor.finished_at || null,
  };
}

function canonicalUsageStats(usage) {
  const tokens = (usage && usage.tokens) || {};
  return {
    tk_in: tokens.input_tokens || 0,
    tk_out: tokens.output_tokens || 0,
    tk_read: tokens.cache_read_tokens || 0,
    tk_create: (tokens.cache_write_tokens || 0)
      + (tokens.one_hour_cache_write_tokens || 0),
    cost: usage && usage.cost_in_usd != null ? Number(usage.cost_in_usd) : null,
  };
}

function canonicalActivityStats(snapshot) {
  const activity = snapshot.statistics || {};
  return Object.assign(canonicalUsageStats(snapshot.usage), {
    commands: activity.shell_command_count || 0,
    failed: activity.failed_shell_command_count || 0,
    files: activity.file_count || 0,
    added: activity.lines_added || 0,
    removed: activity.lines_removed || 0,
    msg_delivered: activity.actor_message_count || 0,
    start: snapshot.session && snapshot.session.started_at || 0,
  });
}

function canonicalSessionMeta(snapshot) {
  const session = snapshot.session || {};
  const actorId = (S.sessionView && S.sessionView.agent) || session.lead_actor_id || "";
  const pendingAttention = ((snapshot.attention || {}).pending || [])
    .find(item => !actorId || item.actor_id === actorId) || null;
  const ask = pendingAttention && pendingAttention.attention_type !== "plan" ? {
    attention_id: pendingAttention.attention_id,
    tool_use_id: pendingAttention.attention_id,
    questions: (pendingAttention.questions || []).map(question => ({
      id: question.question_id,
      header: question.title || "",
      question: question.text,
      multiSelect: !!question.multiple,
      options: (question.options || []).map(option => ({
        value: option.value,
        label: option.label,
        description: option.description || "",
      })),
    })),
  } : null;
  const plan = pendingAttention && pendingAttention.attention_type === "plan" ? {
    attention_id: pendingAttention.attention_id,
    tool_use_id: pendingAttention.attention_id,
    plan_id: pendingAttention.attention_id,
    plan_html: pendingAttention.plan_html || "",
  } : null;
  return {
    harness: session.harness || "",
    title: session.title || "",
    workingDirectory: session.working_directory || "",
    model: session.model ? session.model.native_id : "",
    // the session's CURRENT model, as the last model.changed named it — the ✦
    // button's label and picked entry. Distinct from contextWindow.model, which
    // describes the window the ctx figure was MEASURED against and so only moves
    // on the next assistant record (see setModelBtn).
    model_short: session.model
      ? (session.model.display_name || session.model.native_id) : "",
    model_selection: session.model ? session.model.selection_id : null,
    effort: session.effort || "",
    account: session.account ? {
      slug: session.account.account_id,
      label: session.account.display_name || session.account.account_id,
    } : {},
    prompts: session.prompt_count || 0,
    tasks: (snapshot.tasks || []).map(task => ({
      id: task.task_id,
      label: task.label,
      subject: task.subject,
      description: task.description || "",
      status: task.state,
      owner_actor_id: task.owner_actor_id || null,
    })),
    goal: snapshot.goal ? {
      condition: snapshot.goal.objective,
      met: snapshot.goal.state === "completed",
    } : null,
    ask,
    plan,
    monitor_count: (snapshot.background_work || {}).monitor_count || 0,
    job_count: (snapshot.background_work || {}).background_job_count || 0,
  };
}

function canonicalBackgroundOperation(operation) {
  return {
    task: operation.task,
    agent_id: operation.actor_id || "",
    group: operation.task,
    command: operation.command || "",
    cmd_html: operation.command_html || "",
    description: operation.description || "",
    live: !!operation.live,
    started_at: operation.started_at,
    ended_at: operation.ended_at,
    end_reason: operation.end_reason || "",
    output: operation.output || "",
    lines: operation.line_count || 0,
    persistent: false,
    timeout_ms: null,
    event_count: (operation.events || []).length,
    events_truncated: false,
    events: (operation.events || []).map(event => ({
      event: event.event || "",
      status: event.status || "",
      summary: event.summary || "",
      ts: event.timestamp,
    })),
  };
}

function applyCanonicalBackgroundWork(backgroundWork) {
  if (!S.sessionView) return;
  S.sessionView.monitors = (backgroundWork.monitors || []).map(canonicalBackgroundOperation);
  S.sessionView.jobs = (backgroundWork.jobs || []).map(canonicalBackgroundOperation);
}

function applySessionApplication(snapshot) {
  if (!S.sessionView || !snapshot) return;
  const terminal = snapshot.terminal || {};
  const input = terminal.input_state || {};
  const preferences = snapshot.preferences || {};
  const composer = snapshot.composer || {};
  const dialog = snapshot.dialog || {};
  const errors = snapshot.errors || [];
  S.sessionView.errors = errors;
  updateErrCount(errors.length);
  S.sessionView.meta = Object.assign({}, S.sessionView.meta || {}, {
    live: !!terminal.window_id,
    terminal_window_id: terminal.window_id || "",
    suggestion: input.suggestion || "",
    typed_text: input.typed_text || "",
    view_mode: preferences.view_mode || "default",
    notify_muted: !!preferences.notifications_muted,
    tasks_hidden: !!preferences.tasks_hidden,
    composer_draft: composer.draft || null,
    composer_queue: composer.queue || null,
    ask_draft: dialog.draft || null,
  });
  applyComposerDraft(composer.draft || null);
  applyComposerQueue(composer.queue || null);
  applyAskDraft(dialog.draft || null);
}

function applyCanonicalSnapshot(snapshot) {
  if (!S.sessionView || !snapshot) return;
  const previous = S.sessionView.meta || {};
  S.sessionView.meta = Object.assign({}, previous, canonicalSessionMeta(snapshot));
  S.sessionView.meta.tab = snapshot.tab_state || "";
  // A rename Claude Code makes itself (session.title_changed, origin "automatic")
  // rides this same snapshot rather than a dedicated push — repaint the header
  // span in place so it doesn't wait for the next full chrome rebuild (a live↔
  // parked flip or window move). Skipped mid inline-rename (the input owns the
  // span) and in agent scope (updateAgents' renderAgentScoreboard, called right
  // after this in the "activity" handler, owns the span there).
  if (S.sessionView.projEl && !S.sessionView.agentFocus
      && !S.sessionView.projEl.querySelector("input")
      && S.sessionView.meta.title !== previous.title) {
    S.sessionView.projEl.textContent = S.sessionView.meta.title
      || directoryName(S.sessionView.meta.workingDirectory)
      || shortSid(S.currentSessionId);
  }
  applyCanonicalBackgroundWork(snapshot.background_work || {});
  S.sessionView.stats = canonicalActivityStats(snapshot);
  const leadActorId = (snapshot.session || {}).lead_actor_id || "";
  S.sessionView.agents = (snapshot.actors || [])
    .filter(actor => actor.actor_id !== leadActorId)
    .map(canonicalActorRow);
  S.sessionView.costs = {
    total_usd: snapshot.usage && snapshot.usage.cost_in_usd != null
      ? Number(snapshot.usage.cost_in_usd) : null,
  };
  const actorId = S.sessionView.agent || ((snapshot.session || {}).lead_actor_id || "");
  S.sessionView.contextWindow = actorId && snapshot.context && snapshot.context.by_actor
    ? (() => {
        const context = snapshot.context.by_actor[actorId];
        if (!context) return null;
        const used = context.used_tokens || 0;
        const window = context.window_tokens || 0;
        return {
          used,
          window,
          pct: window ? Math.round(used * 100 / window) : 0,
          model_short: context.model
            ? (context.model.display_name || context.model.native_id) : "",
          model_selection: context.model ? context.model.selection_id : null,
        };
      })() : null;
  S.sessionView.compacting = snapshot.context
    && (snapshot.context.compacting_actor_ids || []).includes(actorId)
    ? { active: true } : null;
  const backgroundWork = snapshot.background_work || {};
  const monitorCount = backgroundWork.monitor_count || 0;
  const backgroundCount = backgroundWork.background_job_count || 0;
  const runningCount = (backgroundWork.running_operation_ids || []).length;
  S.sessionView.running = {
    operation: Math.max(0, runningCount - monitorCount - backgroundCount),
    background: backgroundCount,
    monitor: monitorCount,
  };
}

function canonicalSessionQuery() {
  const actor = (S.sessionView && S.sessionView.agent) || "";
  return actor ? "?actor_id=" + encodeURIComponent(actor) : "";
}

function applyCanonicalCatalog(snapshot, catalog) {
  if (!S.sessionView || !S.sessionView.meta || !snapshot || !snapshot.session) return;
  const harness = snapshot.session.harness || "";
  const host = hostRow(harness);
  const controls = new Set((host && host.controls) || []);
  S.sessionView.meta.host_label = (host && host.label) || harness;
  const models = catalog.models || [];
  S.sessionView.meta.model_choices = models.map(option => option.model_id);
  // Efforts belong to the MODEL, not the harness (ModelOption.efforts): codex's
  // gpt-5.6-luna has no Ultra while its siblings do, so one flat per-harness
  // list offered a level the picker would then refuse. Keep the map, and default
  // the flat list to the default model's for the moment before one is known.
  S.sessionView.meta.model_efforts = {};
  for (const option of models)
    S.sessionView.meta.model_efforts[option.model_id] =
      (option.efforts || []).map(effort => effort.value);
  const fallbackModel = models.find(option => option.default) || models[0];
  S.sessionView.meta.effort_choices =
    fallbackModel ? (fallbackModel.efforts || []).map(effort => effort.value) : [];
  S.sessionView.meta.rewind_modes = (catalog.rewind_modes || []).map(option => ({
    mode: option.value,
    label: option.display_name,
  }));
  S.sessionView.meta.quick_commands = (catalog.commands || []).map(option => ({
    cmd: option.command,
    min_prompts: option.minimum_prompt_count || 0,
  }));
  S.sessionView.meta.caps = {
    send: controls.has("send_text"),
    interrupt: controls.has("interrupt"),
    close: controls.has("close_session"),
    rename: controls.has("rename_session"),
    autoname: controls.has("auto_name_session"),
    rewind: controls.has("apply_rewind"),
    migrate: controls.has("migrate_account"),
    compact: controls.has("compact"),
    model: controls.has("select_model"),
    effort: controls.has("select_effort"),
    answer: controls.has("answer_question"),
    plan: controls.has("decide_plan"),
  };
}

function loadCanonicalSession(sessionId) {
  const query = canonicalSessionQuery();
  S.sessionView.stream.append(el("div", "waiting", "waiting for activity…"));
  loadCanonicalSessionSnapshot(sessionId, query);
  fetch("/api/sessions/" + encodeURIComponent(sessionId) + "/activity?block_count=100"
        + (query ? "&" + query.slice(1) : ""))
    .then(r => r.json())
    .then(page => {
      if (S.currentSessionId !== sessionId || !S.sessionView) return;
      S.sessionView.lastId = page.latest_cursor || 0;
      S.sessionView.oldest = page.has_more ? (page.oldest_cursor || 0) : 0;
      appendItems(page.items || []);
    })
    .catch(() => { clog(sessionId, "backlog.fail", {}); })
    .finally(() => { if (S.currentSessionId === sessionId) connectCanonicalSession(sessionId); });
}

function loadCanonicalSessionSnapshot(sessionId, query) {
  let stage = "transport";
  fetch("/api/sessions/" + encodeURIComponent(sessionId) + query)
    .then(response => {
      stage = "response";
      if (!response.ok) {
        const error = new Error("session metadata " + response.status);
        error.status = response.status;
        throw error;
      }
      stage = "decode";
      return response.json();
    })
    .then(page => {
      stage = "render";
      if (S.currentSessionId !== sessionId || !S.sessionView) return;
      const snapshot = page.canonical || {};
      applyCanonicalSnapshot(snapshot);
      applySessionApplication(page.application || {});
      renderSessionChrome(S.sessionView.tab || "mirror");
      applyViewMode();
      if (S.sessionView.meta.suggestion) applySuggestion(S.sessionView.meta.suggestion);
      const session = snapshot.session || {};
      const catalogQuery = "?session_id=" + encodeURIComponent(sessionId)
        + "&working_directory=" + encodeURIComponent(session.working_directory || "");
      fetch("/api/harnesses/" + encodeURIComponent(session.harness) + "/catalog"
            + catalogQuery)
        .then(response => response.json())
        .then(catalog => {
          if (S.currentSessionId !== sessionId || !S.sessionView) return;
          applyCanonicalCatalog(snapshot, catalog);
          renderSessionChrome(S.sessionView.tab || "mirror");
        })
        .catch(() => { clog(sessionId, "catalog.fail", {}); });
    })
    .catch(error => {
      clog(sessionId, "meta.fail", {
        stage,
        status: (error && error.status) || 0,
        error: String((error && error.message) || error || "").slice(0, 160),
      });
    });
}

function connectCanonicalSession(sessionId) {
  if (!S.sessionView || S.currentSessionId !== sessionId) return;
  if (S.sessionView.es) { try { S.sessionView.es.close(); } catch (error) { /* already closed */ } }
  const actor = (S.sessionView.agent || "");
  const query = "?after_cursor=" + (S.sessionView.lastId || 0)
    + (actor ? "&actor_id=" + encodeURIComponent(actor) : "");
  const stream = new EventSource(
    "/api/sessions/" + encodeURIComponent(sessionId) + "/stream" + query
  );
  S.sessionView.es = stream;
  stream.addEventListener("activity", event => {
    if (!S.sessionView || S.currentSessionId !== sessionId) return;
    const frame = JSON.parse(event.data);
    S.sessionView.lastId = Math.max(S.sessionView.lastId || 0, frame.cursor || 0);
    applyCanonicalSnapshot(frame.snapshot);
    appendItems(frame.items || []);
    updateStatsRow();
    updateAgents();
    updateRunning();
    renderAsk();
    renderPlan();
    renderTasks();
    renderGoal();
    if (S.sessionView.tab === "monitors" || S.sessionView.monitorFocus) loadSection("monitors");
    if (S.sessionView.tab === "jobs" || S.sessionView.jobFocus) loadSection("jobs");
    if (S.sessionView.meta.suggestion) applySuggestion(S.sessionView.meta.suggestion);
  });
  stream.addEventListener("application", event => {
    if (!S.sessionView || S.currentSessionId !== sessionId) return;
    applySessionApplication(JSON.parse(event.data));
    applyViewMode();
    if (S.sessionView.meta.suggestion) applySuggestion(S.sessionView.meta.suggestion);
  });
}

const RUN_TIMER_INTERVAL_MS = 1000;

function dashboardNode(item) {
  const container = el("div");
  container.innerHTML = item.html;
  const node = container.firstElementChild;
  if (!node || node.nextElementSibling)
    throw new Error("DashboardItem.html must contain one top-level node");
  stampItem(node, item);
  if (node.classList.contains("blk"))
    node.dataset.open = S.sessionView.view === "verbose" ? "1" : "0";
  bindCanonicalContent(node, item);
  bindDashboardBlock(node);
  return node;
}

function canonicalContentUrl(reference) {
  return "/api/content/" + encodeURIComponent(reference);
}

function canonicalContentLinks() {
  const links = el("span", "cl");
  const copy = el("a", "cc canonical-content", "⧉copy");
  copy.dataset.contentAction = "copy";
  const view = el("a", "cc canonical-content", "⧉view");
  view.dataset.contentAction = "view";
  links.append(copy, view);
  return links;
}

function canonicalOperationContentLinks(item) {
  const links = el("span", "cl");
  for (const [label, reference] of [
    ["⧉cmd", item.command_reference],
    ["⧉out", item.output_reference],
  ]) {
    if (!reference) continue;
    const copy = el("a", "cc canonical-content", label);
    copy.dataset.contentAction = "copy";
    copy.dataset.contentReference = reference;
    links.append(copy);
  }
  return links;
}

function bindCanonicalContent(node, item) {
  const links = node.querySelector(".blinks");
  if (item.command_reference || item.output_reference) {
    if (links) links.append(canonicalOperationContentLinks(item));
    return;
  }
  if (item.content_reference) {
    node.dataset.contentReference = item.content_reference;
    if (item.file_path) node.dataset.filePath = item.file_path;
    if (links) links.append(canonicalContentLinks());
    if (item.item_type === "file") node.dataset.contentAction = "view";
  }
}

function toggleCanonicalContent(node, text) {
  const next = node.nextElementSibling;
  if (next && next.classList.contains("view-block")) {
    next.remove();
    return;
  }
  const view = el("div", "view-block");
  if (node.dataset.itemGroup === "files") view.innerHTML = text;
  else view.append(pre(text));
  node.insertAdjacentElement("afterend", view);
}

function canonicalViewUrl(node) {
  const url = canonicalContentUrl(node.dataset.contentReference);
  if (node.dataset.itemGroup !== "files") return url;
  const view = node.dataset.summaryKind === "file_edit" ? "diff" : "source";
  const query = new URLSearchParams({ view, path: node.dataset.filePath });
  return url + "?" + query.toString();
}

document.addEventListener("click", event => {
  const actionNode = event.target.closest && event.target.closest("[data-content-action]");
  if (!actionNode) return;
  const itemNode = actionNode.closest("[data-content-reference]");
  if (!itemNode) return;
  // A whole-item action (the node carries data-content-action itself, like a
  // file line) must yield to a nested link/button the click actually landed on.
  const interactive = event.target.closest("a,button");
  if (actionNode === itemNode && interactive && interactive !== actionNode) return;
  event.preventDefault();
  const action = actionNode.dataset.contentAction;
  fetch(action === "view"
    ? canonicalViewUrl(itemNode)
    : canonicalContentUrl(itemNode.dataset.contentReference))
    .then(response => {
      if (!response.ok) throw new Error("content request failed");
      return response.text();
    })
    .then(text => {
      if (action === "view") return toggleCanonicalContent(itemNode, text);
      if (action !== "copy") throw new Error("unknown content action");
      if (!navigator.clipboard) throw new Error("clipboard unavailable");
      return navigator.clipboard.writeText(text).then(() =>
        toast("done", "copied block", text.length + " chars"));
    })
    .catch(() => toast("ask", action + " failed", "try again"));
});

function bindDashboardBlock(node) {
  if (!node.classList.contains("blk")) return;
  const header = node.querySelector(".bhead");
  const body = node.querySelector(".bbody");
  if (!header || !body) throw new Error("dashboard block is missing its header or body");
  header.onclick = event => {
    if (event.target.closest("a") || !body.childElementCount) return;
    node.dataset.userset = "1";
    node.dataset.open = node.dataset.open === "1" ? "0" : "1";
  };
}
// The BROWSER's half of the semantic actor-assignment order (docs/dashboard.md,
// *Semantic actor-assignment order*). The server orders a child's completion and the
// parent turn's final answer whenever both are in ONE payload (read/mirror.
// task_order, backlog and live delta alike) — but a completion whose answer went
// out on an EARLIER tick arrives with that bubble already on screen, and the feed
// is newest-TOP, so prepending puts the `Agent finished` card ABOVE the answer it
// contributed to. This is that case: an END endpoint of a task whose parent turn's
// answer is in the feed lands just BELOW that bubble (below == older here).
//
// Returns the bubble to land under, or null — an item that is not a task END, a
// task whose parent turn is unknown (every Claude agent: core/childtask.py), or an
// answer not on screen (the ordinary case — the server already placed it) all
// prepend exactly as before. A SCAN, not a selector: a turn id is opaque and
// `querySelector` would need it escaped, where the top-level children are the
// bubbles and there are at most a few thousand of them.
function assignmentAnchor(it) {
  if (it.actor_assignment_phase !== "finished" || !it.turn_id) return null;
  for (const elem of S.sessionView.stream.children)
    if (elem.dataset && elem.dataset.final === "1"
        && elem.dataset.turn === it.turn_id) return elem;
  return null;
}

function appendItems(items) {
  const stream = S.sessionView.stream;
  const waiting = stream.querySelector(".waiting");
  if (waiting) waiting.remove();
  for (const item of items) {
    const node = dashboardNode(item);
    const existing = S.sessionView.itemNodes.get(item.item_id);
    if (existing && existing.isConnected) {
      node.dataset.viewKey = existing.dataset.viewKey;
      if (existing.dataset.userset) {
        node.dataset.userset = existing.dataset.userset;
        node.dataset.open = existing.dataset.open;
      }
      existing.replaceWith(node);
    } else {
      const anchor = assignmentAnchor(item);
      if (anchor) stream.insertBefore(node, anchor.nextElementSibling);
      else stream.prepend(node);
    }
    S.sessionView.itemNodes.set(item.item_id, node);
  }
  drainQueue(items);
  drainPending(items);
  dropSuperseded(items);
  while (stream.childElementCount > 3000) {
    let last = stream.lastElementChild;
    if (last === S.sessionView.moreEl) last = last.previousElementSibling;  // the load-older
    if (!last) break;                          //   affordance stays pinned at the bottom
    for (const [itemId, itemNode] of S.sessionView.itemNodes)
      if (itemNode === last) S.sessionView.itemNodes.delete(itemId);
    last.remove();
  }
  tintAgentNotes();              // the notes' dots follow their agents' outcomes
  applyViewMode();               // re-cut the collapsed runs over the final DOM
  ensureElapsedTimer();
  updateShownCount();
}

// The lazy-backlog downward path (item 3): a chunk of OLDER items (server order
// oldest->newest) appended at the BOTTOM of the feed — the feed is newest-top,
// so older loads downward, and each successive page is older still, going lower.
// The page is laid out REVERSED (filled in server order, inserted last
// first), because the feed is newest-top and a page is only a page: inserting it
// in arrival order made the loaded stretch read bottom-up (oldest first) while
// the live tail above it read top-down. Each item takes the position a live
// top-prepend would have given it, keeping the whole feed monotonic.
function appendOlder(items) {
  const stream = S.sessionView.stream;
  const fragment = document.createDocumentFragment();
  const nodes = items.map(dashboardNode);
  for (let index = nodes.length - 1; index >= 0; index--) fragment.append(nodes[index]);
  if (S.sessionView.moreEl) stream.insertBefore(fragment, S.sessionView.moreEl);
  else stream.append(fragment);
  tintAgentNotes();              // history's notes carry their outcome too
  applyViewMode();
  updateShownCount();
}

// Render a self-contained mirror snapshot into an ARBITRARY container (the
// resume-picker's preview panel), not the live #stream. It paints server items
// in arrival order, so the preview
// reads oldest->newest (chronological). It is a short standalone tail read
// top-down, not a feed being prepended to, and nothing lands in it after the
// render — the newest-top convention (and appendOlder's page reversal) exists
// for a stream that keeps growing at one end. Blocks render FOLDED
// (like history) — a compact scannable peek: command/file/agent blocks collapse
// to their one-line summary, while conversation messages (ungrouped items) show
// inline in full; a click on any block header expands it.
function renderPreview(container, items) {
  container.textContent = "";
  for (const item of items) container.append(dashboardNode(item));
  if (!container.childElementCount)
    container.append(el("div", "nspreview-empty", "no mirror history"));
}

// The "load older" affordance: a button pinned at the BOTTOM of the feed (a
// child of the stream, so appendItems' top-prepends never disturb it), shown
// while older blocks remain (S.sessionView.oldest > 0) and hidden once /history is
// exhausted (oldest 0). Each click fetches the previous page and appends it
// downward via appendOlder; filters apply to those items in appendOlder.
function ensureMoreEl() {
  const sessionView = S.sessionView;
  if (!sessionView) return null;
  if (sessionView.moreEl && sessionView.moreEl.isConnected) return sessionView.moreEl;
  const b = el("button", "loadmore");
  b.hidden = true;
  b.onclick = () => loadOlder();
  sessionView.moreEl = b;
  sessionView.stream.append(b);                          // bottom of the feed
  return b;
}

function updateMoreBtn() {
  const sessionView = S.sessionView;
  if (!sessionView) return;
  const b = ensureMoreEl();
  if (!b) return;
  const has = (sessionView.oldest | 0) > 0;
  b.hidden = !has;
  if (has && !sessionView.loadingOlder)
    // "blocks" only in verbose, where a block IS what appears. In default/focus
    // the promise is kept in VISIBLE items (see loadOlder) — most of the blocks
    // fetched to satisfy it collapse into the summary lines, so promising
    // "blocks" there would be promising the wrong noun.
    b.textContent = "load older · " + HISTORY_FETCH
      + (sessionView.view === "verbose" ? " more blocks…" : " more…");
}

// What the reader can actually SEE right now: unhidden stream items plus the
// collapsed-run summary lines standing in for the rest.
function visibleCount() {
  const sessionView = S.sessionView;
  if (!sessionView || !sessionView.stream) return 0;
  return streamItems().filter(elem => !itemHidden(elem)).length
    + sessionView.stream.querySelectorAll(".vsum").length;
}

const OLDER_TRIES = 6;        // /history requests one fill may spend
const OLDER_PAGE_MAX = 400;   // blocks per request ceiling

// How big to make the NEXT page, given what this one cost: `blocks` fetched
// yielded `gained` visible items against a target of `want`. Aim the next
// request at the remaining shortfall at the observed yield — a page that
// collapsed almost entirely (or merged wholly into an existing run, gaining
// nothing) reaches straight for the ceiling. Converges in 2-3 requests instead
// of creeping 40 at a time, which matters because every /history call re-merges
// the session's whole op+conversation history server-side.
function olderPageSize(want, gained, blocks) {
  const per = gained > 0 ? blocks / gained : OLDER_PAGE_MAX;
  const next = Math.ceil((want - gained) * per);
  return Math.max(HISTORY_FETCH, Math.min(next, OLDER_PAGE_MAX));
}

// Load older history until `want` MORE VISIBLE items have landed (default: the
// HISTORY_FETCH the button promises), history is exhausted, or the request
// budget runs out.
//
// It loops because the server counts BLOCKS and the modes hide most of them: one
// 40-block page can collapse to two summary lines — or to nothing at all, when
// every block in it merges into the run already at the boundary. That was the
// "load older 40 blocks doesn't give me 40" report; the fix has to live here,
// since only the client knows what its current mode leaves visible.
function loadOlder(want) {
  const sessionView = S.sessionView;
  if (!sessionView || sessionView.loadingOlder || (sessionView.oldest | 0) <= 0) return;
  const target = want || HISTORY_FETCH;
  const start = visibleCount();
  const sessionId = S.currentSessionId;
  let tries = 0, blocks = HISTORY_FETCH;
  sessionView.loadingOlder = true;
  if (sessionView.moreEl) sessionView.moreEl.textContent = "loading…";

  const step = () => fetch("/api/sessions/" + encodeURIComponent(sessionId)
                           + "/activity?before_cursor=" + (sessionView.oldest | 0)
                           + "&block_count=" + blocks
                           + ((sessionView.agent || "")
                             ? "&actor_id=" + encodeURIComponent(sessionView.agent) : ""))
    .then(r => r.json())
    .then(d => {
      if (S.currentSessionId !== sessionId || !S.sessionView) return;         // navigated away mid-fetch
      tries++;
      appendOlder(d.items || []);
      sessionView.oldest = d.has_more ? (d.oldest_cursor | 0) : 0;
      const gained = visibleCount() - start;
      if (gained >= target || (sessionView.oldest | 0) <= 0 || tries >= OLDER_TRIES) return;
      blocks = olderPageSize(target, gained, blocks);
      if (sessionView.moreEl) sessionView.moreEl.textContent = "loading… " + gained + "/" + target;
      return step();
    });

  step().catch(() => {}).then(() => {
    if (S.currentSessionId !== sessionId || !S.sessionView) return;
    sessionView.loadingOlder = false;
    updateMoreBtn();
  });
}

/* ---------- stream item kinds ---------- */
// Every top-level stream child carries a dashboard-owned kind. View modes use
// this explicit field and never infer it from HTML, glyphs, or harness names.

function dashboardItemGroup(item) {
  if (["message", "reasoning", "attention"].includes(item.item_type)) return "messages";
  if (item.item_type === "file") return "files";
  if (item.item_type === "actor_assignment") return "agents";
  return "commands";
}

// Stamp one freshly-created top-level stream child with everything the view-mode
// pass reads off the DOM: its item kind, its served activity class + failure
// flag, the conversation kind (focus mode narrows on it), a monotonic key that
// names the item for as long as it lives, and its recorded activity time.
function stampItem(node, item) {
  node.dataset.itemGroup = dashboardItemGroup(item);
  node.dataset.summaryKind = item.summary_kind;
  if (item.state === "failed" || item.state === "cancelled") node.dataset.bad = "1";
  if (item.lines_added) node.dataset.add = String(item.lines_added);
  if (item.lines_removed) node.dataset.rem = String(item.lines_removed);
  if (item.conversation_kind) node.dataset.conversationKind = item.conversation_kind;
  // WHICH TURN this bubble belongs to, and whether it is that turn's FINAL answer
  // The answer bubble anchors a late actor-assignment completion
  // (assignmentAnchor) — the one thing in the feed a later item has to be
  // able to find.
  if (item.turn_id) node.dataset.turn = item.turn_id;
  if (item.final) node.dataset.final = "1";
  if (item.actor_assignment_id) {
    node.dataset.actorAssignmentId = item.actor_assignment_id;
    node.dataset.actorAssignmentPhase = item.actor_assignment_phase;
  }
  if (item.actor_assignment_id) node.dataset.summaryKindorId = item.actor_assignment_id;
  if (item.message_id) node.dataset.messageId = item.message_id;
  if (item.state) node.dataset.state = item.state;
  if (item.started_at) node.dataset.startedAt = String(item.started_at);
  if (item.finished_at) node.dataset.finishedAt = String(item.finished_at);
  node.dataset.viewKey = String(++S.sessionView.viewSeq);
  node.dataset.summaryKindivityTime = String(item.started_at || item.finished_at || 0);
}

// An agent note's DOT carries the OUTCOME, exactly like a collapsed run's `.vdot`:
// grey while the agent is still going, green once it finished, red when it didn't.
// It was always dim, so `⏺ Agent "Fix common/ui terminal bugs" finished` said nothing
// about how it went ("why is it grey and not green/red based on the outcome?").
//
// The outcome cannot come off the op — a LAUNCH note is written before there is one,
// and even a finish note only knows its own op failed — so it is joined from the
// agents payload by `data-agent`, through `agentStatus()`: the SAME st-run/st-ok/st-bad
// vocabulary the rail's cards read, so a note and its card can never disagree. Stamped
// as `data-out` on the ROW (the CSS tints the mark inside it), and re-run on every
// `agents` SSE, which is how a launch note goes green the moment its agent ends.
function tintAgentNotes() {
  const sessionView = S.sessionView;
  if (!sessionView || !sessionView.stream) return;
  const by = new Map();
  for (const a of sessionView.agents || []) by.set(a.agent_id, a);
  for (const row of sessionView.stream.querySelectorAll("[data-actor-id]")) {
    const a = by.get(row.dataset.summaryKindorId);
    const st = a ? agentStatus(a)[1] : "";
    // a failing op inside the block reddens it too (`data-bad` — the same rule the
    // run summary's dot follows), so a bad result shows even before the agent's row
    // has caught up
    row.dataset.out = (row.dataset.bad === "1" || st === "st-bad") ? "bad"
      : st === "st-ok" ? "ok" : "run";
  }
}

function streamItems() {
  return [...S.sessionView.stream.children].filter(
    element => element.dataset && element.dataset.itemGroup);
}

// Hidden by the ONE remaining axis: `.vhide` is the view mode's. (There used to
// be a second — a kind-filter chip row, `.fhide` — dropped as unused: the view
// modes are the density control people actually reach for, and `data-kind`
// survives it because the memory routing and the run summaries read it.)
function itemHidden(elem) {
  return elem.classList.contains("vhide");
}

function updateShownCount() {
  const sessionView = S.sessionView;
  if (!sessionView || !sessionView.countEl || !sessionView.countEl.isConnected) return;
  const items = streamItems();
  const shown = items.filter(elem => !itemHidden(elem)).length;
  sessionView.countEl.textContent = shown + " of " + items.length + " shown";
}

/* ---------- view modes: verbose · default · focus ---------- */
// Claude Code's three transcript densities, over the web mirror (docs/
// dashboard.md, *View modes*). This changes only what the BROWSER paints — it
// never touches Claude Code's own `viewMode` setting, and the terminal mirror keeps
// painting everything.
//
//   verbose — every block; nothing is ever hidden
//   default — runs of adjacent read/command/agent activity collapse into ONE
//             clickable summary line; file MUTATIONS stay expanded, so an edit
//             always breaks the run and is always visible
//   focus   — your prompts, ONE message per turn (the one it ends on) and one
//             line of edits; every intermediate step folds away. That message is
//             greyed while the turn is still running (it is provisional) and
//             full weight once the turn settles (it is the result)
//
// Both non-verbose modes also drop SYSTEM messages (`conversation_kind ===
// "system"` — hook feedback, meta results, and other harness-injected text
// that isn't a user prompt or an assistant reply). Verbose is the only mode
// that shows them.
//
// Both non-verbose modes also drop INJECTED prompts — user-shaped turns Claude
// Code wrote itself (a Stop hook's feedback, a loaded skill's body, a resume
// nudge, the post-/compact summary; `data-injected`, from transcript._injected).
// Verbose keeps them: it shows the transcript as it is, and they are genuinely
// in it. One of them still MOVES focus mode without being shown: an injection
// that resumed an ENDED turn (`data-resumed` — a blocking Stop hook) marks the
// reply above it as that turn's final answer.
//
// Must match dashboard/prefs.py VIEW_MODES / VIEW_DEFAULT (grep-tested). The
// list is in CONTROL order (densest to sparsest); the default is NOT its first
// entry — a session nobody switched opens at "default", like the TUI.
const VIEW_MODES = ["verbose", "default", "focus"];
const VIEW_DEFAULT = "default";

// Which activity classes each mode folds into a summary. Everything not listed
// stays its own visible block, which is also what an unclassified item gets —
// a classification gap fails toward SHOWING content, never toward hiding it.
//
// `agent` folds in FOCUS but not in DEFAULT — the one act the two modes disagree
// about. Claude Code's own default density prints agent activity as its own lines
// ("6 background agents launched", "Agent \"…\" finished · 21m 16s"), because on a
// lead session that IS the work: who you dispatched and who came back is the shape
// of the turn, not a detail of it. So default leaves the mirror's launch/resume
// headers and each agent's ⇢ prompt / ⇠ result card standing, while focus — one
// line for the whole turn — folds them in with everything else.
//
// Nothing is ever dropped from the COUNTERS by a mode (there is no second axis;
// see docs/dashboard.md *View modes* for the one that was tried and rejected):
// what a mode collapses, its summary still accounts for.
// A MONITOR folds in default too, asked for in those words ("also monitors should be
// in the under summary in default mode"): a monitor is a watcher you set up once and
// then read only if it fires, so its card standing open in the feed is noise — the
// summary's "watched 2 monitors" is the whole of what default needs to say. A
// BACKGROUND job deliberately stays visible there: it is work still running, whose
// output you came to read.
const VIEW_FOLD = {
  verbose: [],
  default: ["shell", "file_read", "monitor", "task", "message_delivery",
            "actor_message"],
  // `tool` folds in FOCUS only, exactly like `skill`: a generic tool call is a
  // quiet one-liner the lead now emits too (plugins/claude_code/tool_fmt.py),
  // so default SHOWS it — that line is the whole point — and focus collapses a
  // run of them into "used 3 tools".
  focus: ["shell", "file_read", "background", "monitor", "file_edit",
          "file_write", "actor_assignment", "task", "message_delivery",
          "actor_message", "skill", "search", "network", "workspace", "media"],
};

// THE SUMMARY VOCABULARY — Claude Code's own, extracted from the 2.1.220 binary
// (docs/dashboard.md, *View modes* records the full table and how it was read):
// [counter, active verb, done verb, singular unit, plural unit], in Claude
// Code's own emission ORDER. Each fragment is "<verb> <n> <unit>"; the FIRST
// fragment is capitalized and the rest are not; they join with ", "; and while
// the run is still running the participle form is used and the line ends in "…".
// The keys are actclass.ACTS tokens (plus the two memory flavours), so a new act
// with no row here would be counted into nothing (grep-tested both ways).
const VIEW_FRAGMENTS = [
  ["file_change", "editing", "edited", "file", "files"],
  ["file_read", "reading", "read", "file", "files"],
  ["actor_assignment", "running", "ran", "agent", "agents"],
  ["skill", "using", "used", "skill", "skills"],
  ["tool", "using", "used", "tool", "tools"],
  ["shell", "running", "ran", "shell command", "shell commands"],
  ["background", "running", "ran", "background job", "background jobs"],
  ["monitor", "watching", "watched", "monitor", "monitors"],
  ["task", "tracking", "tracked", "task", "tasks"],
  ["message_delivery", "passing", "passed", "message", "messages"],
];

const VIEW_COUNTER = {
  file_edit: "file_change",
  file_write: "file_change",
  search: "tool",
  network: "tool",
  workspace: "tool",
  media: "tool",
  actor_message: "message_delivery",
};

const VIEW_SUBJECT = {
  actor_assignment: "actorAssignmentId",
  message_delivery: "messageId",
};

// Don't show a run's elapsed until it has actually been running a moment —
// Claude Code's own threshold for the same chip, and it keeps a fast run from
// flashing "· 0s".
const VIEW_ELAPSED_MIN_S = 2;
// Collapsing can leave the screen nearly empty (focus over a command-heavy tail
// hides almost everything), so a mode switch tops the feed up to VIEW_FILL_MIN
// visible items. VIEW_FILL_TRIES bounds how many TIMES that may fire per switch
// — each one runs loadOlder(), which has its own OLDER_TRIES request budget, so
// no fill can walk a long session's whole backlog.
// 15, not the 6 this shipped with: 6 left a switch showing a third of a screen,
// which reads as "the mode ate my session". Measured against a real long session
// (the engine driven over the live /history — focus mode, the worst case): a
// target of 6 spent 1 request and left 11 visible; 15 spends 2 and leaves ~25;
// 20 costs the same two requests for the same 25. In default one page already
// clears 15, so nothing changes there. The button's own promise is separate and
// much larger (HISTORY_FETCH, 40) — a switch tops the window up, it does not
// page.
const VIEW_FILL_TRIES = 3;
const VIEW_FILL_MIN = 15;

// Which counter one item feeds: its activity class, with memory-wiki ops (the ❖
// ops, `data-mem`) routed to the memory fragments — Claude Code words those as
// "recalled"/"wrote memories" rather than file reads and edits.
//
// A `bash` act can be a memory op too (most vault recall is a shell command —
// `cat`, `find -exec cat`, `qmd`), and it counts as a RECALL rather than as a
// shell command: what the reader wants to know is that the session consulted
// memory, not that it ran a process. The two flavours come from the served
// `data-mem` VALUE (core/ops.label) — "search" asked a question, "1" opened a
// note — because after collapsing there is nothing else left to tell them apart.
function viewCounter(elem) {
  const summaryKind = elem.dataset.summaryKind || "";
  return VIEW_COUNTER[summaryKind] || summaryKind;
}

// Whether a reply reads as a BOOKKEEPING REPORT rather than an answer: it
// renders as a single markdown block. `Persisted the zone-placement map and the
// single-vs-dual locality contrast to preprod-envoy-lb.` is one paragraph and
// nothing else; an answer has shape — paragraphs, a list, a table, a code block
// (measured over the two reported wiki sessions: every bookkeeping-only reply
// was 1 block, every real answer 3-67). It is the ONE thing that separates the
// two errand shapes, which are otherwise identical on every structural axis
// there is (docs/dashboard.md, *Errand boundaries*), and it is asked of the
// RENDERED message because that is where "how much is here" lives.
// The one-line summary of a run, as nodes: "Read 3 files, ran 2 shell commands"
// (done) / "Reading 3 files, running 2 shell commands…" (still going). `counts`
// is a counter->n map carrying optional `add`/`rem` line totals for the edit
// fragment, whose diffstat Claude Code prints right after "N files".
function viewSummaryNodes(counts, running) {
  const out = [];
  for (const [key, active, done, one, many] of VIEW_FRAGMENTS) {
    const n = counts[key] | 0;
    if (!n) continue;
    let verb = running ? active : done;
    if (!out.length) verb = verb[0].toUpperCase() + verb.slice(1);
    else out.push(tnode(", "));
    out.push(tnode(verb + " "), el("b", "", String(n)),
             tnode(" " + (n === 1 ? one : many)));
    if (key === "file_change" && (counts.add || counts.rem)) {
      out.push(tnode(" "));
      if (counts.add) out.push(el("span", "dadd", "+" + counts.add));
      if (counts.add && counts.rem) out.push(tnode(" "));
      if (counts.rem) out.push(el("span", "drem", "-" + counts.rem));
    }
  }
  if (running) out.push(tnode("…"));
  return out;
}

// A run's collapsed stand-in. `members` are the folded items it speaks for (its
// key is the OLDEST member's — runs grow at the newest end, so that key is
// stable as the run absorbs new items and the user's expansion survives).
function buildRunSummary(key, members, running, anchor, bad, open) {
  const row = el("div", "vsum");
  row.dataset.run = key;
  row.dataset.open = open ? "1" : "0";
  row.append(el("span", "vdot" + (running ? "" : bad ? " bad" : " done")));
  const text = el("span", "vtext");
  const counts = { add: 0, rem: 0 };
  const seen = {};                             // subject counter -> the ids counted
  for (const m of members) {
    const c = viewCounter(m);
    const idk = VIEW_SUBJECT[c];
    if (idk) {
      // a row without an id is its own subject, so an unattributable row still
      // counts once (and can never merge with another one)
      (seen[c] || (seen[c] = new Set())).add(
        m.dataset[idk] || ("view-" + m.dataset.viewKey));
    } else if (c) {
      // WEIGHT the row: one item usually stands for one thing, but a Bash read of
      // several files at once is ONE block (the command produced one undivided
      // output, so it can't be split into rows) — `data-nf` says how many files it
      // actually read (served per item, actclass.readmore). Counting rows made a
      // `cat app.py utils.py` read "Read 1 file", the same under-report the
      // one-liner itself used to make by naming only the first file.
      counts[c] = (counts[c] | 0) + 1;
    }
    counts.add += +(m.dataset.add || 0);       // served per item (actclass.diffstat)
    counts.rem += +(m.dataset.rem || 0);
  }
  for (const c in seen) counts[c] = seen[c].size;
  text.append(...viewSummaryNodes(counts, running));
  row.append(text);
  const timer = el("span", "vtimer");
  row.append(timer);
  // the summary stays PUT when expanded (▾) — it is the only way back to
  // collapsed, and it keeps naming what the revealed blocks below it are
  row.append(el("span", "vcaret", open ? "▾" : "▸"));
  if (running && anchor) {
    row.dataset.anchor = String(anchor);
    paintRunTimer(row);
  }
  row.onclick = () => {
    const open = S.sessionView.viewOpen;
    if (open.has(key)) open.delete(key);
    else open.add(key);
    applyViewMode();
  };
  return row;
}

// The ticking " · 12s" on a still-running run — the same live-elapsed idea as the
// foreground command's ⏱ chip (the server says since WHEN, the browser counts),
// reusing that chip's anchor when the run contains the running command.
function paintRunTimer(row) {
  const anchor = +row.dataset.anchor || 0;
  const timer = row.querySelector(".vtimer");
  if (!timer) return;
  const secs = Date.now() / 1000 - anchor;
  timer.textContent = (anchor && secs >= VIEW_ELAPSED_MIN_S)
    ? " · " + dur(secs) : "";
}

function paintActivityTimer(timer) {
  const anchor = +timer.dataset.anchor || 0;
  timer.textContent = anchor ? tp("· " + dur(Date.now() / 1000 - anchor)) : "";
}

function ensureElapsedTimer() {
  const session = S.sessionView;
  if (!session || session.viewTimer) return;
  if (session.stream.querySelector(".vsum[data-anchor], .blive[data-anchor]"))
    session.viewTimer = setInterval(tickRunTimers, RUN_TIMER_INTERVAL_MS);
  tickRunTimers();
}

function tickRunTimers() {
  const sessionView = S.sessionView;
  if (!sessionView) return;
  const rows = [...sessionView.stream.querySelectorAll(".vsum[data-anchor]")];
  const activityTimers = [...sessionView.stream.querySelectorAll(".blive[data-anchor]")];
  if (!rows.length && !activityTimers.length) {
    clearInterval(sessionView.viewTimer);
    sessionView.viewTimer = null;
    return;
  }
  rows.forEach(paintRunTimer);
  activityTimers.forEach(paintActivityTimer);
}

// The whole pass: decide each item's disposition, cut maximal runs of foldable
// ones, and put a summary line where each run sits. Derived entirely from the
// DOM + the current mode, so it is safe to re-run after any append — which is
// how a live stream keeps its collapse correct as blocks arrive.
// Undo everything a previous pass stamped on the items (hidden flag + the
// expanded-run rail). Both exits of applyViewMode go through it: a mark left
// behind would draw a rail under a run that is no longer open.
function clearViewMarks(items) {
  for (const it of items)
    it.classList.remove("vhide", "vdim", "vrun", "vrun-last");
}

function applyViewMode() {
  const sessionView = S.sessionView;
  if (!sessionView || !sessionView.stream) return;
  const mode = VIEW_MODES.includes(sessionView.view) ? sessionView.view : VIEW_DEFAULT;
  const items = streamItems();
  if (mode === "verbose") {
    if (sessionView.viewSig === "verbose") return;      // already plain — nothing to undo
    for (const old of [...sessionView.stream.children])
      if (old.classList.contains("vsum")) old.remove();
    clearViewMarks(items);
    sessionView.viewSig = "verbose";
    updateShownCount();
    return;
  }

  const fold = VIEW_FOLD[mode] || [];
  // DOM order is newest -> oldest, so "the first reply seen since the last
  // prompt" IS that turn's final one — which is the only assistant prose focus
  // mode keeps. A prompt closes the turn: items below it are the older one's.
  const busy = typeof BUSY_TABS !== "undefined" && BUSY_TABS.includes(liveTab());
  let sawReply = false;
  // Still inside the NEWEST turn: the feed is newest-top and a turn reads
  // [replies … activity … prompt], so everything above the first prompt we meet
  // belongs to the turn in progress.
  let inNewestTurn = true;
  const disp = items.map(elem => {
    const itemGroup = elem.dataset.itemGroup;
    if (itemGroup === "messages") {
      const mk = elem.dataset.conversationKind || "";
      if (mk === "prompt") {
        sawReply = false;
        inNewestTurn = false;
        return "show";
      }
      if (mk === "system") return "hide";
      if (mode === "focus" && mk === "message") {
        // Exactly ONE message survives per turn — the newest, which is the one
        // the turn ends on. The rest are the running commentary and stay hidden.
        //
        // While the turn is STILL GOING that newest message is PROVISIONAL: more
        // prose (and the actual result) is still coming, so it is greyed rather
        // than presented as the answer. Once the turn settles it is the result,
        // and it goes to full weight beside the one-line summary of what the turn
        // did. Greying it only while in flight is the difference between "this is
        // the answer" and "this is where it's got to".
        const newest = !sawReply;
        sawReply = true;
        if (!newest) return "hide";
        return (busy && inNewestTurn) ? "dim" : "show";
      }
      // a recap / an ask / a plan or its verdict / mail — another reply, same
      // rule. All of them SHOW in every mode: each is a turn-level fact of the
      // conversation, not the mid-turn prose focus mode thins out.
      return "show";
    }
    return fold.includes(elem.dataset.summaryKind || "") ? "fold" : "show";
  });

  // PLAN first, mutate second: the runs are computed into a list, and the DOM is
  // only rebuilt when the plan actually differs from the painted one (the
  // signature below). Without that guard every SSE tick tore down and re-created
  // every summary line — which reflows the feed under a reader who has scrolled
  // back, and drops an in-progress text selection. Same reasoning, and the same
  // shape, as `statsSig` for the header.
  const plan = [];
  // Every item the mode DROPS — today only an INJECTED prompt (a hook's feedback,
  // a loaded skill, teammate mail's envelope): not conversation, and counted into
  // nothing. Tracked as one list rather than only the ones falling outside a run,
  // because a hidden item is hidden WHEREVER it lands: trailing a run, inside a
  // collapsed run's span, or inside an EXPANDED one. That last case is why this
  // exists: expanding a summary revealed its whole span, so one click on a summary
  // line brought back every row the mode had dropped — and `viewOpen` remembers
  // the expansion, so it stayed back.
  const hidden = items.filter((_e, k) => disp[k] === "hide");
  const isHide = new Set(hidden);
  const dims = items.filter((_e, k) => disp[k] === "dim");
  const isDim = new Set(dims);
  // "dim" is a PAINT, not a placement: it continues a run exactly as "hide" did,
  // so greying mid-turn prose leaves the collapse semantics untouched — the runs
  // either side of it still merge into one summary. Only "show" ends a run.
  const inRun = d => d === "fold" || d === "hide" || d === "dim";
  // …and a dimmed item is never hidden, wherever it falls: inside a collapsed
  // run's span, or trailing it as a stray.
  const hideIt = elem => { if (!isDim.has(elem)) elem.classList.add("vhide"); };
  let i = 0;
  while (i < items.length) {
    if (!inRun(disp[i])) { i++; continue; }
    let j = i, last = i;
    while (j < items.length && inRun(disp[j])) {
      if (disp[j] === "fold") last = j;
      j++;
    }
    const span = items.slice(i, last + 1);
    const members = span.filter((_, k) => disp[i + k] === "fold");
    i = j;
    if (!members.length) continue;
    const key = span[span.length - 1].dataset.viewKey || "";
    // A actor assignment has two canonical rows: started and finished. Both share one
    // subject id and can sit in this run together; the newest row is its current
    // state. Looking for ANY running row kept the superseded start alive forever.
    const currentSubjects = new Set();
    let explicitRunning = false;
    let hasUnstatedMember = false;
    for (const member of members) {
      const counter = viewCounter(member);
      const subjectField = VIEW_SUBJECT[counter];
      const subject = subjectField && member.dataset[subjectField];
      if (subject && currentSubjects.has(counter + ":" + subject)) continue;
      if (subject) currentSubjects.add(counter + ":" + subject);
      if (member.dataset.state === "running") explicitRunning = true;
      else if (!member.dataset.state) hasUnstatedMember = true;
    }
    const running = explicitRunning
      || (span[0] === items[0] && busy && hasUnstatedMember);
    const startedAt = members
      .map(member => +(member.dataset.startedAt || 0))
      .filter(value => value > 0);
    plan.push({
      key, span, members, running,
      open: sessionView.viewOpen.has(key),
      bad: members.some(m => m.dataset.bad === "1"),
      anchor: running
        ? (startedAt.length ? Math.min(...startedAt)
           : +(span[span.length - 1].dataset.activityTime || 0))
        : 0,
    });
  }
  // Everything the painted lines DEPEND on — deliberately not the elapsed
  // seconds, which the 1s timer owns (a signature carrying the clock would
  // rebuild the DOM every second, the very thing this avoids).
  const sig = mode + "!" + hidden.map(s => s.dataset.viewKey).join(",")
    + "!" + dims.map(s => s.dataset.viewKey).join(",") + "!"
    + plan.map(p => [p.key, p.open ? 1 : 0, p.running ? 1 : 0, p.bad ? 1 : 0,
                     p.anchor, p.members.map(m => m.dataset.viewKey).join(".")].join(":"))
        .join(";");
  if (sig === sessionView.viewSig) return;
  sessionView.viewSig = sig;

  for (const old of [...sessionView.stream.children])
    if (old.classList.contains("vsum")) old.remove();
  clearViewMarks(items);
  for (const s of hidden) hideIt(s);
  for (const s of dims) s.classList.add("vdim");
  for (const p of plan) {
    if (p.open) {
      // An EXPANDED run keeps its summary as the group's HEADER and marks the
      // blocks it revealed, so it is visible at a glance which actions belong to
      // it: `.vrun` draws the shared left rail (and closes the vertical gaps, so
      // the rail is continuous), `.vrun-last` rounds it off at the oldest member.
      // Marking beats re-parenting into a wrapper: SSE inserts by position, the
      // block map holds live references and the eviction sweep walks top-level
      // children — moving items would break all three.
      // …over the span's VISIBLE members only: a hidden item stays hidden inside
      // an expanded run (it is not what the summary stood for), and the rail must
      // not end on a display:none node or the group looks unterminated.
      const shown = p.span.filter(m => !isHide.has(m));
      for (const m of shown) m.classList.add("vrun");
      if (shown.length) shown[shown.length - 1].classList.add("vrun-last");
      // The revealed blocks arrive FOLDED. Expanding a summary asks "which
      // actions were these?", not "dump every command's output" — a run of five
      // commands opening at full body is the wall the collapse existed to
      // remove, and each block is one more click away anyway. A block you opened
      // yourself is left alone (`userset`), so this can't fight you on the next
      // pass. Only in the collapsing modes: verbose has no summary lines.
      for (const m of p.members)
        if (m.classList.contains("blk") && !m.dataset.userset)
          m.dataset.open = "0";
    } else {
      for (const m of p.span) hideIt(m);
    }
    sessionView.stream.insertBefore(
      buildRunSummary(p.key, p.members, p.running, p.anchor, p.bad, p.open),
      p.span[0]);
    if (p.running) ensureElapsedTimer();
  }
  updateShownCount();
  viewAutoFill();
}

// Collapsing can leave the window almost empty (80 blocks of commands become two
// summary lines). Pull the next history page or two so there is something to
// read, bounded by VIEW_FILL_TRIES per mode switch.
function viewAutoFill() {
  const sessionView = S.sessionView;
  if (!sessionView || sessionView.view === "verbose" || sessionView.loadingOlder) return;
  if ((sessionView.viewFill | 0) >= VIEW_FILL_TRIES || (sessionView.oldest | 0) <= 0) return;
  if (visibleCount() >= VIEW_FILL_MIN) return;
  sessionView.viewFill = (sessionView.viewFill | 0) + 1;
  loadOlder(VIEW_FILL_MIN);      // the same loader, aimed at a smaller target —
  //                                two independent pagers would fight over
  //                                `loadingOlder` and double-fetch the boundary
}

function setViewMode(mode) {
  const sessionView = S.sessionView;
  if (!sessionView || !VIEW_MODES.includes(mode)) return;
  sessionView.view = mode;
  sessionView.viewOpen.clear();          // expansions belong to the mode that made them
  sessionView.viewFill = 0;
  for (const block of sessionView.stream.querySelectorAll(".blk")) {
    block.dataset.open = mode === "verbose" ? "1" : "0";
    delete block.dataset.userset;
  }
  if (sessionView.meta) sessionView.meta.view_mode = mode;
  applyViewMode();
  // Durable + per-session (dashboard/prefs.py): re-opening this session — on
  // this device or another — comes back at the mode you left it in.
  postJSON("/api/sessions/" + encodeURIComponent(S.currentSessionId)
           + "/application/view-mode",
           { view_mode: mode }, { audit: "viewmode" });
}

// The stream's control row: the view-mode segment on the left, the visible-item
// count on the right. It used to carry a second control — a kind-filter chip row
// (all · commands · files · memory · agents · messages) — removed on request; the
// view modes are the density cut that gets used, and two axes over one stream
// only ever had to explain which one had hidden a block.
function buildViewBar() {
  const sessionView = S.sessionView;
  const bar = el("div", "fbar");

  const modes = el("div", "vmodes");
  const mbtns = new Map();
  sessionView.modeBtns = mbtns;              // the `view-mode` SSE repaints these
  for (const key of VIEW_MODES) {
    const c = el("button", "vmode" + (sessionView.view === key ? " on" : ""), key);
    c.onclick = () => {
      setViewMode(key);
      mbtns.forEach((cc, k) => cc.classList.toggle("on", k === sessionView.view));
    };
    mbtns.set(key, c);
    modes.append(c);
  }

  const count = el("span", "fcount");
  sessionView.countEl = count;
  bar.append(modes, count);
  return bar;
}

/* ---------- the "/" command menu (composer + new-session prompt) ---------- */
// Claude-Code-style completion: a leading "/" with no whitespace yet opens a
// menu over the selected harness catalog (built-ins + that directory's native
// commands/skills), matched by SUBSTRING (`cmdMatches`, prefix hits ranked
// first) — the memorable middle of a name is enough, no prefix needed.
// ↑/↓ move, Tab completes, Esc closes; Enter completes —
// except with {enterSends: true} an EXACT token falls through to the caller's
// send (so a fully-typed "/compact" sends on one Enter; both boxes pass
// !IS_IPAD, since on an iPad Enter never sends). The TUI stays
// authoritative — sending just types the command into the terminal and Claude
// Code's own palette executes it. The menu drops BELOW its host box (never up
// over the stats row); `host` must be position:relative.
// Wiring contract: the helper listens to input/blur itself; the caller keeps
// its own oninput (autoGrow) and calls sm.key(e) FIRST in onkeydown — a true
// return means the menu consumed the key.

// The menu is host-SCOPED, by whichever of the two handles the caller has:
// `sessionId` (the composer) — the server resolves that session's OWNING tool and
// returns ITS vocabulary (a codex session gets /plan etc., not Claude's) — or
// `tool` (the new-session form, which has no session yet, only a picker), so
// the menu follows the host you are ABOUT to launch. Passing neither means the
// default host, which is what the form used to do unconditionally: it offered
// Claude Code's commands for a codex launch. `key` must vary with whichever one
// is passed, since the cache is per-menu.
function cmdsFor(workingDirectory, cache, key, sessionId, tool) {
  const harness = tool || (sessionId && S.sessionView && S.sessionView.meta && S.sessionView.meta.harness);
  if (!harness) return Promise.resolve([]);
  if (!cache[key])
    cache[key] = fetch("/api/harnesses/" + encodeURIComponent(harness)
                       + "/catalog?working_directory=" + encodeURIComponent(workingDirectory || "")
                       + (sessionId ? "&session_id=" + encodeURIComponent(sessionId) : ""))
      .then(r => r.ok ? r.json() : { commands: [] })
      .then(catalog => (catalog.commands || []).map(command => ({
        name: command.command,
        desc: command.description,
        min_prompts: command.minimum_prompt_count || 0,
      })))
      .catch(() => []);
  return cache[key];
}

// how many rows the menu shows at most (it scrolls past ~9)
const MENU_MAX = 30;

// The match rule: CONTAINS, case-insensitively, over the command NAME — typing
// the memorable middle of one is enough ("/commit" finds `gh:commit`, "/debug"
// finds `audit-debug`), which is what the namespaced/plugin names need since
// their prefix is the namespace you don't remember. Prefix hits still rank
// FIRST (typing the head of a name means that name), and each group keeps the
// server's own order — built-ins first, then nearest-first (`slashcmds.py`) —
// so no scoring heuristic re-litigates the shadowing rules the server settled.
// Descriptions are deliberately NOT searched: a word like "run" appears in
// dozens of them, and a menu you complete against must stay predictable.
function cmdMatches(cmds, tok) {
  const q = tok.toLowerCase();
  const pre = [], mid = [];
  for (const c of cmds) {
    const at = c.name.toLowerCase().indexOf(q);
    if (at === 0) pre.push(c);            // (an empty token: every row, in order)
    else if (at > 0) mid.push(c);
  }
  return pre.concat(mid);
}

// The picked command reads TINTED inside the box (Claude Code's TUI paints the
// selected command/skill the same way). A <textarea> cannot style a range, so a
// mirror div is positioned OVER the box (pointer-events:none, its own text
// transparent) and only the leading "/name" token carries a translucent --exec
// background — a tint the textarea's own glyphs show through, like a selection.
// The mirror's metrics are COPIED from the live box (getComputedStyle), never
// re-declared in CSS: the textarea stays the single owner of its font/padding
// (the iPad ≥16px override among them), so the two can't drift out of alignment.
const HL_METRICS = ["fontFamily", "fontSize", "fontWeight", "fontStyle",
                    "lineHeight", "letterSpacing", "wordSpacing", "tabSize",
                    "textIndent", "paddingTop", "paddingRight", "paddingBottom",
                    "paddingLeft", "borderTopWidth", "borderRightWidth",
                    "borderBottomWidth", "borderLeftWidth", "borderRadius"];

function cmdHighlight(ta, host, isCmd) {
  const hl = el("div", "cmhl");
  hl.hidden = true;
  host.append(hl);
  let queued = false;
  // the mirror tracks the box's live geometry: it grows with autoGrow, moves as
  // the attachment strip wraps, and reflows on a resize/rotation
  const place = () => {
    const r = ta.getBoundingClientRect(), h = host.getBoundingClientRect();
    hl.style.left = (r.left - h.left) + "px";
    hl.style.top = (r.top - h.top) + "px";
    hl.style.width = r.width + "px";
    hl.style.height = r.height + "px";
    const cs = getComputedStyle(ta);
    for (const k of HL_METRICS) hl.style[k] = cs[k];
  };
  // Painted a frame LATE on purpose: the caller's own oninput (autoGrow) resizes
  // the box on the same event, and the mirror must match the SETTLED geometry.
  const paint = () => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => {
      queued = false;
      if (!ta.isConnected) return;
      const v = ta.value;
      // the leading token only, and only while it names a real command — editing
      // it into something else (or sending) drops the tint by itself
      const m = /^\/(\S+)(?=\s|$)/.exec(v);
      const name = m && isCmd(m[1]) ? m[1] : null;
      if (!name || ta.disabled) { hl.hidden = true; hl.textContent = ""; return; }
      place();
      hl.textContent = "";
      hl.append(el("span", "cmhlt", "/" + name),
                document.createTextNode(v.slice(name.length + 1)));
      hl.scrollTop = ta.scrollTop;      // the rest of the text is only there to
      hl.hidden = false;                // wrap/scroll the token like the box does
    });
  };
  ta.addEventListener("input", paint);
  ta.addEventListener("scroll", paint);
  // autoGrow is the one hook every PROGRAMMATIC value change already goes
  // through (draft restore, an SSE draft from another device, a cleared send),
  // none of which fire `input` — see the autoGrow call in app.08-composer.js
  ta.cmdPaint = paint;
  const onResize = () => {
    if (!ta.isConnected) { removeEventListener("resize", onResize); return; }
    paint();
  };
  addEventListener("resize", onResize);
  // the box can MOVE with no value change and no window resize — the attachment
  // strip appearing above it wraps the composer's flex row (paint places the
  // mirror, but nothing else would call it). Safe from feedback: the mirror is
  // absolutely positioned, so painting it never resizes what we observe.
  if (window.ResizeObserver) {
    const ro = new ResizeObserver(() => {
      if (!ta.isConnected) { ro.disconnect(); return; }
      paint();
    });
    ro.observe(ta);
    ro.observe(host);
  }
  return paint;
}

function slashMenu(ta, host, getCmds, opts) {
  const enterSends = !!(opts && opts.enterSends);
  const menu = el("div", "cmenu");
  menu.hidden = true;
  host.append(menu);
  let items = [], sel = 0;
  // every command name this box has seen, so the tint can recognise one without
  // a fetch of its own (a pick adds its name; a menu refresh adds the batch)
  const known = new Set();
  const hlPaint = cmdHighlight(ta, host, (name) => known.has(name));

  // the "/" token being completed, or null when the menu shouldn't show
  // (no leading slash, or whitespace = arguments underway)
  const token = () => {
    const v = ta.value;
    if (!v.startsWith("/")) return null;
    const head = v.slice(1);
    return /\s/.test(head) ? null : head;
  };
  const close = () => { menu.hidden = true; items = []; };
  const complete = (c) => {
    known.add(c.name);                      // …so it paints tinted right away
    ta.value = "/" + c.name + " ";
    close();
    ta.focus();
    ta.dispatchEvent(new Event("input"));   // caller's autoGrow, if any
  };
  const render = () => {
    menu.textContent = "";
    items.forEach((c, i) => {
      const row = el("div", "cmi" + (i === sel ? " sel" : ""));
      row.append(el("span", "cmname", "/" + c.name));
      if (c.desc) row.append(el("span", "cmdesc", c.desc));
      if (c.src && c.src !== "built-in") row.append(el("span", "cmsrc", c.src));
      row.onmousedown = (e) => { e.preventDefault(); complete(c); };
      menu.append(row);
    });
    menu.hidden = !items.length;
    if (items.length && menu.children[sel])
      menu.children[sel].scrollIntoView({ block: "nearest" });
  };
  const learn = (cmds) => {
    cmds.forEach(c => known.add(c.name));
    hlPaint();                              // a name we just learned may be typed
    return cmds;
  };
  const refresh = () => {
    const tok = token();
    if (tok === null) { close(); return; }
    getCmds().then(learn).then(cmds => {
      if (!ta.isConnected || token() !== tok) return;   // view/input moved on
      sel = 0;
      items = cmdMatches(cmds, tok).slice(0, MENU_MAX);
      render();
    });
  };
  const key = (e) => {
    if (menu.hidden || !items.length) return false;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      sel = (sel + (e.key === "ArrowDown" ? 1 : items.length - 1)) % items.length;
      render();
      return true;
    }
    if (e.key === "Tab") { e.preventDefault(); complete(items[sel]); return true; }
    if (e.key === "Escape") { e.stopPropagation(); close(); return true; }
    if (e.key === "Enter" && !e.shiftKey) {
      if (enterSends && token() === items[sel].name) {
        close();                            // exact token: fall through to send
        return false;
      }
      e.preventDefault();
      complete(items[sel]);
      return true;
    }
    return false;
  };
  ta.addEventListener("input", refresh);
  ta.addEventListener("blur", () => setTimeout(close, MENU_BLUR_MS));
  // a RESTORED draft can already hold a picked command with nothing typed since
  // (so no menu fetch would ever happen) — learn the names once, for the tint
  if (ta.value.startsWith("/")) getCmds().then(learn);
  return { key };
}

/* ---------- queued messages (Claude Code's mid-turn queue) ---------- */
// Claude Code natively QUEUES a message typed while a turn is running and
// delivers it when the turn ends — the composer rides exactly that (send_text
// types into the TUI either way). The /message response says which happened
// (`queued: true` when the send landed mid-turn — the server's verdict is the
// authority: a QUEUE_TABS tab colour VERIFIED against a live screen, since a
// terminal-side cancel can freeze the colour mid-turn and a colour-only verdict
// pinned chips no delivery would ever drain; the client QUEUE_TABS below only
// styles the send button). A queued message would otherwise VANISH from the page until
// delivery (it reaches the transcript only when the turn ends), so it shows as
// an amber "⧗ queued" prompt bubble PINNED at the top of the transcript — above
// the newest-first stream, so incoming activity never buries it — until its
// prompt record actually arrives in the stream (drainQueue — matched by text;
// tab transitions are useless as a delivery signal since green flips busy again
// the instant a queued prompt starts processing). At that point drainQueue drops
// the pinned bubble and the delivered prompt appears in the stream itself. ✕
// only removes the marker — the message is already in the TUI's queue and the
// web can't unqueue it.

const QUEUE_TABS = ["thinking", "working", "executing"];

function buildQueuePin() {
  const q = el("div", "pinq");
  q.hidden = true;
  S.sessionView.queueEl = q;
  // restore the pinned queued messages persisted server-side (composer-queue kv)
  // so a reload / device switch keeps showing what the TUI still holds unqueued —
  // seed only when the in-memory queue is empty (a live session already has its
  // entries); drainQueue reconciles them out as their prompts arrive.
  const cq = S.sessionView.meta && S.sessionView.meta.composer_queue;
  if (cq && Array.isArray(cq.items) && !S.sessionView.queue.length)
    S.sessionView.queue = cq.items.map(it => ({ text: (it && it.text) || "" }));
  renderQueue();
  return q;
}

// Persist the WHOLE current chip list to the server (composer-queue kv) so it
// survives a reload; called on every queue mutation (queued-send, delivery
// drain, ✕-hide). Best-effort — a failed write just retries on the next
// change. meta is kept in sync so our own SSE echo is a no-op.
function saveQueue(sessionView) {
  sessionView = sessionView || S.sessionView;
  if (!sessionView || !S.currentSessionId) return;
  const items = sessionView.queue.map(m => ({ text: m.text }));
  if (sessionView.meta)
    sessionView.meta.composer_queue = items.length ? { items, origin: CLIENT_ID } : null;
  postJSON("/api/sessions/" + encodeURIComponent(S.currentSessionId)
           + "/application/composer-queue",
           { items, origin: CLIENT_ID }).catch(() => {});
}

// A peer device's (or our own reload's) queue update arrived over SSE — adopt
// it, ignoring our OWN echo (same origin) so a local drain isn't clobbered.
function applyComposerQueue(q) {
  const sessionView = S.sessionView;
  if (!sessionView) return;
  if (sessionView.meta) sessionView.meta.composer_queue = q || null;
  if (q && q.origin && q.origin === CLIENT_ID) return;   // our own write
  sessionView.queue = ((q && q.items) || []).map(it => ({ text: (it && it.text) || "" }));
  renderQueue();
}

// Paint the queued messages as amber "⧗ queued" prompt bubbles, pinned at the
// top of the transcript until each is delivered (drainQueue removes it, and the
// real prompt bubble then arrives in the stream). Mirrors opshtml.msg_html's
// .msg.prompt shape (minus the rewind ↶ — a not-yet-delivered prompt isn't
// re-runnable), plus a ⧗ badge and a ✕ to drop a stale marker.
function renderQueue() {
  const sessionView = S.sessionView;
  if (!sessionView || !sessionView.queueEl) return;
  const q = sessionView.queueEl;
  q.textContent = "";
  q.hidden = !sessionView.queue.length;
  sessionView.queue.forEach((m, i) => {
    const d = el("div", "msg prompt queued");
    d.title = "queued in the terminal — delivers when this turn ends";
    const who = el("span", "who");
    who.append(tnode("you"), el("span", "qbadge", "⧗ queued"));
    d.append(who);
    const x = el("button", "qx", "✕");
    x.title = "remove this queued marker (the message stays queued in the terminal)";
    x.onclick = () => { sessionView.queue.splice(i, 1); renderQueue(); saveQueue(sessionView); };
    d.append(x);
    d.append(promptMd(m.text));
    q.append(d);
  });
}

function drainQueue(items) {
  const sessionView = S.sessionView;
  if (!sessionView || !sessionView.queue || !sessionView.queue.length) return;
  let hit = false;
  for (const it of items) {
    if (it.item_type !== "message" || it.conversation_kind !== "prompt") continue;
    const real = (it.plain_text || "").trim();
    // suffix match (promptMatches — the one rule, shared with drainPending and
    // mirrored server-side): the delivered prompt may carry attachment mentions
    // OR a terminal-restored draft in front of what we sent.
    const i = sessionView.queue.findIndex(m => promptMatches(real, m.text));
    if (i >= 0) { sessionView.queue.splice(i, 1); hit = true; }
  }
  if (hit) { renderQueue(); saveQueue(sessionView); }
}

// A prompt the terminal DISCARDED (Esc-Esc right after sending, or a rewind)
// is never deleted from the transcript — Claude Code just re-parents around it,
// so the next prompt arrives carrying the SAME data-par and the dead one is
// simply orphaned. The server prunes that on any full read, but a live feed has
// already painted the bubble, so drop it here the moment its replacement shows
// up (docs/dashboard.md, *Discarded prompts*). Newest-top feed ⇒ the survivor
// is the first match in DOM order; only server-rendered bubbles carry data-par,
// so the optimistic .pending / ⧗ .queued stand-ins are untouched.
function dropSuperseded(items) {
  const st = S.sessionView && S.sessionView.stream;
  if (!st) return;
  for (const it of items) {
    if (it.item_type !== "message" || it.conversation_kind !== "prompt"
        || !it.reply_to_message_id) continue;
    let live = false;
    for (const el of st.querySelectorAll(".msg.prompt[data-par]")) {
      if (el.dataset.par !== it.reply_to_message_id) continue;
      if (!live) { live = true; continue; }        // keep the newest
      el.remove();
    }
  }
}
