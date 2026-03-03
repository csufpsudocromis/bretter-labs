import React, { useEffect, useState } from 'react';
import { api } from '../../api';

const AdminContainerImages = () => {
  const [images, setImages] = useState([]);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [form, setForm] = useState({ name: '', image_ref: '' });
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
    try {
      await api.post('/admin/container-images', {
        name: form.name,
        image_ref: form.image_ref,
      });
      setForm({ name: '', image_ref: '' });
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

  return (
    <div>
      <h2>Container Images</h2>
      {message && <div className="info">{message}</div>}
      {error && <div className="error">{error}</div>}
      <div className="grid">
        <div>
          <h3>Add Docker Hub image</h3>
          <div className="form">
            <label>
              Name
              <input
                value={form.name}
                placeholder="Nginx"
                onChange={(e) => setForm((prev) => ({ ...prev, name: e.target.value }))}
              />
            </label>
            <label>
              Docker image reference
              <input
                value={form.image_ref}
                placeholder="nginx:latest"
                onChange={(e) => setForm((prev) => ({ ...prev, image_ref: e.target.value }))}
              />
            </label>
            <button onClick={create} disabled={!form.name || !form.image_ref}>
              Add image
            </button>
            <p className="muted small">Examples: `nginx:latest`, `ubuntu:24.04`, `library/alpine:3.20`.</p>
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
                    <div className="actions">
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
