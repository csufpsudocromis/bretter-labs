import axios from 'axios';

const defaultApiBase = '/api';

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
