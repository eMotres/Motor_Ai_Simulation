// A caption that survives CSS `text-transform: uppercase`: Greek letters are NOT uppercased
// (owner 2026-10-05: η turned into Η, which reads as the Latin H).  Anything that is not Greek
// is left to the surrounding style, so the label looks exactly as before.
import React from 'react';
import { greekParts } from '../../lib/greekLabel';

const GreekLabel: React.FC<{ text: string }> = ({ text }) => (
  <>
    {greekParts(text).map((p, i) => (p.greek
      ? <span key={i} style={{ textTransform: 'none' }}>{p.text}</span>
      : <React.Fragment key={i}>{p.text}</React.Fragment>))}
  </>
);

export default GreekLabel;
