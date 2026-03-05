import React, { useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../../api';

const DEFAULT_THEME = {
  site_title: 'Bretter Labs',
  site_tagline: 'Run Virtual Labs and Software',
  theme_bg_color: '#f5f5f5',
  theme_text_color: '#111111',
  theme_button_color: '#2563eb',
  theme_button_text_color: '#ffffff',
  theme_bg_image: '',
  theme_bg_image_overlay_opacity: 0,
  theme_font_family: 'Inter, system-ui, -apple-system, sans-serif',
  theme_font_size_base: 16,
  theme_font_size_h1: 32,
  theme_font_size_h2: 24,
  theme_tile_bg: '#f8fafc',
  theme_tile_border: '#e2e8f0',
  theme_tile_opacity: 1,
  theme_tile_border_opacity: 1,
};

const DEFAULT_CONTRAST_TARGETS = {
  body: 4.5,
  button: 4.5,
  tile: 4.5,
  tile_border: 1.5,
};

const FONT_FAMILY_OPTIONS = [
  { label: 'Inter', value: 'Inter, system-ui, -apple-system, sans-serif' },
  { label: 'System UI', value: 'system-ui, -apple-system, sans-serif' },
  { label: 'Roboto', value: 'Roboto, Arial, sans-serif' },
  { label: 'Arial', value: 'Arial, sans-serif' },
  { label: 'Georgia', value: 'Georgia, serif' },
  { label: 'Monospace', value: 'JetBrains Mono, SFMono-Regular, Consolas, monospace' },
];

const THEME_KEYS = Object.keys(DEFAULT_THEME);

const normalizeContrastTargets = (payload) => {
  const next = { ...DEFAULT_CONTRAST_TARGETS, ...(payload || {}) };
  for (const key of Object.keys(DEFAULT_CONTRAST_TARGETS)) {
    const raw = Number(next[key]);
    if (!Number.isFinite(raw)) {
      next[key] = DEFAULT_CONTRAST_TARGETS[key];
      continue;
    }
    next[key] = Math.min(21, Math.max(1, Number(raw.toFixed(2))));
  }
  return next;
};

const normalizeTheme = (payload) => {
  const merged = { ...DEFAULT_THEME, ...(payload || {}) };
  const overlay = Number(merged.theme_bg_image_overlay_opacity || 0);
  merged.theme_bg_image_overlay_opacity = Math.min(0.85, Math.max(0, Number.isFinite(overlay) ? overlay : 0));
  const baseSize = Number(merged.theme_font_size_base || DEFAULT_THEME.theme_font_size_base);
  const h1Size = Number(merged.theme_font_size_h1 || DEFAULT_THEME.theme_font_size_h1);
  const h2Size = Number(merged.theme_font_size_h2 || DEFAULT_THEME.theme_font_size_h2);
  merged.theme_font_family = String(merged.theme_font_family || DEFAULT_THEME.theme_font_family).trim();
  merged.theme_font_size_base = Math.min(24, Math.max(12, Number.isFinite(baseSize) ? baseSize : DEFAULT_THEME.theme_font_size_base));
  merged.theme_font_size_h1 = Math.min(64, Math.max(20, Number.isFinite(h1Size) ? h1Size : DEFAULT_THEME.theme_font_size_h1));
  merged.theme_font_size_h2 = Math.min(48, Math.max(16, Number.isFinite(h2Size) ? h2Size : DEFAULT_THEME.theme_font_size_h2));
  const tileOpacity = Number(merged.theme_tile_opacity ?? DEFAULT_THEME.theme_tile_opacity);
  merged.theme_tile_opacity = Math.min(1, Math.max(0.1, Number.isFinite(tileOpacity) ? tileOpacity : DEFAULT_THEME.theme_tile_opacity));
  merged.theme_tile_border_opacity = 1;
  return merged;
};

const hexToRgb = (hex) => {
  const clean = String(hex || '').replace('#', '');
  if (clean.length !== 6) return null;
  const parsed = Number.parseInt(clean, 16);
  if (Number.isNaN(parsed)) return null;
  return {
    r: (parsed >> 16) & 255,
    g: (parsed >> 8) & 255,
    b: parsed & 255,
  };
};

const luminance = (hex) => {
  const rgb = hexToRgb(hex);
  if (!rgb) return null;
  const convert = (v) => {
    const n = v / 255;
    return n <= 0.03928 ? n / 12.92 : ((n + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * convert(rgb.r) + 0.7152 * convert(rgb.g) + 0.0722 * convert(rgb.b);
};

const contrastRatio = (foreground, background) => {
  const l1 = luminance(foreground);
  const l2 = luminance(background);
  if (l1 === null || l2 === null) return null;
  const bright = Math.max(l1, l2);
  const dark = Math.min(l1, l2);
  return (bright + 0.05) / (dark + 0.05);
};

const ratioLabel = (ratio) => (ratio ? ratio.toFixed(2) : 'n/a');

const colorWithAlpha = (hex, alpha, fallback = '#f8fafc') => {
  const rgb = hexToRgb(hex) || hexToRgb(fallback);
  if (!rgb) return fallback;
  const clamped = Math.min(1, Math.max(0, Number(alpha || 1)));
  return `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, ${clamped})`;
};

const resolveThemeImageUrl = (value) => {
  const raw = String(value || '').trim();
  if (!raw) return '';
  if (/^(https?:)?\/\//i.test(raw) || raw.startsWith('data:') || raw.startsWith('blob:')) {
    return raw;
  }
  const apiBase = String(api?.defaults?.baseURL || '').replace(/\/$/, '');
  if (!apiBase) return raw;
  if (raw.startsWith('/')) return `${apiBase}${raw}`;
  return `${apiBase}/${raw}`;
};

const contrastChecks = (theme, targets) => {
  const thresholds = normalizeContrastTargets(targets);
  const checks = [
    {
      key: 'body',
      label: 'Text on page background',
      ratio: contrastRatio(theme.theme_text_color, theme.theme_bg_color),
      min: thresholds.body,
    },
    {
      key: 'button',
      label: 'Button text contrast',
      ratio: contrastRatio(theme.theme_button_text_color, theme.theme_button_color),
      min: thresholds.button,
    },
    {
      key: 'tile',
      label: 'Tile text contrast',
      ratio: contrastRatio(theme.theme_text_color, theme.theme_tile_bg),
      min: thresholds.tile,
    },
    {
      key: 'tile_border',
      label: 'Tile border visibility',
      ratio: contrastRatio(theme.theme_tile_border, theme.theme_tile_bg),
      min: thresholds.tile_border,
    },
  ];

  const warnings = checks
    .filter((check) => check.ratio !== null && check.ratio < check.min)
    .map(
      (check) =>
        `${check.label} is low (${ratioLabel(check.ratio)}:1, target ${check.min}:1).`,
    );

  if (theme.theme_bg_image && Number(theme.theme_bg_image_overlay_opacity || 0) < 0.25) {
    warnings.push('Background image overlay is low; readability may suffer on bright images.');
  }

  return { checks, warnings };
};

const applyThemeToRoot = (next) => {
  const root = document.documentElement;
  const toRgb = (hex, fallback) => {
    const clean = (hex || fallback).replace('#', '');
    if (clean.length === 6) {
      return [
        Number.parseInt(clean.slice(0, 2), 16),
        Number.parseInt(clean.slice(2, 4), 16),
        Number.parseInt(clean.slice(4, 6), 16),
      ];
    }
    return [248, 250, 252];
  };
  const [br, bg, bb] = toRgb(next.theme_tile_bg, '#f8fafc');
  const [cr, cg, cb] = toRgb(next.theme_tile_border, '#e2e8f0');
  const overlay = Math.min(0.85, Math.max(0, Number(next.theme_bg_image_overlay_opacity || 0)));
  const tileOpacity = Math.min(1, Math.max(0.1, Number(next.theme_tile_opacity || DEFAULT_THEME.theme_tile_opacity)));

  root.style.setProperty('--bg-color', next.theme_bg_color || '#f5f5f5');
  root.style.setProperty('--text-color', next.theme_text_color || '#111111');
  root.style.setProperty('--button-bg', next.theme_button_color || '#2563eb');
  root.style.setProperty('--button-text', next.theme_button_text_color || '#ffffff');
  root.style.setProperty('--tile-bg', next.theme_tile_bg || '#f8fafc');
  root.style.setProperty('--tile-border', next.theme_tile_border || '#e2e8f0');
  root.style.setProperty('--tile-opacity', String(tileOpacity));
  root.style.setProperty('--tile-bg-rgba', `rgba(${br}, ${bg}, ${bb}, ${tileOpacity})`);
  root.style.setProperty('--tile-border-rgba', `rgba(${cr}, ${cg}, ${cb}, 1)`);
  root.style.setProperty('--bg-overlay-opacity', String(overlay));
  root.style.setProperty('--bg-overlay', `linear-gradient(rgba(0,0,0,${overlay}), rgba(0,0,0,${overlay}))`);
  root.style.setProperty('--app-font-family', next.theme_font_family || DEFAULT_THEME.theme_font_family);
  root.style.setProperty('--app-font-size-base', `${next.theme_font_size_base || DEFAULT_THEME.theme_font_size_base}px`);
  root.style.setProperty('--app-font-size-h1', `${next.theme_font_size_h1 || DEFAULT_THEME.theme_font_size_h1}px`);
  root.style.setProperty('--app-font-size-h2', `${next.theme_font_size_h2 || DEFAULT_THEME.theme_font_size_h2}px`);
  const resolvedImage = resolveThemeImageUrl(next.theme_bg_image);
  if (resolvedImage) {
    root.style.setProperty('--bg-image', `url('${resolvedImage}')`);
  } else {
    root.style.removeProperty('--bg-image');
  }
};

const sanitizeImportedTheme = (payload) => {
  const source = payload?.settings && typeof payload.settings === 'object' ? payload.settings : payload;
  if (!source || typeof source !== 'object') {
    throw new Error('Invalid JSON: expected a theme object or { settings: { ... } }.');
  }
  const filtered = {};
  for (const key of THEME_KEYS) {
    if (source[key] !== undefined) {
      filtered[key] = source[key];
    }
  }
  return normalizeTheme(filtered);
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

const AdminAppearanceSettings = () => {
  const [site, setSite] = useState(DEFAULT_THEME);
  const [savedSite, setSavedSite] = useState(DEFAULT_THEME);
  const [contrastTargets, setContrastTargets] = useState(DEFAULT_CONTRAST_TARGETS);
  const [draftContrastTargets, setDraftContrastTargets] = useState(DEFAULT_CONTRAST_TARGETS);
  const [savedContrastTargets, setSavedContrastTargets] = useState(DEFAULT_CONTRAST_TARGETS);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(false);
  const [uploadingBackground, setUploadingBackground] = useState(false);
  const [bgTestStatus, setBgTestStatus] = useState('');
  const fileRef = useRef(null);
  const backgroundFileRef = useRef(null);

  const hasUnsaved = useMemo(() => {
    const themeDirty = JSON.stringify(normalizeTheme(site)) !== JSON.stringify(normalizeTheme(savedSite));
    const contrastDirty =
      JSON.stringify(normalizeContrastTargets(contrastTargets)) !==
      JSON.stringify(normalizeContrastTargets(savedContrastTargets));
    return themeDirty || contrastDirty;
  }, [site, savedSite, contrastTargets, savedContrastTargets]);

  const contrast = useMemo(() => contrastChecks(site, contrastTargets), [site, contrastTargets]);

  const setTheme = (next) => {
    setSite(normalizeTheme(next));
  };

  const setDraftTarget = (key, value) => {
    setDraftContrastTargets((prev) =>
      normalizeContrastTargets({
        ...prev,
        [key]: value,
      }),
    );
  };

  const applyTarget = async (key) => {
    if (loading || saving) return;
    const nextTargets = normalizeContrastTargets({
      ...contrastTargets,
      [key]: draftContrastTargets[key],
    });
    setContrastTargets(nextTargets);
    setSaving(true);
    setError('');
    try {
      const payload = {
        ...normalizeTheme(savedSite),
        theme_contrast_body: nextTargets.body,
        theme_contrast_button: nextTargets.button,
        theme_contrast_tile: nextTargets.tile,
        theme_contrast_tile_border: nextTargets.tile_border,
      };
      const res = await api.patch('/admin/settings/site', payload);
      const nextRaw = res.data || payload;
      const persistedTargets = normalizeContrastTargets({
        body: nextRaw.theme_contrast_body,
        button: nextRaw.theme_contrast_button,
        tile: nextRaw.theme_contrast_tile,
        tile_border: nextRaw.theme_contrast_tile_border,
      });
      setContrastTargets(persistedTargets);
      setDraftContrastTargets(persistedTargets);
      setSavedContrastTargets(persistedTargets);
      setMessage(`Saved target contrast for ${key}.`);
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to save target contrast');
    } finally {
      setSaving(false);
    }
  };

  useEffect(() => {
    const load = async () => {
      setLoading(true);
      try {
        const siteRes = await api.get('/admin/settings/site');
        const next = normalizeTheme(siteRes.data || {});
        const loadedTargets = normalizeContrastTargets({
          body: siteRes.data?.theme_contrast_body,
          button: siteRes.data?.theme_contrast_button,
          tile: siteRes.data?.theme_contrast_tile,
          tile_border: siteRes.data?.theme_contrast_tile_border,
        });
        setSite(next);
        setSavedSite(next);
        setContrastTargets(loadedTargets);
        setDraftContrastTargets(loadedTargets);
        setSavedContrastTargets(loadedTargets);
        applyThemeToRoot(next);
      } catch (err) {
        setError(err.response?.data?.detail || 'Failed to load appearance settings');
      } finally {
        setLoading(false);
      }
    };
    load();
  }, []);

  const saveSite = async () => {
    setSaving(true);
    setError('');
    setMessage('');
    try {
      const payload = {
        ...normalizeTheme(site),
        theme_contrast_body: contrastTargets.body,
        theme_contrast_button: contrastTargets.button,
        theme_contrast_tile: contrastTargets.tile,
        theme_contrast_tile_border: contrastTargets.tile_border,
      };
      const res = await api.patch('/admin/settings/site', payload);
      const nextRaw = res.data || payload;
      const next = normalizeTheme(nextRaw);
      const nextTargets = normalizeContrastTargets({
        body: nextRaw.theme_contrast_body,
        button: nextRaw.theme_contrast_button,
        tile: nextRaw.theme_contrast_tile,
        tile_border: nextRaw.theme_contrast_tile_border,
      });
      setSite(next);
      setSavedSite(next);
      setContrastTargets(nextTargets);
      setDraftContrastTargets(nextTargets);
      setSavedContrastTargets(nextTargets);
      setMessage('Appearance updated.');
      applyThemeToRoot(next);
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to save appearance');
    } finally {
      setSaving(false);
    }
  };

  const handleBackgroundUpload = async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    setUploadingBackground(true);
    setError('');
    setMessage('');
    setBgTestStatus('');
    try {
      const form = new FormData();
      form.append('file', file);
      const res = await api.post('/admin/settings/site/background', form);
      const uploadedPath = String(res.data?.theme_bg_image || '').trim();
      if (!uploadedPath) {
        throw new Error('background upload response missing theme_bg_image');
      }
      const nextSite = normalizeTheme({ ...site, theme_bg_image: uploadedPath });
      setSite(nextSite);
      setSavedSite((prev) => normalizeTheme({ ...prev, theme_bg_image: uploadedPath }));
      setMessage(`Background uploaded: ${file.name}`);
      applyThemeToRoot(nextSite);
    } catch (err) {
      setError(err.response?.data?.detail || err.message || 'Failed to upload background image');
    } finally {
      event.target.value = '';
      setUploadingBackground(false);
    }
  };

  const testBackgroundImage = async () => {
    const url = resolveThemeImageUrl(site.theme_bg_image);
    if (!url) {
      setBgTestStatus('No background image URL set. Fallback color will be used.');
      return;
    }
    setBgTestStatus('Testing image URL...');
    try {
      await new Promise((resolve, reject) => {
        const img = new Image();
        img.onload = () => resolve();
        img.onerror = () => reject(new Error('Image failed to load.'));
        img.src = url;
      });
      setBgTestStatus('Background image loaded successfully in browser preview.');
    } catch (err) {
      setBgTestStatus('Image test failed. Check URL, SSL, or host accessibility.');
    }
  };

  const exportThemeJson = () => {
    const payload = {
      version: 1,
      exported_at: new Date().toISOString(),
      settings: normalizeTheme(site),
      contrast_targets: normalizeContrastTargets(contrastTargets),
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `bretter-theme-${new Date().toISOString().replace(/[:.]/g, '-')}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    setMessage('Theme JSON exported.');
  };

  const handleImportFile = async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    try {
      const text = await file.text();
      const parsed = JSON.parse(text);
      const imported = sanitizeImportedTheme(parsed);
      setTheme(imported);
      const importedTargets = normalizeContrastTargets(parsed?.contrast_targets);
      setContrastTargets(importedTargets);
      setDraftContrastTargets(importedTargets);
      setMessage(`Imported theme from ${file.name}. Save to apply globally.`);
      setError('');
    } catch (err) {
      setError(`Import failed: ${err.message || 'invalid JSON file'}`);
    } finally {
      event.target.value = '';
    }
  };

  const overlay = Math.min(0.85, Math.max(0, Number(site.theme_bg_image_overlay_opacity || 0)));
  const resolvedBackgroundUrl = resolveThemeImageUrl(site.theme_bg_image);
  const previewBackground = resolvedBackgroundUrl
    ? `linear-gradient(rgba(0,0,0,${overlay}), rgba(0,0,0,${overlay})), url('${resolvedBackgroundUrl}')`
    : `linear-gradient(rgba(0,0,0,${overlay}), rgba(0,0,0,${overlay}))`;

  return (
    <div>
      <h2>Appearance</h2>
      <p>Configure theme colors, preview before saving, and import/export theme JSON.</p>
      {error && <div className="error">{error}</div>}
      {message && <div className="info">{message}</div>}

      <div className="actions" style={{ marginBottom: '1rem', flexWrap: 'wrap' }}>
        <button className="ghost" onClick={exportThemeJson} disabled={loading || saving}>
          Export Theme JSON
        </button>
        <button className="ghost" onClick={() => fileRef.current?.click()} disabled={loading || saving}>
          Import Theme JSON
        </button>
        <input
          ref={fileRef}
          type="file"
          accept="application/json,.json"
          style={{ display: 'none' }}
          onChange={handleImportFile}
        />
      </div>

      <div className="card" style={{ marginBottom: '1rem' }}>
        <h3>Accessibility Checks</h3>
        <div className="actions" style={{ marginTop: '0.5rem', flexWrap: 'wrap' }}>
          <button
            className="ghost"
            onClick={() => {
              setContrastTargets(DEFAULT_CONTRAST_TARGETS);
              setDraftContrastTargets(DEFAULT_CONTRAST_TARGETS);
            }}
            disabled={loading || saving}
          >
            Reset Contrast Targets
          </button>
        </div>
        <div className="tile-grid" style={{ marginTop: '0.75rem' }}>
          {contrast.checks.map((check) => {
            const ok = check.ratio !== null && check.ratio >= check.min;
            return (
              <div key={check.key} className="tile template-tile">
                <div className="tile-header">
                  <h4>{check.label}</h4>
                  <span className={ok ? 'badge success' : 'badge warn'}>{ok ? 'OK' : 'Warn'}</span>
                </div>
                <div className="muted small">
                  Contrast: {ratioLabel(check.ratio)}:1 (target {check.min}:1)
                </div>
                <label className="muted small" style={{ marginTop: '0.45rem', display: 'block' }}>
                  Target ratio
                  <input
                    type="number"
                    min="1"
                    max="21"
                    step="0.1"
                    value={draftContrastTargets[check.key]}
                    onChange={(e) => setDraftTarget(check.key, Number(e.target.value || DEFAULT_CONTRAST_TARGETS[check.key]))}
                    style={{ marginTop: '0.25rem' }}
                  />
                </label>
                <div className="actions" style={{ marginTop: '0.35rem' }}>
                  <button className="ghost" type="button" onClick={() => applyTarget(check.key)} disabled={loading || saving}>
                    Set Target Contrast
                  </button>
                </div>
              </div>
            );
          })}
        </div>
        {contrast.warnings.length > 0 && (
          <div style={{ marginTop: '0.8rem' }}>
            {contrast.warnings.map((warn, idx) => (
              <div key={`warn-${idx}`} className="muted small">
                - {warn}
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="grid">
        <div className="card">
          <div className="form" style={{ maxWidth: '640px' }}>
            <label>
              Title
              <input value={site.site_title} onChange={(e) => setTheme({ ...site, site_title: e.target.value })} />
            </label>
            <label>
              Tagline
              <input value={site.site_tagline} onChange={(e) => setTheme({ ...site, site_tagline: e.target.value })} />
            </label>
            <label>
              Font Family
              <select
                value={FONT_FAMILY_OPTIONS.some((item) => item.value === site.theme_font_family) ? site.theme_font_family : '__custom__'}
                onChange={(e) => {
                  if (e.target.value === '__custom__') return;
                  setTheme({ ...site, theme_font_family: e.target.value });
                }}
              >
                {FONT_FAMILY_OPTIONS.map((item) => (
                  <option key={item.value} value={item.value}>
                    {item.label}
                  </option>
                ))}
                <option value="__custom__">Custom</option>
              </select>
            </label>
            <label>
              Custom Font Family (comma-separated fallback stack)
              <input
                value={site.theme_font_family}
                onChange={(e) => setTheme({ ...site, theme_font_family: e.target.value })}
                placeholder="Inter, system-ui, sans-serif"
              />
            </label>
            <div className="grid">
              <label>
                Base Font Size (px)
                <input
                  type="number"
                  min="12"
                  max="24"
                  step="1"
                  value={site.theme_font_size_base}
                  onChange={(e) =>
                    setTheme({
                      ...site,
                      theme_font_size_base: Number(e.target.value || DEFAULT_THEME.theme_font_size_base),
                    })
                  }
                />
              </label>
              <label>
                Title Font Size (px)
                <input
                  type="number"
                  min="20"
                  max="64"
                  step="1"
                  value={site.theme_font_size_h1}
                  onChange={(e) =>
                    setTheme({
                      ...site,
                      theme_font_size_h1: Number(e.target.value || DEFAULT_THEME.theme_font_size_h1),
                    })
                  }
                />
              </label>
              <label>
                Section Header Size (px)
                <input
                  type="number"
                  min="16"
                  max="48"
                  step="1"
                  value={site.theme_font_size_h2}
                  onChange={(e) =>
                    setTheme({
                      ...site,
                      theme_font_size_h2: Number(e.target.value || DEFAULT_THEME.theme_font_size_h2),
                    })
                  }
                />
              </label>
            </div>
            <div className="grid">
              <label>
                Background Color (fallback)
                <input
                  type="color"
                  value={site.theme_bg_color}
                  onChange={(e) => setTheme({ ...site, theme_bg_color: e.target.value })}
                />
                <Swatch color={site.theme_bg_color} />
              </label>
              <label>
                Text Color
                <input
                  type="color"
                  value={site.theme_text_color}
                  onChange={(e) => setTheme({ ...site, theme_text_color: e.target.value })}
                />
                <Swatch color={site.theme_text_color} />
              </label>
              <label>
                Button Color
                <input
                  type="color"
                  value={site.theme_button_color}
                  onChange={(e) => setTheme({ ...site, theme_button_color: e.target.value })}
                />
                <Swatch color={site.theme_button_color} />
              </label>
              <label>
                Button Text Color
                <input
                  type="color"
                  value={site.theme_button_text_color}
                  onChange={(e) => setTheme({ ...site, theme_button_text_color: e.target.value })}
                />
                <Swatch color={site.theme_button_text_color} />
              </label>
              <label>
                Tile Background
                <input
                  type="color"
                  value={site.theme_tile_bg}
                  onChange={(e) => setTheme({ ...site, theme_tile_bg: e.target.value })}
                />
                <Swatch color={site.theme_tile_bg} />
              </label>
              <label>
                Tile Border
                <input
                  type="color"
                  value={site.theme_tile_border}
                  onChange={(e) => setTheme({ ...site, theme_tile_border: e.target.value })}
                />
                <Swatch color={site.theme_tile_border} />
              </label>
            </div>

            <label>
              Login Background (cluster-hosted)
              <input value={site.theme_bg_image || ''} readOnly placeholder="No image uploaded" />
            </label>
            <div className="actions" style={{ flexWrap: 'wrap' }}>
              <button
                className="ghost"
                onClick={() => backgroundFileRef.current?.click()}
                type="button"
                disabled={loading || saving || uploadingBackground}
              >
                {uploadingBackground ? 'Uploading...' : 'Upload Background Image'}
              </button>
              <button
                className="ghost"
                onClick={() => setTheme({ ...site, theme_bg_image: '' })}
                type="button"
                disabled={loading || saving || uploadingBackground || !site.theme_bg_image}
              >
                Clear Background
              </button>
              <input
                ref={backgroundFileRef}
                type="file"
                accept="image/png,image/jpeg,image/webp,image/gif,image/svg+xml"
                style={{ display: 'none' }}
                onChange={handleBackgroundUpload}
              />
            </div>

            <label>
              Background image dim overlay ({overlay.toFixed(2)})
              <input
                type="range"
                min="0"
                max="0.85"
                step="0.05"
                value={overlay}
                onChange={(e) => setTheme({ ...site, theme_bg_image_overlay_opacity: Number(e.target.value) })}
              />
            </label>
            <label>
              Tile background opacity ({Number(site.theme_tile_opacity || 1).toFixed(2)})
              <input
                type="range"
                min="0.1"
                max="1"
                step="0.05"
                value={site.theme_tile_opacity}
                onChange={(e) => setTheme({ ...site, theme_tile_opacity: Number(e.target.value) })}
              />
            </label>

            <div className="actions">
              <button className="ghost" onClick={testBackgroundImage} type="button" disabled={loading || saving}>
                Test/Load Background Image
              </button>
            </div>
            {bgTestStatus && <div className="muted small">{bgTestStatus}</div>}

            <div className="actions">
              <button onClick={saveSite} disabled={saving || loading || !hasUnsaved}>
                {saving ? 'Saving...' : 'Save'}
              </button>
            </div>
          </div>
        </div>

        <div className="card">
          <h3>Live Preview</h3>
          <div
            style={{
              marginTop: '0.75rem',
              borderRadius: '12px',
              border: `1px solid ${site.theme_tile_border}`,
              padding: '1rem',
              color: site.theme_text_color,
              fontFamily: site.theme_font_family,
              fontSize: `${site.theme_font_size_base}px`,
              backgroundColor: site.theme_bg_color,
              backgroundImage: previewBackground,
              backgroundSize: 'cover',
              backgroundPosition: 'center',
              minHeight: '260px',
            }}
          >
            <div style={{ marginBottom: '0.75rem' }}>
              <h3 style={{ margin: 0, fontSize: `${site.theme_font_size_h1}px` }}>{site.site_title || 'Title Preview'}</h3>
              <div className="muted small" style={{ color: site.theme_text_color }}>
                {site.site_tagline || 'Tagline Preview'}
              </div>
            </div>

            <div style={{ marginBottom: '0.75rem' }}>
              <button
                type="button"
                style={{
                  background: site.theme_button_color,
                  color: site.theme_button_text_color,
                  border: 'none',
                  borderRadius: '8px',
                  padding: '0.45rem 0.75rem',
                }}
              >
                Primary Button
              </button>
            </div>

            <div className="tile-grid">
              <div
                className="tile"
                style={{
                  background: colorWithAlpha(site.theme_tile_bg, site.theme_tile_opacity, '#f8fafc'),
                  borderColor: site.theme_tile_border,
                  color: site.theme_text_color,
                }}
              >
                <h4 style={{ margin: 0, fontSize: `${site.theme_font_size_h2}px` }}>Template Tile</h4>
                <div className="muted small" style={{ color: site.theme_text_color }}>
                  Sample text readability on tile background.
                </div>
              </div>
              <div
                className="tile"
                style={{
                  background: site.theme_bg_color,
                  borderColor: site.theme_tile_border,
                  color: site.theme_text_color,
                }}
              >
                <h4 style={{ margin: 0, fontSize: `${site.theme_font_size_h2}px` }}>Fallback Color</h4>
                <div className="muted small" style={{ color: site.theme_text_color }}>
                  Used when background image is unavailable.
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default AdminAppearanceSettings;
