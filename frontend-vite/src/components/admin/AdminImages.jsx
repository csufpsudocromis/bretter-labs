import React, { useEffect, useState } from 'react';
import { api } from '../../api';

const AdminImages = () => {
  const [images, setImages] = useState([]);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [file, setFile] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [editId, setEditId] = useState(null);
  const [editName, setEditName] = useState('');
  const [editFilename, setEditFilename] = useState('');

  const load = async () => {
    try {
      const res = await api.get('/admin/images');
      setImages(res.data);
      setMessage('');
      setError('');
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to load images');
    }
  };

  useEffect(() => {
    load();
  }, []);

  const upload = async () => {
    if (!file) return;
    const formData = new FormData();
    formData.append('file', file);
    setUploading(true);
    setProgress(0);
    setMessage('');
    setError('');
    try {
      await api.post('/admin/images', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
        onUploadProgress: (evt) => {
          if (evt.total) {
            setProgress(Math.round((evt.loaded / evt.total) * 100));
          }
        },
      });
      setFile(null);
      setProgress(0);
      setMessage('Upload complete');
      load();
    } catch (err) {
      setError(err.response?.data?.detail || 'Upload failed');
    } finally {
      setUploading(false);
    }
  };

  const remove = async (id) => {
    try {
      await api.delete(`/admin/images/${id}`);
      setMessage('Deleted');
      load();
    } catch (err) {
      setError(err.response?.data?.detail || 'Delete failed');
    }
  };

  const startEdit = (img) => {
    setEditId(img.id);
    setEditName(img.name);
    setEditFilename(img.name);
  };

  const saveEdit = async () => {
    try {
      await api.patch(`/admin/images/${editId}`, { name: editName, filename: editFilename });
      setEditId(null);
      setMessage('Updated');
      load();
    } catch (err) {
      setError(err.response?.data?.detail || 'Update failed');
    }
  };

  const cancelEdit = () => {
    setEditId(null);
    setEditName('');
    setEditFilename('');
  };

  return (
    <div>
      <h2>Images</h2>
      {message && <div className="info">{message}</div>}
      {error && <div className="error">{error}</div>}
      <div className="grid">
        <div>
          <h3>Upload image</h3>
          <input type="file" onChange={(e) => setFile(e.target.files?.[0] || null)} />
          <button onClick={upload} disabled={!file || uploading}>
            {uploading ? `Uploading (${progress}%)` : 'Upload'}
          </button>
          {uploading && <p>Progress: {progress}%</p>}
          <p className="muted small">Allowed: .vhd, .qcow/.qcow2, .vdi. QCOW is auto-converted to raw.</p>
        </div>
        <div>
          <h3>Golden Images</h3>
          <div className="tile-grid">
            {images.length === 0 && <div className="muted">No images.</div>}
            {images.map((img) => (
              <div key={img.id} className="tile template-tile">
                <div className="tile-header">
                  <h4>{img.name}</h4>
                  <span className="muted small">{Math.round(img.size_bytes / (1024 * 1024))} MB</span>
                </div>
                {editId === img.id ? (
                  <div className="form">
                    <label>
                      Name
                      <input value={editName} onChange={(e) => setEditName(e.target.value)} />
                    </label>
                    <label>
                      Filename
                      <input value={editFilename} onChange={(e) => setEditFilename(e.target.value)} />
                    </label>
                    <div className="actions">
                      <button onClick={saveEdit}>Save</button>
                      <button className="ghost" onClick={cancelEdit}>
                        Cancel
                      </button>
                    </div>
                  </div>
                ) : (
                  <>
                    <div className="actions">
                      <button className="ghost" onClick={() => startEdit(img)}>
                        Rename
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

export default AdminImages;
