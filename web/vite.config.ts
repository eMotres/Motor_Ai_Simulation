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
    // `localhost` to.  That is the "веб не отвечает" that has been coming back
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
})
