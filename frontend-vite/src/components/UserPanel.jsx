import React, { useEffect, useState } from 'react';
import { api } from '../api';

const UserPanel = () => {
  const [templates, setTemplates] = useState([]);
  const [instances, setInstances] = useState([]);
  const [message, setMessage] = useState('');
  const [polling, setPolling] = useState(null);

  const refresh = async () => {
    try {
      const [tmplRes, podsRes] = await Promise.all([api.get('/user/templates'), api.get('/user/pods')]);
      setTemplates(tmplRes.data);
      setInstances(podsRes.data);
    } catch (err) {
      setMessage(err.response?.data?.detail || 'Failed to load data');
    }
  };

  useEffect(() => {
    refresh();
    const handle = setInterval(refresh, 5000);
    setPolling(handle);
    return () => clearInterval(handle);
  }, []);

  const start = async (templateId) => {
    try {
      const res = await api.post(`/user/templates/${templateId}/start`);
      setMessage('');
      refresh();
    } catch (err) {
      setMessage(err.response?.data?.detail || 'Failed to start VM');
    }
  };

  const stop = async (instanceId) => {
    try {
      await api.post(`/user/pods/${instanceId}/stop`);
      setMessage('');
      refresh();
    } catch (err) {
      setMessage(err.response?.data?.detail || 'Failed to stop VM');
    }
  };

  const remove = async (instanceId) => {
    try {
      await api.delete(`/user/pods/${instanceId}`);
      setMessage('');
      refresh();
    } catch (err) {
      setMessage(err.response?.data?.detail || 'Failed to delete VM');
    }
  };

  const connect = (instance) => {
    if (instance?.console_url) {
      window.open(instance.console_url, '_blank', 'noopener,noreferrer');
    } else {
      setMessage('Console URL not available yet');
    }
  };

  const templateName = (templateId) => templates.find((t) => t.id === templateId)?.name || 'VM';
  const podName = (instance) => `vm-${instance.owner}-${instance.id.slice(0, 8)}`;
  const displayStatus = (status) => (status === 'completed' ? 'stopped' : status);
  const isRunning = (status) => status === 'running';

  return (
    <div>
      <h2>User</h2>
      {message && <div className="info">{message}</div>}
      <div className="grid">
        <div>
          <h3>Available Virtual Labs</h3>
          <div className="tile-grid">
            {templates.length === 0 && <div className="muted">No templates available.</div>}
            {templates.map((t) => (
              <div key={t.id} className="tile template-tile">
                <div className="tile-header">
                  <h4>{t.name}</h4>
                </div>
                {t.description && <div className="muted small">{t.description}</div>}
                <div style={{ marginTop: '0.75rem' }}>
                  <button onClick={() => start(t.id)}>Start Lab</button>
                </div>
              </div>
            ))}
          </div>
        </div>
        <div>
          <h3>My Running Labs</h3>
          <div className="tile-grid">
            {instances.length === 0 && <div className="muted">No labs yet. Start a lab to see it here.</div>}
            {instances.map((p) => (
              <div key={p.id} className="tile pod-tile">
                <div className="tile-header">
                  <h4>{templateName(p.template_id)}</h4>
                  <span className={`badge ${isRunning(p.status) ? 'success' : 'warn'}`}>{displayStatus(p.status)}</span>
                </div>
                <div className="specs">
                  <span>{podName(p)}</span>
                </div>
                <div className="actions">
                  <button className="ghost" onClick={() => remove(p.id)}>
                    Delete
                  </button>
                  <button onClick={() => connect(p)} disabled={p.status !== 'running'}>
                    Connect
                  </button>
                </div>
                {!p.console_url && <div className="muted small">Console pending...</div>}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
};

export default UserPanel;
