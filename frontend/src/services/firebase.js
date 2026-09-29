import { initializeApp } from 'firebase/app';
import { getAuth } from 'firebase/auth';
import { getFirestore } from 'firebase/firestore';

/**
 * Firebase configuration is read exclusively from Vite environment variables.
 * Copy .env.example → .env and fill in your project credentials.
 * Never hardcode values here.
 */
const firebaseConfig = {
  apiKey:            import.meta.env.VITE_FIREBASE_API_KEY,
  authDomain:        import.meta.env.VITE_FIREBASE_AUTH_DOMAIN,
  projectId:         import.meta.env.VITE_FIREBASE_PROJECT_ID,
  storageBucket:     import.meta.env.VITE_FIREBASE_STORAGE_BUCKET,
  messagingSenderId: import.meta.env.VITE_FIREBASE_MESSAGING_SENDER_ID,
  appId:             import.meta.env.VITE_FIREBASE_APP_ID,
};

// Check that all required variables are present before initializing.
export const firebaseConfigured = Object.values(firebaseConfig).every(Boolean);

let app, auth, db;

if (firebaseConfigured) {
  app  = initializeApp(firebaseConfig);
  auth = getAuth(app);
  db   = getFirestore(app);
} else {
  // Running without .env — auth and db will be null.
  // The app will render but Firebase features will be disabled.
  console.warn(
    '[Code Review Agent] Firebase environment variables are not set.\n' +
    'Copy frontend/.env.example → frontend/.env and add your credentials.\n' +
    'Auth and Firestore will be unavailable until then.'
  );
  auth = null;
  db   = null;
}

export { auth, db };
