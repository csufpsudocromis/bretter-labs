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
  const countdownEndsAtRef = useRef(null);
  const idleStartsAtRef = useRef(null);
  const lastActivityAtRef = useRef(null);
  const consoleWindowsRef = useRef({});
  const consoleHandshakeRef = useRef({});
  const idleSuspendedRef = useRef(false);
  const latestInstanceIdsRef = useRef([]);
  const [sessionEnded, setSessionEnded] = useState(false);
  const idlePromptRef = useRef(false);

  const DEFAULT_IDLE_MINUTES = 30;
  const PROMPT_COUNTDOWN_SECONDS = 300; // 5 minutes
  const ACTIVITY_STORAGE_KEY = 'blabs:last-activity-at';

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

  const activeInstances = useMemo(
    () => instances.filter((i) => i.status === 'running' || i.status === 'pending'),
    [instances],
  );

  useEffect(() => {
    latestInstanceIdsRef.current = activeInstances.map((inst) => inst.id);
  }, [activeInstances]);

  const templateIdleMinutes = (templateId) => {
    const tmpl = templates.find((t) => t.id === templateId);
    return tmpl?.idle_timeout_minutes || DEFAULT_IDLE_MINUTES;
  };

  const activeIdleMinutes = useMemo(() => {
    if (activeInstances.length === 0) return null;
    return Math.min(...activeInstances.map((inst) => templateIdleMinutes(inst.template_id)));
  }, [activeInstances, templates]);

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
      const win = window.open(instance.console_url, '_blank');
      if (win && instance?.id) {
        consoleWindowsRef.current[instance.id] = win;
        startConsoleHandshake(instance.id, win);
        if (document.hasFocus()) {
          try {
            win.postMessage(
              { type: 'idle-focus', source: 'user', instanceId: instance.id, timestamp: Date.now() },
              '*',
            );
          } catch (err) {
            // ignore postMessage failures
          }
        }
      }
    } else {
      setMessage('Console URL not available yet');
    }
  };

  const templateName = (templateId) => templates.find((t) => t.id === templateId)?.name || 'VM';
  const podName = (instance) => `vm-${instance.owner}-${instance.id.slice(0, 8)}`;
  const displayStatus = (status) => (status === 'completed' ? 'stopped' : status);
  const isRunning = (status) => status === 'running';

  const readStoredActivity = () => {
    try {
      const stored = sessionStorage.getItem(ACTIVITY_STORAGE_KEY);
      const parsed = stored ? Number(stored) : NaN;
      return Number.isFinite(parsed) ? parsed : null;
    } catch (err) {
      return null;
    }
  };

  const writeStoredActivity = (timestamp) => {
    try {
      sessionStorage.setItem(ACTIVITY_STORAGE_KEY, String(timestamp));
    } catch (err) {
      // ignore storage failures
    }
  };

  const clearStoredActivity = () => {
    try {
      sessionStorage.removeItem(ACTIVITY_STORAGE_KEY);
    } catch (err) {
      // ignore storage failures
    }
  };

  const stopConsoleHandshake = (instanceId) => {
    const timers = consoleHandshakeRef.current;
    if (timers[instanceId]) {
      clearInterval(timers[instanceId]);
      delete timers[instanceId];
    }
  };

  const startConsoleHandshake = (instanceId, win) => {
    if (!instanceId || !win) {
      return;
    }
    const send = () => {
      if (!win || win.closed) {
        delete consoleWindowsRef.current[instanceId];
        stopConsoleHandshake(instanceId);
        return;
      }
      try {
        win.postMessage({ type: 'idle-handshake', source: 'user', instanceId }, '*');
      } catch (err) {
        // ignore postMessage failures
      }
    };
    send();
    if (!consoleHandshakeRef.current[instanceId]) {
      consoleHandshakeRef.current[instanceId] = setInterval(send, 1000);
    }
  };

  const broadcastActivityToConsoles = (timestamp) => {
    const windows = consoleWindowsRef.current;
    Object.entries(windows).forEach(([id, win]) => {
      if (!win || win.closed) {
        delete windows[id];
        stopConsoleHandshake(id);
        return;
      }
      try {
        win.postMessage({ type: 'idle-activity', source: 'user', timestamp }, '*');
      } catch (err) {
        // ignore postMessage failures
      }
    });
  };

  const broadcastFocusToConsoles = (focused) => {
    const windows = consoleWindowsRef.current;
    const timestamp = Date.now();
    Object.entries(windows).forEach(([id, win]) => {
      if (!win || win.closed) {
        delete windows[id];
        stopConsoleHandshake(id);
        return;
      }
      try {
        win.postMessage(
          { type: focused ? 'idle-focus' : 'idle-blur', source: 'user', instanceId: id, timestamp },
          '*',
        );
      } catch (err) {
        // ignore postMessage failures
      }
    });
  };

  const clearIdleTimers = (resetIdleStart = true) => {
    if (idleTimerRef.current) {
      clearTimeout(idleTimerRef.current);
      idleTimerRef.current = null;
    }
    if (countdownRef.current) {
      clearInterval(countdownRef.current);
      countdownRef.current = null;
    }
    countdownEndsAtRef.current = null;
    if (resetIdleStart) {
      idleStartsAtRef.current = null;
    }
  };

  const clearIdlePrompt = () => {
    setShowIdlePrompt(false);
    idlePromptRef.current = false;
    setIdleCountdown(null);
  };

  const scheduleIdleTimer = () => {
    if (idleTimerRef.current) {
      clearTimeout(idleTimerRef.current);
      idleTimerRef.current = null;
    }
    if (idleSuspendedRef.current) {
      return;
    }
    const idleStartsAt = idleStartsAtRef.current;
    if (!idleStartsAt) {
      return;
    }
    const delay = Math.max(0, idleStartsAt - Date.now());
    idleTimerRef.current = setTimeout(() => startIdleCountdown(idleStartsAt), delay);
  };

  const updateCountdown = () => {
    if (!idlePromptRef.current) {
      return;
    }
    const endsAt = countdownEndsAtRef.current;
    if (!endsAt) {
      return;
    }
    const remainingSeconds = Math.max(0, Math.ceil((endsAt - Date.now()) / 1000));
    setIdleCountdown(remainingSeconds);
    if (remainingSeconds <= 0) {
      endNow(true);
    }
  };

  const startIdleCountdown = (startedAt, instanceIds = latestInstanceIdsRef.current) => {
    if (!instanceIds || instanceIds.length === 0) {
      return;
    }
    latestInstanceIdsRef.current = instanceIds;
    idlePromptRef.current = true;
    setShowIdlePrompt(true);
    setSessionEnded(false);
    const baseline = startedAt || idleStartsAtRef.current || Date.now();
    countdownEndsAtRef.current = baseline + PROMPT_COUNTDOWN_SECONDS * 1000;
    const remainingSeconds = Math.max(0, Math.ceil((countdownEndsAtRef.current - Date.now()) / 1000));
    setIdleCountdown(remainingSeconds);
    if (countdownRef.current) {
      clearInterval(countdownRef.current);
    }
    countdownRef.current = setInterval(updateCountdown, 1000);
    updateCountdown();
  };

  const updateIdleStartFromActivity = (activityAt) => {
    if (!activeIdleMinutes) {
      return;
    }
    idleStartsAtRef.current = activityAt + Math.max(1, activeIdleMinutes) * 60 * 1000;
  };

  const ensureActivityTimestamp = () => {
    if (lastActivityAtRef.current) {
      return lastActivityAtRef.current;
    }
    const stored = readStoredActivity();
    const fallback = stored || Date.now();
    lastActivityAtRef.current = fallback;
    if (!stored) {
      writeStoredActivity(fallback);
    }
    return fallback;
  };

  const recordActivity = ({ emit = true, timestamp } = {}) => {
    if (idleSuspendedRef.current) {
      return;
    }
    if (idlePromptRef.current) {
      return;
    }
    if (!activeInstances.length || !activeIdleMinutes) {
      return;
    }
    const now = timestamp || Date.now();
    lastActivityAtRef.current = now;
    writeStoredActivity(now);
    updateIdleStartFromActivity(now);
    scheduleIdleTimer();
    if (emit) {
      broadcastActivityToConsoles(now);
    }
  };

  const handleExternalActivity = (timestamp) => {
    if (!activeInstances.length || !activeIdleMinutes) {
      return;
    }
    const now = timestamp || Date.now();
    suspendIdle(now);
  };

  const suspendIdle = (timestamp) => {
    idleSuspendedRef.current = true;
    clearIdleTimers();
    clearIdlePrompt();
    setSessionEnded(false);
    if (timestamp) {
      lastActivityAtRef.current = timestamp;
      writeStoredActivity(timestamp);
      updateIdleStartFromActivity(timestamp);
    }
  };

  const resumeIdle = (timestamp) => {
    idleSuspendedRef.current = false;
    if (!activeInstances.length || !activeIdleMinutes) {
      return;
    }
    const now = timestamp || Date.now();
    lastActivityAtRef.current = now;
    writeStoredActivity(now);
    updateIdleStartFromActivity(now);
    scheduleIdleTimer();
  };

  const syncIdleState = () => {
    if (idleSuspendedRef.current) {
      clearIdleTimers(false);
      clearIdlePrompt();
      return;
    }
    if (idlePromptRef.current) {
      updateCountdown();
      return;
    }
    if (!activeInstances.length || !activeIdleMinutes) {
      clearIdleTimers();
      clearIdlePrompt();
      idleStartsAtRef.current = null;
      lastActivityAtRef.current = null;
      clearStoredActivity();
      return;
    }
    const activityAt = ensureActivityTimestamp();
    updateIdleStartFromActivity(activityAt);
    if (idleStartsAtRef.current && Date.now() >= idleStartsAtRef.current) {
      startIdleCountdown(idleStartsAtRef.current);
      return;
    }
    scheduleIdleTimer();
  };

  useEffect(() => {
    syncIdleState();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeIdleMinutes, activeInstances.length]);

  useEffect(() => () => clearIdleTimers(), []);

  useEffect(() => {
    const onActivity = () => {
      if (document.hidden) {
        return;
      }
      if (!activeInstances.length || !activeIdleMinutes) {
        return;
      }
      const now = Date.now();
      if (idleSuspendedRef.current) {
        idleSuspendedRef.current = false;
        recordActivity({ emit: true, timestamp: now });
        return;
      }
      const lastActivity = lastActivityAtRef.current || readStoredActivity();
      if (lastActivity) {
        const idleStart = lastActivity + Math.max(1, activeIdleMinutes) * 60 * 1000;
        if (now >= idleStart) {
          idleStartsAtRef.current = idleStart;
          startIdleCountdown(idleStart);
          return;
        }
      }
      recordActivity();
    };
    const onFocus = () => {
      idleSuspendedRef.current = false;
      broadcastFocusToConsoles(true);
      recordActivity({ emit: true });
      syncIdleState();
    };
    const onBlur = () => {
      broadcastFocusToConsoles(false);
    };
    const onVisibility = () => {
      if (!document.hidden) {
        syncIdleState();
      }
    };
    const events = ['mousemove', 'keydown', 'mousedown', 'touchstart', 'scroll'];
    events.forEach((evt) => window.addEventListener(evt, onActivity, { passive: true }));
    window.addEventListener('focus', onFocus);
    window.addEventListener('blur', onBlur);
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      events.forEach((evt) => window.removeEventListener(evt, onActivity));
      window.removeEventListener('focus', onFocus);
      window.removeEventListener('blur', onBlur);
      document.removeEventListener('visibilitychange', onVisibility);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeIdleMinutes, activeInstances.length]);

  useEffect(() => {
    const handleMessage = (event) => {
      const payload = event.data || {};
      if (payload.type === 'idle-focus' && payload.source === 'vm') {
        const ts = Number.isFinite(payload.timestamp) ? payload.timestamp : Date.now();
        suspendIdle(ts);
        return;
      }
      if (payload.type === 'idle-blur' && payload.source === 'vm') {
        const ts = Number.isFinite(payload.timestamp) ? payload.timestamp : Date.now();
        resumeIdle(ts);
        return;
      }
      if (payload.type === 'idle-activity' && payload.source === 'vm') {
        const ts = Number.isFinite(payload.timestamp) ? payload.timestamp : Date.now();
        handleExternalActivity(ts);
        return;
      }
      if (payload.type === 'idle-handshake-ack' && payload.source === 'vm' && payload.instanceId) {
        stopConsoleHandshake(payload.instanceId);
        return;
      }
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
    clearIdlePrompt();
    setSessionEnded(false);
    idleSuspendedRef.current = false;
    recordActivity();
    refresh();
  };

  const endNow = (auto = false) => {
    clearIdleTimers();
    clearIdlePrompt();
    setSessionEnded(true);
    setMessage(auto ? 'Session ended due to inactivity.' : 'Session ended.');
    deleteInstances(latestInstanceIdsRef.current, auto ? 'idle-timeout' : 'user-end', true);
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
            <div className="actions">
              <button onClick={() => setSessionEnded(false)}>OK</button>
            </div>
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
