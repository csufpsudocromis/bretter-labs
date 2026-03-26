import React, { useEffect, useState } from "react";
import { api } from "../../api";

const DEFAULT_FORM = {
  namespace: "",
  team_label: "default",
  security_profile: "baseline",
  enforce_network_policies: true,
  max_pods: "200",
  max_services: "100",
  max_persistent_volume_claims: "200",
  requests_cpu: "8",
  limits_cpu: "16",
  requests_memory: "16Gi",
  limits_memory: "32Gi",
  requests_storage: "2Ti",
  limit_min_cpu: "50m",
  limit_min_memory: "64Mi",
  limit_default_request_cpu: "250m",
  limit_default_request_memory: "256Mi",
  limit_default_cpu: "2",
  limit_default_memory: "2Gi",
  limit_max_cpu: "8",
  limit_max_memory: "16Gi",
  enabled: true,
};

const profileOptions = [
  { value: "restricted", label: "Restricted" },
  { value: "baseline", label: "Baseline" },
  { value: "privileged", label: "Privileged" },
];

const AdminNamespacesSettings = () => {
  const [rows, setRows] = useState([]);
  const [editingNamespace, setEditingNamespace] = useState("");
  const [form, setForm] = useState({ ...DEFAULT_FORM });
  const [message, setMessage] = useState("");
  const [saving, setSaving] = useState(false);

  const load = async () => {
    try {
      const res = await api.get("/admin/settings/namespaces");
      setRows(Array.isArray(res.data) ? res.data : []);
    } catch (err) {
      setMessage(err.response?.data?.detail || "Failed to load managed namespaces");
    }
  };

  useEffect(() => {
    load();
  }, []);

  const updateField = (key, value) => setForm((prev) => ({ ...prev, [key]: value }));

  const resetForm = () => {
    setEditingNamespace("");
    setForm({ ...DEFAULT_FORM });
  };

  const startEdit = (row) => {
    setEditingNamespace(String(row.namespace || ""));
    setForm({
      namespace: String(row.namespace || ""),
      team_label: String(row.team_label || "default"),
      security_profile: String(row.security_profile || "baseline"),
      enforce_network_policies: Boolean(row.enforce_network_policies),
      max_pods: String(row.max_pods || "200"),
      max_services: String(row.max_services || "100"),
      max_persistent_volume_claims: String(row.max_persistent_volume_claims || "200"),
      requests_cpu: String(row.requests_cpu || "8"),
      limits_cpu: String(row.limits_cpu || "16"),
      requests_memory: String(row.requests_memory || "16Gi"),
      limits_memory: String(row.limits_memory || "32Gi"),
      requests_storage: String(row.requests_storage || "2Ti"),
      limit_min_cpu: String(row.limit_min_cpu || "50m"),
      limit_min_memory: String(row.limit_min_memory || "64Mi"),
      limit_default_request_cpu: String(row.limit_default_request_cpu || "250m"),
      limit_default_request_memory: String(row.limit_default_request_memory || "256Mi"),
      limit_default_cpu: String(row.limit_default_cpu || "2"),
      limit_default_memory: String(row.limit_default_memory || "2Gi"),
      limit_max_cpu: String(row.limit_max_cpu || "8"),
      limit_max_memory: String(row.limit_max_memory || "16Gi"),
      enabled: Boolean(row.enabled),
    });
    setMessage("");
  };

  const buildPayload = () => ({
    team_label: String(form.team_label || "").trim() || "default",
    security_profile: String(form.security_profile || "baseline")
      .trim()
      .toLowerCase(),
    enforce_network_policies: Boolean(form.enforce_network_policies),
    max_pods: String(form.max_pods || "").trim() || "200",
    max_services: String(form.max_services || "").trim() || "100",
    max_persistent_volume_claims: String(form.max_persistent_volume_claims || "").trim() || "200",
    requests_cpu: String(form.requests_cpu || "").trim() || "8",
    limits_cpu: String(form.limits_cpu || "").trim() || "16",
    requests_memory: String(form.requests_memory || "").trim() || "16Gi",
    limits_memory: String(form.limits_memory || "").trim() || "32Gi",
    requests_storage: String(form.requests_storage || "").trim() || "2Ti",
    limit_min_cpu: String(form.limit_min_cpu || "").trim() || "50m",
    limit_min_memory: String(form.limit_min_memory || "").trim() || "64Mi",
    limit_default_request_cpu: String(form.limit_default_request_cpu || "").trim() || "250m",
    limit_default_request_memory: String(form.limit_default_request_memory || "").trim() || "256Mi",
    limit_default_cpu: String(form.limit_default_cpu || "").trim() || "2",
    limit_default_memory: String(form.limit_default_memory || "").trim() || "2Gi",
    limit_max_cpu: String(form.limit_max_cpu || "").trim() || "8",
    limit_max_memory: String(form.limit_max_memory || "").trim() || "16Gi",
    enabled: Boolean(form.enabled),
  });

  const save = async () => {
    const namespace = String(form.namespace || "")
      .trim()
      .toLowerCase();
    if (!namespace) {
      setMessage("Namespace is required");
      return;
    }
    setSaving(true);
    try {
      if (editingNamespace) {
        await api.patch(`/admin/settings/namespaces/${encodeURIComponent(editingNamespace)}`, buildPayload());
        setMessage(`Managed namespace ${namespace} updated.`);
      } else {
        await api.post("/admin/settings/namespaces", { namespace, ...buildPayload() });
        setMessage(`Managed namespace ${namespace} created.`);
      }
      resetForm();
      await load();
    } catch (err) {
      setMessage(err.response?.data?.detail || "Failed to save managed namespace");
    } finally {
      setSaving(false);
    }
  };

  const reconcile = async (namespace) => {
    setSaving(true);
    try {
      await api.post(`/admin/settings/namespaces/${encodeURIComponent(namespace)}/reconcile`);
      setMessage(`Reconciled namespace ${namespace}.`);
      await load();
    } catch (err) {
      setMessage(err.response?.data?.detail || "Failed to reconcile namespace");
    } finally {
      setSaving(false);
    }
  };

  const remove = async (namespace) => {
    if (!window.confirm(`Delete managed namespace ${namespace}? This blocks when active labs are present.`)) {
      return;
    }
    setSaving(true);
    try {
      await api.delete(`/admin/settings/namespaces/${encodeURIComponent(namespace)}`);
      if (editingNamespace === namespace) {
        resetForm();
      }
      setMessage(`Deleted managed namespace ${namespace}.`);
      await load();
    } catch (err) {
      setMessage(err.response?.data?.detail || "Failed to delete managed namespace");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div>
      <h2>Managed Namespaces</h2>
      <p>Add or remove runtime namespaces and tune namespace-scoped resource/security controls.</p>
      {message && <div className="info">{message}</div>}
      <div className="grid">
        <div>
          <h3>{editingNamespace ? "Edit managed namespace" : "Add managed namespace"}</h3>
          <div className="form">
            <label>
              Namespace
              <input
                value={form.namespace}
                onChange={(e) => updateField("namespace", e.target.value.toLowerCase())}
                placeholder="labs-team-default"
                disabled={Boolean(editingNamespace)}
              />
            </label>
            <label>
              Team label
              <input value={form.team_label} onChange={(e) => updateField("team_label", e.target.value)} />
            </label>
            <label>
              Security profile
              <select value={form.security_profile} onChange={(e) => updateField("security_profile", e.target.value)}>
                {profileOptions.map((item) => (
                  <option key={item.value} value={item.value}>
                    {item.label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Enforce default network policies
              <select
                value={form.enforce_network_policies ? "yes" : "no"}
                onChange={(e) => updateField("enforce_network_policies", e.target.value === "yes")}
              >
                <option value="yes">Enabled</option>
                <option value="no">Disabled</option>
              </select>
            </label>
            <label>
              Pod quota
              <input value={form.max_pods} onChange={(e) => updateField("max_pods", e.target.value)} />
            </label>
            <label>
              Service quota
              <input value={form.max_services} onChange={(e) => updateField("max_services", e.target.value)} />
            </label>
            <label>
              PVC quota
              <input
                value={form.max_persistent_volume_claims}
                onChange={(e) => updateField("max_persistent_volume_claims", e.target.value)}
              />
            </label>
            <label>
              Requests CPU
              <input value={form.requests_cpu} onChange={(e) => updateField("requests_cpu", e.target.value)} />
            </label>
            <label>
              Limits CPU
              <input value={form.limits_cpu} onChange={(e) => updateField("limits_cpu", e.target.value)} />
            </label>
            <label>
              Requests memory
              <input value={form.requests_memory} onChange={(e) => updateField("requests_memory", e.target.value)} />
            </label>
            <label>
              Limits memory
              <input value={form.limits_memory} onChange={(e) => updateField("limits_memory", e.target.value)} />
            </label>
            <label>
              Requests storage
              <input value={form.requests_storage} onChange={(e) => updateField("requests_storage", e.target.value)} />
            </label>
            <label>
              Min CPU
              <input value={form.limit_min_cpu} onChange={(e) => updateField("limit_min_cpu", e.target.value)} />
            </label>
            <label>
              Min memory
              <input value={form.limit_min_memory} onChange={(e) => updateField("limit_min_memory", e.target.value)} />
            </label>
            <label>
              Default request CPU
              <input
                value={form.limit_default_request_cpu}
                onChange={(e) => updateField("limit_default_request_cpu", e.target.value)}
              />
            </label>
            <label>
              Default request memory
              <input
                value={form.limit_default_request_memory}
                onChange={(e) => updateField("limit_default_request_memory", e.target.value)}
              />
            </label>
            <label>
              Default CPU limit
              <input
                value={form.limit_default_cpu}
                onChange={(e) => updateField("limit_default_cpu", e.target.value)}
              />
            </label>
            <label>
              Default memory limit
              <input
                value={form.limit_default_memory}
                onChange={(e) => updateField("limit_default_memory", e.target.value)}
              />
            </label>
            <label>
              Max CPU
              <input value={form.limit_max_cpu} onChange={(e) => updateField("limit_max_cpu", e.target.value)} />
            </label>
            <label>
              Max memory
              <input value={form.limit_max_memory} onChange={(e) => updateField("limit_max_memory", e.target.value)} />
            </label>
            <label>
              Enabled
              <select
                value={form.enabled ? "yes" : "no"}
                onChange={(e) => updateField("enabled", e.target.value === "yes")}
              >
                <option value="yes">Enabled</option>
                <option value="no">Disabled</option>
              </select>
            </label>
            <div className="actions">
              {editingNamespace && (
                <button type="button" className="ghost" onClick={resetForm}>
                  Cancel
                </button>
              )}
              <button type="button" onClick={save} disabled={saving}>
                {editingNamespace ? "Save namespace" : "Add namespace"}
              </button>
            </div>
          </div>
        </div>
        <div>
          <h3>Configured namespaces</h3>
          <div className="tile-grid">
            {rows.length === 0 && <div className="muted">No managed namespaces configured.</div>}
            {rows.map((row) => (
              <div key={row.id} className="tile">
                <div className="tile-header">
                  <h4>{row.namespace}</h4>
                  <span className={`badge ${row.enabled ? "success" : "warn"}`}>
                    {row.enabled ? "Enabled" : "Disabled"}
                  </span>
                </div>
                <div className="small muted">Team label: {row.team_label}</div>
                <div className="small muted">Security profile: {row.security_profile}</div>
                <div className="small muted">
                  Cluster status: {row.present_in_cluster ? "Present" : "Missing"} | Active labs:{" "}
                  {row.active_total_instances}
                </div>
                <div className="small muted">
                  VM active: {row.active_vm_instances} | Container active: {row.active_container_instances}
                </div>
                <div className="actions" style={{ marginTop: "0.75rem" }}>
                  <button type="button" className="ghost" onClick={() => startEdit(row)} disabled={saving}>
                    Edit
                  </button>
                  <button
                    type="button"
                    className="ghost"
                    onClick={() => reconcile(row.namespace)}
                    disabled={saving || !row.enabled}
                  >
                    Reconcile
                  </button>
                  <button type="button" className="danger" onClick={() => remove(row.namespace)} disabled={saving}>
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

export default AdminNamespacesSettings;
