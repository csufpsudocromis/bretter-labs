import React, { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api";

const normalizeNamespace = (value) =>
  String(value || "")
    .trim()
    .toLowerCase();

const ACTIVE_STATUSES = new Set(["queued", "pending", "building", "starting", "running"]);

const NamespaceDirectory = ({ namespaces }) => {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const targets = useMemo(() => {
    const deduped = new Set(
      (Array.isArray(namespaces) ? namespaces : []).map((namespace) => normalizeNamespace(namespace)).filter(Boolean)
    );
    return [...deduped].sort();
  }, [namespaces]);

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      setLoading(true);
      setError("");
      try {
        const effectiveTargets = targets.length > 0 ? targets : [""];
        const summaries = await Promise.all(
          effectiveTargets.map(async (namespace) => {
            const headers = namespace ? { "X-Bretter-Namespace": namespace } : {};
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
              const runningVm = vmInstances.filter((item) =>
                ACTIVE_STATUSES.has(String(item?.status_stage || item?.status || "").toLowerCase())
              );
              const runningContainer = containerInstances.filter((item) =>
                ACTIVE_STATUSES.has(String(item?.status_stage || item?.status || "").toLowerCase())
              );
              const vmNames = vmTemplates
                .map((item) => String(item?.name || "").trim())
                .filter(Boolean)
                .slice(0, 3)
                .map((name) => `VM: ${name}`);
              const containerNames = containerTemplates
                .map((item) => String(item?.name || "").trim())
                .filter(Boolean)
                .slice(0, 3)
                .map((name) => `Container: ${name}`);
              return {
                namespace: namespace || "unscoped",
                link,
                vmTemplateCount: vmTemplates.length,
                containerTemplateCount: containerTemplates.length,
                runningVmCount: runningVm.length,
                runningContainerCount: runningContainer.length,
                previewLabs: [...vmNames, ...containerNames].slice(0, 5),
                previewOverflow: Math.max(0, vmTemplates.length + containerTemplates.length - 5),
                loadError: "",
              };
            } catch (err) {
              return {
                namespace: namespace || "unscoped",
                link,
                vmTemplateCount: 0,
                containerTemplateCount: 0,
                runningVmCount: 0,
                runningContainerCount: 0,
                previewLabs: [],
                previewOverflow: 0,
                loadError: err?.response?.data?.detail || "Failed to load namespace labs",
              };
            }
          })
        );
        if (cancelled) return;
        setRows(summaries);
      } catch (err) {
        if (cancelled) return;
        setRows([]);
        setError(err?.response?.data?.detail || "Failed to load namespace directory");
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    };

    load();

    return () => {
      cancelled = true;
    };
  }, [targets]);

  return (
    <section className="card">
      <h2>Namespaces</h2>
      <p>Select a namespace to open its labs.</p>
      {error && <div className="error">{error}</div>}
      {loading && rows.length === 0 && <div className="muted">Loading namespaces...</div>}
      <div className="tile-grid">
        {rows.map((row) => (
          <div key={row.namespace} className="tile template-tile">
            <div className="tile-header">
              <h4>
                <Link to={row.link}>{row.namespace}</Link>
              </h4>
            </div>
            <div className="muted small">
              Available labs: VM {row.vmTemplateCount} | Container {row.containerTemplateCount}
            </div>
            <div className="muted small">
              Running labs: VM {row.runningVmCount} | Container {row.runningContainerCount}
            </div>
            {row.previewLabs.length > 0 ? (
              <div className="muted small">
                {row.previewLabs.join(", ")}
                {row.previewOverflow > 0 ? `, +${row.previewOverflow} more` : ""}
              </div>
            ) : (
              <div className="muted small">No lab templates currently available.</div>
            )}
            {row.loadError && <div className="muted small">Load warning: {row.loadError}</div>}
            <div className="actions">
              <Link to={row.link} className="ghost namespace-open-link">
                Open namespace
              </Link>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
};

export default NamespaceDirectory;
