import React, { useEffect, useState } from "react";
import { api } from "../../api";

const AdminAuditEvents = () => {
  const [events, setEvents] = useState([]);
  const [error, setError] = useState("");

  const load = async () => {
    try {
      const res = await api.get("/admin/audit-events", { params: { limit: 200 } });
      setEvents(Array.isArray(res.data) ? res.data : []);
      setError("");
    } catch (err) {
      setError(err.response?.data?.detail || "Failed to load audit events");
    }
  };

  useEffect(() => {
    load();
  }, []);

  return (
    <div>
      <h2>Audit Events</h2>
      <p className="muted small">Recent admin mutations for templates, images, quotas, settings, and operations.</p>
      {error && <div className="error">{error}</div>}
      <div className="actions" style={{ marginBottom: "0.75rem" }}>
        <button type="button" className="ghost" onClick={load}>
          Refresh
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
