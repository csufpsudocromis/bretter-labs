import React, { useEffect, useState } from 'react';
import { api } from '../../api';

const AdminAppearanceSettings = () => {
  const [site, setSite] = useState({
    site_title: '',
    site_tagline: '',
    theme_bg_color: '#f5f5f5',
    theme_text_color: '#111111',
    theme_button_color: '#2563eb',
    theme_button_text_color: '#ffffff',
    theme_bg_image: '',
    theme_tile_bg: '#f8fafc',
    theme_tile_border: '#e2e8f0',
    theme_tile_border_opacity: 1,
  });
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [saving, setSaving] = useState(false);

  const applyTheme = (next) => {
    const root = document.documentElement;
    root.style.setProperty('--bg-color', next.theme_bg_color || '#f5f5f5');
    root.style.setProperty('--text-color', next.theme_text_color || '#111111');
    root.style.setProperty('--button-bg', next.theme_button_color || '#2563eb');
    root.style.setProperty('--button-text', next.theme_button_text_color || '#ffffff');
    root.style.setProperty('--tile-bg', next.theme_tile_bg || '#f8fafc');
    root.style.setProperty('--tile-border', next.theme_tile_border || '#e2e8f0');
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
    const bgOpacity = 1; // tile opacity fixed at full
    const borderOpacity = 1; // keep border fully opaque
    const [br, bg, bb] = toRgb(next.theme_tile_bg, '#f8fafc');
    const [cr, cg, cb] = toRgb(next.theme_tile_border, '#e2e8f0');
    root.style.setProperty('--tile-opacity', String(bgOpacity));
    root.style.setProperty('--tile-bg-rgba', `rgba(${br}, ${bg}, ${bb}, ${bgOpacity})`);
    root.style.setProperty('--tile-border-rgba', `rgba(${cr}, ${cg}, ${cb}, ${borderOpacity})`);
    if (next.theme_bg_image) {
      root.style.setProperty('--bg-image', `url('${next.theme_bg_image}')`);
    } else {
      root.style.removeProperty('--bg-image');
    }
  };

  const Swatch = ({ color }) => (
    <span
      style={{
        display: 'inline-block',
        width: '24px',
        height: '24px',
        borderRadius: '4px',
        border: '1px solid #ccc',
        marginLeft: '8px',
        backgroundColor: color || '#ffffff',
      }}
    />
  );

  useEffect(() => {
    const load = async () => {
      try {
        const siteRes = await api.get('/admin/settings/site');
        const next = siteRes.data || {};
        setSite((prev) => ({ ...prev, ...next }));
        applyTheme({ ...site, ...next });
      } catch (err) {
        setError(err.response?.data?.detail || 'Failed to load appearance settings');
      }
    };
    load();
  }, []);

  const saveSite = async () => {
    setSaving(true);
    setError('');
    setMessage('');
    try {
      await api.patch('/admin/settings/site', { ...site, theme_tile_opacity: 1, theme_tile_border_opacity: 1 });
      setMessage('Appearance updated.');
      applyTheme({ ...site, theme_tile_opacity: 1, theme_tile_border_opacity: 1 });
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to save appearance');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div>
      <h2>Appearance</h2>
      <p>Update title/tagline and theme colors.</p>
      {error && <div className="error">{error}</div>}
      <div className="card">
        <div className="form">
          <label>
            Title
            <input value={site.site_title} onChange={(e) => setSite({ ...site, site_title: e.target.value })} />
          </label>
          <label>
            Tagline
            <input value={site.site_tagline} onChange={(e) => setSite({ ...site, site_tagline: e.target.value })} />
          </label>
          <div className="grid">
            <label>
              Background Color
              <input
                type="color"
                value={site.theme_bg_color}
                onChange={(e) => setSite({ ...site, theme_bg_color: e.target.value })}
              />
              <Swatch color={site.theme_bg_color} />
            </label>
            <label>
              Text Color
              <input
                type="color"
                value={site.theme_text_color}
                onChange={(e) => setSite({ ...site, theme_text_color: e.target.value })}
              />
              <Swatch color={site.theme_text_color} />
            </label>
            <label>
              Button Color
              <input
                type="color"
                value={site.theme_button_color}
                onChange={(e) => setSite({ ...site, theme_button_color: e.target.value })}
              />
              <Swatch color={site.theme_button_color} />
            </label>
            <label>
              Button Text Color
              <input
                type="color"
                value={site.theme_button_text_color}
                onChange={(e) => setSite({ ...site, theme_button_text_color: e.target.value })}
              />
              <Swatch color={site.theme_button_text_color} />
            </label>
            <label>
              Tile Background
              <input
                type="color"
                value={site.theme_tile_bg}
                onChange={(e) => setSite({ ...site, theme_tile_bg: e.target.value })}
              />
              <Swatch color={site.theme_tile_bg} />
          </label>
          <label>
            Tile Border
            <input
              type="color"
              value={site.theme_tile_border}
              onChange={(e) => setSite({ ...site, theme_tile_border: e.target.value })}
            />
            <Swatch color={site.theme_tile_border} />
          </label>
        </div>
        <label>
          Background Image URL (optional)
          <input value={site.theme_bg_image} onChange={(e) => setSite({ ...site, theme_bg_image: e.target.value })} />
          </label>
          <div className="actions">
            <button onClick={saveSite} disabled={saving}>
              {saving ? 'Saving…' : 'Save'}
            </button>
          </div>
          {message && <div className="info">{message}</div>}
        </div>
      </div>
    </div>
  );
};

export default AdminAppearanceSettings;
