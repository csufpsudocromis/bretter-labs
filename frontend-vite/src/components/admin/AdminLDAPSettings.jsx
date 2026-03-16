import React, { useEffect, useState } from 'react';
import { api } from '../../api';

const DEFAULTS = {
  ldap_enabled: false,
  ldap_server_uri: '',
  ldap_bind_dn: '',
  ldap_bind_password_configured: false,
  ldap_user_base_dn: '',
  ldap_user_filter: '(uid={username})',
  ldap_start_tls: false,
  ldap_insecure_skip_verify: false,
  ldap_timeout_seconds: 10,
  ldap_auto_create_users: true,
};

const AdminLDAPSettings = () => {
  const [data, setData] = useState({ ...DEFAULTS });
  const [bindPasswordInput, setBindPasswordInput] = useState('');
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    const load = async () => {
      try {
        const res = await api.get('/admin/settings/ldap');
        setData({ ...DEFAULTS, ...(res.data || {}) });
      } catch (err) {
        setError(err.response?.data?.detail || 'Failed to load LDAP settings');
      }
    };
    load();
  }, []);

  const save = async () => {
    setSaving(true);
    setError('');
    setMessage('');
    const payload = {
      ldap_enabled: data.ldap_enabled,
      ldap_server_uri: data.ldap_server_uri,
      ldap_bind_dn: data.ldap_bind_dn,
      ldap_user_base_dn: data.ldap_user_base_dn,
      ldap_user_filter: data.ldap_user_filter,
      ldap_start_tls: data.ldap_start_tls,
      ldap_insecure_skip_verify: data.ldap_insecure_skip_verify,
      ldap_timeout_seconds: Number(data.ldap_timeout_seconds || 10),
      ldap_auto_create_users: data.ldap_auto_create_users,
    };
    if (bindPasswordInput.trim()) {
      payload.ldap_bind_password = bindPasswordInput;
    }
    try {
      const res = await api.patch('/admin/settings/ldap', payload);
      setData({ ...DEFAULTS, ...(res.data || {}) });
      setBindPasswordInput('');
      setMessage('LDAP settings updated.');
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to save LDAP settings');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div>
      <h2>LDAP Authentication</h2>
      <p className="muted small">
        Configure LDAP bind/search authentication. When enabled, login will attempt local auth first, then LDAP.
      </p>
      {error && <div className="error">{error}</div>}
      <div className="card">
        <div className="form">
          <label>
            Enable LDAP
            <select
              value={data.ldap_enabled ? 'true' : 'false'}
              onChange={(e) => setData({ ...data, ldap_enabled: e.target.value === 'true' })}
            >
              <option value="false">Disabled</option>
              <option value="true">Enabled</option>
            </select>
          </label>

          <label>
            LDAP Server URI
            <input
              placeholder="ldaps://ldap.example.edu:636"
              value={data.ldap_server_uri}
              onChange={(e) => setData({ ...data, ldap_server_uri: e.target.value })}
            />
          </label>

          <label>
            Bind DN (service account)
            <input
              placeholder="cn=svc-ldap,ou=service,dc=example,dc=edu"
              value={data.ldap_bind_dn}
              onChange={(e) => setData({ ...data, ldap_bind_dn: e.target.value })}
            />
          </label>

          <label>
            Bind Password
            <input
              type="password"
              value={bindPasswordInput}
              placeholder={data.ldap_bind_password_configured ? 'Configured (leave blank to keep current)' : 'Not configured'}
              onChange={(e) => setBindPasswordInput(e.target.value)}
            />
          </label>

          <label>
            User Base DN
            <input
              placeholder="ou=users,dc=example,dc=edu"
              value={data.ldap_user_base_dn}
              onChange={(e) => setData({ ...data, ldap_user_base_dn: e.target.value })}
            />
          </label>

          <label>
            User Search Filter
            <input
              value={data.ldap_user_filter}
              onChange={(e) => setData({ ...data, ldap_user_filter: e.target.value })}
            />
            <small className="muted">Must include {'{username}'} placeholder. Example: (uid={'{username}'})</small>
          </label>

          <label>
            StartTLS
            <select
              value={data.ldap_start_tls ? 'true' : 'false'}
              onChange={(e) => setData({ ...data, ldap_start_tls: e.target.value === 'true' })}
            >
              <option value="false">Disabled</option>
              <option value="true">Enabled</option>
            </select>
          </label>

          <label>
            Skip TLS certificate verification
            <select
              value={data.ldap_insecure_skip_verify ? 'true' : 'false'}
              onChange={(e) => setData({ ...data, ldap_insecure_skip_verify: e.target.value === 'true' })}
            >
              <option value="false">No</option>
              <option value="true">Yes (insecure)</option>
            </select>
          </label>

          <label>
            LDAP timeout (seconds)
            <input
              type="number"
              min={3}
              max={60}
              value={data.ldap_timeout_seconds}
              onChange={(e) => setData({ ...data, ldap_timeout_seconds: e.target.value })}
            />
          </label>

          <label>
            Auto-create local users on first LDAP login
            <select
              value={data.ldap_auto_create_users ? 'true' : 'false'}
              onChange={(e) => setData({ ...data, ldap_auto_create_users: e.target.value === 'true' })}
            >
              <option value="true">Enabled</option>
              <option value="false">Disabled</option>
            </select>
          </label>

          <div className="actions">
            <button onClick={save} disabled={saving}>
              {saving ? 'Saving…' : 'Save'}
            </button>
          </div>
          {message && <div className="info">{message}</div>}
        </div>
      </div>
    </div>
  );
};

export default AdminLDAPSettings;
