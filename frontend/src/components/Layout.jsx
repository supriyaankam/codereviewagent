import { NavLink, Outlet, useNavigate } from 'react-router-dom';
import { logoutUser } from '../services/authService';
import { useAuth } from '../context/AuthContext';
import '../styles/Layout.css';

const navItems = [
  { to: '/dashboard', label: 'Dashboard',      icon: 'bi-speedometer2'   },
  { to: '/review',    label: 'Code Review',    icon: 'bi-code-slash'     },
  { to: '/autofix',   label: 'Auto-Fix',       icon: 'bi-magic'          },
  { to: '/history',   label: 'Review History', icon: 'bi-clock-history'  },
  { to: '/recently-deleted', label: 'Recently Deleted', icon: 'bi-trash3' },
  { to: '/project-history', label: 'Project History', icon: 'bi-folder2-open' },
  { to: '/standards', label: 'Team Standards', icon: 'bi-shield-check'   },
];

export default function Layout() {
  const navigate = useNavigate();
  const { currentUser } = useAuth();

  async function handleLogout() {
    try {
      await logoutUser();
    } finally {
      navigate('/login', { replace: true });
    }
  }

  return (
    <div className="app-shell d-flex">
      {/* ── Sidebar ── */}
      <aside className="sidebar d-flex flex-column">

        {/* Brand */}
        <div className="sidebar-brand px-4 py-4">
          <span className="sidebar-brand-icon me-2">⚙</span>
          <span className="sidebar-brand-text">
            Code Review<br /><small>Agent</small>
          </span>
        </div>

        {/* Nav links */}
        <nav className="flex-grow-1 px-3 mt-2">
          {navItems.map(({ to, label, icon }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                `sidebar-link d-flex align-items-center gap-3 px-3 py-2 mb-1 rounded${isActive ? ' active' : ''}`
              }
            >
              <i className={`bi ${icon}`}></i>
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>

        {/* User info + logout */}
        <div className="px-3 pb-4">
          {currentUser && (
            <div className="sidebar-user px-3 py-2 mb-2 rounded">
              <div className="d-flex align-items-center gap-2">
                <span className="sidebar-avatar d-flex align-items-center justify-content-center rounded-circle flex-shrink-0">
                  {(currentUser.displayName?.[0] ?? currentUser.email[0]).toUpperCase()}
                </span>
                <div className="overflow-hidden">
                  <div className="small fw-medium text-truncate" style={{ color: '#f1f5f9' }}>
                    {currentUser.displayName ?? 'User'}
                  </div>
                </div>
              </div>
            </div>
          )}

          <button
            onClick={handleLogout}
            className="sidebar-link sidebar-logout d-flex align-items-center gap-3 px-3 py-2 rounded w-100 border-0 bg-transparent"
          >
            <i className="bi bi-box-arrow-left"></i>
            <span>Logout</span>
          </button>
        </div>
      </aside>

      {/* ── Main content ── */}
      <main className="main-content flex-grow-1 overflow-auto">
        <Outlet />
      </main>
    </div>
  );
}
