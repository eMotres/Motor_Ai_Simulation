// Turn a backend error `detail` payload into ONE readable line.
//
// Our own 422s carry `detail` as {error, invalid_parameters: [{field,
// value, kind, message, ...}, ...]} (routes/_validation.py's `reject`, the
// shape every geometry/material/etc. guard raises with).  FastAPI's own
// request-validation 422s carry `detail` as a LIST of {loc, msg, type}.  A
// route that just wants to say "no" raises `detail` as a plain string.
//
// `new Error(detail)` (or `String(detail)`, or interpolating `detail`
// straight into a template string) on either object shape stringifies to
// the literal text "[object Object]" -- readable to nobody.  The Fusion CSV
// import's confirm-dialog flow hit exactly this on 2026-09-25: a refused
// import showed "✗ import failed: [object Object]" instead of which
// parameter was wrong and why.  Route every place that turns a fetch
// response's `detail` into user-facing text through this instead of
// re-deriving the same unwrap.
export function formatApiErrorDetail(detail: unknown): string {
  if (detail == null || detail === '') return '';
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail.map(formatDetailItem).filter(Boolean).join('; ');
  }
  if (typeof detail === 'object') {
    const d = detail as Record<string, unknown>;
    const parts: string[] = [];
    if (typeof d.error === 'string') parts.push(d.error);
    else if (typeof d.message === 'string') parts.push(d.message);
    const list = Array.isArray(d.invalid_parameters) ? d.invalid_parameters
               : Array.isArray(d.errors) ? d.errors
               : Array.isArray(d.detail) ? d.detail
               : null;
    if (list && list.length) {
      parts.push(list.map(formatDetailItem).filter(Boolean).join('; '));
    }
    if (parts.length) return parts.join(' — ');
    try { return JSON.stringify(d); } catch { return String(d); }
  }
  return String(detail);
}

function formatDetailItem(item: unknown): string {
  if (typeof item === 'string') return item;
  if (item && typeof item === 'object') {
    const d = item as Record<string, unknown>;
    const field = typeof d.field === 'string' ? d.field
                 : Array.isArray(d.loc) ? d.loc.join('.') : undefined;
    const msg = (typeof d.message === 'string' && d.message)
             || (typeof d.msg === 'string' && d.msg);
    if (msg) return field ? `${field}: ${msg}` : msg;
    try { return JSON.stringify(d); } catch { return String(d); }
  }
  return String(item);
}
