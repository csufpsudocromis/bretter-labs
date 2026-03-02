import axios from 'axios';

const defaultApiBase =
  typeof window !== 'undefined'
    ? `${window.location.protocol}//${window.location.hostname}:30080`
    : 'https://127.0.0.1:30080';

export const AUTH_INVALID_EVENT = 'blabs-auth-invalid';

export const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE || defaultApiBase,
});

api.interceptors.request.use((config) => {
  // Inject bearer token from localStorage if present.
  const token = localStorage.getItem('blabs_token');
  if (token) {
    config.headers = config.headers || {};
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

api.interceptors.response.use(
  (response) => response,
  (error) => {
    const status = error?.response?.status;
    const detail = String(error?.response?.data?.detail || '').toLowerCase();
    const url = String(error?.config?.url || '');
    const isLoginCall = url.includes('/auth/login');
    const tokenError =
      detail.includes('invalid token') ||
      detail.includes('missing authorization header') ||
      detail.includes('invalid authorization header');

    if (!isLoginCall && status === 401 && tokenError && typeof window !== 'undefined') {
      try {
        localStorage.removeItem('blabs_token');
        localStorage.removeItem('blabs_user');
      } catch (e) {
        // ignore storage errors
      }
      window.dispatchEvent(
        new CustomEvent(AUTH_INVALID_EVENT, {
          detail: { message: 'Session expired. Please sign in again.' },
        }),
      );
    }
    return Promise.reject(error);
  },
);
