/* eslint-disable no-control-regex -- ANSI input and private HTML placeholders use control characters by design. */

import type { ElementContent, Root, RootContent, Text } from 'hast';
import { toHtml } from 'hast-util-to-html';
import { refractor } from 'refractor';
import tsx from 'refractor/tsx';

refractor.register(tsx);

const HEADING = /^(#{1,4})\s+(.*?)\s*#*\s*$/;
const HORIZONTAL_RULE = /^ {0,3}([-*_])(?:\s*\1){2,}\s*$/;
const UNORDERED_ITEM = /^ {0,3}[-*+]\s+(.*)$/;
const ORDERED_ITEM = /^ {0,3}\d+[.)]\s+(.*)$/;
const QUOTE = /^ {0,3}>\s?(.*)$/;
const FENCE = /^ {0,3}(`{3,}|~{3,})\s*([\w.+-]*)\s*$/;
const TABLE_SEPARATOR = /^ {0,3}\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)*\|?\s*$/;
const CODE = /`([^`\n]+?)`/g;
const LINK = /\[([^\]\n]*)\]\(([^)\s]+)\)/g;
const BOLD = /\*\*(\S.*?\S|\S)\*\*|__(\S.*?\S|\S)__/g;
const ITALIC =
  /(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])|(?<![\w_])_(?!\s)(.+?)(?<!\s)_(?![\w_])/g;
const BARE_URL = /https?:\/\/(?:(?!&lt;|&gt;)[^\s\u0000\u0001])+/g;
const PLACEHOLDER = /\u0000(\d+)\u0001/g;
const URL_TRAIL = ['&amp;', '.', ',', ';', ':', '!', '?', '*', "'", '"'];
const ANSI_SGR = /\u001b\[([0-9;]*)m/g;
const ANSI_NOISE =
  /\u001b\][^\u0007\u001b]*(?:\u0007|\u001b\\)|\u001b[[?][0-9;]*[A-Za-z]|\u001b[()][0-9A-Za-z]|[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g;
const DIFF_HUNK = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/;

const LANGUAGE_BY_EXTENSION: Readonly<Record<string, string>> = {
  bash: 'bash',
  c: 'c',
  cc: 'cpp',
  cpp: 'cpp',
  cs: 'csharp',
  css: 'css',
  go: 'go',
  h: 'cpp',
  hpp: 'cpp',
  htm: 'markup',
  html: 'markup',
  ini: 'ini',
  java: 'java',
  js: 'javascript',
  json: 'json',
  jsonl: 'json',
  jsx: 'jsx',
  kt: 'kotlin',
  less: 'less',
  lua: 'lua',
  md: 'markdown',
  php: 'php',
  py: 'python',
  rb: 'ruby',
  rs: 'rust',
  sass: 'sass',
  scss: 'scss',
  sh: 'bash',
  sql: 'sql',
  svg: 'markup',
  swift: 'swift',
  toml: 'ini',
  ts: 'typescript',
  tsx: 'tsx',
  xml: 'markup',
  yaml: 'yaml',
  yml: 'yaml',
  zsh: 'bash',
};

const LANGUAGE_BY_NAME: Readonly<Record<string, string>> = {
  dockerfile: 'bash',
  makefile: 'makefile',
};

type Rgb = readonly [number, number, number];

type AnsiState = {
  foreground?: Rgb;
  background?: Rgb;
  bold?: boolean;
  dim?: boolean;
  italic?: boolean;
  underline?: boolean;
};

type DiffRow =
  | { readonly kind: 'separator' }
  | {
      readonly kind: 'removed' | 'added' | 'context';
      readonly number: number;
      readonly text: string;
      changed: readonly [number, number] | null;
    };

const ANSI_BASIC: readonly Rgb[] = [
  [40, 44, 52],
  [224, 108, 117],
  [152, 195, 121],
  [229, 192, 123],
  [97, 175, 239],
  [198, 120, 221],
  [86, 182, 194],
  [171, 178, 191],
];

const ANSI_BRIGHT: readonly Rgb[] = [
  [92, 99, 112],
  [224, 108, 117],
  [152, 195, 121],
  [229, 192, 123],
  [97, 175, 239],
  [198, 120, 221],
  [86, 182, 194],
  [255, 255, 255],
];

const HTML_BOUNDARY = Symbol('escaped-html');

export type EscapedHtml = {
  readonly value: string;
  readonly [HTML_BOUNDARY]: true;
};

function escapedHtml(value: string): EscapedHtml {
  return { value, [HTML_BOUNDARY]: true };
}

function escapeText(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function safeUrl(value: string): string {
  return /^https?:\/\//i.test(value)
    ? escapeText(value).replace(/"/g, '&quot;')
    : '';
}

function trimUrl(value: string): readonly [string, string] {
  let url = value;
  let trail = '';
  for (;;) {
    const suffix = URL_TRAIL.find((candidate) => url.endsWith(candidate));
    if (suffix !== undefined) {
      url = url.slice(0, -suffix.length);
      trail = suffix + trail;
      continue;
    }
    if (
      url.endsWith(')') &&
      url.split('(').length - 1 < url.split(')').length - 1
    ) {
      url = url.slice(0, -1);
      trail = `)${trail}`;
      continue;
    }
    return [url, trail];
  }
}

function inlineMarkup(value: string): string {
  const stash: string[] = [];
  let output = value.replace(CODE, (_whole, code: string) => {
    stash.push(`<code>${escapeText(code)}</code>`);
    return `\u0000${String(stash.length - 1)}\u0001`;
  });
  output = output.replace(LINK, (_whole, label: string, target: string) => {
    const href = safeUrl(target);
    const body = inlineMarkup(label) || escapeText(target);
    stash.push(
      href.length > 0
        ? `<a href="${href}" target="_blank" rel="noopener noreferrer">${body}</a>`
        : body,
    );
    return `\u0000${String(stash.length - 1)}\u0001`;
  });
  output = escapeText(output);
  output = output.replace(
    BOLD,
    (_whole, first: string | undefined, second: string | undefined) =>
      `<strong>${first ?? second ?? ''}</strong>`,
  );
  output = output.replace(
    ITALIC,
    (_whole, first: string | undefined, second: string | undefined) =>
      `<em>${first ?? second ?? ''}</em>`,
  );
  output = output.replace(BARE_URL, (match) => {
    const [url, trail] = trimUrl(match);
    return url.length === 0
      ? match
      : `<a href="${url.replace(/"/g, '&quot;')}" target="_blank" rel="noopener noreferrer">${url}</a>${trail}`;
  });
  return output.replace(PLACEHOLDER, (_whole, rawIndex: string) => {
    const index = Number(rawIndex);
    return Number.isSafeInteger(index) ? (stash[index] ?? '') : '';
  });
}

function tableCells(line: string): readonly string[] {
  return line
    .trim()
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split('|')
    .map((cell) => cell.trim());
}

function isTableSeparator(line: string): boolean {
  return line.includes('|') && TABLE_SEPARATOR.test(line);
}

function markdownBlocks(source: string): string {
  const lines = source.replace(/\r\n?/g, '\n').split('\n');
  const output: string[] = [];
  let index = 0;
  while (index < lines.length) {
    const line = lines[index] ?? '';
    const fence = FENCE.exec(line);
    if (fence !== null) {
      const delimiter = fence[1]?.at(0) ?? '`';
      const body: string[] = [];
      index += 1;
      while (index < lines.length) {
        const candidate = FENCE.exec(lines[index] ?? '');
        if (candidate?.[1]?.at(0) === delimiter) {
          index += 1;
          break;
        }
        body.push(lines[index] ?? '');
        index += 1;
      }
      output.push(`<pre><code>${escapeText(body.join('\n'))}</code></pre>`);
      continue;
    }
    if (line.trim().length === 0) {
      index += 1;
      continue;
    }
    if (HORIZONTAL_RULE.test(line)) {
      output.push('<hr>');
      index += 1;
      continue;
    }
    const heading = HEADING.exec(line);
    if (heading !== null) {
      const level = Math.min((heading[1]?.length ?? 1) + 2, 6);
      output.push(
        `<h${String(level)}>${inlineMarkup(heading[2] ?? '')}</h${String(level)}>`,
      );
      index += 1;
      continue;
    }
    const unordered = UNORDERED_ITEM.test(line);
    if (unordered || ORDERED_ITEM.test(line)) {
      const items: string[] = [];
      while (index < lines.length) {
        const item = unordered
          ? UNORDERED_ITEM.exec(lines[index] ?? '')
          : ORDERED_ITEM.exec(lines[index] ?? '');
        if (item === null) break;
        items.push(`<li>${inlineMarkup(item[1] ?? '')}</li>`);
        index += 1;
      }
      const tag = unordered ? 'ul' : 'ol';
      output.push(`<${tag}>${items.join('')}</${tag}>`);
      continue;
    }
    if (QUOTE.test(line)) {
      const quoted: string[] = [];
      while (index < lines.length) {
        const quote = QUOTE.exec(lines[index] ?? '');
        if (quote === null) break;
        quoted.push(quote[1] ?? '');
        index += 1;
      }
      output.push(
        `<blockquote>${markdownBlocks(quoted.join('\n'))}</blockquote>`,
      );
      continue;
    }
    if (
      line.includes('|') &&
      index + 1 < lines.length &&
      isTableSeparator(lines[index + 1] ?? '')
    ) {
      const header = tableCells(line);
      const rows: (readonly string[])[] = [];
      index += 2;
      while (
        index < lines.length &&
        (lines[index] ?? '').includes('|') &&
        (lines[index] ?? '').trim().length > 0
      ) {
        rows.push(tableCells(lines[index] ?? ''));
        index += 1;
      }
      output.push(
        `<table><thead><tr>${header.map((cell) => `<th>${inlineMarkup(cell)}</th>`).join('')}</tr></thead><tbody>${rows.map((row) => `<tr>${row.map((cell) => `<td>${inlineMarkup(cell)}</td>`).join('')}</tr>`).join('')}</tbody></table>`,
      );
      continue;
    }
    const paragraph: string[] = [];
    while (index < lines.length && (lines[index] ?? '').trim().length > 0) {
      const next = lines[index] ?? '';
      if (
        HORIZONTAL_RULE.test(next) ||
        HEADING.test(next) ||
        FENCE.test(next) ||
        UNORDERED_ITEM.test(next) ||
        ORDERED_ITEM.test(next) ||
        QUOTE.test(next)
      )
        break;
      paragraph.push(next);
      index += 1;
    }
    if (paragraph.length > 0)
      output.push(`<p>${inlineMarkup(paragraph.join('\n'))}</p>`);
  }
  return output.join('');
}

export function markdownHtml(value: string): EscapedHtml {
  try {
    return escapedHtml(markdownBlocks(value));
  } catch {
    return escapedHtml(`<p>${escapeText(value)}</p>`);
  }
}

export function plainTextHtml(value: string): EscapedHtml {
  return escapedHtml(`<p class="plain-text">${escapeText(value)}</p>`);
}

function changedRanges(
  before: string,
  after: string,
): readonly [readonly [number, number], readonly [number, number]] {
  let prefix = 0;
  const limit = Math.min(before.length, after.length);
  while (prefix < limit && before[prefix] === after[prefix]) prefix += 1;
  let suffix = 0;
  const remaining = Math.min(before.length - prefix, after.length - prefix);
  while (
    suffix < remaining &&
    before[before.length - suffix - 1] === after[after.length - suffix - 1]
  )
    suffix += 1;
  return [
    [prefix, before.length - suffix],
    [prefix, after.length - suffix],
  ];
}

function markChangedRows(rows: DiffRow[]): void {
  let index = 0;
  while (index < rows.length) {
    if (rows[index]?.kind !== 'removed') {
      index += 1;
      continue;
    }
    const removedStart = index;
    while (rows[index]?.kind === 'removed') index += 1;
    const addedStart = index;
    while (rows[index]?.kind === 'added') index += 1;
    const pairs = Math.min(addedStart - removedStart, index - addedStart);
    for (let offset = 0; offset < pairs; offset += 1) {
      const removed = rows[removedStart + offset];
      const added = rows[addedStart + offset];
      if (removed?.kind !== 'removed' || added?.kind !== 'added') continue;
      const [removedRange, addedRange] = changedRanges(
        removed.text,
        added.text,
      );
      removed.changed = removedRange;
      added.changed = addedRange;
    }
  }
}

function diffRows(value: string): DiffRow[] {
  const rows: DiffRow[] = [];
  let oldNumber: number | null = null;
  let newNumber: number | null = null;
  for (const line of value.replace(/\r\n?/g, '\n').split('\n')) {
    const hunk = DIFF_HUNK.exec(line);
    if (hunk !== null) {
      if (oldNumber !== null) rows.push({ kind: 'separator' });
      oldNumber = Number(hunk[1] ?? '0');
      newNumber = Number(hunk[2] ?? '0');
      continue;
    }
    if (
      oldNumber === null ||
      newNumber === null ||
      line.startsWith('--- ') ||
      line.startsWith('+++ ') ||
      line === '\\ No newline at end of file'
    )
      continue;
    if (line.startsWith('-')) {
      rows.push({
        kind: 'removed',
        number: oldNumber,
        text: line.slice(1),
        changed: null,
      });
      oldNumber += 1;
    } else if (line.startsWith('+')) {
      rows.push({
        kind: 'added',
        number: newNumber,
        text: line.slice(1),
        changed: null,
      });
      newNumber += 1;
    } else if (line.startsWith(' ')) {
      rows.push({
        kind: 'context',
        number: newNumber,
        text: line.slice(1),
        changed: null,
      });
      oldNumber += 1;
      newNumber += 1;
    }
  }
  markChangedRows(rows);
  return rows;
}

function languageFor(path: string, value: string): string | null {
  const name = path.split(/[\\/]/).at(-1)?.toLowerCase() ?? '';
  const namedLanguage = LANGUAGE_BY_NAME[name];
  if (namedLanguage !== undefined) return namedLanguage;
  const extension = name.slice(name.lastIndexOf('.') + 1);
  if (extension === 'svelte' || extension === 'vue')
    return /<\/?[a-z]|{[#/:@]/i.test(value) ? 'markup' : 'typescript';
  return LANGUAGE_BY_EXTENSION[extension] ?? null;
}

function text(value: string): Text {
  return { type: 'text', value };
}

function markedText(
  node: Text,
  range: readonly [number, number],
  position: { value: number },
): ElementContent[] {
  const start = position.value;
  const end = start + node.value.length;
  position.value = end;
  const markedStart = Math.max(start, range[0]);
  const markedEnd = Math.min(end, range[1]);
  if (markedStart >= markedEnd) return [node];
  const children: ElementContent[] = [];
  if (start < markedStart)
    children.push(text(node.value.slice(0, markedStart - start)));
  children.push({
    type: 'element',
    tagName: 'mark',
    properties: { className: ['changed'] },
    children: [text(node.value.slice(markedStart - start, markedEnd - start))],
  });
  if (markedEnd < end) children.push(text(node.value.slice(markedEnd - start)));
  return children;
}

function markRange(
  children: ElementContent[],
  range: readonly [number, number],
  position: { value: number },
): ElementContent[] {
  return children.flatMap((node) => {
    if (node.type === 'text') return markedText(node, range, position);
    if (node.type !== 'element') return [node];
    return [
      {
        ...node,
        children: markRange(node.children, range, position),
      },
    ];
  });
}

function markRootRange(
  children: RootContent[],
  range: readonly [number, number],
): RootContent[] {
  const position = { value: 0 };
  const marked: RootContent[] = [];
  for (const node of children) {
    if (node.type === 'doctype') marked.push(node);
    else marked.push(...markRange([node], range, position));
  }
  return marked;
}

function highlightedCode(
  value: string,
  path: string,
  changed: readonly [number, number] | null = null,
): string {
  const language = languageFor(path, value);
  const tree: Root =
    language === null
      ? { type: 'root', children: [text(value)] }
      : refractor.highlight(value, language);
  if (changed !== null) tree.children = markRootRange(tree.children, changed);
  return toHtml(tree);
}

function changedCode(
  row: Exclude<DiffRow, { readonly kind: 'separator' }>,
  path: string,
): string {
  return highlightedCode(row.text, path, row.changed);
}

export function unifiedDiffHtml(value: string, path: string): EscapedHtml {
  const rows = diffRows(value).map((row) => {
    if (row.kind === 'separator')
      return '<div class="dl sep"><span class="ln"></span><span class="tx">⋮</span></div>';
    if (row.kind === 'context')
      return `<div class="dl context"><span class="ln">${String(row.number)}</span><span class="tx">${changedCode(row, path)}</span></div>`;
    const marker = row.kind === 'removed' ? '−' : '+';
    const label = `${row.kind} line ${String(row.number)}`;
    return `<div class="dl ${row.kind}" aria-label="${label}"><span class="ln"><span class="dm" aria-hidden="true">${marker}</span>${String(row.number)}</span><span class="tx">${changedCode(row, path)}</span></div>`;
  });
  return escapedHtml(`<div class="tdiff">${rows.join('')}</div>`);
}

export function sourceHtml(value: string, path: string): EscapedHtml {
  const lines = value.split(/\r?\n/);
  const rows = lines
    .filter((line, index) => index < lines.length - 1 || line.length > 0)
    .map(
      (line, index) =>
        `<div class="dl context"><span class="ln">${String(index + 1)}</span><span class="tx">${highlightedCode(line, path)}</span></div>`,
    );
  return escapedHtml(`<div class="tdiff">${rows.join('')}</div>`);
}

function xtermColor(index: number): Rgb {
  if (index < 8) return ANSI_BASIC[index] ?? [0, 0, 0];
  if (index < 16) return ANSI_BRIGHT[index - 8] ?? [255, 255, 255];
  if (index < 232) {
    const steps = [0, 95, 135, 175, 215, 255];
    const value = index - 16;
    return [
      steps[Math.floor(value / 36)] ?? 0,
      steps[Math.floor(value / 6) % 6] ?? 0,
      steps[value % 6] ?? 0,
    ];
  }
  const gray = 8 + (index - 232) * 10;
  return [gray, gray, gray];
}

function resetAnsi(state: AnsiState): void {
  delete state.foreground;
  delete state.background;
  delete state.bold;
  delete state.dim;
  delete state.italic;
  delete state.underline;
}

function setAnsiColor(
  state: AnsiState,
  channel: 'foreground' | 'background',
  color: Rgb | undefined,
): void {
  if (color === undefined) return;
  if (channel === 'foreground') state.foreground = color;
  else state.background = color;
}

function applyAnsi(state: AnsiState, parameters: readonly number[]): void {
  let index = 0;
  while (index < parameters.length) {
    const code = parameters[index] ?? 0;
    if (code === 0) resetAnsi(state);
    else if (code === 1) state.bold = true;
    else if (code === 2) state.dim = true;
    else if (code === 3) state.italic = true;
    else if (code === 4) state.underline = true;
    else if (code === 22) {
      delete state.bold;
      delete state.dim;
    } else if (code === 23) delete state.italic;
    else if (code === 24) delete state.underline;
    else if (code === 39) delete state.foreground;
    else if (code === 49) delete state.background;
    else if (code === 38 || code === 48) {
      const extended = parameters[index + 1];
      let color: Rgb | null = null;
      if (extended === 5) {
        color = xtermColor(parameters[index + 2] ?? 0);
        index += 2;
      } else if (extended === 2) {
        color = [
          parameters[index + 2] ?? 0,
          parameters[index + 3] ?? 0,
          parameters[index + 4] ?? 0,
        ];
        index += 4;
      }
      if (color !== null) {
        if (code === 38) state.foreground = color;
        else state.background = color;
      }
    } else if (code >= 30 && code <= 37)
      setAnsiColor(state, 'foreground', ANSI_BASIC[code - 30]);
    else if (code >= 90 && code <= 97)
      setAnsiColor(state, 'foreground', ANSI_BRIGHT[code - 90]);
    else if (code >= 40 && code <= 47)
      setAnsiColor(state, 'background', ANSI_BASIC[code - 40]);
    else if (code >= 100 && code <= 107)
      setAnsiColor(state, 'background', ANSI_BRIGHT[code - 100]);
    index += 1;
  }
}

function ansiCss(state: AnsiState): string {
  const parts: string[] = [];
  if (state.foreground !== undefined)
    parts.push(`color:rgb(${state.foreground.join(',')})`);
  if (state.background !== undefined)
    parts.push(`background:rgb(${state.background.join(',')})`);
  if (state.bold === true) parts.push('font-weight:600');
  if (state.dim === true) parts.push('opacity:.55');
  if (state.italic === true) parts.push('font-style:italic');
  if (state.underline === true) parts.push('text-decoration:underline');
  return parts.join(';');
}

export function ansiHtml(value: string): EscapedHtml {
  const state: AnsiState = {};
  const output: string[] = [];
  let last = 0;
  ANSI_SGR.lastIndex = 0;
  let match = ANSI_SGR.exec(value);
  const flush = (segment: string): void => {
    const body = escapeText(segment.replace(ANSI_NOISE, ''));
    if (body.length === 0) return;
    const css = ansiCss(state);
    output.push(css.length > 0 ? `<span style="${css}">${body}</span>` : body);
  };
  while (match !== null) {
    flush(value.slice(last, match.index));
    last = match.index + match[0].length;
    applyAnsi(
      state,
      (match[1] ?? '0').split(';').map((part) => Number(part || '0')),
    );
    match = ANSI_SGR.exec(value);
  }
  flush(value.slice(last));
  ANSI_SGR.lastIndex = 0;
  return escapedHtml(output.join(''));
}
