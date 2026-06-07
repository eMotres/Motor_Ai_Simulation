# Architecture, data storage & security

## Pieces
```
Browser ──► Firebase Hosting (static React)        ← Phase 2 ✅ live
   │
   ├─ Firebase Auth   (who the user is — Google / email)      ← Phase 3
   ├─ Firestore       (user data: tier + saved designs)       ← Phase 3
   └─ Cloud Run API   (FastAPI + FEM)  ← Phase 3 (this step)
          └─ verifies the Firebase ID token, enforces the tier
```

## How user data is stored (Firestore)
Document model — each user owns a subtree keyed by their auth UID:

```
users/{uid}                      { tier:"free|pro|team", email, createdAt, stripeCustomerId }
users/{uid}/designs/{designId}   { name, geometry:{…}, simulation:{…}, metrics:{…}, createdAt }
catalog/{motorId}                 (read-only ready-made motors; admin-written)
```
- The motor a user edits/saves is just a `designs` doc — geometry params + operating point + cached metrics. Small JSON, cheap.
- The **catalog** is read-only to everyone; only you (admin) write it.

## The golden rule
**The frontend is untrusted.** Never trust a tier check done in the browser — anyone
can edit JS or call the API directly. Every limit is re-checked on the **backend** and
in **Firestore rules**. Defense in depth:

### 1. Firestore Security Rules (data isolation)
A user can touch only their own subtree; catalog is read-only:
```
rules_version = '2';
service cloud.firestore {
  match /databases/{db}/documents {
    match /users/{uid}/{document=**} {
      allow read, write: if request.auth != null && request.auth.uid == uid;
    }
    match /catalog/{doc} { allow read: if true; allow write: if false; }
  }
}
```
→ Even a crafted request can't read another user's designs or forge a tier.

### 2. Backend token verification (paywall enforcement)
Expensive endpoints (live FEM, save) require a valid **Firebase ID token**:
- Frontend sends `Authorization: Bearer <idToken>`.
- Backend verifies the JWT signature with **firebase-admin** (can't be forged).
- Backend reads the user's `tier` (custom claim or Firestore) and **gates server-side**:
  free → catalog + precomputed + analytical only; pro/team → live FEM.
→ A free user calling `/api/.../fem` directly is rejected (402/403). The paywall lives
  on the server, not in the UI.

### 3. Secrets
- Stripe secret key, Firebase admin service-account key → **Cloud Run + Secret Manager**.
- **Never** in the frontend bundle, **never** committed. (Frontend only gets the *public*
  Firebase web config — that's safe to ship.)

### 4. CORS
Backend `allow_origins` is restricted to the hosting domain
(`aerostator-core-simulation.web.app`) + `localhost` for dev. Extra origins via the
`ALLOWED_ORIGINS` env var. → other sites can't call your API from a browser.

### 5. Rate limiting & input bounds (cost + abuse control)
- Per-user/day cap on FEM runs (free=0, pro=N) so nobody cost-bombs Cloud Run.
- FEM params are bounded (mesh size, steps) so a request can't blow up memory/CPU.

### 6. Stripe webhooks
- Subscription events (`checkout.session.completed`, `customer.subscription.deleted`)
  hit a backend webhook that **verifies the Stripe signature** before flipping
  `users/{uid}.tier`. → tier can only change via a real verified payment.

## "Can it be broken?" — the threat checklist
| Attack | Stopped by |
|---|---|
| Edit JS to unlock Pro | Backend re-verifies tier on every paid call (#2) |
| Read another user's designs | Firestore rules `uid == request.auth.uid` (#1) |
| Forge an auth token | JWT signature verified by firebase-admin (#2) |
| Steal Stripe/admin keys | Secrets in Secret Manager, not in frontend (#3) |
| Call API from another site | CORS allowlist (#4) |
| Cost-bomb FEM | Rate limit + param bounds (#5) |
| Fake a paid upgrade | Stripe webhook signature check (#6) |

This is the standard, battle-tested SaaS shape (Firebase + a verified backend). We add
it in Phase 3 once the Cloud Run backend is up and verified.
