import React, { useEffect, useState } from "react";
import { api } from "../../api";

const AdminAuditEvents = () => {
  const [events, setEvents] = useState([]);
  const [filters, setFilters] = useState({
    actor: "",
    action: "",
    resource: "",
    namespace: "",
    target: "",
  });
  const [info, setInfo] = useState("");
  const [error, setError] = useState("");
  const [clearing, setClearing] = useState(false);
  const [exporting, setExporting] = useState(false);

  const buildParams = () => {
    const params = { limit: 50 };
    const actor = String(filters.actor || "").trim();
    const action = String(filters.action || "").trim();
    const resource = String(filters.resource || "").trim();
    const namespace = String(filters.namespace || "").trim();
    const target = String(filters.target || "").trim();
    if (actor) params.actor = actor;
    if (action) params.action = action;
    if (resource) params.resource = resource;
    if (namespace) params.namespace = namespace;
    if (target) params.target = target;
    return params;
  };

  const load = async () => {
    try {
      const res = await api.get("/admin/audit-events", { params: buildParams() });
      setEvents(Array.isArray(res.data) ? res.data : []);
      setInfo("");
      setError("");
    } catch (err) {
      setInfo("");
      setError(err.response?.data?.detail || "Failed to load audit events");
    }
  };

  const updateFilter = (key, value) => setFilters((prev) => ({ ...prev, [key]: value }));

  const clearEvents = async () => {
    if (!window.confirm("Clear all audit events?")) {
      return;
    }
    try {
      setClearing(true);
      const res = await api.delete("/admin/audit-events");
      const deleted = Number(res?.data?.deleted || 0);
      setInfo(`Cleared ${deleted} audit event${deleted === 1 ? "" : "s"}.`);
      setError("");
      await load();
    } catch (err) {
      setInfo("");
      setError(err.response?.data?.detail || "Failed to clear audit events");
    } finally {
      setClearing(false);
    }
  };

  const exportEvents = async () => {
    try {
      setExporting(true);
      const res = await api.get("/admin/audit-events/export", {
        params: buildParams(),
        responseType: "blob",
      });
      const contentDisposition = String(res.headers?.["content-disposition"] || "");
      const match = contentDisposition.match(/filename=\"?([^\";]+)\"?/i);
      const filename = match?.[1] || "admin-audit-events.csv";
      const blob = new Blob([res.data], { type: "text/csv;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      setError("");
      setInfo(`Exported ${filename}.`);
    } catch (err) {
      setInfo("");
      setError(err.response?.data?.detail || "Failed to export audit events");
    } finally {
      setExporting(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  return (
    <div>
      <h2>Audit Events</h2>
      <p className="muted small">Recent admin mutations for templates, images, quotas, settings, and operations.</p>
      {info && <div className="info">{info}</div>}
      {error && <div className="error">{error}</div>}
      <div className="form" style={{ marginBottom: "0.75rem" }}>
        <label>
          Namespace
          <input
            value={filters.namespace}
            onChange={(e) => updateFilter("namespace", e.target.value.toLowerCase())}
            placeholder="labs-team-default"
          />
        </label>
        <label>
          Actor
          <input value={filters.actor} onChange={(e) => updateFilter("actor", e.target.value)} placeholder="admin" />
        </label>
        <label>
          Action
          <input value={filters.action} onChange={(e) => updateFilter("action", e.target.value)} placeholder="create" />
        </label>
        <label>
          Resource
          <input
            value={filters.resource}
            onChange={(e) => updateFilter("resource", e.target.value)}
            placeholder="template"
          />
        </label>
        <label>
          Target
          <input value={filters.target} onChange={(e) => updateFilter("target", e.target.value)} placeholder="tmpl-1" />
        </label>
      </div>
      <div className="actions" style={{ marginBottom: "0.75rem" }}>
        <button type="button" className="ghost" onClick={load}>
          Refresh
        </button>
        <button type="button" className="ghost" onClick={exportEvents} disabled={exporting}>
          {exporting ? "Exporting..." : "Export CSV"}
        </button>
        <button type="button" className="danger" onClick={clearEvents} disabled={clearing}>
          {clearing ? "Clearing..." : "Clear Events"}
        </button>
      </div>
      <div className="tile-grid">
        {events.length === 0 && <div className="muted">No audit events.</div>}
        {events.map((event) => (
          <div key={event.id} className="tile">
            <div className="tile-header">
              <h4>{event.action}</h4>
              <span className="badge">{event.target_type}</span>
            </div>
            <div className="small muted">Namespace: {event.namespace || "-"}</div>
            <div className="small muted">Actor: {event.actor || "unknown"}</div>
            <div className="small muted">Target: {event.target_id || "-"}</div>
            <div className="small muted">When: {new Date(event.created_at).toLocaleString()}</div>
            {event.detail ? <div className="small muted">Detail: {event.detail}</div> : null}
          </div>
        ))}
      </div>
    </div>
  );
};

export default AdminAuditEvents;
