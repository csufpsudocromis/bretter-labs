import React, { useEffect, useState } from 'react';
import { api } from '../../api';

const AdminPods = () => {
  const [pods, setPods] = useState([]);
  const [containers, setContainers] = useState([]);
  const [message, setMessage] = useState('');

  const podName = (p) => `vm-${p.owner}-${p.id.slice(0, 8)}`;
  const containerPodName = (c) => c.pod_name || `ct-${c.owner}-${c.id.slice(0, 8)}`;

  const load = async () => {
    try {
      const [podRes, containerRes] = await Promise.all([api.get('/admin/pods'), api.get('/admin/containers')]);
      setPods(podRes.data || []);
      setContainers(containerRes.data || []);
      setMessage('');
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

  const stopContainer = async (id) => {
    try {
      await api.post(`/admin/containers/${id}/stop`);
      load();
    } catch (err) {
      setMessage(err.response?.data?.detail || 'Container stop failed');
    }
  };

  const removeContainer = async (id) => {
    try {
      await api.delete(`/admin/containers/${id}`);
      load();
    } catch (err) {
      setMessage(err.response?.data?.detail || 'Container delete failed');
    }
  };

  return (
    <div>
      <h2>Pods</h2>
      {message && <div className="info">{message}</div>}
      <h3>Virtual Machine Pods</h3>
      <ul>
        {pods.map((p) => (
          <li key={p.id}>
            {podName(p)} – {p.status} – owner: {p.owner}{' '}
            <button onClick={() => stop(p.id)}>Stop</button>
            <button onClick={() => remove(p.id)}>Delete</button>
          </li>
        ))}
      </ul>
      {pods.length === 0 && <p>No VM pods.</p>}
      <h3 style={{ marginTop: '1rem' }}>Container Pods</h3>
      <ul>
        {containers.map((c) => (
          <li key={c.id}>
            {containerPodName(c)} – {c.status} – owner: {c.owner}{' '}
            <button onClick={() => stopContainer(c.id)}>Stop</button>
            <button onClick={() => removeContainer(c.id)}>Delete</button>
          </li>
        ))}
      </ul>
      {containers.length === 0 && <p>No container pods.</p>}
    </div>
  );
};

export default AdminPods;
