import React, { useEffect, useState } from "react";
import { api } from "../../api";

const parseCsv = (value) =>
  String(value || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);

const defaultClusterForm = {
  id: "",
  name: "",
  region: "local",
  capacity_weight: 100,
  enabled: true,
  schedule_enabled: true,
  runtime_enabled: false,
  runtime_namespace: "",
  compliance_tags_csv: "",
  kubeconfig_secret_name: "",
  kubeconfig_secret_namespace: "",
  kubeconfig_secret_key: "kubeconfig",
  notes: "",
};

const defaultPolicyForm = {
  team: "default",
  preferred_cluster_id: "",
  hard_pin_cluster: false,
  required_regions_csv: "",
  required_compliance_tags_csv: "",
  allowed_cluster_ids_csv: "",
};

const defaultReplicationForm = {
  artifact_type: "vm_image",
  artifact_id: "",
  source_cluster_id: "local",
  target_cluster_ids_csv: "",
  tenant: "global",
};

const defaultExplainForm = {
  team: "default",
  workload_kind: "vm",
  template_cluster_id: "",
};

const AdminMultiClusterSettings = () => {
  const [clusters, setClusters] = useState([]);
  const [telemetry, setTelemetry] = useState([]);
  const [policies, setPolicies] = useState([]);
  const [replications, setReplications] = useState([]);
  const [clusterForm, setClusterForm] = useState(defaultClusterForm);
  const [policyForm, setPolicyForm] = useState(defaultPolicyForm);
  const [replicationForm, setReplicationForm] = useState(defaultReplicationForm);
  const [explainForm, setExplainForm] = useState(defaultExplainForm);
  const [explainResult, setExplainResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const [clustersRes, telemetryRes, policiesRes, replicationsRes] = await Promise.all([
        api.get("/admin/settings/clusters"),
        api.get("/admin/settings/clusters/telemetry"),
        api.get("/admin/settings/placement-policies"),
        api.get("/admin/replication/artifacts", { params: { limit: 200 } }),
      ]);
      setClusters(Array.isArray(clustersRes.data) ? clustersRes.data : []);
      setTelemetry(Array.isArray(telemetryRes.data) ? telemetryRes.data : []);
      setPolicies(Array.isArray(policiesRes.data) ? policiesRes.data : []);
      setReplications(Array.isArray(replicationsRes.data) ? replicationsRes.data : []);
    } catch (err) {
      setError(err.response?.data?.detail || "Failed to load multi-cluster settings.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const createCluster = async () => {
    setError("");
    setMessage("");
    try {
      await api.post("/admin/settings/clusters", {
        id: clusterForm.id,
        name: clusterForm.name,
        region: clusterForm.region,
        capacity_weight: Number(clusterForm.capacity_weight || 100),
        enabled: Boolean(clusterForm.enabled),
        schedule_enabled: Boolean(clusterForm.schedule_enabled),
        runtime_enabled: Boolean(clusterForm.runtime_enabled),
        runtime_namespace: clusterForm.runtime_namespace || undefined,
        compliance_tags: parseCsv(clusterForm.compliance_tags_csv),
        kubeconfig_secret_name: clusterForm.kubeconfig_secret_name || undefined,
        kubeconfig_secret_namespace: clusterForm.kubeconfig_secret_namespace || undefined,
        kubeconfig_secret_key: clusterForm.kubeconfig_secret_key || undefined,
        notes: clusterForm.notes || "",
      });
      setClusterForm(defaultClusterForm);
      setMessage("Cluster created.");
      await load();
    } catch (err) {
      setError(err.response?.data?.detail || "Failed to create cluster.");
    }
  };

  const probeCluster = async (clusterId) => {
    setError("");
    setMessage("");
    try {
      await api.post(`/admin/settings/clusters/${encodeURIComponent(clusterId)}/probe`);
      setMessage(`Cluster ${clusterId} probed.`);
      await load();
    } catch (err) {
      setError(err.response?.data?.detail || "Failed to probe cluster.");
    }
  };

  const disableCluster = async (clusterId) => {
    setError("");
    setMessage("");
    try {
      await api.delete(`/admin/settings/clusters/${encodeURIComponent(clusterId)}`);
      setMessage(`Cluster ${clusterId} disabled.`);
      await load();
    } catch (err) {
      if (err.response?.status === 409) {
        const accepted = window.confirm(
          `${err.response?.data?.detail || "Cluster disable blocked by guardrails."}\n\nDisable with force=true?`
        );
        if (!accepted) return;
        try {
          await api.delete(`/admin/settings/clusters/${encodeURIComponent(clusterId)}`, { params: { force: true } });
          setMessage(`Cluster ${clusterId} force-disabled.`);
          await load();
          return;
        } catch (forceErr) {
          setError(forceErr.response?.data?.detail || "Failed to force-disable cluster.");
          return;
        }
      }
      setError(err.response?.data?.detail || "Failed to disable cluster.");
    }
  };

  const savePolicy = async () => {
    setError("");
    setMessage("");
    try {
      await api.put(`/admin/settings/placement-policies/${encodeURIComponent(policyForm.team)}`, {
        preferred_cluster_id: policyForm.preferred_cluster_id || null,
        hard_pin_cluster: Boolean(policyForm.hard_pin_cluster),
        required_regions: parseCsv(policyForm.required_regions_csv),
        required_compliance_tags: parseCsv(policyForm.required_compliance_tags_csv),
        allowed_cluster_ids: parseCsv(policyForm.allowed_cluster_ids_csv),
      });
      setMessage(`Placement policy saved for team ${policyForm.team}.`);
      await load();
    } catch (err) {
      setError(err.response?.data?.detail || "Failed to save placement policy.");
    }
  };

  const enqueueReplication = async () => {
    setError("");
    setMessage("");
    try {
      await api.post("/admin/replication/artifacts", {
        artifact_type: replicationForm.artifact_type,
        artifact_id: replicationForm.artifact_id,
        source_cluster_id: replicationForm.source_cluster_id || "local",
        target_cluster_ids: parseCsv(replicationForm.target_cluster_ids_csv),
        tenant: replicationForm.tenant || "global",
      });
      setMessage("Replication queued.");
      await load();
    } catch (err) {
      setError(err.response?.data?.detail || "Failed to queue replication.");
    }
  };

  const processReplicationQueue = async () => {
    setError("");
    setMessage("");
    try {
      const res = await api.post("/admin/replication/artifacts/process", null, { params: { limit: 50 } });
      setMessage(`Processed ${Number(res.data?.processed || 0)} replication task(s).`);
      await load();
    } catch (err) {
      setError(err.response?.data?.detail || "Failed to process replication queue.");
    }
  };

  const runPlacementExplain = async () => {
    setError("");
    setMessage("");
    try {
      const res = await api.get("/admin/settings/placement-policies/explain", {
        params: {
          team: explainForm.team || "default",
          workload_kind: explainForm.workload_kind || "vm",
          template_cluster_id: explainForm.template_cluster_id || undefined,
        },
      });
      setExplainResult(res.data || null);
    } catch (err) {
      setError(err.response?.data?.detail || "Failed to run placement explain.");
    }
  };

  const telemetryByCluster = new Map((telemetry || []).map((item) => [item.cluster_id, item]));

  return (
    <div>
      <h2>Multi-Cluster</h2>
      <p className="muted small">Manage clusters, placement policies, and replication jobs.</p>

      <div className="actions" style={{ marginBottom: "1rem", flexWrap: "wrap" }}>
        <button className="ghost" onClick={load} disabled={loading}>
          {loading ? "Refreshing..." : "Refresh"}
        </button>
        <button className="ghost" onClick={processReplicationQueue}>
          Process Replication Queue
        </button>
      </div>

      {error && <div className="error">{error}</div>}
      {message && <div className="info">{message}</div>}

      <div className="card" style={{ marginBottom: "1rem" }}>
        <h3>Cluster Inventory</h3>
        <div className="tile-grid" style={{ marginTop: "0.75rem" }}>
          {clusters.map((cluster) => {
            const stats = telemetryByCluster.get(cluster.id) || {};
            return (
              <div className="tile template-tile" key={cluster.id}>
                <div className="tile-header">
                  <h4>
                    {cluster.name} <span className="muted">({cluster.id})</span>
                  </h4>
                  <span
                    className={`badge ${String(cluster.health_status || "").toLowerCase() === "healthy" ? "success" : ""}`}
                  >
                    {cluster.health_status || "unknown"}
                  </span>
                </div>
                <div className="muted small">Region: {cluster.region}</div>
                <div className="muted small">Runtime NS: {cluster.runtime_namespace || "labs"}</div>
                <div className="muted small">
                  Runtime client: {stats.runtime_client_ready ? "ready" : "not ready"}{" "}
                  {stats.runtime_client_message || ""}
                </div>
                <div className="muted small">
                  Active workloads: VM {stats.active_vm_instances || 0}, Container{" "}
                  {stats.active_container_instances || 0}
                </div>
                <div className="muted small">
                  Replication: queued {stats.queued_replications || 0}, syncing {stats.syncing_replications || 0}, error{" "}
                  {stats.error_replications || 0}
                </div>
                <div className="muted small">Kubeconfig source: {cluster.kubeconfig_source || "none"}</div>
                <div className="actions" style={{ marginTop: "0.75rem" }}>
                  <button className="ghost" onClick={() => probeCluster(cluster.id)}>
                    Probe
                  </button>
                  {!cluster.is_local && (
                    <button className="ghost" onClick={() => disableCluster(cluster.id)}>
                      Disable
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      <div className="card" style={{ marginBottom: "1rem" }}>
        <h3>Add Cluster</h3>
        <div className="row" style={{ gap: "0.75rem", flexWrap: "wrap" }}>
          <input
            className="input"
            placeholder="cluster id"
            value={clusterForm.id}
            onChange={(e) => setClusterForm((prev) => ({ ...prev, id: e.target.value }))}
          />
          <input
            className="input"
            placeholder="name"
            value={clusterForm.name}
            onChange={(e) => setClusterForm((prev) => ({ ...prev, name: e.target.value }))}
          />
          <input
            className="input"
            placeholder="region"
            value={clusterForm.region}
            onChange={(e) => setClusterForm((prev) => ({ ...prev, region: e.target.value }))}
          />
          <input
            className="input"
            placeholder="runtime namespace"
            value={clusterForm.runtime_namespace}
            onChange={(e) => setClusterForm((prev) => ({ ...prev, runtime_namespace: e.target.value }))}
          />
          <input
            className="input"
            placeholder="capacity weight"
            type="number"
            value={clusterForm.capacity_weight}
            onChange={(e) => setClusterForm((prev) => ({ ...prev, capacity_weight: e.target.value }))}
          />
        </div>
        <div className="row" style={{ gap: "0.75rem", marginTop: "0.5rem", flexWrap: "wrap" }}>
          <input
            className="input"
            placeholder="compliance tags csv"
            value={clusterForm.compliance_tags_csv}
            onChange={(e) => setClusterForm((prev) => ({ ...prev, compliance_tags_csv: e.target.value }))}
          />
          <input
            className="input"
            placeholder="kubeconfig secret name"
            value={clusterForm.kubeconfig_secret_name}
            onChange={(e) => setClusterForm((prev) => ({ ...prev, kubeconfig_secret_name: e.target.value }))}
          />
          <input
            className="input"
            placeholder="kubeconfig secret namespace"
            value={clusterForm.kubeconfig_secret_namespace}
            onChange={(e) => setClusterForm((prev) => ({ ...prev, kubeconfig_secret_namespace: e.target.value }))}
          />
          <input
            className="input"
            placeholder="kubeconfig secret key"
            value={clusterForm.kubeconfig_secret_key}
            onChange={(e) => setClusterForm((prev) => ({ ...prev, kubeconfig_secret_key: e.target.value }))}
          />
        </div>
        <div className="row" style={{ gap: "0.75rem", marginTop: "0.5rem", flexWrap: "wrap" }}>
          <label className="muted small">
            <input
              type="checkbox"
              checked={clusterForm.enabled}
              onChange={(e) => setClusterForm((prev) => ({ ...prev, enabled: e.target.checked }))}
            />{" "}
            enabled
          </label>
          <label className="muted small">
            <input
              type="checkbox"
              checked={clusterForm.schedule_enabled}
              onChange={(e) => setClusterForm((prev) => ({ ...prev, schedule_enabled: e.target.checked }))}
            />{" "}
            schedule enabled
          </label>
          <label className="muted small">
            <input
              type="checkbox"
              checked={clusterForm.runtime_enabled}
              onChange={(e) => setClusterForm((prev) => ({ ...prev, runtime_enabled: e.target.checked }))}
            />{" "}
            runtime enabled
          </label>
        </div>
        <textarea
          className="input"
          rows={2}
          style={{ marginTop: "0.5rem" }}
          placeholder="notes"
          value={clusterForm.notes}
          onChange={(e) => setClusterForm((prev) => ({ ...prev, notes: e.target.value }))}
        />
        <div className="actions" style={{ marginTop: "0.75rem" }}>
          <button onClick={createCluster}>Create Cluster</button>
        </div>
      </div>

      <div className="card" style={{ marginBottom: "1rem" }}>
        <h3>Placement Policy</h3>
        <div className="row" style={{ gap: "0.75rem", flexWrap: "wrap" }}>
          <input
            className="input"
            placeholder="team"
            value={policyForm.team}
            onChange={(e) => setPolicyForm((prev) => ({ ...prev, team: e.target.value }))}
          />
          <input
            className="input"
            placeholder="preferred cluster id"
            value={policyForm.preferred_cluster_id}
            onChange={(e) => setPolicyForm((prev) => ({ ...prev, preferred_cluster_id: e.target.value }))}
          />
          <label className="muted small">
            <input
              type="checkbox"
              checked={policyForm.hard_pin_cluster}
              onChange={(e) => setPolicyForm((prev) => ({ ...prev, hard_pin_cluster: e.target.checked }))}
            />{" "}
            hard pin cluster
          </label>
        </div>
        <div className="row" style={{ gap: "0.75rem", marginTop: "0.5rem", flexWrap: "wrap" }}>
          <input
            className="input"
            placeholder="required regions csv"
            value={policyForm.required_regions_csv}
            onChange={(e) => setPolicyForm((prev) => ({ ...prev, required_regions_csv: e.target.value }))}
          />
          <input
            className="input"
            placeholder="required compliance tags csv"
            value={policyForm.required_compliance_tags_csv}
            onChange={(e) => setPolicyForm((prev) => ({ ...prev, required_compliance_tags_csv: e.target.value }))}
          />
          <input
            className="input"
            placeholder="allowed cluster ids csv"
            value={policyForm.allowed_cluster_ids_csv}
            onChange={(e) => setPolicyForm((prev) => ({ ...prev, allowed_cluster_ids_csv: e.target.value }))}
          />
        </div>
        <div className="actions" style={{ marginTop: "0.75rem" }}>
          <button onClick={savePolicy}>Save Policy</button>
        </div>
        <div className="muted small" style={{ marginTop: "0.75rem" }}>
          Existing policies:{" "}
          {(policies || []).map((policy) => `${policy.team}(${policy.preferred_cluster_id || "none"})`).join(", ") ||
            "none"}
        </div>
      </div>

      <div className="card" style={{ marginBottom: "1rem" }}>
        <h3>Placement Explain</h3>
        <div className="row" style={{ gap: "0.75rem", flexWrap: "wrap" }}>
          <input
            className="input"
            placeholder="team"
            value={explainForm.team}
            onChange={(e) => setExplainForm((prev) => ({ ...prev, team: e.target.value }))}
          />
          <select
            className="input"
            value={explainForm.workload_kind}
            onChange={(e) => setExplainForm((prev) => ({ ...prev, workload_kind: e.target.value }))}
          >
            <option value="vm">vm</option>
            <option value="container">container</option>
          </select>
          <input
            className="input"
            placeholder="template cluster id (optional)"
            value={explainForm.template_cluster_id}
            onChange={(e) => setExplainForm((prev) => ({ ...prev, template_cluster_id: e.target.value }))}
          />
          <button className="ghost" onClick={runPlacementExplain}>
            Explain
          </button>
        </div>
        {explainResult && (
          <div style={{ marginTop: "0.75rem" }}>
            <div className="muted small">
              Selected: {explainResult.selected_cluster_id || "none"} ({explainResult.selected_reason || "n/a"})
            </div>
            {explainResult.error && <div className="error">{explainResult.error}</div>}
            <div className="tile-grid" style={{ marginTop: "0.5rem" }}>
              {(explainResult.candidates || []).map((candidate) => (
                <div key={candidate.cluster_id} className="tile template-tile">
                  <div className="tile-header">
                    <h4>{candidate.cluster_id}</h4>
                    <span className={`badge ${candidate.allowed ? "success" : "warn"}`}>
                      {candidate.allowed ? "allowed" : "blocked"}
                    </span>
                  </div>
                  <div className="muted small">{(candidate.reasons || []).join("; ") || "eligible"}</div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      <div className="card" style={{ marginBottom: "1rem" }}>
        <h3>Artifact Replication Queue</h3>
        <div className="row" style={{ gap: "0.75rem", flexWrap: "wrap" }}>
          <select
            className="input"
            value={replicationForm.artifact_type}
            onChange={(e) => setReplicationForm((prev) => ({ ...prev, artifact_type: e.target.value }))}
          >
            <option value="vm_image">vm_image</option>
            <option value="vm_template">vm_template</option>
            <option value="container_image">container_image</option>
            <option value="container_template">container_template</option>
          </select>
          <input
            className="input"
            placeholder="artifact id"
            value={replicationForm.artifact_id}
            onChange={(e) => setReplicationForm((prev) => ({ ...prev, artifact_id: e.target.value }))}
          />
          <input
            className="input"
            placeholder="source cluster id"
            value={replicationForm.source_cluster_id}
            onChange={(e) => setReplicationForm((prev) => ({ ...prev, source_cluster_id: e.target.value }))}
          />
          <input
            className="input"
            placeholder="target cluster ids csv"
            value={replicationForm.target_cluster_ids_csv}
            onChange={(e) => setReplicationForm((prev) => ({ ...prev, target_cluster_ids_csv: e.target.value }))}
          />
          <button className="ghost" onClick={enqueueReplication}>
            Queue Replication
          </button>
        </div>
        <div className="tile-grid" style={{ marginTop: "0.75rem" }}>
          {(replications || []).slice(0, 40).map((row) => (
            <div key={row.id} className="tile template-tile">
              <div className="tile-header">
                <h4>
                  {row.artifact_type}:{row.artifact_id}
                </h4>
                <span className={`badge ${String(row.status || "").toLowerCase() === "ready" ? "success" : ""}`}>
                  {row.status}
                </span>
              </div>
              <div className="muted small">
                {row.source_cluster_id} → {row.target_cluster_id}
              </div>
              <div className="muted small">{row.detail || ""}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};

export default AdminMultiClusterSettings;
