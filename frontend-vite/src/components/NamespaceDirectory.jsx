import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api";

const normalizeNamespace = (value) =>
  String(value || "")
    .trim()
    .toLowerCase();

const ACTIVE_STATUSES = new Set(["queued", "pending", "building", "starting", "running"]);

const namespaceHeaders = (namespace) => (namespace ? { "X-Bretter-Namespace": namespace } : {});

const vmStage = (instance) => String(instance?.status_stage || instance?.status || "unknown").toLowerCase();
const containerStage = (instance) => String(instance?.status_stage || instance?.status || "unknown").toLowerCase();

const vmStatusLabel = (instance) => {
  const stage = vmStage(instance);
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
  return labelMap[stage] || "Unknown";
};

const vmStatusReason = (instance) => {
  const stage = vmStage(instance);
  const detail = String(instance?.status_detail || "").trim();
  if (stage === "pending") {
    return "Waiting for resources...";
  }
  if (stage === "running") {
    return "";
  }
  return detail;
};

const containerStatusLabel = (instance) => {
  const stage = containerStage(instance);
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
  return labelMap[stage] || "Unknown";
};

const containerStatusReason = (instance) => String(instance?.status_detail || "").trim();

const hasContainerStartupError = (instance) => {
  const stage = containerStage(instance);
  if (stage === "failed") {
    return true;
  }
  const detail = String(instance?.status_detail || "");
  return /(error|failed|back-?off|imagepull|errimagepull|invalid|crashloop)/i.test(detail);
};

const NamespaceDirectory = ({ namespaces }) => {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const targets = useMemo(() => {
    const deduped = new Set(
      (Array.isArray(namespaces) ? namespaces : []).map((namespace) => normalizeNamespace(namespace)).filter(Boolean)
    );
    return [...deduped].sort();
  }, [namespaces]);

  const load = useCallback(
    async (isCancelled = () => false) => {
      if (targets.length === 0) {
        if (!isCancelled()) {
          setRows([]);
          setError("");
          setLoading(false);
        }
        return;
      }
      setLoading(true);
      setError("");
      try {
        const summaries = await Promise.all(
          targets.map(async (namespace) => {
            const headers = namespaceHeaders(namespace);
            const link = namespace ? `/ns/${encodeURIComponent(namespace)}` : "/";
            try {
              const [vmTemplatesRes, vmInstancesRes, containerTemplatesRes, containerInstancesRes] = await Promise.all([
                api.get("/user/templates", { headers }),
                api.get("/user/pods", { headers }),
                api.get("/user/container-templates", { headers }),
                api.get("/user/containers", { headers }),
              ]);
              const vmTemplates = Array.isArray(vmTemplatesRes?.data) ? vmTemplatesRes.data : [];
              const containerTemplates = Array.isArray(containerTemplatesRes?.data) ? containerTemplatesRes.data : [];
              const vmInstances = Array.isArray(vmInstancesRes?.data) ? vmInstancesRes.data : [];
              const containerInstances = Array.isArray(containerInstancesRes?.data) ? containerInstancesRes.data : [];
              return {
                namespace: namespace || "unscoped",
                namespaceValue: namespace,
                link,
                vmTemplates,
                containerTemplates,
                vmInstances,
                containerInstances,
                loadError: "",
              };
            } catch (err) {
              return {
                namespace: namespace || "unscoped",
                namespaceValue: namespace,
                link,
                vmTemplates: [],
                containerTemplates: [],
                vmInstances: [],
                containerInstances: [],
                loadError: err?.response?.data?.detail || "Failed to load namespace labs",
              };
            }
          })
        );
        if (isCancelled()) return;
        setRows(summaries);
      } catch (err) {
        if (isCancelled()) return;
        setRows([]);
        setError(err?.response?.data?.detail || "Failed to load namespace directory");
      } finally {
        if (!isCancelled()) {
          setLoading(false);
        }
      }
    },
    [targets]
  );

  useEffect(() => {
    let cancelled = false;
    const isCancelled = () => cancelled;
    void load(isCancelled);
    const handle = window.setInterval(() => {
      void load(isCancelled);
    }, 5000);
    return () => {
      cancelled = true;
      window.clearInterval(handle);
    };
  }, [load]);

  const startVm = async (namespace, templateId) => {
    try {
      await api.post(`/user/templates/${templateId}/start`, null, { headers: namespaceHeaders(namespace) });
      setMessage("");
      await load();
    } catch (err) {
      setMessage(err?.response?.data?.detail || "Failed to start VM");
    }
  };

  const startContainer = async (namespace, templateId) => {
    try {
      await api.post(`/user/container-templates/${templateId}/start`, null, { headers: namespaceHeaders(namespace) });
      setMessage("");
      await load();
    } catch (err) {
      setMessage(err?.response?.data?.detail || "Failed to start container");
    }
  };

  const deleteVm = async (namespace, instanceId) => {
    try {
      await api.delete(`/user/pods/${instanceId}`, { headers: namespaceHeaders(namespace) });
      setMessage("");
      await load();
    } catch (err) {
      setMessage(err?.response?.data?.detail || "Failed to delete VM");
    }
  };

  const deleteContainer = async (namespace, instanceId) => {
    try {
      await api.delete(`/user/containers/${instanceId}`, { headers: namespaceHeaders(namespace) });
      setMessage("");
      await load();
    } catch (err) {
      setMessage(err?.response?.data?.detail || "Failed to delete container");
    }
  };

  const connectVm = async (namespace, instance) => {
    if (!instance?.id) return;
    try {
      const response = await api.post(`/user/pods/${instance.id}/connect-token`, null, {
        headers: namespaceHeaders(namespace),
      });
      const connectUrl = String(response?.data?.connect_url || "").trim() || String(instance?.console_url || "").trim();
      if (!connectUrl) {
        setMessage("Console URL not available yet");
        return;
      }
      window.open(connectUrl, "_blank");
    } catch (err) {
      setMessage(err?.response?.data?.detail || "Failed to open console");
    }
  };

  const connectContainer = async (namespace, instance) => {
    if (!instance?.id) return;
    try {
      const readiness = await api.get(`/user/containers/${instance.id}/connect-readiness`, {
        headers: namespaceHeaders(namespace),
      });
      const readinessPayload = readiness?.data || {};
      if (!readinessPayload.ready) {
        setMessage(String(readinessPayload.detail || "Container connect is not ready yet."));
        return;
      }
      const response = await api.post(`/user/containers/${instance.id}/connect-token`, null, {
        headers: namespaceHeaders(namespace),
      });
      const connectUrl = String(response?.data?.connect_url || "").trim() || String(instance?.access_url || "").trim();
      if (!connectUrl) {
        setMessage("Container URL not available yet");
        return;
      }
      window.open(connectUrl, "_blank");
    } catch (err) {
      setMessage(err?.response?.data?.detail || "Failed to open container");
    }
  };

  return (
    <section className="card namespace-directory">
      <h2>Namespaces</h2>
      <p>Select a namespace or launch directly below.</p>
      {error && <div className="error">{error}</div>}
      {message && <div className="info">{message}</div>}
      {loading && rows.length === 0 && <div className="muted">Loading namespaces...</div>}
      {!loading && rows.length === 0 && <div className="muted">No namespaces available.</div>}
      <div className="namespace-directory-list">
        {rows.map((row) => {
          const vmInstances = Array.isArray(row.vmInstances) ? row.vmInstances : [];
          const containerInstances = Array.isArray(row.containerInstances) ? row.containerInstances : [];
          const vmTemplates = Array.isArray(row.vmTemplates) ? row.vmTemplates : [];
          const containerTemplates = Array.isArray(row.containerTemplates) ? row.containerTemplates : [];
          const vmTemplateNames = new Map(vmTemplates.map((template) => [template.id, String(template.name || "VM")]));
          const containerTemplateNames = new Map(
            containerTemplates.map((template) => [template.id, String(template.name || "Container")])
          );
          const activeVmInstances = vmInstances.filter((item) => ACTIVE_STATUSES.has(vmStage(item)));
          const activeContainerInstances = containerInstances.filter((item) =>
            ACTIVE_STATUSES.has(containerStage(item))
          );

          return (
            <div key={row.namespace} className="tile namespace-lab-row">
              <div className="namespace-row-head">
                <h3>
                  <Link to={row.link}>{row.namespace}</Link>
                </h3>
              </div>
              {row.loadError && <div className="muted small">Load warning: {row.loadError}</div>}
              <div className="namespace-row-grid">
                <div>
                  <h4>Available Virtual Labs</h4>
                  <div className="tile-grid">
                    {vmTemplates.length === 0 && containerTemplates.length === 0 && (
                      <div className="muted small">No templates available.</div>
                    )}
                    {vmTemplates.map((template) => (
                      <div key={`${row.namespace}-vm-${template.id}`} className="tile template-tile">
                        <div className="tile-header">
                          <h4>{template.name}</h4>
                        </div>
                        {template.description && <div className="muted small">{template.description}</div>}
                        <div className="namespace-card-actions">
                          <button onClick={() => startVm(row.namespaceValue, template.id)}>Start Lab</button>
                        </div>
                      </div>
                    ))}
                    {containerTemplates.map((template) => (
                      <div key={`${row.namespace}-container-${template.id}`} className="tile template-tile">
                        <div className="tile-header">
                          <h4>{template.name}</h4>
                        </div>
                        {template.description && <div className="muted small">{template.description}</div>}
                        <div className="namespace-card-actions">
                          <button onClick={() => startContainer(row.namespaceValue, template.id)}>Start Lab</button>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>

                <div>
                  <h4>My Running Labs</h4>
                  <div className="tile-grid">
                    {activeVmInstances.length === 0 && activeContainerInstances.length === 0 && (
                      <div className="muted small">No running labs yet.</div>
                    )}
                    {activeVmInstances.map((instance) => {
                      const podName = `vm-${instance.owner}-${String(instance.id || "").slice(0, 8)}`;
                      const running = vmStage(instance) === "running";
                      return (
                        <div key={`${row.namespace}-vmi-${instance.id}`} className="tile pod-tile">
                          <div className="tile-header">
                            <h4>{vmTemplateNames.get(instance.template_id) || "VM"}</h4>
                            <span className={`badge ${running ? "success" : "warn"}`}>{vmStatusLabel(instance)}</span>
                          </div>
                          <div className="specs">
                            <span>{podName}</span>
                          </div>
                          {vmStatusReason(instance) && <div className="muted small">{vmStatusReason(instance)}</div>}
                          <div className="actions">
                            <button className="danger" onClick={() => deleteVm(row.namespaceValue, instance.id)}>
                              Delete
                            </button>
                            <button onClick={() => connectVm(row.namespaceValue, instance)} disabled={!running}>
                              Connect
                            </button>
                          </div>
                        </div>
                      );
                    })}

                    {activeContainerInstances.map((instance) => {
                      const running = containerStage(instance) === "running";
                      const hasStartupError = hasContainerStartupError(instance);
                      return (
                        <div key={`${row.namespace}-ci-${instance.id}`} className="tile pod-tile">
                          <div className="tile-header">
                            <h4>{containerTemplateNames.get(instance.template_id) || "Container"}</h4>
                            <span className={`badge ${running ? "success" : "warn"}`}>
                              {containerStatusLabel(instance)}
                            </span>
                          </div>
                          <div className="specs">
                            <span>
                              {instance.pod_name || `ct-${instance.owner}-${String(instance.id || "").slice(0, 8)}`}
                            </span>
                          </div>
                          {hasStartupError && containerStatusReason(instance) && (
                            <div className="muted small">{containerStatusReason(instance)}</div>
                          )}
                          {!hasStartupError && !running && containerStatusReason(instance) && (
                            <div className="muted small">{containerStatusReason(instance)}</div>
                          )}
                          <div className="actions">
                            <button className="danger" onClick={() => deleteContainer(row.namespaceValue, instance.id)}>
                              Delete
                            </button>
                            <button
                              onClick={() => connectContainer(row.namespaceValue, instance)}
                              disabled={!instance.access_url || !running}
                            >
                              Connect
                            </button>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
};

export default NamespaceDirectory;
