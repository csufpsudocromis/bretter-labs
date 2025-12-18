import React, { useEffect, useState } from 'react';
import { api } from '../../api';

const fmtCpu = (m) => `${(m / 1000).toFixed(2)} cores`;
const fmtMem = (b) => `${(b / (1024 * 1024 * 1024)).toFixed(2)} GB`;

const Bar = ({ label, used, total, formatter }) => {
  const pct = total > 0 ? Math.min(100, (used / total) * 100) : 0;
  return (
    <div className="card" style={{ marginBottom: '1rem' }}>
      <h4>{label}</h4>
      <div className="specs">
        <span>
          Used: {formatter(used)} / {formatter(total)}
        </span>
      </div>
      <div className="bar">
        <div className="bar-fill" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
};

const AdminResources = () => {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');

  const load = async () => {
    setError('');
    try {
      const res = await api.get('/admin/resources');
      setData(res.data);
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to load resources');
    }
  };

  useEffect(() => {
    load();
  }, []);

  const cpuUsed = data ? data.requested.cpu_m : 0;
  const cpuTotal = data ? data.allocatable.cpu_m : 0;
  const memUsed = data ? data.requested.memory_bytes : 0;
  const memTotal = data ? data.allocatable.memory_bytes : 0;
  const diskUsed = data ? data.requested.disk_bytes : 0;
  const diskTotal = data ? data.allocatable.disk_bytes : 0;
  const nodes = data?.nodes || [];

  return (
    <div>
      <h2>Cluster Resources</h2>
      <p>Current allocatable vs requested across CPU, memory, and ephemeral storage.</p>
      {error && <div className="error">{error}</div>}
      {data && (
        <>
          <Bar label="CPU" used={cpuUsed} total={cpuTotal} formatter={fmtCpu} />
          <Bar label="Memory" used={memUsed} total={memTotal} formatter={fmtMem} />
          <Bar label="Disk (ephemeral)" used={diskUsed} total={diskTotal} formatter={fmtMem} />
          <div className="card">
            <h3>Cluster Nodes</h3>
            <div className="tile-grid">
              {nodes.length === 0 && <div className="muted">No nodes.</div>}
              {nodes.map((n) => (
                <div key={n.name} className="tile template-tile">
                  <div className="tile-header">
                    <h4>{n.name}</h4>
                  </div>
                  <div className="specs">
                    <span>IP: {n.ip || 'n/a'}</span>
                    {n.usage && (
                      <>
                        <span>CPU: {fmtCpu(n.usage.cpu_m)} / {fmtCpu(n.capacity_cpu_m || 0)}</span>
                        <span>RAM: {fmtMem(n.usage.mem_bytes)} / {fmtMem(n.capacity_mem_bytes || 0)}</span>
                        <span>Disk: {fmtMem(n.usage.disk_bytes)} / {fmtMem(n.capacity_disk_bytes || 0)}</span>
                      </>
                    )}
                  </div>
                  {n.taints && n.taints.length > 0 ? (
                    <div className="muted small">Taints: {n.taints.join(', ')}</div>
                  ) : (
                    <div className="muted small">Taints: none</div>
                  )}
                </div>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  );
};

export default AdminResources;
