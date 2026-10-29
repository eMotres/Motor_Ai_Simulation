// Firebase client init. Reads PUBLIC config from VITE_FIREBASE_* env vars
// (safe to expose — these identify the project, they are not secrets).
// If the config is absent the app runs in anonymous mode (auth disabled),
// so local dev works before Firebase is wired.
import { initializeApp, type FirebaseApp } from 'firebase/app';
import { getAuth, GoogleAuthProvider, type Auth } from 'firebase/auth';
import { getFirestore, type Firestore } from 'firebase/firestore';
import { firebaseConfig as cfg, firebaseEnabled } from './firebaseConfig';

export { firebaseEnabled };

let app: FirebaseApp | undefined;
let auth: Auth | undefined;
let db: Firestore | undefined;
let googleProvider: GoogleAuthProvider | undefined;

if (firebaseEnabled) {
  app = initializeApp(cfg as Required<typeof cfg>);
  auth = getAuth(app);
  db = getFirestore(app);
  googleProvider = new GoogleAuthProvider();
} else {
  // eslint-disable-next-line no-console
  console.info('[firebase] not configured (VITE_FIREBASE_* unset) — auth disabled, anonymous mode.');
}

export { app, auth, db, googleProvider };
