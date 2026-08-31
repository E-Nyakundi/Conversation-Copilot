// Talks directly to the Django server's /api/admin/ and /api/auth/
// endpoints (accounts/admin_views.py, accounts/views.py). No separate
// backend of its own — this app is a thin authenticated client.
//
// API_BASE is read at runtime from window (not build-time env) so the
// same static build can point at different deployments without a
// rebuild — set it in a small config script tag, see layout.tsx.

export function getApiBase() {
  if (typeof window !== "undefined" && window.__COPILOT_ADMIN_API_BASE__) {
    return window.__COPILOT_ADMIN_API_BASE__;
  }
  return process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";
}

const TOKEN_KEY = "copilot_admin_token";

export function getToken() {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function setToken(token) {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken() {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(TOKEN_KEY);
}

class ApiError extends Error {
  constructor(message, status, reason) {
    super(message);
    this.status = status;
    this.reason = reason;
  }
}

async function request(path, { method = "GET", body, authed = true } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (authed) {
    const token = getToken();
    if (token) headers["Authorization"] = `Bearer ${token}`;
  }
  const res = await fetch(`${getApiBase()}${path}`, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });

  let data = null;
  try {
    data = await res.json();
  } catch (_) {
    // no body
  }

  if (!res.ok) {
    if (res.status === 401 && authed) clearToken();
    throw new ApiError(data?.error || `Request failed (${res.status})`, res.status, data?.reason);
  }
  return data;
}

export const api = {
  login: (email, password) =>
    request("/api/auth/login/", { method: "POST", body: { email, password }, authed: false }),
  changePassword: (new_password) =>
    request("/api/auth/change-password/", { method: "POST", body: { new_password } }),
  me: () => request("/api/auth/me/"),

  listUsers: () => request("/api/admin/users/"),
  inviteUser: (email, max_sessions, max_minutes) =>
    request("/api/admin/users/", {
      method: "POST",
      body: { email, max_sessions, max_minutes },
    }),
  userDetail: (id) => request(`/api/admin/users/${id}/`),
  adjustQuota: (id, patch) =>
    request(`/api/admin/users/${id}/quota/`, { method: "PATCH", body: patch }),
  revoke: (id) => request(`/api/admin/users/${id}/revoke/`, { method: "POST" }),
  reactivate: (id) => request(`/api/admin/users/${id}/reactivate/`, { method: "POST" }),
  deleteUser: (id) => request(`/api/admin/users/${id}/delete/`, { method: "DELETE" }),
};

export { ApiError };
