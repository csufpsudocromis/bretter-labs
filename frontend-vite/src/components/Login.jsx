import React, { useEffect, useState } from 'react';
import { api } from '../api';

const Login = ({ onLogin, user }) => {
  const [username, setUsername] = useState('admin');
  const [password, setPassword] = useState('admin');
  const [error, setError] = useState('');
  const [sso, setSso] = useState({ enabled: false, authorize_url: '' });

  useEffect(() => {
    const load = async () => {
      try {
        const res = await api.get('/user/settings/sso');
        setSso({
          enabled: res.data?.sso_enabled,
          authorize_url: res.data?.sso_authorize_url || '',
        });
      } catch (err) {
        // ignore
      }
    };
    load();
  }, []);

  const handleSubmit = (e) => {
    e.preventDefault();
    onLogin(username, password);
  };

  return (
    <div>
      <h2>Login</h2>
      {user && (
        <p>
          Logged in as <strong>{user.username}</strong> {user.is_admin ? '(admin)' : ''}
        </p>
      )}
      <form onSubmit={handleSubmit} className="form">
        <label>
          Username
          <input value={username} onChange={(e) => setUsername(e.target.value)} />
        </label>
        <label>
          Password
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </label>
        <button type="submit">Sign In</button>
        {sso.enabled && (
          <button
            type="button"
            className="ghost"
            onClick={() => {
              if (sso.authorize_url) {
                window.location.href = sso.authorize_url;
              } else {
                setError('SSO is enabled but not configured.');
              }
            }}
          >
            Sign in with SSO
          </button>
        )}
        {error && <div className="error">{error}</div>}
      </form>
    </div>
  );
};

export default Login;
