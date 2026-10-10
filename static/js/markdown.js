// Markdown minimal et sûr pour les notes et les réponses de Claude : titres, listes, citations, code,
// tableaux, gras, italique, liens.
// Les formules ($…$, $$…$$, \(…\), \[…\]) sont laissées telles quelles pour MathJax.
// Tout le texte est échappé : seules les balises produites ici peuvent apparaître.

const MATH = /(\$\$[\s\S]+?\$\$|\\\[[\s\S]+?\\\]|\$[^$\n]+?\$|\\\([\s\S]+?\\\))/g;

function escape(text) {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function inline(text, math, refs) {
  let html = escape(text);
  html = html.replace(/`([^`]+)`/g, (_, code) => `<code>${code}</code>`);
  html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/(^|[^\w*])\*([^*\s][^*]*?)\*(?!\w)/g, "$1<em>$2</em>");
  html = html.replace(/(^|[^\w])_([^_\s][^_]*?)_(?!\w)/g, "$1<em>$2</em>");
  html = html.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  if (refs) html = linkOutside(html, refs);
  return html.replace(/\u0000(\d+)\u0000/g, (_, i) => escape(math[Number(i)]));
}

// Renvois (« Lemma 2.3 », « (2.11) ») transformés en liens, hors balises, code et liens existants
function linkOutside(html, refs) {
  let depth = 0;
  return html.split(/(<[^>]+>)/).map((part) => {
    if (part.startsWith("<")) {
      if (/^<(code|a)\b/.test(part)) depth++;
      else if (/^<\/(code|a)>/.test(part)) depth--;
      return part;
    }
    return depth ? part : refs(part);
  }).join("");
}

const cells = (line) => line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());

export function markdownToHtml(source, { refs } = {}) {
  const math = [];
  const text = String(source || "").replace(MATH, (m) => `\u0000${math.push(m) - 1}\u0000`);
  const lines = text.split("\n");
  const out = [];
  let paragraph = [];
  let list = null;
  const flushParagraph = () => {
    if (paragraph.length) out.push(`<p>${inline(paragraph.join(" "), math, refs)}</p>`);
    paragraph = [];
  };
  const flushList = () => {
    const start = list?.start > 1 ? ` start="${list.start}"` : "";
    if (list) out.push(`<${list.tag}${start}>${list.items.map((i) => `<li>${inline(i, math, refs)}</li>`).join("")}</${list.tag}>`);
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
    if (/^\s*\|.*\|\s*$/.test(line) && /^\s*\|?[\s:|-]+\|?\s*$/.test(lines[i + 1] || "") && lines[i + 1].includes("-")) {
      flushParagraph(); flushList();
      const head = cells(line);
      const rows = [];
      i++;
      while (i + 1 < lines.length && /^\s*\|.*\|\s*$/.test(lines[i + 1])) rows.push(cells(lines[++i]));
      out.push(`<div class="md-table"><table><thead><tr>${head.map((c) => `<th>${inline(c, math, refs)}</th>`).join("")}</tr></thead>`
        + `<tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${inline(c, math, refs)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`);
      continue;
    }
    if (/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) {
      flushParagraph(); flushList();
      out.push("<hr>");
      continue;
    }
    const heading = line.match(/^(#{1,4})\s+(.*)$/);
    const bullet = line.match(/^\s*[-*]\s+(.*)$/);
    const numbered = line.match(/^\s*(\d+)[.)]\s+(.*)$/);
    const quote = line.match(/^>\s?(.*)$/);
    if (heading) {
      flushParagraph(); flushList();
      const level = Math.min(6, heading[1].length + 3);
      out.push(`<h${level}>${inline(heading[2], math, refs)}</h${level}>`);
    } else if (bullet || numbered) {
      flushParagraph();
      const tag = bullet ? "ul" : "ol";
      if (list && list.tag !== tag) flushList();
      list ||= { tag, items: [], start: numbered ? Number(numbered[1]) : 1 };
      list.items.push(bullet ? bullet[1] : numbered[2]);
    } else if (quote) {
      flushParagraph(); flushList();
      out.push(`<blockquote>${inline(quote[1], math, refs)}</blockquote>`);
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
