"use strict";
// Part of the dashboard SPA — the presentation leaf: text to markup.
//
// This is what moved into the browser when the daemon stopped rendering. The
// server used to ship pre-escaped HTML per feed item; now it ships the text and
// this turns it into nodes — so a resize, a density change or a re-collapse is a
// local repaint instead of a request.
//
// THE ESCAPING DISCIPLINE IS THE WHOLE DESIGN, and it is the same one the server
// followed: text becomes page content only through `escapeHtml`, at the leaf, as
// the FIRST thing done to it. Block structure is detected on the raw lines —
// the sigils #-*>`[]()| are ASCII and emit nothing themselves — but every
// fragment that reaches the output is escaped where it is emitted, never
// "escaped later". Nothing here ever inserts a caller's string as markup.
//
// It is a SUBSET on purpose: correctness of escaping beats markdown
// completeness, and malformed input degrades to escaped plain text. One thing
// the server did that this cannot: syntax-highlight a fenced block. That needed
// pygments, the dashboard has no build step and no dependencies, and a fence
// renders as plain escaped code instead.

const MD_HEAD = /^(#{1,4})\s+(.*?)\s*#*\s*$/;
const MD_HR = /^ {0,3}([-*_])(?:\s*\1){2,}\s*$/;
const MD_UL = /^ {0,3}[-*+]\s+(.*)$/;
const MD_OL = /^ {0,3}\d+[.)]\s+(.*)$/;
const MD_QUOTE = /^ {0,3}>\s?(.*)$/;
const MD_FENCE = /^ {0,3}(`{3,}|~{3,})\s*([\w.+-]*)\s*$/;
// A pipe-table delimiter row. The shape alone also matches a bare "---", so
// `isTableSeparator` additionally requires a pipe — otherwise it would collide
// with MD_HR and eat every horizontal rule.
const MD_TSEP = /^ {0,3}\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)*\|?\s*$/;
// Emphasis must HUG non-space text, so "2 * 3" and a bare "*" are left alone.
const MD_CODE = /`([^`\n]+?)`/g;
const MD_LINK = /\[([^\]\n]*)\]\(([^)\s]+)\)/g;
const MD_BOLD = /\*\*(\S.*?\S|\S)\*\*|__(\S.*?\S|\S)__/g;
const MD_ITAL = /(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])|(?<![\w_])_(?!\s)(.+?)(?<!\s)_(?![\w_])/g;
// A bare URL in prose. Runs on ESCAPED text, so raw <> cannot occur — the
// tempered class stops at their entities instead. \x00/\x01 are excluded so a
// URL can never swallow a stashed code-span or link placeholder.
const MD_URL = /https?:\/\/(?:(?!&lt;|&gt;)[^\s\x00\x01])+/g;
// What a bare URL's tail gives back to the sentence. The &amp; entity first,
// since it also ends in ";" and peeling only the ";" would strand "&amp".
const URL_TRAIL = ["&amp;", ".", ",", ";", ":", "!", "?", "*", "'", "\""];

function escapeHtml(text) {
  return String(text == null ? "" : text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

/* ---------- file content ---------------------------------------------------
   File bytes now arrive in the session entry itself. The old server-side
   content route parsed unified diffs before returning HTML; keep that parser
   at the presentation leaf now that each frontend owns its rendering. */

const DIFF_HUNK = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/;

function changedRanges(before, after) {
  let prefix = 0;
  const limit = Math.min(before.length, after.length);
  while (prefix < limit && before[prefix] === after[prefix]) prefix += 1;
  let suffix = 0;
  const remaining = Math.min(before.length - prefix, after.length - prefix);
  while (suffix < remaining
         && before[before.length - suffix - 1] === after[after.length - suffix - 1]) {
    suffix += 1;
  }
  return [[prefix, before.length - suffix], [prefix, after.length - suffix]];
}

function markChangedDiffRows(rows) {
  let index = 0;
  while (index < rows.length) {
    if (rows[index].kind !== "removed") { index += 1; continue; }
    const removedStart = index;
    while (index < rows.length && rows[index].kind === "removed") index += 1;
    const addedStart = index;
    while (index < rows.length && rows[index].kind === "added") index += 1;
    const pairs = Math.min(addedStart - removedStart, index - addedStart);
    for (let offset = 0; offset < pairs; offset += 1) {
      const removed = rows[removedStart + offset];
      const added = rows[addedStart + offset];
      [removed.changed, added.changed] = changedRanges(removed.text, added.text);
    }
  }
  return rows;
}

function diffRows(unifiedDiff) {
  const parsed = [];
  let oldNumber = null;
  let newNumber = null;
  for (const line of String(unifiedDiff || "").replace(/\r\n?/g, "\n").split("\n")) {
    const hunk = DIFF_HUNK.exec(line);
    if (hunk) {
      if (oldNumber !== null) parsed.push({ kind: "sep", number: null, text: "⋮" });
      oldNumber = Number(hunk[1]);
      newNumber = Number(hunk[2]);
      continue;
    }
    if (oldNumber === null || newNumber === null || line.startsWith("--- ")
        || line.startsWith("+++ ") || line === "\\ No newline at end of file") continue;
    if (line.startsWith("-")) {
      parsed.push({ kind: "removed", number: oldNumber, text: line.slice(1), changed: null });
      oldNumber += 1;
    } else if (line.startsWith("+")) {
      parsed.push({ kind: "added", number: newNumber, text: line.slice(1), changed: null });
      newNumber += 1;
    } else if (line.startsWith(" ")) {
      parsed.push({ kind: "context", number: newNumber, text: line.slice(1), changed: null });
      oldNumber += 1;
      newNumber += 1;
    }
  }
  return markChangedDiffRows(parsed);
}

function diffCodeHtml(row) {
  if (!row.changed) return escapeHtml(row.text);
  const [from, to] = row.changed;
  return escapeHtml(row.text.slice(0, from))
    + '<mark class="changed">' + escapeHtml(row.text.slice(from, to)) + "</mark>"
    + escapeHtml(row.text.slice(to));
}

function unifiedDiffHtml(unifiedDiff) {
  return '<div class="tdiff">' + diffRows(unifiedDiff).map(row => {
    if (row.kind === "sep") {
      return '<div class="dl sep"><span class="ln"></span><span class="tx">⋮</span></div>';
    }
    return '<div class="dl ' + row.kind + '"><span class="ln">' + row.number
      + '</span><span class="tx">' + diffCodeHtml(row) + "</span></div>";
  }).join("") + "</div>";
}

function sourceHtml(source) {
  return '<div class="tdiff">' + String(source || "").split(/\r?\n/)
    .filter((line, index, lines) => index < lines.length - 1 || line)
    .map((line, index) => '<div class="dl context"><span class="ln">' + (index + 1)
      + '</span><span class="tx">' + escapeHtml(line) + "</span></div>")
    .join("") + "</div>";
}

// A URL going into an attribute: only http(s) survives, so no javascript: or
// data: string can ever reach an href.
function safeUrl(url) {
  const text = String(url || "");
  return /^https?:\/\//i.test(text) ? escapeHtml(text).replace(/"/g, "&quot;") : "";
}

function trimUrl(url) {
  // In "see https://x.test." the period is the sentence's; a ")" is peeled only
  // while unbalanced, so ".../Foo_(bar)" survives and "(see https://x)" does not.
  let trail = "";
  for (;;) {
    let peeled = false;
    for (const suffix of URL_TRAIL) {
      if (url.endsWith(suffix)) {
        url = url.slice(0, -suffix.length);
        trail = suffix + trail;
        peeled = true;
        break;
      }
    }
    if (peeled) continue;
    if (url.endsWith(")") && (url.split("(").length - 1) < (url.split(")").length - 1)) {
      url = url.slice(0, -1);
      trail = ")" + trail;
      continue;
    }
    return [url, trail];
  }
}

// Inline markup. Code spans and links are stashed behind control characters
// FIRST, so emphasis and autolinking can never reach inside them — the same
// two-phase trick the server used, and the reason \x00/\x01 are excluded from
// the URL class above.
function mdInline(text) {
  const stash = [];
  let out = String(text == null ? "" : text);
  out = out.replace(MD_CODE, (whole, code) => {
    stash.push("<code>" + escapeHtml(code) + "</code>");
    return "\x00" + (stash.length - 1) + "\x01";
  });
  out = out.replace(MD_LINK, (whole, label, target) => {
    const href = safeUrl(target);
    const text_ = mdInline(label) || escapeHtml(target);
    stash.push(
      href
        ? '<a href="' + href + '" target="_blank" rel="noopener noreferrer">' + text_ + "</a>"
        : text_
    );
    return "\x00" + (stash.length - 1) + "\x01";
  });
  out = escapeHtml(out);
  out = out.replace(MD_BOLD, (whole, one, two) => "<strong>" + (one || two) + "</strong>");
  out = out.replace(MD_ITAL, (whole, one, two) => "<em>" + (one || two) + "</em>");
  out = out.replace(MD_URL, match => {
    const [url, trail] = trimUrl(match);
    if (!url) return match;
    return (
      '<a href="' + url.replace(/"/g, "&quot;") + '" target="_blank" rel="noopener noreferrer">'
      + url + "</a>" + trail
    );
  });
  return out.replace(/\x00(\d+)\x01/g, (whole, index) => stash[Number(index)]);
}

function isTableSeparator(line) {
  return line.includes("|") && MD_TSEP.test(line);
}

function tableCells(line) {
  const trimmed = line.trim().replace(/^\|/, "").replace(/\|$/, "");
  return trimmed.split("|").map(cell => cell.trim());
}

// The markdown subset, as HTML. Any failure degrades to escaped plain text:
// a malformed message must still be readable, and must still be safe.
function mdHtml(text) {
  try {
    return mdBlocks(String(text == null ? "" : text));
  } catch (error) {
    return "<p>" + escapeHtml(text) + "</p>";
  }
}

function mdBlocks(source) {
  const lines = source.replace(/\r\n?/g, "\n").split("\n");
  const out = [];
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    const fence = MD_FENCE.exec(line);
    if (fence) {
      const closing = fence[1][0];
      const body = [];
      index += 1;
      while (index < lines.length) {
        const candidate = MD_FENCE.exec(lines[index]);
        if (candidate && candidate[1][0] === closing) { index += 1; break; }
        body.push(lines[index]);
        index += 1;
      }
      out.push("<pre><code>" + escapeHtml(body.join("\n")) + "</code></pre>");
      continue;
    }
    if (!line.trim()) { index += 1; continue; }
    if (MD_HR.test(line)) { out.push("<hr>"); index += 1; continue; }
    const heading = MD_HEAD.exec(line);
    if (heading) {
      const level = Math.min(heading[1].length + 2, 6);
      out.push("<h" + level + ">" + mdInline(heading[2]) + "</h" + level + ">");
      index += 1;
      continue;
    }
    if (MD_UL.test(line) || MD_OL.test(line)) {
      const ordered = !MD_UL.test(line);
      const items = [];
      while (index < lines.length) {
        const item = ordered ? MD_OL.exec(lines[index]) : MD_UL.exec(lines[index]);
        if (!item) break;
        items.push("<li>" + mdInline(item[1]) + "</li>");
        index += 1;
      }
      const tag = ordered ? "ol" : "ul";
      out.push("<" + tag + ">" + items.join("") + "</" + tag + ">");
      continue;
    }
    if (MD_QUOTE.test(line)) {
      const quoted = [];
      while (index < lines.length && MD_QUOTE.test(lines[index])) {
        quoted.push(MD_QUOTE.exec(lines[index])[1]);
        index += 1;
      }
      out.push("<blockquote>" + mdBlocks(quoted.join("\n")) + "</blockquote>");
      continue;
    }
    if (
      line.includes("|")
      && index + 1 < lines.length
      && isTableSeparator(lines[index + 1])
    ) {
      const head = tableCells(line);
      index += 2;
      const rows = [];
      while (index < lines.length && lines[index].includes("|") && lines[index].trim()) {
        rows.push(tableCells(lines[index]));
        index += 1;
      }
      out.push(
        "<table><thead><tr>"
        + head.map(cell => "<th>" + mdInline(cell) + "</th>").join("")
        + "</tr></thead><tbody>"
        + rows
          .map(row => "<tr>" + row.map(cell => "<td>" + mdInline(cell) + "</td>").join("") + "</tr>")
          .join("")
        + "</tbody></table>"
      );
      continue;
    }
    const paragraph = [];
    while (index < lines.length && lines[index].trim()) {
      const next = lines[index];
      if (
        MD_HR.test(next) || MD_HEAD.test(next) || MD_FENCE.test(next)
        || MD_UL.test(next) || MD_OL.test(next) || MD_QUOTE.test(next)
      ) break;
      paragraph.push(next);
      index += 1;
    }
    if (paragraph.length) out.push("<p>" + mdInline(paragraph.join("\n")) + "</p>");
  }
  return out.join("");
}

/* ---------- terminal output ---------------------------------------------------
   A command's output arrives as the bytes a terminal would have shown, escape
   sequences and all. The colours are meaning — a failing test suite is red
   because the runner said so — so they are kept, as spans over a fixed palette,
   and everything else a terminal understands is dropped rather than guessed at.

   Runs on escaped text, like the markdown above: the tags below are the only
   markup that ever reaches the page. */

const ANSI_SGR = /\x1b\[([0-9;]*)m/g;
// Everything else a program might emit: cursor moves, erases, OSC titles,
// bracketed paste. A feed shows what was said, not where a cursor went.
const ANSI_NOISE = /\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[[?][0-9;]*[A-Za-z]|\x1b[()][0-9A-Za-z]|[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/g;
// One Dark-ish, and the same numbers the daemon used when it rendered this —
// four of the hues come from the semantic colour table, so foreign command
// output blends into a feed that was coloured by us.
const ANSI_BASIC = [
  [40, 44, 52], [224, 108, 117], [152, 195, 121], [229, 192, 123],
  [97, 175, 239], [198, 120, 221], [86, 182, 194], [171, 178, 191],
];
const ANSI_BRIGHT = [
  [92, 99, 112], [224, 108, 117], [152, 195, 121], [229, 192, 123],
  [97, 175, 239], [198, 120, 221], [86, 182, 194], [255, 255, 255],
];

// xterm 256-colour index -> rgb.
function xtermColor(index) {
  if (index < 8) return ANSI_BASIC[index];
  if (index < 16) return ANSI_BRIGHT[index - 8];
  if (index < 232) {
    const steps = [0, 95, 135, 175, 215, 255];
    const value = index - 16;
    return [steps[Math.floor(value / 36)], steps[Math.floor(value / 6) % 6], steps[value % 6]];
  }
  const gray = 8 + (index - 232) * 10;
  return [gray, gray, gray];
}

// Fold one SGR parameter list into the style state. Written as a loop with an
// index rather than a for-of because 38/48 CONSUME the parameters after them:
// an extended colour is one parameter naming how many more it takes.
function applyAnsiStyle(state, parameters) {
  let index = 0;
  while (index < parameters.length) {
    const code = parameters[index];
    if (code === 0) { for (const key of Object.keys(state)) delete state[key]; }
    else if (code === 1) state.bold = true;
    else if (code === 2) state.dim = true;
    else if (code === 3) state.italic = true;
    else if (code === 4) state.underline = true;
    else if (code === 22) { delete state.bold; delete state.dim; }
    else if (code === 23) delete state.italic;
    else if (code === 24) delete state.underline;
    else if (code === 39) delete state.fg;
    else if (code === 49) delete state.bg;
    else if (code === 38 || code === 48) {
      const key = code === 38 ? "fg" : "bg";
      if (parameters[index + 1] === 5) {
        state[key] = xtermColor(parameters[index + 2] || 0);
        index += 3;
        continue;
      }
      if (parameters[index + 1] === 2) {
        state[key] = [
          parameters[index + 2] || 0, parameters[index + 3] || 0, parameters[index + 4] || 0,
        ];
        index += 5;
        continue;
      }
    } else if (code >= 30 && code <= 37) state.fg = ANSI_BASIC[code - 30];
    else if (code >= 90 && code <= 97) state.fg = ANSI_BRIGHT[code - 90];
    else if (code >= 40 && code <= 47) state.bg = ANSI_BASIC[code - 40];
    else if (code >= 100 && code <= 107) state.bg = ANSI_BRIGHT[code - 100];
    index += 1;
  }
}

function ansiStyleCss(state) {
  const parts = [];
  if (state.fg) parts.push("color:rgb(" + state.fg.join(",") + ")");
  if (state.bg) parts.push("background:rgb(" + state.bg.join(",") + ")");
  if (state.bold) parts.push("font-weight:600");
  if (state.dim) parts.push("opacity:.55");
  if (state.italic) parts.push("font-style:italic");
  if (state.underline) parts.push("text-decoration:underline");
  return parts.join(";");
}

// Terminal output as HTML: the colours kept, the cursor games dropped.
//
// The colour codes are read FIRST and the noise stripped from what is left, in
// that order and not the other way round: a colour code IS an escape sequence,
// so a noise filter run first eats every one of them and the output comes out
// uniformly grey.
//
// Inline styles rather than classes, exactly as the daemon emitted them: the
// palette is 256 colours plus truecolor, and a stylesheet cannot name those.
function ansiHtml(text) {
  const source = String(text == null ? "" : text);
  const state = {};
  let out = "";
  let last = 0;
  let match;

  const flush = segment => {
    const body = escapeHtml(segment.replace(ANSI_NOISE, ""));
    if (!body) return;
    const css = ansiStyleCss(state);
    out += css ? '<span style="' + css + '">' + body + "</span>" : body;
  };

  ANSI_SGR.lastIndex = 0;
  while ((match = ANSI_SGR.exec(source)) !== null) {
    flush(source.slice(last, match.index));
    last = match.index + match[0].length;
    applyAnsiStyle(
      state,
      (match[1] || "0").split(";").map(part => Number(part || 0)),
    );
  }
  flush(source.slice(last));
  return out;
}

/* ---------- the one block shape ----------------------------------------------
   Every feed entry paints the same frame — a header, a summary, a tail, a body —
   because the density modes, the collapse pass and the run summaries all read
   that frame off the DOM. This is the one place it is built, so a renderer below
   decides only what goes INSIDE it. */

function outcomeAttribute(state) {
  if (!state || state === "running") return "run";
  return state === "succeeded" ? "ok" : "bad";
}

function durationText(seconds) {
  const total = Math.max(0, Math.round(Number(seconds) || 0));
  if (total < 60) return total + "s";
  if (total < 3600) return Math.floor(total / 60) + "m " + (total % 60) + "s";
  return Math.floor(total / 3600) + "h " + Math.floor((total % 3600) / 60) + "m";
}

// The block's frame, as HTML. `header`, `body` and `tail` are MARKUP the callers
// below built with the helpers above; `summary` is text and is escaped here.
function blockHtml(spec) {
  const attributes = ['class="blk"', 'data-open="0"'];
  if (spec.note) attributes.push('data-note="1"');
  if (spec.quiet) attributes.push('data-quiet="1"');
  attributes.push('data-out="' + outcomeAttribute(spec.state) + '"');
  let tail = spec.tail || "";
  if (spec.quiet && (!spec.state || spec.state === "running") && spec.startedAt) {
    tail = '<span class="chip blive" data-anchor="' + Number(spec.startedAt) + '"></span>';
  } else if (spec.quiet && spec.state && spec.finishedAt) {
    const verdict = spec.state === "succeeded" ? "finished" : spec.state;
    const elapsed = spec.startedAt
      ? " · " + durationText(spec.finishedAt - spec.startedAt)
      : "";
    tail = '<span class="cqt">' + escapeHtml(verdict + elapsed) + "</span>";
  }
  return (
    "<div " + attributes.join(" ") + '><div class="bhead">'
    + '<span class="bchips">' + (spec.header || "") + "</span>"
    + '<span class="bsum">' + escapeHtml(spec.summary || "") + "</span>"
    + '<span class="btail">' + tail + '</span><span class="blinks"></span>'
    + '</div><div class="bbody">' + (spec.body || "") + "</div></div>"
  );
}

// One chip: the little labelled tag a block's header is made of.
function chipHtml(kind, text) {
  return '<span class="chip ' + escapeHtml(kind) + '">' + escapeHtml(text) + "</span>";
}

// An agent-note row — the quiet one-line form a skill, an assignment or a
// compaction takes.
function noteHtml(state, text) {
  return (
    '<div class="anote" data-out="' + outcomeAttribute(state) + '">'
    + '<span class="anmark">⏺</span>'
    + '<span class="atext">' + escapeHtml(text) + "</span></div>"
  );
}

// Node.js loads this file to test the pure functions above; a browser has no
// `module` and skips it.
if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    ansiHtml, blockHtml, chipHtml, durationText, escapeHtml, mdHtml, mdInline,
    noteHtml, outcomeAttribute, safeUrl, trimUrl, xtermColor,
  };
}
