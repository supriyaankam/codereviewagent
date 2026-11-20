import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Eye, EyeOff } from 'lucide-react';
import CodeGuardLogo from '../components/CodeGuardLogo';
import { loginUser } from '../services/authService';
import { useAuth } from '../context/AuthContext';

export default function Login() {
  const navigate = useNavigate();
  const { firebaseReady } = useAuth();
  const [form, setForm] = useState({ email: '', password: '' });
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [passwordVisible, setPasswordVisible] = useState(false);

  function handleChange(e) {
    setForm({ ...form, [e.target.name]: e.target.value });
    setError('');
  }

  async function handleSubmit(e) {
    e.preventDefault();
    if (!firebaseReady) {
      // Firebase not configured — navigate directly for UI preview
      navigate('/dashboard', { replace: true });
      return;
    }
    setLoading(true);
    try {
      await loginUser(form.email.trim(), form.password);
      navigate('/dashboard', { replace: true });
    } catch (err) {
      setError(friendlyError(err.code));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-vh-100 d-flex align-items-center justify-content-center bg-light px-3">
      <div className="card shadow-sm border-0 w-100" style={{ maxWidth: 420 }}>
        <div className="card-body p-4 p-md-5">

          {/* Header */}
          <div className="text-center mb-4">
            <div className="mb-3"><CodeGuardLogo /></div>
            <h4 className="fw-bold text-dark mb-1">Welcome</h4>
            <p className="text-secondary small">Sign in to Code Review Agent</p>
          </div>

          {/* Firebase not configured banner */}
          {!firebaseReady && (
            <div className="alert alert-warning py-2 small" role="alert">
              <i className="bi bi-exclamation-triangle me-2"></i>
              <strong>Firebase not configured.</strong> Add your credentials to{' '}
              <code>frontend/.env</code> to enable authentication.
              <br />
              <span className="text-muted">You can still preview the UI — click Sign in to continue.</span>
            </div>
          )}

          {/* Error alert */}
          {error && (
            <div className="alert alert-danger py-2 small" role="alert">
              <i className="bi bi-exclamation-circle me-2"></i>{error}
            </div>
          )}

          {/* Form */}
          <form onSubmit={handleSubmit} noValidate>
            <div className="mb-3">
              <label htmlFor="email" className="form-label fw-medium small">Email address</label>
              <input
                id="email" name="email" type="email"
                className="form-control"
                placeholder="you@company.com"
                value={form.email} onChange={handleChange}
                autoComplete="email"
              />
            </div>

            <div className="mb-4">
              <label htmlFor="password" className="form-label fw-medium small">Password</label>
              <div className="position-relative">
                <input
                  id="password" name="password" type={passwordVisible ? 'text' : 'password'}
                  className="form-control pe-5"
                  placeholder="••••••••"
                  value={form.password} onChange={handleChange}
                  autoComplete="current-password"
                />
                <button
                  type="button"
                  className="btn btn-link position-absolute top-50 end-0 translate-middle-y me-1 p-2 text-secondary text-decoration-none"
                  aria-label={passwordVisible ? 'Hide password' : 'Show password'}
                  aria-controls="password"
                  onClick={() => setPasswordVisible(!passwordVisible)}
                >
                  {passwordVisible
                    ? <EyeOff size={18} aria-hidden="true" />
                    : <Eye size={18} aria-hidden="true" />}
                </button>
              </div>
            </div>

            <button type="submit" className="btn btn-primary w-100 fw-medium" disabled={loading}>
              {loading
                ? <><span className="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>Signing in…</>
                : 'Sign in'}
            </button>
          </form>

          <p className="text-center text-secondary small mt-4 mb-0">
            Don&apos;t have an account?{' '}
            <Link to="/register" className="text-primary text-decoration-none fw-medium">Register</Link>
          </p>
        </div>
      </div>
    </div>
  );
}

function friendlyError(code) {
  switch (code) {
    case 'auth/user-not-found':
    case 'auth/wrong-password':
    case 'auth/invalid-credential':     return 'Incorrect email or password.';
    case 'auth/invalid-email':          return 'Please enter a valid email address.';
    case 'auth/too-many-requests':      return 'Too many attempts. Please wait and try again.';
    case 'auth/network-request-failed': return 'Network error. Check your connection and try again.';
    default:                            return 'Sign-in failed. Please try again.';
  }
}
