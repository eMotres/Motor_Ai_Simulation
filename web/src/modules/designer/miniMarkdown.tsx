/**
 * A small, safe markdown renderer for the design assistant's answers: paragraphs, headings, bullet / numbered
 * lists, tables, **bold**, *italic* and `code`.  It builds React elements (never innerHTML), so nothing the model
 * writes can inject markup.
 */
import React from 'react';
import { Box, Typography } from '@mui/material';

function inline(text: string, key: string): React.ReactNode[] {
  const out: React.ReactNode[] = [];
  const re = /(\*\*[^*]+\*\*|`[^`]+`|\*[^*\s][^*]*\*)/g;
  let last = 0; let m: RegExpExecArray | null; let i = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const t = m[0];
    if (t.startsWith('**')) out.push(<strong key={`${key}-${i}`}>{t.slice(2, -2)}</strong>);
    else if (t.startsWith('`')) out.push(<code key={`${key}-${i}`} style={{ fontSize: '0.92em' }}>{t.slice(1, -1)}</code>);
    else out.push(<em key={`${key}-${i}`}>{t.slice(1, -1)}</em>);
    last = m.index + t.length; i += 1;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

const cells = (line: string): string[] => line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((c) => c.trim());
const isRule = (line: string): boolean => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(line);

export function MiniMarkdown({ text }: { text: string }) {
  const lines = (text || '').replace(/\r/g, '').split('\n');
  const blocks: React.ReactNode[] = [];
  let i = 0; let k = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i += 1; continue; }
    const key = `b${k++}`;
    const h = /^(#{1,4})\s+(.*)$/.exec(line);
    if (h) {
      blocks.push(<Typography key={key} sx={{ fontWeight: 700, fontSize: h[1].length <= 2 ? 15 : 13.5, mt: 1, mb: 0.5 }}>
        {inline(h[2], key)}</Typography>);
      i += 1; continue;
    }
    if (line.trim().startsWith('|') && i + 1 < lines.length && isRule(lines[i + 1])) {
      const head = cells(line); i += 2;
      const rows: string[][] = [];
      while (i < lines.length && lines[i].trim().startsWith('|')) { rows.push(cells(lines[i])); i += 1; }
      blocks.push(
        <Box key={key} sx={{ overflowX: 'auto', my: 1 }}>
          <table style={{ borderCollapse: 'collapse', fontSize: 12.5 }}>
            <thead><tr>{head.map((c, j) => <th key={j} style={{ textAlign: 'left', padding: '3px 8px',
              borderBottom: '1px solid var(--app-border, #475569)', whiteSpace: 'nowrap' }}>{inline(c, `${key}h${j}`)}</th>)}</tr></thead>
            <tbody>{rows.map((r, ri) => <tr key={ri}>{r.map((c, j) => <td key={j} style={{ padding: '3px 8px',
              borderBottom: '1px solid rgba(148,163,184,0.2)', whiteSpace: 'nowrap' }}>{inline(c, `${key}r${ri}c${j}`)}</td>)}</tr>)}</tbody>
          </table>
        </Box>);
      continue;
    }
    const bullet = /^\s*([-*•]|\d+[.)])\s+/;
    if (bullet.test(line)) {
      const ordered = /^\s*\d/.test(line);
      const items: string[] = [];
      while (i < lines.length && bullet.test(lines[i])) { items.push(lines[i].replace(bullet, '')); i += 1; }
      const L = ordered ? 'ol' : 'ul';
      blocks.push(<L key={key} style={{ margin: '4px 0', paddingLeft: 22 }}>
        {items.map((t, j) => <li key={j} style={{ margin: '2px 0' }}>{inline(t, `${key}i${j}`)}</li>)}</L>);
      continue;
    }
    const para: string[] = [];
    while (i < lines.length && lines[i].trim() && !bullet.test(lines[i]) && !/^#{1,4}\s/.test(lines[i])
           && !(lines[i].trim().startsWith('|') && i + 1 < lines.length && isRule(lines[i + 1]))) {
      para.push(lines[i]); i += 1;
    }
    blocks.push(<Typography key={key} component="p" sx={{ fontSize: 13.5, my: 0.75, lineHeight: 1.55 }}>
      {para.flatMap((p, j) => (j ? [<br key={`${key}br${j}`} />, ...inline(p, `${key}p${j}`)] : inline(p, `${key}p${j}`)))}
    </Typography>);
  }
  return <>{blocks}</>;
}
