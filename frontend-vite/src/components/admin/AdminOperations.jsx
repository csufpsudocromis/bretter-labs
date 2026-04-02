import React, { useEffect, useState } from "react";
import { api } from "../../api";

const fmtDateTime = (value) => {
  if (!value) return "n/a";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString();
};

const formatDuration = (seconds) => {
  const total = Math.max(0, Number(seconds || 0));
  const mins = Math.floor(total / 60);
  const secs = total % 60;
  if (mins > 0) return `${mins}m ${secs}s`;
  return `${secs}s`;
};

const statusBadge = (status) => {
  const normalized = String(status || "")
    .trim()
    .toLowerCase();
  if (["failed", "error"].includes(normalized)) return "badge warn";
  if (["completed", "running", "ready"].includes(normalized)) return "badge success";
  return "badge";
};

const AdminOperations = () => {
  const [uploadTasks, setUploadTasks] = useState([]);
  const [launchTasks, setLaunchTasks] = useState([]);
  const [loading, setLoading] = useState(false);
  const [acting, setActing] = useState({});
  const [message, setMessage] = useState("");

  const setBusy = (key, value) =>
    setActing((prev) => {
      if (!value) {
        const next = { ...prev };
        delete next[key];
        return next;
      }
      return { ...prev, [key]: true };
    });

  const load = async () => {
    setLoading(true);
    setMessage("");
    try {
      const [uploadRes, launchRes] = await Promise.all([
        api.get("/admin/operations/upload-tasks", { params: { limit: 100 } }),
        api.get("/admin/operations/launch-tasks", { params: { limit: 150 } }),
      ]);
      setUploadTasks(Array.isArray(uploadRes.data) ? uploadRes.data : []);
      setLaunchTasks(Array.isArray(launchRes.data) ? launchRes.data : []);
    } catch (err) {
      setMessage(err.response?.data?.detail || "Failed to load admin operations.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const runUploadAction = async (taskId, action) => {
    const key = `upload:${taskId}:${action}`;
    setBusy(key, true);
    setMessage("");
    try {
      if (action === "retry") {
        await api.post(`/admin/operations/upload-tasks/${taskId}/retry`);
      } else if (action === "cancel") {
        await api.post(`/admin/operations/upload-tasks/${taskId}/cancel`);
      } else if (action === "cleanup") {
        await api.delete(`/admin/operations/upload-tasks/${taskId}`);
      }
      await load();
    } catch (err) {
      setMessage(err.response?.data?.detail || `Upload task ${action} failed.`);
    } finally {
      setBusy(key, false);
    }
  };

  const runLaunchAction = async (kind, taskId, action) => {
    const key = `launch:${kind}:${taskId}:${action}`;
    setBusy(key, true);
    setMessage("");
    try {
      if (action === "cleanup") {
        await api.delete(`/admin/operations/launch-tasks/${kind}/${taskId}`);
      } else {
        await api.post(`/admin/operations/launch-tasks/${kind}/${taskId}/${action}`);
      }
      await load();
    } catch (err) {
      setMessage(err.response?.data?.detail || `Launch task ${action} failed.`);
    } finally {
      setBusy(key, false);
    }
  };

  const pendingUploadCount = uploadTasks.filter(
    (item) => !["completed"].includes(String(item.status || "").toLowerCase())
  ).length;
  const actionableLaunchCount = launchTasks.filter((item) =>
    ["queued", "pending", "failed", "error", "stopped"].includes(String(item.status || "").toLowerCase())
  ).length;

  return (
    <div>
      <h2>Operations</h2>
      <p className="muted small">
        Review failed/queued upload and launch tasks. Retry, cancel, or clean up tasks without leaving this page.
      </p>
      <div className="actions">
        <button className="ghost" type="button" onClick={load} disabled={loading}>
          {loading ? "Refreshing..." : "Refresh"}
        </button>
      </div>
      {message && <div className="error">{message}</div>}

      <div className="grid" style={{ marginBottom: "1rem" }}>
        <div className="tile">
          <div className="tile-header">
            <h4>Upload/Finalize Queue</h4>
            <span className="badge">{pendingUploadCount}</span>
          </div>
          <div className="muted small">Active or failed upload tasks requiring operator action.</div>
        </div>
        <div className="tile">
          <div className="tile-header">
            <h4>Launch Queue</h4>
            <span className="badge">{actionableLaunchCount}</span>
          </div>
          <div className="muted small">VM/container launches that are queued, pending, or failed.</div>
        </div>
      </div>

      <div className="card" style={{ marginBottom: "1rem" }}>
        <h3>Image Upload/Finalize Tasks</h3>
        {uploadTasks.length === 0 && <div className="muted small">No active or failed upload tasks.</div>}
        {uploadTasks.length > 0 && (
          <div className="tile-grid">
            {uploadTasks.map((task) => {
              const status = String(task.status || "unknown").toLowerCase();
              const shortId = String(task.task_id || "").slice(0, 8);
              const retryBusy = Boolean(acting[`upload:${task.task_id}:retry`]);
              const cancelBusy = Boolean(acting[`upload:${task.task_id}:cancel`]);
              const cleanupBusy = Boolean(acting[`upload:${task.task_id}:cleanup`]);
              return (
                <div key={task.task_id} className="tile template-tile">
                  <div className="tile-header">
                    <h4>{task.filename || task.original_filename || "image upload"}</h4>
                    <span className={statusBadge(task.status)}>{task.status}</span>
                  </div>
                  <div className="small muted">
                    Task: {shortId} | NS: {task.namespace || "labs"} | Stage: {task.stage || "n/a"}
                  </div>
                  <div className="small muted">
                    Progress: {Number.isFinite(task.progress_percent) ? `${task.progress_percent}%` : "n/a"} | Retries:{" "}
                    {task.retry_count}/{task.max_retries}
                  </div>
                  <div className="small muted">Updated: {fmtDateTime(task.updated_at)}</div>
                  <div className="small muted">{task.detail || task.error || "No detail provided."}</div>
                  {task.error && <div className="error small">{task.error}</div>}
                  <div className="actions">
                    <button
                      type="button"
                      className="ghost"
                      onClick={() => runUploadAction(task.task_id, "retry")}
                      disabled={retryBusy || loading || status === "completed" || status === "uploading"}
                    >
                      {retryBusy ? "Retrying..." : "Retry"}
                    </button>
                    <button
                      type="button"
                      className="ghost"
                      onClick={() => runUploadAction(task.task_id, "cancel")}
                      disabled={cancelBusy || loading || status === "completed"}
                    >
                      {cancelBusy ? "Canceling..." : "Cancel"}
                    </button>
                    <button
                      type="button"
                      className="danger"
                      onClick={() => runUploadAction(task.task_id, "cleanup")}
                      disabled={cleanupBusy || loading}
                    >
                      {cleanupBusy ? "Cleaning..." : "Cleanup"}
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      <div className="card">
        <h3>Launch Tasks (VM + Container)</h3>
        {launchTasks.length === 0 && <div className="muted small">No queued or failed launch tasks.</div>}
        {launchTasks.length > 0 && (
          <div className="tile-grid">
            {launchTasks.map((task) => {
              const retryBusy = Boolean(acting[`launch:${task.kind}:${task.task_id}:retry`]);
              const cancelBusy = Boolean(acting[`launch:${task.kind}:${task.task_id}:cancel`]);
              const cleanupBusy = Boolean(acting[`launch:${task.kind}:${task.task_id}:cleanup`]);
              const status = String(task.status || "")
                .trim()
                .toLowerCase();
              return (
                <div key={`${task.kind}-${task.task_id}`} className="tile template-tile">
                  <div className="tile-header">
                    <h4>
                      {task.kind === "vm" ? "VM" : "Container"} launch {String(task.task_id || "").slice(0, 8)}
                    </h4>
                    <span className={statusBadge(task.status)}>{task.status}</span>
                  </div>
                  <div className="small muted">
                    Owner: {task.owner} | NS: {task.namespace} | Cluster: {task.cluster_id}
                  </div>
                  <div className="small muted">Template: {task.template_id}</div>
                  <div className="small muted">Elapsed: {formatDuration(task.elapsed_seconds)}</div>
                  <div className="small muted">Started: {fmtDateTime(task.started_at)}</div>
                  <div className="small muted">{task.detail || "No detail provided."}</div>
                  <div className="actions">
                    <button
                      type="button"
                      className="ghost"
                      onClick={() => runLaunchAction(task.kind, task.task_id, "retry")}
                      disabled={
                        retryBusy || loading || !["queued", "pending", "failed", "error", "stopped"].includes(status)
                      }
                    >
                      {retryBusy ? "Retrying..." : "Retry"}
                    </button>
                    <button
                      type="button"
                      className="ghost"
                      onClick={() => runLaunchAction(task.kind, task.task_id, "cancel")}
                      disabled={cancelBusy || loading}
                    >
                      {cancelBusy ? "Canceling..." : "Cancel"}
                    </button>
                    <button
                      type="button"
                      className="danger"
                      onClick={() => runLaunchAction(task.kind, task.task_id, "cleanup")}
                      disabled={cleanupBusy || loading}
                    >
                      {cleanupBusy ? "Cleaning..." : "Cleanup"}
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
};

export default AdminOperations;
