import React, { useEffect, useState } from "react";
import { api } from "../../api";

const ROLE_OPTIONS = [
  { value: "user", label: "User" },
  { value: "viewer", label: "Viewer (read-only)" },
  { value: "image_manager", label: "Image Manager" },
  { value: "template_manager", label: "Template Manager" },
  { value: "lab_operator", label: "Lab Operator" },
  { value: "platform_admin", label: "Platform Admin" },
];

const roleLabel = (value) => ROLE_OPTIONS.find((item) => item.value === value)?.label || value;

const AdminUsers = () => {
  const [users, setUsers] = useState([]);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState("user");
  const [editingUser, setEditingUser] = useState(null);
  const [editPassword, setEditPassword] = useState("");
  const [editUsername, setEditUsername] = useState("");
  const [editRole, setEditRole] = useState("user");
  const [message, setMessage] = useState("");

  const load = async () => {
    try {
      const res = await api.get("/admin/users");
      setUsers(res.data);
    } catch (err) {
      setMessage(err.response?.data?.detail || "Failed to load users");
    }
  };

  useEffect(() => {
    load();
  }, []);

  const create = async () => {
    try {
      await api.post("/admin/users", {
        username,
        password,
        role,
        is_admin: role !== "user",
      });
      setUsername("");
      setPassword("");
      setRole("user");
      setMessage("User created");
      load();
    } catch (err) {
      setMessage(err.response?.data?.detail || "Failed to create user");
    }
  };

  const selectUser = (user) => {
    setEditingUser(user.username);
    setEditUsername(user.username);
    setEditPassword("");
    setEditRole(user.role || (user.is_admin ? "platform_admin" : "user"));
    setMessage("");
  };

  const saveUser = async () => {
    try {
      await api.patch(`/admin/users/${editingUser}`, {
        username: editUsername,
        password: editPassword || undefined,
        role: editRole,
        is_admin: editRole !== "user",
      });
      setMessage("User updated");
      setEditingUser(null);
      setEditPassword("");
      load();
    } catch (err) {
      setMessage(err.response?.data?.detail || "Failed to update user");
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
                {ROLE_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
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
                  <span className="badge">{roleLabel(u.role || (u.is_admin ? "platform_admin" : "user"))}</span>
                </div>
              </button>
            ))}
          </div>
          {editingUser && (
            <div className="card" style={{ marginTop: "1rem" }}>
              <h4>Edit user</h4>
              <div className="form">
                <label>
                  Username
                  <input value={editUsername} onChange={(e) => setEditUsername(e.target.value)} />
                </label>
                <label>
                  Role
                  <select value={editRole} onChange={(e) => setEditRole(e.target.value)}>
                    {ROLE_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
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
