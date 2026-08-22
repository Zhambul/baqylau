"use strict";
// Part of the dashboard SPA — one feed entry to the markup that draws it.
//
// The decision table the daemon used to hold. Every function here is PURE: an
// entry (plus the actor names it joins against) in, a descriptor out — markup,
// the dataset the density pass reads off the DOM, and where the block belongs.
// The DOM itself is app.05-session.js's business, and the fold that turns a
// command's start, its output chunks and its finish into ONE block is too.
//
// Pure on purpose: this is the whole rendering vocabulary of the product, and
// keeping it out of the DOM is what lets it be tested at all — a browser is not
// available to a test runner, and a wrong header on a failed command is exactly
// the kind of thing nobody notices by eye.
//
// The four markers a reader never sees — the two turn markers and the two
// aggregate-shaped entries — return null. They are grouping facts, not lines.

// Which of the four rails an entry belongs to. The density modes hide by rail,
// so this is what "focus" and "verbose" actually act on.
const ENTRY_GROUPS = {
  message: "messages",
  reasoning: "messages",
  question_asked: "messages",
  question_answered: "messages",
  plan_proposed: "messages",
  plan_resolved: "messages",
  file: "files",
  assignment_started: "agents",
  assignment_finished: "agents",
};

// The one-word kind the run summaries count by ("Ran 3 shell commands, read 2
// files"). A summary that said "3 entries" would be true and useless.
const ENTRY_SUMMARY_KINDS = {
  shell_started: "shell",
  shell_output: "shell",
  shell_finished: "shell",
  shell_backgrounded: "shell",
  search: "search",
  web: "network",
  worktree: "workspace",
  skill_started: "skill",
  skill_finished: "skill",
  message: "message",
  reasoning: "message",
  question_asked: "attention",
  question_answered: "attention",
  plan_proposed: "attention",
  plan_resolved: "attention",
  assignment_started: "agent",
  assignment_finished: "agent",
  compaction_started: "compaction",
  compaction_finished: "compaction",
  model_change: "state",
  effort_change: "state",
};

const FILE_VERBS = {
  read: ["Read", "rgb(97,175,239)"],
  created: ["Write", "rgb(152,195,121)"],
  updated: ["Edit", "rgb(229,192,123)"],
  deleted: ["Delete", "rgb(224,108,117)"],
  renamed: ["Move", "rgb(229,192,123)"],
};

const FILE_SUMMARY_KINDS = {
  read: "file_read",
  created: "file_write",
  updated: "file_edit",
  deleted: "file_edit",
  renamed: "file_edit",
};

// The actor's own name, for a bubble's byline. Falls back to the id: an actor
// whose `actor.started` has not been folded yet is still somebody who spoke.
function entryActorName(entry, actors) {
  const actor = (actors || {})[entry.actor_id];
  return (actor && actor.name) || entry.actor_id || "";
}

function entryText(content) {
  return (content && content.text) || "";
}

// Markdown when the harness said markdown, plain text otherwise. The media type
// is why the entry carries one: guessing by role renders a plain-text tool
// result as markup.
function entryBody(content) {
  if (!content) return "";
  return content.media_type === "text/markdown"
    ? '<div class="md">' + mdHtml(content.text) + "</div>"
    : '<div class="md"><p>' + escapeHtml(content.text) + "</p></div>";
}

/* ---------- the conversation --------------------------------------------------
   Bubbles: who said it, and what they said. The one place `data-txt` is stamped
   is a user prompt, because the rewind gesture replays that exact text. */

function messageMarkup(entry, actors) {
  const body = entry.body;
  const name = entryActorName(entry, actors);
  const text = entryText(body.content);
  let cssClass = "message";
  let label = escapeHtml(name);
  let attributes = "";
  if (body.phase === "recap") {
    cssClass = "recap";
    label = "↩ recap";
  } else if (body.recipient_actor_id) {
    cssClass = "message peer";
    label = escapeHtml(name + " → " + body.recipient_actor_id);
  } else if (body.role === "user" && body.phase === "synthetic") {
    cssClass = "prompt sys";
    label = "⚙ system";
  } else if (body.role === "user") {
    cssClass = "prompt";
    label = 'you<button class="rw" title="rewind to here">↶</button>';
    attributes = ' data-txt="' + escapeHtml(text).replace(/"/g, "&quot;") + '"';
    // The parent this prompt replaced. Two prompts naming the same parent means
    // the older was DISCARDED, and `dropSuperseded` needs to see both to know.
    if (body.reply_to)
      attributes += ' data-par="' + escapeHtml(body.reply_to).replace(/"/g, "&quot;") + '"';
  } else if (body.role === "parent") {
    label = "parent agent";
  } else if (body.role !== "assistant") {
    cssClass = "prompt sys";
    label = "⚙ system";
  }
  return (
    '<div class="msg ' + cssClass + '"' + attributes + '><span class="who">' + label
    + "</span>" + entryBody(body.content) + "</div>"
  );
}

function conversationKind(entry) {
  const body = entry.body;
  if (entry.type === "reasoning") return "message";
  if (entry.type === "question_asked") return "question";
  if (entry.type === "plan_proposed") return "plan";
  if (entry.type === "question_answered") return "answer";
  if (entry.type === "plan_resolved") return "plan_decision";
  if (body.recipient_actor_id) return "actor_message";
  if (body.phase === "recap") return "recap";
  if (body.role === "user") return "prompt";
  return body.role === "assistant" || body.role === "parent" ? "message" : "system";
}

/* ---------- attention --------------------------------------------------------
   The feed's record of a question or a plan — what was asked and what came back.
   The live CARD that takes the answer is app.07-dialogs.js's; these are the
   lines that stay behind afterwards. */

function questionAskedMarkup(entry, actors) {
  const lines = [];
  for (const question of entry.body.questions || []) {
    const block = [];
    if (question.question) block.push(question.question);
    for (const choice of question.choices || []) {
      if (choice.label) block.push("- " + choice.label);
    }
    if (block.length) lines.push(block.join("\n"));
  }
  return (
    '<div class="msg question"><span class="who">'
    + escapeHtml(entryActorName(entry, actors) + " ▸ asks you")
    + '</span><div class="md">' + mdHtml(lines.join("\n\n")) + "</div></div>"
  );
}

function questionAnsweredMarkup(entry) {
  const rows = [];
  for (const answer of entry.body.answers || []) {
    const labels = (answer.labels || [])
      .map(label => '<span class="ansv">' + escapeHtml(label) + "</span>")
      .join("") || '<span class="ansv none">—</span>';
    rows.push(
      '<div class="ansq"><div class="ansqh"><span class="ansqt">'
      + escapeHtml(answer.question_id) + "</span></div>"
      + '<div class="ansvs">' + labels + "</div></div>"
    );
  }
  const body = rows.length
    ? '<div class="ansqa">' + rows.join("") + "</div>"
    : '<div class="md"><p>' + escapeHtml(entry.body.feedback || "—") + "</p></div>";
  return '<div class="msg answer"><span class="who">you ▸ answered</span>' + body + "</div>";
}

function planProposedMarkup(entry, actors) {
  return (
    '<div class="msg plan"><span class="who">'
    + escapeHtml(entryActorName(entry, actors) + " ▸ proposes a plan")
    + "</span>" + entryBody(entry.body.plan) + "</div>"
  );
}

const PLAN_DECISIONS = {
  approved: ["approved", "you ▸ approved the plan"],
  changes_requested: ["changes", "you ▸ asked for changes"],
  rejected: ["rejected", "you ▸ rejected the plan"],
};

function planResolvedMarkup(entry) {
  const [css, label] = PLAN_DECISIONS[entry.body.state] || ["", "you ▸ decided"];
  const edited = entry.body.edited
    ? '<span class="pedit">edited before approval</span>'
    : "";
  const feedback = entry.body.feedback ? mdHtml(entry.body.feedback) : "";
  return (
    '<div class="msg plandecision ' + css + '"><span class="who">' + escapeHtml(label)
    + '</span><div class="md">' + edited + feedback + "</div></div>"
  );
}

/* ---------- files, searches, fetches -----------------------------------------
   One line each, coloured by what was done. These are the quiet majority of a
   feed and they read as a log, not as prose. */

function fileMarkup(entry) {
  const body = entry.body;
  const [verb, verbColor] = FILE_VERBS[body.action] || ["Touch", "rgb(171,178,191)"];
  const color = body.state === "failed" ? "rgb(224,108,117)" : verbColor;
  let markup = (
    '<span style="color:' + color + '">' + escapeHtml(verb) + "</span>"
    + '<span style="color:rgb(92,99,112)">(</span>'
    + '<span style="color:rgb(171,178,191)">' + escapeHtml(body.path) + "</span>"
    + '<span style="color:rgb(92,99,112)">)</span>'
  );
  const counts = [];
  if (body.lines_added) counts.push(["+" + body.lines_added, "rgb(152,195,121)"]);
  if (body.lines_removed) counts.push(["-" + body.lines_removed, "rgb(224,108,117)"]);
  if (counts.length) {
    markup += "  " + counts
      .map(([count, countColor]) => '<span style="color:' + countColor + '">' + count + "</span>")
      .join(" ");
  }
  const content = entryText(body.content);
  if (!content) return '<pre class="opl">' + markup + "</pre>";
  // The entry already carries a mutation's unified diff (or a Read's text).
  // Give that content the standard block frame so the same click binding as a
  // command can reveal it; the old loose <pre> discarded the body completely.
  return blockHtml({
    header: markup,
    summary: "",
    body: body.action === "read" || body.action === "created"
      ? sourceHtml(content)
      : unifiedDiffHtml(content),
    state: body.state,
    quiet: true,
  });
}

function toolBlockMarkup(kind, title, summary, body, state, entry) {
  return blockHtml({
    header: chipHtml(kind, title),
    summary: summary,
    body: body,
    state: state,
    startedAt: entry.occurred_at,
    finishedAt: entry.occurred_at,
    quiet: true,
  });
}

function searchMarkup(entry) {
  const body = entry.body;
  return toolBlockMarkup(
    "tool",
    body.tool,
    entryText(body.query),
    body.result ? '<pre class="opo">' + ansiHtml(entryText(body.result)) + "</pre>" : "",
    body.state,
    entry
  );
}

function webMarkup(entry) {
  const body = entry.body;
  return toolBlockMarkup(
    "tool",
    "WebFetch",
    body.url || "",
    body.result ? '<div class="md">' + mdHtml(entryText(body.result)) + "</div>" : "",
    body.state,
    entry
  );
}

function worktreeMarkup(entry) {
  const body = entry.body;
  return toolBlockMarkup(
    "tool",
    body.action === "entered" ? "EnterWorktree" : "ExitWorktree",
    entryText(body.arguments),
    "",
    body.state,
    entry
  );
}

/* ---------- notes ------------------------------------------------------------
   The one-line ⏺ form: something happened that is worth a line and no more. */

function noteBlock(entry, text, state, body) {
  return blockHtml({
    header: noteHtml(state, text),
    summary: "",
    body: body || "",
    state: state,
    startedAt: entry.occurred_at,
    finishedAt: entry.occurred_at,
    note: true,
  });
}

function skillStartedMarkup(entry) {
  return noteBlock(entry, "Skill(" + entry.body.name + ")", null, "");
}

function skillFinishedMarkup(entry) {
  const failed = entry.body.state !== "succeeded";
  return noteBlock(
    entry,
    "Skill finished" + (failed ? " (" + entry.body.state + ")" : ""),
    entry.body.state,
    entry.body.result ? '<div class="md">' + mdHtml(entryText(entry.body.result)) + "</div>" : ""
  );
}

function assignmentStartedMarkup(entry) {
  const name = entry.body.assigned_actor_name || "agent";
  const brief = entry.summary ? ': "' + entry.summary + '"' : "";
  return noteBlock(
    entry,
    "Agent " + name + brief,
    null,
    entry.body.prompt ? entryBody(entry.body.prompt) : ""
  );
}

function assignmentFinishedMarkup(entry) {
  return noteBlock(
    entry,
    "Agent finished" + (entry.body.state === "succeeded" ? "" : " (" + entry.body.state + ")"),
    entry.body.state,
    entry.body.result ? entryBody(entry.body.result) : ""
  );
}

function compactionMarkup(entry) {
  const body = entry.body;
  if (entry.type === "compaction_started") {
    return noteBlock(entry, "Compacting the context…", null, "");
  }
  const before = body.before_tokens;
  const after = body.after_tokens;
  const detail = before && after
    ? " · " + before.toLocaleString() + " → " + after.toLocaleString() + " tokens"
    : "";
  return noteBlock(entry, "Context compacted" + detail, "succeeded", "");
}

function stateChangeMarkup(entry) {
  const body = entry.body;
  const what = entry.type === "model_change" ? "Model" : "Effort";
  const arrow = body.previous ? body.previous + " → " + body.current : body.current;
  const why = body.automatic ? " (chosen for you)" : "";
  return noteBlock(entry, what + " " + arrow + why, null, "");
}

/* ---------- the table --------------------------------------------------------
   One entry in, one descriptor out — or null for the markers a reader never
   sees. `null` is not a gap: a turn marker is how the collapse pass finds a
   turn's edges, and drawing it would put an empty line between every two. */

function entryDescriptor(entry, actors) {
  const markup = entryMarkup(entry, actors);
  if (markup === null) return null;
  return {
    markup: markup,
    group: ENTRY_GROUPS[entry.type] || "commands",
    // A file's kind is its ACTION: the run summaries say "read 2 files, edited
    // 1", and one `file` kind for all five actions cannot.
    summaryKind: entry.type === "file"
      ? (FILE_SUMMARY_KINDS[entry.body.action] || "file_read")
      : (ENTRY_SUMMARY_KINDS[entry.type] || "tool"),
    conversationKind:
      ENTRY_GROUPS[entry.type] === "messages" ? conversationKind(entry) : "",
    state: entryState(entry),
    linesAdded: entry.type === "file" ? entry.body.lines_added || 0 : 0,
    linesRemoved: entry.type === "file" ? entry.body.lines_removed || 0 : 0,
    final: entry.type === "message" && entry.body.phase === "end_turn",
  };
}

// The states a block paints itself by. Only the entries that REPORT one have
// one: a note with no state draws as running, which is what a start is.
const STATEFUL_ENTRIES = [
  "file", "search", "web", "worktree", "shell_finished", "skill_finished",
  "assignment_finished",
];

function entryState(entry) {
  return STATEFUL_ENTRIES.includes(entry.type) ? (entry.body || {}).state || null : null;
}

function entryMarkup(entry, actors) {
  switch (entry.type) {
    case "turn_started":
    case "turn_finished":
      return null;
    case "message":
      return messageMarkup(entry, actors);
    case "reasoning":
      return (
        '<div class="msg message"><span class="who">'
        + escapeHtml(entryActorName(entry, actors))
        + "</span>" + entryBody(entry.body.content) + "</div>"
      );
    case "file":
      return fileMarkup(entry);
    case "search":
      return searchMarkup(entry);
    case "web":
      return webMarkup(entry);
    case "worktree":
      return worktreeMarkup(entry);
    case "skill_started":
      return skillStartedMarkup(entry);
    case "skill_finished":
      return skillFinishedMarkup(entry);
    case "question_asked":
      return questionAskedMarkup(entry, actors);
    case "question_answered":
      return questionAnsweredMarkup(entry);
    case "plan_proposed":
      return planProposedMarkup(entry, actors);
    case "plan_resolved":
      return planResolvedMarkup(entry);
    case "compaction_started":
    case "compaction_finished":
      return compactionMarkup(entry);
    case "assignment_started":
      return assignmentStartedMarkup(entry);
    case "assignment_finished":
      return assignmentFinishedMarkup(entry);
    case "model_change":
    case "effort_change":
      return stateChangeMarkup(entry);
    default:
      // shell_* is the fold's, not this table's: a command is one block built
      // from several entries, and `shellBlockMarkup` below owns it.
      return null;
  }
}

/* ---------- the command fold -------------------------------------------------
   A command arrives as a start, then any number of output chunks, then a
   finish — and it is ONE block. The state below is what a client keeps per
   `shell_id`, and this is the one place the read model expects the client to
   fold anything: re-sending a growing output on every line was the churn the
   redesign removed.

   `mode: "replace"` is why the chunks cannot simply be concatenated — a
   harness that reports its whole output at once sends one replacing chunk, and
   appending it to what the file watch already streamed would double it. */

function newShellFold(entry) {
  return {
    shellId: entry.body.shell_id,
    command: entryText(entry.body.command),
    execution: entry.body.execution,
    output: "",
    status: "",
    state: null,
    exitCode: null,
    backgrounded: false,
    startedAt: entry.occurred_at,
    finishedAt: null,
  };
}

// One entry folded into a command's state. Returns the state, mutated, so the
// caller can repaint exactly one block.
function foldShellEntry(fold, entry) {
  const body = entry.body;
  if (entry.type === "shell_output") {
    const text = entryText(body.content);
    const target = body.stream === "status" ? "status" : "output";
    fold[target] = body.mode === "replace" ? text : fold[target] + text;
    return fold;
  }
  if (entry.type === "shell_backgrounded") {
    fold.backgrounded = true;
    return fold;
  }
  if (entry.type === "shell_finished") {
    fold.state = body.state;
    fold.exitCode = body.exit_code;
    fold.finishedAt = entry.occurred_at;
    // A harness that streamed nothing reports the whole output here, and it is
    // folded exactly as a replacing chunk would be — because that is what it is.
    // Claude Code streams and leaves this empty; Codex reports it once.
    const result = entryText(body.result);
    if (result) fold.output = result;
    return fold;
  }
  return fold;
}

// A folded command as one block. `running` is the state the aggregate reports —
// a background job whose launch already "finished" is still running, and the
// block says so rather than claiming a result it does not have.
function shellBlockMarkup(fold, running) {
  const kind = fold.backgrounded || fold.execution !== "foreground"
    ? (fold.execution === "monitor" ? "monitor" : "background")
    : "cmd";
  const state = running ? null : fold.state;
  const exit = fold.exitCode === null || fold.exitCode === undefined || fold.exitCode === 0
    ? ""
    : '<span class="cqt">exit ' + escapeHtml(String(fold.exitCode)) + "</span>";
  const body = [
    fold.status ? '<pre class="ope">' + ansiHtml(fold.status) + "</pre>" : "",
    fold.output ? '<pre class="opo">' + ansiHtml(fold.output) + "</pre>" : "",
  ].join("");
  return blockHtml({
    header: chipHtml(kind, kind === "cmd" ? "▶" : kind),
    summary: fold.command,
    body: body,
    state: state,
    startedAt: fold.startedAt,
    finishedAt: fold.finishedAt,
    quiet: true,
    tail: exit,
  });
}

function shellDescriptor(fold, running) {
  return {
    markup: shellBlockMarkup(fold, running),
    group: "commands",
    summaryKind: fold.execution === "monitor"
      ? "monitor"
      : (fold.backgrounded || fold.execution === "background") ? "background" : "shell",
    conversationKind: "",
    state: running ? null : fold.state,
    linesAdded: 0,
    linesRemoved: 0,
    final: false,
  };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    ENTRY_GROUPS, ENTRY_SUMMARY_KINDS, conversationKind, entryActorName,
    entryDescriptor, entryMarkup, entryState, foldShellEntry, newShellFold,
    shellDescriptor,
  };
}
