import React, { useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../api';

const UserPanel = () => {
  const [templates, setTemplates] = useState([]);
  const [instances, setInstances] = useState([]);
  const [message, setMessage] = useState('');
  const [polling, setPolling] = useState(null);
  const [showIdlePrompt, setShowIdlePrompt] = useState(false);
  const [idleCountdown, setIdleCountdown] = useState(null);
  const idleTimerRef = useRef(null);
  const countdownRef = useRef(null);
  const latestInstanceIdsRef = useRef([]);
  const [sessionEnded, setSessionEnded] = useState(false);
  const idlePromptRef = useRef(false);

  const DEFAULT_IDLE_MINUTES = 30;
  const PROMPT_COUNTDOWN_SECONDS = 300; // 5 minutes

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

  const runningInstances = useMemo(() => instances.filter((i) => i.status === 'running'), [instances]);

  const templateIdleMinutes = (templateId) => {
    const tmpl = templates.find((t) => t.id === templateId);
    return tmpl?.idle_timeout_minutes || DEFAULT_IDLE_MINUTES;
  };

  const activeIdleMinutes = useMemo(() => {
    if (runningInstances.length === 0) return null;
    return Math.min(...runningInstances.map((inst) => templateIdleMinutes(inst.template_id)));
  }, [runningInstances, templates]);

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

  const stopInstances = async (instanceIds) => {
    if (!instanceIds || instanceIds.length === 0) return;
    try {
      const results = await Promise.all(instanceIds.map((id) => api.post(`/user/pods/${id}/stop`).catch((err) => err)));
      const failures = results.filter((r) => r instanceof Error || r?.response?.status >= 400);
      if (failures.length) {
        setMessage('Some idle VMs failed to stop; please check the labs list.');
      } else {
        setMessage('');
      }
      refresh();
    } catch (err) {
      setMessage(err.response?.data?.detail || 'Failed to stop idle VM');
    }
  };

  const deleteInstances = async (instanceIds, reason, keepMessage = false) => {
    if (!instanceIds || instanceIds.length === 0) return;
    try {
      const results = await Promise.all(instanceIds.map((id) => api.delete(`/user/pods/${id}`).catch((err) => err)));
      const failures = results.filter((result) => {
        if (result?.status && result.status < 400) {
          return false;
        }
        const status = result?.response?.status;
        if (status === 404) {
          return false;
        }
        return true;
      });
      if (failures.length) {
        setMessage('Some idle VMs failed to delete; please check the labs list.');
      } else if (!keepMessage) {
        if (reason === 'idle-timeout') {
          setMessage('Session ended due to inactivity.');
        } else {
          setMessage('');
        }
      }
      refresh();
    } catch (err) {
      setMessage(err.response?.data?.detail || 'Failed to delete idle VM');
    }
  };

  const connect = (instance) => {
    if (instance?.console_url) {
      window.open(instance.console_url, '_blank');
    } else {
      setMessage('Console URL not available yet');
    }
  };

  const templateName = (templateId) => templates.find((t) => t.id === templateId)?.name || 'VM';
  const podName = (instance) => `vm-${instance.owner}-${instance.id.slice(0, 8)}`;
  const displayStatus = (status) => (status === 'completed' ? 'stopped' : status);
  const isRunning = (status) => status === 'running';

  const clearIdleTimers = () => {
    if (idleTimerRef.current) {
      clearTimeout(idleTimerRef.current);
      idleTimerRef.current = null;
    }
    if (countdownRef.current) {
      clearInterval(countdownRef.current);
      countdownRef.current = null;
    }
  };

  const startIdleCountdown = (instanceIds) => {
    latestInstanceIdsRef.current = instanceIds;
    idlePromptRef.current = true;
    setShowIdlePrompt(true);
    setSessionEnded(false);
    setIdleCountdown(PROMPT_COUNTDOWN_SECONDS);
    countdownRef.current = setInterval(() => {
      setIdleCountdown((prev) => {
        const next = (prev || PROMPT_COUNTDOWN_SECONDS) - 1;
        if (next <= 0) {
          clearIdleTimers();
          endNow(true);
          return 0;
        }
        return next;
      });
    }, 1000);
  };

  const resetIdleTimer = () => {
    if (idlePromptRef.current) {
      return;
    }
    clearIdleTimers();
    setShowIdlePrompt(false);
    idlePromptRef.current = false;
    setIdleCountdown(null);
    setSessionEnded(false);
    if (!runningInstances.length || !activeIdleMinutes) {
      return;
    }
    const ms = Math.max(1, activeIdleMinutes) * 60 * 1000;
    const targetInstanceIds = runningInstances.map((inst) => inst.id);
    idleTimerRef.current = setTimeout(() => startIdleCountdown(targetInstanceIds), ms);
  };

  useEffect(() => {
    resetIdleTimer();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeIdleMinutes, runningInstances.length]);

  useEffect(() => () => clearIdleTimers(), []);

  useEffect(() => {
    const onActivity = () => resetIdleTimer();
    const events = ['mousemove', 'keydown', 'mousedown', 'touchstart', 'scroll', 'visibilitychange'];
    events.forEach((evt) => window.addEventListener(evt, onActivity));
    return () => events.forEach((evt) => window.removeEventListener(evt, onActivity));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeIdleMinutes, runningInstances.length]);

  useEffect(() => {
    const handleMessage = (event) => {
      const payload = event.data || {};
      if (payload.type === 'idle-stop' && payload.instanceId) {
        if (payload.action === 'delete') {
          deleteInstances([payload.instanceId], payload.reason);
        } else {
          stopInstances([payload.instanceId]);
        }
      }
    };
    window.addEventListener('message', handleMessage);
    return () => window.removeEventListener('message', handleMessage);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const continueSession = () => {
    idlePromptRef.current = false;
    resetIdleTimer();
    refresh();
  };

  const endNow = (auto = false) => {
    clearIdleTimers();
    setShowIdlePrompt(false);
    idlePromptRef.current = false;
    setSessionEnded(true);
    setMessage(auto ? 'Session ended due to inactivity.' : 'Session ended.');
    deleteInstances(latestInstanceIdsRef.current, auto ? 'idle-timeout' : 'user-end', true);
    setTimeout(() => {
      try {
        window.close();
      } catch (err) {
        // ignore if browser blocks close
      }
    }, 1500);
  };

  const formatCountdown = (seconds) => {
    const mins = Math.floor((seconds || 0) / 60)
      .toString()
      .padStart(2, '0');
    const secs = ((seconds || 0) % 60).toString().padStart(2, '0');
    return `${mins}:${secs}`;
  };

  return (
    <div>
      <h2>User</h2>
      {message && <div className="info">{message}</div>}
      {showIdlePrompt && (
        <div className="modal-backdrop">
          <div className="modal">
            <h3>Still using this lab?</h3>
            <p className="muted">
              We have not seen activity for {activeIdleMinutes || DEFAULT_IDLE_MINUTES} minutes. Your running lab(s) will
              stop in {formatCountdown(idleCountdown || PROMPT_COUNTDOWN_SECONDS)} unless you continue.
            </p>
            <div className="actions">
              <button className="ghost" onClick={() => endNow(false)}>
                No, end lab
              </button>
              <button onClick={continueSession}>Yes, continue</button>
            </div>
          </div>
        </div>
      )}
      {sessionEnded && (
        <div className="modal-backdrop">
          <div className="modal">
            <h3>Session ended</h3>
            <p className="muted">Session ended due to inactivity.</p>
          </div>
        </div>
      )}
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
