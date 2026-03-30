import React, { useEffect, useState } from "react";
import { api } from "../../api";

const DEFAULT_FORM = {
  namespace: "",
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
  idle_timeout_minutes_default: 30,
  vm_auto_delete_minutes_default: 60,
  container_auto_delete_minutes_default: 60,
  queue_max_pending: 25,
  upload_max_bytes: 60 * 1024 * 1024 * 1024,
  enabled: true,
};

const profileOptions = [
  { value: "restricted", label: "Restricted" },
  { value: "baseline", label: "Baseline" },
  { value: "privileged", label: "Privileged" },
];

const FIELD_HELP = {
  namespace: "Kubernetes namespace to manage. Use lowercase DNS-style names.",
  security_profile: "Pod Security Admission profile applied as enforce/audit/warn labels.",
  enforce_network_policies: "When enabled, applies default-deny plus same-namespace and DNS allow policies.",
  max_pods: "Maximum number of pods allowed in this namespace.",
  max_services: "Maximum number of services allowed in this namespace.",
  max_persistent_volume_claims: "Maximum number of PVCs allowed in this namespace.",
  requests_cpu: "Total CPU requests quota across all workloads in this namespace.",
  limits_cpu: "Total CPU limits quota across all workloads in this namespace.",
  requests_memory: "Total memory requests quota across all workloads in this namespace.",
  limits_memory: "Total memory limits quota across all workloads in this namespace.",
  requests_storage: "Total requested persistent storage quota (across PVCs).",
  limit_min_cpu: "Minimum CPU a container can request/limit (LimitRange min).",
  limit_min_memory: "Minimum memory a container can request/limit (LimitRange min).",
  limit_default_request_cpu: "Default CPU request applied when a container omits requests.",
  limit_default_request_memory: "Default memory request applied when a container omits requests.",
  limit_default_cpu: "Default CPU limit applied when a container omits limits.",
  limit_default_memory: "Default memory limit applied when a container omits limits.",
  limit_max_cpu: "Maximum CPU a single container can request/limit (LimitRange max).",
  limit_max_memory: "Maximum memory a single container can request/limit (LimitRange max).",
  idle_timeout_minutes_default: "Namespace default/cap for lab idle timeout (auto-stop).",
  vm_auto_delete_minutes_default: "Namespace default/cap for VM auto-delete after stop/completion.",
  container_auto_delete_minutes_default: "Namespace default/cap for container auto-delete after stop/completion.",
  queue_max_pending: "Maximum queued container launches allowed in this namespace.",
  upload_max_bytes: "Maximum VM image upload size allowed in this namespace (bytes).",
  enabled: "Disable to keep config without reconciliation; enable to enforce in-cluster resources.",
};

const AdminNamespacesSettings = () => {
  const [rows, setRows] = useState([]);
  const [observabilityRows, setObservabilityRows] = useState([]);
  const [editingNamespace, setEditingNamespace] = useState("");
  const [form, setForm] = useState({ ...DEFAULT_FORM });
  const [message, setMessage] = useState("");
  const [saving, setSaving] = useState(false);

  const load = async () => {
    try {
      const res = await api.get("/admin/settings/namespaces");
      setRows(Array.isArray(res.data) ? res.data : []);
      const obs = await api.get("/admin/settings/namespaces/observability");
      setObservabilityRows(Array.isArray(obs.data) ? obs.data : []);
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
      idle_timeout_minutes_default: Number(row.idle_timeout_minutes_default || 30),
      vm_auto_delete_minutes_default: Number(row.vm_auto_delete_minutes_default || 60),
      container_auto_delete_minutes_default: Number(row.container_auto_delete_minutes_default || 60),
      queue_max_pending: Number(row.queue_max_pending || 25),
      upload_max_bytes: Number(row.upload_max_bytes || 60 * 1024 * 1024 * 1024),
      enabled: Boolean(row.enabled),
    });
    setMessage("");
  };

  const buildPayload = () => ({
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
    idle_timeout_minutes_default: Math.max(1, Number(form.idle_timeout_minutes_default || 30)),
    vm_auto_delete_minutes_default: Math.max(1, Number(form.vm_auto_delete_minutes_default || 60)),
    container_auto_delete_minutes_default: Math.max(1, Number(form.container_auto_delete_minutes_default || 60)),
    queue_max_pending: Math.max(1, Number(form.queue_max_pending || 25)),
    upload_max_bytes: Math.max(1, Number(form.upload_max_bytes || 60 * 1024 * 1024 * 1024)),
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

  const reconcileAll = async () => {
    setSaving(true);
    try {
      const res = await api.post("/admin/settings/namespaces/reconcile-all");
      const summary = res?.data || {};
      setMessage(
        `Reconciled namespaces: total=${Number(summary.total || 0)} succeeded=${Number(
          summary.succeeded || 0
        )} failed=${Number(summary.failed || 0)}.`
      );
      await load();
    } catch (err) {
      setMessage(err.response?.data?.detail || "Failed to reconcile all namespaces");
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
      const res = await api.delete(`/admin/settings/namespaces/${encodeURIComponent(namespace)}`);
      const report = res?.data || {};
      if (editingNamespace === namespace) {
        resetForm();
      }
      setMessage(
        `Decommissioned ${namespace}. DB records deleted: ${Number(
          report.deleted_database_records || 0
        )}, cluster resources deleted: ${Number(report.deleted_cluster_resources || 0)}.`
      );
      await load();
    } catch (err) {
      if (Number(err?.response?.status || 0) === 409) {
        const detail = String(err.response?.data?.detail || "");
        const force = window.confirm(
          `${detail || "Namespace has active labs."}\n\nForce cleanup and decommission anyway?`
        );
        if (force) {
          try {
            const res = await api.post(
              `/admin/settings/namespaces/${encodeURIComponent(namespace)}/decommission`,
              null,
              {
                params: { force_cleanup: "true" },
              }
            );
            const report = res?.data || {};
            if (editingNamespace === namespace) {
              resetForm();
            }
            setMessage(
              `Forced decommission completed for ${namespace}. DB records deleted: ${Number(
                report.deleted_database_records || 0
              )}, cluster resources deleted: ${Number(report.deleted_cluster_resources || 0)}.`
            );
            await load();
            return;
          } catch (forceErr) {
            setMessage(forceErr.response?.data?.detail || "Failed to force decommission namespace");
            return;
          }
        }
      }
      setMessage(err.response?.data?.detail || "Failed to delete managed namespace");
    } finally {
      setSaving(false);
    }
  };

  const obsByNamespace = new Map(observabilityRows.map((row) => [String(row.namespace || ""), row]));
  const formatDuration = (seconds) => {
    const total = Math.max(0, Number(seconds || 0));
    if (!Number.isFinite(total) || total <= 0) return "0s";
    const mins = Math.floor(total / 60);
    const rem = Math.floor(total % 60);
    if (mins <= 0) return `${rem}s`;
    return `${mins}m ${rem}s`;
  };

  return (
    <div>
      <h2>Managed Namespaces</h2>
      <p>Add or remove runtime namespaces and tune namespace-scoped resource/security controls.</p>
      {message && <div className="info">{message}</div>}
      <div className="actions" style={{ marginBottom: "0.75rem" }}>
        <button type="button" className="ghost" onClick={load} disabled={saving}>
          Refresh
        </button>
        <button type="button" className="ghost" onClick={reconcileAll} disabled={saving}>
          Reconcile All
        </button>
      </div>
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
              <span className="muted small">{FIELD_HELP.namespace}</span>
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
              <span className="muted small">{FIELD_HELP.security_profile}</span>
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
              <span className="muted small">{FIELD_HELP.enforce_network_policies}</span>
            </label>
            <label>
              Pod quota
              <input value={form.max_pods} onChange={(e) => updateField("max_pods", e.target.value)} />
              <span className="muted small">{FIELD_HELP.max_pods}</span>
            </label>
            <label>
              Service quota
              <input value={form.max_services} onChange={(e) => updateField("max_services", e.target.value)} />
              <span className="muted small">{FIELD_HELP.max_services}</span>
            </label>
            <label>
              PVC quota
              <input
                value={form.max_persistent_volume_claims}
                onChange={(e) => updateField("max_persistent_volume_claims", e.target.value)}
              />
              <span className="muted small">{FIELD_HELP.max_persistent_volume_claims}</span>
            </label>
            <label>
              Requests CPU
              <input value={form.requests_cpu} onChange={(e) => updateField("requests_cpu", e.target.value)} />
              <span className="muted small">{FIELD_HELP.requests_cpu}</span>
            </label>
            <label>
              Limits CPU
              <input value={form.limits_cpu} onChange={(e) => updateField("limits_cpu", e.target.value)} />
              <span className="muted small">{FIELD_HELP.limits_cpu}</span>
            </label>
            <label>
              Requests memory
              <input value={form.requests_memory} onChange={(e) => updateField("requests_memory", e.target.value)} />
              <span className="muted small">{FIELD_HELP.requests_memory}</span>
            </label>
            <label>
              Limits memory
              <input value={form.limits_memory} onChange={(e) => updateField("limits_memory", e.target.value)} />
              <span className="muted small">{FIELD_HELP.limits_memory}</span>
            </label>
            <label>
              Requests storage
              <input value={form.requests_storage} onChange={(e) => updateField("requests_storage", e.target.value)} />
              <span className="muted small">{FIELD_HELP.requests_storage}</span>
            </label>
            <label>
              Min CPU
              <input value={form.limit_min_cpu} onChange={(e) => updateField("limit_min_cpu", e.target.value)} />
              <span className="muted small">{FIELD_HELP.limit_min_cpu}</span>
            </label>
            <label>
              Min memory
              <input value={form.limit_min_memory} onChange={(e) => updateField("limit_min_memory", e.target.value)} />
              <span className="muted small">{FIELD_HELP.limit_min_memory}</span>
            </label>
            <label>
              Default request CPU
              <input
                value={form.limit_default_request_cpu}
                onChange={(e) => updateField("limit_default_request_cpu", e.target.value)}
              />
              <span className="muted small">{FIELD_HELP.limit_default_request_cpu}</span>
            </label>
            <label>
              Default request memory
              <input
                value={form.limit_default_request_memory}
                onChange={(e) => updateField("limit_default_request_memory", e.target.value)}
              />
              <span className="muted small">{FIELD_HELP.limit_default_request_memory}</span>
            </label>
            <label>
              Default CPU limit
              <input
                value={form.limit_default_cpu}
                onChange={(e) => updateField("limit_default_cpu", e.target.value)}
              />
              <span className="muted small">{FIELD_HELP.limit_default_cpu}</span>
            </label>
            <label>
              Default memory limit
              <input
                value={form.limit_default_memory}
                onChange={(e) => updateField("limit_default_memory", e.target.value)}
              />
              <span className="muted small">{FIELD_HELP.limit_default_memory}</span>
            </label>
            <label>
              Max CPU
              <input value={form.limit_max_cpu} onChange={(e) => updateField("limit_max_cpu", e.target.value)} />
              <span className="muted small">{FIELD_HELP.limit_max_cpu}</span>
            </label>
            <label>
              Max memory
              <input value={form.limit_max_memory} onChange={(e) => updateField("limit_max_memory", e.target.value)} />
              <span className="muted small">{FIELD_HELP.limit_max_memory}</span>
            </label>
            <label>
              Idle timeout default (minutes)
              <input
                type="number"
                min="1"
                value={form.idle_timeout_minutes_default}
                onChange={(e) => updateField("idle_timeout_minutes_default", Number(e.target.value))}
              />
              <span className="muted small">{FIELD_HELP.idle_timeout_minutes_default}</span>
            </label>
            <label>
              VM auto-delete default (minutes)
              <input
                type="number"
                min="1"
                value={form.vm_auto_delete_minutes_default}
                onChange={(e) => updateField("vm_auto_delete_minutes_default", Number(e.target.value))}
              />
              <span className="muted small">{FIELD_HELP.vm_auto_delete_minutes_default}</span>
            </label>
            <label>
              Container auto-delete default (minutes)
              <input
                type="number"
                min="1"
                value={form.container_auto_delete_minutes_default}
                onChange={(e) => updateField("container_auto_delete_minutes_default", Number(e.target.value))}
              />
              <span className="muted small">{FIELD_HELP.container_auto_delete_minutes_default}</span>
            </label>
            <label>
              Queue max pending
              <input
                type="number"
                min="1"
                value={form.queue_max_pending}
                onChange={(e) => updateField("queue_max_pending", Number(e.target.value))}
              />
              <span className="muted small">{FIELD_HELP.queue_max_pending}</span>
            </label>
            <label>
              Upload max bytes
              <input
                type="number"
                min="1"
                value={form.upload_max_bytes}
                onChange={(e) => updateField("upload_max_bytes", Number(e.target.value))}
              />
              <span className="muted small">{FIELD_HELP.upload_max_bytes}</span>
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
              <span className="muted small">{FIELD_HELP.enabled}</span>
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
                <div className="small muted">Security profile: {row.security_profile}</div>
                <div className="small muted">
                  Cluster status: {row.present_in_cluster ? "Present" : "Missing"} | Active labs:{" "}
                  {row.active_total_instances}
                </div>
                <div className="small muted">
                  Defaults: idle {row.idle_timeout_minutes_default}m | VM delete {row.vm_auto_delete_minutes_default}m |
                  CT delete {row.container_auto_delete_minutes_default}m
                </div>
                <div className="small muted">
                  Queue cap: {row.queue_max_pending} | Upload cap: {Number(row.upload_max_bytes || 0).toLocaleString()}{" "}
                  bytes
                </div>
                <div className="small muted">
                  VM active: {row.active_vm_instances} | Container active: {row.active_container_instances}
                </div>
                {obsByNamespace.get(row.namespace) ? (
                  <div className="small muted">
                    Health: quota {obsByNamespace.get(row.namespace).resource_quota_present ? "ok" : "missing"} | limit{" "}
                    {obsByNamespace.get(row.namespace).limit_range_present ? "ok" : "missing"} | netpol{" "}
                    {Number(obsByNamespace.get(row.namespace).network_policy_count || 0)}
                    {Array.isArray(obsByNamespace.get(row.namespace).required_network_policies_missing) &&
                    obsByNamespace.get(row.namespace).required_network_policies_missing.length
                      ? ` (missing: ${obsByNamespace.get(row.namespace).required_network_policies_missing.join(", ")})`
                      : ""}
                  </div>
                ) : null}
                {obsByNamespace.get(row.namespace) ? (
                  <div className="small muted">
                    SLO (60m): VM fail{" "}
                    {Number(obsByNamespace.get(row.namespace).vm_launch_failure_rate_pct || 0).toFixed(2)}% | Upload
                    fail {Number(obsByNamespace.get(row.namespace).upload_finalize_failure_rate_pct || 0).toFixed(2)}% |
                    Budget {Number(obsByNamespace.get(row.namespace).error_budget_remaining_pct || 0).toFixed(2)}%
                  </div>
                ) : null}
                {obsByNamespace.get(row.namespace) ? (
                  <div className="small muted">
                    Drift: {Number(obsByNamespace.get(row.namespace).drift_count || 0)} | Route key:{" "}
                    {String(obsByNamespace.get(row.namespace).alert_route_key || "-")}
                  </div>
                ) : null}
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
          {observabilityRows.length > 0 && (
            <div className="card" style={{ marginTop: "1rem" }}>
              <h4>Namespace Observability</h4>
              <div className="tile-grid">
                {observabilityRows.map((item) => (
                  <div key={`obs-${item.namespace}`} className="tile template-tile">
                    <div className="tile-header">
                      <h4>{item.namespace}</h4>
                      <span className={`badge ${item.present_in_cluster ? "success" : "warn"}`}>
                        {item.present_in_cluster ? "Present" : "Missing"}
                      </span>
                    </div>
                    <div className="small muted">
                      Running: {item.running_total_instances} | Failed: {item.failed_total_instances} | Queued CT:{" "}
                      {item.queued_container_instances}
                    </div>
                    <div className="small muted">
                      SLO ({item.slo_window_minutes || 60}m): VM fail{" "}
                      {Number(item.vm_launch_failure_rate_pct || 0).toFixed(2)}% ({item.vm_launches_failed}/
                      {item.vm_launches_total}) | Upload fail{" "}
                      {Number(item.upload_finalize_failure_rate_pct || 0).toFixed(2)}% ({item.upload_finalizes_failed}/
                      {item.upload_finalizes_total})
                    </div>
                    <div className="small muted">
                      Queue oldest pending: {formatDuration(item.queue_oldest_pending_seconds)} | Error budget
                      remaining: {Number(item.error_budget_remaining_pct || 0).toFixed(2)}%
                    </div>
                    <div className="small muted">
                      Upload tasks pending: {item.image_upload_tasks_pending} | failed: {item.image_upload_tasks_failed}
                    </div>
                    <div className="small muted">
                      Quota: {item.resource_quota_present ? "ok" : "missing"} | LimitRange:{" "}
                      {item.limit_range_present ? "ok" : "missing"} | Netpol: {item.network_policy_count}
                    </div>
                    <div className="small muted">
                      Drift: {item.drift_count || 0} | Alert route key: {item.alert_route_key || "-"}
                    </div>
                    {item.required_network_policies_missing?.length > 0 && (
                      <div className="small muted">
                        Missing policies: {item.required_network_policies_missing.join(", ")}
                      </div>
                    )}
                    {item.drift_items?.length > 0 && (
                      <div className="small muted">Drift details: {item.drift_items.join(" | ")}</div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default AdminNamespacesSettings;
