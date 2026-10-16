# Deploy guide

The app splits into two deployables:

| Part | Tech | Hosts on | Status |
|---|---|---|---|
| **Frontend** (React/Vite) | static bundle | **Firebase Hosting** | ✅ ready (this doc) |
| **Backend** (FastAPI + FEM) | Python + gmsh/CadQuery/scikit-fem | **Google Cloud Run** (Docker) | ⏳ next phase |

The FEM backend is heavy/native and **cannot** run on Firebase — it goes to Cloud Run.
Firebase hosts the frontend, Auth, and Firestore (saved designs).

---

## Phase 2 — Frontend on Firebase Hosting (ready now)

### What I prepared
- `firebase.json` — hosting config (serves `web/dist`, SPA rewrites, asset caching)
- `.firebaserc` — project alias (you fill your project id)
- `web/.env.production` — `VITE_API_URL` for the deployed backend
- `web/package.json` build → `vite build` produces `web/dist`

### What YOU do (once)
1. Create a Firebase project at https://console.firebase.google.com (free Spark plan is fine for hosting).
2. Put the project id into `.firebaserc` (replace `YOUR_FIREBASE_PROJECT_ID`).
3. Install the CLI and log in (these need YOUR Google account — I can't do this):
   ```bash
   npm install -g firebase-tools
   firebase login
   ```

### Deploy
```bash
cd web && npm run build && cd ..
firebase deploy --only hosting
```
→ your site goes live at `https://YOUR_FIREBASE_PROJECT_ID.web.app`.

> Until the backend (Phase 3) is live, the hosted site loads but API features stay
> in "Local Mode". Set `VITE_API_URL` in `web/.env.production` to the Cloud Run URL
> and re-deploy once the backend is up.

---

## Phase 3 — Backend on Cloud Run + Auth + Firestore + Stripe (next)

Planned, I'll prepare on request:
- **Dockerfile** for the FastAPI+FEM backend → `gcloud run deploy` (you run it; needs GCP billing).
- **Firebase Auth** (Google / email) in the UI; gate features by tier.
- **Firestore** schema: `users/{uid}` (tier) + `users/{uid}/designs/{id}` (saved motors).
- **Tier gating**: Free = catalog + precomputed + analytical preview (zero backend cost);
  Pro/Team = live FEM on Cloud Run.
- **Stripe** subscriptions (Firebase "Run Payments with Stripe" extension or Cloud Functions webhook).

### What you'll need for Phase 3
- Enable **billing** on the Google Cloud project (Cloud Run + Artifact Registry are pay-as-you-go; scale-to-zero keeps idle cost ~$0).
- A **Stripe** account (for paid tiers) — keys go into backend env, not committed.
- Enable **Authentication** and **Firestore** in the Firebase console.
