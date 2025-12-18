import React, { useEffect, useState } from 'react';
import { api } from '../../api';

const AdminPods = () => {
  const [pods, setPods] = useState([]);
  const [message, setMessage] = useState('');

  const podName = (p) => `vm-${p.owner}-${p.id.slice(0, 8)}`;

  const load = async () => {
    try {
      const res = await api.get('/admin/pods');
      setPods(res.data);
    } catch (err) {
      setMessage(err.response?.data?.detail || 'Failed to load pods');
    }
  };

  useEffect(() => {
    load();
  }, []);

  const stop = async (id) => {
    try {
      await api.post(`/admin/pods/${id}/stop`);
      load();
    } catch (err) {
      setMessage(err.response?.data?.detail || 'Stop failed');
    }
  };

  const remove = async (id) => {
    try {
      await api.delete(`/admin/pods/${id}`);
      load();
    } catch (err) {
      setMessage(err.response?.data?.detail || 'Delete failed');
    }
  };

  return (
    <div>
      <h2>Pods</h2>
      {message && <div className="info">{message}</div>}
      <ul>
        {pods.map((p) => (
          <li key={p.id}>
            {podName(p)} – {p.status} – owner: {p.owner}{' '}
            <button onClick={() => stop(p.id)}>Stop</button>
            <button onClick={() => remove(p.id)}>Delete</button>
          </li>
        ))}
      </ul>
      {pods.length === 0 && <p>No running pods.</p>}
    </div>
  );
};

export default AdminPods;
