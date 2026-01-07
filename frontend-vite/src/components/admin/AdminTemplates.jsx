import React, { useEffect, useState } from 'react';
import { api } from '../../api';

const AdminTemplates = () => {
  const [templates, setTemplates] = useState([]);
  const [images, setImages] = useState([]);
  const [message, setMessage] = useState('');
  const [form, setForm] = useState({
    name: '',
    description: '',
    os_type: 'windows',
    image_id: '',
    cpu_cores: 2,
    ram_mb: 4096,
    auto_delete_minutes: 30,
    idle_timeout_minutes: 30,
    network_mode: 'bridge',
  });
  const [editingId, setEditingId] = useState(null);
  const [editForm, setEditForm] = useState({
    name: '',
    description: '',
    os_type: 'windows',
    image_id: '',
    cpu_cores: 2,
    ram_mb: 4096,
    auto_delete_minutes: 30,
    idle_timeout_minutes: 30,
    enabled: false,
    network_mode: 'bridge',
  });

  const load = async () => {
    try {
      const [tmplRes, imgRes] = await Promise.all([api.get('/admin/templates'), api.get('/admin/images')]);
      setTemplates(tmplRes.data);
      setImages(imgRes.data);
    } catch (err) {
      setMessage(err.response?.data?.detail || 'Failed to load templates/images');
    }
  };

  useEffect(() => {
    load();
  }, []);

  const create = async () => {
    try {
      await api.post('/admin/templates', { ...form, enabled: false });
      setMessage('');
      setForm({
        name: '',
        description: '',
        os_type: 'windows',
        image_id: '',
        cpu_cores: 2,
        ram_mb: 4096,
        auto_delete_minutes: 30,
        idle_timeout_minutes: 30,
        network_mode: 'bridge',
      });
      load();
    } catch (err) {
      setMessage(err.response?.data?.detail || 'Failed to create template');
    }
  };

  const toggle = async (id, enabled) => {
    try {
      await api.patch(`/admin/templates/${id}`, { enabled });
      setMessage('');
      load();
    } catch (err) {
      setMessage(err.response?.data?.detail || 'Failed to toggle template');
    }
  };

  const remove = async (id) => {
    try {
      await api.delete(`/admin/templates/${id}`);
      setMessage('');
      load();
    } catch (err) {
      setMessage(err.response?.data?.detail || 'Failed to delete template');
    }
  };

  const imageName = (id) => images.find((img) => img.id === id)?.name || 'Image';

  const startEdit = (tmpl) => {
    setEditingId(tmpl.id);
    setEditForm({
      name: tmpl.name,
      description: tmpl.description || '',
      os_type: tmpl.os_type || 'windows',
      image_id: tmpl.image_id,
      cpu_cores: tmpl.cpu_cores,
      ram_mb: tmpl.ram_mb,
      auto_delete_minutes: tmpl.auto_delete_minutes,
      idle_timeout_minutes: tmpl.idle_timeout_minutes || 30,
      enabled: tmpl.enabled,
      network_mode: tmpl.network_mode || 'bridge',
    });
  };

  const saveEdit = async () => {
    try {
      await api.patch(`/admin/templates/${editingId}`, { ...editForm });
      setMessage('');
      setEditingId(null);
      load();
    } catch (err) {
      setMessage(err.response?.data?.detail || 'Failed to update template');
    }
  };

  return (
    <div>
      <h2>Templates</h2>
      {message && <div className="info">{message}</div>}
      <div className="grid">
        <div>
          <h3>Create template</h3>
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
              Auto-delete stopped/completed after (minutes)
              <select
                value={form.auto_delete_minutes}
                onChange={(e) => setForm({ ...form, auto_delete_minutes: parseInt(e.target.value, 10) || 1 })}
              >
                {Array.from({ length: 30 }, (_, i) => i + 1).map((n) => (
                  <option key={n} value={n}>
                    {n} minute{n > 1 ? 's' : ''}
                  </option>
                ))}
              </select>
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
              Network mode
              <select value={form.network_mode} onChange={(e) => setForm({ ...form, network_mode: e.target.value })}>
                <option value="bridge">Bridge (DNS/HTTP/HTTPS egress)</option>
                <option value="host">Host</option>
                <option value="none">None (no egress)</option>
                <option value="unrestricted">Unrestricted</option>
                <option value="isolated">Isolated (no egress)</option>
              </select>
            </label>
            <button onClick={create} disabled={!form.image_id || !form.name}>
              Create
            </button>
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
                  <span className={`badge ${t.enabled ? 'success' : 'warn'}`}>{t.enabled ? 'enabled' : 'disabled'}</span>
                </div>
                <div className="specs">
                  <span>{t.cpu_cores} CPU</span>
                  <span>{Math.round(t.ram_mb / 1024)} GB RAM</span>
                </div>
                {t.description && <div className="muted small">{t.description}</div>}
                <div className="muted small">Image: {imageName(t.image_id)}</div>
                <div className="actions">
                  <button className="ghost" onClick={() => toggle(t.id, !t.enabled)}>
                    {t.enabled ? 'Disable' : 'Enable'}
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
          {editingId && (
            <div className="card" style={{ marginTop: '1rem' }}>
              <h4>Edit template</h4>
              <div className="form">
                <label>
                  Name
                  <input value={editForm.name} onChange={(e) => setEditForm({ ...editForm, name: e.target.value })} />
                </label>
                <label>
                  Description
                  <textarea
                    rows={3}
                    value={editForm.description}
                    onChange={(e) => setEditForm({ ...editForm, description: e.target.value })}
                  />
                </label>
                <label>
                  Operating System Type
                  <select value={editForm.os_type} onChange={(e) => setEditForm({ ...editForm, os_type: e.target.value })}>
                    <option value="windows">Windows</option>
                    <option value="linux">Linux</option>
                  </select>
                </label>
                <label>
                  Image
                  <select
                    value={editForm.image_id}
                    onChange={(e) => setEditForm({ ...editForm, image_id: e.target.value })}
                  >
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
                    value={editForm.cpu_cores}
                    onChange={(e) =>
                      setEditForm({ ...editForm, cpu_cores: parseInt(e.target.value, 10) || editForm.cpu_cores })
                    }
                  />
                </label>
                <label>
                  RAM (MB)
                  <input
                    type="number"
                    value={editForm.ram_mb}
                    onChange={(e) =>
                      setEditForm({ ...editForm, ram_mb: parseInt(e.target.value, 10) || editForm.ram_mb })
                    }
                  />
                </label>
                <label>
                  Auto-delete stopped/completed after (minutes)
                  <select
                    value={editForm.auto_delete_minutes}
                    onChange={(e) =>
                      setEditForm({
                        ...editForm,
                        auto_delete_minutes: parseInt(e.target.value, 10) || editForm.auto_delete_minutes,
                      })
                    }
                  >
                    {Array.from({ length: 30 }, (_, i) => i + 1).map((n) => (
                      <option key={n} value={n}>
                        {n} minute{n > 1 ? 's' : ''}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Idle timeout (minutes)
                  <input
                    type="number"
                    min={1}
                    max={1440}
                    value={editForm.idle_timeout_minutes}
                    onChange={(e) =>
                      setEditForm({
                        ...editForm,
                        idle_timeout_minutes: Math.max(1, parseInt(e.target.value, 10) || editForm.idle_timeout_minutes),
                      })
                    }
                  />
                  <span className="muted small">User inactivity before showing a prompt and auto-stopping the VM.</span>
                </label>
                <label>
                  Network mode
                  <select
                    value={editForm.network_mode}
                    onChange={(e) => setEditForm({ ...editForm, network_mode: e.target.value })}
                  >
                    <option value="bridge">Bridge (DNS/HTTP/HTTPS egress)</option>
                    <option value="host">Host</option>
                    <option value="none">None (no egress)</option>
                    <option value="unrestricted">Unrestricted</option>
                    <option value="isolated">Isolated (no egress)</option>
                  </select>
                </label>
                <label>
                  Enabled
                  <select
                    value={editForm.enabled ? 'true' : 'false'}
                    onChange={(e) => setEditForm({ ...editForm, enabled: e.target.value === 'true' })}
                  >
                    <option value="true">Enabled</option>
                    <option value="false">Disabled</option>
                  </select>
                </label>
                <div className="actions">
                  <button className="ghost" onClick={() => setEditingId(null)}>
                    Cancel
                  </button>
                  <button onClick={saveEdit} disabled={!editForm.name || !editForm.image_id}>
                    Save
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default AdminTemplates;
