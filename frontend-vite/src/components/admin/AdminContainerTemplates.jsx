import React, { useEffect, useState } from 'react';
import { api } from '../../api';

const DEFAULT_FORM = {
  name: '',
  description: '',
  container_image_id: '',
  cpu_cores: 1,
  memory_mb: 512,
  container_port: 80,
  command: '',
  args_text: '',
  env_text: '',
  auto_delete_minutes: 60,
  enabled: false,
};

const parseArgs = (raw) =>
  String(raw || '')
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean);

const parseEnv = (raw) => {
  const env = {};
  const lines = String(raw || '')
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean);
  for (const line of lines) {
    const idx = line.indexOf('=');
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
    .join('\n');

const formatArgs = (args) => (args || []).join(', ');
const toCpuCores = (millicores) => Math.max(1, Math.round((Number(millicores) || 1000) / 1000));
const toMillicores = (cores) => Math.max(1, parseInt(cores, 10) || 1) * 1000;

const AdminContainerTemplates = () => {
  const [templates, setTemplates] = useState([]);
  const [images, setImages] = useState([]);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [form, setForm] = useState({ ...DEFAULT_FORM });
  const [editingId, setEditingId] = useState(null);
  const [editForm, setEditForm] = useState({ ...DEFAULT_FORM });

  const load = async () => {
    try {
      const [tmplRes, imgRes] = await Promise.all([
        api.get('/admin/container-templates'),
        api.get('/admin/container-images'),
      ]);
      setTemplates(tmplRes.data || []);
      setImages(imgRes.data || []);
      setError('');
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to load container templates');
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
    command: source.command || null,
    args: parseArgs(source.args_text),
    env: parseEnv(source.env_text),
    auto_delete_minutes: Number(source.auto_delete_minutes) || 60,
    enabled: Boolean(source.enabled),
  });

  const create = async () => {
    try {
      await api.post('/admin/container-templates', toPayload(form));
      setForm({ ...DEFAULT_FORM });
      setMessage('Container template created');
      setError('');
      load();
    } catch (err) {
      setError(err.response?.data?.detail || err.message || 'Failed to create container template');
    }
  };

  const remove = async (templateId) => {
    try {
      await api.delete(`/admin/container-templates/${templateId}`);
      setMessage('Container template deleted');
      setError('');
      load();
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to delete container template');
    }
  };

  const toggle = async (templateId, enabled) => {
    try {
      await api.patch(`/admin/container-templates/${templateId}`, { enabled });
      setMessage('Container template updated');
      setError('');
      load();
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to update container template');
    }
  };

  const startEdit = (tmpl) => {
    setEditingId(tmpl.id);
    setEditForm({
      name: tmpl.name,
      description: tmpl.description || '',
      container_image_id: tmpl.container_image_id,
      cpu_cores: toCpuCores(tmpl.cpu_millicores),
      memory_mb: tmpl.memory_mb || 512,
      container_port: tmpl.container_port || 80,
      command: tmpl.command || '',
      args_text: formatArgs(tmpl.args || []),
      env_text: formatEnv(tmpl.env || {}),
      auto_delete_minutes: tmpl.auto_delete_minutes || 60,
      enabled: Boolean(tmpl.enabled),
    });
  };

  const saveEdit = async () => {
    try {
      await api.patch(`/admin/container-templates/${editingId}`, toPayload(editForm));
      setEditingId(null);
      setEditForm({ ...DEFAULT_FORM });
      setMessage('Container template saved');
      setError('');
      load();
    } catch (err) {
      setError(err.response?.data?.detail || err.message || 'Failed to update container template');
    }
  };

  const imageName = (imageId) => images.find((img) => img.id === imageId)?.name || 'Container image';
  const imageRef = (imageId) => images.find((img) => img.id === imageId)?.image_ref || '-';

  return (
    <div>
      <h2>Container Templates</h2>
      {message && <div className="info">{message}</div>}
      {error && <div className="error">{error}</div>}
      <div className="grid">
        <div>
          <h3>Create container template</h3>
          <div className="form">
            <label>
              Name
              <input value={form.name} onChange={(e) => setForm((prev) => ({ ...prev, name: e.target.value }))} />
            </label>
            <label>
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
              Command (optional)
              <input
                value={form.command}
                placeholder="python -m http.server 8080"
                onChange={(e) => setForm((prev) => ({ ...prev, command: e.target.value }))}
              />
            </label>
            <label>
              Args (comma-separated)
              <input
                value={form.args_text}
                placeholder="--port, 8080"
                onChange={(e) => setForm((prev) => ({ ...prev, args_text: e.target.value }))}
              />
            </label>
            <label>
              Environment variables
              <textarea
                rows={4}
                value={form.env_text}
                placeholder={'KEY=value\\nKEY2=value2'}
                onChange={(e) => setForm((prev) => ({ ...prev, env_text: e.target.value }))}
              />
            </label>
            <label>
              Auto-delete stopped/completed after (minutes)
              <input
                type="number"
                min={1}
                max={1440}
                value={form.auto_delete_minutes}
                onChange={(e) =>
                  setForm((prev) => ({
                    ...prev,
                    auto_delete_minutes: Math.max(1, parseInt(e.target.value, 10) || 60),
                  }))
                }
              />
            </label>
            <button onClick={create} disabled={!form.name || !form.container_image_id}>
              Create
            </button>
          </div>
        </div>
        <div>
          <h3>Existing container templates</h3>
          <div className="tile-grid">
            {templates.length === 0 && <div className="muted">No container templates yet.</div>}
            {templates.map((tmpl) => (
              <div key={tmpl.id} className="tile template-tile">
                <div className="tile-header">
                  <h4>{tmpl.name}</h4>
                  <span className={`badge ${tmpl.enabled ? 'success' : 'warn'}`}>
                    {tmpl.enabled ? 'enabled' : 'disabled'}
                  </span>
                </div>
                <div className="specs">
                  <span>{toCpuCores(tmpl.cpu_millicores)} CPU</span>
                  <span>{tmpl.memory_mb} MB RAM</span>
                  <span>Port {tmpl.container_port || 80}</span>
                </div>
                {tmpl.description && <div className="muted small">{tmpl.description}</div>}
                <div className="muted small">Image: {imageName(tmpl.container_image_id)}</div>
                <div className="muted small">Ref: {imageRef(tmpl.container_image_id)}</div>
                <div className="actions">
                  <button className="ghost" onClick={() => toggle(tmpl.id, !tmpl.enabled)}>
                    {tmpl.enabled ? 'Disable' : 'Enable'}
                  </button>
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
          {editingId && (
            <div className="card" style={{ marginTop: '1rem' }}>
              <h4>Edit container template</h4>
              <div className="form">
                <label>
                  Name
                  <input
                    value={editForm.name}
                    onChange={(e) => setEditForm((prev) => ({ ...prev, name: e.target.value }))}
                  />
                </label>
                <label>
                  Description
                  <textarea
                    rows={3}
                    value={editForm.description}
                    onChange={(e) => setEditForm((prev) => ({ ...prev, description: e.target.value }))}
                  />
                </label>
                <label>
                  Container image
                  <select
                    value={editForm.container_image_id}
                    onChange={(e) => setEditForm((prev) => ({ ...prev, container_image_id: e.target.value }))}
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
                    value={editForm.cpu_cores}
                    onChange={(e) =>
                      setEditForm((prev) => ({ ...prev, cpu_cores: parseInt(e.target.value, 10) || 1 }))
                    }
                  />
                </label>
                <label>
                  Memory (MB)
                  <input
                    type="number"
                    min={64}
                    max={131072}
                    value={editForm.memory_mb}
                    onChange={(e) =>
                      setEditForm((prev) => ({ ...prev, memory_mb: parseInt(e.target.value, 10) || 512 }))
                    }
                  />
                </label>
                <label>
                  Container port
                  <input
                    type="number"
                    min={1}
                    max={65535}
                    value={editForm.container_port}
                    onChange={(e) =>
                      setEditForm((prev) => ({
                        ...prev,
                        container_port: Math.max(1, Math.min(65535, parseInt(e.target.value, 10) || 80)),
                      }))
                    }
                  />
                </label>
                <label>
                  Command (optional)
                  <input
                    value={editForm.command}
                    onChange={(e) => setEditForm((prev) => ({ ...prev, command: e.target.value }))}
                  />
                </label>
                <label>
                  Args (comma-separated)
                  <input
                    value={editForm.args_text}
                    onChange={(e) => setEditForm((prev) => ({ ...prev, args_text: e.target.value }))}
                  />
                </label>
                <label>
                  Environment variables
                  <textarea
                    rows={4}
                    value={editForm.env_text}
                    onChange={(e) => setEditForm((prev) => ({ ...prev, env_text: e.target.value }))}
                  />
                </label>
                <label>
                  Auto-delete stopped/completed after (minutes)
                  <input
                    type="number"
                    min={1}
                    max={1440}
                    value={editForm.auto_delete_minutes}
                    onChange={(e) =>
                      setEditForm((prev) => ({
                        ...prev,
                        auto_delete_minutes: Math.max(1, parseInt(e.target.value, 10) || 60),
                      }))
                    }
                  />
                </label>
                <label>
                  Enabled
                  <select
                    value={editForm.enabled ? 'true' : 'false'}
                    onChange={(e) => setEditForm((prev) => ({ ...prev, enabled: e.target.value === 'true' }))}
                  >
                    <option value="true">Enabled</option>
                    <option value="false">Disabled</option>
                  </select>
                </label>
                <div className="actions">
                  <button className="ghost" onClick={() => setEditingId(null)}>
                    Cancel
                  </button>
                  <button onClick={saveEdit} disabled={!editForm.name || !editForm.container_image_id}>
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

export default AdminContainerTemplates;
