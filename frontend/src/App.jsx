import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import 'bootstrap/dist/css/bootstrap.min.css';
import 'bootstrap-icons/font/bootstrap-icons.css';
import './App.css';

import { AuthProvider }    from './context/AuthContext';
import ProtectedRoute      from './components/ProtectedRoute';
import Layout              from './components/Layout';
import Login               from './pages/Login';
import Register            from './pages/Register';
import Dashboard           from './pages/Dashboard';
import CodeReview          from './pages/CodeReview';
import AutoFix             from './pages/AutoFix';
import ReviewHistory       from './pages/ReviewHistory';
import TeamStandards       from './pages/TeamStandards';

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          {/* ── Public routes ── */}
          <Route path="/login"    element={<Login />} />
          <Route path="/register" element={<Register />} />

          {/* ── Protected routes — require authentication ── */}
          <Route
            element={
              <ProtectedRoute>
                <Layout />
              </ProtectedRoute>
            }
          >
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/review"    element={<CodeReview />} />
            <Route path="/autofix"   element={<AutoFix />} />
            <Route path="/history"   element={<ReviewHistory />} />
            <Route path="/standards" element={<TeamStandards />} />
          </Route>

          {/* ── Default redirect ── */}
          <Route path="*" element={<Navigate to="/login" replace />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}
