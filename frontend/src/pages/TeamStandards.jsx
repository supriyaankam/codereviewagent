import { useState, useEffect, useCallback } from 'react';
import { useAuth } from '../context/AuthContext';
import {
  fetchStandards,
  createStandard,
  updateStandard,
  deleteStandard,
} from '../services/standardsService';
import '../styles/TeamStandards.css';

const CATEGORIES = [
  'general', 'naming', 'security', 'architecture',
  'error-handling', 'testing', 'documentation', 'performance', 'style',
];

const CATEGORY_COLORS = {
  security:       'danger',
  naming:         'primary',
  architecture:   'info',
  'error-handling': 'warning',
  testing:        'success',
  documentation:  'secondary',
  performance:    'warning',
  style:          'light',
  general:        'secondary',
};

const EMPTY_FORM = {
  title: '',
  description: '',
  category: 'general',
  severity: 'warning',
  recommended_solution: '',
  enabled: true,
  project_id: 'global',
};

export default function TeamStandards() {
  const { currentUser } = useAuth();

  const [standards, setStandards]   = useState([]);
  const [loading, setLoading]       = useState(true);
  const [error, setError]           = useState('');
  const [showModal, setShowModal]   = useState(false);
  const [editTarget, setEditTarget] = useState(null);   // null = create, obj = edit
  const [form, setForm]             = useState(EMPTY_FORM);
  const [saving, setSaving]         = useState(false);
  const [deleteId, setDeleteId]     = useState(null);
  const [deleting, setDeleting]     = useState(false);
  const [formError, setFormError]   = useState('');

  // ── Load ──────────────────────────────────────────────────────────────────
  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const data = await fetchStandards('', currentUser);
      setStandards(data || []);
    } catch (err) {
      setError(err.message || 'Could not load standards. Is the backend running?');
    } finally {
      setLoading(false);
    }
  }, [currentUser]);

  useEffect(() => { load(); }, [load]);

  // ── Modal helpers ─────────────────────────────────────────────────────────
  function openCreate() {
    setEditTarget(null);
    setForm(EMPTY_FORM);
    setFormError('');
    setShowModal(true);
  }

  function openEdit(s) {
    setEditTarget(s);
    setForm({ title: s.title, description: s.description,
              category: s.category, severity: s.severity || 'warning',
              recommended_solution: s.recommended_solution || '',
              enabled: s.enabled !== false,
              project_id: s.project_id || 'global' });
    setFormError('');
    setShowModal(true);
  }

  function closeModal() {
    setShowModal(false);
    setEditTarget(null);
  }

  function handleFormChange(e) {
    setForm({ ...form, [e.target.name]: e.target.value });
    setFormError('');
  }

  async function handleSave(e) {
    e.preventDefault();
    if (!form.title.trim() || !form.description.trim()) {
      setFormError('Title and description are required.');
      return;
    }
    setSaving(true);
    try {
      if (editTarget) {
        await updateStandard(editTarget.id, {
          title:       form.title.trim(),
          description: form.description.trim(),
          category:    form.category,
          severity:    form.severity,
          recommended_solution: form.recommended_solution.trim(),
          enabled:     form.enabled,
        }, currentUser);
      } else {
        if (!currentUser) throw new Error('Sign in before creating a standard.');
        await createStandard({
          title:       form.title.trim(),
          description: form.description.trim(),
          category:    form.category,
          severity:    form.severity,
          recommended_solution: form.recommended_solution.trim(),
          enabled:     form.enabled,
          project_id:  form.project_id || 'global',
          created_by:  currentUser.uid,
        }, currentUser);
      }
      closeModal();
      await load();
    } catch (err) {
      setFormError(err.message?.includes('503') || err.message?.includes('Firebase')
        ? 'Firebase is not configured. Add credentials to backend/.env to save standards.'
        : err.message || 'Save failed. Is the backend running?');
    } finally {
      setSaving(false);
    }
  }

  // ── Delete ────────────────────────────────────────────────────────────────
  async function handleDelete() {
    if (!deleteId) return;
    setDeleting(true);
    try {
      await deleteStandard(deleteId, currentUser);
      setDeleteId(null);
      await load();
    } catch (err) {
      setError(err.message || 'Delete failed.');
      setDeleteId(null);
    } finally {
      setDeleting(false);
    }
  }

  async function handleToggle(standard) {
    try {
      await updateStandard(standard.id, {
        title: standard.title,
        description: standard.description,
        category: standard.category,
        severity: standard.severity || 'warning',
        recommended_solution: standard.recommended_solution || '',
        enabled: standard.enabled === false,
      }, currentUser);
      await load();
    } catch (err) {
      setError(err.message || 'Could not update standard.');
    }
  }

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div className="p-4 p-md-5">

      {/* Page header */}
      <div className="d-flex align-items-start justify-content-between mb-5 flex-wrap gap-3">
        <div>
          <h4 className="fw-bold text-dark mb-1">Team Standards</h4>
          <p className="text-secondary small mb-0">
            Coding conventions your AI reviewer will check against.
          </p>
        </div>
        <button className="btn btn-primary btn-sm px-4" onClick={openCreate}>
          <i className="bi bi-plus-lg me-2"></i>Add Standard
        </button>
      </div>

      {/* Firebase not configured info — only show when trying to write */}

      {/* Error */}
      {error && (
        <div className="alert alert-danger small mb-4">
          <i className="bi bi-exclamation-circle me-2"></i>{error}
        </div>
      )}

      {/* Loading */}
      {loading && (
        <div className="text-center py-5">
          <div className="spinner-border text-primary" role="status">
            <span className="visually-hidden">Loading…</span>
          </div>
        </div>
      )}

      {/* Empty state */}
      {!loading && !error && standards.length === 0 && (
        <div className="card border-0 shadow-sm">
          <div className="card-body py-5 text-center">
            <span className="d-inline-flex align-items-center justify-content-center
                             rounded-circle bg-success-subtle mb-4"
                  style={{ width: 72, height: 72 }}>
              <i className="bi bi-shield-check fs-2 text-success"></i>
            </span>
            <h5 className="fw-semibold text-dark mb-2">No standards yet</h5>
            <p className="text-secondary small mb-4" style={{ maxWidth: 400, margin: '0 auto' }}>
              Add your team&apos;s coding conventions. The AI reviewer will check every
              submission against them and label violations clearly.
            </p>
            <button className="btn btn-primary btn-sm px-4" onClick={openCreate}>
              <i className="bi bi-plus-lg me-2"></i>Add your first standard
            </button>
          </div>
        </div>
      )}

      {!loading && (
        <div className="row g-3 mb-4">
          {[
            ['Total standards', standards.length, 'primary', 'bi-shield-check'],
            ['Enabled', standards.filter((s) => s.enabled !== false).length, 'success', 'bi-check-circle'],
            ['Disabled', standards.filter((s) => s.enabled === false).length, 'secondary', 'bi-pause-circle'],
          ].map(([label, value, color, icon]) => (
            <div className="col-12 col-md-4" key={label}>
              <div className="card border-0 shadow-sm">
                <div className="card-body d-flex align-items-center justify-content-between">
                  <span className="small text-secondary">{label}</span>
                  <span className={`text-${color}`}>
                    <i className={`bi ${icon} me-2`}></i><strong>{value}</strong>
                  </span>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Standards grid */}
      {!loading && standards.length > 0 && (
        <>
        <div className="row g-3">
          {standards.map((s) => (
            <div key={s.id} className="col-12 col-md-6 col-xl-4">
              <div className="card border-0 shadow-sm h-100 standard-card">
                <div className="card-body p-4">
                  <div className="d-flex align-items-start justify-content-between gap-2 mb-2">
                    <span className={`badge bg-${CATEGORY_COLORS[s.category] ?? 'secondary'}-subtle
                                      text-${CATEGORY_COLORS[s.category] ?? 'secondary'}
                                      text-capitalize px-2 py-1 small`}>
                      {s.category}
                    </span>
                    <div className="d-flex align-items-center gap-2">
                      <span className={`badge bg-${s.severity === 'critical' ? 'danger' : s.severity === 'error' ? 'danger' : s.severity === 'warning' ? 'warning' : 'secondary'}-subtle text-${s.severity === 'critical' || s.severity === 'error' ? 'danger' : s.severity === 'warning' ? 'warning' : 'secondary'} text-capitalize`}>
                        {s.severity || 'warning'}
                      </span>
                      <div className="form-check form-switch mb-0" title={s.enabled === false ? 'Enable standard' : 'Disable standard'}>
                        <input
                          className="form-check-input"
                          type="checkbox"
                          checked={s.enabled !== false}
                          onChange={() => handleToggle(s)}
                          aria-label={`${s.enabled === false ? 'Enable' : 'Disable'} ${s.title}`}
                        />
                      </div>
                    </div>
                    {s.created_by === currentUser?.uid && (
                      <div className="d-flex gap-1 flex-shrink-0">
                        <button
                          className="btn btn-sm btn-outline-secondary p-1 lh-1"
                          title="Edit"
                          onClick={() => openEdit(s)}
                          aria-label={`Edit ${s.title}`}
                        >
                          <i className="bi bi-pencil" style={{ fontSize: '0.75rem' }}></i>
                        </button>
                        <button
                          className="btn btn-sm btn-outline-danger p-1 lh-1"
                          title="Delete"
                          onClick={() => setDeleteId(s.id)}
                          aria-label={`Delete ${s.title}`}
                        >
                          <i className="bi bi-trash" style={{ fontSize: '0.75rem' }}></i>
                        </button>
                      </div>
                    )}
                  </div>
                  <h6 className="fw-semibold text-dark mb-2" style={{ fontSize: '0.9rem' }}>
                    {s.title}
                  </h6>
                  <p className="text-secondary small mb-0 standard-desc">
                    {s.description}
                  </p>
                  {s.recommended_solution && (
                    <p className="small mt-3 mb-0">
                      <strong>Recommended:</strong> {s.recommended_solution}
                    </p>
                  )}
                </div>
                {s.project_id && s.project_id !== 'global' && (
                  <div className="card-footer bg-transparent border-top-0 pt-0 px-4 pb-3">
                    <span className="text-muted" style={{ fontSize: '0.72rem' }}>
                      <i className="bi bi-folder me-1"></i>{s.project_id}
                    </span>
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
        </>
      )}

      {/* ── Create / Edit Modal ── */}
      {showModal && (
        <div className="modal d-block" tabIndex="-1" role="dialog"
             style={{ background: 'rgba(0,0,0,0.45)' }}
             onClick={(e) => { if (e.target === e.currentTarget) closeModal(); }}>
          <div className="modal-dialog modal-dialog-centered" role="document">
            <div className="modal-content border-0 shadow">
              <div className="modal-header border-0 pb-0">
                <h5 className="modal-title fw-semibold">
                  {editTarget ? 'Edit Standard' : 'Add Standard'}
                </h5>
                <button type="button" className="btn-close" onClick={closeModal}
                        aria-label="Close"></button>
              </div>
              <form onSubmit={handleSave} noValidate>
                <div className="modal-body pt-3">
                  {formError && (
                    <div className="alert alert-danger py-2 small mb-3">
                      <i className="bi bi-exclamation-circle me-2"></i>{formError}
                    </div>
                  )}

                  <div className="mb-3">
                    <label htmlFor="st-title" className="form-label fw-medium small">
                      Title <span className="text-danger">*</span>
                    </label>
                    <input id="st-title" name="title" type="text"
                           className="form-control"
                           placeholder='e.g. "Never hardcode credentials"'
                           value={form.title} onChange={handleFormChange} required />
                  </div>

                  <div className="mb-3">
                    <label htmlFor="st-desc" className="form-label fw-medium small">
                      Description <span className="text-danger">*</span>
                    </label>
                    <textarea id="st-desc" name="description"
                              className="form-control" rows={3}
                              placeholder="Explain the rule and why it matters…"
                              value={form.description} onChange={handleFormChange} required />
                  </div>

                  <div className="mb-3">
                    <label htmlFor="st-cat" className="form-label fw-medium small">
                      Category
                    </label>
                    <select id="st-cat" name="category" className="form-select"
                            value={form.category} onChange={handleFormChange}>
                      {CATEGORIES.map((c) => (
                        <option key={c} value={c}>{c}</option>
                      ))}
                    </select>
                  </div>

                  <div className="mb-3">
                    <label htmlFor="st-severity" className="form-label fw-medium small">
                      Severity
                    </label>
                    <select id="st-severity" name="severity" className="form-select"
                            value={form.severity} onChange={handleFormChange}>
                      <option value="critical">Critical</option>
                      <option value="error">High / Error</option>
                      <option value="warning">Medium / Warning</option>
                      <option value="info">Low / Info</option>
                    </select>
                  </div>

                  <div className="mb-3">
                    <label htmlFor="st-solution" className="form-label fw-medium small">
                      Recommended solution
                    </label>
                    <textarea id="st-solution" name="recommended_solution"
                              className="form-control" rows={2}
                              placeholder="Describe the preferred correction…"
                              value={form.recommended_solution} onChange={handleFormChange} />
                  </div>

                  <div className="form-check form-switch mb-3">
                    <input id="st-enabled" name="enabled" className="form-check-input"
                           type="checkbox" checked={form.enabled}
                           onChange={(e) => setForm({ ...form, enabled: e.target.checked })} />
                    <label className="form-check-label small" htmlFor="st-enabled">
                      Standard enabled
                    </label>
                  </div>

                  {!editTarget && (
                    <div className="mb-1">
                      <label htmlFor="st-project" className="form-label fw-medium small">
                        Project ID <span className="text-muted">(leave &quot;global&quot; for all projects)</span>
                      </label>
                      <input id="st-project" name="project_id" type="text"
                             className="form-control"
                             placeholder="global"
                             value={form.project_id} onChange={handleFormChange} />
                    </div>
                  )}
                </div>

                <div className="modal-footer border-0 pt-0">
                  <button type="button" className="btn btn-outline-secondary btn-sm"
                          onClick={closeModal}>Cancel</button>
                  <button type="submit" className="btn btn-primary btn-sm px-4"
                          disabled={saving}>
                    {saving
                      ? <><span className="spinner-border spinner-border-sm me-2"
                                role="status" aria-hidden="true"></span>Saving…</>
                      : editTarget ? 'Save changes' : 'Add standard'}
                  </button>
                </div>
              </form>
            </div>
          </div>
        </div>
      )}

      {/* ── Delete Confirmation Modal ── */}
      {deleteId && (
        <div className="modal d-block" tabIndex="-1" role="dialog"
             style={{ background: 'rgba(0,0,0,0.45)' }}>
          <div className="modal-dialog modal-dialog-centered modal-sm" role="document">
            <div className="modal-content border-0 shadow">
              <div className="modal-body p-4 text-center">
                <i className="bi bi-trash fs-2 text-danger mb-3 d-block"></i>
                <h6 className="fw-semibold mb-2">Delete this standard?</h6>
                <p className="text-secondary small mb-4">This action cannot be undone.</p>
                <div className="d-flex gap-2 justify-content-center">
                  <button className="btn btn-outline-secondary btn-sm"
                          onClick={() => setDeleteId(null)} disabled={deleting}>
                    Cancel
                  </button>
                  <button className="btn btn-danger btn-sm px-4"
                          onClick={handleDelete} disabled={deleting}>
                    {deleting
                      ? <span className="spinner-border spinner-border-sm" role="status"
                              aria-hidden="true"></span>
                      : 'Delete'}
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
