import axios from "axios";

const defaultApiBase = typeof window !== "undefined" ? `${window.location.origin}/api` : "http://127.0.0.1/api";

export const AUTH_INVALID_EVENT = "blabs-auth-invalid";

export const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE || defaultApiBase,
  withCredentials: true,
});

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
    return Promise.reject(error);
  }
);
