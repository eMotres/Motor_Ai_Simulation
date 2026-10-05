/**
 * SupportMarkdown — renders the assistant's markdown as React elements.
 *
 * The parser (lib/supportMarkdown.ts) returns a tree; this turns it into
 * elements. There is NO dangerouslySetInnerHTML anywhere: every text node goes
 * through React's escaping, so raw HTML from the model is shown as characters,
 * and the only attribute that carries model text is a link's href, which the
 * parser has already restricted to http(s) / mailto.
 */
import React from 'react';
import { Box } from '@mui/material';
import { parseMarkdown, type Inline, type Block } from '../../lib/supportMarkdown';

const inline = (nodes: Inline[], keyPrefix = ''): React.ReactNode[] =>
  nodes.map((n, i) => {
    const key = `${keyPrefix}${i}`;
    switch (n.t) {
      case 'text': return <React.Fragment key={key}>{n.v}</React.Fragment>;
      case 'br': return <br key={key} />;
      case 'strong': return <strong key={key}>{inline(n.c, `${key}.`)}</strong>;
      case 'em': return <em key={key}>{inline(n.c, `${key}.`)}</em>;
      case 'code':
        return (
          <Box component="code" key={key}
            sx={{ fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', fontSize: '0.92em',
                  bgcolor: 'var(--panel)', px: 0.5, borderRadius: 0.5 }}>{n.v}</Box>
        );
      case 'link':
        return (
          <Box component="a" key={key} href={n.href} target="_blank" rel="noopener noreferrer"
            sx={{ color: '#60a5fa', textDecoration: 'underline', wordBreak: 'break-word' }}>
            {inline(n.c, `${key}.`)}
          </Box>
        );
    }
  });

const block = (b: Block, i: number): React.ReactNode => {
  switch (b.t) {
    case 'p':
      return <Box key={i} sx={{ m: 0 }}>{inline(b.c)}</Box>;
    case 'h':
      return <Box key={i} sx={{ m: 0, fontWeight: 700 }}>{inline(b.c)}</Box>;
    case 'pre':
      return (
        <Box key={i} component="pre"
          sx={{ m: 0, p: 1, overflowX: 'auto', whiteSpace: 'pre-wrap', bgcolor: 'var(--panel)', borderRadius: 1,
                fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', fontSize: 13 }}>{b.v}</Box>
      );
    case 'ul':
      return (
        <Box key={i} component="ul" sx={{ m: 0, pl: 2.5 }}>
          {b.items.map((it, j) => <li key={j}>{inline(it)}</li>)}
        </Box>
      );
    case 'ol':
      return (
        <Box key={i} component="ol" start={b.start} sx={{ m: 0, pl: 3 }}>
          {b.items.map((it, j) => <li key={j}>{inline(it)}</li>)}
        </Box>
      );
  }
};

const SupportMarkdown: React.FC<{ text: string }> = ({ text }) => (
  <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.75, wordBreak: 'break-word' }}>
    {parseMarkdown(text).map(block)}
  </Box>
);

export default SupportMarkdown;
