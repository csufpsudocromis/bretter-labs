import React, { useEffect, useState } from 'react';
import { api } from '../../api';

const DEFAULT_FORM = {
  name: '',
  source: 'docker_hub',
  registry: 'docker.io',
  repository: '',
  tag: 'latest',
  image_ref: '',
};

const normalizeRegistry = (value) => String(value || '').trim().replace(/^https?:\/\//, '').replace(/\/+$/, '');

const inferNameFromRef = (imageRef) => {
  const raw = String(imageRef || '').trim();
  if (!raw) return '';
  const withoutDigest = raw.split('@')[0];
  const lastSlash = withoutDigest.lastIndexOf('/');
  const lastColon = withoutDigest.lastIndexOf(':');
  const withoutTag = lastColon > lastSlash ? withoutDigest.slice(0, lastColon) : withoutDigest;
  const tail = withoutTag.split('/').filter(Boolean).pop();
  return tail || raw;
};

const buildImageRef = (form) => {
  const source = String(form.source || 'docker_hub');
  if (source === 'direct') {
    return String(form.image_ref || '').trim();
  }

  const repository = String(form.repository || '').trim().replace(/^\/+/, '').replace(/\/+$/, '');
  if (!repository) return '';

  const tag = String(form.tag || '').trim();
  const suffix = tag ? `:${tag}` : '';
  if (source === 'docker_hub') {
    return `${repository}${suffix}`;
  }

  const registry = normalizeRegistry(form.registry);
  if (!registry) return '';
  return `${registry}/${repository}${suffix}`;
};

const AdminContainerImages = () => {
  const [images, setImages] = useState([]);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [form, setForm] = useState({ ...DEFAULT_FORM });
  const [editingId, setEditingId] = useState(null);
  const [editForm, setEditForm] = useState({ name: '', image_ref: '' });

  const load = async () => {
    try {
      const res = await api.get('/admin/container-images');
      setImages(res.data || []);
      setError('');
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to load container images');
    }
  };

  useEffect(() => {
    load();
  }, []);

  const create = async () => {
    const imageRef = buildImageRef(form);
    const payload = {
      name: String(form.name || '').trim() || inferNameFromRef(imageRef),
      image_ref: imageRef,
    };
    if (!payload.name || !payload.image_ref) {
      setError('Name and image source are required');
      return;
    }
    try {
      await api.post('/admin/container-images', payload);
      setForm({ ...DEFAULT_FORM });
      setMessage('Container image added');
      setError('');
      load();
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to add container image');
    }
  };

  const startEdit = (row) => {
    setEditingId(row.id);
    setEditForm({ name: row.name || '', image_ref: row.image_ref || '' });
  };

  const saveEdit = async () => {
    try {
      await api.patch(`/admin/container-images/${editingId}`, editForm);
      setEditingId(null);
      setEditForm({ name: '', image_ref: '' });
      setMessage('Container image updated');
      setError('');
      load();
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to update container image');
    }
  };

  const remove = async (imageId) => {
    try {
      await api.delete(`/admin/container-images/${imageId}`);
      setMessage('Container image deleted');
      setError('');
      load();
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to delete container image');
    }
  };

  const prepull = async (imageId) => {
    try {
      const res = await api.post(`/admin/container-images/${imageId}/prepull`);
      setMessage(res.data?.detail || 'Pre-pull triggered');
      setError('');
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to trigger pre-pull');
    }
  };

  const scan = async (imageId) => {
    try {
      await api.post(`/admin/container-images/${imageId}/scan`);
      setMessage('Scan triggered');
      setError('');
      load();
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to scan image');
    }
  };

  const sourceImageRef = buildImageRef(form);
  const canCreate = Boolean(
    String(form.name || '').trim() ||
      (form.source === 'direct' ? String(form.image_ref || '').trim() : String(form.repository || '').trim())
  ) && Boolean(sourceImageRef);

  return (
    <div>
      <h2>Container Images</h2>
      {message && <div className="info">{message}</div>}
      {error && <div className="error">{error}</div>}
      <div className="grid">
        <div>
          <h3>Add container image</h3>
          <div className="form">
            <label>
              Name
              <input
                value={form.name}
                placeholder="nginx"
                onChange={(e) => setForm((prev) => ({ ...prev, name: e.target.value }))}
              />
            </label>
            <label>
              Source
              <select value={form.source} onChange={(e) => setForm((prev) => ({ ...prev, source: e.target.value }))}>
                <option value="docker_hub">Docker Hub</option>
                <option value="registry">Other OCI registry</option>
                <option value="direct">Direct image reference</option>
              </select>
            </label>
            {form.source === 'direct' ? (
              <label>
                Image reference
                <input
                  value={form.image_ref}
                  placeholder="ghcr.io/org/app:1.2.3"
                  onChange={(e) => setForm((prev) => ({ ...prev, image_ref: e.target.value }))}
                />
              </label>
            ) : (
              <>
                {form.source === 'registry' && (
                  <label>
                    Registry host
                    <input
                      value={form.registry}
                      placeholder="ghcr.io"
                      onChange={(e) => setForm((prev) => ({ ...prev, registry: e.target.value }))}
                    />
                  </label>
                )}
                <label>
                  Repository
                  <input
                    value={form.repository}
                    placeholder={form.source === 'docker_hub' ? 'library/nginx' : 'owner/app'}
                    onChange={(e) => setForm((prev) => ({ ...prev, repository: e.target.value }))}
                  />
                </label>
                <label>
                  Tag
                  <input
                    value={form.tag}
                    placeholder="latest"
                    onChange={(e) => setForm((prev) => ({ ...prev, tag: e.target.value }))}
                  />
                </label>
              </>
            )}
            <div className="muted small">Resolved reference: <code>{sourceImageRef || '-'}</code></div>
            <button onClick={create} disabled={!canCreate}>
              Add image
            </button>
            <p className="muted small">
              Supports Docker Hub, GHCR, Quay, ECR, GCR, ACR, and any OCI registry reachable by the cluster.
            </p>
          </div>
        </div>
        <div>
          <h3>Registered container images</h3>
          <div className="tile-grid">
            {images.length === 0 && <div className="muted">No container images yet.</div>}
            {images.map((img) => (
              <div key={img.id} className="tile template-tile">
                <div className="tile-header">
                  <h4>{img.name}</h4>
                </div>
                {editingId === img.id ? (
                  <div className="form">
                    <label>
                      Name
                      <input
                        value={editForm.name}
                        onChange={(e) => setEditForm((prev) => ({ ...prev, name: e.target.value }))}
                      />
                    </label>
                    <label>
                      Image reference
                      <input
                        value={editForm.image_ref}
                        onChange={(e) => setEditForm((prev) => ({ ...prev, image_ref: e.target.value }))}
                      />
                    </label>
                    <div className="actions">
                      <button className="ghost" onClick={() => setEditingId(null)}>
                        Cancel
                      </button>
                      <button onClick={saveEdit} disabled={!editForm.name || !editForm.image_ref}>
                        Save
                      </button>
                    </div>
                  </div>
                ) : (
                  <>
                    <div className="muted small">{img.image_ref}</div>
                    <div className="muted small">
                      Scan: {img.last_scan_status || 'never'}
                      {img.last_scan_at ? ` (${new Date(img.last_scan_at).toLocaleString()})` : ''}
                    </div>
                    {img.last_scan_summary && <div className="muted small">{img.last_scan_summary}</div>}
                    <div className="actions">
                      <button className="ghost" onClick={() => scan(img.id)}>
                        Scan
                      </button>
                      <button className="ghost" onClick={() => prepull(img.id)}>
                        Pre-pull
                      </button>
                      <button className="ghost" onClick={() => startEdit(img)}>
                        Edit
                      </button>
                      <button className="danger" onClick={() => remove(img.id)}>
                        Delete
                      </button>
                    </div>
                  </>
                )}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
};

export default AdminContainerImages;
