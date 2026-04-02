import React, { useEffect, useState } from "react";
import axios from "axios";
import { api } from "../../api";

const AdminImages = () => {
  const [images, setImages] = useState([]);
  const [isPlatformAdmin, setIsPlatformAdmin] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [file, setFile] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [uploadStage, setUploadStage] = useState("idle");
  const [progress, setProgress] = useState(0);
  const [uploadTaskId, setUploadTaskId] = useState("");
  const [uploadDetail, setUploadDetail] = useState("");
  const [editId, setEditId] = useState(null);
  const [editName, setEditName] = useState("");
  const [editFilename, setEditFilename] = useState("");
  const [editSharedCatalog, setEditSharedCatalog] = useState(false);

  const load = async () => {
    try {
      const [res, me] = await Promise.all([api.get("/admin/images"), api.get("/auth/me")]);
      setImages(res.data);
      setIsPlatformAdmin(
        String(me?.data?.role || "")
          .trim()
          .toLowerCase() === "platform_admin"
      );
      setMessage("");
      setError("");
    } catch (err) {
      setError(err.response?.data?.detail || "Failed to load images");
    }
  };

  useEffect(() => {
    load();
  }, []);

  const sleep = (ms) =>
    new Promise((resolve) => {
      window.setTimeout(resolve, ms);
    });

  const formatTaskDetail = (task) => {
    if (!task) return "";
    const pieces = [];
    const status = String(task.status || "")
      .trim()
      .toLowerCase();
    const stage = String(task.stage || task.status || "")
      .trim()
      .toLowerCase();
    const detail = String(task.detail || "").trim();
    const progressValue = task.progress_percent;
    const hasProgress = Number.isFinite(progressValue) && progressValue >= 0;
    if (detail) {
      pieces.push(detail);
    }
    if (hasProgress && status !== "uploading") {
      pieces.push(`Progress: ${Math.min(100, Math.max(0, Math.round(progressValue)))}%`);
    }
    const retryCount = Number(task.retry_count || 0);
    const maxRetries = Number(task.max_retries || 0);
    if (retryCount > 0 || maxRetries > 0) {
      pieces.push(`Retries: ${retryCount}/${Math.max(0, maxRetries)}`);
    }
    if (task.next_retry_at) {
      const nextRetry = new Date(task.next_retry_at);
      if (!Number.isNaN(nextRetry.getTime())) {
        pieces.push(`Next retry: ${nextRetry.toLocaleTimeString()}`);
      }
    }
    return pieces.join(" ");
  };

  const waitForUploadTask = async (taskId) => {
    for (;;) {
      const res = await api.get(`/admin/images/upload-tasks/${taskId}`);
      const task = res.data;
      setUploadDetail(formatTaskDetail(task));
      const status = String(task.status || "")
        .trim()
        .toLowerCase();
      const stage = String(task.stage || task.status || "")
        .trim()
        .toLowerCase();
      if (status === "uploading") {
        setUploadStage("uploading");
      } else if (["uploaded", "normalizing", "seeded", "ready", "finalizing", "importing"].includes(stage)) {
        setUploadStage("finalizing");
      }
      if (Number.isFinite(task.progress_percent) && status !== "uploading") {
        setProgress(Math.min(100, Math.max(0, Math.round(task.progress_percent))));
      }
      if (task.status === "completed") return task;
      if (task.status === "failed") {
        throw new Error(task.error || "Upload finalize failed");
      }
      await sleep(2000);
    }
  };

  const upload = async () => {
    if (!file) return;
    setUploading(true);
    setUploadStage("uploading");
    setProgress(0);
    setUploadTaskId("");
    setUploadDetail("");
    setMessage("");
    setError("");

    const waitForTaskAndFinish = async (taskId) => {
      setUploadTaskId(taskId);
      setUploadStage("finalizing");
      setUploadDetail("Finalizing on cluster storage");
      await waitForUploadTask(taskId);
      setFile(null);
      setProgress(0);
      setUploadStage("idle");
      setUploadTaskId("");
      setUploadDetail("");
      setMessage("Upload complete");
      load();
    };

    const uploadLegacy = async () => {
      const formData = new FormData();
      formData.append("file", file);
      const kickoff = await api.post("/admin/images", formData, {
        headers: { "Content-Type": "multipart/form-data" },
        onUploadProgress: (evt) => {
          if (evt.total) {
            const percent = Math.min(100, Math.round((evt.loaded / evt.total) * 100));
            setProgress(percent);
            if (percent >= 100) {
              setUploadStage("finalizing");
            }
          }
        },
      });
      const taskId = kickoff.data?.task_id;
      if (!taskId) {
        throw new Error("Upload task did not start");
      }
      await waitForTaskAndFinish(taskId);
    };

    let attemptedDirect = false;
    try {
      attemptedDirect = true;
      const direct = await api.post("/admin/images/direct-upload/start", {
        filename: file.name,
        size_bytes: file.size,
      });
      const uploadUrl = direct.data?.upload_url;
      const uploadToken = direct.data?.upload_token;
      const taskId = direct.data?.task?.task_id;
      if (!uploadUrl || !uploadToken || !taskId) {
        throw new Error("Direct upload did not initialize");
      }
      setUploadTaskId(taskId);
      setUploadDetail("Uploading image directly to cluster storage");
      await axios.post(uploadUrl, file, {
        headers: {
          Authorization: `Bearer ${uploadToken}`,
          "Content-Type": "application/octet-stream",
        },
        maxBodyLength: Infinity,
        maxContentLength: Infinity,
        onUploadProgress: (evt) => {
          if (evt.total) {
            const percent = Math.min(100, Math.round((evt.loaded / evt.total) * 100));
            setProgress(percent);
            if (percent >= 100) {
              setUploadStage("finalizing");
            }
          }
        },
      });
      await waitForTaskAndFinish(taskId);
    } catch (err) {
      const status = err?.response?.status;
      const shouldFallbackToLegacy =
        status === 404 || status === 409 || status >= 500 || status == null || err?.code === "ERR_NETWORK";
      if (shouldFallbackToLegacy) {
        try {
          if (attemptedDirect) {
            setUploadDetail("Direct upload unavailable; retrying with legacy upload path");
          }
          await uploadLegacy();
          if (attemptedDirect) {
            setMessage("Upload complete (legacy path)");
          }
        } catch (legacyErr) {
          setError(legacyErr.response?.data?.detail || legacyErr.message || "Upload failed");
        }
      } else {
        setError(err.response?.data?.detail || err.message || "Upload failed");
      }
    } finally {
      setUploadStage("idle");
      setUploading(false);
    }
  };

  const remove = async (id) => {
    try {
      await api.delete(`/admin/images/${id}`);
      setMessage("Deleted");
      load();
    } catch (err) {
      setError(err.response?.data?.detail || "Delete failed");
    }
  };

  const startEdit = (img) => {
    setEditId(img.id);
    setEditName(img.name);
    setEditFilename(img.filename || img.name);
    setEditSharedCatalog(Boolean(img.shared_catalog));
  };

  const saveEdit = async () => {
    try {
      const payload = { name: editName, filename: editFilename };
      if (isPlatformAdmin) {
        payload.shared_catalog = Boolean(editSharedCatalog);
      }
      await api.patch(`/admin/images/${editId}`, payload);
      setEditId(null);
      setMessage("Updated");
      load();
    } catch (err) {
      setError(err.response?.data?.detail || "Update failed");
    }
  };

  const cancelEdit = () => {
    setEditId(null);
    setEditName("");
    setEditFilename("");
    setEditSharedCatalog(false);
  };

  return (
    <div>
      <h2>Images</h2>
      {message && <div className="info">{message}</div>}
      {error && <div className="error">{error}</div>}
      <div className="grid">
        <div>
          <h3>Upload image</h3>
          <input type="file" onChange={(e) => setFile(e.target.files?.[0] || null)} />
          <button onClick={upload} disabled={!file || uploading}>
            {!uploading && "Upload"}
            {uploading && uploadStage === "uploading" && `Uploading (${progress}%)`}
            {uploading && uploadStage === "finalizing" && "Finalizing on cluster..."}
          </button>
          {uploading && uploadStage === "uploading" && <p>Uploading from browser: {progress}%</p>}
          {uploading && uploadStage === "finalizing" && (
            <p>
              Upload complete. Finalizing on cluster (copy/normalize). This can take a few minutes.
              {uploadDetail ? ` ${uploadDetail}` : progress ? ` Progress: ${progress}%` : ""}
              {uploadTaskId ? ` (task ${uploadTaskId.slice(0, 8)})` : ""}
            </p>
          )}
          <p className="muted small">Allowed: .vhd/.vhdx, .qcow/.qcow2, .vdi. QCOW is auto-converted to raw.</p>
        </div>
        <div>
          <h3>Golden Images</h3>
          <div className="tile-grid">
            {images.length === 0 && <div className="muted">No images.</div>}
            {images.map((img) => (
              <div key={img.id} className="tile template-tile">
                <div className="tile-header">
                  <h4>{img.name}</h4>
                  <span className="muted small">{Math.round(img.size_bytes / (1024 * 1024))} MB</span>
                </div>
                {editId === img.id ? (
                  <div className="form">
                    <label>
                      Name
                      <input value={editName} onChange={(e) => setEditName(e.target.value)} />
                    </label>
                    <label>
                      Filename
                      <input value={editFilename} onChange={(e) => setEditFilename(e.target.value)} />
                    </label>
                    {isPlatformAdmin && (
                      <label>
                        Catalog scope
                        <select
                          value={editSharedCatalog ? "shared" : "namespace"}
                          onChange={(e) => setEditSharedCatalog(e.target.value === "shared")}
                        >
                          <option value="namespace">Namespace-owned</option>
                          <option value="shared">Shared (cross-namespace)</option>
                        </select>
                      </label>
                    )}
                    <div className="actions">
                      <button onClick={saveEdit}>Save</button>
                      <button className="ghost" onClick={cancelEdit}>
                        Cancel
                      </button>
                    </div>
                  </div>
                ) : (
                  <>
                    <div className="muted small">
                      {img.shared_catalog ? "Shared catalog" : "Namespace-owned catalog"}
                    </div>
                    <div className="actions">
                      <button className="ghost" onClick={() => startEdit(img)}>
                        Rename
                      </button>
                      <button className="danger" onClick={() => remove(img.id)}>
                        Delete
                      </button>
                    </div>
                  </>
                )}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
};

export default AdminImages;
