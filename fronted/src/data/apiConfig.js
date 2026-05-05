const API_BASE = import.meta.env.VITE_API_BASE || ''

const isNgrok = API_BASE.includes('ngrok')

export const apiUrl = (path) => {
  const cleanPath = String(path).startsWith('/') ? path : `/${path}`
  if (API_BASE) {
    return `${API_BASE.replace(/\/$/, '')}${cleanPath}`
  }
  return cleanPath
}

const ngrokHeaders = () => (isNgrok ? { 'ngrok-skip-browser-warning': 'true' } : {})

export const apiFetch = (url, options = {}) => {
  const mergedHeaders = {
    Accept: 'application/json',
    ...ngrokHeaders(),
    ...(options.headers || {}),
  }
  return fetch(apiUrl(url), { ...options, headers: mergedHeaders })
}
