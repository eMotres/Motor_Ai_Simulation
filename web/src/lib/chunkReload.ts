import { recordStaleChunkRecovery } from './reloadGuard';

let handledInThisDocument = false;

function showRecoveryUi(decision: 'reload' | 'banner'): void {
  if (typeof document === 'undefined') return;
  if (!document.body) {
    document.addEventListener('DOMContentLoaded', () => showRecoveryUi(decision), { once: true });
    return;
  }
  if (document.getElementById('stale-chunk-recovery')) return;

  const hidePage = document.createElement('style');
  hidePage.id = 'stale-chunk-recovery-style';
  hidePage.textContent = 'body > :not(#stale-chunk-recovery) { display: none !important; }';
  document.head?.append(hidePage);

  const language = document.documentElement.lang || navigator.language || '';
  const isChinese = /^zh(?:-|$)/i.test(language);
  const label = decision === 'reload'
    ? (isChinese ? '正在加载新版本…' : 'Loading the new version…')
    : (isChinese ? '有新版本可用 - 重新加载' : 'A new version is available - reload');
  const buttonLabel = isChinese ? '重新加载' : 'Reload';
  const backdrop = document.createElement('div');
  backdrop.id = 'stale-chunk-recovery';
  backdrop.setAttribute('role', 'alert');
  backdrop.setAttribute('aria-live', 'assertive');
  Object.assign(backdrop.style, {
    position: 'fixed', inset: '0', zIndex: '2147483647', display: 'grid',
    placeItems: 'start center', padding: '16px', boxSizing: 'border-box',
    background: '#f7f6f3', font: '14px system-ui, sans-serif',
  });
  const banner = document.createElement('div');
  Object.assign(banner.style, {
    display: 'flex', alignItems: 'center', gap: '12px', maxWidth: '100%',
    padding: '10px 14px', borderRadius: '6px', background: '#fff', color: '#222',
    boxShadow: '0 2px 12px rgba(0,0,0,.25)',
  });
  const text = document.createElement('span');
  text.textContent = label;
  banner.append(text);
  if (decision === 'banner') {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = buttonLabel;
    Object.assign(button.style, {
      border: '0', borderRadius: '4px', padding: '6px 10px', cursor: 'pointer',
      background: '#1769aa', color: '#fff', font: 'inherit',
    });
    button.addEventListener('click', () => window.location.reload());
    banner.append(button);
  }
  backdrop.append(banner);
  document.body.append(backdrop);
}

/** Report a rejected chunk/import, masking possibly untranslated content while recovering. */
export function reportChunkLoadFailure(_error?: unknown): void {
  if (handledInThisDocument || typeof window === 'undefined') return;
  handledInThisDocument = true;

  let storage: Storage | null = null;
  try { storage = window.sessionStorage; } catch { /* storage can be disabled */ }
  const decision = recordStaleChunkRecovery(storage, Date.now());
  showRecoveryUi(decision);

  if (decision === 'reload') {
    window.location.reload();
  }
}

/** Keep a rejected dynamic import rejected while routing it through stale-chunk recovery. */
export function watchChunkImport<T>(load: () => Promise<T>): () => Promise<T> {
  return () => load().catch((error: unknown) => {
    reportChunkLoadFailure(error);
    throw error;
  });
}

// Vite dispatches this before its import promise rejects. Do not preventDefault:
// Vite documents that doing so can swallow the error and resolve as undefined.
if (typeof window !== 'undefined') {
  window.addEventListener('vite:preloadError', (event) => {
    reportChunkLoadFailure((event as Event & { payload?: unknown }).payload);
  });
}
