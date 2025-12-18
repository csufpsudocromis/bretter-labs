import React, { useEffect, useState } from 'react';
import { api } from '../../api';

const fields = [
  { key: 'storage_root', label: 'Storage Root' },
  { key: 'kube_namespace', label: 'Kubernetes Namespace' },
  { key: 'kube_image_pvc', label: 'Image PVC' },
  { key: 'kube_runtime_class', label: 'RuntimeClass' },
  { key: 'runner_image', label: 'Runner Image' },
  { key: 'image_pull_secret', label: 'Image Pull Secret' },
  { key: 'kube_node_selector_key', label: 'Node Selector Key' },
  { key: 'kube_node_selector_value', label: 'Node Selector Value' },
  { key: 'kube_use_kvm', label: 'Use KVM' },
  { key: 'kube_spice_embed_configmap', label: 'SPICE Embed ConfigMap' },
  { key: 'kube_node_external_host', label: 'External Node Host' },
];

const AdminRuntimeSettings = () => {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');

  useEffect(() => {
    const load = async () => {
      try {
        const res = await api.get('/admin/settings/runtime');
        setData(res.data || {});
      } catch (err) {
        setError(err.response?.data?.detail || 'Failed to load runtime settings');
      }
    };
    load();
  }, []);

  return (
    <div>
      <h2>Runtime Settings</h2>
      <p className="muted small">Read-only values from the backend configuration.</p>
      {error && <div className="error">{error}</div>}
      <div className="card">
        {data ? (
          <div className="grid">
            {fields.map((f) => (
              <div key={f.key} className="specs">
                <strong>{f.label}:</strong> <span>{String(data[f.key] ?? '')}</span>
              </div>
            ))}
          </div>
        ) : (
          <p className="muted small">Loading…</p>
        )}
      </div>
    </div>
  );
};

export default AdminRuntimeSettings;
