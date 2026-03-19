import React, { useEffect, useState } from "react";
import { api } from "../../api";

const ROLE_OPTIONS = [
  { value: "user", label: "User" },
  { value: "viewer", label: "Viewer" },
  { value: "image_manager", label: "Image Manager" },
  { value: "template_manager", label: "Template Manager" },
  { value: "lab_operator", label: "Lab Operator" },
  { value: "platform_admin", label: "Platform Admin" },
];

const DEFAULT_DATA = {
  sso_enabled: false,
  sso_provider: "",
  sso_client_id: "",
  sso_client_secret_configured: false,
  sso_authorize_url: "",
  sso_token_url: "",
  sso_userinfo_url: "",
  sso_redirect_url: "",
  sso_role_claim: "groups",
  sso_default_role: "user",
  sso_role_mappings: {},
  sso_auto_create_users: true,
  sso_sync_roles_on_login: true,
};

const formatRoleMappings = (mappings) => {
  if (!mappings || typeof mappings !== "object") {
    return "";
  }
  return Object.entries(mappings)
    .filter(([claim, role]) => String(claim || "").trim() && String(role || "").trim())
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([claim, role]) => `${claim}=${role}`)
    .join("\n");
};

const parseRoleMappings = (input) => {
  const mappings = {};
  const lines = String(input || "").split("\n");
  for (let idx = 0; idx < lines.length; idx += 1) {
    const line = String(lines[idx] || "").trim();
    if (!line || line.startsWith("#")) {
      continue;
    }
    const separatorIndex = line.indexOf("=");
    if (separatorIndex <= 0) {
      throw new Error(`Invalid role mapping on line ${idx + 1}. Use claim_value=role.`);
    }
    const claim = line.slice(0, separatorIndex).trim().toLowerCase();
    const role = line
      .slice(separatorIndex + 1)
      .trim()
      .toLowerCase();
    if (!claim || !role) {
      throw new Error(`Invalid role mapping on line ${idx + 1}. Use claim_value=role.`);
    }
    mappings[claim] = role;
  }
  return mappings;
};

const AdminSSOSettings = () => {
  const [data, setData] = useState(DEFAULT_DATA);
  const [mappingsInput, setMappingsInput] = useState("");
  const [secretInput, setSecretInput] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    const load = async () => {
      try {
        const res = await api.get("/admin/settings/sso");
        const payload = res.data || {};
        const roleMappings =
          payload.sso_role_mappings && typeof payload.sso_role_mappings === "object" ? payload.sso_role_mappings : {};
        setData({
          ...DEFAULT_DATA,
          ...payload,
          sso_role_mappings: roleMappings,
        });
        setMappingsInput(formatRoleMappings(roleMappings));
      } catch (err) {
        setError(err.response?.data?.detail || "Failed to load SSO settings");
      }
    };
    load();
  }, []);

  const save = async () => {
    setSaving(true);
    setError("");
    setMessage("");
    let roleMappings = {};
    try {
      roleMappings = parseRoleMappings(mappingsInput);
    } catch (err) {
      setError(err.message || "Invalid role mappings");
      setSaving(false);
      return;
    }
    const payload = {
      sso_enabled: data.sso_enabled,
      sso_provider: data.sso_provider,
      sso_client_id: data.sso_client_id,
      sso_authorize_url: data.sso_authorize_url,
      sso_token_url: data.sso_token_url,
      sso_userinfo_url: data.sso_userinfo_url,
      sso_redirect_url: data.sso_redirect_url,
      sso_role_claim: data.sso_role_claim,
      sso_default_role: data.sso_default_role,
      sso_role_mappings: roleMappings,
      sso_auto_create_users: data.sso_auto_create_users,
      sso_sync_roles_on_login: data.sso_sync_roles_on_login,
    };
    if (secretInput.trim()) {
      payload.sso_client_secret = secretInput;
    }
    try {
      const res = await api.patch("/admin/settings/sso", payload);
      const body = res.data || {};
      const normalizedRoleMappings =
        body.sso_role_mappings && typeof body.sso_role_mappings === "object" ? body.sso_role_mappings : {};
      setData({
        ...DEFAULT_DATA,
        ...body,
        sso_role_mappings: normalizedRoleMappings,
      });
      setMappingsInput(formatRoleMappings(normalizedRoleMappings));
      setSecretInput("");
      setMessage("SSO settings updated.");
    } catch (err) {
      setError(err.response?.data?.detail || "Failed to save SSO settings");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div>
      <h2>OIDC Single Sign-On</h2>
      <p className="muted small">
        Configure OIDC login and role mapping. SAML can be added later as a separate adapter.
      </p>
      {error && <div className="error">{error}</div>}
      <div className="card">
        <div className="form">
          <label>
            Enable OIDC SSO
            <select
              value={data.sso_enabled ? "true" : "false"}
              onChange={(e) => setData({ ...data, sso_enabled: e.target.value === "true" })}
            >
              <option value="false">Disabled</option>
              <option value="true">Enabled</option>
            </select>
          </label>
          <label>
            Provider Label
            <input
              value={data.sso_provider}
              onChange={(e) => setData({ ...data, sso_provider: e.target.value })}
              placeholder="Example: Okta, Azure AD, Keycloak"
            />
          </label>
          <label>
            Client ID
            <input value={data.sso_client_id} onChange={(e) => setData({ ...data, sso_client_id: e.target.value })} />
          </label>
          <label>
            Client Secret
            <input
              type="password"
              value={secretInput}
              placeholder={
                data.sso_client_secret_configured ? "Configured (leave blank to keep current)" : "Not configured"
              }
              onChange={(e) => setSecretInput(e.target.value)}
            />
          </label>
          <label>
            Authorization URL
            <input
              value={data.sso_authorize_url}
              onChange={(e) => setData({ ...data, sso_authorize_url: e.target.value })}
            />
          </label>
          <label>
            Token URL
            <input value={data.sso_token_url} onChange={(e) => setData({ ...data, sso_token_url: e.target.value })} />
          </label>
          <label>
            UserInfo URL
            <input
              value={data.sso_userinfo_url}
              onChange={(e) => setData({ ...data, sso_userinfo_url: e.target.value })}
            />
          </label>
          <label>
            Redirect URL
            <input
              value={data.sso_redirect_url}
              onChange={(e) => setData({ ...data, sso_redirect_url: e.target.value })}
            />
          </label>
          <label>
            Role Claim
            <input
              value={data.sso_role_claim}
              onChange={(e) => setData({ ...data, sso_role_claim: e.target.value })}
              placeholder="groups"
            />
          </label>
          <label>
            Default Role
            <select
              value={data.sso_default_role}
              onChange={(e) => setData({ ...data, sso_default_role: e.target.value })}
            >
              {ROLE_OPTIONS.map((roleOption) => (
                <option key={roleOption.value} value={roleOption.value}>
                  {roleOption.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            Auto-create users on first OIDC login
            <select
              value={data.sso_auto_create_users ? "true" : "false"}
              onChange={(e) => setData({ ...data, sso_auto_create_users: e.target.value === "true" })}
            >
              <option value="true">Enabled</option>
              <option value="false">Disabled</option>
            </select>
          </label>
          <label>
            Sync mapped role on every OIDC login
            <select
              value={data.sso_sync_roles_on_login ? "true" : "false"}
              onChange={(e) => setData({ ...data, sso_sync_roles_on_login: e.target.value === "true" })}
            >
              <option value="true">Enabled</option>
              <option value="false">Disabled</option>
            </select>
          </label>
          <label>
            Role Mappings
            <textarea
              rows={8}
              value={mappingsInput}
              onChange={(e) => setMappingsInput(e.target.value)}
              placeholder={"admins=platform_admin\nops=lab_operator\nviewers=viewer"}
            />
          </label>
          <p className="muted small">
            One mapping per line. Format: <code>claim_value=role</code>. The highest mapped role wins when multiple
            values match.
          </p>
          <div className="actions">
            <button onClick={save} disabled={saving}>
              {saving ? "Saving…" : "Save"}
            </button>
          </div>
          {message && <div className="info">{message}</div>}
        </div>
      </div>
    </div>
  );
};

export default AdminSSOSettings;
