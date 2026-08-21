// tests/jsdom/domshim.js — the tiny DOM stand-in the node-driven harnesses run
// the REAL dashboard sources against (tests/jsdom/*.js; see docs/testing.md).
//
// Extracted when the second harness arrived: it is 90 lines of element/class/
// dataset/selector emulation, and a copy per harness is the duplication these
// harnesses exist to catch elsewhere. Deliberately dumb — it shims only what
// the page sources actually touch, and asserts nothing itself.
"use strict";

/* ---------- a real, small HTML parser --------------------------------------
   `innerHTML` is how every entry's markup (app.00a-markup.js, app.00b-entries.js)
   enters the DOM, so a shim that does not really parse it cannot tell "the
   markup is right but the click handler is broken" from "the markup never
   built the nodes the handler reads". Void tags and comments are the only
   HTML quirks the sources' own output ever needs. */
const VOID_TAGS = new Set([
  "area", "base", "br", "col", "embed", "hr", "img", "input",
  "link", "meta", "param", "source", "track", "wbr",
]);

function decodeEntities(text) {
  return text
    .replace(/&lt;/g, "<").replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"').replace(/&#39;/g, "'")
    .replace(/&nbsp;/g, " ").replace(/&amp;/g, "&");
}

function parseAttributes(text) {
  const attrs = {};
  const attrPattern = /([a-zA-Z_:][-\w:.]*)\s*=\s*("([^"]*)"|'([^']*)'|(\S+))/g;
  let match;
  while ((match = attrPattern.exec(text)) !== null) {
    attrs[match[1]] = match[3] !== undefined ? match[3]
      : match[4] !== undefined ? match[4] : match[5];
  }
  return attrs;
}

// Builds real element/text children under `root`, one open tag deep at a
// time — exactly what a browser's parser does, minus everything this
// codebase's own markup never emits (unquoted attributes aside, self-closing
// foreign tags, and so on).
function parseHtmlInto(root, html) {
  const tokenPattern =
    /<!--[\s\S]*?-->|<\/([a-zA-Z][\w-]*)\s*>|<([a-zA-Z][\w-]*)((?:\s+[^<>]*?)?)\s*(\/?)>|([^<]+)/g;
  const stack = [root];
  let match;
  while ((match = tokenPattern.exec(html)) !== null) {
    const [whole, closeTag, openTag, attrText, selfClosingMark, text] = match;
    if (whole.startsWith("<!--")) continue;
    if (closeTag) {
      if (stack.length > 1) stack.pop();
      continue;
    }
    if (openTag) {
      const attrs = parseAttributes(attrText || "");
      const node = new El(openTag, attrs.class || "");
      for (const [name, value] of Object.entries(attrs)) {
        if (!name.startsWith("data-")) continue;
        const camel = name.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase());
        node.dataset[camel] = value;
      }
      stack[stack.length - 1].append(node);
      if (!selfClosingMark && !VOID_TAGS.has(openTag.toLowerCase())) stack.push(node);
      continue;
    }
    if (text) stack[stack.length - 1].append(new El("#text", "", decodeEntities(text)));
  }
}

/* ---------- the DOM shim: elements, classes, dataset, class-selector queries */
class El {
  constructor(tag, cls, text) {
    this.tag = tag; this.className = cls || ""; this.dataset = {};
    this.children = []; this.parentNode = null; this._text = text || "";
    this.onclick = null;
    // enough of the event surface for the harnesses that DRIVE a form: the
    // sources attach a few listeners (blur, input) and never dispatch them
    this._on = {};
    // `style` is a plain bag of the properties the sources assign, PLUS
    // setProperty/getPropertyValue — the only channel for a CUSTOM property
    // (`--aname-w`, the accounts strip's name-column width): `style["--x"] = v`
    // is a no-op in a real browser, so a source that aligns columns through a
    // var can only be driven here if the shim speaks setProperty.
    this.style = {};
    Object.defineProperties(this.style, {       // non-enumerable: `style` stays
      setProperty: {                            // a plain bag of what was SET
        value(k, v) { this[k] = v; } },
      getPropertyValue: {
        value(k) { return this[k] === undefined ? "" : this[k]; } },
    });
    this.addEventListener = (t, f) => { (this._on[t] = this._on[t] || []).push(f); };
    this.removeEventListener = () => {};
    this.dispatch = (t) => { for (const f of this._on[t] || []) f({}); };
    this.focus = () => {};
    this.blur = () => {};
    this.select = () => {};
    // a no-op: dropdown()/suggest() scroll the highlighted row into view on open
    // — a harness that DRIVES a dropdown (newsession's tool picker) reaches it
    this.scrollIntoView = () => {};
    this.attrs = {};
    this.setAttribute = (k, v) => { this.attrs[k] = String(v); };
    this.getAttribute = (k) => (k in this.attrs ? this.attrs[k] : null);
    this.removeAttribute = (k) => { delete this.attrs[k]; };
    const self = this;
    this.classList = {
      // varargs, like the real DOM: `add("rec", "pre")` must add BOTH — a
      // single-arg shim silently dropped the second and made a correct source
      // look broken (the mic's capturing-but-not-connected state)
      add(...cs) {
        for (const c of cs)
          if (!self._cls().includes(c))
            self.className = (self.className + " " + c).trim();
      },
      remove(...cs) {
        self.className = self._cls().filter(x => !cs.includes(x)).join(" ");
      },
      contains(c) { return self._cls().includes(c); },
      toggle(c, on) { if (on) this.add(c); else this.remove(c); },
    };
  }
  _cls() { return this.className.split(/\s+/).filter(Boolean); }
  get textContent() { return this._text + this.children.map(c => c.textContent).join(""); }
  set textContent(v) { this._text = String(v); this.children = []; }
  // A DocumentFragment must FLATTEN on insert (its children move, the fragment
  // itself never becomes a node) — appendOlder batches a whole page into one.
  _flat(kids) {
    const out = [];
    for (const k of kids) {
      if (k.tag === "#frag") { out.push(...k.children); k.children = []; }
      else out.push(k);
    }
    return out;
  }
  // Inserting an ALREADY-PLACED node MOVES it — real DOM semantics, and load-bearing:
  // the live ⏱ chip is re-homed from one header slot to another (fillBlock), and a shim
  // that merely added it to the new parent left a phantom copy under the old one, so a
  // later `remove()` looked like it had failed.
  _adopt(k) {
    if (k.parentNode && k.parentNode !== this) k.remove();
    else if (k.parentNode === this) {
      const at = this.children.indexOf(k);
      if (at >= 0) this.children.splice(at, 1);
    }
    k.parentNode = this;
    return k;
  }
  append(...kids) {
    for (const k of this._flat(kids)) this.children.push(this._adopt(k));
  }
  replaceChildren(...kids) {           // real DOM: drop everything (own text
    for (const k of this.children.splice(0)) k.parentNode = null;  // included),
    this._text = "";                                               // then append
    this.append(...kids);
  }
  insertBefore(node, ref) {
    const at = this.children.indexOf(ref);
    const kids = this._flat([node]).map(k => this._adopt(k));
    this.children.splice(at < 0 ? this.children.length : at, 0, ...kids);
  }
  remove() {
    if (!this.parentNode) return;
    const a = this.parentNode.children;
    a.splice(a.indexOf(this), 1);
    this.parentNode = null;
  }
  // A REAL parse of the markup, not a stand-in for it: the renderer nests a
  // block's header inside its chips inside its summary, and a click handler
  // reads that structure back (`.bhead`, `.bbody`, `body.childElementCount`) —
  // a shim that only sniffed the outer tag left every one of those checks
  // seeing an empty shell, which is exactly the shape "nothing expands" bug
  // took (issue: a real click reaching a handler that then bails on a body
  // the shim never actually built).
  set innerHTML(html) {
    this.children = [];
    parseHtmlInto(this, String(html));
  }
  get innerHTML() { return ""; }
  // sibling walks — the click-to-view panel is tied to its host row by ADJACENCY
  // alone (toggleView inserts it "afterend"), so a test of that invariant needs them
  get nextElementSibling() {
    const a = this.parentNode ? this.parentNode._elementChildren() : [];
    return a[a.indexOf(this) + 1] || null;
  }
  get previousElementSibling() {
    const a = this.parentNode ? this.parentNode._elementChildren() : [];
    const i = a.indexOf(this);
    return i > 0 ? a[i - 1] : null;
  }
  // ELEMENT views of `children`: real DOM excludes text nodes from all three,
  // and innerHTML now parses real text runs (a block's summary, a message's
  // words), so a count or a walk that did not skip them would see phantom
  // children a bare-markup shim never produced.
  _elementChildren() { return this.children.filter(c => c.tag !== "#text"); }
  get firstElementChild() { return this._elementChildren()[0] || null; }
  get lastElementChild() {
    const kids = this._elementChildren();
    return kids[kids.length - 1] || null;
  }
  get childElementCount() { return this._elementChildren().length; }
  // Enough of insertAdjacentHTML for the block filler: the served HTML is opaque
  // to these tests (they measure counts, classes and data-*), so it is kept as
  // one text-bearing child rather than parsed.
  //
  // The POSITION is honoured, because one caller's whole meaning is its position:
  // the feed is newest-TOP, so appendItems prepends with "afterbegin" and then
  // reads `firstElementChild` back to stamp it — a shim that appended handed it
  // the OLDEST row every time, which is exactly the ordering a harness measuring
  // the feed has to be able to see.
  insertAdjacentHTML(pos, html) {
    const n = new El("#html", "", html);
    n.parentNode = this;
    if (pos === "afterbegin") this.children.unshift(n);
    else this.children.push(n);
  }
  prepend(...kids) {
    const kids2 = this._flat(kids).map(k => this._adopt(k));
    this.children.unshift(...kids2);
  }
  _all(out) { for (const c of this.children) { out.push(c); c._all(out); } return out; }
  querySelectorAll(sel) {              // only ".cls" / ".cls[data-x]" / "[data-x]"
    const cls = sel.split("[")[0].replace(".", "");
    // `data-copy-block` names the dataset key `copyBlock` (the DOM's own
    // hyphen-to-camelCase rule) — a selector that skipped this dropped the
    // attribute filter for every hyphenated name and matched EVERY node.
    const rawAttr = (/\[data-([a-z-]+)\]/.exec(sel) || [])[1];
    const attr = rawAttr && rawAttr.replace(/-([a-z])/g, (_, c) => c.toUpperCase());
    // an attribute-ONLY selector filters on the attribute alone (the note-dot tint
    // asks for every `[data-agent]` row, block card or loose line alike)
    return this._all([]).filter(n => (!cls || n._cls().includes(cls))
      && (!attr || n.dataset[attr] !== undefined));
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  // Walks SELF then ancestors for a match — only what the sources ask of it: a
  // bare tag name, or a ".cls" class selector. `matches` is the one-node half.
  matches(sel) {
    if (sel.startsWith(".")) return this._cls().includes(sel.slice(1));
    return this.tag === sel;
  }
  closest(sel) {
    for (let node = this; node; node = node.parentNode)
      if (node.matches && node.matches(sel)) return node;
    return null;
  }
}

/* The document + element factories every harness's sandbox starts from. Each
   harness adds the app globals ITS engine calls on top. */
function domGlobals() {
  return {
    document: {
      createElement: t => new El(t),
      createTextNode: s => new El("#text", "", s),
      createDocumentFragment: () => new El("#frag"),
      addEventListener: () => {},
    },
    el: (tag, cls, text) => new El(tag, cls, text),
    tnode: s => new El("#text", "", s),
  };
}

module.exports = { El, domGlobals };
