import React, { useEffect, useState } from "react";
import { api } from "../../api";

const DEFAULT_FORM = {
  name: "",
  description: "",
  container_image_id: "",
  cpu_cores: 1,
  memory_mb: 512,
  container_port: 80,
  healthcheck_protocol: "tcp",
  healthcheck_path: "/",
  readiness_http_status: 200,
  readiness_success_path: "",
  startup_timeout_seconds: 300,
  dependency_checks_text: "",
  expose_strategy: "nodeport",
  network_mode: "bridge",
  run_as_non_root: false,
  read_only_root_filesystem: false,
  command: "",
  args_text: "",
  env_text: "",
  auto_delete_minutes: 60,
  idle_timeout_minutes: 30,
  enabled_namespaces: [],
  enabled: false,
};

const parseArgs = (raw) =>
  String(raw || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);

const parseEnv = (raw) => {
  const env = {};
  const lines = String(raw || "")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  for (const line of lines) {
    const idx = line.indexOf("=");
    if (idx <= 0) {
      throw new Error(`Invalid env line: ${line}. Use KEY=value format.`);
    }
    const key = line.slice(0, idx).trim();
    const value = line.slice(idx + 1).trim();
    env[key] = value;
  }
  return env;
};

const formatEnv = (env) =>
  Object.entries(env || {})
    .map(([key, value]) => `${key}=${value}`)
    .join("\n");

const formatArgs = (args) => (args || []).join(", ");
const parseDependencyChecks = (raw) =>
  String(raw || "")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const parts = line.split(":").map((p) => p.trim());
      if (parts.length < 2) {
        throw new Error(`Invalid dependency line: ${line}. Use host:port[:timeoutSeconds].`);
      }
      return {
        host: parts[0],
        port: Math.max(1, Math.min(65535, parseInt(parts[1], 10) || 0)),
        timeout_seconds: Math.max(5, Math.min(600, parseInt(parts[2], 10) || 90)),
      };
    })
    .filter((item) => item.host && item.port > 0);
const formatDependencyChecks = (items) =>
  (items || []).map((item) => `${item.host}:${item.port}:${item.timeout_seconds || 90}`).join("\n");
const toCpuCores = (millicores) => Math.max(1, Math.round((Number(millicores) || 1000) / 1000));
const toMillicores = (cores) => Math.max(1, parseInt(cores, 10) || 1) * 1000;

const AdminContainerTemplates = () => {
  const [templates, setTemplates] = useState([]);
  const [images, setImages] = useState([]);
  const [namespaceOptions, setNamespaceOptions] = useState([]);
  const [isPlatformAdmin, setIsPlatformAdmin] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [form, setForm] = useState({ ...DEFAULT_FORM });
  const [editingId, setEditingId] = useState(null);

  const load = async () => {
    try {
      const [tmplRes, imgRes, nsRes, meRes] = await Promise.all([
        api.get("/admin/container-templates"),
        api.get("/admin/container-images"),
        api.get("/admin/template-namespaces"),
        api.get("/auth/me"),
      ]);
      setTemplates(tmplRes.data || []);
      setImages(imgRes.data || []);
      const options = Array.isArray(nsRes.data)
        ? [
            ...new Set(
              nsRes.data
                .map((value) =>
                  String(value || "")
                    .trim()
                    .toLowerCase()
                )
                .filter(Boolean)
            ),
          ]
        : [];
      setNamespaceOptions(options);
      setIsPlatformAdmin(
        String(meRes?.data?.role || "")
          .trim()
          .toLowerCase() === "platform_admin"
      );
      setError("");
    } catch (err) {
      setError(err.response?.data?.detail || "Failed to load container templates");
    }
  };

  useEffect(() => {
    load();
  }, []);

  const toPayload = (source) => ({
    name: source.name,
    description: source.description,
    container_image_id: source.container_image_id,
    cpu_millicores: toMillicores(source.cpu_cores),
    memory_mb: Number(source.memory_mb) || 512,
    container_port: Math.max(1, Math.min(65535, Number(source.container_port) || 80)),
    healthcheck_protocol: source.healthcheck_protocol === "http" ? "http" : "tcp",
    healthcheck_path: String(source.healthcheck_path || "/").trim() || "/",
    readiness_http_status: Math.max(100, Math.min(599, Number(source.readiness_http_status) || 200)),
    readiness_success_path: String(source.readiness_success_path || "").trim() || null,
    startup_timeout_seconds: Math.max(10, Number(source.startup_timeout_seconds) || 300),
    dependency_checks: parseDependencyChecks(source.dependency_checks_text),
    expose_strategy: source.expose_strategy === "ingress" ? "ingress" : "nodeport",
    network_mode: ["bridge", "none", "isolated", "unrestricted"].includes(String(source.network_mode || "bridge"))
      ? String(source.network_mode || "bridge")
      : "bridge",
    run_as_non_root: Boolean(source.run_as_non_root),
    read_only_root_filesystem: Boolean(source.read_only_root_filesystem),
    command: source.command || null,
    args: parseArgs(source.args_text),
    env: parseEnv(source.env_text),
    auto_delete_minutes: Number(source.auto_delete_minutes) || 60,
    idle_timeout_minutes: Math.max(1, Number(source.idle_timeout_minutes) || 30),
    enabled_namespaces: Array.isArray(source.enabled_namespaces) ? source.enabled_namespaces : [],
    enabled: Boolean(source.enabled),
  });

  const create = async () => {
    try {
      await api.post("/admin/container-templates", toPayload(form));
      setForm({ ...DEFAULT_FORM });
      setMessage("Container template created");
      setError("");
      load();
    } catch (err) {
      setError(err.response?.data?.detail || err.message || "Failed to create container template");
    }
  };

  const remove = async (templateId) => {
    try {
      await api.delete(`/admin/container-templates/${templateId}`);
      setMessage("Container template deleted");
      setError("");
      load();
    } catch (err) {
      setError(err.response?.data?.detail || "Failed to delete container template");
    }
  };

  const toggle = async (templateId, enabled) => {
    try {
      await api.patch(`/admin/container-templates/${templateId}`, { enabled });
      setMessage("Container template updated");
      setError("");
      load();
    } catch (err) {
      setError(err.response?.data?.detail || "Failed to update container template");
    }
  };

  const startEdit = (tmpl) => {
    setEditingId(tmpl.id);
    setForm({
      name: tmpl.name,
      description: tmpl.description || "",
      container_image_id: tmpl.container_image_id,
      cpu_cores: toCpuCores(tmpl.cpu_millicores),
      memory_mb: tmpl.memory_mb || 512,
      container_port: tmpl.container_port || 80,
      healthcheck_protocol: tmpl.healthcheck_protocol || "tcp",
      healthcheck_path: tmpl.healthcheck_path || "/",
      readiness_http_status: tmpl.readiness_http_status || 200,
      readiness_success_path: tmpl.readiness_success_path || "",
      startup_timeout_seconds: tmpl.startup_timeout_seconds || 300,
      dependency_checks_text: formatDependencyChecks(tmpl.dependency_checks || []),
      expose_strategy: tmpl.expose_strategy || "nodeport",
      network_mode: tmpl.network_mode || "bridge",
      run_as_non_root: Boolean(tmpl.run_as_non_root),
      read_only_root_filesystem: Boolean(tmpl.read_only_root_filesystem),
      command: tmpl.command || "",
      args_text: formatArgs(tmpl.args || []),
      env_text: formatEnv(tmpl.env || {}),
      auto_delete_minutes: tmpl.auto_delete_minutes || 60,
      idle_timeout_minutes: tmpl.idle_timeout_minutes || 30,
      enabled_namespaces: Array.isArray(tmpl.enabled_namespaces) ? tmpl.enabled_namespaces : [],
      enabled: Boolean(tmpl.enabled),
    });
    setMessage("");
    setError("");
  };

  const saveEdit = async () => {
    try {
      await api.patch(`/admin/container-templates/${editingId}`, toPayload(form));
      setEditingId(null);
      setForm({ ...DEFAULT_FORM });
      setMessage("Container template saved");
      setError("");
      load();
    } catch (err) {
      setError(err.response?.data?.detail || err.message || "Failed to update container template");
    }
  };

  const cancelEdit = () => {
    setEditingId(null);
    setForm({ ...DEFAULT_FORM });
    setError("");
  };

  const imageRef = (imageId) => images.find((img) => img.id === imageId)?.image_ref || "-";
  const toggleNamespaceSelection = (namespace) => {
    const target = String(namespace || "")
      .trim()
      .toLowerCase();
    if (!target) return;
    setForm((prev) => {
      const current = Array.isArray(prev.enabled_namespaces) ? prev.enabled_namespaces : [];
      if (current.includes(target)) {
        return { ...prev, enabled_namespaces: current.filter((item) => item !== target) };
      }
      return { ...prev, enabled_namespaces: [...current, target].sort() };
    });
  };

  return (
    <div className="container-templates-page">
      <h2>Container Templates</h2>
      {message && <div className="info">{message}</div>}
      {error && <div className="error">{error}</div>}
      <div className="grid">
        <div className="card">
          <h3>{editingId ? "Edit container template" : "Create container template"}</h3>
          <div className="form container-template-form">
            <label>
              Name
              <input value={form.name} onChange={(e) => setForm((prev) => ({ ...prev, name: e.target.value }))} />
            </label>
            <label className="span-2">
              Description
              <textarea
                rows={3}
                value={form.description}
                onChange={(e) => setForm((prev) => ({ ...prev, description: e.target.value }))}
              />
            </label>
            <label>
              Container image
              <select
                value={form.container_image_id}
                onChange={(e) => setForm((prev) => ({ ...prev, container_image_id: e.target.value }))}
              >
                <option value="">Select image</option>
                {images.map((img) => (
                  <option key={img.id} value={img.id}>
                    {img.name} ({img.image_ref})
                  </option>
                ))}
              </select>
            </label>
            <label>
              CPU cores
              <input
                type="number"
                min={1}
                max={32}
                value={form.cpu_cores}
                onChange={(e) => setForm((prev) => ({ ...prev, cpu_cores: parseInt(e.target.value, 10) || 1 }))}
              />
            </label>
            <label>
              Memory (MB)
              <input
                type="number"
                min={64}
                max={131072}
                value={form.memory_mb}
                onChange={(e) => setForm((prev) => ({ ...prev, memory_mb: parseInt(e.target.value, 10) || 512 }))}
              />
            </label>
            <label>
              Container port
              <input
                type="number"
                min={1}
                max={65535}
                value={form.container_port}
                onChange={(e) =>
                  setForm((prev) => ({
                    ...prev,
                    container_port: Math.max(1, Math.min(65535, parseInt(e.target.value, 10) || 80)),
                  }))
                }
              />
            </label>
            <label>
              Access strategy
              <select
                value={form.expose_strategy}
                onChange={(e) => setForm((prev) => ({ ...prev, expose_strategy: e.target.value }))}
              >
                <option value="nodeport">NodePort</option>
                <option value="ingress">Ingress</option>
              </select>
            </label>
            <label>
              Network mode
              <select
                value={form.network_mode}
                onChange={(e) => setForm((prev) => ({ ...prev, network_mode: e.target.value }))}
              >
                <option value="bridge">Bridge (DNS/HTTP/HTTPS egress)</option>
                <option value="isolated">Isolated (deny egress)</option>
                <option value="none">None (deny egress)</option>
                <option value="unrestricted">Unrestricted (no policy)</option>
              </select>
            </label>
            <div className="span-2 form-field">
              <span>Enabled namespaces</span>
              <div className="namespace-scope-list">
                {namespaceOptions.length === 0 && <div className="muted small">No namespace options available.</div>}
                {namespaceOptions.map((namespace) => (
                  <label key={namespace} className="permission-row">
                    <input
                      type="checkbox"
                      checked={(form.enabled_namespaces || []).includes(namespace)}
                      onChange={() => toggleNamespaceSelection(namespace)}
                    />
                    <span className="permission-id">{namespace}</span>
                  </label>
                ))}
              </div>
              <span className="muted small">Select the namespaces where this template can be launched.</span>
            </div>
            <label>
              Healthcheck protocol
              <select
                value={form.healthcheck_protocol}
                onChange={(e) => setForm((prev) => ({ ...prev, healthcheck_protocol: e.target.value }))}
              >
                <option value="tcp">TCP</option>
                <option value="http">HTTP</option>
              </select>
            </label>
            <label>
              Healthcheck path
              <input
                value={form.healthcheck_path}
                placeholder="/"
                onChange={(e) => setForm((prev) => ({ ...prev, healthcheck_path: e.target.value }))}
              />
            </label>
            <label>
              Expected HTTP status
              <input
                type="number"
                min={100}
                max={599}
                value={form.readiness_http_status}
                onChange={(e) =>
                  setForm((prev) => ({
                    ...prev,
                    readiness_http_status: Math.max(100, Math.min(599, parseInt(e.target.value, 10) || 200)),
                  }))
                }
              />
            </label>
            <label>
              Optional success path
              <input
                value={form.readiness_success_path}
                placeholder="/ready"
                onChange={(e) => setForm((prev) => ({ ...prev, readiness_success_path: e.target.value }))}
              />
            </label>
            <label>
              Startup timeout (seconds)
              <input
                type="number"
                min={10}
                max={1800}
                value={form.startup_timeout_seconds}
                onChange={(e) =>
                  setForm((prev) => ({
                    ...prev,
                    startup_timeout_seconds: Math.max(10, Math.min(1800, parseInt(e.target.value, 10) || 300)),
                  }))
                }
              />
            </label>
            <label style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
              <input
                type="checkbox"
                checked={form.run_as_non_root}
                onChange={(e) => setForm((prev) => ({ ...prev, run_as_non_root: e.target.checked }))}
              />
              Run as non-root
            </label>
            <label style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
              <input
                type="checkbox"
                checked={form.read_only_root_filesystem}
                onChange={(e) => setForm((prev) => ({ ...prev, read_only_root_filesystem: e.target.checked }))}
              />
              Read-only root filesystem
            </label>
            <label className="span-2">
              Dependency checks (host:port[:timeoutSeconds], one per line)
              <textarea
                rows={4}
                value={form.dependency_checks_text}
                placeholder={"db.<namespace>.svc.cluster.local:5432:120\\ncache.<namespace>.svc.cluster.local:6379:60"}
                onChange={(e) => setForm((prev) => ({ ...prev, dependency_checks_text: e.target.value }))}
              />
            </label>
            <label className="span-2">
              Command (optional)
              <input
                value={form.command}
                placeholder="python -m http.server 8080"
                onChange={(e) => setForm((prev) => ({ ...prev, command: e.target.value }))}
              />
            </label>
            <label className="span-2">
              Args (comma-separated)
              <input
                value={form.args_text}
                placeholder="--port, 8080"
                onChange={(e) => setForm((prev) => ({ ...prev, args_text: e.target.value }))}
              />
            </label>
            <label className="span-2">
              Environment variables
              <textarea
                rows={4}
                value={form.env_text}
                placeholder={"KEY=value\\nKEY2=value2"}
                onChange={(e) => setForm((prev) => ({ ...prev, env_text: e.target.value }))}
              />
            </label>
            <label>
              Idle timeout (minutes)
              <input
                type="number"
                min={1}
                max={1440}
                value={form.idle_timeout_minutes}
                onChange={(e) =>
                  setForm((prev) => ({
                    ...prev,
                    idle_timeout_minutes: Math.max(1, parseInt(e.target.value, 10) || 30),
                  }))
                }
              />
            </label>
            {editingId && isPlatformAdmin && (
              <label>
                Enabled
                <select
                  value={form.enabled ? "true" : "false"}
                  onChange={(e) => setForm((prev) => ({ ...prev, enabled: e.target.value === "true" }))}
                >
                  <option value="true">Enabled</option>
                  <option value="false">Disabled</option>
                </select>
              </label>
            )}
            {editingId ? (
              <div className="actions span-2">
                <button className="ghost" onClick={cancelEdit}>
                  Cancel
                </button>
                <button onClick={saveEdit} disabled={!form.name || !form.container_image_id}>
                  Save
                </button>
              </div>
            ) : (
              <button className="span-2" onClick={create} disabled={!form.name || !form.container_image_id}>
                Create
              </button>
            )}
          </div>
        </div>
        <div className="card">
          <h3>Existing container templates</h3>
          <div className="tile-grid">
            {templates.length === 0 && <div className="muted">No container templates yet.</div>}
            {templates.map((tmpl) => (
              <div key={tmpl.id} className="tile template-tile">
                <div className="tile-header">
                  <h4>{tmpl.name}</h4>
                  <span className={`badge ${tmpl.enabled ? "success" : "warn"}`}>
                    {tmpl.enabled ? "enabled" : "disabled"}
                  </span>
                </div>
                <div className="specs">
                  <span>{toCpuCores(tmpl.cpu_millicores)} CPU</span>
                  <span>{tmpl.memory_mb} MB RAM</span>
                  <span>Port {tmpl.container_port || 80}</span>
                </div>
                <div className="muted small template-image-ref">Image: {imageRef(tmpl.container_image_id)}</div>
                <div className="muted small">
                  Enabled namespaces:{" "}
                  {Array.isArray(tmpl.enabled_namespaces) && tmpl.enabled_namespaces.length > 0
                    ? tmpl.enabled_namespaces.join(", ")
                    : "-"}
                </div>
                <div className="actions">
                  {isPlatformAdmin && (
                    <button className="ghost" onClick={() => toggle(tmpl.id, !tmpl.enabled)}>
                      {tmpl.enabled ? "Disable" : "Enable"}
                    </button>
                  )}
                  <button className="ghost" onClick={() => startEdit(tmpl)}>
                    Edit
                  </button>
                  <button className="danger" onClick={() => remove(tmpl.id)}>
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

export default AdminContainerTemplates;
