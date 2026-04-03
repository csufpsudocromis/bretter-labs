import React, { useEffect, useState } from "react";
import { api } from "../../api";

const gib = (bytes) => {
  const value = Number(bytes || 0);
  if (!Number.isFinite(value) || value <= 0) return "0 GiB";
  return `${(value / 1024 ** 3).toFixed(value >= 10 * 1024 ** 3 ? 0 : 1)} GiB`;
};

const AdminIsoImages = () => {
  const [isoImages, setIsoImages] = useState([]);
  const [isPlatformAdmin, setIsPlatformAdmin] = useState(false);
  const [uploadFile, setUploadFile] = useState(null);
  const [uploadName, setUploadName] = useState("");
  const [uploadSharedCatalog, setUploadSharedCatalog] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [editId, setEditId] = useState("");
  const [editName, setEditName] = useState("");
  const [editSharedCatalog, setEditSharedCatalog] = useState(false);

  const load = async () => {
    try {
      const [res, me] = await Promise.all([api.get("/admin/iso-images"), api.get("/auth/me")]);
      setIsoImages(Array.isArray(res.data) ? res.data : []);
      setIsPlatformAdmin(
        String(me?.data?.role || "")
          .trim()
          .toLowerCase() === "platform_admin"
      );
      setError("");
    } catch (err) {
      setError(err.response?.data?.detail || "Failed to load ISO images");
    }
  };

  useEffect(() => {
    load();
  }, []);

  const upload = async () => {
    if (!uploadFile) return;
    setUploading(true);
    setUploadProgress(0);
    setMessage("");
    setError("");
    try {
      const form = new FormData();
      form.append("file", uploadFile);
      const params = new URLSearchParams();
      if (uploadName.trim()) {
        params.set("name", uploadName.trim());
      }
      if (isPlatformAdmin && uploadSharedCatalog) {
        params.set("shared_catalog", "true");
      }
      const query = params.toString();
      await api.post(`/admin/iso-images${query ? `?${query}` : ""}`, form, {
        headers: { "Content-Type": "multipart/form-data" },
        onUploadProgress: (evt) => {
          if (!evt.total) return;
          const percent = Math.min(100, Math.round((evt.loaded / evt.total) * 100));
          setUploadProgress(percent);
        },
      });
      setUploadFile(null);
      setUploadName("");
      setUploadSharedCatalog(false);
      setMessage("ISO uploaded");
      load();
    } catch (err) {
      setError(err.response?.data?.detail || "ISO upload failed");
    } finally {
      setUploading(false);
      setUploadProgress(0);
    }
  };

  const startEdit = (row) => {
    setEditId(row.id);
    setEditName(String(row.name || ""));
    setEditSharedCatalog(Boolean(row.shared_catalog));
  };

  const cancelEdit = () => {
    setEditId("");
    setEditName("");
    setEditSharedCatalog(false);
  };

  const saveEdit = async () => {
    if (!editId) return;
    try {
      const payload = { name: editName.trim() || "ISO image" };
      if (isPlatformAdmin) {
        payload.shared_catalog = Boolean(editSharedCatalog);
      }
      await api.patch(`/admin/iso-images/${editId}`, payload);
      setMessage("ISO updated");
      cancelEdit();
      load();
    } catch (err) {
      setError(err.response?.data?.detail || "Failed to update ISO");
    }
  };

  const remove = async (id) => {
    try {
      await api.delete(`/admin/iso-images/${id}`);
      setMessage("ISO deleted");
      load();
    } catch (err) {
      setError(err.response?.data?.detail || "Delete failed");
    }
  };

  return (
    <div>
      <h2>ISO Images</h2>
      {message && <div className="info">{message}</div>}
      {error && <div className="error">{error}</div>}
      <div className="grid">
        <div>
          {editId ? (
            <>
              <h3>Edit ISO Image</h3>
              <div className="form">
                <label>
                  Name
                  <input value={editName} onChange={(event) => setEditName(event.target.value)} />
                </label>
                {isPlatformAdmin && (
                  <label>
                    Catalog scope
                    <select
                      value={editSharedCatalog ? "shared" : "namespace"}
                      onChange={(event) => setEditSharedCatalog(event.target.value === "shared")}
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
          ) : (
            <>
              <h3>Upload ISO</h3>
              <label>
                ISO file
                <input
                  type="file"
                  accept=".iso,application/x-iso9660-image,application/octet-stream"
                  onChange={(event) => setUploadFile(event.target.files?.[0] || null)}
                />
              </label>
              <label>
                Display name
                <input
                  value={uploadName}
                  onChange={(event) => setUploadName(event.target.value)}
                  placeholder={uploadFile?.name || "Windows 11 Installer"}
                />
              </label>
              {isPlatformAdmin && (
                <label>
                  Catalog scope
                  <select
                    value={uploadSharedCatalog ? "shared" : "namespace"}
                    onChange={(event) => setUploadSharedCatalog(event.target.value === "shared")}
                  >
                    <option value="namespace">Namespace-owned</option>
                    <option value="shared">Shared (cross-namespace)</option>
                  </select>
                </label>
              )}
              <button onClick={upload} disabled={!uploadFile || uploading}>
                {uploading ? `Uploading (${uploadProgress}%)` : "Upload ISO"}
              </button>
              {uploading && <p className="muted small">Uploading from browser: {uploadProgress}%</p>}
              <p className="muted small">
                ISO files are stored in dedicated ISO storage and can be used by Create Image.
              </p>
            </>
          )}
        </div>
        <div>
          <h3>Available ISO images</h3>
          <div className="tile-grid">
            {isoImages.length === 0 && <div className="muted">No ISO images.</div>}
            {isoImages.map((row) => (
              <div key={row.id} className="tile template-tile">
                <div className="tile-header">
                  <h4>{row.name || row.filename}</h4>
                  <span className="muted small">{gib(row.size_bytes)}</span>
                </div>
                <div className="muted small">{row.filename}</div>
                <div className="muted small">{row.shared_catalog ? "Shared catalog" : "Namespace-owned catalog"}</div>
                <div className="actions">
                  <button className="ghost" onClick={() => startEdit(row)}>
                    Edit
                  </button>
                  <button className="danger" onClick={() => remove(row.id)}>
                    Delete
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

export default AdminIsoImages;
