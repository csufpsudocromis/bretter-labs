import React, { useEffect, useState } from 'react';
import { api } from '../../api';

const fields = [
  { key: 'storage_root', label: 'Storage Root' },
  { key: 'kube_image_pvc', label: 'Image PVC' },
  { key: 'kube_namespace', label: 'Namespace (read-only)', readOnly: true },
];

const AdminStorageSettings = () => {
  const [data, setData] = useState({
    storage_root: '',
    kube_image_pvc: '',
    kube_namespace: '',
  });
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    const load = async () => {
      try {
        const res = await api.get('/admin/settings/runtime');
        setData({
          storage_root: res.data?.storage_root || '',
          kube_image_pvc: res.data?.kube_image_pvc || '',
          kube_namespace: res.data?.kube_namespace || '',
        });
      } catch (err) {
        setError(err.response?.data?.detail || 'Failed to load storage settings');
      }
    };
    load();
  }, []);

  const save = async () => {
    setSaving(true);
    setError('');
    setMessage('');
    try {
      // Patch into the same runtime endpoint (writes overrides).
      await api.patch('/admin/settings/runtime', {
        storage_root: data.storage_root,
        kube_image_pvc: data.kube_image_pvc,
      });
      setMessage('Storage settings updated. Restart pods/backend to apply.');
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to save storage settings');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div>
      <h2>Storage Options</h2>
      <p className="muted small">Update storage root and image PVC for this environment.</p>
      {error && <div className="error">{error}</div>}
      <div className="card">
        <div className="form">
          {fields.map((f) => (
            <label key={f.key}>
              {f.label}
              <input
                value={data[f.key] || ''}
                onChange={(e) => setData({ ...data, [f.key]: e.target.value })}
                disabled={f.readOnly}
              />
            </label>
          ))}
          <div className="actions">
            <button onClick={save} disabled={saving}>
              {saving ? 'Saving…' : 'Save'}
            </button>
          </div>
          {message && <div className="info">{message}</div>}
        </div>
      </div>
    </div>
  );
};

export default AdminStorageSettings;
