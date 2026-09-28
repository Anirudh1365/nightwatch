// api.js -- shared helpers: login session, API calls, small formatting bits.
// Loaded by every page before its own script.

const NW = {
  // ---------- session (token + name + role + block, saved after login) ----------
  session() {
    try { return JSON.parse(localStorage.getItem("nw_session")); } catch { return null; }
  },
  save(s) { localStorage.setItem("nw_session", JSON.stringify(s)); },
  logout() {
    localStorage.removeItem("nw_session");
    location.href = "index.html";
  },

  // Which page each role lands on after login.
  homeFor(role) {
    return {
      student: "student.html",
      taker: "taker.html",
      guard: "guard.html",
      warden: "warden.html",
      chief: "warden.html",
    }[role] || "index.html";
  },

  // Call at the top of a page. Sends people to login if they're not allowed here.
  requireLogin(roles) {
    const s = NW.session();
    if (!s || !roles.includes(s.role)) {
      location.href = "index.html";
      throw new Error("not logged in");
    }
    return s;
  },

  // ---------- API calls ----------
  // Throws an Error with the server's message so pages can show it as-is.
  async api(path, { method = "GET", body } = {}) {
    const headers = {};
    const s = NW.session();
    if (s) headers.Authorization = "Bearer " + s.token;
    if (body !== undefined) headers["Content-Type"] = "application/json";

    let res;
    try {
      res = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
    } catch {
      throw new Error("Can't reach the server. Check your connection.");
    }
    const data = await res.json().catch(() => null);
    if (res.status === 401 && s) {           // token expired -> back to login
      NW.logout();
    }
    if (!res.ok) {
      let detail = data && data.detail;
      if (Array.isArray(detail)) detail = detail.map(d => d.msg).join(", ");  // form validation errors
      throw new Error(detail || "Something went wrong (" + res.status + ")");
    }
    return data;
  },

  // Fill a <select> with the blocks this staff member can open and return the chosen id.
  // Only the chief warden has more than one, so everyone else never sees the dropdown.
  // The choice is kept in the URL (?block=2) so links and reloads keep it.
  async blockPicker(select, onChange) {
    const blocks = await NW.api("/api/blocks");
    const wanted = Number(new URLSearchParams(location.search).get("block"));
    const current = blocks.find(b => b.id === wanted) || blocks[0];
    select.innerHTML = blocks.map(b => `<option value="${b.id}">${NW.esc(b.name)}</option>`).join("");
    select.value = current.id;
    select.classList.toggle("hidden", blocks.length < 2);
    select.onchange = () => {
      history.replaceState(null, "", "?block=" + select.value);
      onChange(Number(select.value));
    };
    return current.id;
  },

  // ---------- formatting ----------
  // Escape text before putting it into innerHTML.
  esc(v) {
    return String(v ?? "").replace(/[&<>"']/g, c =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  },
  // "2026-09-29T23:30:00+04:00" -> "23:30" (campus time, whatever the phone's timezone is)
  hhmm(iso) { return String(iso).slice(11, 16); },
  // "2026-09-29" -> "Tue 29 Sep"
  niceDate(d) {
    const [y, m, day] = String(d).split("-").map(Number);
    return new Date(y, m - 1, day).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
  },
  // Show a message in a .msg element. kind: error / ok / info. Empty text hides it.
  show(el, text, kind = "error") {
    el.textContent = text || "";
    el.className = "msg " + kind;
    el.classList.toggle("hidden", !text);
  },
};
