import React, { useEffect, useMemo, useState } from "react";
import { BrowserRouter, Link, Route, Routes, useLocation, useNavigate } from "react-router-dom";

import { api, AUTH_INVALID_EVENT, NAMESPACE_FORBIDDEN_EVENT } from "./api";
import Login from "./components/Login.jsx";
import UserPanel from "./components/UserPanel.jsx";
import AdminDashboard from "./components/admin/AdminDashboard.jsx";
import AdminUsers from "./components/admin/AdminUsers.jsx";
import AdminTeamQuotas from "./components/admin/AdminTeamQuotas.jsx";
import AdminTemplates from "./components/admin/AdminTemplates.jsx";
import AdminImages from "./components/admin/AdminImages.jsx";
import AdminContainerImages from "./components/admin/AdminContainerImages.jsx";
import AdminContainerTemplates from "./components/admin/AdminContainerTemplates.jsx";
import AdminPods from "./components/admin/AdminPods.jsx";
import AdminResources from "./components/admin/AdminResources.jsx";
import AdminAlertsErrors from "./components/admin/AdminAlertsErrors.jsx";
import AdminAuditEvents from "./components/admin/AdminAuditEvents.jsx";
import AdminSettingsLanding from "./components/admin/AdminSettingsLanding.jsx";
import AdminAppearanceSettings from "./components/admin/AdminAppearanceSettings.jsx";
import AdminRuntimeSettings from "./components/admin/AdminRuntimeSettings.jsx";
import AdminSSOSettings from "./components/admin/AdminSSOSettings.jsx";
import AdminStorageSettings from "./components/admin/AdminStorageSettings.jsx";
import AdminLDAPSettings from "./components/admin/AdminLDAPSettings.jsx";
import AdminNamespacesSettings from "./components/admin/AdminNamespacesSettings.jsx";
import NamespaceDirectory from "./components/NamespaceDirectory.jsx";

const DEFAULT_SITE = {
  title: "Bretter Labs",
  tagline: "Run Virtual Labs and Software",
  theme_bg_color: "#f5f5f5",
  theme_text_color: "#111111",
  theme_button_color: "#2563eb",
  theme_button_text_color: "#ffffff",
  theme_bg_image: "",
  theme_bg_image_overlay_opacity: 0,
  theme_tile_bg: "#f8fafc",
  theme_tile_border: "#e2e8f0",
  theme_tile_opacity: 1,
  theme_tile_border_opacity: 1,
  theme_font_family: "Inter, system-ui, -apple-system, sans-serif",
  theme_font_size_base: 16,
  theme_font_size_h1: 32,
  theme_font_size_h2: 24,
};

const resolveThemeImageUrl = (value) => {
  const raw = String(value || "").trim();
  if (!raw) return "";
  if (/^(https?:)?\/\//i.test(raw) || raw.startsWith("data:") || raw.startsWith("blob:")) {
    return raw;
  }
  const apiBase = String(api?.defaults?.baseURL || "").replace(/\/$/, "");
  if (!apiBase) return raw;
  if (raw.startsWith("/")) return `${apiBase}${raw}`;
  return `${apiBase}/${raw}`;
};

const roleDisplay = (user) => {
  const raw = String(user?.role || "").trim();
  if (!raw) return user?.is_admin ? "admin" : "";
  return raw.replace(/_/g, " ");
};

const normalizeNamespace = (value) =>
  String(value || "")
    .trim()
    .toLowerCase();

const namespacePath = (namespace) => {
  const normalized = normalizeNamespace(namespace);
  if (!normalized) return "";
  return `/ns/${encodeURIComponent(normalized)}`;
};

const withNamespacePath = (pathname, namespace) => {
  const base = namespacePath(namespace);
  if (!base) return pathname || "/";
  const rawPath = String(pathname || "/");
  const stripped = rawPath.replace(/^\/(?:ns|namespace)\/[^/]+/i, "") || "/";
  if (stripped === "/") return base;
  return `${base}${stripped.startsWith("/") ? stripped : `/${stripped}`}`;
};

const AppShell = () => {
  const [user, setUser] = useState(null);
  const [error, setError] = useState(null);
  const [site, setSite] = useState({ ...DEFAULT_SITE });
  const [availableNamespaces, setAvailableNamespaces] = useState([]);
  const [selectedNamespace, setSelectedNamespace] = useState("");
  const navigate = useNavigate();
  const location = useLocation();

  const namespaceMatch = String(location?.pathname || "").match(/^\/(?:ns|namespace)\/([^/]+)/i);
  const pathNamespace = normalizeNamespace(namespaceMatch?.[1]);
  const userNamespaceScopes = useMemo(() => {
    if (!Array.isArray(user?.namespace_scopes)) return [];
    return [...new Set(user.namespace_scopes.map((ns) => normalizeNamespace(ns)).filter(Boolean))];
  }, [user]);
  const preferredUserNamespace = userNamespaceScopes[0] || "";
  const rememberedNamespace = normalizeNamespace(selectedNamespace);
  const activeNamespace = pathNamespace || rememberedNamespace || preferredUserNamespace;
  const canAccessAdmin = Boolean(user?.can_access_admin ?? user?.is_admin);
  const isStandardUser =
    String(user?.role || "")
      .trim()
      .toLowerCase() === "user" && !canAccessAdmin;
  const namespaceOptions = useMemo(() => {
    const merged = new Set([
      ...userNamespaceScopes,
      ...(Array.isArray(availableNamespaces) ? availableNamespaces.map((ns) => normalizeNamespace(ns)) : []),
    ]);
    if (activeNamespace) {
      merged.add(activeNamespace);
    }
    return [...merged].filter(Boolean).sort();
  }, [userNamespaceScopes, availableNamespaces, activeNamespace]);
  const namespacePrefix = namespacePath(activeNamespace);
  const userRootPath = namespacePrefix || "/";
  const adminRootPath = namespacePrefix ? `${namespacePrefix}/admin` : "/admin";
  const namespaceLabel = activeNamespace || "unscoped";
  const canSwitchNamespace = !isStandardUser && namespaceOptions.length > 0;

  useEffect(() => {
    const loadCurrentUser = async () => {
      try {
        const res = await api.get("/auth/me");
        setUser(res.data);
      } catch (err) {
        setUser(null);
      }
    };
    loadCurrentUser();
  }, []);

  useEffect(() => {
    if (!pathNamespace) return;
    setSelectedNamespace((prev) => {
      const normalizedPrev = normalizeNamespace(prev);
      return normalizedPrev === pathNamespace ? normalizedPrev : pathNamespace;
    });
  }, [pathNamespace]);

  useEffect(() => {
    if (!user) {
      setSelectedNamespace("");
      return;
    }
    const role = String(user?.role || "")
      .trim()
      .toLowerCase();
    if (role !== "namespace_admin") return;
    if (userNamespaceScopes.length === 0) {
      setSelectedNamespace("");
      return;
    }
    setSelectedNamespace((prev) => {
      const normalizedPrev = normalizeNamespace(prev);
      if (normalizedPrev && userNamespaceScopes.includes(normalizedPrev)) {
        return normalizedPrev;
      }
      return userNamespaceScopes[0];
    });
  }, [user, userNamespaceScopes]);

  useEffect(() => {
    if (!user || !canAccessAdmin) {
      setAvailableNamespaces([]);
      return;
    }
    let cancelled = false;
    const loadNamespaces = async () => {
      try {
        const res = await api.get("/admin/template-namespaces");
        if (cancelled) return;
        const options = Array.isArray(res?.data)
          ? [...new Set(res.data.map((value) => normalizeNamespace(value)).filter(Boolean))]
          : [];
        setAvailableNamespaces(options);
      } catch (err) {
        if (!cancelled) {
          setAvailableNamespaces([]);
        }
      }
    };
    loadNamespaces();
    return () => {
      cancelled = true;
    };
  }, [user, canAccessAdmin]);

  useEffect(() => {
    const handleAuthInvalid = (event) => {
      const msg = event?.detail?.message || "Session expired. Please sign in again.";
      setUser(null);
      setError(msg);
      navigate(userRootPath);
    };
    const handleNamespaceForbidden = (event) => {
      const eventNamespace = normalizeNamespace(event?.detail?.namespace || activeNamespace);
      const detail = String(event?.detail?.message || "").trim();
      const reason = eventNamespace
        ? `Namespace access denied for "${eventNamespace}". ${detail || "Check your assigned namespace scopes."}`
        : detail || "Namespace access denied.";
      setError(reason);
    };
    window.addEventListener(AUTH_INVALID_EVENT, handleAuthInvalid);
    window.addEventListener(NAMESPACE_FORBIDDEN_EVENT, handleNamespaceForbidden);
    return () => {
      window.removeEventListener(AUTH_INVALID_EVENT, handleAuthInvalid);
      window.removeEventListener(NAMESPACE_FORBIDDEN_EVENT, handleNamespaceForbidden);
    };
  }, [navigate, userRootPath, activeNamespace]);

  const onLogin = async (username, password) => {
    try {
      const res = await api.post("/auth/login", { username, password });
      const nextUser = res?.data?.user || null;
      const nextRole = String(nextUser?.role || "")
        .trim()
        .toLowerCase();
      const nextScopes = Array.isArray(nextUser?.namespace_scopes)
        ? nextUser.namespace_scopes.map((ns) => normalizeNamespace(ns)).filter(Boolean)
        : [];
      setUser(nextUser);
      setError(null);
      if (nextRole === "namespace_admin" && nextScopes.length > 0) {
        setSelectedNamespace(nextScopes[0]);
        navigate(withNamespacePath(location.pathname, nextScopes[0]));
      } else {
        navigate(userRootPath);
      }
    } catch (err) {
      setError(err.response?.data?.detail || "Login failed");
      setUser(null);
    }
  };

  useEffect(() => {
    if (!user) return;
    const role = String(user?.role || "")
      .trim()
      .toLowerCase();
    if (role !== "namespace_admin") return;
    const targetNamespace = rememberedNamespace || preferredUserNamespace;
    if (!targetNamespace) return;
    if (String(location.pathname || "/") === "/") return;
    if (pathNamespace === targetNamespace) return;
    navigate(withNamespacePath(location.pathname, targetNamespace), { replace: true });
  }, [user, rememberedNamespace, preferredUserNamespace, pathNamespace, location.pathname, navigate]);

  useEffect(() => {
    const loadSite = async () => {
      try {
        const res = await api.get("/user/settings/site");
        setSite({
          title: res.data.site_title,
          tagline: res.data.site_tagline,
          theme_bg_color: res.data.theme_bg_color,
          theme_text_color: res.data.theme_text_color,
          theme_button_color: res.data.theme_button_color,
          theme_button_text_color: res.data.theme_button_text_color,
          theme_bg_image: resolveThemeImageUrl(res.data.theme_bg_image),
          theme_bg_image_overlay_opacity: Number(res.data.theme_bg_image_overlay_opacity || 0),
          theme_tile_bg: res.data.theme_tile_bg,
          theme_tile_border: res.data.theme_tile_border,
          theme_tile_opacity: Number(res.data.theme_tile_opacity || DEFAULT_SITE.theme_tile_opacity),
          theme_tile_border_opacity: 1,
          theme_font_family: String(res.data.theme_font_family || DEFAULT_SITE.theme_font_family),
          theme_font_size_base: Number(res.data.theme_font_size_base || DEFAULT_SITE.theme_font_size_base),
          theme_font_size_h1: Number(res.data.theme_font_size_h1 || DEFAULT_SITE.theme_font_size_h1),
          theme_font_size_h2: Number(res.data.theme_font_size_h2 || DEFAULT_SITE.theme_font_size_h2),
        });
      } catch (err) {
        setSite({ ...DEFAULT_SITE });
      }
    };
    loadSite();
  }, [user]);

  useEffect(() => {
    const root = document.documentElement;
    const toRgb = (hex, fallback) => {
      const clean = (hex || fallback).replace("#", "");
      if (clean.length === 6) {
        return [parseInt(clean.slice(0, 2), 16), parseInt(clean.slice(2, 4), 16), parseInt(clean.slice(4, 6), 16)];
      }
      return [248, 250, 252];
    };
    const bgOpacity = Math.min(1, Math.max(0.1, Number(site.theme_tile_opacity || DEFAULT_SITE.theme_tile_opacity)));
    const borderOpacity = 1;
    const [br, bg, bb] = toRgb(site.theme_tile_bg, "#f8fafc");
    const [cr, cg, cb] = toRgb(site.theme_tile_border, "#e2e8f0");

    root.style.setProperty("--bg-color", site.theme_bg_color);
    root.style.setProperty("--text-color", site.theme_text_color);
    root.style.setProperty("--button-bg", site.theme_button_color);
    root.style.setProperty("--button-text", site.theme_button_text_color);
    const overlayOpacity = Math.min(0.85, Math.max(0, Number(site.theme_bg_image_overlay_opacity || 0)));
    root.style.setProperty("--tile-bg", site.theme_tile_bg || "#f8fafc");
    root.style.setProperty("--tile-border", site.theme_tile_border || "#e2e8f0");
    root.style.setProperty("--tile-bg-rgba", `rgba(${br}, ${bg}, ${bb}, ${bgOpacity})`);
    root.style.setProperty("--tile-border-rgba", `rgba(${cr}, ${cg}, ${cb}, ${borderOpacity})`);
    root.style.setProperty("--tile-opacity", String(bgOpacity));
    root.style.setProperty("--tile-border-opacity", String(borderOpacity));
    root.style.setProperty("--bg-overlay-opacity", String(overlayOpacity));
    root.style.setProperty(
      "--bg-overlay",
      `linear-gradient(rgba(0,0,0,${overlayOpacity}), rgba(0,0,0,${overlayOpacity}))`
    );
    const baseSize = Math.min(24, Math.max(12, Number(site.theme_font_size_base || DEFAULT_SITE.theme_font_size_base)));
    const h1Size = Math.min(64, Math.max(20, Number(site.theme_font_size_h1 || DEFAULT_SITE.theme_font_size_h1)));
    const h2Size = Math.min(48, Math.max(16, Number(site.theme_font_size_h2 || DEFAULT_SITE.theme_font_size_h2)));
    root.style.setProperty("--app-font-family", site.theme_font_family || DEFAULT_SITE.theme_font_family);
    root.style.setProperty("--app-font-size-base", `${baseSize}px`);
    root.style.setProperty("--app-font-size-h1", `${h1Size}px`);
    root.style.setProperty("--app-font-size-h2", `${h2Size}px`);
    if (site.theme_bg_image) {
      root.style.setProperty("--bg-image", `url('${site.theme_bg_image}')`);
    } else {
      root.style.removeProperty("--bg-image");
    }
  }, [site]);

  const logout = async () => {
    try {
      await api.post("/auth/logout");
    } catch (err) {
      // Ignore logout transport issues and clear local state anyway.
    }
    setUser(null);
    setSelectedNamespace("");
    navigate(userRootPath);
  };

  const switchNamespace = (nextNamespace) => {
    const next = normalizeNamespace(nextNamespace);
    if (!next) return;
    setError(null);
    setSelectedNamespace(next);
    navigate(withNamespacePath(location.pathname, next));
  };

  const authed = Boolean(user);
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
              {user.username} {canAccessAdmin ? `(${roleDisplay(user)})` : ""}
            </span>
            {canSwitchNamespace ? (
              <span className="namespace-switch">
                <span className="muted small">Namespace:</span>
                <select
                  value={activeNamespace}
                  onChange={(e) => switchNamespace(e.target.value)}
                  disabled={namespaceOptions.length < 2}
                >
                  {namespaceOptions.map((entry) => (
                    <option key={entry} value={entry}>
                      {entry}
                    </option>
                  ))}
                </select>
              </span>
            ) : (
              <span className="badge">Namespace: {namespaceLabel}</span>
            )}
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
          {error && <div className="error">Error: {error}</div>}
          <nav className="nav">
            <Link to={userRootPath}>User</Link>
            {canAccessAdmin && <Link to={adminRootPath}>Admin</Link>}
          </nav>
          <Routes key={activeNamespace || "unscoped"}>
            <Route path="/" element={<NamespaceDirectory namespaces={namespaceOptions} />} />
            <Route
              path="/ns/:namespace"
              element={
                <section className="card">
                  <UserPanel />
                </section>
              }
            />
            {canAccessAdmin && (
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
                  path="/ns/:namespace/admin"
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
                  path="/ns/:namespace/admin/users"
                  element={
                    <section className="card">
                      <AdminUsers />
                    </section>
                  }
                />
                <Route
                  path="/admin/scaling-quotas"
                  element={
                    <section className="card">
                      <AdminTeamQuotas />
                    </section>
                  }
                />
                <Route
                  path="/ns/:namespace/admin/scaling-quotas"
                  element={
                    <section className="card">
                      <AdminTeamQuotas />
                    </section>
                  }
                />
                <Route
                  path="/admin/team-quotas"
                  element={
                    <section className="card">
                      <AdminTeamQuotas />
                    </section>
                  }
                />
                <Route
                  path="/ns/:namespace/admin/team-quotas"
                  element={
                    <section className="card">
                      <AdminTeamQuotas />
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
                  path="/ns/:namespace/admin/templates"
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
                  path="/ns/:namespace/admin/images"
                  element={
                    <section className="card">
                      <AdminImages />
                    </section>
                  }
                />
                <Route
                  path="/admin/container-images"
                  element={
                    <section className="card">
                      <AdminContainerImages />
                    </section>
                  }
                />
                <Route
                  path="/ns/:namespace/admin/container-images"
                  element={
                    <section className="card">
                      <AdminContainerImages />
                    </section>
                  }
                />
                <Route
                  path="/admin/container-templates"
                  element={
                    <section className="card">
                      <AdminContainerTemplates />
                    </section>
                  }
                />
                <Route
                  path="/ns/:namespace/admin/container-templates"
                  element={
                    <section className="card">
                      <AdminContainerTemplates />
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
                  path="/ns/:namespace/admin/pods"
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
                  path="/ns/:namespace/admin/resources"
                  element={
                    <section className="card">
                      <AdminResources />
                    </section>
                  }
                />
                <Route
                  path="/admin/alerts-errors"
                  element={
                    <section className="card">
                      <AdminAlertsErrors />
                    </section>
                  }
                />
                <Route
                  path="/ns/:namespace/admin/alerts-errors"
                  element={
                    <section className="card">
                      <AdminAlertsErrors />
                    </section>
                  }
                />
                <Route
                  path="/admin/audit-events"
                  element={
                    <section className="card">
                      <AdminAuditEvents />
                    </section>
                  }
                />
                <Route
                  path="/ns/:namespace/admin/audit-events"
                  element={
                    <section className="card">
                      <AdminAuditEvents />
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
                  path="/ns/:namespace/admin/settings"
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
                  path="/ns/:namespace/admin/settings/appearance"
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
                  path="/ns/:namespace/admin/settings/storage"
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
                  path="/ns/:namespace/admin/settings/runtime"
                  element={
                    <section className="card">
                      <AdminRuntimeSettings />
                    </section>
                  }
                />
                <Route
                  path="/admin/settings/namespaces"
                  element={
                    <section className="card">
                      <AdminNamespacesSettings />
                    </section>
                  }
                />
                <Route
                  path="/ns/:namespace/admin/settings/namespaces"
                  element={
                    <section className="card">
                      <AdminNamespacesSettings />
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
                <Route
                  path="/ns/:namespace/admin/settings/sso"
                  element={
                    <section className="card">
                      <AdminSSOSettings />
                    </section>
                  }
                />
                <Route
                  path="/admin/settings/ldap"
                  element={
                    <section className="card">
                      <AdminLDAPSettings />
                    </section>
                  }
                />
                <Route
                  path="/ns/:namespace/admin/settings/ldap"
                  element={
                    <section className="card">
                      <AdminLDAPSettings />
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
