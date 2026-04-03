import React, { useEffect, useState } from "react";
import axios from "axios";
import { api } from "../../api";

const formatGiB = (value) => {
  const bytes = Number(value || 0);
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 GiB";
  return `${(bytes / 1024 ** 3).toFixed(bytes >= 10 * 1024 ** 3 ? 0 : 1)} GiB`;
};

const AdminImages = () => {
  const [images, setImages] = useState([]);
  const [isoImages, setIsoImages] = useState([]);
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
  const [editDefaultCpuCores, setEditDefaultCpuCores] = useState(2);
  const [editDefaultRamMb, setEditDefaultRamMb] = useState(4096);
  const [editUpdateIsoImageId, setEditUpdateIsoImageId] = useState("");
  const [creatingImage, setCreatingImage] = useState(false);
  const [createName, setCreateName] = useState("");
  const [createIsoId, setCreateIsoId] = useState("");
  const [createOsType, setCreateOsType] = useState("windows");
  const [createDriveSizeGiB, setCreateDriveSizeGiB] = useState(64);
  const [createDefaultCpuCores, setCreateDefaultCpuCores] = useState(2);
  const [createDefaultRamMb, setCreateDefaultRamMb] = useState(4096);
  const [createSharedCatalog, setCreateSharedCatalog] = useState(false);
  const [launchingImageId, setLaunchingImageId] = useState("");

  const load = async () => {
    try {
      const [res, isoRes, me] = await Promise.all([
        api.get("/admin/images"),
        api.get("/admin/iso-images"),
        api.get("/auth/me"),
      ]);
      setImages(Array.isArray(res.data) ? res.data : []);
      const isoRows = Array.isArray(isoRes.data) ? isoRes.data : [];
      setIsoImages(isoRows);
      if (!createIsoId && isoRows.length > 0) {
        setCreateIsoId(String(isoRows[0].id || ""));
      }
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
    setEditDefaultCpuCores(Math.max(1, Number(img.update_cpu_cores_default || 2)));
    setEditDefaultRamMb(Math.max(512, Number(img.update_ram_mb_default || 4096)));
    setEditUpdateIsoImageId(String(img.installer_iso_id || ""));
  };

  const saveEdit = async () => {
    try {
      const payload = {
        name: editName,
        filename: editFilename,
        update_cpu_cores_default: Math.max(1, Number(editDefaultCpuCores || 2)),
        update_ram_mb_default: Math.max(512, Number(editDefaultRamMb || 4096)),
        update_iso_image_id: String(editUpdateIsoImageId || ""),
      };
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
    setEditDefaultCpuCores(2);
    setEditDefaultRamMb(4096);
    setEditUpdateIsoImageId("");
  };

  const createFromIso = async () => {
    if (!createName.trim() || !createIsoId) {
      setError("Name and ISO are required");
      return;
    }
    setCreatingImage(true);
    setMessage("");
    setError("");
    try {
      const payload = {
        name: createName.trim(),
        iso_image_id: createIsoId,
        os_type: createOsType,
        drive_size_gib: Math.max(10, Number(createDriveSizeGiB || 64)),
        default_cpu_cores: Math.max(1, Number(createDefaultCpuCores || 2)),
        default_ram_mb: Math.max(512, Number(createDefaultRamMb || 4096)),
        shared_catalog: isPlatformAdmin ? Boolean(createSharedCatalog) : false,
      };
      const res = await api.post("/admin/images/create-from-iso", payload);
      const created = res?.data;
      setMessage(`Created image ${created?.name || payload.name}`);
      setCreateName("");
      setCreateDriveSizeGiB(64);
      setCreateDefaultCpuCores(2);
      setCreateDefaultRamMb(4096);
      setCreateSharedCatalog(false);
      load();
    } catch (err) {
      setError(err.response?.data?.detail || "Failed to create image");
    } finally {
      setCreatingImage(false);
    }
  };

  const launchForUpdate = async (img) => {
    setLaunchingImageId(img.id);
    setMessage("");
    setError("");
    const popup = window.open("", "_blank");
    if (!popup) {
      setLaunchingImageId("");
      setError("Browser blocked the console tab. Allow popups for this site and retry.");
      return;
    }
    popup.document.title = "Preparing VM Console";
    popup.document.body.innerHTML =
      '<p style="font-family:sans-serif;padding:16px;">Preparing VM console. This tab will connect automatically.</p>';
    try {
      const payload = {
        os_type: String(img.installer_os_type || createOsType || "windows"),
        console_provider: "guacamole",
      };
      const res = await api.post(`/admin/images/${img.id}/launch-update`, payload);
      const instance = res?.data || {};
      const waitFor = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
      const connectDeadline = Date.now() + 180000;
      let connectUrl = String(instance.console_url || "").trim();
      if (instance?.id) {
        while (Date.now() < connectDeadline) {
          try {
            const tokenRes = await api.post(`/user/pods/${instance.id}/connect-token`);
            connectUrl = String(tokenRes?.data?.connect_url || "").trim() || connectUrl;
            if (connectUrl) {
              break;
            }
          } catch (tokenErr) {
            const waitable = Number(tokenErr?.response?.status || 0) === 409;
            if (!waitable) {
              throw tokenErr;
            }
          }
          await waitFor(2000);
        }
      }
      if (connectUrl) {
        popup.location.replace(connectUrl);
      } else {
        throw new Error("VM console is still starting. Try again in a few moments.");
      }
      setMessage(`Update VM started (${String(instance.id || "").slice(0, 8)})`);
    } catch (err) {
      try {
        popup.close();
      } catch (_closeErr) {
        // no-op
      }
      setError(err.response?.data?.detail || err.message || "Failed to launch update VM");
    } finally {
      setLaunchingImageId("");
    }
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
          {editId && (
            <>
              <hr />
              <h3>Edit Golden Image</h3>
              <div className="form">
                <label>
                  Name
                  <input value={editName} onChange={(e) => setEditName(e.target.value)} />
                </label>
                <label>
                  Filename
                  <input value={editFilename} onChange={(e) => setEditFilename(e.target.value)} />
                </label>
                <label>
                  Default CPU cores
                  <input
                    type="number"
                    min={1}
                    max={16}
                    value={editDefaultCpuCores}
                    onChange={(event) => setEditDefaultCpuCores(Number(event.target.value || 2))}
                  />
                </label>
                <label>
                  Default RAM (MiB)
                  <input
                    type="number"
                    min={512}
                    max={65536}
                    step={256}
                    value={editDefaultRamMb}
                    onChange={(event) => setEditDefaultRamMb(Number(event.target.value || 4096))}
                  />
                </label>
                <label>
                  Mount ISO as CD (update VM)
                  <select
                    value={editUpdateIsoImageId}
                    onChange={(event) => setEditUpdateIsoImageId(event.target.value)}
                  >
                    <option value="">None</option>
                    {isoImages.map((row) => (
                      <option key={row.id} value={row.id}>
                        {row.name} ({formatGiB(row.size_bytes)})
                      </option>
                    ))}
                  </select>
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
            </>
          )}
          {!editId && (
            <>
              <hr />
              <h3>Create Image</h3>
              <label>
                Name
                <input
                  value={createName}
                  onChange={(event) => setCreateName(event.target.value)}
                  placeholder="Windows 11 Golden"
                />
              </label>
              <label>
                Boot ISO
                <select value={createIsoId} onChange={(event) => setCreateIsoId(event.target.value)}>
                  <option value="">Select ISO</option>
                  {isoImages.map((row) => (
                    <option key={row.id} value={row.id}>
                      {row.name} ({formatGiB(row.size_bytes)})
                    </option>
                  ))}
                </select>
              </label>
              <label>
                OS type
                <select value={createOsType} onChange={(event) => setCreateOsType(event.target.value)}>
                  <option value="windows">Windows</option>
                  <option value="linux">Linux</option>
                </select>
              </label>
              <label>
                Drive size (GiB)
                <input
                  type="number"
                  min={10}
                  max={1024}
                  value={createDriveSizeGiB}
                  onChange={(event) => setCreateDriveSizeGiB(Number(event.target.value || 64))}
                />
              </label>
              <label>
                Default CPU cores
                <input
                  type="number"
                  min={1}
                  max={16}
                  value={createDefaultCpuCores}
                  onChange={(event) => setCreateDefaultCpuCores(Number(event.target.value || 2))}
                />
              </label>
              <label>
                Default RAM (MiB)
                <input
                  type="number"
                  min={512}
                  max={65536}
                  step={256}
                  value={createDefaultRamMb}
                  onChange={(event) => setCreateDefaultRamMb(Number(event.target.value || 4096))}
                />
              </label>
              {isPlatformAdmin && (
                <label>
                  Catalog scope
                  <select
                    value={createSharedCatalog ? "shared" : "namespace"}
                    onChange={(event) => setCreateSharedCatalog(event.target.value === "shared")}
                  >
                    <option value="namespace">Namespace-owned</option>
                    <option value="shared">Shared (cross-namespace)</option>
                  </select>
                </label>
              )}
              <button onClick={createFromIso} disabled={creatingImage || !createName.trim() || !createIsoId}>
                {creatingImage ? "Creating..." : "Create Image"}
              </button>
              <p className="muted small">
                Scratch image creation clones a blank disk, injects installer ISO media, and can then be launched for
                update.
              </p>
            </>
          )}
        </div>
        <div>
          <h3>Golden Images</h3>
          <div className="tile-grid">
            {images.length === 0 && <div className="muted">No images.</div>}
            {images.map((img) => (
              <div key={img.id} className="tile template-tile">
                <div className="tile-header">
                  <h4>{img.name}</h4>
                  <span className="muted small">{formatGiB(img.size_bytes)}</span>
                </div>
                <>
                  <div className="muted small">{img.filename || "no filename"}</div>
                  <div className="muted small">{img.shared_catalog ? "Shared catalog" : "Namespace-owned catalog"}</div>
                  <div className="muted small">
                    Source: {img.source_kind || "uploaded"}
                    {img.installer_iso_filename ? ` | ISO: ${img.installer_iso_filename}` : ""}
                  </div>
                  <div className="muted small">
                    Update defaults: {Number(img.update_cpu_cores_default || 2)} CPU,{" "}
                    {Number(img.update_ram_mb_default || 4096)} MiB RAM
                  </div>
                  <div className="actions">
                    <button className="ghost" onClick={() => startEdit(img)}>
                      Edit
                    </button>
                    <button
                      onClick={() => launchForUpdate(img)}
                      disabled={Boolean(launchingImageId) && launchingImageId !== img.id}
                    >
                      {launchingImageId === img.id ? "Launching..." : "Launch update VM"}
                    </button>
                    <button className="danger" onClick={() => remove(img.id)}>
                      Delete
                    </button>
                  </div>
                </>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
};

export default AdminImages;
