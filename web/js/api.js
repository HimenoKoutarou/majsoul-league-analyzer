export async function api(path, opts = {}) {
  const headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
  const resp = await fetch(path, Object.assign(
    { credentials: "same-origin" }, opts, { headers }));
  if (resp.status === 401 && path.startsWith("/api/admin")) {
    // 未登录或会话过期，跳登录页
    const next = encodeURIComponent(location.pathname + location.search);
    location.href = `/admin/login?next=${next}`;
    throw new Error("需要登录");
  }
  if (!resp.ok) {
    let msg = `HTTP ${resp.status}`;
    try { msg = (await resp.json()).detail || msg; } catch (e) { /* ignore */ }
    throw new Error(msg);
  }
  return resp.json();
}

export async function isLoggedIn() {
  try {
    const resp = await fetch("/api/admin/me", { credentials: "same-origin" });
    return resp.ok;
  } catch (e) {
    return false;
  }
}

export async function logout() {
  try {
    await fetch("/api/admin/logout", {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
    });
  } catch (e) { /* ignore */ }
  location.href = "/admin/login";
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
