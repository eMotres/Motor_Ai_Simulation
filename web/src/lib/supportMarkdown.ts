// A small, SAFE markdown reader for the assistant's answers (owner 2026-10-05:
// the widget showed "**Report**" raw).
//
// It returns a tree, never a string of HTML: the widget turns the tree into React
// elements, and React escapes every text node, so a model (or a user pasting into
// it) cannot inject markup - `<script>`, `<img onerror>` and friends are just
// characters.  Links are the only attribute-bearing nodes and go through
// `safeHref`, which admits http(s) and mailto only.
//
// Supported: paragraphs, **bold**, *italic*, `code`, ``` fences, #-headings (shown
// as bold lines), "-" / "*" / "•" bullet lists, "1." numbered lists, [text](url)
// links, and bare http(s) URLs / e-mail addresses.  Underscore emphasis is NOT
// supported on purpose: engineering text is full of T_j and ψ_PM.
//
// No imports (a node test loads this file itself).

export type Inline =
  | { t: 'text'; v: string }
  | { t: 'strong'; c: Inline[] }
  | { t: 'em'; c: Inline[] }
  | { t: 'code'; v: string }
  | { t: 'link'; href: string; c: Inline[] }
  | { t: 'br' };

export type Block =
  | { t: 'p'; c: Inline[] }
  | { t: 'h'; c: Inline[] }
  | { t: 'ul'; items: Inline[][] }
  | { t: 'ol'; start: number; items: Inline[][] }
  | { t: 'pre'; v: string };

/** http(s) and mailto only; anything else (javascript:, data:, vbscript:, a
 *  protocol-relative //host, a relative path) is not a link. */
export function safeHref(raw: string): string | null {
  // eslint-disable-next-line no-control-regex
  const u = String(raw ?? '').replace(/[\u0000- \u007f-\u009f]+/g, '');
  if (!u || u.length > 2000) return null;
  if (/^https?:\/\/[^/?#\s]+/i.test(u)) return u;
  if (/^mailto:[^\s@]+@[^\s@]+\.[A-Za-z]{2,}$/i.test(u)) return u;
  return null;
}

const MAX_DEPTH = 4;
// order matters: the earliest match in the text wins; at one position the first alternative wins
const INLINE_RE = new RegExp([
  '`([^`\\n]+)`',                                                       // 1 code
  '\\*\\*(?=\\S)([\\s\\S]*?\\S)\\*\\*',                                 // 2 strong
  '(?<![\\w*])\\*(?=[^\\s*])([^*\\n]*?[^\\s*])\\*(?![\\w*])',           // 3 em
  '\\[([^\\]\\n]+)\\]\\(([^)\\s]+)\\)',                                 // 4 text, 5 url
  '(https?:\\/\\/[^\\s<>()\\[\\]]+[^\\s<>()\\[\\].,;:!?\'"])',          // 6 bare url
  '([A-Za-z0-9._%+\\-]+@[A-Za-z0-9](?:[A-Za-z0-9.\\-]*[A-Za-z0-9])?\\.[A-Za-z]{2,})', // 7 e-mail
].join('|'), 'g');

export function parseInline(src: string, depth = 0): Inline[] {
  const out: Inline[] = [];
  let pos = 0;
  const text = (v: string) => { if (v) out.push({ t: 'text', v }); };
  const re = new RegExp(INLINE_RE.source, 'g');
  let m: RegExpExecArray | null;
  while ((m = re.exec(src))) {
    text(src.slice(pos, m.index));
    pos = m.index + m[0].length;
    if (m[1] !== undefined) out.push({ t: 'code', v: m[1] });
    else if (m[2] !== undefined) out.push({ t: 'strong', c: depth < MAX_DEPTH ? parseInline(m[2], depth + 1) : [{ t: 'text', v: m[2] }] });
    else if (m[3] !== undefined) out.push({ t: 'em', c: depth < MAX_DEPTH ? parseInline(m[3], depth + 1) : [{ t: 'text', v: m[3] }] });
    else if (m[4] !== undefined) {
      const href = safeHref(m[5]);
      if (href) out.push({ t: 'link', href, c: depth < MAX_DEPTH ? parseInline(m[4], depth + 1) : [{ t: 'text', v: m[4] }] });
      else text(m[0]);                       // an unsafe target stays visible as plain text
    } else if (m[6] !== undefined) {
      const href = safeHref(m[6]);
      if (href) out.push({ t: 'link', href, c: [{ t: 'text', v: m[6] }] }); else text(m[0]);
    } else if (m[7] !== undefined) {
      out.push({ t: 'link', href: `mailto:${m[7]}`, c: [{ t: 'text', v: m[7] }] });
    }
  }
  text(src.slice(pos));
  return out;
}

const BULLET = /^\s*(?:[-*•])\s+(.*)$/;
const ORDERED = /^\s*(\d{1,3})[.)]\s+(.*)$/;
const HEADING = /^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$/;
const FENCE = /^\s*```/;

export function parseMarkdown(src: string): Block[] {
  const lines = String(src ?? '').replace(/\r\n?/g, '\n').split('\n');
  const blocks: Block[] = [];
  let para: string[] = [];
  const flush = () => {
    if (!para.length) return;
    const c: Inline[] = [];
    para.forEach((ln, i) => { if (i) c.push({ t: 'br' }); c.push(...parseInline(ln)); });
    blocks.push({ t: 'p', c });
    para = [];
  };
  for (let i = 0; i < lines.length; i++) {
    const ln = lines[i];
    if (FENCE.test(ln)) {
      flush();
      const body: string[] = [];
      i++;
      while (i < lines.length && !FENCE.test(lines[i])) body.push(lines[i++]);
      blocks.push({ t: 'pre', v: body.join('\n') });
      continue;
    }
    if (!ln.trim()) { flush(); continue; }
    const h = HEADING.exec(ln);
    if (h) { flush(); blocks.push({ t: 'h', c: parseInline(h[1]) }); continue; }
    const ob = ORDERED.exec(ln);
    const ub = ob ? null : BULLET.exec(ln);
    if (ob || ub) {
      flush();
      const ordered = !!ob;
      const items: Inline[][] = [];
      const start = ob ? Number(ob[1]) : 1;
      // collect the consecutive items of this list; an indented non-item line continues the item above
      let j = i;
      for (; j < lines.length; j++) {
        const l = lines[j];
        const o2 = ORDERED.exec(l);
        const u2 = o2 ? null : BULLET.exec(l);
        if (ordered ? o2 : u2) items.push(parseInline(((ordered ? o2![2] : u2![1]) ?? '').trim()));
        else if (o2 || u2) {                           // a different list kind starts: end this one
          break;
        } else if (/^\s{2,}\S/.test(l) && items.length) {
          items[items.length - 1].push({ t: 'text', v: ' ' }, ...parseInline(l.trim()));
        } else break;
      }
      blocks.push(ordered ? { t: 'ol', start, items } : { t: 'ul', items });
      i = j - 1;
      continue;
    }
    para.push(ln);
  }
  flush();
  return blocks;
}

/** The plain text of a tree (tests, and a screen-reader fallback). */
export function inlineText(c: Inline[]): string {
  return c.map((n) => (n.t === 'text' || n.t === 'code' ? n.v
    : n.t === 'br' ? '\n' : inlineText(n.c))).join('');
}
