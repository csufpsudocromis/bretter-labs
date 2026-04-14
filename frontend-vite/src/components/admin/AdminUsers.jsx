import React, { useEffect, useMemo, useState } from "react";
import { api } from "../../api";

const FALLBACK_PERMISSION_CATALOG = [
  "admin.access",
  "admin.users.read",
  "admin.users.write",
  "admin.images.read",
  "admin.images.write",
  "admin.templates.read",
  "admin.templates.write",
  "admin.operations.read",
  "admin.operations.write",
  "admin.settings.read",
  "admin.settings.write",
];

const FALLBACK_ROLE_CATALOG = [
  {
    role: "user",
    label: "User",
    description: "Can only access the user launch experience (VMs/containers).",
    permissions: [],
    editable: true,
    deletable: false,
  },
  {
    role: "lab_admin",
    label: "Lab Admin",
    description: "Can manage images/templates and operate running labs.",
    permissions: [
      "admin.images.read",
      "admin.images.write",
      "admin.templates.read",
      "admin.templates.write",
      "admin.operations.read",
      "admin.operations.write",
    ],
    editable: true,
    deletable: false,
  },
  {
    role: "namespace_admin",
    label: "Namespace Admin",
    description: "Can fully manage namespace-scoped operations and settings.",
    permissions: [
      "admin.users.read",
      "admin.users.write",
      "admin.images.read",
      "admin.images.write",
      "admin.templates.read",
      "admin.templates.write",
      "admin.operations.read",
      "admin.operations.write",
      "admin.settings.read",
      "admin.settings.write",
    ],
    editable: true,
    deletable: false,
  },
  {
    role: "platform_admin",
    label: "Platform Admin",
    description: "Full platform-wide administrative access.",
    permissions: ["*"],
    editable: false,
    deletable: false,
  },
];

const ROLE_SORT = {
  user: 0,
  lab_admin: 1,
  namespace_admin: 2,
  platform_admin: 3,
};

const normalizeRoleCatalog = (items) => {
  if (!Array.isArray(items) || items.length === 0) return FALLBACK_ROLE_CATALOG;
  return items
    .map((item) => ({
      role: String(item?.role || "")
        .trim()
        .toLowerCase(),
      label: String(item?.label || item?.role || "").trim() || "Unknown Role",
      description: String(item?.description || "").trim(),
      permissions: Array.isArray(item?.permissions)
        ? item.permissions.map((p) => String(p || "").trim()).filter(Boolean)
        : [],
      editable: Boolean(item?.editable),
      deletable: Boolean(item?.deletable),
    }))
    .filter((item) => item.role)
    .sort((a, b) => {
      const left = ROLE_SORT[a.role] ?? 999;
      const right = ROLE_SORT[b.role] ?? 999;
      if (left !== right) return left - right;
      return a.role.localeCompare(b.role);
    });
};

const roleSupportsNamespaceScopes = (role) =>
  String(role || "")
    .trim()
    .toLowerCase() !== "platform_admin";

const hasEffectivePermission = (permissions, permission) => {
  if (!Array.isArray(permissions)) return false;
  const normalized = permissions
    .map((value) =>
      String(value || "")
        .trim()
        .toLowerCase()
    )
    .filter(Boolean);
  if (normalized.includes("*")) return true;
  if (normalized.includes(permission)) return true;
  return normalized.some((entry) => entry.endsWith(".*") && permission.startsWith(entry.slice(0, -1)));
};

const PERMISSION_MATRIX = [
  { label: "Admin console", read: "admin.access", write: null },
  { label: "Users", read: "admin.users.read", write: "admin.users.write" },
  { label: "VM/Container images", read: "admin.images.read", write: "admin.images.write" },
  { label: "Templates", read: "admin.templates.read", write: "admin.templates.write" },
  { label: "Operations", read: "admin.operations.read", write: "admin.operations.write" },
  { label: "Settings", read: "admin.settings.read", write: "admin.settings.write" },
];

const AdminUsers = () => {
  const [users, setUsers] = useState([]);
  const [roleCatalog, setRoleCatalog] = useState(FALLBACK_ROLE_CATALOG);
  const [permissionCatalog, setPermissionCatalog] = useState(FALLBACK_PERMISSION_CATALOG);
  const [availableNamespaces, setAvailableNamespaces] = useState(["labs"]);
  const [roleDrafts, setRoleDrafts] = useState({});
  const [newRoleId, setNewRoleId] = useState("");
  const [newRoleLabel, setNewRoleLabel] = useState("");
  const [newRoleDescription, setNewRoleDescription] = useState("");
  const [newRolePermissions, setNewRolePermissions] = useState([]);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState("user");
  const [namespaceScopes, setNamespaceScopes] = useState([]);
  const [editingUser, setEditingUser] = useState(null);
  const [editPassword, setEditPassword] = useState("");
  const [editUsername, setEditUsername] = useState("");
  const [editRole, setEditRole] = useState("user");
  const [editNamespaceScopes, setEditNamespaceScopes] = useState([]);
  const [message, setMessage] = useState("");

  const roleMetaMap = useMemo(() => {
    const map = new Map();
    for (const item of roleCatalog) {
      map.set(item.role, item);
    }
    return map;
  }, [roleCatalog]);

  const initRoleDrafts = (catalog) => {
    const next = {};
    for (const roleEntry of catalog) {
      next[roleEntry.role] = {
        label: roleEntry.label,
        description: roleEntry.description,
        permissions: [...roleEntry.permissions].sort(),
      };
    }
    setRoleDrafts(next);
  };

  const roleLabel = (value) => {
    const key = String(value || "")
      .trim()
      .toLowerCase();
    return roleMetaMap.get(key)?.label || key || "Unknown";
  };

  const loadUsers = async () => {
    try {
      const res = await api.get("/admin/users");
      setUsers(Array.isArray(res.data) ? res.data : []);
    } catch (err) {
      setMessage(err.response?.data?.detail || "Failed to load users");
    }
  };

  const loadRoleCatalog = async () => {
    try {
      const res = await api.get("/admin/settings/roles");
      const normalizedRoles = normalizeRoleCatalog(res.data?.roles);
      const fetchedPermissions = Array.isArray(res.data?.permission_catalog)
        ? res.data.permission_catalog.map((p) => String(p || "").trim()).filter(Boolean)
        : [];
      setRoleCatalog(normalizedRoles);
      setPermissionCatalog(fetchedPermissions.length > 0 ? fetchedPermissions : FALLBACK_PERMISSION_CATALOG);
      initRoleDrafts(normalizedRoles);
    } catch (_err) {
      try {
        const fallbackRes = await api.get("/admin/users/roles");
        const normalizedRoles = normalizeRoleCatalog(fallbackRes.data);
        setRoleCatalog(normalizedRoles);
        setPermissionCatalog(FALLBACK_PERMISSION_CATALOG);
        initRoleDrafts(normalizedRoles);
      } catch (__err) {
        setRoleCatalog(FALLBACK_ROLE_CATALOG);
        setPermissionCatalog(FALLBACK_PERMISSION_CATALOG);
        initRoleDrafts(FALLBACK_ROLE_CATALOG);
      }
    }
  };

  const loadNamespaceOptions = async () => {
    try {
      const res = await api.get("/admin/quota-namespaces");
      const options = Array.isArray(res.data)
        ? [
            ...new Set(
              res.data
                .map((entry) =>
                  String(entry || "")
                    .trim()
                    .toLowerCase()
                )
                .filter(Boolean)
            ),
          ]
        : [];
      setAvailableNamespaces(options.length > 0 ? options : ["labs"]);
    } catch (_err) {
      setAvailableNamespaces(["labs"]);
    }
  };

  useEffect(() => {
    loadUsers();
    loadRoleCatalog();
    loadNamespaceOptions();
  }, []);

  const create = async () => {
    const selectedScopes = roleSupportsNamespaceScopes(role) ? namespaceScopes : [];
    try {
      await api.post("/admin/users", {
        username,
        password,
        role,
        is_admin: role !== "user",
        namespace_scopes: selectedScopes,
      });
      setUsername("");
      setPassword("");
      setRole("user");
      setNamespaceScopes([]);
      setMessage("User created");
      loadUsers();
    } catch (err) {
      setMessage(err.response?.data?.detail || "Failed to create user");
    }
  };

  const selectUser = (user) => {
    const selectedRole = String(user.role || (user.is_admin ? "platform_admin" : "user"))
      .trim()
      .toLowerCase();
    setEditingUser(user.username);
    setEditUsername(user.username);
    setEditPassword("");
    setEditRole(selectedRole || "user");
    setEditNamespaceScopes(
      Array.isArray(user.namespace_scopes)
        ? [
            ...new Set(
              user.namespace_scopes
                .map((entry) =>
                  String(entry || "")
                    .trim()
                    .toLowerCase()
                )
                .filter(Boolean)
            ),
          ]
        : []
    );
    setMessage("");
  };

  const saveUser = async () => {
    const selectedScopes = roleSupportsNamespaceScopes(editRole) ? editNamespaceScopes : [];
    try {
      await api.patch(`/admin/users/${editingUser}`, {
        username: editUsername,
        password: editPassword || undefined,
        role: editRole,
        is_admin: editRole !== "user",
        namespace_scopes: selectedScopes,
      });
      setMessage("User updated");
      setEditingUser(null);
      setEditPassword("");
      setEditNamespaceScopes([]);
      loadUsers();
    } catch (err) {
      setMessage(err.response?.data?.detail || "Failed to update user");
    }
  };

  const deleteUser = async () => {
    const targetUsername = String(editingUser || "").trim();
    if (!targetUsername) return;
    if (!window.confirm(`Delete user "${targetUsername}"? This cannot be undone.`)) return;
    try {
      await api.delete(`/admin/users/${encodeURIComponent(targetUsername)}`);
      setMessage(`User "${targetUsername}" deleted`);
      setEditingUser(null);
      setEditUsername("");
      setEditPassword("");
      setEditRole("user");
      setEditNamespaceScopes([]);
      loadUsers();
    } catch (err) {
      setMessage(err.response?.data?.detail || "Failed to delete user");
    }
  };

  const togglePermission = (permissions, permission) => {
    if (permissions.includes(permission)) {
      return permissions.filter((entry) => entry !== permission);
    }
    return [...permissions, permission].sort();
  };

  const updateRoleDraft = (roleId, patch) => {
    setRoleDrafts((prev) => ({
      ...prev,
      [roleId]: {
        ...(prev[roleId] || { label: "", description: "", permissions: [] }),
        ...patch,
      },
    }));
  };

  const saveRole = async (roleId) => {
    const draft = roleDrafts[roleId];
    if (!draft) return;
    try {
      await api.patch(`/admin/settings/roles/${roleId}`, {
        label: draft.label,
        description: draft.description,
        permissions: draft.permissions,
      });
      setMessage(`Role "${roleId}" updated`);
      await loadRoleCatalog();
    } catch (err) {
      setMessage(err.response?.data?.detail || `Failed to update role "${roleId}"`);
    }
  };

  const createRole = async () => {
    const roleId = String(newRoleId || "")
      .trim()
      .toLowerCase();
    if (!roleId) return;
    try {
      await api.post("/admin/settings/roles", {
        role: roleId,
        label: newRoleLabel,
        description: newRoleDescription,
        permissions: newRolePermissions,
      });
      setMessage(`Role "${roleId}" created`);
      setNewRoleId("");
      setNewRoleLabel("");
      setNewRoleDescription("");
      setNewRolePermissions([]);
      await loadRoleCatalog();
    } catch (err) {
      setMessage(err.response?.data?.detail || "Failed to create role");
    }
  };

  const deleteRole = async (roleId) => {
    if (!window.confirm(`Delete role "${roleId}"?`)) return;
    try {
      await api.delete(`/admin/settings/roles/${roleId}`);
      setMessage(`Role "${roleId}" deleted`);
      await loadRoleCatalog();
      if (role === roleId) setRole("user");
      if (editRole === roleId) setEditRole("user");
    } catch (err) {
      setMessage(err.response?.data?.detail || `Failed to delete role "${roleId}"`);
    }
  };

  const selectedRoleMeta = roleMetaMap.get(editRole) || roleMetaMap.get("user") || FALLBACK_ROLE_CATALOG[0];

  const toggleNamespace = (setter, values, namespace) => {
    if (values.includes(namespace)) {
      setter(values.filter((entry) => entry !== namespace));
      return;
    }
    setter([...values, namespace].sort());
  };

  return (
    <div>
      <h2>Users &amp; Permissions</h2>
      <p className="muted small">
        Assign one role per user. Permissions are role-based and applied immediately after save.
      </p>
      {message && <div className="info">{message}</div>}
      <div className="card admin-users-role-editor" style={{ marginBottom: "1rem" }}>
        <h3>Role editor</h3>
        <p className="muted small">Edit built-in roles, create new roles, and toggle permissions with checkboxes.</p>
        <div className="tile-grid">
          {roleCatalog.map((entry) => (
            <div key={entry.role} className="tile admin-users-role-tile">
              {(() => {
                const effectivePermissions = roleDrafts[entry.role]?.permissions || entry.permissions;
                const namespacePolicy = hasEffectivePermission(effectivePermissions, "*")
                  ? "All namespaces (platform-wide)"
                  : roleSupportsNamespaceScopes(entry.role)
                    ? "Scoped by each user's Allowed Namespaces selection"
                    : "No namespace-scoped admin access";
                return (
                  <>
                    <div className="tile-header">
                      <h4>{entry.label}</h4>
                      <span className="badge">{entry.role}</span>
                    </div>
                    {entry.description && <div className="muted small">{entry.description}</div>}
                    <div className="form">
                      <label>
                        Label
                        <input
                          value={roleDrafts[entry.role]?.label ?? entry.label}
                          onChange={(e) => updateRoleDraft(entry.role, { label: e.target.value })}
                          disabled={!entry.editable}
                        />
                      </label>
                      <label>
                        Description
                        <input
                          value={roleDrafts[entry.role]?.description ?? entry.description}
                          onChange={(e) => updateRoleDraft(entry.role, { description: e.target.value })}
                          disabled={!entry.editable}
                        />
                      </label>
                      <div className="muted small">Permissions</div>
                      <div style={{ display: "flex", flexDirection: "column", gap: "0.25rem", marginBottom: "0.5rem" }}>
                        {permissionCatalog.map((permission) => (
                          <label key={`${entry.role}-${permission}`} className="permission-row">
                            <input
                              type="checkbox"
                              checked={(roleDrafts[entry.role]?.permissions || entry.permissions).includes(permission)}
                              disabled={!entry.editable}
                              onChange={() =>
                                updateRoleDraft(entry.role, {
                                  permissions: togglePermission(
                                    roleDrafts[entry.role]?.permissions || entry.permissions,
                                    permission
                                  ),
                                })
                              }
                            />
                            <span className="permission-id">{permission}</span>
                          </label>
                        ))}
                      </div>
                      <div className="permission-matrix">
                        <div className="muted small">
                          <strong>Effective access preview</strong>
                        </div>
                        <div className="permission-matrix-grid">
                          <div className="muted small">Area</div>
                          <div className="muted small">Read</div>
                          <div className="muted small">Write</div>
                          {PERMISSION_MATRIX.map((row) => (
                            <React.Fragment key={`${entry.role}-${row.label}`}>
                              <div className="small">{row.label}</div>
                              <div className="small">
                                {hasEffectivePermission(effectivePermissions, row.read) ? "Yes" : "No"}
                              </div>
                              <div className="small">
                                {row.write
                                  ? hasEffectivePermission(effectivePermissions, row.write)
                                    ? "Yes"
                                    : "No"
                                  : "N/A"}
                              </div>
                            </React.Fragment>
                          ))}
                        </div>
                        <div className="muted small">Namespace scope: {namespacePolicy}</div>
                      </div>
                      <div className="actions">
                        <button type="button" onClick={() => saveRole(entry.role)} disabled={!entry.editable}>
                          Save Role
                        </button>
                        {entry.deletable && (
                          <button type="button" className="ghost" onClick={() => deleteRole(entry.role)}>
                            Delete Role
                          </button>
                        )}
                      </div>
                      {!entry.editable && <div className="muted small">This role is fixed.</div>}
                    </div>
                  </>
                );
              })()}
            </div>
          ))}
        </div>
        <div className="card" style={{ marginTop: "1rem" }}>
          <h4>Create role</h4>
          <div className="form">
            <label>
              Role ID
              <input
                value={newRoleId}
                onChange={(e) => setNewRoleId(e.target.value.toLowerCase())}
                placeholder="example: support_admin"
              />
            </label>
            <label>
              Label
              <input value={newRoleLabel} onChange={(e) => setNewRoleLabel(e.target.value)} />
            </label>
            <label>
              Description
              <input value={newRoleDescription} onChange={(e) => setNewRoleDescription(e.target.value)} />
            </label>
            <div className="muted small">Permissions</div>
            <div style={{ display: "flex", flexDirection: "column", gap: "0.25rem" }}>
              {permissionCatalog.map((permission) => (
                <label key={`new-role-${permission}`} className="permission-row">
                  <input
                    type="checkbox"
                    checked={newRolePermissions.includes(permission)}
                    onChange={() => setNewRolePermissions((prev) => togglePermission(prev, permission))}
                  />
                  <span className="permission-id">{permission}</span>
                </label>
              ))}
            </div>
            <div className="permission-matrix">
              <div className="muted small">
                <strong>Effective access preview</strong>
              </div>
              <div className="permission-matrix-grid">
                <div className="muted small">Area</div>
                <div className="muted small">Read</div>
                <div className="muted small">Write</div>
                {PERMISSION_MATRIX.map((row) => (
                  <React.Fragment key={`create-${row.label}`}>
                    <div className="small">{row.label}</div>
                    <div className="small">{hasEffectivePermission(newRolePermissions, row.read) ? "Yes" : "No"}</div>
                    <div className="small">
                      {row.write ? (hasEffectivePermission(newRolePermissions, row.write) ? "Yes" : "No") : "N/A"}
                    </div>
                  </React.Fragment>
                ))}
              </div>
              <div className="muted small">
                Namespace scope:{" "}
                {hasEffectivePermission(newRolePermissions, "*")
                  ? "All namespaces (platform-wide)"
                  : "Scoped by each user's Allowed Namespaces selection"}
              </div>
            </div>
            <div className="actions">
              <button type="button" onClick={createRole} disabled={!newRoleId.trim()}>
                Create Role
              </button>
            </div>
          </div>
        </div>
      </div>
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
                {roleCatalog.map((option) => (
                  <option key={option.role} value={option.role}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
            {roleSupportsNamespaceScopes(role) && (
              <div>
                <div className="muted small">Allowed namespaces</div>
                <div className="namespace-scope-list">
                  {availableNamespaces.map((namespace) => (
                    <label key={`create-ns-${namespace}`} className="permission-row">
                      <input
                        type="checkbox"
                        checked={namespaceScopes.includes(namespace)}
                        onChange={() => toggleNamespace(setNamespaceScopes, namespaceScopes, namespace)}
                      />
                      <span className="permission-id">{namespace}</span>
                    </label>
                  ))}
                </div>
              </div>
            )}
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
                {Array.isArray(u.namespace_scopes) && u.namespace_scopes.length > 0 && (
                  <div className="muted small">Namespaces: {u.namespace_scopes.join(", ")}</div>
                )}
              </button>
            ))}
          </div>
          {editingUser && (
            <div className="card" style={{ marginTop: "1rem" }}>
              <h4>Edit user permissions</h4>
              <div className="form">
                <label>
                  Username
                  <input value={editUsername} onChange={(e) => setEditUsername(e.target.value)} />
                </label>
                <label>
                  Role
                  <select value={editRole} onChange={(e) => setEditRole(e.target.value)}>
                    {roleCatalog.map((option) => (
                      <option key={option.role} value={option.role}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </label>
                {roleSupportsNamespaceScopes(editRole) && (
                  <div>
                    <div className="muted small">Allowed namespaces</div>
                    <div className="namespace-scope-list">
                      {availableNamespaces.map((namespace) => (
                        <label key={`edit-ns-${namespace}`} className="permission-row">
                          <input
                            type="checkbox"
                            checked={editNamespaceScopes.includes(namespace)}
                            onChange={() => toggleNamespace(setEditNamespaceScopes, editNamespaceScopes, namespace)}
                          />
                          <span className="permission-id">{namespace}</span>
                        </label>
                      ))}
                    </div>
                  </div>
                )}
                <div className="muted small">
                  <strong>Effective permissions:</strong>{" "}
                  {selectedRoleMeta.permissions.length > 0 ? selectedRoleMeta.permissions.join(", ") : "none"}
                </div>
                <label>
                  Password (leave blank to keep)
                  <input type="password" value={editPassword} onChange={(e) => setEditPassword(e.target.value)} />
                </label>
                <div className="actions">
                  <button type="button" className="ghost" onClick={() => setEditingUser(null)}>
                    Cancel
                  </button>
                  <button type="button" className="danger" onClick={deleteUser}>
                    Delete User
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
