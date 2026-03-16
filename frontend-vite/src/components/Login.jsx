import React, { useEffect, useState } from "react";
import { api } from "../api";

const Login = ({ onLogin, user }) => {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [sso, setSso] = useState({ enabled: false, authorize_url: "" });

  useEffect(() => {
    const load = async () => {
      try {
        const res = await api.get("/user/settings/sso");
        setSso({
          enabled: res.data?.sso_enabled,
          authorize_url: res.data?.sso_authorize_url || "",
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

  const startSso = async () => {
    try {
      const returnTo = typeof window !== "undefined" ? `${window.location.origin}${window.location.pathname}` : "/";
      const res = await api.get("/auth/sso/start", { params: { return_to: returnTo } });
      const authorizeUrl = String(res?.data?.authorize_url || "").trim();
      if (!authorizeUrl) {
        setError("SSO authorize URL is missing.");
        return;
      }
      window.location.href = authorizeUrl;
    } catch (err) {
      setError(err.response?.data?.detail || "Failed to start SSO login.");
    }
  };

  return (
    <div>
      <h2>Login</h2>
      {user && (
        <p>
          Logged in as <strong>{user.username}</strong>{" "}
          {Boolean(user?.can_access_admin ?? user?.is_admin)
            ? `(${String(user.role || "admin").replace(/_/g, " ")})`
            : ""}
        </p>
      )}
      <form onSubmit={handleSubmit} className="form">
        <label>
          Username
          <input autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} />
        </label>
        <label>
          Password
          <input
            autoComplete="current-password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        <button type="submit">Sign In</button>
        {sso.enabled && (
          <button type="button" className="ghost" onClick={startSso}>
            Sign in with SSO
          </button>
        )}
        {error && <div className="error">{error}</div>}
      </form>
    </div>
  );
};

export default Login;
