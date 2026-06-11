import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { AuthProvider } from './contexts/AuthContext'

// ── Hybrid ResizeObserver ────────────────────────────────────────────────
// Some embedded / preview browsers ship a ResizeObserver whose callback NEVER
// fires.  That silently breaks every size-measuring library: react-three-fiber
// only creates its WebGL root once it measures size > 0, so the mesh / field
// 3-D canvases stay at the 300×150 default and render BLACK; recharts logs
// endless "width(0) height(0)".  Route the native RO (when it works) and a
// polling fallback through one deduped emit so sizing works everywhere.
(() => {
  if (typeof window === 'undefined') return;
  const Native = window.ResizeObserver;
  class HybridRO {
    private cb: ResizeObserverCallback;
    private native: ResizeObserver | null = null;
    private seen = new Map<Element, { w: number; h: number }>();
    private timer: number | null = null;
    constructor(cb: ResizeObserverCallback) {
      this.cb = cb;
      try { this.native = Native ? new Native(() => this.poll()) : null; } catch { this.native = null; }
    }
    observe(el: Element, opts?: ResizeObserverOptions) {
      this.seen.set(el, { w: -1, h: -1 });
      try { this.native?.observe(el, opts); } catch { /* ignore */ }
      if (this.timer == null) this.timer = window.setInterval(() => this.poll(), 200);
      setTimeout(() => this.poll(), 0);
    }
    unobserve(el: Element) {
      this.seen.delete(el);
      try { this.native?.unobserve(el); } catch { /* ignore */ }
      if (this.seen.size === 0 && this.timer != null) { clearInterval(this.timer); this.timer = null; }
    }
    disconnect() {
      this.seen.clear();
      try { this.native?.disconnect(); } catch { /* ignore */ }
      if (this.timer != null) { clearInterval(this.timer); this.timer = null; }
    }
    private poll() {
      const entries: any[] = [];
      this.seen.forEach((last, el) => {
        const r = (el as HTMLElement).getBoundingClientRect();
        if (Math.abs(r.width - last.w) > 0.5 || Math.abs(r.height - last.h) > 0.5) {
          last.w = r.width; last.h = r.height;
          const box = [{ inlineSize: r.width, blockSize: r.height }];
          entries.push({ target: el, contentRect: r, borderBoxSize: box,
            contentBoxSize: box, devicePixelContentBoxSize: box });
        }
      });
      if (entries.length) { try { this.cb(entries as any, this as any); } catch { /* ignore */ } }
    }
  }
  (window as any).ResizeObserver = HybridRO as any;
})();

// Suppress THREE.js deprecation warnings (from library internals)
const originalConsoleWarn = console.warn;
console.warn = (...args) => {
  const message = args[0];
  if (typeof message === 'string' && (
    message.includes('THREE.Clock') || 
    message.includes('THREE.WebGLShadowMap') ||
    message.includes('PCFSoftShadowMap')
  )) {
    return; // Suppress these warnings
  }
  originalConsoleWarn.apply(console, args);
};

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <AuthProvider>
      <App />
    </AuthProvider>
  </StrictMode>,
)
