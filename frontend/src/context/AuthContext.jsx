import { createContext, useContext, useEffect, useState } from 'react';
import { onAuthStateChanged } from 'firebase/auth';
import PropTypes from 'prop-types';
import { auth, firebaseConfigured } from '../services/firebase';

const AuthContext = createContext(null);

/**
 * Wraps the app and makes the current Firebase user available everywhere
 * via useAuth(). Also exposes:
 *   loading          — true while Firebase resolves the initial auth state
 *   firebaseReady    — false when .env credentials are missing
 */
export function AuthProvider({ children }) {
  const [currentUser, setCurrentUser] = useState(null);
  // If Firebase isn't configured we can resolve immediately (no user, not loading)
  const [loading, setLoading] = useState(firebaseConfigured);

  useEffect(() => {
    if (!firebaseConfigured) return; // nothing to subscribe to

    const unsubscribe = onAuthStateChanged(auth, (user) => {
      setCurrentUser(user);
      setLoading(false);
    });
    return unsubscribe;
  }, []);

  return (
    <AuthContext.Provider value={{ currentUser, loading, firebaseReady: firebaseConfigured }}>
      {children}
    </AuthContext.Provider>
  );
}

AuthProvider.propTypes = {
  children: PropTypes.node.isRequired,
};

/** Convenience hook — throws if used outside <AuthProvider>. */
export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>');
  return ctx;
}
