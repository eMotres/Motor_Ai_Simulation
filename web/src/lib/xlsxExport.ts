// ─────────────────────────────────────────────────────────────────────────────
// Spreadsheet export without a dependency: a minimal .xlsx writer (a ZIP of
// XML parts, "stored" entries, real numeric cells so Excel / Google Sheets
// treat them as numbers, not text), plus CSV and a tab-separated clipboard
// payload that pastes straight into a Google Sheet.
//
// Why hand-rolled: SheetJS is ~1 MB for what is here one sheet of numbers, and
// a CSV alone is locale-fragile (a Russian Excel reads "1.23" as text and
// wants ";" separators).  The .xlsx carries numbers as numbers, so every
// locale opens it right.
// ─────────────────────────────────────────────────────────────────────────────

export type Cell = number | string | null | undefined;

const enc = new TextEncoder();

// ── CRC-32 (ZIP) ─────────────────────────────────────────────────────────────
const CRC_TABLE = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c >>> 0;
  }
  return t;
})();
const crc32 = (b: Uint8Array): number => {
  let c = 0xffffffff;
  for (let i = 0; i < b.length; i++) c = CRC_TABLE[(c ^ b[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
};

// ── ZIP (stored, no compression) ─────────────────────────────────────────────
const u16 = (v: number) => [v & 0xff, (v >>> 8) & 0xff];
const u32 = (v: number) => [v & 0xff, (v >>> 8) & 0xff, (v >>> 16) & 0xff, (v >>> 24) & 0xff];

function zipStored(files: Array<{ name: string; data: Uint8Array }>): Uint8Array {
  const parts: number[][] = [];
  const central: number[][] = [];
  let offset = 0;
  // DOS time/date of 2000-01-01 00:00 — spreadsheets do not care, ZIP wants a value
  const dosTime = 0, dosDate = ((2000 - 1980) << 9) | (1 << 5) | 1;
  for (const f of files) {
    const name = enc.encode(f.name);
    const crc = crc32(f.data);
    const local = [
      ...u32(0x04034b50), ...u16(20), ...u16(0x0800), ...u16(0),
      ...u16(dosTime), ...u16(dosDate), ...u32(crc),
      ...u32(f.data.length), ...u32(f.data.length), ...u16(name.length), ...u16(0),
      ...name,
    ];
    parts.push(local, Array.from(f.data));
    central.push([
      ...u32(0x02014b50), ...u16(20), ...u16(20), ...u16(0x0800), ...u16(0),
      ...u16(dosTime), ...u16(dosDate), ...u32(crc),
      ...u32(f.data.length), ...u32(f.data.length), ...u16(name.length),
      ...u16(0), ...u16(0), ...u16(0), ...u16(0), ...u32(0), ...u32(offset),
      ...name,
    ]);
    offset += local.length + f.data.length;
  }
  const cdSize = central.reduce((s, c) => s + c.length, 0);
  const eocd = [
    ...u32(0x06054b50), ...u16(0), ...u16(0), ...u16(files.length), ...u16(files.length),
    ...u32(cdSize), ...u32(offset), ...u16(0),
  ];
  const total = parts.reduce((s, p) => s + p.length, 0) + cdSize + eocd.length;
  const out = new Uint8Array(total);
  let p = 0;
  for (const chunk of [...parts, ...central, eocd]) { out.set(chunk, p); p += chunk.length; }
  return out;
}

// ── XLSX ─────────────────────────────────────────────────────────────────────
const xmlEsc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
export const colLetter = (i: number): string => {
  let s = ''; let n = i + 1;
  while (n > 0) { const r = (n - 1) % 26; s = String.fromCharCode(65 + r) + s; n = Math.floor((n - 1) / 26); }
  return s;
};

/** Build a one-sheet .xlsx: `header` as the first row (text), then `rows`
 *  (numbers stay numbers; strings become inline strings; null = empty). */
export function buildXlsx(header: string[], rows: Cell[][], sheetName = 'Sheet1'): Uint8Array {
  const cell = (r: number, c: number, v: Cell): string => {
    const ref = `${colLetter(c)}${r}`;
    if (v == null || v === '') return '';
    if (typeof v === 'number') return Number.isFinite(v) ? `<c r="${ref}"><v>${v}</v></c>` : '';
    return `<c r="${ref}" t="inlineStr"><is><t xml:space="preserve">${xmlEsc(String(v))}</t></is></c>`;
  };
  const rowsXml: string[] = [];
  rowsXml.push(`<row r="1">${header.map((h, c) => cell(1, c, h)).join('')}</row>`);
  rows.forEach((row, i) => rowsXml.push(`<row r="${i + 2}">${row.map((v, c) => cell(i + 2, c, v)).join('')}</row>`));
  // column widths: header length or 10, capped — readable without a manual resize
  const colsXml = header.map((h, c) => `<col min="${c + 1}" max="${c + 1}" width="${Math.min(28, Math.max(9, h.length + 2))}" customWidth="1"/>`).join('');
  const sheet = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>`
    + `<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">`
    + `<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>`
    + `<cols>${colsXml}</cols><sheetData>${rowsXml.join('')}</sheetData></worksheet>`;
  const safeName = xmlEsc(sheetName.replace(/[\\/?*[\]:]/g, ' ').slice(0, 31) || 'Sheet1');
  const workbook = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>`
    + `<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">`
    + `<sheets><sheet name="${safeName}" sheetId="1" r:id="rId1"/></sheets></workbook>`;
  const wbRels = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>`
    + `<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">`
    + `<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>`;
  const rootRels = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>`
    + `<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">`
    + `<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>`;
  const types = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>`
    + `<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">`
    + `<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>`
    + `<Default Extension="xml" ContentType="application/xml"/>`
    + `<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>`
    + `<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>`
    + `</Types>`;
  return zipStored([
    { name: '[Content_Types].xml', data: enc.encode(types) },
    { name: '_rels/.rels', data: enc.encode(rootRels) },
    { name: 'xl/workbook.xml', data: enc.encode(workbook) },
    { name: 'xl/_rels/workbook.xml.rels', data: enc.encode(wbRels) },
    { name: 'xl/worksheets/sheet1.xml', data: enc.encode(sheet) },
  ]);
}

// ── CSV / TSV ────────────────────────────────────────────────────────────────
const csvCell = (v: Cell, sep: string): string => {
  if (v == null) return '';
  const s = typeof v === 'number' ? String(v) : String(v);
  return (s.includes(sep) || s.includes('"') || s.includes('\n')) ? `"${s.replace(/"/g, '""')}"` : s;
};
/** UTF-8 CSV with a BOM (Excel then reads the header's µ/η/° correctly). */
export const toCsv = (header: string[], rows: Cell[][], sep = ','): string =>
  '﻿' + [header, ...rows].map(r => r.map(v => csvCell(v, sep)).join(sep)).join('\r\n');
/** Tab-separated, no BOM — what a paste into Google Sheets / Excel expects. */
export const toTsv = (header: string[], rows: Cell[][]): string =>
  [header, ...rows].map(r => r.map(v => csvCell(v, '\t')).join('\t')).join('\n');

// ── browser helpers ──────────────────────────────────────────────────────────
export function downloadBytes(name: string, bytes: Uint8Array | string, mime: string): void {
  const blob = new Blob([bytes as BlobPart], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = name; a.style.display = 'none';
  document.body.appendChild(a); a.click();
  setTimeout(() => { document.body.removeChild(a); URL.revokeObjectURL(url); }, 1000);
}
export const downloadXlsx = (name: string, header: string[], rows: Cell[][], sheet?: string) =>
  downloadBytes(name.endsWith('.xlsx') ? name : `${name}.xlsx`, buildXlsx(header, rows, sheet),
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');
export const downloadCsv = (name: string, header: string[], rows: Cell[][]) =>
  downloadBytes(name.endsWith('.csv') ? name : `${name}.csv`, toCsv(header, rows), 'text/csv;charset=utf-8');
/** Copy as TSV; resolves true when the clipboard took it. */
export async function copyTsv(header: string[], rows: Cell[][]): Promise<boolean> {
  try { await navigator.clipboard.writeText(toTsv(header, rows)); return true; }
  catch { return false; }
}
export const stampName = (base: string): string => {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, '0');
  return `${base}_${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}_${p(d.getHours())}${p(d.getMinutes())}`;
};
