// Markdown minimal et sûr pour les notes : titres, listes, citations, code, gras, italique, liens.
// Les formules ($…$, $$…$$, \(…\), \[…\]) sont laissées telles quelles pour MathJax.
// Tout le texte est échappé : seules les balises produites ici peuvent apparaître.

const MATH = /(\$\$[\s\S]+?\$\$|\\\[[\s\S]+?\\\]|\$[^$\n]+?\$|\\\([\s\S]+?\\\))/g;

function escape(text) {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function inline(text, math) {
  let html = escape(text);
  html = html.replace(/`([^`]+)`/g, (_, code) => `<code>${code}</code>`);
  html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/(^|[^\w*])\*([^*\s][^*]*?)\*(?!\w)/g, "$1<em>$2</em>");
  html = html.replace(/(^|[^\w])_([^_\s][^_]*?)_(?!\w)/g, "$1<em>$2</em>");
  html = html.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  return html.replace(/\u0000(\d+)\u0000/g, (_, i) => escape(math[Number(i)]));
}

export function markdownToHtml(source) {
  const math = [];
  const text = String(source || "").replace(MATH, (m) => `\u0000${math.push(m) - 1}\u0000`);
  const lines = text.split("\n");
  const out = [];
  let paragraph = [];
  let list = null;
  const flushParagraph = () => {
    if (paragraph.length) out.push(`<p>${inline(paragraph.join(" "), math)}</p>`);
    paragraph = [];
  };
  const flushList = () => {
    if (list) out.push(`<${list.tag}>${list.items.map((i) => `<li>${inline(i, math)}</li>`).join("")}</${list.tag}>`);
    list = null;
  };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (/^```/.test(line)) {
      flushParagraph(); flushList();
      const code = [];
      while (++i < lines.length && !/^```/.test(lines[i])) code.push(lines[i]);
      out.push(`<pre><code>${escape(code.join("\n").replace(/\u0000(\d+)\u0000/g, (_, k) => math[Number(k)]))}</code></pre>`);
      continue;
    }
    const heading = line.match(/^(#{1,3})\s+(.*)$/);
    const bullet = line.match(/^\s*[-*]\s+(.*)$/);
    const numbered = line.match(/^\s*\d+[.)]\s+(.*)$/);
    const quote = line.match(/^>\s?(.*)$/);
    if (heading) {
      flushParagraph(); flushList();
      out.push(`<h${heading[1].length + 3}>${inline(heading[2], math)}</h${heading[1].length + 3}>`);
    } else if (bullet || numbered) {
      flushParagraph();
      const tag = bullet ? "ul" : "ol";
      if (list && list.tag !== tag) flushList();
      list ||= { tag, items: [] };
      list.items.push((bullet || numbered)[1]);
    } else if (quote) {
      flushParagraph(); flushList();
      out.push(`<blockquote>${inline(quote[1], math)}</blockquote>`);
    } else if (!line.trim()) {
      flushParagraph(); flushList();
    } else if (list && /^\s{2,}/.test(line)) {
      list.items[list.items.length - 1] += ` ${line.trim()}`;
    } else {
      flushList();
      paragraph.push(line.trim());
    }
  }
  flushParagraph(); flushList();
  return out.join("");
}
