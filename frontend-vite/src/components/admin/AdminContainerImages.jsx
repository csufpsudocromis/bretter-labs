import React, { useCallback, useEffect, useState } from "react";
import { api } from "../../api";

const DEFAULT_FORM = {
  name: "",
  image_ref: "",
};

const inferNameFromRef = (imageRef) => {
  const raw = String(imageRef || "").trim();
  if (!raw) return "";
  const withoutDigest = raw.split("@")[0];
  const lastSlash = withoutDigest.lastIndexOf("/");
  const lastColon = withoutDigest.lastIndexOf(":");
  const withoutTag = lastColon > lastSlash ? withoutDigest.slice(0, lastColon) : withoutDigest;
  const tail = withoutTag.split("/").filter(Boolean).pop();
  return tail || raw;
};

const normalizeSignatureWarning = (warning) => {
  const text = String(warning || "").trim();
  if (!text) return "";
  if (/no signatures/i.test(text)) return "Image has no signatures.";
  return text.endsWith(".") ? text : `${text}.`;
};

const AdminContainerImages = () => {
  const [images, setImages] = useState([]);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [form, setForm] = useState({ ...DEFAULT_FORM });
  const [editingId, setEditingId] = useState(null);
  const [editForm, setEditForm] = useState({ name: "", image_ref: "" });
  const [busyAction, setBusyAction] = useState("");
  const [isCreating, setIsCreating] = useState(false);

  const actionKey = (imageId, action) => `${imageId}:${action}`;
  const isBusy = (imageId, action) => busyAction === actionKey(imageId, action);
  const isImageBusy = (imageId) => busyAction.startsWith(`${imageId}:`);

  const load = useCallback(async () => {
    try {
      const res = await api.get("/admin/container-images");
      setImages(res.data || []);
      setError("");
    } catch (err) {
      setError(err.response?.data?.detail || "Failed to load container images");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const hasQueuedScans = images.some((img) => String(img.last_scan_status || "").toLowerCase() === "queued");

  useEffect(() => {
    if (!hasQueuedScans) return undefined;
    const timer = window.setInterval(() => {
      load();
    }, 3000);
    return () => window.clearInterval(timer);
  }, [hasQueuedScans, load]);

  const create = async () => {
    if (isCreating) return;
    const imageRef = String(form.image_ref || "").trim();
    const payload = {
      name: String(form.name || "").trim() || inferNameFromRef(imageRef),
      image_ref: imageRef,
    };
    if (!payload.name || !payload.image_ref) {
      setError("Image reference is required");
      return;
    }
    setIsCreating(true);
    setMessage("Adding image...");
    try {
      const res = await api.post("/admin/container-images", payload);
      const created = res.data;
      const signatureWarning = String(created?.signature_warning || "").trim();
      setForm({ ...DEFAULT_FORM });
      if (created && created.id) {
        setImages((prev) => [created, ...prev.filter((img) => img.id !== created.id)]);
      }
      setMessage(
        signatureWarning
          ? `Container image added. Warning: ${normalizeSignatureWarning(signatureWarning)}`
          : "Container image added. Pre-pull/scan running in background."
      );
      setError("");
      load();
    } catch (err) {
      setError(err.response?.data?.detail || "Failed to add container image");
      setMessage("");
    } finally {
      setIsCreating(false);
    }
  };

  const startEdit = (row) => {
    setEditingId(row.id);
    setEditForm({ name: row.name || "", image_ref: row.image_ref || "" });
  };

  const saveEdit = async () => {
    const imageId = editingId;
    if (!imageId || isImageBusy(imageId)) return;
    setBusyAction(actionKey(imageId, "save"));
    try {
      const res = await api.patch(`/admin/container-images/${imageId}`, editForm);
      const signatureWarning = String(res?.data?.signature_warning || "").trim();
      setEditingId(null);
      setEditForm({ name: "", image_ref: "" });
      setMessage(
        signatureWarning
          ? `Container image updated. Warning: ${normalizeSignatureWarning(signatureWarning)}`
          : "Container image updated"
      );
      setError("");
      load();
    } catch (err) {
      setError(err.response?.data?.detail || "Failed to update container image");
    } finally {
      setBusyAction("");
    }
  };

  const remove = async (imageId) => {
    if (isImageBusy(imageId)) return;
    setBusyAction(actionKey(imageId, "delete"));
    try {
      await api.delete(`/admin/container-images/${imageId}`);
      setMessage("Container image deleted");
      setError("");
      load();
    } catch (err) {
      setError(err.response?.data?.detail || "Failed to delete container image");
    } finally {
      setBusyAction("");
    }
  };

  const prepull = async (imageId) => {
    if (isImageBusy(imageId)) return;
    setBusyAction(actionKey(imageId, "prepull"));
    setMessage("Queueing pre-pull...");
    try {
      const res = await api.post(`/admin/container-images/${imageId}/prepull`);
      setMessage(res.data?.detail || "Pre-pull triggered");
      setError("");
    } catch (err) {
      setError(err.response?.data?.detail || "Failed to trigger pre-pull");
    } finally {
      setBusyAction("");
    }
  };

  const scan = async (imageId) => {
    if (isImageBusy(imageId)) return;
    setBusyAction(actionKey(imageId, "scan"));
    setMessage("Queueing scan...");
    try {
      await api.post(`/admin/container-images/${imageId}/scan`);
      setMessage("Scan queued");
      setError("");
      load();
    } catch (err) {
      setError(err.response?.data?.detail || "Failed to scan image");
    } finally {
      setBusyAction("");
    }
  };

  const canCreate = Boolean(String(form.image_ref || "").trim());

  return (
    <div>
      <h2>Container Images</h2>
      {message && <div className="info">{message}</div>}
      {error && <div className="error">{error}</div>}
      <div className="grid">
        <div>
          <h3>Add container image</h3>
          <div className="form">
            <label>
              Name
              <input
                value={form.name}
                placeholder="nginx"
                onChange={(e) => setForm((prev) => ({ ...prev, name: e.target.value }))}
              />
            </label>
            <label>
              Image reference
              <input
                value={form.image_ref}
                placeholder="ghcr.io/org/app:1.2.3"
                onChange={(e) => setForm((prev) => ({ ...prev, image_ref: e.target.value }))}
              />
            </label>
            <button onClick={create} disabled={!canCreate || isCreating}>
              {isCreating ? "Adding..." : "Add image"}
            </button>
            <p className="muted small">
              Supports Docker Hub, GHCR, Quay, ECR, GCR, ACR, and any OCI registry reachable by the cluster.
            </p>
          </div>
        </div>
        <div>
          <h3>Registered container images</h3>
          {hasQueuedScans && (
            <div className="muted small">A scan is running in the background. Refreshing status...</div>
          )}
          <div className="tile-grid">
            {images.length === 0 && <div className="muted">No container images yet.</div>}
            {images.map((img) => (
              <div key={img.id} className="tile template-tile">
                <div className="tile-header">
                  <h4>{img.name}</h4>
                </div>
                {editingId === img.id ? (
                  <div className="form">
                    <label>
                      Name
                      <input
                        value={editForm.name}
                        onChange={(e) => setEditForm((prev) => ({ ...prev, name: e.target.value }))}
                      />
                    </label>
                    <label>
                      Image reference
                      <input
                        value={editForm.image_ref}
                        onChange={(e) => setEditForm((prev) => ({ ...prev, image_ref: e.target.value }))}
                      />
                    </label>
                    <div className="actions container-image-actions">
                      <button className="ghost" onClick={() => setEditingId(null)} disabled={isBusy(img.id, "save")}>
                        Cancel
                      </button>
                      <button
                        onClick={saveEdit}
                        disabled={!editForm.name || !editForm.image_ref || isBusy(img.id, "save")}
                      >
                        Save
                      </button>
                    </div>
                  </div>
                ) : (
                  <>
                    <div className="muted small">{img.image_ref}</div>
                    <div className="muted small">
                      Scan: {img.last_scan_status || "never"}
                      {img.last_scan_at ? ` (${new Date(img.last_scan_at).toLocaleString()})` : ""}
                    </div>
                    {img.last_scan_summary && <div className="muted small">{img.last_scan_summary}</div>}
                    <div className="actions container-image-actions">
                      <button className="ghost" onClick={() => scan(img.id)} disabled={isImageBusy(img.id)}>
                        {isBusy(img.id, "scan") ? "Scanning..." : "Scan"}
                      </button>
                      <button className="ghost" onClick={() => prepull(img.id)} disabled={isImageBusy(img.id)}>
                        {isBusy(img.id, "prepull") ? "Queueing..." : "Pre-pull"}
                      </button>
                      <button className="ghost" onClick={() => startEdit(img)} disabled={isImageBusy(img.id)}>
                        Edit
                      </button>
                      <button className="danger" onClick={() => remove(img.id)} disabled={isImageBusy(img.id)}>
                        {isBusy(img.id, "delete") ? "Deleting..." : "Delete"}
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

export default AdminContainerImages;
