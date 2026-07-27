// tests/jsdom/domshim.js — the tiny DOM stand-in the node-driven harnesses run
// the REAL dashboard sources against (tests/jsdom/*.js; see docs/testing.md).
//
// Extracted when the second harness arrived: it is 90 lines of element/class/
// dataset/selector emulation, and a copy per harness is the duplication these
// harnesses exist to catch elsewhere. Deliberately dumb — it shims only what
// the page sources actually touch, and asserts nothing itself.
"use strict";

/* ---------- the DOM shim: elements, classes, dataset, class-selector queries */
class El {
  constructor(tag, cls, text) {
    this.tag = tag; this.className = cls || ""; this.dataset = {};
    this.children = []; this.parentNode = null; this._text = text || "";
    this.onclick = null;
    // enough of the event surface for the harnesses that DRIVE a form: the
    // sources attach a few listeners (blur, input) and never dispatch them
    this._on = {};
    this.style = {};
    this.addEventListener = (t, f) => { (this._on[t] = this._on[t] || []).push(f); };
    this.removeEventListener = () => {};
    this.dispatch = (t) => { for (const f of this._on[t] || []) f({}); };
    this.focus = () => {};
    this.blur = () => {};
    const self = this;
    this.classList = {
      add(c) { if (!self._cls().includes(c)) self.className = (self.className + " " + c).trim(); },
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
  // Enough of innerHTML for appendItems/appendOlder, which set it on a scratch
  // div and take firstElementChild: one child element carrying the outermost
  // tag's class. The served HTML is otherwise opaque to these tests.
  set innerHTML(html) {
    const m = /^\s*<(\w+)[^>]*?(?:\sclass="([^"]*)")?[^>]*>/.exec(String(html));
    this.children = [];
    if (m) this.append(new El(m[1], m[2] || ""));
  }
  get innerHTML() { return ""; }
  get firstElementChild() { return this.children[0] || null; }
  get lastElementChild() { return this.children[this.children.length - 1] || null; }
  get childElementCount() { return this.children.length; }
  // Enough of insertAdjacentHTML for the block filler: the served HTML is opaque
  // to these tests (they measure counts, classes and data-*), so it is kept as
  // one text-bearing child rather than parsed.
  insertAdjacentHTML(_pos, html) {
    const n = new El("#html", "", html);
    n.parentNode = this;
    this.children.push(n);
  }
  _all(out) { for (const c of this.children) { out.push(c); c._all(out); } return out; }
  querySelectorAll(sel) {              // only ".cls" / ".cls[data-x]" / "[data-x]"
    const cls = sel.split("[")[0].replace(".", "");
    const attr = (/\[data-([a-z]+)\]/.exec(sel) || [])[1];
    // an attribute-ONLY selector filters on the attribute alone (the note-dot tint
    // asks for every `[data-agent]` row, block card or loose line alike)
    return this._all([]).filter(n => (!cls || n._cls().includes(cls))
      && (!attr || n.dataset[attr] !== undefined));
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
}

/* The document + element factories every harness's sandbox starts from. Each
   harness adds the app globals ITS engine calls on top. */
function domGlobals() {
  return {
    document: {
      createElement: t => new El(t),
      createTextNode: s => new El("#text", "", s),
      createDocumentFragment: () => new El("#frag"),
    },
    el: (tag, cls, text) => new El(tag, cls, text),
    tnode: s => new El("#text", "", s),
  };
}

module.exports = { El, domGlobals };
