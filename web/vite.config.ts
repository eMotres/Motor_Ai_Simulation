import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { readFileSync } from 'node:fs'
import { execSync } from 'node:child_process'
import { resolve } from 'node:path'

// Single source of truth for the app version = ../VERSION (repo root).
// Stamped into the build so the UI shows it and can detect frontend/backend skew.
function appVersion(): string {
  try { return readFileSync(resolve(process.cwd(), '..', 'VERSION'), 'utf8').trim() || '0.0.0' }
  catch { return '0.0.0' }
}
function gitSha(): string {
  try { return execSync('git rev-parse --short HEAD', { cwd: process.cwd() }).toString().trim() }
  catch { return 'dev' }
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // LISTEN ON BOTH STACKS.  Vite's default bind on this machine is IPv6 only
    // ([::1]:5173), while the API binds IPv4 (127.0.0.1:8001) — so whether the
    // app opened at all depended on which address the browser resolved
    // `localhost` to.  That is the "the web isn't responding" that has been coming back
    // for days: nothing had crashed, the server was simply not on the address
    // the browser tried.  0.0.0.0 also puts it on the LAN, which is what the
    // phone/remote-control view needs anyway.
    // '::' = dual stack: Node binds the IPv6 wildcard and accepts IPv4 through
    // v4-mapped addresses, so BOTH 127.0.0.1 and [::1] answer.  '0.0.0.0' would
    // only fix IPv4 and break the browsers that resolve localhost to ::1 —
    // trading one half of the problem for the other.
    host: '::',
    port: 5173,
    strictPort: true,   // fail loudly instead of silently moving to 5174
  },
  define: {
    __APP_VERSION__: JSON.stringify(appVersion()),
    __APP_GIT_SHA__: JSON.stringify(gitSha()),
  },
  build: {
    rollupOptions: {
      output: {
        // Vendor groups that change rarely get their own files, so a deploy of app code does not make a
        // returning browser fetch React or MUI again.  three.js and recharts are only reached from lazy tabs
        // (App.tsx), so their chunks load when a 3-D view or a chart first opens.
        manualChunks(id) {
          // Rollup's helpers (lazy-import preloader, CommonJS interop) go with React: left alone they are pulled
          // into the first vendor group that needs them - three.js - and every page would import that chunk.
          if (id.includes('vite/preload-helper') || id.includes('commonjsHelpers')) return 'vendor-react'
          if (!id.includes('/node_modules/')) return undefined
          const pkg = id.split('/node_modules/').pop()!
          // use-sync-external-store is shared by zustand and recharts' redux store: with React, or the two chunks import each other
          if (/^(react|react-dom|scheduler|use-sync-external-store|zustand|i18next[^/]*|react-i18next)\//.test(pkg)) return 'vendor-react'
          // MUI's styling core only: the @mui/material components stay with the chunks that use them, so the signed-out
          // landing does not download every component of the workspace (all of MUI in one chunk: +35 KiB gzip there)
          if (/^(@emotion\/[^/]+|@mui\/(system|utils|styled-engine|private-theming)|stylis)\//.test(pkg)) return 'vendor-mui'
          if (/^(three|three-stdlib|@react-three|postprocessing|maath|troika-[^/]+|camera-controls|meshline|stats-gl|@monogrid)\//.test(pkg)) return 'vendor-three'
          if (/^(recharts|d3-[^/]+|victory-vendor)\//.test(pkg)) return 'vendor-charts'
          return undefined
        },
      },
    },
  },
})
