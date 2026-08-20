# Google sign-in setup (Google Identity Services)

Auth is self-hosted (no Firebase): Google only proves *identity*; tiers and
admin rights live in `config/users.json` + `ADMIN_EMAILS`. To enable the
"Sign in with Google" button you need one OAuth **Client ID** (free, no
billing, ~5 minutes).

## 1. Create the OAuth client

1. Open <https://console.cloud.google.com> and create (or pick) a project,
   e.g. `emotres-portal`. Firebase is NOT needed.
2. **APIs & Services → OAuth consent screen**:
   - User type: **External**, app name: `AeroStator Core`,
     support email: your address.
   - Publish the app (Publishing status → **In production**) so any Google
     account can sign in, not just test users.
3. **APIs & Services → Credentials → + Create credentials → OAuth client ID**:
   - Application type: **Web application**.
   - Authorized JavaScript origins:
     - `http://localhost:5173` (dev)
     - `https://emotres.com` (add when the portal goes live)
   - Authorized redirect URIs: **leave empty** (the GIS button uses popup
     mode, no redirects).
4. Copy the client ID — it looks like
   `1234567890-abc123.apps.googleusercontent.com`.

## 2. Wire it into the app

The SAME value goes to both sides:

- **Frontend** — `web/.env.local`:

  ```
  VITE_GOOGLE_CLIENT_ID=1234567890-abc123.apps.googleusercontent.com
  ```

  then restart the Vite dev server (Vite bakes env vars at startup).

- **Backend** — the API process env. For the local scheduled task, uncomment
  and fill the `set GOOGLE_CLIENT_ID=...` line in `scripts/start_api.cmd`,
  then restart the API. For a server deploy, set `GOOGLE_CLIENT_ID` in the
  service env (see `.env.example`).

The Google button appears in the Sign-in dialog only when
`VITE_GOOGLE_CLIENT_ID` is set; the backend accepts Google tokens only when
`GOOGLE_CLIENT_ID` is set. Password accounts work regardless.

## 3. First sign-in

- A Google account signing in for the first time is auto-created in
  `config/users.json` with tier `free`.
- Emails listed in `ADMIN_EMAILS` (comma-separated env var) are ALWAYS
  admin, whatever the registry says — put the owner address there in
  production: `ADMIN_EMAILS=vadim@motresres.com`.
- Tiers/disabling are managed in the app: **Admin tab → Users**.

## How it works (for reference)

1. The GIS button returns a Google **ID token** (1-hour life).
2. The frontend POSTs it to `/api/auth/google`; the backend verifies it
   against Google's JWKS (audience = `GOOGLE_CLIENT_ID`, issuer
   `accounts.google.com`, `email_verified` required).
3. The backend answers with OUR HS256 token (30-day life, signed with
   `config/.auth_secret` / `AUTH_SECRET`); that token is what every API call
   carries. Tier and the disabled flag are re-read from `users.json` on every
   request — demoting or disabling an account takes effect immediately.
