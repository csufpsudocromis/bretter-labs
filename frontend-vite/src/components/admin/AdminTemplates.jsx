import React, { useEffect, useRef, useState } from "react";
import { api } from "../../api";

const consoleProviderLabel = (provider) => {
  if (provider === "guacamole") return "Guacamole (VNC)";
  if (provider === "guacamole_rdp") return "Guacamole (RDP)";
  return "SPICE";
};

const DEFAULT_FORM = {
  name: "",
  description: "",
  os_type: "windows",
  image_id: "",
  cpu_cores: 2,
  ram_mb: 4096,
  auto_delete_minutes: 30,
  idle_timeout_minutes: 30,
  preclone_pool_size: 0,
  preclone_pool_max: 0,
  network_mode: "bridge",
  console_provider: "spice",
  rdp_default_username: "",
  rdp_default_password: "",
  rdp_default_password_configured: false,
  enabled: false,
};

const AdminTemplates = () => {
  const [templates, setTemplates] = useState([]);
  const [images, setImages] = useState([]);
  const [message, setMessage] = useState("");
  const [form, setForm] = useState({ ...DEFAULT_FORM });
  const [editingId, setEditingId] = useState(null);
  const formCardRef = useRef(null);

  const resetForm = () => {
    setForm({ ...DEFAULT_FORM });
  };

  const load = async () => {
    try {
      const [tmplRes, imgRes] = await Promise.all([api.get("/admin/templates"), api.get("/admin/images")]);
      setTemplates(tmplRes.data);
      setImages(imgRes.data);
    } catch (err) {
      setMessage(err.response?.data?.detail || "Failed to load templates/images");
    }
  };

  useEffect(() => {
    load();
  }, []);

  const create = async () => {
    try {
      const payload = { ...form, enabled: false };
      delete payload.rdp_default_password_configured;
      if (!payload.rdp_default_password) {
        delete payload.rdp_default_password;
      }
      await api.post("/admin/templates", payload);
      setMessage("");
      resetForm();
      load();
    } catch (err) {
      setMessage(err.response?.data?.detail || "Failed to create template");
    }
  };

  const toggle = async (id, enabled) => {
    try {
      await api.patch(`/admin/templates/${id}`, { enabled });
      setMessage("");
      load();
    } catch (err) {
      setMessage(err.response?.data?.detail || "Failed to toggle template");
    }
  };

  const remove = async (id) => {
    try {
      await api.delete(`/admin/templates/${id}`);
      setMessage("");
      load();
    } catch (err) {
      setMessage(err.response?.data?.detail || "Failed to delete template");
    }
  };

  const imageName = (id) => images.find((img) => img.id === id)?.name || "Image";

  const startEdit = (tmpl) => {
    setEditingId(tmpl.id);
    setForm({
      name: tmpl.name,
      description: tmpl.description || "",
      os_type: tmpl.os_type || "windows",
      image_id: tmpl.image_id,
      cpu_cores: tmpl.cpu_cores,
      ram_mb: tmpl.ram_mb,
      auto_delete_minutes: tmpl.auto_delete_minutes,
      idle_timeout_minutes: tmpl.idle_timeout_minutes || 30,
      preclone_pool_size: tmpl.preclone_pool_size || 0,
      preclone_pool_max: tmpl.preclone_pool_max ?? tmpl.preclone_pool_size ?? 0,
      enabled: tmpl.enabled,
      network_mode: tmpl.network_mode || "bridge",
      console_provider: tmpl.console_provider || "spice",
      rdp_default_username: tmpl.rdp_default_username || "",
      rdp_default_password: "",
      rdp_default_password_configured: !!tmpl.rdp_default_password_configured,
    });
    window.requestAnimationFrame(() => {
      formCardRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  };

  const saveEdit = async () => {
    try {
      const payload = { ...form };
      delete payload.rdp_default_password_configured;
      if (!payload.rdp_default_password) {
        delete payload.rdp_default_password;
      }
      await api.patch(`/admin/templates/${editingId}`, payload);
      setMessage("");
      setEditingId(null);
      resetForm();
      load();
    } catch (err) {
      setMessage(err.response?.data?.detail || "Failed to update template");
    }
  };

  const cancelEdit = () => {
    setEditingId(null);
    resetForm();
  };

  return (
    <div>
      <h2>Templates</h2>
      {message && <div className="info">{message}</div>}
      <div className="grid">
        <div>
          <div ref={formCardRef} />
          <h3>{editingId ? "Edit template" : "Create template"}</h3>
          <div className="form">
            <label>
              Name
              <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </label>
            <label>
              Description
              <textarea
                rows={3}
                value={form.description}
                onChange={(e) => setForm({ ...form, description: e.target.value })}
              />
            </label>
            <label>
              Operating System Type
              <select value={form.os_type} onChange={(e) => setForm({ ...form, os_type: e.target.value })}>
                <option value="windows">Windows</option>
                <option value="linux">Linux</option>
              </select>
            </label>
            <label>
              Image
              <select value={form.image_id} onChange={(e) => setForm({ ...form, image_id: e.target.value })}>
                <option value="">Select image</option>
                {images.map((img) => (
                  <option key={img.id} value={img.id}>
                    {img.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              CPU cores
              <input
                type="number"
                value={form.cpu_cores}
                onChange={(e) => setForm({ ...form, cpu_cores: parseInt(e.target.value, 10) || 1 })}
              />
            </label>
            <label>
              RAM (MB)
              <input
                type="number"
                value={form.ram_mb}
                onChange={(e) => setForm({ ...form, ram_mb: parseInt(e.target.value, 10) || 512 })}
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
                  setForm({ ...form, idle_timeout_minutes: Math.max(1, parseInt(e.target.value, 10) || 1) })
                }
              />
              <span className="muted small">User inactivity before showing a prompt and auto-stopping the VM.</span>
            </label>
            <label>
              Pre-clone min available
              <input
                type="number"
                min={0}
                max={50}
                value={form.preclone_pool_size}
                onChange={(e) =>
                  setForm((prev) => {
                    const minVal = Math.max(0, Math.min(50, parseInt(e.target.value, 10) || 0));
                    return {
                      ...prev,
                      preclone_pool_size: minVal,
                      preclone_pool_max: Math.max(minVal, prev.preclone_pool_max || 0),
                    };
                  })
                }
              />
              <span className="muted small">Always keep at least this many clone-ready disks.</span>
            </label>
            <label>
              Pre-clone max available
              <input
                type="number"
                min={0}
                max={50}
                value={form.preclone_pool_max}
                onChange={(e) =>
                  setForm((prev) => ({
                    ...prev,
                    preclone_pool_max: Math.max(
                      prev.preclone_pool_size || 0,
                      Math.max(0, Math.min(50, parseInt(e.target.value, 10) || 0))
                    ),
                  }))
                }
              />
              <span className="muted small">Autoscaler can grow available pre-clones up to this cap.</span>
            </label>
            <label>
              Network mode
              <select value={form.network_mode} onChange={(e) => setForm({ ...form, network_mode: e.target.value })}>
                <option value="bridge">Bridge (DNS + web egress)</option>
                <option value="none">None (no egress)</option>
                <option value="unrestricted">Unrestricted</option>
                <option value="isolated">Isolated (no egress)</option>
              </select>
            </label>
            <label>
              Console provider
              <select
                value={form.console_provider}
                onChange={(e) => setForm({ ...form, console_provider: e.target.value })}
              >
                <option value="spice">SPICE</option>
                <option value="guacamole">Guacamole (VNC)</option>
                <option value="guacamole_rdp">Guacamole (RDP)</option>
              </select>
            </label>
            {form.console_provider === "guacamole_rdp" && (
              <>
                <label>
                  RDP default username (optional)
                  <input
                    value={form.rdp_default_username}
                    onChange={(e) => setForm({ ...form, rdp_default_username: e.target.value })}
                  />
                </label>
                <label>
                  RDP default password (optional)
                  <input
                    type="password"
                    value={form.rdp_default_password}
                    onChange={(e) => setForm({ ...form, rdp_default_password: e.target.value })}
                    autoComplete="new-password"
                  />
                  <span className="muted small">
                    {editingId
                      ? form.rdp_default_password_configured
                        ? "Password is configured. Leave blank to keep the current password."
                        : "No password configured yet."
                      : "Stored encrypted per template and used for auto-connect."}
                  </span>
                </label>
              </>
            )}
            {editingId && (
              <label>
                Enabled
                <select
                  value={form.enabled ? "true" : "false"}
                  onChange={(e) => setForm({ ...form, enabled: e.target.value === "true" })}
                >
                  <option value="true">Enabled</option>
                  <option value="false">Disabled</option>
                </select>
              </label>
            )}
            {editingId ? (
              <div className="actions">
                <button className="ghost" onClick={cancelEdit}>
                  Cancel
                </button>
                <button onClick={saveEdit} disabled={!form.name || !form.image_id}>
                  Save
                </button>
              </div>
            ) : (
              <button onClick={create} disabled={!form.image_id || !form.name}>
                Create
              </button>
            )}
          </div>
        </div>
        <div>
          <h3>Existing templates</h3>
          <div className="tile-grid">
            {templates.length === 0 && <div className="muted">No templates yet.</div>}
            {templates.map((t) => (
              <div key={t.id} className="tile template-tile">
                <div className="tile-header">
                  <h4>{t.name}</h4>
                  <span className={`badge ${t.enabled ? "success" : "warn"}`}>
                    {t.enabled ? "enabled" : "disabled"}
                  </span>
                </div>
                <div className="specs">
                  <span>{t.cpu_cores} CPU</span>
                  <span>{Math.round(t.ram_mb / 1024)} GB RAM</span>
                </div>
                <div className="muted small">
                  Pre-clone pool: {t.preclone_pool_size || 0} - {t.preclone_pool_max ?? t.preclone_pool_size ?? 0}
                </div>
                <div className="muted small">Console: {consoleProviderLabel(t.console_provider)}</div>
                {t.console_provider === "guacamole_rdp" && (
                  <div className="muted small">
                    RDP defaults: {t.rdp_default_username ? `user ${t.rdp_default_username}` : "no username"},{" "}
                    {t.rdp_default_password_configured ? "password configured" : "no password"}
                  </div>
                )}
                {t.description && <div className="muted small">{t.description}</div>}
                <div className="muted small">Image: {imageName(t.image_id)}</div>
                <div className="actions">
                  <button className="ghost" onClick={() => toggle(t.id, !t.enabled)}>
                    {t.enabled ? "Disable" : "Enable"}
                  </button>
                  <button className="ghost" onClick={() => startEdit(t)}>
                    Edit
                  </button>
                  <button className="danger" onClick={() => remove(t.id)}>
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

export default AdminTemplates;
