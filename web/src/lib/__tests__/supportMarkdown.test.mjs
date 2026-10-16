// node --test — the help assistant's markdown reader (lib/supportMarkdown.ts).
// Imports the module itself (no runtime imports); needs a Node that strips
// TypeScript types (>= 22.18 / 23.6 / 24), else the suite is skipped.
//
// What it pins (owner 2026-10-05: the widget showed "**Report**" raw):
//   * bold, italic, code, lists, links and paragraphs become a TREE (never HTML);
//   * raw HTML from the model stays inert text - the tree has no node that could carry markup;
//   * a link is only ever http(s) or mailto - javascript:, data:, vbscript:, relative and
//     protocol-relative targets are not links, however they are spelled;
//   * engineering text survives: T_j, psi_PM, "5 * 3", units.
import test from 'node:test';
import assert from 'node:assert/strict';

let M = null;
try { M = await import('../supportMarkdown.ts'); } catch { /* old Node */ }
const t = M ? test : test.skip;

const walk = (nodes, f) => {
  for (const n of nodes) {
    f(n);
    if (n.c) walk(n.c, f);
    if (n.items) n.items.forEach((it) => walk(it, f));
  }
};
const allInline = (blocks) => {
  const out = [];
  walk(blocks, (n) => out.push(n));
  return out;
};

t('bold and a one-character bold, as the greeting used to print raw', () => {
  const b = M.parseMarkdown('Press **Send** or **▶** now');
  const strong = allInline(b).filter((n) => n.t === 'strong');
  assert.deepEqual(strong.map((n) => M.inlineText(n.c)), ['Send', '▶']);
  assert.ok(!JSON.stringify(b).includes('**'), 'no raw asterisks are left');
});

t('numbered and bulleted lists, with a continuation line', () => {
  const b = M.parseMarkdown('Steps:\n\n1. Open **Motors**.\n2. Click **▶**.\n\n- one\n- two\n  more\n* three');
  assert.equal(b[0].t, 'p');
  assert.equal(b[1].t, 'ol');
  assert.equal(b[1].items.length, 2);
  assert.equal(b[2].t, 'ul');
  assert.equal(b[2].items.length, 3);
  assert.equal(M.inlineText(b[2].items[1]), 'two more');
});

t('italic, inline code, headings and fences', () => {
  const b = M.parseMarkdown('## Title\n\nuse *this* and `that`\n\n```\n**not bold**\n```');
  assert.equal(b[0].t, 'h');
  const kinds = allInline(b).map((n) => n.t);
  assert.ok(kinds.includes('em') && kinds.includes('code'));
  const pre = b.find((x) => x.t === 'pre');
  assert.equal(pre.v, '**not bold**', 'a fenced block is literal');
});

t('engineering text is not mangled', () => {
  const src = 'T_j and ψ_PM stay; 5 * 3 = 15; 12 N·m at 8000 rpm; I_rms*2';
  const b = M.parseMarkdown(src);
  assert.equal(M.inlineText(b[0].c), src);
  assert.ok(!allInline(b).some((n) => n.t === 'em' || n.t === 'strong'));
});

t('raw HTML from the model is inert text', () => {
  const evil = '<script>alert(1)</script> <img src=x onerror=alert(1)> <b>x</b> &lt;';
  const b = M.parseMarkdown(evil);
  assert.equal(b.length, 1);
  assert.deepEqual(b[0].c, [{ t: 'text', v: evil }], 'one literal text node, no element');
});

t('only http(s) and mailto are links', () => {
  const good = ['https://example.com/a?b=1', 'http://example.com', 'mailto:vadim@motresres.com', 'HTTPS://EXAMPLE.COM'];
  for (const u of good) assert.equal(M.safeHref(u), u, u);
  const bad = ['javascript:alert(1)', 'JaVaScRiPt:alert(1)', ' javascript:alert(1)', 'java\nscript:alert(1)', 'java\tscript:alert(1)',
    'data:text/html,<script>', 'vbscript:x', '//evil.example.com', '/relative/path', 'ftp://example.com', 'file:///etc/passwd',
    'https://', 'mailto:', '', 'x'.repeat(3000)];
  for (const u of bad) assert.equal(M.safeHref(u), null, JSON.stringify(u).slice(0, 40));
});

t('a markdown link to an unsafe target is shown as text, never as a link', () => {
  for (const u of ['javascript:alert(1)', 'data:text/html,x', 'JAVASCRIPT:alert(1)']) {
    const b = M.parseMarkdown(`[click](${u}) here`);
    assert.ok(!allInline(b).some((n) => n.t === 'link'), u);
  }
  const ok = M.parseMarkdown('[docs](https://example.com/x) and mail vadim@motresres.com and https://example.com/y.');
  const links = allInline(ok).filter((n) => n.t === 'link');
  assert.deepEqual(links.map((n) => n.href), ['https://example.com/x', 'mailto:vadim@motresres.com', 'https://example.com/y']);
});

t('every link in any output passes safeHref (a fuzz of hostile strings)', () => {
  const pieces = ['[a](', 'javascript:', 'https://x.io', ')', '](', '<a href="', '">', '**', '*', '`', '\n', ' ', 'mailto:a@b.co', 'onerror=', '<img>'];
  let seed = 7;
  const rnd = () => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff; };
  for (let k = 0; k < 300; k++) {
    let s = '';
    for (let j = 0; j < 12; j++) s += pieces[Math.floor(rnd() * pieces.length)];
    const b = M.parseMarkdown(s);
    for (const n of allInline(b)) if (n.t === 'link') assert.notEqual(M.safeHref(n.href), null, s);
  }
});

t('the real answers of the live check parse without losing words', () => {
  const answer = 'To load a motor:\n\n1. In the **Motors** tab, expand the stator diameter (**Ø**) section.\n2. Click the green **▶** button.\n\nThe machine opens in **Configure**.';
  const b = M.parseMarkdown(answer);
  const text = b.map((x) => (x.items ? x.items.map(M.inlineText).join(' ') : M.inlineText(x.c ?? []))).join(' ');
  for (const w of ['Motors', 'Ø', '▶', 'Configure', 'stator diameter']) assert.ok(text.includes(w), w);
});
