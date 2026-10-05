// Split a caption into Greek runs and the rest, so the Greek runs can be protected from
// CSS uppercase (η -> Η looks like the Latin H).  Pure: node-testable.
export interface LabelPart { text: string; greek: boolean }

const GREEK = /[Ͱ-Ͽἀ-῿]+/g;

export function greekParts(text: string): LabelPart[] {
  const out: LabelPart[] = [];
  let last = 0;
  for (const m of text.matchAll(GREEK)) {
    const i = m.index ?? 0;
    if (i > last) out.push({ text: text.slice(last, i), greek: false });
    out.push({ text: m[0], greek: true });
    last = i + m[0].length;
  }
  if (last < text.length) out.push({ text: text.slice(last), greek: false });
  return out;
}
