import React from "react";
import { Link } from "react-router-dom";

const AdminSettingsLanding = () => (
  <div>
    <h2>Settings</h2>
    <p>Select a settings section.</p>
    <div className="tiles">
      <Link to="/admin/users" className="tile">
        <h3>User Permissions</h3>
        <p>Create users and assign role-based permissions.</p>
      </Link>
      <Link to="/admin/settings/appearance" className="tile">
        <h3>Appearance</h3>
        <p>Title, tagline, colors, and background image.</p>
      </Link>
      <Link to="/admin/settings/storage" className="tile">
        <h3>Storage Options</h3>
        <p>Configure image storage and run readiness checks.</p>
      </Link>
      <Link to="/admin/settings/runtime" className="tile">
        <h3>Runtime Settings</h3>
        <p>View backend/runtime configuration and update global limits.</p>
      </Link>
      <Link to="/admin/settings/namespaces" className="tile">
        <h3>Namespaces</h3>
        <p>Add/remove managed namespaces and tune security, resources, and launch quota caps.</p>
      </Link>
      <Link to="/admin/settings/sso" className="tile">
        <h3>OIDC / SSO</h3>
        <p>Configure OIDC login, role mapping, and user provisioning behavior.</p>
      </Link>
      <Link to="/admin/settings/ldap" className="tile">
        <h3>LDAP</h3>
        <p>Enable/configure LDAP authentication and search settings.</p>
      </Link>
    </div>
  </div>
);

export default AdminSettingsLanding;
