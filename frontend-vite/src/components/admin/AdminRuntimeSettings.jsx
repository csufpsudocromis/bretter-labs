import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../../api';

const sections = [
  {
    title: 'Storage',
    advanced: false,
    fields: ['storage_root', 'kube_namespace', 'kube_image_pvc', 'kube_vm_storage_class'],
  },
  {
    title: 'Scheduling and VM',
    advanced: false,
    fields: ['kube_node_selector_key', 'kube_node_selector_value', 'kube_use_kvm', 'kube_runtime_class'],
  },
  {
    title: 'Advanced Runtime',
    advanced: true,
    fields: ['runner_image', 'image_pull_secret', 'kube_spice_embed_configmap', 'kube_node_external_host'],
  },
];

const labels = {
  storage_root: 'Storage Root',
  kube_namespace: 'Kubernetes Namespace',
  kube_image_pvc: 'Image PVC',
  kube_runtime_class: 'RuntimeClass',
  kube_vm_storage_class: 'VM Clone StorageClass',
  runner_image: 'Runner Image',
  image_pull_secret: 'Image Pull Secret',
  kube_node_selector_key: 'Node Selector Key',
  kube_node_selector_value: 'Node Selector Value',
  kube_use_kvm: 'Use KVM',
  kube_spice_embed_configmap: 'SPICE Embed ConfigMap',
  kube_node_external_host: 'External Node Host',
};

const sensitiveFields = new Set(['image_pull_secret']);

const healthMeta = (status) => {
  const normalized = String(status || '').toLowerCase();
  if (normalized === 'critical') return { label: 'Critical', className: 'badge warn' };
  if (normalized === 'warning') return { label: 'Warning', className: 'badge warn' };
  if (normalized === 'healthy') return { label: 'Healthy', className: 'badge success' };
  return { label: 'Unknown', className: 'badge' };
};

const checkMeta = (status) => {
  const normalized = String(status || '').toLowerCase();
  if (normalized === 'error') return { label: 'Error', className: 'badge warn' };
  if (normalized === 'warn') return { label: 'Warn', className: 'badge warn' };
  if (normalized === 'ok') return { label: 'OK', className: 'badge success' };
  return { label: 'Info', className: 'badge' };
};

const shellQuote = (value) => `'${String(value ?? '').replace(/'/g, `'"'"'`)}'`;

const AdminRuntimeSettings = () => {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [loading, setLoading] = useState(false);
  const [showSensitive, setShowSensitive] = useState(false);

  const load = async () => {
    setLoading(true);
    setError('');
    try {
      const res = await api.get('/admin/settings/runtime');
      setData(res.data || {});
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to load runtime settings');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const copyText = async (value, successMessage) => {
    try {
      await navigator.clipboard.writeText(String(value ?? ''));
      setMessage(successMessage);
    } catch (err) {
      setError('Clipboard copy failed in this browser/session.');
    }
  };

  const copyAllAsEnv = async () => {
    if (!data) return;
    const envNames = data.env_names || {};
    const lines = Object.keys(labels)
      .filter((key) => envNames[key])
      .map((key) => `export ${envNames[key]}=${shellQuote(data[key])}`);
    await copyText(lines.join('\n'), 'Copied runtime values as shell exports.');
  };

  const renderValue = (key) => {
    const raw = data?.[key];
    if (typeof raw === 'boolean') {
      return raw ? 'true' : 'false';
    }
    const value = String(raw ?? '');
    if (!showSensitive && sensitiveFields.has(key) && value) {
      return '********';
    }
    return value;
  };

  const banner = healthMeta(data?.health_status);

  return (
    <div>
      <h2>Runtime Settings</h2>
      <p className="muted small">Read-only backend runtime configuration with health and drift checks.</p>

      <div className="actions" style={{ marginBottom: '1rem', flexWrap: 'wrap' }}>
        <button className="ghost" onClick={load} disabled={loading}>
          {loading ? 'Refreshing...' : 'Refresh Now'}
        </button>
        <button className="ghost" onClick={copyAllAsEnv} disabled={!data || loading}>
          Copy All as Env
        </button>
        <button className="ghost" onClick={() => setShowSensitive((v) => !v)} disabled={!data}>
          {showSensitive ? 'Hide Sensitive' : 'Show Sensitive'}
        </button>
        <Link to="/admin/settings/storage" className="tile" style={{ padding: '0.5rem 0.75rem' }}>
          Storage Settings
        </Link>
        <Link to="/admin/resources" className="tile" style={{ padding: '0.5rem 0.75rem' }}>
          Cluster Resources
        </Link>
      </div>

      {error && <div className="error">{error}</div>}
      {message && <div className="info">{message}</div>}

      {data && (
        <>
          <div className="card" style={{ marginBottom: '1rem' }}>
            <div className="tile-header">
              <h3>Runtime Health</h3>
              <span className={banner.className}>{banner.label}</span>
            </div>
            <div className="specs">
              <span>Backend Pods: {data.backend_pod_count || 0}</span>
              <span>Drift Items: {(data.drift || []).length}</span>
              <span>Checks: {(data.health_checks || []).length}</span>
            </div>
            <div className="tile-grid" style={{ marginTop: '0.75rem' }}>
              {(data.health_checks || []).map((check) => {
                const meta = checkMeta(check.status);
                return (
                  <div key={check.key} className="tile template-tile">
                    <div className="tile-header">
                      <h4>{check.title}</h4>
                      <span className={meta.className}>{meta.label}</span>
                    </div>
                    <div className="muted small">{check.detail}</div>
                  </div>
                );
              })}
            </div>
          </div>

          <div className="card" style={{ marginBottom: '1rem' }}>
            <h3>Runtime Drift</h3>
            {(data.drift || []).length === 0 ? (
              <div className="muted small">No drift detected between effective settings and backend pod env.</div>
            ) : (
              <div className="tile-grid" style={{ marginTop: '0.75rem' }}>
                {(data.drift || []).map((item, idx) => (
                  <div key={`${item.pod_name}-${item.env_var}-${idx}`} className="tile template-tile">
                    <div className="tile-header">
                      <h4>{item.field_key}</h4>
                      <span className="badge warn">Drift</span>
                    </div>
                    <div className="muted small">Pod: {item.pod_name}</div>
                    <div className="muted small">Env: {item.env_var}</div>
                    <div className="muted small">Configured: {item.configured_value}</div>
                    <div className="muted small">Pod Value: {item.pod_value}</div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {sections.map((section) => {
            const body = (
              <div className="tile-grid" style={{ marginTop: '0.75rem' }}>
                {section.fields.map((key) => (
                  <div key={key} className="tile template-tile">
                    <div className="tile-header">
                      <h4>{labels[key]}</h4>
                      <span className="badge">{data.sources?.[key] || 'environment'}</span>
                    </div>
                    <div className="muted small" style={{ marginBottom: '0.3rem' }}>
                      Value: {renderValue(key) || '(empty)'}
                    </div>
                    <div className="muted small">Env: {data.env_names?.[key] || 'n/a'}</div>
                    <div className="muted small">Apply: {data.apply_behavior?.[key] || 'Environment controlled.'}</div>
                    <div className="actions" style={{ marginTop: '0.6rem' }}>
                      <button
                        className="ghost"
                        onClick={() => copyText(data?.[key], `Copied ${labels[key]} value.`)}
                      >
                        Copy Value
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            );

            if (!section.advanced) {
              return (
                <div className="card" key={section.title}>
                  <h3>{section.title}</h3>
                  {body}
                </div>
              );
            }

            return (
              <div className="card" key={section.title}>
                <details>
                  <summary style={{ cursor: 'pointer', fontWeight: 700 }}>{section.title}</summary>
                  {body}
                </details>
              </div>
            );
          })}
        </>
      )}
    </div>
  );
};

export default AdminRuntimeSettings;
