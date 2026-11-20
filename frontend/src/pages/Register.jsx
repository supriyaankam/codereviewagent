import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Eye, EyeOff } from 'lucide-react';
import CodeGuardLogo from '../components/CodeGuardLogo';
import { registerUser } from '../services/authService';
import { useAuth } from '../context/AuthContext';

export default function Register() {
  const navigate = useNavigate();
  const { firebaseReady } = useAuth();
  const [form, setForm] = useState({ name: '', email: '', password: '', confirm: '' });
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [passwordVisible, setPasswordVisible] = useState(false);
  const [confirmVisible, setConfirmVisible] = useState(false);

  function handleChange(e) {
    setForm({ ...form, [e.target.name]: e.target.value });
    setError('');
  }

  async function handleSubmit(e) {
    e.preventDefault();
    if (!firebaseReady) {
      navigate('/dashboard', { replace: true });
      return;
    }
    if (form.password !== form.confirm) {
      setError('Passwords do not match.');
      return;
    }
    if (form.password.length < 6) {
      setError('Password must be at least 6 characters.');
      return;
    }
    setLoading(true);
    try {
      const { profileWarnings } = await registerUser(
        form.name.trim(),
        form.email.trim(),
        form.password,
      );
      navigate('/dashboard', {
        replace: true,
        state: profileWarnings.length ? { accountWarning: profileWarnings.join(' ') } : undefined,
      });
    } catch (err) {
      setError(friendlyError(err.code));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-vh-100 d-flex align-items-center justify-content-center bg-light px-3">
      <div className="card shadow-sm border-0 w-100" style={{ maxWidth: 440 }}>
        <div className="card-body p-4 p-md-5">

          {/* Header */}
          <div className="text-center mb-4">
            <div className="mb-3"><CodeGuardLogo /></div>
            <h4 className="fw-bold text-dark mb-1">Create an account</h4>
            <p className="text-secondary small">Get started with Code Review Agent</p>
          </div>

          {/* Firebase not configured banner */}
          {!firebaseReady && (
            <div className="alert alert-warning py-2 small" role="alert">
              <i className="bi bi-exclamation-triangle me-2"></i>
              <strong>Firebase not configured.</strong> Add your credentials to{' '}
              <code>frontend/.env</code> to enable authentication.
              <br />
              <span className="text-muted">You can still preview the UI — click Create account to continue.</span>
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
              <label htmlFor="name" className="form-label fw-medium small">Full name</label>
              <input
                id="name" name="name" type="text"
                className="form-control" placeholder="Jane Smith"
                value={form.name} onChange={handleChange}
                autoComplete="name"
                required
              />
            </div>

            <div className="mb-3">
              <label htmlFor="email" className="form-label fw-medium small">Email address</label>
              <input
                id="email" name="email" type="email"
                className="form-control" placeholder="you@company.com"
                value={form.email} onChange={handleChange}
                autoComplete="email"
                required
              />
            </div>

            <div className="mb-3">
              <label htmlFor="password" className="form-label fw-medium small">Password</label>
              <div className="position-relative">
                <input
                  id="password" name="password" type={passwordVisible ? 'text' : 'password'}
                  className="form-control pe-5" placeholder="Min. 6 characters"
                  value={form.password} onChange={handleChange}
                  autoComplete="new-password"
                  minLength={6}
                  required
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

            <div className="mb-4">
              <label htmlFor="confirm" className="form-label fw-medium small">Confirm password</label>
              <div className="position-relative">
                <input
                  id="confirm" name="confirm" type={confirmVisible ? 'text' : 'password'}
                  className="form-control pe-5" placeholder="••••••••"
                  value={form.confirm} onChange={handleChange}
                  autoComplete="new-password"
                  minLength={6}
                  required
                />
                <button
                  type="button"
                  className="btn btn-link position-absolute top-50 end-0 translate-middle-y me-1 p-2 text-secondary text-decoration-none"
                  aria-label={confirmVisible ? 'Hide confirm password' : 'Show confirm password'}
                  aria-controls="confirm"
                  onClick={() => setConfirmVisible(!confirmVisible)}
                >
                  {confirmVisible
                    ? <EyeOff size={18} aria-hidden="true" />
                    : <Eye size={18} aria-hidden="true" />}
                </button>
              </div>
            </div>

            <button type="submit" className="btn btn-primary w-100 fw-medium" disabled={loading}>
              {loading
                ? <><span className="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>Creating account…</>
                : 'Create account'}
            </button>
          </form>

          <p className="text-center text-secondary small mt-4 mb-0">
            Already have an account?{' '}
            <Link to="/login" className="text-primary text-decoration-none fw-medium">Sign in</Link>
          </p>
        </div>
      </div>
    </div>
  );
}

function friendlyError(code) {
  switch (code) {
    case 'auth/email-already-in-use':   return 'An account with this email already exists. Use the Sign in link below.';
    case 'auth/invalid-email':          return 'Please enter a valid email address.';
    case 'auth/weak-password':          return 'Password must be at least 6 characters.';
    case 'auth/network-request-failed': return 'Network error. Check your connection and try again.';
    case 'auth/operation-not-allowed':
      return 'Email/password sign-up is disabled for this Firebase project. Enable the Email/Password provider in Firebase Authentication settings.';
    case 'auth/unauthorized-domain':
      return 'This site is not authorized for Firebase sign-in. Add localhost to the authorized domains in Firebase Authentication settings.';
    case 'auth/invalid-api-key':
    case 'auth/app-not-authorized':
    case 'auth/configuration-not-found':
      return 'Firebase Authentication is not configured correctly for this app. Check the frontend Firebase settings and Authentication provider.';
    case 'auth/too-many-requests':
      return 'Too many registration attempts. Wait a while and try again.';
    default:                            return 'Registration failed. Please try again.';
  }
}
