import React, { useEffect, useState } from "react";
import { api } from "../../api";

const AdminSSOSettings = () => {
  const [data, setData] = useState({
    sso_enabled: false,
    sso_provider: "",
    sso_client_id: "",
    sso_client_secret_configured: false,
    sso_authorize_url: "",
    sso_token_url: "",
    sso_userinfo_url: "",
    sso_redirect_url: "",
  });
  const [secretInput, setSecretInput] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    const load = async () => {
      try {
        const res = await api.get("/admin/settings/sso");
        setData(res.data || {});
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
    const payload = {
      sso_enabled: data.sso_enabled,
      sso_provider: data.sso_provider,
      sso_client_id: data.sso_client_id,
      sso_authorize_url: data.sso_authorize_url,
      sso_token_url: data.sso_token_url,
      sso_userinfo_url: data.sso_userinfo_url,
      sso_redirect_url: data.sso_redirect_url,
    };
    if (secretInput.trim()) {
      payload.sso_client_secret = secretInput;
    }
    try {
      const res = await api.patch("/admin/settings/sso", payload);
      setData(res.data || {});
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
      <h2>Single Sign-On (SSO)</h2>
      <p className="muted small">Configure SSO provider details. When enabled, users can choose “Sign in with SSO”.</p>
      {error && <div className="error">{error}</div>}
      <div className="card">
        <div className="form">
          <label>
            Enable SSO
            <select
              value={data.sso_enabled ? "true" : "false"}
              onChange={(e) => setData({ ...data, sso_enabled: e.target.value === "true" })}
            >
              <option value="false">Disabled</option>
              <option value="true">Enabled</option>
            </select>
          </label>
          <label>
            Provider Name
            <input value={data.sso_provider} onChange={(e) => setData({ ...data, sso_provider: e.target.value })} />
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
