import React, { useEffect, useState } from 'react';
import { api } from '../../api';

const fields = [
  { key: 'storage_root', label: 'Storage Root' },
  { key: 'kube_image_pvc', label: 'Image PVC' },
  { key: 'kube_vm_storage_class', label: 'VM Clone StorageClass' },
  { key: 'kube_namespace', label: 'Namespace (read-only)', readOnly: true },
];

const statusMeta = (status) => {
  const normalized = String(status || '').toLowerCase();
  if (normalized === 'error') return { label: 'Error', className: 'badge warn' };
  if (normalized === 'warn') return { label: 'Warn', className: 'badge warn' };
  if (normalized === 'ok') return { label: 'OK', className: 'badge success' };
  return { label: 'Info', className: 'badge' };
};

const AdminStorageSettings = () => {
  const [data, setData] = useState({
    storage_root: '',
    kube_image_pvc: '',
    kube_vm_storage_class: '',
    kube_namespace: '',
  });
  const [sources, setSources] = useState({});
  const [checks, setChecks] = useState([]);
  const [warnings, setWarnings] = useState([]);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [clearing, setClearing] = useState(false);

  const applyResponse = (payload) => {
    setData({
      storage_root: payload?.storage_root || '',
      kube_image_pvc: payload?.kube_image_pvc || '',
      kube_vm_storage_class: payload?.kube_vm_storage_class || '',
      kube_namespace: payload?.kube_namespace || '',
    });
    setSources(payload?.sources || {});
    setChecks(payload?.checks || []);
    setWarnings(payload?.warnings || []);
  };

  const load = async () => {
    setLoading(true);
    setError('');
    try {
      const res = await api.get('/admin/settings/storage');
      applyResponse(res.data || {});
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to load storage settings');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const save = async () => {
    setSaving(true);
    setError('');
    setMessage('');
    try {
      const res = await api.patch('/admin/settings/storage', {
        storage_root: data.storage_root,
        kube_image_pvc: data.kube_image_pvc,
        kube_vm_storage_class: data.kube_vm_storage_class,
      });
      applyResponse(res.data || {});
      setMessage('Storage settings saved and applied.');
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to save storage settings');
    } finally {
      setSaving(false);
    }
  };

  const clearOverrides = async () => {
    setClearing(true);
    setError('');
    setMessage('');
    try {
      const res = await api.patch('/admin/settings/storage', { clear_overrides: true });
      applyResponse(res.data || {});
      setMessage('Storage overrides cleared; environment defaults are active.');
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to clear storage overrides');
    } finally {
      setClearing(false);
    }
  };

  return (
    <div>
      <h2>Storage Options</h2>
      <p className="muted small">Configure image storage and verify cluster readiness for clone-based VM launches.</p>
      <div className="actions" style={{ marginBottom: '1rem' }}>
        <button className="ghost" onClick={load} disabled={loading || saving || clearing}>
          {loading ? 'Refreshing...' : 'Refresh'}
        </button>
      </div>
      {error && <div className="error">{error}</div>}
      {message && <div className="info">{message}</div>}

      <div className="card">
        <div className="form" style={{ maxWidth: '640px' }}>
          {fields.map((f) => (
            <label key={f.key}>
              {f.label}
              <input
                value={data[f.key] || ''}
                onChange={(e) => setData({ ...data, [f.key]: e.target.value })}
                disabled={Boolean(f.readOnly)}
              />
              {!f.readOnly && sources[f.key] ? <div className="muted small">Source: {sources[f.key]}</div> : null}
            </label>
          ))}
          <div className="actions">
            <button onClick={save} disabled={saving || clearing || loading}>
              {saving ? 'Saving...' : 'Save'}
            </button>
            <button className="ghost" onClick={clearOverrides} disabled={saving || clearing || loading}>
              {clearing ? 'Clearing...' : 'Use Env Defaults'}
            </button>
          </div>
        </div>
      </div>

      <div className="card">
        <h3>Validation</h3>
        {checks.length === 0 ? (
          <div className="muted small">No validation checks yet.</div>
        ) : (
          <div className="tile-grid" style={{ marginTop: '0.75rem' }}>
            {checks.map((check) => {
              const badge = statusMeta(check.status);
              return (
                <div key={check.key} className="tile template-tile">
                  <div className="tile-header">
                    <h4>{check.title}</h4>
                    <span className={badge.className}>{badge.label}</span>
                  </div>
                  <div className="muted small">{check.detail}</div>
                </div>
              );
            })}
          </div>
        )}
        {warnings.length > 0 && (
          <div style={{ marginTop: '1rem' }}>
            <h4 style={{ marginBottom: '0.5rem' }}>Warnings</h4>
            {warnings.map((item, idx) => (
              <div key={`warn-${idx}`} className="muted small" style={{ marginBottom: '0.35rem' }}>
                - {item}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};

export default AdminStorageSettings;
