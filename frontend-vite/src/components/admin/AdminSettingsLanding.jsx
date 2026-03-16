import React from "react";
import { Link } from "react-router-dom";

const AdminSettingsLanding = () => (
  <div>
    <h2>Settings</h2>
    <p>Select a settings section.</p>
    <div className="tiles">
      <Link to="/admin/scaling-quotas" className="tile">
        <h3>Scaling &amp; Quotas</h3>
        <p>Set namespace limits for max labs, CPU/RAM, storage, and idle caps.</p>
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
        <p>View backend/runtime configuration (read-only).</p>
      </Link>
      <Link to="/admin/settings/sso" className="tile">
        <h3>Single Sign-On</h3>
        <p>Enable/configure SSO for this environment.</p>
      </Link>
      <Link to="/admin/settings/ldap" className="tile">
        <h3>LDAP</h3>
        <p>Enable/configure LDAP authentication and search settings.</p>
      </Link>
    </div>
  </div>
);

export default AdminSettingsLanding;
