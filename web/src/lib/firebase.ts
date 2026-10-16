// Firebase client init. Reads PUBLIC config from VITE_FIREBASE_* env vars
// (safe to expose — these identify the project, they are not secrets).
// If the config is absent the app runs in anonymous mode (auth disabled),
// so local dev works before Firebase is wired.
import { initializeApp, type FirebaseApp } from 'firebase/app';
import { getAuth, GoogleAuthProvider, type Auth } from 'firebase/auth';
import { getFirestore, type Firestore } from 'firebase/firestore';

const cfg = {
  apiKey: import.meta.env.VITE_FIREBASE_API_KEY as string | undefined,
  authDomain: import.meta.env.VITE_FIREBASE_AUTH_DOMAIN as string | undefined,
  projectId: import.meta.env.VITE_FIREBASE_PROJECT_ID as string | undefined,
  storageBucket: import.meta.env.VITE_FIREBASE_STORAGE_BUCKET as string | undefined,
  messagingSenderId: import.meta.env.VITE_FIREBASE_MESSAGING_SENDER_ID as string | undefined,
  appId: import.meta.env.VITE_FIREBASE_APP_ID as string | undefined,
};

export const firebaseEnabled = Boolean(cfg.apiKey && cfg.projectId && cfg.appId);

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
