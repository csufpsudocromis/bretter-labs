import React, { useEffect, useState } from "react";
import { api } from "../../api";

const DEFAULT_TEAM = "default";

const emptyForm = {
  team: DEFAULT_TEAM,
  namespace: "labs",
  max_concurrent_labs: "",
  max_cpu_millicores: "",
  max_memory_mb: "",
  max_storage_gib: "",
  idle_timeout_minutes_cap: "",
  enabled: true,
};

const normalizeNumber = (value) => {
  const raw = String(value ?? "").trim();
  if (!raw) return null;
  const parsed = Number(raw);
  return Number.isFinite(parsed) && parsed > 0 ? Math.floor(parsed) : null;
};

const AdminTeamQuotas = () => {
  const [quotas, setQuotas] = useState([]);
  const [namespaces, setNamespaces] = useState(["labs"]);
  const [teams, setTeams] = useState([DEFAULT_TEAM]);
  const [form, setForm] = useState({ ...emptyForm });
  const [editingId, setEditingId] = useState("");
  const [message, setMessage] = useState("");
  const [saving, setSaving] = useState(false);

  const load = async () => {
    let loadedQuotas = [];
    try {
      const res = await api.get("/admin/team-quotas");
      loadedQuotas = res.data || [];
      setQuotas(loadedQuotas);
    } catch (err) {
      setMessage(err.response?.data?.detail || "Failed to load team quotas");
    }
    try {
      const res = await api.get("/admin/quota-namespaces");
      const values = Array.isArray(res.data) ? res.data : [];
      const merged = new Set(["labs", String(form.namespace || "").trim() || "labs"]);
      values.forEach((item) =>
        merged.add(
          String(item || "")
            .trim()
            .toLowerCase()
        )
      );
      loadedQuotas.forEach((row) =>
        merged.add(
          String(row.namespace || "")
            .trim()
            .toLowerCase()
        )
      );
      setNamespaces(Array.from(merged).filter(Boolean).sort());
    } catch {
      const merged = new Set(["labs", String(form.namespace || "").trim() || "labs"]);
      loadedQuotas.forEach((row) =>
        merged.add(
          String(row.namespace || "")
            .trim()
            .toLowerCase()
        )
      );
      setNamespaces(Array.from(merged).filter(Boolean).sort());
    }
    try {
      const res = await api.get("/admin/quota-teams");
      const values = Array.isArray(res.data) ? res.data : [];
      const merged = new Set([
        DEFAULT_TEAM,
        String(form.team || "")
          .trim()
          .toLowerCase() || DEFAULT_TEAM,
      ]);
      values.forEach((item) =>
        merged.add(
          String(item || "")
            .trim()
            .toLowerCase()
        )
      );
      loadedQuotas.forEach((row) =>
        merged.add(
          String(row.team || "")
            .trim()
            .toLowerCase()
        )
      );
      setTeams(Array.from(merged).filter(Boolean).sort());
    } catch {
      const merged = new Set([
        DEFAULT_TEAM,
        String(form.team || "")
          .trim()
          .toLowerCase() || DEFAULT_TEAM,
      ]);
      loadedQuotas.forEach((row) =>
        merged.add(
          String(row.team || "")
            .trim()
            .toLowerCase()
        )
      );
      setTeams(Array.from(merged).filter(Boolean).sort());
    }
  };

  useEffect(() => {
    load();
  }, []);

  const updateField = (key, value) => setForm((prev) => ({ ...prev, [key]: value }));

  const startCreate = () => {
    setEditingId("");
    setForm({ ...emptyForm });
    setMessage("");
  };

  const startEdit = (row) => {
    const team =
      String(row.team || DEFAULT_TEAM)
        .trim()
        .toLowerCase() || DEFAULT_TEAM;
    const namespace = row.namespace || "labs";
    setTeams((prev) => {
      const merged = new Set([...(prev || []), team]);
      return Array.from(merged).filter(Boolean).sort();
    });
    setNamespaces((prev) => {
      const merged = new Set([...(prev || []), namespace]);
      return Array.from(merged).filter(Boolean).sort();
    });
    setEditingId(row.id);
    setForm({
      team,
      namespace,
      max_concurrent_labs: row.max_concurrent_labs ?? "",
      max_cpu_millicores: row.max_cpu_millicores ?? "",
      max_memory_mb: row.max_memory_mb ?? "",
      max_storage_gib: row.max_storage_gib ?? "",
      idle_timeout_minutes_cap: row.idle_timeout_minutes_cap ?? "",
      enabled: Boolean(row.enabled),
    });
    setMessage("");
  };

  const buildPayload = (forUpdate = false) => {
    const normalizedTeam =
      String(form.team || "")
        .trim()
        .toLowerCase() || DEFAULT_TEAM;
    const payload = {
      team: normalizedTeam,
      namespace: String(form.namespace || "").trim() || "labs",
      enabled: Boolean(form.enabled),
    };
    const maxConcurrent = normalizeNumber(form.max_concurrent_labs);
    const maxCpu = normalizeNumber(form.max_cpu_millicores);
    const maxMem = normalizeNumber(form.max_memory_mb);
    const maxStorage = normalizeNumber(form.max_storage_gib);
    const idleCap = normalizeNumber(form.idle_timeout_minutes_cap);
    if (forUpdate) {
      if (String(form.max_concurrent_labs).trim()) payload.max_concurrent_labs = maxConcurrent;
      else payload.clear_max_concurrent_labs = true;
      if (String(form.max_cpu_millicores).trim()) payload.max_cpu_millicores = maxCpu;
      else payload.clear_max_cpu_millicores = true;
      if (String(form.max_memory_mb).trim()) payload.max_memory_mb = maxMem;
      else payload.clear_max_memory_mb = true;
      if (String(form.max_storage_gib).trim()) payload.max_storage_gib = maxStorage;
      else payload.clear_max_storage_gib = true;
      if (String(form.idle_timeout_minutes_cap).trim()) payload.idle_timeout_minutes_cap = idleCap;
      else payload.clear_idle_timeout_minutes_cap = true;
      return payload;
    }
    payload.max_concurrent_labs = maxConcurrent;
    payload.max_cpu_millicores = maxCpu;
    payload.max_memory_mb = maxMem;
    payload.max_storage_gib = maxStorage;
    payload.idle_timeout_minutes_cap = idleCap;
    return payload;
  };

  const save = async () => {
    const team = String(form.team || "").trim();
    const namespace = String(form.namespace || "").trim();
    if (!team) {
      setMessage("Team is required");
      return;
    }
    if (!namespace) {
      setMessage("Namespace is required");
      return;
    }
    setSaving(true);
    try {
      if (editingId) {
        await api.patch(`/admin/team-quotas/${editingId}`, buildPayload(true));
        setMessage("Quota updated");
      } else {
        await api.post("/admin/team-quotas", buildPayload(false));
        setMessage("Quota created");
      }
      setEditingId("");
      setForm({ ...emptyForm, team: team.toLowerCase(), namespace: namespace || "labs" });
      await load();
    } catch (err) {
      setMessage(err.response?.data?.detail || "Failed to save team quota");
    } finally {
      setSaving(false);
    }
  };

  const remove = async (id) => {
    setSaving(true);
    try {
      await api.delete(`/admin/team-quotas/${id}`);
      if (editingId === id) {
        setEditingId("");
        setForm({ ...emptyForm });
      }
      setMessage("Quota deleted");
      await load();
    } catch (err) {
      setMessage(err.response?.data?.detail || "Failed to delete team quota");
    } finally {
      setSaving(false);
    }
  };

  const limitLabel = (value, suffix = "") => {
    if (!value) return "Unlimited";
    return `${value}${suffix}`;
  };

  return (
    <div>
      <h2>Scaling &amp; Quotas</h2>
      <p>Apply namespace limits for max concurrent labs, CPU/RAM caps, storage caps, and idle timeout caps.</p>
      {message && <div className="info">{message}</div>}
      <div className="grid">
        <div>
          <h3>{editingId ? "Edit quota" : "Create quota"}</h3>
          <div className="form">
            <label>
              Team
              <input
                list="quota-teams"
                value={form.team}
                onChange={(e) => updateField("team", e.target.value)}
                placeholder="default"
              />
              <datalist id="quota-teams">
                {teams.map((teamName) => (
                  <option key={teamName} value={teamName} />
                ))}
              </datalist>
            </label>
            <label>
              Namespace
              <select value={form.namespace} onChange={(e) => updateField("namespace", e.target.value)}>
                {namespaces.map((namespace) => (
                  <option key={namespace} value={namespace}>
                    {namespace}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Max concurrent labs
              <input
                type="number"
                min="1"
                value={form.max_concurrent_labs}
                onChange={(e) => updateField("max_concurrent_labs", e.target.value)}
                placeholder="Unlimited"
              />
            </label>
            <label>
              CPU cap (millicores)
              <input
                type="number"
                min="100"
                value={form.max_cpu_millicores}
                onChange={(e) => updateField("max_cpu_millicores", e.target.value)}
                placeholder="Unlimited"
              />
            </label>
            <label>
              RAM cap (MB)
              <input
                type="number"
                min="128"
                value={form.max_memory_mb}
                onChange={(e) => updateField("max_memory_mb", e.target.value)}
                placeholder="Unlimited"
              />
            </label>
            <label>
              Storage cap (GiB)
              <input
                type="number"
                min="1"
                value={form.max_storage_gib}
                onChange={(e) => updateField("max_storage_gib", e.target.value)}
                placeholder="Unlimited"
              />
            </label>
            <label>
              Idle timeout cap (minutes)
              <input
                type="number"
                min="1"
                max="1440"
                value={form.idle_timeout_minutes_cap}
                onChange={(e) => updateField("idle_timeout_minutes_cap", e.target.value)}
                placeholder="No cap"
              />
            </label>
            <label>
              Enabled
              <select
                value={form.enabled ? "yes" : "no"}
                onChange={(e) => updateField("enabled", e.target.value === "yes")}
              >
                <option value="yes">Enabled</option>
                <option value="no">Disabled</option>
              </select>
            </label>
            <div className="actions">
              {editingId && (
                <button type="button" className="ghost" onClick={startCreate}>
                  Cancel
                </button>
              )}
              <button type="button" onClick={save} disabled={saving}>
                {editingId ? "Save quota" : "Add quota"}
              </button>
            </div>
          </div>
        </div>
        <div>
          <h3>Existing quotas</h3>
          <div className="tile-grid">
            {quotas.length === 0 && <div className="muted">No quotas configured.</div>}
            {quotas.map((row) => (
              <div key={row.id} className="tile">
                <div className="tile-header">
                  <h4>{row.namespace}</h4>
                  <span className={`badge ${row.enabled ? "success" : "warn"}`}>
                    {row.enabled ? "Enabled" : "Disabled"}
                  </span>
                </div>
                <div className="small muted">Team: {row.team || DEFAULT_TEAM}</div>
                <div className="small muted">Max labs: {limitLabel(row.max_concurrent_labs)}</div>
                <div className="small muted">CPU cap: {limitLabel(row.max_cpu_millicores, "m")}</div>
                <div className="small muted">RAM cap: {limitLabel(row.max_memory_mb, " MB")}</div>
                <div className="small muted">Storage cap: {limitLabel(row.max_storage_gib, " GiB")}</div>
                <div className="small muted">Idle cap: {limitLabel(row.idle_timeout_minutes_cap, " min")}</div>
                <div className="actions">
                  <button type="button" className="ghost" onClick={() => startEdit(row)}>
                    Edit
                  </button>
                  <button type="button" className="danger" onClick={() => remove(row.id)} disabled={saving}>
                    Delete
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
};

export default AdminTeamQuotas;
