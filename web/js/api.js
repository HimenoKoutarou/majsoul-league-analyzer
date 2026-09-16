export function token() { return localStorage.getItem("admin_token") || ""; }
export function setToken(t) { localStorage.setItem("admin_token", t); }

export async function api(path, opts = {}) {
  const headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
  if (token() && path.startsWith("/api/admin")) headers["Authorization"] = `Bearer ${token()}`;
  const resp = await fetch(path, Object.assign({}, opts, { headers }));
  if (!resp.ok) {
    let msg = `HTTP ${resp.status}`;
    try { msg = (await resp.json()).detail || msg; } catch (e) { /* ignore */ }
    throw new Error(msg);
  }
  return resp.json();
}

export function toast(msg, ms = 2600) {
  const el = document.createElement("div");
  el.className = "toast";
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), ms);
}

export function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}