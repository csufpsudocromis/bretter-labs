import React from "react";
import { Link } from "react-router-dom";

const tiles = [
  {
    to: "/admin/users",
    title: "User Permissions",
    description: "Create users and assign role-based permissions.",
    permission: "admin.users.read",
  },
  {
    to: "/admin/settings/appearance",
    title: "Appearance",
    description: "Title, tagline, colors, and background image.",
    permission: "admin.settings.read",
  },
  {
    to: "/admin/settings/storage",
    title: "Storage Options",
    description: "Configure image storage and run readiness checks.",
    permission: "admin.settings.read",
  },
  {
    to: "/admin/settings/runtime",
    title: "Runtime Settings",
    description: "View backend/runtime configuration and update global limits.",
    permission: "admin.settings.read",
  },
  {
    to: "/admin/settings/namespaces",
    title: "Namespaces",
    description: "Add/remove managed namespaces and tune security, resources, and launch quota caps.",
    permission: "admin.settings.read",
  },
  {
    to: "/admin/settings/sso",
    title: "OIDC / SSO",
    description: "Configure OIDC login, role mapping, and user provisioning behavior.",
    permission: "admin.settings.read",
  },
  {
    to: "/admin/settings/ldap",
    title: "LDAP",
    description: "Enable/configure LDAP authentication and search settings.",
    permission: "admin.settings.read",
  },
];

const hasPermission = (permissions, permission) => {
  if (!permission) return true;
  if (!Array.isArray(permissions)) return false;
  const normalized = permissions
    .map((entry) =>
      String(entry || "")
        .trim()
        .toLowerCase()
    )
    .filter(Boolean);
  return (
    normalized.includes("*") ||
    normalized.includes(
      String(permission || "")
        .trim()
        .toLowerCase()
    )
  );
};

const AdminSettingsLanding = ({ user }) => {
  const permissions = Array.isArray(user?.permissions) ? user.permissions : [];
  const visibleTiles = tiles.filter((tile) => hasPermission(permissions, tile.permission));
  return (
    <div>
      <h2>Settings</h2>
      <p>Select a settings section.</p>
      <div className="tiles">
        {visibleTiles.map((tile) => (
          <Link key={tile.to} to={tile.to} className="tile">
            <h3>{tile.title}</h3>
            <p>{tile.description}</p>
          </Link>
        ))}
      </div>
    </div>
  );
};

export default AdminSettingsLanding;
