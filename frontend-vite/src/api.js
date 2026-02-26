import axios from 'axios';

const defaultApiBase =
  typeof window !== 'undefined'
    ? `${window.location.protocol}//${window.location.hostname}:30080`
    : 'https://127.0.0.1:30080';

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
