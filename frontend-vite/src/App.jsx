import React, { useEffect, useState } from 'react';
import { BrowserRouter, Link, Route, Routes, useNavigate } from 'react-router-dom';

import { api } from './api';
import Login from './components/Login.jsx';
import UserPanel from './components/UserPanel.jsx';
import AdminDashboard from './components/admin/AdminDashboard.jsx';
import AdminUsers from './components/admin/AdminUsers.jsx';
import AdminTemplates from './components/admin/AdminTemplates.jsx';
import AdminImages from './components/admin/AdminImages.jsx';
import AdminPods from './components/admin/AdminPods.jsx';
import AdminResources from './components/admin/AdminResources.jsx';
import AdminSettingsLanding from './components/admin/AdminSettingsLanding.jsx';
import AdminAppearanceSettings from './components/admin/AdminAppearanceSettings.jsx';
import AdminRuntimeSettings from './components/admin/AdminRuntimeSettings.jsx';
import AdminSSOSettings from './components/admin/AdminSSOSettings.jsx';
import AdminStorageSettings from './components/admin/AdminStorageSettings.jsx';

const AppShell = () => {
  const [token, setToken] = useState(null);
  const [user, setUser] = useState(null);
  const [error, setError] = useState(null);
  const [site, setSite] = useState({
    title: 'Bretter Labs',
    tagline: 'Run Virtual Labs and Software',
    theme_bg_color: '#f5f5f5',
    theme_text_color: '#111111',
    theme_button_color: '#2563eb',
    theme_button_text_color: '#ffffff',
    theme_bg_image: '',
    theme_tile_bg: '#f8fafc',
    theme_tile_border: '#e2e8f0',
    theme_tile_border_opacity: 1,
  });
  const navigate = useNavigate();

  useEffect(() => {
    const savedToken = localStorage.getItem('blabs_token');
    const savedUser = localStorage.getItem('blabs_user');
    if (savedToken) setToken(savedToken);
    if (savedUser) setUser(JSON.parse(savedUser));
  }, []);

  const onLogin = async (username, password) => {
    try {
      const res = await api.post('/auth/login', { username, password });
      setToken(res.data.token);
      setUser(res.data.user);
      localStorage.setItem('blabs_token', res.data.token);
      localStorage.setItem('blabs_user', JSON.stringify(res.data.user));
      setError(null);
      navigate('/');
    } catch (err) {
      setError(err.response?.data?.detail || 'Login failed');
      setToken(null);
      setUser(null);
      localStorage.removeItem('blabs_token');
      localStorage.removeItem('blabs_user');
    }
  };

  useEffect(() => {
    api.defaults.headers.common['Authorization'] = token ? `Bearer ${token}` : '';
    const loadSite = async () => {
      try {
        const res = await api.get('/user/settings/site');
        setSite({
          title: res.data.site_title,
          tagline: res.data.site_tagline,
          theme_bg_color: res.data.theme_bg_color,
          theme_text_color: res.data.theme_text_color,
          theme_button_color: res.data.theme_button_color,
          theme_button_text_color: res.data.theme_button_text_color,
          theme_bg_image: res.data.theme_bg_image,
          theme_tile_bg: res.data.theme_tile_bg,
          theme_tile_border: res.data.theme_tile_border,
          theme_tile_border_opacity: 1,
        });
      } catch (err) {
        setSite({
          title: 'Bretter Labs',
          tagline: 'Run Virtual Labs and Software',
          theme_bg_color: '#f5f5f5',
          theme_text_color: '#111111',
          theme_button_color: '#2563eb',
          theme_button_text_color: '#ffffff',
          theme_bg_image: '',
          theme_tile_bg: '#f8fafc',
          theme_tile_border: '#e2e8f0',
          theme_tile_border_opacity: 1,
        });
      }
    };
    if (token) {
      loadSite();
    }
  }, [token]);

  useEffect(() => {
    const root = document.documentElement;
    const clamp = (v) => (isNaN(v) ? 1 : Math.min(1, Math.max(0, v)));
    const toRgb = (hex, fallback) => {
      const clean = (hex || fallback).replace('#', '');
      if (clean.length === 6) {
        return [
          parseInt(clean.slice(0, 2), 16),
          parseInt(clean.slice(2, 4), 16),
          parseInt(clean.slice(4, 6), 16),
        ];
      }
      return [248, 250, 252];
    };
    const bgOpacity = 1;
    const borderOpacity = 1;
    const [br, bg, bb] = toRgb(site.theme_tile_bg, '#f8fafc');
    const [cr, cg, cb] = toRgb(site.theme_tile_border, '#e2e8f0');

    root.style.setProperty('--bg-color', site.theme_bg_color);
    root.style.setProperty('--text-color', site.theme_text_color);
    root.style.setProperty('--button-bg', site.theme_button_color);
    root.style.setProperty('--button-text', site.theme_button_text_color);
    root.style.setProperty('--tile-bg', site.theme_tile_bg || '#f8fafc');
    root.style.setProperty('--tile-border', site.theme_tile_border || '#e2e8f0');
    root.style.setProperty('--tile-bg-rgba', `rgba(${br}, ${bg}, ${bb}, ${bgOpacity})`);
    root.style.setProperty('--tile-border-rgba', `rgba(${cr}, ${cg}, ${cb}, ${borderOpacity})`);
    root.style.setProperty('--tile-opacity', String(bgOpacity));
    root.style.setProperty('--tile-border-opacity', String(borderOpacity));
    if (site.theme_bg_image) {
      root.style.setProperty('--bg-image', `url('${site.theme_bg_image}')`);
    } else {
      root.style.removeProperty('--bg-image');
    }
  }, [site]);

  const logout = () => {
    setToken(null);
    setUser(null);
    localStorage.removeItem('blabs_token');
    localStorage.removeItem('blabs_user');
    navigate('/');
  };

  const authed = Boolean(token && user);

  return (
    <div className="page">
      <header>
        <div>
          <h1>{site.title}</h1>
          <p>{site.tagline}</p>
        </div>
        {authed && (
          <div className="user-info">
            <span>
              {user.username} {user.is_admin ? '(admin)' : ''}
            </span>
            <button onClick={logout} className="ghost">
              Logout
            </button>
          </div>
        )}
      </header>

      {!authed && (
        <section className="card">
          <Login onLogin={onLogin} user={user} />
          {error && <div className="error">Error: {error}</div>}
        </section>
      )}

      {authed && (
        <>
          <nav className="nav">
            <Link to="/">User</Link>
            {user.is_admin && <Link to="/admin">Admin</Link>}
          </nav>
          <Routes>
            <Route
              path="/"
              element={
                <section className="card">
                  <UserPanel />
                </section>
              }
            />
            {user.is_admin && (
              <>
                <Route
                  path="/admin"
                  element={
                    <section className="card">
                      <AdminDashboard />
                    </section>
                  }
                />
                <Route
                  path="/admin/users"
                  element={
                    <section className="card">
                      <AdminUsers />
                    </section>
                  }
                />
                <Route
                  path="/admin/templates"
                  element={
                    <section className="card">
                      <AdminTemplates />
                    </section>
                  }
                />
                <Route
                  path="/admin/images"
                  element={
                    <section className="card">
                      <AdminImages />
                    </section>
                  }
                />
                <Route
                  path="/admin/pods"
                  element={
                    <section className="card">
                      <AdminPods />
                    </section>
                  }
                />
                <Route
                  path="/admin/resources"
                  element={
                    <section className="card">
                      <AdminResources />
                    </section>
                  }
                />
                <Route
                  path="/admin/settings"
                  element={
                    <section className="card">
                      <AdminSettingsLanding />
                    </section>
                  }
                />
                <Route
                  path="/admin/settings/appearance"
                  element={
                    <section className="card">
                      <AdminAppearanceSettings />
                    </section>
                  }
                />
                <Route
                  path="/admin/settings/storage"
                  element={
                    <section className="card">
                      <AdminStorageSettings />
                    </section>
                  }
                />
                <Route
                  path="/admin/settings/runtime"
                  element={
                    <section className="card">
                      <AdminRuntimeSettings />
                    </section>
                  }
                />
                <Route
                  path="/admin/settings/sso"
                  element={
                    <section className="card">
                      <AdminSSOSettings />
                    </section>
                  }
                />
              </>
            )}
          </Routes>
        </>
      )}
    </div>
  );
};

const App = () => (
  <BrowserRouter>
    <AppShell />
  </BrowserRouter>
);

export default App;
