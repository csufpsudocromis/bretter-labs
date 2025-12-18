import React from 'react';
import { Link } from 'react-router-dom';

const tiles = [
  { title: 'Users', description: 'Add/remove users, reset passwords', to: '/admin/users' },
  { title: 'Templates', description: 'Create and enable VM templates', to: '/admin/templates' },
  { title: 'Images', description: 'Upload and manage VM images', to: '/admin/images' },
  { title: 'Pods', description: 'View/stop/destroy running pods', to: '/admin/pods' },
  { title: 'Resources', description: 'Cluster capacity vs requested usage', to: '/admin/resources' },
  { title: 'Settings', description: 'View runtime settings', to: '/admin/settings' },
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
