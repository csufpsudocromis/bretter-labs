import React, { useEffect, useState } from "react";
import { api } from "../../api";

const fields = [
  { key: "storage_root", label: "Storage Root" },
  { key: "kube_image_pvc", label: "Image PVC" },
  { key: "kube_vm_storage_class", label: "VM Clone StorageClass" },
  { key: "kube_namespace", label: "Namespace (read-only)", readOnly: true },
];

const statusMeta = (status) => {
  const normalized = String(status || "").toLowerCase();
  if (normalized === "error") return { label: "Error", className: "badge warn" };
  if (normalized === "warn") return { label: "Warn", className: "badge warn" };
  if (normalized === "ok") return { label: "OK", className: "badge success" };
  return { label: "Info", className: "badge" };
};

const fmtBytes = (value) => {
  const bytes = Number(value || 0);
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let size = bytes;
  let idx = 0;
  while (size >= 1024 && idx < units.length - 1) {
    size /= 1024;
    idx += 1;
  }
  if (idx === 0) return `${Math.round(size)} ${units[idx]}`;
  return `${size.toFixed(1)} ${units[idx]}`;
};

const fmtPct = (value) => {
  const num = Number(value || 0);
  return `${Number.isFinite(num) ? num.toFixed(1) : "0.0"}%`;
};

const AdminStorageSettings = () => {
  const [data, setData] = useState({
    storage_root: "",
    kube_image_pvc: "",
    kube_vm_storage_class: "",
    kube_namespace: "",
  });
  const [sources, setSources] = useState({});
  const [checks, setChecks] = useState([]);
  const [warnings, setWarnings] = useState([]);
  const [capacity, setCapacity] = useState({ namespace: "", headroom: {}, pvcs: [], warnings: [] });
  const [resizeDraft, setResizeDraft] = useState({});
  const [resizing, setResizing] = useState({});
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [clearing, setClearing] = useState(false);

  const applyResponse = (payload) => {
    setData({
      storage_root: payload?.storage_root || "",
      kube_image_pvc: payload?.kube_image_pvc || "",
      kube_vm_storage_class: payload?.kube_vm_storage_class || "",
      kube_namespace: payload?.kube_namespace || "",
    });
    setSources(payload?.sources || {});
    setChecks(payload?.checks || []);
    setWarnings(payload?.warnings || []);
    const cap = payload?.capacity || { namespace: "", headroom: {}, pvcs: [], warnings: [] };
    setCapacity(cap);
    const nextDraft = {};
    (cap?.pvcs || []).forEach((pvc) => {
      nextDraft[pvc.pvc_name] = String(Math.max(1, Number(pvc.current_size_gib || pvc.min_size_gib || 1)));
    });
    setResizeDraft(nextDraft);
  };

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const res = await api.get("/admin/settings/storage");
      applyResponse(res.data || {});
    } catch (err) {
      setError(err.response?.data?.detail || "Failed to load storage settings");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const save = async () => {
    setSaving(true);
    setError("");
    setMessage("");
    try {
      const res = await api.patch("/admin/settings/storage", {
        storage_root: data.storage_root,
        kube_image_pvc: data.kube_image_pvc,
        kube_vm_storage_class: data.kube_vm_storage_class,
      });
      applyResponse(res.data || {});
      setMessage("Storage settings saved and applied.");
    } catch (err) {
      setError(err.response?.data?.detail || "Failed to save storage settings");
    } finally {
      setSaving(false);
    }
  };

  const clearOverrides = async () => {
    setClearing(true);
    setError("");
    setMessage("");
    try {
      const res = await api.patch("/admin/settings/storage", { clear_overrides: true });
      applyResponse(res.data || {});
      setMessage("Storage overrides cleared; environment defaults are active.");
    } catch (err) {
      setError(err.response?.data?.detail || "Failed to clear storage overrides");
    } finally {
      setClearing(false);
    }
  };

  const resizePVC = async (pvc) => {
    const targetSizeGiB = Number.parseInt(resizeDraft[pvc.pvc_name] || `${pvc.current_size_gib || 1}`, 10);
    if (!Number.isFinite(targetSizeGiB) || targetSizeGiB <= 0) {
      setError("Target size must be a positive whole number in GiB.");
      return;
    }
    setResizing((prev) => ({ ...prev, [pvc.pvc_name]: true }));
    setError("");
    setMessage("");
    try {
      const res = await api.post("/admin/settings/storage/resize", {
        pvc_name: pvc.pvc_name,
        namespace: pvc.namespace,
        target_size_gib: targetSizeGiB,
      });
      const detail = res.data?.detail ? ` ${res.data.detail}` : "";
      setMessage(
        `Resize requested for ${pvc.pvc_name}: ${res.data?.old_size_gib || pvc.current_size_gib}Gi -> ${
          res.data?.new_size_gib || targetSizeGiB
        }Gi.${detail}`
      );
      await load();
    } catch (err) {
      setError(err.response?.data?.detail || "Failed to resize PVC");
    } finally {
      setResizing((prev) => ({ ...prev, [pvc.pvc_name]: false }));
    }
  };

  const groupedPVCs = {};
  (capacity?.pvcs || []).forEach((pvc) => {
    const key = pvc.category_label || pvc.category || "Other";
    if (!groupedPVCs[key]) groupedPVCs[key] = [];
    groupedPVCs[key].push(pvc);
  });
  const groupNames = Object.keys(groupedPVCs).sort((a, b) => a.localeCompare(b));
  const headroom = capacity?.headroom || {};
  const headroomUtil = Number(headroom?.utilization_pct || 0);
  const headroomRisk = String(headroom?.risk || "unknown").toLowerCase();
  const headroomBadge =
    headroomRisk === "critical" || headroomRisk === "high"
      ? "badge warn"
      : headroomRisk === "warning"
        ? "badge warn"
        : headroomRisk === "healthy"
          ? "badge success"
          : "badge";

  return (
    <div>
      <h2>Storage Options</h2>
      <p className="muted small">Configure image storage and verify cluster readiness for clone-based VM launches.</p>
      <div className="actions" style={{ marginBottom: "1rem" }}>
        <button className="ghost" onClick={load} disabled={loading || saving || clearing}>
          {loading ? "Refreshing..." : "Refresh"}
        </button>
      </div>
      {error && <div className="error">{error}</div>}
      {message && <div className="info">{message}</div>}

      <div className="card">
        <div className="form" style={{ maxWidth: "640px" }}>
          {fields.map((f) => (
            <label key={f.key}>
              {f.label}
              <input
                value={data[f.key] || ""}
                onChange={(e) => setData({ ...data, [f.key]: e.target.value })}
                disabled={Boolean(f.readOnly)}
              />
              {!f.readOnly && sources[f.key] ? <div className="muted small">Source: {sources[f.key]}</div> : null}
            </label>
          ))}
          <div className="actions">
            <button onClick={save} disabled={saving || clearing || loading}>
              {saving ? "Saving..." : "Save"}
            </button>
            <button className="ghost" onClick={clearOverrides} disabled={saving || clearing || loading}>
              {clearing ? "Clearing..." : "Use Env Defaults"}
            </button>
          </div>
        </div>
      </div>

      <div className="card">
        <h3>Validation</h3>
        {checks.length === 0 ? (
          <div className="muted small">No validation checks yet.</div>
        ) : (
          <div className="tile-grid" style={{ marginTop: "0.75rem" }}>
            {checks.map((check) => {
              const badge = statusMeta(check.status);
              return (
                <div key={check.key} className="tile template-tile">
                  <div className="tile-header">
                    <h4>{check.title}</h4>
                    <span className={badge.className}>{badge.label}</span>
                  </div>
                  <div className="muted small">{check.detail}</div>
                </div>
              );
            })}
          </div>
        )}
        {warnings.length > 0 && (
          <div style={{ marginTop: "1rem" }}>
            <h4 style={{ marginBottom: "0.5rem" }}>Warnings</h4>
            {warnings.map((item, idx) => (
              <div key={`warn-${idx}`} className="muted small" style={{ marginBottom: "0.35rem" }}>
                - {item}
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="card">
        <h3>Capacity And Resize Controls</h3>
        <p className="muted small" style={{ marginBottom: "0.75rem" }}>
          Grow-only resize controls for PVC-backed storage. Shrink requires migration to a new PVC.
        </p>
        <div className="tile template-tile" style={{ marginBottom: "0.75rem" }}>
          <div className="tile-header">
            <h4>Cluster Storage Headroom</h4>
            <span className={headroomBadge}>{headroomRisk.toUpperCase()}</span>
          </div>
          <div className="specs">
            <span>Provider: {headroom.provider || "unknown"}</span>
            <span>Capacity: {fmtBytes(headroom.capacity_bytes)}</span>
            <span>Allocated (provisioned): {fmtBytes(headroom.allocated_bytes)}</span>
            <span>Used (actual): {fmtBytes(headroom.used_bytes || 0)}</span>
            <span>Free Unallocated: {fmtBytes(headroom.free_unallocated_bytes)}</span>
            <span>Utilization: {fmtPct(headroomUtil)}</span>
          </div>
          <div className="muted small">{headroom.detail || "No storage headroom details reported."}</div>
        </div>
        {capacity?.warnings?.length > 0 && (
          <div style={{ marginBottom: "0.75rem" }}>
            {capacity.warnings.map((item, idx) => (
              <div key={`capwarn-${idx}`} className="muted small" style={{ marginBottom: "0.25rem" }}>
                - {item}
              </div>
            ))}
          </div>
        )}
        {groupNames.length === 0 ? (
          <div className="muted small">No PVC capacity targets discovered.</div>
        ) : (
          groupNames.map((group) => (
            <div key={group} style={{ marginBottom: "1rem" }}>
              <h4 style={{ marginBottom: "0.5rem" }}>{group}</h4>
              <div className="tile-grid">
                {groupedPVCs[group].map((pvc) => {
                  const minSize = Number(pvc.min_size_gib || pvc.current_size_gib || 1);
                  const currentSize = Number(pvc.current_size_gib || minSize);
                  const maxRecommended = Math.max(minSize + 1, Number(pvc.max_recommended_size_gib || minSize + 200));
                  const draftRaw = resizeDraft[pvc.pvc_name];
                  const draftValue = Number.parseInt(String(draftRaw ?? currentSize), 10);
                  const safeDraft = Number.isFinite(draftValue) ? draftValue : currentSize;
                  const inProgress = Boolean(resizing[pvc.pvc_name]);
                  return (
                    <div key={pvc.pvc_name} className="tile template-tile">
                      <div className="tile-header">
                        <h4>{pvc.pvc_name}</h4>
                        <span className={pvc.allow_resize ? "badge success" : "badge warn"}>
                          {pvc.allow_resize ? "Grow Enabled" : "Read-Only"}
                        </span>
                      </div>
                      <div className="specs">
                        <span>Namespace: {pvc.namespace}</span>
                        <span>StorageClass: {pvc.storage_class || "unspecified"}</span>
                        <span>Phase: {pvc.phase || "unknown"}</span>
                        <span>Requested: {fmtBytes(pvc.requested_bytes)}</span>
                        <span>Capacity: {fmtBytes(pvc.capacity_bytes)}</span>
                        <span>Used (actual): {pvc.used_bytes_known ? fmtBytes(pvc.used_bytes) : "unknown"}</span>
                        <span>Current: {currentSize} GiB</span>
                      </div>
                      {pvc.used_by?.length > 0 ? (
                        <div className="muted small" style={{ marginBottom: "0.5rem" }}>
                          Used by: {pvc.used_by.join(", ")}
                        </div>
                      ) : (
                        <div className="muted small" style={{ marginBottom: "0.5rem" }}>
                          Used by: none
                        </div>
                      )}
                      <label className="muted small" style={{ marginBottom: "0.35rem", display: "block" }}>
                        Target size (GiB)
                      </label>
                      <input
                        type="range"
                        min={minSize}
                        max={maxRecommended}
                        step={1}
                        value={Math.min(Math.max(safeDraft, minSize), maxRecommended)}
                        disabled={!pvc.allow_resize || inProgress || loading || saving || clearing}
                        onChange={(e) =>
                          setResizeDraft((prev) => ({
                            ...prev,
                            [pvc.pvc_name]: String(Number.parseInt(e.target.value, 10)),
                          }))
                        }
                      />
                      <div className="specs" style={{ marginTop: "0.45rem" }}>
                        <span>Min: {minSize} GiB</span>
                      </div>
                      <div className="actions" style={{ marginTop: "0.5rem" }}>
                        <input
                          type="number"
                          min={minSize}
                          max={maxRecommended}
                          step={1}
                          value={safeDraft}
                          disabled={!pvc.allow_resize || inProgress || loading || saving || clearing}
                          onChange={(e) =>
                            setResizeDraft((prev) => ({
                              ...prev,
                              [pvc.pvc_name]: e.target.value,
                            }))
                          }
                          style={{ width: "120px" }}
                        />
                        <button
                          onClick={() => resizePVC(pvc)}
                          disabled={
                            !pvc.allow_resize ||
                            inProgress ||
                            loading ||
                            saving ||
                            clearing ||
                            !Number.isFinite(safeDraft) ||
                            safeDraft <= currentSize
                          }
                        >
                          {inProgress ? "Resizing..." : "Apply Grow"}
                        </button>
                      </div>
                      {!pvc.allow_resize && <div className="muted small">Reason: {pvc.resize_reason}</div>}
                      {pvc.allow_resize && safeDraft <= currentSize && (
                        <div className="muted small">Choose a value greater than current size.</div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
};

export default AdminStorageSettings;
