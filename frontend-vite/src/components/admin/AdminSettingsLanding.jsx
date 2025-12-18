import React from 'react';
import { Link } from 'react-router-dom';

const AdminSettingsLanding = () => (
  <div>
    <h2>Settings</h2>
    <p>Select a settings section.</p>
    <div className="tiles">
      <Link to="/admin/settings/appearance" className="tile">
        <h3>Appearance</h3>
        <p>Title, tagline, colors, and background image.</p>
      </Link>
      <Link to="/admin/settings/storage" className="tile">
        <h3>Storage Options</h3>
        <p>Configure storage root and image PVC.</p>
      </Link>
      <Link to="/admin/settings/runtime" className="tile">
        <h3>Runtime Settings</h3>
        <p>View backend/runtime configuration (read-only).</p>
      </Link>
      <Link to="/admin/settings/sso" className="tile">
        <h3>Single Sign-On</h3>
        <p>Enable/configure SSO for this environment.</p>
      </Link>
    </div>
  </div>
);

export default AdminSettingsLanding;
