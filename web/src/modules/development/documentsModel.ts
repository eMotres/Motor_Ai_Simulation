/** Pure helpers of the project Documents tab. */
export const MAX_DOCUMENT_BYTES = 200 * 1024 * 1024;

/** 1536 -> "1.5 KB"; the number follows the page language, the unit stays a unit. */
export function formatBytes(bytes: number, language = 'en'): string {
  const units = ['B', 'KB', 'MB', 'GB'];
  let value = Number.isFinite(bytes) && bytes > 0 ? bytes : 0;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) { value /= 1024; i += 1; }
  const digits = i === 0 || value >= 100 ? 0 : 1;
  return `${value.toLocaleString(language, { minimumFractionDigits: digits, maximumFractionDigits: digits })} ${units[i]}`;
}

/** PNG, JPEG and WebP files can be the project cover (the server checks the content too). */
export const isCoverImage = (name: string): boolean => /\.(png|jpe?g|webp)$/i.test(name);
