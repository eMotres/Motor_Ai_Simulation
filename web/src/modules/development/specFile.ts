// The specification-file upload of the assistant (owner 2026-11-24): the checks made in the browser before a file is sent, and the
// words of a card's source.  Pure functions (no React, no fetch), covered by `node --test`; the server repeats every check.
export const FILE_EXTENSIONS = ['.txt', '.csv', '.md', '.docx', '.pdf', '.xlsx', '.xls'] as const;
export const FILE_MAX_BYTES = 5 * 1024 * 1024;

export type FileRefusal = 'empty' | 'doc' | 'macro' | 'type' | 'size';

const extOf = (name: string): string => {
  const base = name.replace(/\\/g, '/').split('/').pop() ?? '';
  const i = base.lastIndexOf('.');
  return i >= 0 ? base.slice(i).toLowerCase() : '';
};

/** Why the browser will not send this file, or null. */
export function fileRefusal(name: string, size: number, maxBytes: number = FILE_MAX_BYTES, extensions: readonly string[] = FILE_EXTENSIONS): FileRefusal | null {
  const ext = extOf(name);
  if (ext === '.doc') return 'doc';
  if (['.docm', '.xlsm', '.dotm', '.xltm', '.pptm'].includes(ext)) return 'macro';
  if (!extensions.includes(ext)) return 'type';
  if (size <= 0) return 'empty';
  if (size > maxBytes) return 'size';
  return null;
}

export interface FileSource { ref: string; cited?: string; excerpt?: string; converted?: string; found?: boolean }

/** The source of a card as it is read from the proposal (a card without a file has none). */
export function fileSourceOf(p: { file_source?: unknown }): FileSource | null {
  const s = p.file_source as Partial<FileSource> | undefined;
  return s && typeof s === 'object' && typeof s.ref === 'string' && s.ref ? (s as FileSource) : null;
}
