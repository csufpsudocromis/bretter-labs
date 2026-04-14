import axios from "axios";

const defaultApiBase = typeof window !== "undefined" ? `${window.location.origin}/api` : "http://127.0.0.1/api";

export const AUTH_INVALID_EVENT = "blabs-auth-invalid";
export const NAMESPACE_FORBIDDEN_EVENT = "blabs-namespace-forbidden";

export const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE || defaultApiBase,
  withCredentials: true,
});

const namespaceFromPath = () => {
  if (typeof window === "undefined") return "";
  const match = window.location.pathname.match(/^\/(?:ns|namespace)\/([^/]+)/i);
  if (!match || !match[1]) return "";
  try {
    return decodeURIComponent(match[1]).trim();
  } catch (err) {
    return String(match[1] || "").trim();
  }
};

api.interceptors.request.use((config) => {
  const namespace = namespaceFromPath();
  if (!namespace) {
    const explicitNamespace =
      (config.headers && typeof config.headers.get === "function"
        ? config.headers.get("X-Bretter-Namespace") || config.headers.get("x-bretter-namespace")
        : config.headers?.["X-Bretter-Namespace"] || config.headers?.["x-bretter-namespace"]) || "";
    if (String(explicitNamespace).trim()) {
      return config;
    }
    if (config.headers && typeof config.headers.delete === "function") {
      config.headers.delete("X-Bretter-Namespace");
      config.headers.delete("x-bretter-namespace");
    } else if (config.headers) {
      delete config.headers["X-Bretter-Namespace"];
      delete config.headers["x-bretter-namespace"];
    }
    return config;
  }
  if (config.headers && typeof config.headers.set === "function") {
    config.headers.set("X-Bretter-Namespace", namespace);
  } else {
    config.headers = {
      ...(config.headers || {}),
      "X-Bretter-Namespace": namespace,
    };
  }
  return config;
});

const namespaceFromRequestConfig = (config) => {
  const headers = config?.headers;
  if (!headers) return "";
  if (typeof headers.get === "function") {
    return String(headers.get("X-Bretter-Namespace") || headers.get("x-bretter-namespace") || "").trim();
  }
  return String(headers["X-Bretter-Namespace"] || headers["x-bretter-namespace"] || "").trim();
};

api.interceptors.response.use(
  (response) => response,
  (error) => {
    const status = error?.response?.status;
    const detail = String(error?.response?.data?.detail || "").toLowerCase();
    const url = String(error?.config?.url || "");
    const isLoginCall = url.includes("/auth/login");
    const tokenError =
      detail.includes("invalid token") ||
      detail.includes("invalid connect token") ||
      detail.includes("invalid connect session") ||
      detail.includes("missing authorization token") ||
      detail.includes("missing authorization header") ||
      detail.includes("invalid authorization header");

    if (!isLoginCall && status === 401 && tokenError && typeof window !== "undefined") {
      window.dispatchEvent(
        new CustomEvent(AUTH_INVALID_EVENT, {
          detail: { message: "Session expired. Please sign in again." },
        })
      );
    }
    if (status === 403 && typeof window !== "undefined") {
      const namespace = namespaceFromPath() || namespaceFromRequestConfig(error?.config);
      const fallback = namespace
        ? `Access denied for namespace "${namespace}".`
        : "Access denied for the requested namespace or action.";
      window.dispatchEvent(
        new CustomEvent(NAMESPACE_FORBIDDEN_EVENT, {
          detail: { message: error?.response?.data?.detail || fallback, namespace },
        })
      );
    }
    return Promise.reject(error);
  }
);
