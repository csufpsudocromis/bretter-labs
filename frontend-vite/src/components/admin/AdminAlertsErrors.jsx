import React, { useEffect, useState } from 'react';

import { api } from '../../api';

const fmtDateTime = (value) => {
  if (!value) return 'n/a';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
};

const AdminAlertsErrors = () => {
  const [data, setData] = useState(null);
  const [message, setMessage] = useState('');
  const [loading, setLoading] = useState(false);

  const load = async () => {
    setLoading(true);
    setMessage('');
    try {
      const res = await api.get('/admin/alerts-errors');
      setData(res.data);
    } catch (err) {
      setMessage(err.response?.data?.detail || 'Failed to load alerts and errors.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  return (
    <div>
      <h2>Alerts and Errors</h2>
      <div className="actions" style={{ marginBottom: '1rem' }}>
        <button className="ghost" onClick={load} disabled={loading}>
          {loading ? 'Refreshing...' : 'Refresh'}
        </button>
      </div>
      {message && <div className="error">{message}</div>}
      {data && (
        <>
          <div className="card" style={{ marginBottom: '1rem' }}>
            <h3>Alertmanager Alerts</h3>
            <div className="muted small">Fetched: {fmtDateTime(data.fetched_at)}</div>
            <div className="muted small">Source: {data.alertmanager_url || 'not configured'}</div>
            {data.alertmanager_error && <div className="error">{data.alertmanager_error}</div>}
            {!data.alertmanager_error && data.alerts.length === 0 && (
              <div className="muted small">No active Alertmanager messages.</div>
            )}
            {data.alerts.length > 0 && (
              <div className="tile-grid" style={{ marginTop: '0.75rem' }}>
                {data.alerts.map((alert, idx) => (
                  <div className="tile template-tile" key={`${alert.name}-${alert.starts_at || idx}`}>
                    <div className="tile-header">
                      <h4>{alert.summary || alert.name}</h4>
                      <span className={`badge ${alert.state === 'active' ? 'warn' : 'success'}`}>{alert.state}</span>
                    </div>
                    <div className="specs">
                      <span>name: {alert.name}</span>
                      <span>severity: {alert.severity || 'n/a'}</span>
                      <span>starts: {fmtDateTime(alert.starts_at)}</span>
                    </div>
                    {alert.description && <div className="muted small">{alert.description}</div>}
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="card">
            <h3>Error Logs</h3>
            <div className="muted small">Source: {data.error_log.source}</div>
            <div className="muted small">
              Size shown: {(data.error_log.bytes / (1024 * 1024)).toFixed(2)} MB (max 10.00 MB)
            </div>
            {data.error_log.truncated && <div className="muted small">Output was truncated to the latest 10MB.</div>}
            <pre
              style={{
                marginTop: '0.75rem',
                maxHeight: '28rem',
                overflowY: 'auto',
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
              }}
            >
              {data.error_log.content || 'No error log lines found.'}
            </pre>
          </div>
        </>
      )}
    </div>
  );
};

export default AdminAlertsErrors;
