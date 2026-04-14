import React from "react";
import { Link } from "react-router-dom";

const tiles = [
  {
    title: "VM Templates",
    description: "Create and enable VM templates",
    to: "/admin/templates",
    permission: "admin.templates.read",
  },
  {
    title: "VM Images",
    description: "Upload and manage VM images",
    to: "/admin/images",
    permission: "admin.images.read",
  },
  {
    title: "Container Templates",
    description: "Create and enable container run templates",
    to: "/admin/container-templates",
    permission: "admin.templates.read",
  },
  {
    title: "Container Images",
    description: "Register container images",
    to: "/admin/container-images",
    permission: "admin.images.read",
  },
  {
    title: "ISO Images",
    description: "Upload installer ISOs for scratch image creation",
    to: "/admin/iso-images",
    permission: "admin.images.read",
  },
  {
    title: "Pods",
    description: "View/stop/destroy running pods",
    to: "/admin/pods",
    permission: "admin.operations.read",
  },
  {
    title: "Operations",
    description: "Retry/cancel/cleanup failed upload and launch tasks",
    to: "/admin/operations",
    permission: "admin.operations.read",
  },
  {
    title: "Resources",
    description: "Cluster capacity vs requested usage",
    to: "/admin/resources",
    permission: "admin.operations.read",
  },
  {
    title: "Alerts and Errors",
    description: "View Alertmanager alerts and backend error logs",
    to: "/admin/alerts-errors",
    permission: "admin.operations.read",
  },
  {
    title: "Audit Events",
    description: "Review admin changes and operational mutations",
    to: "/admin/audit-events",
    permission: "admin.operations.read",
  },
  {
    title: "Settings",
    description: "Runtime, storage, SSO, and LDAP controls",
    to: "/admin/settings",
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

const AdminDashboard = ({ user }) => {
  const permissions = Array.isArray(user?.permissions) ? user.permissions : [];
  const visibleTiles = tiles.filter((tile) => hasPermission(permissions, tile.permission));

  return (
    <div>
      <h2>Admin</h2>
      <p>Choose a section to manage.</p>
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

export default AdminDashboard;
