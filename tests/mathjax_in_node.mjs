// Le MathJax livré (static/vendor/mathjax), avec les options de static/js/math.js, exécuté dans Node sur un DOM
// minimal (tests/test_mathjax.py). Entrée : liste JSON de formules TeX (argument) ; sortie : leur HTML, en JSON.
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const VENDOR = path.join(ROOT, "static/vendor/mathjax");

// style : comme le navigateur, cssText découpé en déclarations (un style posé par setAttribute garde tout)
class Style {
  constructor() { this.props = {}; }
  get cssText() { return Object.entries(this.props).map(([k, v]) => `${k}: ${v};`).join(" "); }
  set cssText(text) {
    this.props = {};
    for (const part of String(text).split(";")) {
      const i = part.indexOf(":");
      if (i > 0) this.props[part.slice(0, i).trim()] = part.slice(i + 1).trim();
    }
  }
}
const cssName = (key) => key.replace(/[A-Z]/g, (m) => "-" + m.toLowerCase());
const styleProxy = () => new Proxy(new Style(), {
  get(target, key) {
    if (key in target) return target[key];
    return typeof key === "string" ? target.props[cssName(key)] || "" : undefined;
  },
  set(target, key, value) {
    if (key === "cssText" || key === "props") { target[key] = value; return true; }
    // propriété posée une à une (setStyle) : le CSSOM refuse une valeur qui contient ; { }
    if (value === "" || value == null) delete target.props[cssName(key)];
    else if (!/[;{}]/.test(String(value))) target.props[cssName(key)] = String(value);
    return true;
  },
});

class Node_ {
  constructor() { this.childNodes = []; this.parentNode = null; }
  get firstChild() { return this.childNodes[0] || null; }
  get lastChild() { return this.childNodes[this.childNodes.length - 1] || null; }
  get nextSibling() { const p = this.parentNode; return p ? p.childNodes[p.childNodes.indexOf(this) + 1] || null : null; }
  get previousSibling() { const p = this.parentNode; return p ? p.childNodes[p.childNodes.indexOf(this) - 1] || null : null; }
  appendChild(child) {
    if (child.nodeType === 11) { for (const c of [...child.childNodes]) this.appendChild(c); return child; }
    child.parentNode?.removeChild(child);
    child.parentNode = this;
    this.childNodes.push(child);
    if (child.localName === "script") loadScript(child);
    return child;
  }
  insertBefore(child, ref) {
    if (!ref) return this.appendChild(child);
    child.parentNode?.removeChild(child);
    child.parentNode = this;
    this.childNodes.splice(this.childNodes.indexOf(ref), 0, child);
    return child;
  }
  removeChild(child) {
    const i = this.childNodes.indexOf(child);
    if (i >= 0) this.childNodes.splice(i, 1);
    child.parentNode = null;
    return child;
  }
  replaceChild(next, old) { this.insertBefore(next, old); return this.removeChild(old); }
  remove() { this.parentNode?.removeChild(this); }
  append(...children) { for (const c of children) this.appendChild(typeof c === "string" ? new Text_(c) : c); }
}
class Text_ extends Node_ {
  constructor(text) { super(); this.nodeType = 3; this.nodeName = "#text"; this.nodeValue = text; }
  get textContent() { return this.nodeValue; }
  set textContent(v) { this.nodeValue = v; }
  get data() { return this.nodeValue; }
  cloneNode() { return new Text_(this.nodeValue); }
  get outerHTML() { return this.nodeValue.replace(/&/g, "&amp;").replace(/</g, "&lt;"); }
}
class Fragment_ extends Node_ { constructor() { super(); this.nodeType = 11; this.nodeName = "#document-fragment"; } }
class Element_ extends Node_ {
  constructor(tag, ns) {
    super();
    this.nodeType = 1;
    this.localName = tag;
    this.nodeName = this.tagName = ns && !/xhtml/.test(ns) ? tag : tag.toUpperCase();
    this.namespaceURI = ns || "http://www.w3.org/1999/xhtml";
    this.attrs = new Map();
    this.style = styleProxy();
    const self = this;
    this.classList = {
      add: (...names) => { self.className = [...new Set([...self.className.split(/ +/), ...names])].filter(Boolean).join(" "); },
      remove: (...names) => { self.className = self.className.split(/ +/).filter((n) => n && !names.includes(n)).join(" "); },
      contains: (name) => self.className.split(/ +/).includes(name),
    };
  }
  get className() { return this.attrs.get("class") || ""; }
  set className(v) { this.attrs.set("class", v); }
  get id() { return this.attrs.get("id") || ""; }
  setAttribute(k, v) { if (k === "style") this.style.cssText = v; else this.attrs.set(k, String(v)); }
  setAttributeNS(ns, k, v) { this.setAttribute(k, v); }
  getAttribute(k) { return k === "style" ? this.style.cssText || null : this.attrs.has(k) ? this.attrs.get(k) : null; }
  getAttributeNS(ns, k) { return this.getAttribute(k); }
  hasAttribute(k) { return k === "style" ? !!this.style.cssText : this.attrs.has(k); }
  removeAttribute(k) { if (k === "style") this.style.cssText = ""; this.attrs.delete(k); }
  get attributes() {
    const list = [...this.attrs].map(([name, value]) => ({ name, value }));
    if (this.style.cssText) list.push({ name: "style", value: this.style.cssText });
    return list;
  }
  get textContent() { return this.childNodes.map((c) => c.textContent).join(""); }
  set textContent(v) { this.childNodes = []; if (v) this.appendChild(new Text_(v)); }
  cloneNode(deep) {
    const copy = new Element_(this.localName, this.namespaceURI);
    for (const [k, v] of this.attrs) copy.attrs.set(k, v);
    copy.style.cssText = this.style.cssText;
    if (deep) for (const c of this.childNodes) copy.appendChild(c.cloneNode(true));
    return copy;
  }
  getBoundingClientRect() { return { left: 0, right: 0, top: 0, bottom: 0, width: 0, height: 0 }; }
  get offsetWidth() { return 0; }
  get offsetHeight() { return 0; }
  getElementsByTagName() { return []; }
  getElementsByTagNameNS() { return []; }
  getElementsByClassName() { return []; }
  querySelectorAll() { return []; }
  querySelector() { return null; }
  get outerHTML() {
    const attrs = this.attributes.map((a) => ` ${a.name}="${String(a.value).replace(/"/g, "&quot;")}"`).join("");
    return `<${this.localName}${attrs}>${this.innerHTML}</${this.localName}>`;
  }
  get innerHTML() { return this.childNodes.map((c) => c.outerHTML).join(""); }
  set innerHTML(v) { this.childNodes = []; if (v) this.appendChild(new Text_(String(v))); }
  addEventListener() {}
  removeEventListener() {}
  get sheet() { return { cssRules: [], insertRule() {} }; }
}

// extension chargée à la demande (color.js, unicode.js…) : <script src="/static/vendor/mathjax/…"> exécuté sur place
function loadScript(script) {
  const src = script.getAttribute("src") || script.src;
  if (!src) return;
  const file = path.join(VENDOR, src.replace(/^\/static\/vendor\/mathjax\/?/, ""));
  try {
    vm.runInThisContext(fs.readFileSync(file, "utf8"), { filename: file });
    setTimeout(() => script.onload?.(), 0);
  } catch (error) {
    setTimeout(() => script.onerror?.(error), 0);
  }
}

const document = {
  nodeType: 9,
  readyState: "complete",
  createElement: (tag) => new Element_(tag),
  createElementNS: (ns, tag) => new Element_(tag, ns),
  createTextNode: (text) => new Text_(text),
  createDocumentFragment: () => new Fragment_(),
  createComment: () => new Text_(""),
  getElementsByTagName: () => [],
  querySelectorAll: () => [],
  querySelector: () => null,
  getElementById: () => null,
  addEventListener() {},
};
document.documentElement = new Element_("html");
document.head = new Element_("head");
document.body = new Element_("body");
document.documentElement.append(document.head, document.body);
Object.assign(globalThis, {
  window: globalThis, document, Node: Node_, Element: Element_, HTMLElement: Element_, Text: Text_,
  DocumentFragment: Fragment_, HTMLDocument: Object, Document: Object,
  DOMParser: class { parseFromString() { return document; } },
  getComputedStyle: () => ({}), addEventListener() {},
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
});
Object.defineProperty(globalThis, "navigator", { value: { userAgent: "node", vendor: "", platform: "" }, configurable: true });

const { mathJaxOptions } = await import(pathToFileURL(path.join(ROOT, "static/js/math.js")).href);
const MathJax = await new Promise((resolve) => {
  globalThis.MathJax = mathJaxOptions(resolve);
  vm.runInThisContext(fs.readFileSync(path.join(VENDOR, "tex-mml-chtml.js"), "utf8"), { filename: "tex-mml-chtml.js" });
});
const out = [];
for (const tex of JSON.parse(process.argv[2])) {
  out.push((await MathJax.tex2chtmlPromise(tex, { display: false })).outerHTML);
}
console.log(JSON.stringify(out));
process.exit(0);
