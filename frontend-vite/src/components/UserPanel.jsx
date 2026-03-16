import React, { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";

const UserPanel = () => {
  const SINGLE_LAB_LIMIT_MESSAGE =
    "You already have a virtual lab running. Delete the current lab before starting a new one.";
  const [templates, setTemplates] = useState([]);
  const [instances, setInstances] = useState([]);
  const [containerTemplates, setContainerTemplates] = useState([]);
  const [containerInstances, setContainerInstances] = useState([]);
  const [message, setMessage] = useState("");
  const [polling, setPolling] = useState(null);
  const [showIdlePrompt, setShowIdlePrompt] = useState(false);
  const [idleCountdown, setIdleCountdown] = useState(null);
  const idleTimerRef = useRef(null);
  const countdownRef = useRef(null);
  const countdownEndsAtRef = useRef(null);
  const idleStartsAtRef = useRef(null);
  const lastActivityAtRef = useRef(null);
  const consoleWindowsRef = useRef({});
  const consoleWindowOriginsRef = useRef({});
  const allowedMessageOriginsRef = useRef(new Set());
  const containerWindowIdsRef = useRef(new Set());
  const consoleHandshakeRef = useRef({});
  const idleSuspendedRef = useRef(false);
  const idleSuspendReasonRef = useRef(null);
  const vmPresenceAtRef = useRef(null);
  const vmParentPromptActiveRef = useRef(false);
  const activeIdleMinutesRef = useRef(null);
  const activeWorkloadCountRef = useRef(0);
  const latestVmInstanceIdsRef = useRef([]);
  const latestContainerInstanceIdsRef = useRef([]);
  const stickyLimitMessageRef = useRef(false);
  const [sessionEnded, setSessionEnded] = useState(false);
  const idlePromptRef = useRef(false);

  const DEFAULT_IDLE_MINUTES = 30;
  const PROMPT_COUNTDOWN_SECONDS = 300; // 5 minutes
  const VM_PRESENCE_GRACE_MS = 10000;
  const ACTIVITY_STORAGE_KEY = "blabs:last-activity-at";

  const normalizeOrigin = (value) => {
    try {
      return new URL(String(value || ""), window.location.href).origin;
    } catch (err) {
      return "";
    }
  };

  const rememberAllowedOrigin = (value) => {
    const origin = normalizeOrigin(value);
    if (origin) {
      allowedMessageOriginsRef.current.add(origin);
    }
    return origin;
  };

  const isSameHostOrigin = (origin) => {
    if (!origin) {
      return false;
    }
    try {
      const parsed = new URL(origin);
      return parsed.protocol === window.location.protocol && parsed.hostname === window.location.hostname;
    } catch (err) {
      return false;
    }
  };

  const isKnownActiveInstanceId = (instanceId) => {
    if (!instanceId) {
      return false;
    }
    return (
      latestVmInstanceIdsRef.current.includes(instanceId) || latestContainerInstanceIdsRef.current.includes(instanceId)
    );
  };

  const refresh = async () => {
    try {
      const [tmplRes, podsRes, ctTmplRes, ctInstRes] = await Promise.all([
        api.get("/user/templates"),
        api.get("/user/pods"),
        api.get("/user/container-templates"),
        api.get("/user/containers"),
      ]);
      const nextVmInstances = podsRes.data || [];
      const nextContainerInstances = ctInstRes.data || [];
      setTemplates(tmplRes.data);
      setInstances(nextVmInstances);
      setContainerTemplates(ctTmplRes.data || []);
      setContainerInstances(nextContainerInstances);
      nextVmInstances.forEach((inst) => rememberAllowedOrigin(inst?.console_url));
      nextContainerInstances.forEach((inst) => {
        rememberAllowedOrigin(inst?.access_url);
      });

      const hasActiveLab = [...nextVmInstances, ...nextContainerInstances].some((inst) => {
        const statusText = String(inst?.status || "").toLowerCase();
        return !["stopped", "completed", "failed"].includes(statusText);
      });
      if (!hasActiveLab) {
        stickyLimitMessageRef.current = false;
      }
      if (stickyLimitMessageRef.current) {
        setMessage(SINGLE_LAB_LIMIT_MESSAGE);
      } else {
        setMessage("");
      }
    } catch (err) {
      setMessage(err.response?.data?.detail || "Failed to load data");
    }
  };

  useEffect(() => {
    refresh();
    const handle = setInterval(refresh, 5000);
    setPolling(handle);
    return () => clearInterval(handle);
  }, []);

  useEffect(() => {
    rememberAllowedOrigin(window.location.origin);
    rememberAllowedOrigin(api?.defaults?.baseURL || "");
  }, []);

  const ACTIVE_WORKLOAD_STATUSES = new Set(["queued", "pending", "building", "starting", "running"]);
  const workloadStatus = (inst) => String(inst?.status_stage || inst?.status || "unknown").toLowerCase();

  const activeVmInstances = useMemo(
    () => instances.filter((i) => ACTIVE_WORKLOAD_STATUSES.has(workloadStatus(i))),
    [instances]
  );
  const activeContainerInstances = useMemo(
    () => containerInstances.filter((i) => ACTIVE_WORKLOAD_STATUSES.has(workloadStatus(i))),
    [containerInstances]
  );
  const activeWorkloadCount = activeVmInstances.length + activeContainerInstances.length;

  useEffect(() => {
    latestVmInstanceIdsRef.current = activeVmInstances.map((inst) => inst.id);
  }, [activeVmInstances]);
  useEffect(() => {
    latestContainerInstanceIdsRef.current = activeContainerInstances.map((inst) => inst.id);
  }, [activeContainerInstances]);

  const templateIdleMinutes = (templateId) => {
    const tmpl = templates.find((t) => t.id === templateId);
    return tmpl?.idle_timeout_minutes || DEFAULT_IDLE_MINUTES;
  };
  const containerTemplateIdleMinutes = (templateId) => {
    const tmpl = containerTemplates.find((t) => t.id === templateId);
    return tmpl?.idle_timeout_minutes || DEFAULT_IDLE_MINUTES;
  };

  const activeIdleMinutes = useMemo(() => {
    const vmIdleMinutes = activeVmInstances.map((inst) => templateIdleMinutes(inst.template_id));
    const containerIdleMinutes = activeContainerInstances.map((inst) => containerTemplateIdleMinutes(inst.template_id));
    const allIdleMinutes = [...vmIdleMinutes, ...containerIdleMinutes];
    if (allIdleMinutes.length === 0) return null;
    return Math.min(...allIdleMinutes);
  }, [activeVmInstances, activeContainerInstances, templates, containerTemplates]);

  useEffect(() => {
    activeIdleMinutesRef.current = activeIdleMinutes;
    activeWorkloadCountRef.current = activeWorkloadCount;
  }, [activeIdleMinutes, activeWorkloadCount]);

  const start = async (templateId) => {
    try {
      const res = await api.post(`/user/templates/${templateId}/start`);
      setMessage("");
      refresh();
    } catch (err) {
      const detail = err.response?.data?.detail || "Failed to start VM";
      if (detail === SINGLE_LAB_LIMIT_MESSAGE) {
        stickyLimitMessageRef.current = true;
      }
      setMessage(detail);
    }
  };

  const stop = async (instanceId) => {
    try {
      await api.post(`/user/pods/${instanceId}/stop`);
      setMessage("");
      refresh();
    } catch (err) {
      setMessage(err.response?.data?.detail || "Failed to stop VM");
    }
  };

  const remove = async (instanceId) => {
    try {
      await api.delete(`/user/pods/${instanceId}`);
      setMessage("");
      refresh();
    } catch (err) {
      setMessage(err.response?.data?.detail || "Failed to delete VM");
    }
  };

  const startContainer = async (templateId) => {
    try {
      await api.post(`/user/container-templates/${templateId}/start`);
      setMessage("");
      refresh();
    } catch (err) {
      const detail = err.response?.data?.detail || "Failed to start container";
      if (detail === SINGLE_LAB_LIMIT_MESSAGE) {
        stickyLimitMessageRef.current = true;
      }
      setMessage(detail);
    }
  };

  const removeContainer = async (instanceId) => {
    try {
      await api.delete(`/user/containers/${instanceId}`);
      setMessage("");
      refresh();
    } catch (err) {
      setMessage(err.response?.data?.detail || "Failed to delete container");
    }
  };

  const openContainer = async (instance) => {
    if (!instance?.id) return;
    try {
      const res = await api.post(`/user/containers/${instance.id}/connect-token`);
      const connectUrl = String(res?.data?.connect_url || "").trim();
      const fallbackUrl = String(instance.access_url || "").trim();
      const launchUrl = connectUrl || fallbackUrl;
      const win = window.open(launchUrl, "_blank");
      if (win) {
        const preferredOrigin = rememberAllowedOrigin(connectUrl) || rememberAllowedOrigin(fallbackUrl);
        if (preferredOrigin) {
          consoleWindowOriginsRef.current[instance.id] = preferredOrigin;
        }
        consoleWindowsRef.current[instance.id] = win;
        containerWindowIdsRef.current.add(instance.id);
        if (showIdlePrompt) {
          // Child app boot can be delayed; rebroadcast prompt shortly after open.
          window.setTimeout(() => broadcastIdlePromptToConsoles(true), 1200);
        }
      }
    } catch (err) {
      setMessage(err.response?.data?.detail || "Failed to open container");
    }
  };

  const stopInstances = async (instanceIds) => {
    if (!instanceIds || instanceIds.length === 0) return;
    try {
      const results = await Promise.all(instanceIds.map((id) => api.post(`/user/pods/${id}/stop`).catch((err) => err)));
      const failures = results.filter((r) => r instanceof Error || r?.response?.status >= 400);
      if (failures.length) {
        setMessage("Some idle VMs failed to stop; please check the labs list.");
      } else {
        setMessage("");
      }
      refresh();
    } catch (err) {
      setMessage(err.response?.data?.detail || "Failed to stop idle VM");
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
        setMessage("Some idle VMs failed to delete; please check the labs list.");
      } else if (!keepMessage) {
        if (reason === "idle-timeout") {
          setMessage("Session ended due to inactivity.");
        } else {
          setMessage("");
        }
      }
      refresh();
    } catch (err) {
      setMessage(err.response?.data?.detail || "Failed to delete idle VM");
    }
  };

  const deleteContainerInstances = async (instanceIds, reason, keepMessage = false) => {
    if (!instanceIds || instanceIds.length === 0) return;
    try {
      const results = await Promise.all(
        instanceIds.map((id) => api.delete(`/user/containers/${id}`).catch((err) => err))
      );
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
        setMessage("Some idle containers failed to delete; please check the labs list.");
      } else if (!keepMessage) {
        if (reason === "idle-timeout") {
          setMessage("Session ended due to inactivity.");
        } else {
          setMessage("");
        }
      }
      refresh();
    } catch (err) {
      setMessage(err.response?.data?.detail || "Failed to delete idle container");
    }
  };

  const connect = async (instance) => {
    if (!instance?.id) {
      setMessage("Console URL not available yet");
      return;
    }
    try {
      const res = await api.post(`/user/pods/${instance.id}/connect-token`);
      const connectUrl = String(res?.data?.connect_url || "").trim() || String(instance.console_url || "").trim();
      if (!connectUrl) {
        setMessage("Console URL not available yet");
        return;
      }
      const win = window.open(connectUrl, "_blank");
      if (win) {
        const origin = rememberAllowedOrigin(connectUrl);
        if (origin) {
          consoleWindowOriginsRef.current[instance.id] = origin;
        }
        consoleWindowsRef.current[instance.id] = win;
        containerWindowIdsRef.current.delete(instance.id);
        startConsoleHandshake(instance.id, win);
        if (document.hasFocus()) {
          postToConsoleWindow(instance.id, win, {
            type: "idle-focus",
            source: "user",
            instanceId: instance.id,
            timestamp: Date.now(),
          });
        }
      }
    } catch (err) {
      setMessage(err.response?.data?.detail || "Failed to open console");
    }
  };

  const templateName = (templateId) => templates.find((t) => t.id === templateId)?.name || "VM";
  const podName = (instance) => `vm-${instance.owner}-${instance.id.slice(0, 8)}`;
  const effectiveStatus = (instance) => instance?.status_stage || instance?.status || "unknown";
  const statusLabel = (instance) => {
    const status = effectiveStatus(instance);
    const labelMap = {
      pending: "Pending",
      building: "Building",
      starting: "Starting",
      running: "Running",
      stopped: "Stopped",
      completed: "Completed",
      failed: "Failed",
      unknown: "Unknown",
    };
    return labelMap[status] || "Unknown";
  };
  const statusReason = (instance) => (effectiveStatus(instance) === "pending" ? "waiting for available resources" : "");
  const isRunning = (instance) => effectiveStatus(instance) === "running";
  const containerTemplateName = (templateId) =>
    containerTemplates.find((t) => t.id === templateId)?.name || "Container";
  const effectiveContainerStatus = (instance) => instance?.status_stage || instance?.status || "unknown";
  const containerStatusLabel = (instance) => {
    const status = effectiveContainerStatus(instance);
    const labelMap = {
      queued: "Queued",
      pending: "Pending",
      building: "Building",
      starting: "Starting",
      running: "Running",
      stopped: "Stopped",
      completed: "Completed",
      failed: "Failed",
      unknown: "Unknown",
    };
    return labelMap[status] || "Unknown";
  };
  const containerStatusReason = (instance) => instance?.status_detail || "";
  const containerDiagnostics = (instance) =>
    Array.isArray(instance?.launch_diagnostics) ? instance.launch_diagnostics.slice(0, 5) : [];
  const hasContainerStartupError = (instance) => {
    const status = effectiveContainerStatus(instance);
    if (status === "failed") {
      return true;
    }
    const errorPattern = /(error|failed|back-?off|imagepull|errimagepull|invalid|crashloop)/i;
    const detail = String(instance?.status_detail || "");
    if (errorPattern.test(detail)) {
      return true;
    }
    return containerDiagnostics(instance).some((line) => errorPattern.test(String(line || "")));
  };
  const isContainerRunning = (instance) => effectiveContainerStatus(instance) === "running";

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

  const postToConsoleWindow = (instanceId, win, payload) => {
    if (!instanceId || !win || win.closed) {
      return;
    }
    const targetOrigin = consoleWindowOriginsRef.current[instanceId] || "*";
    try {
      win.postMessage(payload, targetOrigin);
    } catch (err) {
      // ignore postMessage failures
    }
  };

  const resolveConsoleSourceInstanceId = (sourceWindow) => {
    if (!sourceWindow) {
      return null;
    }
    const windows = consoleWindowsRef.current;
    for (const [id, win] of Object.entries(windows)) {
      if (!win || win.closed) {
        delete windows[id];
        delete consoleWindowOriginsRef.current[id];
        containerWindowIdsRef.current.delete(id);
        stopConsoleHandshake(id);
        continue;
      }
      if (win === sourceWindow) {
        return id;
      }
      try {
        if (sourceWindow.top === win || sourceWindow.parent === win) {
          return id;
        }
      } catch (err) {
        // Cross-origin hierarchy checks can throw; ignore and continue.
      }
    }
    return null;
  };

  const sendAuthToConsole = (instanceId, win) => {
    if (!instanceId || !win || win.closed) {
      return;
    }
    const apiBase = api?.defaults?.baseURL || "";
    rememberAllowedOrigin(window.location.origin);
    rememberAllowedOrigin(apiBase);
    const allowedOrigins = Array.from(allowedMessageOriginsRef.current);
    postToConsoleWindow(instanceId, win, { type: "idle-auth", source: "user", instanceId, apiBase, allowedOrigins });
  };

  const startConsoleHandshake = (instanceId, win) => {
    if (!instanceId || !win) {
      return;
    }
    const send = () => {
      if (!win || win.closed) {
        delete consoleWindowsRef.current[instanceId];
        delete consoleWindowOriginsRef.current[instanceId];
        stopConsoleHandshake(instanceId);
        return;
      }
      postToConsoleWindow(instanceId, win, { type: "idle-handshake", source: "user", instanceId });
      sendAuthToConsole(instanceId, win);
    };
    send();
    if (!consoleHandshakeRef.current[instanceId]) {
      consoleHandshakeRef.current[instanceId] = setInterval(send, 1000);
    }
  };

  const hasOpenConsoles = () => {
    const windows = consoleWindowsRef.current;
    let open = false;
    Object.entries(windows).forEach(([id, win]) => {
      if (!win || win.closed) {
        delete windows[id];
        delete consoleWindowOriginsRef.current[id];
        containerWindowIdsRef.current.delete(id);
        stopConsoleHandshake(id);
        return;
      }
      if (containerWindowIdsRef.current.has(id)) {
        return;
      }
      open = true;
    });
    return open;
  };

  const broadcastActivityToConsoles = (timestamp) => {
    const windows = consoleWindowsRef.current;
    Object.entries(windows).forEach(([id, win]) => {
      if (!win || win.closed) {
        delete windows[id];
        delete consoleWindowOriginsRef.current[id];
        containerWindowIdsRef.current.delete(id);
        stopConsoleHandshake(id);
        return;
      }
      if (containerWindowIdsRef.current.has(id)) {
        return;
      }
      postToConsoleWindow(id, win, { type: "idle-activity", source: "user", timestamp });
    });
  };

  const broadcastFocusToConsoles = (focused) => {
    const windows = consoleWindowsRef.current;
    const timestamp = Date.now();
    Object.entries(windows).forEach(([id, win]) => {
      if (!win || win.closed) {
        delete windows[id];
        delete consoleWindowOriginsRef.current[id];
        containerWindowIdsRef.current.delete(id);
        stopConsoleHandshake(id);
        return;
      }
      if (containerWindowIdsRef.current.has(id)) {
        return;
      }
      postToConsoleWindow(id, win, {
        type: focused ? "idle-focus" : "idle-blur",
        source: "user",
        instanceId: id,
        timestamp,
      });
    });
  };

  const broadcastIdlePromptToConsoles = (showPrompt) => {
    const windows = consoleWindowsRef.current;
    const endsAt = countdownEndsAtRef.current || Date.now() + PROMPT_COUNTDOWN_SECONDS * 1000;
    Object.entries(windows).forEach(([id, win]) => {
      if (!win || win.closed) {
        delete windows[id];
        delete consoleWindowOriginsRef.current[id];
        containerWindowIdsRef.current.delete(id);
        stopConsoleHandshake(id);
        return;
      }
      if (!containerWindowIdsRef.current.has(id)) {
        return;
      }
      if (showPrompt) {
        postToConsoleWindow(id, win, {
          type: "idle-parent-prompt",
          source: "user",
          instanceId: id,
          endsAt,
          timestamp: Date.now(),
        });
      } else {
        postToConsoleWindow(id, win, {
          type: "idle-parent-clear",
          source: "user",
          instanceId: id,
          timestamp: Date.now(),
        });
      }
    });
  };

  const broadcastIdleControlToVmConsoles = (type, extra = {}) => {
    const windows = consoleWindowsRef.current;
    const timestamp = Date.now();
    Object.entries(windows).forEach(([id, win]) => {
      if (!win || win.closed) {
        delete windows[id];
        delete consoleWindowOriginsRef.current[id];
        containerWindowIdsRef.current.delete(id);
        stopConsoleHandshake(id);
        return;
      }
      if (containerWindowIdsRef.current.has(id)) {
        return;
      }
      postToConsoleWindow(id, win, { type, source: "user", instanceId: id, timestamp, ...extra });
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

  const showExternalIdlePrompt = (endsAt) => {
    vmParentPromptActiveRef.current = true;
    idlePromptRef.current = true;
    setShowIdlePrompt(true);
    setSessionEnded(false);
    const targetEndsAt =
      Number.isFinite(endsAt) && endsAt > Date.now() ? endsAt : Date.now() + PROMPT_COUNTDOWN_SECONDS * 1000;
    countdownEndsAtRef.current = targetEndsAt;
    const remainingSeconds = Math.max(0, Math.ceil((targetEndsAt - Date.now()) / 1000));
    setIdleCountdown(remainingSeconds);
    if (countdownRef.current) {
      clearInterval(countdownRef.current);
    }
    countdownRef.current = setInterval(updateCountdown, 1000);
    updateCountdown();
  };

  const clearExternalIdlePrompt = (timestamp) => {
    vmParentPromptActiveRef.current = false;
    if (countdownRef.current) {
      clearInterval(countdownRef.current);
      countdownRef.current = null;
    }
    countdownEndsAtRef.current = null;
    clearIdlePrompt();
    const idleMinutes = activeIdleMinutesRef.current;
    const workloadCount = activeWorkloadCountRef.current;
    if (!idleMinutes || workloadCount === 0) {
      return;
    }
    const ts = Number.isFinite(timestamp) ? timestamp : Date.now();
    lastActivityAtRef.current = ts;
    writeStoredActivity(ts);
    idleStartsAtRef.current = ts + Math.max(1, idleMinutes) * 60 * 1000;
    scheduleIdleTimer();
  };

  const startIdleCountdown = (startedAt) => {
    if (!latestVmInstanceIdsRef.current.length && !latestContainerInstanceIdsRef.current.length) {
      return;
    }
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
    if (activeWorkloadCount === 0 || !activeIdleMinutes) {
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

  const noteVmPresence = (timestamp) => {
    vmPresenceAtRef.current = timestamp || Date.now();
  };

  const handleExternalActivity = (timestamp) => {
    if (activeWorkloadCount === 0 || !activeIdleMinutes) {
      return;
    }
    const now = timestamp || Date.now();
    noteVmPresence(now);
    recordActivity({ emit: false, timestamp: now });
  };

  const suspendIdle = (timestamp, reason = "vm") => {
    idleSuspendedRef.current = true;
    idleSuspendReasonRef.current = reason;
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
    idleSuspendReasonRef.current = null;
    if (activeWorkloadCount === 0 || !activeIdleMinutes) {
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
    if (activeWorkloadCount === 0 || !activeIdleMinutes) {
      clearIdleTimers();
      clearIdlePrompt();
      idleStartsAtRef.current = null;
      lastActivityAtRef.current = null;
      vmPresenceAtRef.current = null;
      idleSuspendedRef.current = false;
      idleSuspendReasonRef.current = null;
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
  }, [activeIdleMinutes, activeWorkloadCount]);

  useEffect(() => {
    broadcastIdlePromptToConsoles(showIdlePrompt);
    if (!showIdlePrompt) {
      return undefined;
    }
    // Child apps can attach message listeners late; retry for a short window.
    let attempts = 0;
    const retry = window.setInterval(() => {
      attempts += 1;
      broadcastIdlePromptToConsoles(true);
      if (attempts >= 20) {
        window.clearInterval(retry);
      }
    }, 1500);
    return () => window.clearInterval(retry);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showIdlePrompt]);

  useEffect(() => () => clearIdleTimers(), []);

  useEffect(() => {
    const interval = setInterval(() => {
      if (idleSuspendReasonRef.current !== "vm") {
        return;
      }
      const lastPresence = vmPresenceAtRef.current;
      if (!lastPresence) {
        return;
      }
      if (Date.now() - lastPresence <= VM_PRESENCE_GRACE_MS) {
        return;
      }
      if (!document.hasFocus() && hasOpenConsoles()) {
        return;
      }
      resumeIdle(Date.now());
    }, 1000);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeIdleMinutes, activeWorkloadCount]);

  useEffect(() => {
    const onActivity = () => {
      if (document.hidden) {
        return;
      }
      if (activeWorkloadCount === 0 || !activeIdleMinutes) {
        return;
      }
      const now = Date.now();
      if (idleSuspendedRef.current) {
        idleSuspendedRef.current = false;
        idleSuspendReasonRef.current = null;
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
      idleSuspendReasonRef.current = null;
      broadcastFocusToConsoles(true);
      syncIdleState();
      if (!idlePromptRef.current && !vmParentPromptActiveRef.current) {
        recordActivity({ emit: true });
      }
    };
    const onBlur = () => {
      broadcastFocusToConsoles(false);
    };
    const onVisibility = () => {
      if (!document.hidden) {
        syncIdleState();
      }
    };
    const events = ["mousemove", "keydown", "mousedown", "touchstart", "scroll"];
    events.forEach((evt) => window.addEventListener(evt, onActivity, { passive: true }));
    window.addEventListener("focus", onFocus);
    window.addEventListener("blur", onBlur);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      events.forEach((evt) => window.removeEventListener(evt, onActivity));
      window.removeEventListener("focus", onFocus);
      window.removeEventListener("blur", onBlur);
      document.removeEventListener("visibilitychange", onVisibility);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeIdleMinutes, activeWorkloadCount]);

  useEffect(() => {
    const handleMessage = (event) => {
      const payload = event.data || {};
      const sourceInstanceId = resolveConsoleSourceInstanceId(event.source);
      const messageOrigin = normalizeOrigin(event.origin);
      const payloadInstanceId = typeof payload.instanceId === "string" ? payload.instanceId : null;
      const payloadSource = payload.source;
      const isConsoleSource = payloadSource === "vm" || payloadSource === "container";
      const trustedByInstance =
        isConsoleSource &&
        payloadInstanceId &&
        isKnownActiveInstanceId(payloadInstanceId) &&
        isSameHostOrigin(messageOrigin);
      if (
        messageOrigin &&
        !allowedMessageOriginsRef.current.has(messageOrigin) &&
        !sourceInstanceId &&
        !trustedByInstance
      ) {
        return;
      }
      const resolvedInstanceId = sourceInstanceId || (trustedByInstance ? payloadInstanceId : null);
      if (messageOrigin) {
        rememberAllowedOrigin(messageOrigin);
        if (resolvedInstanceId) {
          consoleWindowOriginsRef.current[resolvedInstanceId] = messageOrigin;
        }
      }
      if (resolvedInstanceId && event.source && typeof event.source.postMessage === "function") {
        consoleWindowsRef.current[resolvedInstanceId] = event.source;
        if (payload.source === "container") {
          containerWindowIdsRef.current.add(resolvedInstanceId);
        } else if (payload.source === "vm") {
          containerWindowIdsRef.current.delete(resolvedInstanceId);
        }
      }
      const isVmSource = payload.source === "vm";
      const isContainerSource = payload.source === "container";
      if (payload.type === "idle-parent-prompt" && isVmSource) {
        showExternalIdlePrompt(Number(payload.endsAt));
        return;
      }
      if (payload.type === "idle-parent-clear" && isVmSource) {
        clearExternalIdlePrompt(payload.timestamp);
        return;
      }
      if (payload.type === "idle-parent-ended" && isVmSource) {
        vmParentPromptActiveRef.current = false;
        clearIdleTimers();
        clearIdlePrompt();
        setSessionEnded(true);
        return;
      }
      if (payload.type === "idle-focus" && isVmSource) {
        if (vmParentPromptActiveRef.current) {
          return;
        }
        const ts = Number.isFinite(payload.timestamp) ? payload.timestamp : Date.now();
        noteVmPresence(ts);
        recordActivity({ emit: false, timestamp: ts });
        return;
      }
      if (payload.type === "idle-blur" && isVmSource) {
        if (vmParentPromptActiveRef.current) {
          return;
        }
        vmPresenceAtRef.current = null;
        return;
      }
      if (payload.type === "idle-activity" && isVmSource) {
        if (vmParentPromptActiveRef.current) {
          return;
        }
        const ts = Number.isFinite(payload.timestamp) ? payload.timestamp : Date.now();
        handleExternalActivity(ts);
        return;
      }
      if ((payload.type === "idle-focus" || payload.type === "idle-activity") && isContainerSource) {
        const ts = Number.isFinite(payload.timestamp) ? payload.timestamp : Date.now();
        recordActivity({ emit: false, timestamp: ts });
        return;
      }
      if (payload.type === "idle-blur" && isContainerSource) {
        return;
      }
      if (payload.type === "idle-handshake-ack" && payload.source === "vm" && payload.instanceId) {
        stopConsoleHandshake(payload.instanceId);
        const win = consoleWindowsRef.current[payload.instanceId];
        if (win) {
          sendAuthToConsole(payload.instanceId, win);
        }
        return;
      }
      if (payload.type === "idle-stop" && payload.instanceId) {
        const isContainerSource = payload.source === "container";
        if (payload.source === "vm" && (payload.reason === "idle-timeout" || payload.reason === "user-end")) {
          vmParentPromptActiveRef.current = false;
          clearIdleTimers();
          clearIdlePrompt();
          setSessionEnded(true);
          setMessage(payload.reason === "idle-timeout" ? "Session ended due to inactivity." : "Session ended.");
        }
        delete consoleWindowsRef.current[payload.instanceId];
        delete consoleWindowOriginsRef.current[payload.instanceId];
        containerWindowIdsRef.current.delete(payload.instanceId);
        stopConsoleHandshake(payload.instanceId);
        if (payload.action === "delete") {
          if (isContainerSource) {
            deleteContainerInstances([payload.instanceId], payload.reason);
          } else {
            deleteInstances([payload.instanceId], payload.reason);
          }
        } else {
          if (!isContainerSource) {
            stopInstances([payload.instanceId]);
          }
        }
        return;
      }
      if (payload.type === "idle-continue" && (payload.source === "vm" || payload.source === "container")) {
        continueSession();
      }
    };
    window.addEventListener("message", handleMessage);
    return () => window.removeEventListener("message", handleMessage);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const continueSession = () => {
    vmParentPromptActiveRef.current = false;
    clearIdlePrompt();
    setSessionEnded(false);
    idleSuspendedRef.current = false;
    idleSuspendReasonRef.current = null;
    broadcastIdleControlToVmConsoles("idle-parent-clear");
    recordActivity();
    refresh();
  };

  const endNow = (auto = false) => {
    vmParentPromptActiveRef.current = false;
    clearIdleTimers();
    clearIdlePrompt();
    setSessionEnded(true);
    setMessage(auto ? "Session ended due to inactivity." : "Session ended.");
    const reason = auto ? "idle-timeout" : "user-end";
    broadcastIdleControlToVmConsoles("idle-parent-ended", { reason });
    deleteInstances(latestVmInstanceIdsRef.current, reason, true);
    deleteContainerInstances(latestContainerInstanceIdsRef.current, reason, true);
  };

  const formatCountdown = (seconds) => {
    const mins = Math.floor((seconds || 0) / 60)
      .toString()
      .padStart(2, "0");
    const secs = ((seconds || 0) % 60).toString().padStart(2, "0");
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
              We have not seen activity for {activeIdleMinutes || DEFAULT_IDLE_MINUTES} minutes. Your running lab(s)
              will stop in {formatCountdown(idleCountdown || PROMPT_COUNTDOWN_SECONDS)} unless you continue.
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
            {templates.length === 0 && containerTemplates.length === 0 && (
              <div className="muted">No templates available.</div>
            )}
            {templates.map((t) => (
              <div key={t.id} className="tile template-tile">
                <div className="tile-header">
                  <h4>{t.name}</h4>
                </div>
                {t.description && <div className="muted small">{t.description}</div>}
                <div style={{ marginTop: "0.75rem" }}>
                  <button onClick={() => start(t.id)}>Start Lab</button>
                </div>
              </div>
            ))}
            {containerTemplates.map((t) => (
              <div key={`ct-${t.id}`} className="tile template-tile">
                <div className="tile-header">
                  <h4>{t.name}</h4>
                </div>
                {t.description && <div className="muted small">{t.description}</div>}
                <div style={{ marginTop: "0.75rem" }}>
                  <button onClick={() => startContainer(t.id)}>Start Lab</button>
                </div>
              </div>
            ))}
          </div>
        </div>
        <div>
          <h3>My Running Labs</h3>
          <div className="tile-grid">
            {instances.length === 0 && containerInstances.length === 0 && (
              <div className="muted">No labs yet. Start a lab to see it here.</div>
            )}
            {instances.map((p) => (
              <div key={p.id} className="tile pod-tile">
                <div className="tile-header">
                  <h4>{templateName(p.template_id)}</h4>
                  <span className={`badge ${isRunning(p) ? "success" : "warn"}`}>{statusLabel(p)}</span>
                </div>
                <div className="specs">
                  <span>{podName(p)}</span>
                </div>
                {statusReason(p) && <div className="muted small">{statusReason(p)}</div>}
                <div className="actions">
                  <button className="danger" onClick={() => remove(p.id)}>
                    Delete
                  </button>
                  <button onClick={() => connect(p)} disabled={!isRunning(p)}>
                    Connect
                  </button>
                </div>
              </div>
            ))}
            {containerInstances.map((c) => (
              <div key={`ci-${c.id}`} className="tile pod-tile">
                <div className="tile-header">
                  <h4>{containerTemplateName(c.template_id)}</h4>
                  <span className={`badge ${isContainerRunning(c) ? "success" : "warn"}`}>
                    {containerStatusLabel(c)}
                  </span>
                </div>
                <div className="specs">
                  <span>{c.pod_name || `ct-${c.owner}-${c.id.slice(0, 8)}`}</span>
                </div>
                {hasContainerStartupError(c) && containerStatusReason(c) && (
                  <div className="muted small">{containerStatusReason(c)}</div>
                )}
                {hasContainerStartupError(c) &&
                  containerDiagnostics(c).map((line, idx) => (
                    <div key={`${c.id}-diag-${idx}`} className="muted small">
                      {line}
                    </div>
                  ))}
                <div className="actions">
                  <button className="danger" onClick={() => removeContainer(c.id)}>
                    Delete
                  </button>
                  <button onClick={() => openContainer(c)} disabled={!c.access_url || !isContainerRunning(c)}>
                    Connect
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
};

export default UserPanel;
