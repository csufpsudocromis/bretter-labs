import React, { useEffect, useState } from "react";

import { api } from "../../api";

const fmtDateTime = (value) => {
  if (!value) return "n/a";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
};

const PAGE_BUTTON_WINDOW = 7;

const buildPageNumbers = (currentPage, totalPages) => {
  if (!totalPages || totalPages <= 1) return [];
  let start = Math.max(1, currentPage - Math.floor(PAGE_BUTTON_WINDOW / 2));
  let end = Math.min(totalPages, start + PAGE_BUTTON_WINDOW - 1);
  start = Math.max(1, end - PAGE_BUTTON_WINDOW + 1);
  const pages = [];
  for (let page = start; page <= end; page += 1) {
    pages.push(page);
  }
  return pages;
};

const AdminAlertsErrors = () => {
  const [data, setData] = useState(null);
  const [message, setMessage] = useState("");
  const [infoMessage, setInfoMessage] = useState("");
  const [loading, setLoading] = useState(false);
  const [errorPage, setErrorPage] = useState(1);

  const load = async (targetPage = errorPage) => {
    setLoading(true);
    setMessage("");
    setInfoMessage("");
    try {
      const res = await api.get("/admin/alerts-errors", { params: { page: targetPage } });
      setData(res.data);
      const resolvedPage = res.data?.error_log?.page || targetPage;
      if (resolvedPage !== errorPage) {
        setErrorPage(resolvedPage);
      }
    } catch (err) {
      setMessage(err.response?.data?.detail || "Failed to load alerts and errors.");
    } finally {
      setLoading(false);
    }
  };

  const clearErrorLog = async () => {
    if (!window.confirm("Clear the backend error log file now?")) return;
    setLoading(true);
    setMessage("");
    setInfoMessage("");
    try {
      const res = await api.post("/admin/alerts-errors/clear");
      setInfoMessage(res.data?.detail || "Error log cleared.");
      const targetPage = 1;
      const refreshed = await api.get("/admin/alerts-errors", { params: { page: targetPage } });
      setData(refreshed.data);
      setErrorPage(targetPage);
    } catch (err) {
      setMessage(err.response?.data?.detail || "Failed to clear error log.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load(errorPage);
  }, [errorPage]);

  const errorLog = data?.error_log;
  const pageNumbers = buildPageNumbers(errorLog?.page || 1, errorLog?.total_pages || 1);
  const errorText = errorLog?.lines?.length
    ? errorLog.lines.join("\n")
    : errorLog?.content || "No error log lines found.";

  return (
    <div>
      <h2>Alerts and Errors</h2>
      <div className="actions" style={{ marginBottom: "1rem" }}>
        <button className="ghost" onClick={() => load(errorPage)} disabled={loading}>
          {loading ? "Refreshing..." : "Refresh"}
        </button>
        <button className="danger" onClick={clearErrorLog} disabled={loading}>
          Clear Error Log
        </button>
      </div>
      {message && <div className="error">{message}</div>}
      {infoMessage && <div className="muted small">{infoMessage}</div>}
      {data && (
        <>
          <div className="card" style={{ marginBottom: "1rem" }}>
            <h3>Alertmanager Alerts</h3>
            <div className="muted small">Fetched: {fmtDateTime(data.fetched_at)}</div>
            <div className="muted small">Source: {data.alertmanager_url || "not configured"}</div>
            {data.alertmanager_error && <div className="error">{data.alertmanager_error}</div>}
            {!data.alertmanager_error && data.alerts.length === 0 && (
              <div className="muted small">No active Alertmanager messages.</div>
            )}
            {data.alerts.length > 0 && (
              <div className="tile-grid" style={{ marginTop: "0.75rem" }}>
                {data.alerts.map((alert, idx) => (
                  <div className="tile template-tile" key={`${alert.name}-${alert.starts_at || idx}`}>
                    <div className="tile-header">
                      <h4>{alert.summary || alert.name}</h4>
                      <span className={`badge ${alert.state === "active" ? "warn" : "success"}`}>{alert.state}</span>
                    </div>
                    <div className="specs">
                      <span>name: {alert.name}</span>
                      <span>severity: {alert.severity || "n/a"}</span>
                      <span>starts: {fmtDateTime(alert.starts_at)}</span>
                    </div>
                    {alert.description && <div className="muted small">{alert.description}</div>}
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="card">
            <h3>Error Logs</h3>
            <div className="muted small">Source: {data.error_log.source}</div>
            <div className="muted small">
              Size shown: {(data.error_log.bytes / (1024 * 1024)).toFixed(2)} MB (max 10.00 MB)
            </div>
            <div className="muted small">
              Showing page {data.error_log.page} of {data.error_log.total_pages} ({data.error_log.total_lines} errors,
              50 per page)
            </div>
            {data.error_log.truncated && (
              <div className="muted small">Oldest entries were dropped after reaching the 10MB cap.</div>
            )}
            {pageNumbers.length > 0 && (
              <div className="actions" style={{ marginTop: "0.75rem", gap: "0.35rem", flexWrap: "wrap" }}>
                <button
                  className="ghost"
                  onClick={() => setErrorPage((prev) => Math.max(1, prev - 1))}
                  disabled={!data.error_log.has_prev || loading}
                >
                  Prev
                </button>
                {pageNumbers.map((page) => (
                  <button
                    key={page}
                    className={page === data.error_log.page ? "" : "ghost"}
                    onClick={() => setErrorPage(page)}
                    disabled={loading}
                  >
                    {page}
                  </button>
                ))}
                <button
                  className="ghost"
                  onClick={() => setErrorPage((prev) => prev + 1)}
                  disabled={!data.error_log.has_next || loading}
                >
                  Next
                </button>
              </div>
            )}
            <pre
              style={{
                marginTop: "0.75rem",
                maxHeight: "28rem",
                overflowY: "auto",
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
              }}
            >
              {errorText}
            </pre>
          </div>
        </>
      )}
    </div>
  );
};

export default AdminAlertsErrors;
