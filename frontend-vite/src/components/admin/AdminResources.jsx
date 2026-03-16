import React, { useEffect, useState } from "react";
import { api } from "../../api";

const fmtCpu = (m) => `${(m / 1000).toFixed(2)} cores`;
const fmtMem = (b) => `${(b / (1024 * 1024 * 1024)).toFixed(2)} GB`;
const fmtPct = (n) => `${Number.isFinite(n) ? n.toFixed(1) : "0.0"}%`;
const REFRESH_MS = 30000;

const riskMeta = (risk) => {
  const normalized = (risk || "healthy").toLowerCase();
  if (normalized === "critical") return { label: "Critical", className: "badge warn", color: "#dc2626" };
  if (normalized === "high") return { label: "High", className: "badge warn", color: "#ea580c" };
  if (normalized === "warning") return { label: "Warning", className: "badge warn", color: "#c2410c" };
  if (normalized === "info") return { label: "Info", className: "badge", color: "#2563eb" };
  return { label: "Healthy", className: "badge success", color: "#166534" };
};

const fmtAge = (seconds) => {
  const s = Number(seconds || 0);
  if (!Number.isFinite(s) || s <= 0) return "n/a";
  if (s < 60) return `${Math.floor(s)}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  return `${Math.floor(s / 86400)}d`;
};

const Bar = ({ label, used, total, formatter, risk, headroom }) => {
  const pct = total > 0 ? Math.min(100, (used / total) * 100) : 0;
  const badge = riskMeta(risk);
  const fillColor = badge.color;
  return (
    <div className="card" style={{ marginBottom: "1rem" }}>
      <div className="tile-header">
        <h4>{label}</h4>
        <span className={badge.className} style={{ borderColor: fillColor, color: fillColor }}>
          {badge.label}
        </span>
      </div>
      <div className="specs">
        <span>
          Used: {formatter(used)} / {formatter(total)}
        </span>
        <span>Headroom: {formatter(headroom)}</span>
        <span>{fmtPct(pct)} utilized</span>
      </div>
      <div style={{ height: "10px", background: "#e2e8f0", borderRadius: "999px", overflow: "hidden" }}>
        <div style={{ height: "100%", width: `${pct}%`, background: fillColor, transition: "width 150ms ease" }} />
      </div>
    </div>
  );
};

const AdminResources = () => {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const res = await api.get("/admin/resources");
      setData(res.data);
    } catch (err) {
      setError(err.response?.data?.detail || "Failed to load resources");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    const timer = setInterval(load, REFRESH_MS);
    return () => clearInterval(timer);
  }, []);

  const cpuUsed = data ? data.requested.cpu_m : 0;
  const cpuTotal = data ? data.allocatable.cpu_m : 0;
  const memUsed = data ? data.requested.memory_bytes : 0;
  const memTotal = data ? data.allocatable.memory_bytes : 0;
  const diskUsed = data ? data.requested.disk_bytes : 0;
  const diskTotal = data ? data.allocatable.disk_bytes : 0;
  const headroom = data?.headroom || {};
  const utilization = data?.utilization_pct || {};
  const risk = data?.risk || {};
  const pending = data?.pending || { count: 0, top_reasons: [], pods: [] };
  const recommendations = data?.recommendations || [];
  const top = data?.top_consumers || {};
  const longhorn = data?.storage?.longhorn;
  const summary = data?.summary || {};
  const nodes = data?.nodes || [];
  const fetchedAt = data?.fetched_at ? new Date(data.fetched_at).toLocaleString() : "n/a";

  return (
    <div>
      <h2>Cluster Resources</h2>
      <p>Capacity, pressure risks, pending blockers, and top consumers. Auto-refreshes every 30s.</p>
      <div className="actions" style={{ marginBottom: "1rem" }}>
        <button className="ghost" onClick={load} disabled={loading}>
          {loading ? "Refreshing..." : "Refresh"}
        </button>
      </div>
      {error && <div className="error">{error}</div>}
      {data && (
        <>
          <div className="card" style={{ marginBottom: "1rem" }}>
            <h3>Cluster Summary</h3>
            <div className="specs">
              <span>Fetched: {fetchedAt}</span>
              <span>
                Nodes Ready: {summary.ready_nodes ?? 0}/{summary.total_nodes ?? nodes.length}
              </span>
              <span>Pending Pods: {pending.count || 0}</span>
              <span>Overall Risk: {(risk.overall || "healthy").toUpperCase()}</span>
            </div>
            <div className="specs">
              <span>CPU Utilization: {fmtPct(utilization.cpu_pct || 0)}</span>
              <span>Memory Utilization: {fmtPct(utilization.memory_pct || 0)}</span>
              <span>Disk Utilization: {fmtPct(utilization.disk_pct || 0)}</span>
            </div>
          </div>

          <Bar
            label="CPU"
            used={cpuUsed}
            total={cpuTotal}
            formatter={fmtCpu}
            risk={risk.cpu}
            headroom={headroom.cpu_m || 0}
          />
          <Bar
            label="Memory"
            used={memUsed}
            total={memTotal}
            formatter={fmtMem}
            risk={risk.memory}
            headroom={headroom.memory_bytes || 0}
          />
          <Bar
            label="Disk (ephemeral)"
            used={diskUsed}
            total={diskTotal}
            formatter={fmtMem}
            risk={risk.disk}
            headroom={headroom.disk_bytes || 0}
          />

          <div className="card">
            <h3>Recommendations</h3>
            {recommendations.length === 0 && <div className="muted small">No immediate actions recommended.</div>}
            <div className="tile-grid">
              {recommendations.map((rec, idx) => {
                const badge = riskMeta(rec.severity || "info");
                return (
                  <div key={`${rec.title}-${idx}`} className="tile template-tile">
                    <div className="tile-header">
                      <h4>{rec.title}</h4>
                      <span className={badge.className} style={{ borderColor: badge.color, color: badge.color }}>
                        {badge.label}
                      </span>
                    </div>
                    <div className="muted small">{rec.detail}</div>
                    {rec.action && (
                      <div className="muted small" style={{ marginTop: "0.4rem" }}>
                        Action: {rec.action}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>

          <div className="card">
            <h3>Pending Workload Reasons</h3>
            <div className="specs">
              <span>Pending Pods: {pending.count || 0}</span>
            </div>
            {pending.top_reasons?.length ? (
              <div className="tile-grid">
                {pending.top_reasons.map((item) => (
                  <div key={item.reason} className="tile template-tile">
                    <div className="tile-header">
                      <h4>{item.reason}</h4>
                      <span className="badge warn">{item.count}</span>
                    </div>
                    {(item.examples || []).map((ex, idx) => (
                      <div key={`${item.reason}-${idx}`} className="muted small" style={{ marginBottom: "0.25rem" }}>
                        {ex}
                      </div>
                    ))}
                  </div>
                ))}
              </div>
            ) : (
              <div className="muted small">No pending blockers detected.</div>
            )}
            {pending.pods?.length > 0 && (
              <div style={{ marginTop: "0.75rem" }}>
                <h4 style={{ marginBottom: "0.5rem" }}>Sample Pending Pods</h4>
                <div className="tile-grid">
                  {pending.pods.slice(0, 8).map((pod) => (
                    <div key={`${pod.namespace}/${pod.name}`} className="tile template-tile">
                      <div className="specs">
                        <span>
                          {pod.namespace}/{pod.name}
                        </span>
                        <span>Age: {fmtAge(pod.age_seconds)}</span>
                      </div>
                      <div className="muted small">{pod.reason}</div>
                      {pod.detail && <div className="muted small">{pod.detail}</div>}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>

          <div className="card">
            <h3>Top Requested Consumers</h3>
            <div className="muted small">
              Metrics source: {top.metrics_available ? "requested + usage (metrics-server)" : "requested only"}
              {top.metrics_error ? ` (${top.metrics_error})` : ""}
            </div>
            <div className="tile-grid" style={{ marginTop: "0.75rem" }}>
              {[
                {
                  key: "cpu",
                  title: "CPU",
                  fmtReq: (item) => fmtCpu(item.requested?.cpu_m || 0),
                  fmtUse: (item) => fmtCpu(item.usage?.cpu_m || 0),
                },
                {
                  key: "memory",
                  title: "Memory",
                  fmtReq: (item) => fmtMem(item.requested?.memory_bytes || 0),
                  fmtUse: (item) => fmtMem(item.usage?.memory_bytes || 0),
                },
                {
                  key: "disk",
                  title: "Disk (ephemeral)",
                  fmtReq: (item) => fmtMem(item.requested?.disk_bytes || 0),
                  fmtUse: () => "n/a",
                },
              ].map((section) => (
                <div key={section.key} className="tile template-tile">
                  <h4>{section.title}</h4>
                  {(top[section.key] || []).slice(0, 5).map((item) => (
                    <div
                      key={`${section.key}-${item.namespace}-${item.name}`}
                      className="muted small"
                      style={{ marginBottom: "0.35rem" }}
                    >
                      {item.namespace}/{item.name} ({item.phase || "unknown"}) - req {section.fmtReq(item)}
                      {top.metrics_available && section.key !== "disk" ? `, use ${section.fmtUse(item)}` : ""}
                    </div>
                  ))}
                  {!(top[section.key] || []).length && (
                    <div className="muted small">No requested resources reported.</div>
                  )}
                </div>
              ))}
            </div>
          </div>

          <div className="card">
            <h3>Cluster Nodes</h3>
            <div className="tile-grid">
              {nodes.length === 0 && <div className="muted">No nodes.</div>}
              {nodes.map((n) => (
                <div key={n.name} className="tile template-tile">
                  <div className="tile-header">
                    <h4>{n.name}</h4>
                    <span className={riskMeta(n.risk || "healthy").className}>
                      {(n.risk || "healthy").toUpperCase()}
                    </span>
                  </div>
                  <div className="specs">
                    <span>IP: {n.ip || "n/a"}</span>
                    <span>Roles: {(n.roles || []).join(", ") || "worker"}</span>
                    <span>Ready: {n.conditions?.Ready || "Unknown"}</span>
                    <span>Schedulable: {n.schedulable ? "yes" : "no"}</span>
                    <span>
                      CPU req: {fmtCpu((n.requested || n.usage || {}).cpu_m || 0)} /{" "}
                      {fmtCpu((n.allocatable || {}).cpu_m || n.capacity_cpu_m || 0)}
                    </span>
                    <span>
                      RAM req: {fmtMem((n.requested || n.usage || {}).memory_bytes || (n.usage || {}).mem_bytes || 0)} /{" "}
                      {fmtMem((n.allocatable || {}).memory_bytes || n.capacity_mem_bytes || 0)}
                    </span>
                    <span>
                      Disk req: {fmtMem((n.requested || n.usage || {}).disk_bytes || 0)} /{" "}
                      {fmtMem((n.allocatable || {}).disk_bytes || n.capacity_disk_bytes || 0)}
                    </span>
                    {n.usage && typeof n.usage.cpu_m === "number" && <span>CPU use: {fmtCpu(n.usage.cpu_m)}</span>}
                    {n.usage && typeof n.usage.memory_bytes === "number" && (
                      <span>RAM use: {fmtMem(n.usage.memory_bytes)}</span>
                    )}
                  </div>
                  {n.pressures?.length > 0 && <div className="muted small">Pressure: {n.pressures.join(", ")}</div>}
                  {n.taints && n.taints.length > 0 ? (
                    <div className="muted small">Taints: {n.taints.join(", ")}</div>
                  ) : (
                    <div className="muted small">Taints: none</div>
                  )}
                </div>
              ))}
            </div>
          </div>

          <div className="card">
            <h3>Storage (Longhorn)</h3>
            {!longhorn?.available ? (
              <div className="muted small">{longhorn?.detail || "Longhorn data unavailable."}</div>
            ) : (
              <>
                <div className="specs">
                  <span>Nodes: {longhorn.node_count}</span>
                  <span>Volumes: {longhorn.volume_count}</span>
                  <span>
                    Used: {fmtMem(longhorn.used_bytes || 0)} / {fmtMem(longhorn.capacity_bytes || 0)}
                  </span>
                  <span>Utilization: {fmtPct(longhorn.utilization_pct || 0)}</span>
                  <span>Risk: {(longhorn.risk || "healthy").toUpperCase()}</span>
                </div>
                <div className="specs">
                  <span>NotReady Nodes: {longhorn.degraded_nodes || 0}</span>
                  <span>Unschedulable Nodes: {longhorn.unschedulable_nodes || 0}</span>
                  <span>Detached Volumes: {longhorn.detached_volumes || 0}</span>
                  <span>Robustness: {JSON.stringify(longhorn.volume_robustness || {})}</span>
                </div>
                {(longhorn.detached_volumes || 0) > 0 && (
                  <div className="muted small">
                    Note: detached volumes report robustness as unknown in Longhorn; this is expected when volumes are
                    not attached.
                  </div>
                )}
              </>
            )}
          </div>
        </>
      )}
    </div>
  );
};

export default AdminResources;
