import { Navigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';

/**
 * Guards authenticated pages.
 * - While Firebase resolves auth state → shows a spinner.
 * - If Firebase is not configured → lets the user through (dev mode).
 * - If unauthenticated → redirects to /login.
 */
export default function ProtectedRoute({ children }) {
  const { currentUser, loading, firebaseReady } = useAuth();

  if (loading) {
    return (
      <div className="min-vh-100 d-flex align-items-center justify-content-center">
        <div className="spinner-border text-primary" role="status">
          <span className="visually-hidden">Loading…</span>
        </div>
      </div>
    );
  }

  // Firebase not configured — allow access so the UI is visible without .env
  if (!firebaseReady) return children;

  if (!currentUser) {
    return <Navigate to="/login" replace />;
  }

  return children;
}
