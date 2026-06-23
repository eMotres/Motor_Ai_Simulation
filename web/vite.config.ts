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
  define: {
    __APP_VERSION__: JSON.stringify(appVersion()),
    __APP_GIT_SHA__: JSON.stringify(gitSha()),
  },
})
