import React, { useEffect, useState } from 'react';
import { api } from '../../api';

const AdminUsers = () => {
  const [users, setUsers] = useState([]);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [role, setRole] = useState('user');
  const [editingUser, setEditingUser] = useState(null);
  const [editPassword, setEditPassword] = useState('');
  const [editUsername, setEditUsername] = useState('');
  const [editRole, setEditRole] = useState('user');
  const [message, setMessage] = useState('');

  const load = async () => {
    try {
      const res = await api.get('/admin/users');
      setUsers(res.data);
    } catch (err) {
      setMessage(err.response?.data?.detail || 'Failed to load users');
    }
  };

  useEffect(() => {
    load();
  }, []);

  const create = async () => {
    try {
      await api.post('/admin/users', { username, password, is_admin: role === 'admin' });
      setUsername('');
      setPassword('');
      setRole('user');
      setMessage('User created');
      load();
    } catch (err) {
      setMessage(err.response?.data?.detail || 'Failed to create user');
    }
  };

  const selectUser = (user) => {
    setEditingUser(user.username);
    setEditUsername(user.username);
    setEditPassword('');
    setEditRole(user.is_admin ? 'admin' : 'user');
    setMessage('');
  };

  const saveUser = async () => {
    try {
      await api.patch(`/admin/users/${editingUser}`, {
        username: editUsername,
        password: editPassword || undefined,
        is_admin: editRole === 'admin',
      });
      setMessage('User updated');
      setEditingUser(null);
      setEditPassword('');
      load();
    } catch (err) {
      setMessage(err.response?.data?.detail || 'Failed to update user');
    }
  };

  return (
    <div>
      <h2>Users</h2>
      {message && <div className="info">{message}</div>}
      <div className="grid">
        <div>
          <h3>Create user</h3>
          <div className="form">
            <label>
              Username
              <input value={username} onChange={(e) => setUsername(e.target.value)} />
            </label>
            <label>
              Role
              <select value={role} onChange={(e) => setRole(e.target.value)}>
                <option value="user">User</option>
                <option value="admin">Admin</option>
              </select>
            </label>
            <label>
              Password
              <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
            </label>
            <button type="button" onClick={create} disabled={!username || !password}>
              Create
            </button>
          </div>
        </div>
        <div>
          <h3>Existing users</h3>
          <div className="tile-grid">
            {users.length === 0 && <div className="muted">No users yet.</div>}
            {users.map((u) => (
              <button type="button" key={u.username} className="tile tile-button" onClick={() => selectUser(u)}>
                <div className="tile-header">
                  <h4>{u.username}</h4>
                  {u.is_admin && <span className="badge">admin</span>}
                </div>
              </button>
            ))}
          </div>
          {editingUser && (
            <div className="card" style={{ marginTop: '1rem' }}>
              <h4>Edit user</h4>
              <div className="form">
                <label>
                  Username
                  <input value={editUsername} onChange={(e) => setEditUsername(e.target.value)} />
                </label>
                <label>
                  Role
                  <select value={editRole} onChange={(e) => setEditRole(e.target.value)}>
                    <option value="user">User</option>
                    <option value="admin">Admin</option>
                  </select>
                </label>
                <label>
                  Password (leave blank to keep)
                  <input type="password" value={editPassword} onChange={(e) => setEditPassword(e.target.value)} />
                </label>
                <div className="actions">
                  <button type="button" className="ghost" onClick={() => setEditingUser(null)}>
                    Cancel
                  </button>
                  <button type="button" onClick={saveUser} disabled={!editUsername}>
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

export default AdminUsers;
