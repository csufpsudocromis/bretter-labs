import React from "react";
import { Link } from "react-router-dom";

const tiles = [
  { title: "Users", description: "Add/remove users, reset passwords", to: "/admin/users" },
  { title: "VM Templates", description: "Create and enable VM templates", to: "/admin/templates" },
  { title: "VM Images", description: "Upload and manage VM images", to: "/admin/images" },
  { title: "Container Images", description: "Register container images", to: "/admin/container-images" },
  {
    title: "Container Templates",
    description: "Create and enable container run templates",
    to: "/admin/container-templates",
  },
  { title: "Pods", description: "View/stop/destroy running pods", to: "/admin/pods" },
  { title: "Resources", description: "Cluster capacity vs requested usage", to: "/admin/resources" },
  {
    title: "Alerts and Errors",
    description: "View Alertmanager alerts and backend error logs",
    to: "/admin/alerts-errors",
  },
  {
    title: "Audit Events",
    description: "Review admin changes and operational mutations",
    to: "/admin/audit-events",
  },
  {
    title: "Settings",
    description: "Runtime, storage, SSO, LDAP, and multi-cluster controls",
    to: "/admin/settings",
  },
  {
    title: "Multi-Cluster",
    description: "Placement policies, replication queue, and cluster routing controls",
    to: "/admin/settings/multi-cluster",
  },
];

const AdminDashboard = () => (
  <div>
    <h2>Admin</h2>
    <p>Choose a section to manage.</p>
    <div className="tiles">
      {tiles.map((tile) => (
        <Link key={tile.to} to={tile.to} className="tile">
          <h3>{tile.title}</h3>
          <p>{tile.description}</p>
        </Link>
      ))}
    </div>
  </div>
);

export default AdminDashboard;
