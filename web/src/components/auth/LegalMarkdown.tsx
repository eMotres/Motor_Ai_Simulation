// The Markdown of a legal text as React elements (lib/legalLogic.ts parseLegalMarkdown).  Never innerHTML: every text node goes through
// React's escaping and a link keeps only an http(s) / mailto / same-site href.  A table scrolls INSIDE its own box so the page itself
// never scrolls sideways on a phone; long words and addresses wrap.
import React from 'react';
import { Box, Typography } from '@mui/material';
import { parseLegalMarkdown, displayText, type Block, type Inline } from '../../lib/legalLogic';

const inline = (nodes: Inline[], k = ''): React.ReactNode[] => nodes.map((n, i) => {
  const key = `${k}${i}`;
  if (n.t === 'text') return <React.Fragment key={key}>{n.v}</React.Fragment>;
  if (n.t === 'strong') return <strong key={key}>{inline(n.c, `${key}.`)}</strong>;
  if (n.t === 'em') return <em key={key}>{inline(n.c, `${key}.`)}</em>;
  const external = /^https?:/i.test(n.href);
  return (
    <Box component="a" key={key} href={n.href} {...(external ? { target: '_blank', rel: 'noopener noreferrer' } : {})}
      sx={{ color: 'primary.main', textDecoration: 'underline' }}>{inline(n.c, `${key}.`)}</Box>
  );
});

const bodySx = { fontSize: { xs: '0.9rem', sm: '0.94rem' }, color: 'var(--text-2)', lineHeight: 1.65, overflowWrap: 'anywhere', m: 0, mb: 1.25 } as const;

const renderBlock = (b: Block, i: number): React.ReactNode => {
  switch (b.t) {
    case 'h':
      return (
        <Typography key={i} component={b.level === 1 ? 'h1' : b.level === 2 ? 'h2' : 'h3'} id={b.id}
          sx={{ scrollMarginTop: 12, fontWeight: b.level === 1 ? 800 : 700, color: 'var(--text)', overflowWrap: 'anywhere',
            fontSize: b.level === 1 ? { xs: '1.5rem', sm: '1.9rem' } : b.level === 2 ? { xs: '1.05rem', sm: '1.15rem' } : '1rem',
            mt: b.level === 1 ? 0 : 3, mb: 1 }}>
          {inline(b.c)}
        </Typography>
      );
    case 'ul':
      return (
        <Box key={i} component="ul" sx={{ ...bodySx, pl: 3 }}>
          {b.items.map((it, j) => <li key={j}>{inline(it)}</li>)}
        </Box>
      );
    case 'table':
      return (
        <Box key={i} tabIndex={0} role="region" sx={{ overflowX: 'auto', maxWidth: '100%', mb: 1.5, border: '1px solid var(--line-soft)', borderRadius: 1 }}>
          <Box component="table" sx={{ borderCollapse: 'collapse', minWidth: 640, width: '100%', fontSize: '0.84rem', color: 'var(--text-2)' }}>
            <thead>
              <tr>{b.head.map((c, j) => (
                <Box component="th" key={j} sx={{ textAlign: 'left', p: 1, borderBottom: '1px solid var(--line-soft)', bgcolor: 'var(--panel-2)', color: 'var(--text)' }}>{inline(c)}</Box>
              ))}</tr>
            </thead>
            <tbody>
              {b.rows.map((r, j) => (
                <tr key={j}>{r.map((c, l) => (
                  <Box component="td" key={l} sx={{ p: 1, verticalAlign: 'top', borderBottom: '1px solid var(--line-soft)' }}>{inline(c)}</Box>
                ))}</tr>
              ))}
            </tbody>
          </Box>
        </Box>
      );
    default:
      return <Box key={i} component="p" sx={bodySx}>{inline(b.c)}</Box>;
  }
};

const LegalMarkdown: React.FC<{ text: string }> = ({ text }) => (
  <Box data-testid="legal-text">{parseLegalMarkdown(displayText(text)).map(renderBlock)}</Box>
);

export default LegalMarkdown;
