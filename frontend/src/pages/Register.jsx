import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { registerUser } from '../services/authService';
import { useAuth } from '../context/AuthContext';

export default function Register() {
  const navigate = useNavigate();
  const { firebaseReady } = useAuth();
  const [form, setForm] = useState({ name: '', email: '', password: '', confirm: '' });
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

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
      await registerUser(form.name.trim(), form.email.trim(), form.password);
      navigate('/dashboard', { replace: true });
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
            <div className="mb-3"><span className="fs-2">⚙</span></div>
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
              />
            </div>

            <div className="mb-3">
              <label htmlFor="email" className="form-label fw-medium small">Email address</label>
              <input
                id="email" name="email" type="email"
                className="form-control" placeholder="you@company.com"
                value={form.email} onChange={handleChange}
                autoComplete="email"
              />
            </div>

            <div className="mb-3">
              <label htmlFor="password" className="form-label fw-medium small">Password</label>
              <input
                id="password" name="password" type="password"
                className="form-control" placeholder="Min. 6 characters"
                value={form.password} onChange={handleChange}
                autoComplete="new-password"
              />
            </div>

            <div className="mb-4">
              <label htmlFor="confirm" className="form-label fw-medium small">Confirm password</label>
              <input
                id="confirm" name="confirm" type="password"
                className="form-control" placeholder="••••••••"
                value={form.confirm} onChange={handleChange}
                autoComplete="new-password"
              />
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
    case 'auth/email-already-in-use':   return 'An account with this email already exists.';
    case 'auth/invalid-email':          return 'Please enter a valid email address.';
    case 'auth/weak-password':          return 'Password must be at least 6 characters.';
    case 'auth/network-request-failed': return 'Network error. Check your connection and try again.';
    default:                            return 'Registration failed. Please try again.';
  }
}
