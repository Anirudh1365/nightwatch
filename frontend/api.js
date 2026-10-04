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

  // Download a file (the CSV reports). A plain link can't send the login token,
  // so fetch it with the token and hand the browser the result.
  async download(path) {
    const s = NW.session();
    let res;
    try {
      res = await fetch(path, { headers: s ? { Authorization: "Bearer " + s.token } : {} });
    } catch {
      throw new Error("Can't reach the server. Check your connection.");
    }
    if (!res.ok) {
      const data = await res.json().catch(() => null);
      throw new Error((data && data.detail) || "Download failed (" + res.status + ")");
    }
    const name = (res.headers.get("Content-Disposition") || "").match(/filename="([^"]+)"/);
    const url = URL.createObjectURL(await res.blob());
    const a = document.createElement("a");
    a.href = url;
    a.download = name ? name[1] : "nightwatch.csv";
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
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
  // "2026-09-30" -> "30/09/26" (DD/MM/YY everywhere)
  niceDate(d) {
    const [y, m, day] = String(d).split("-");
    return `${day}/${m}/${y.slice(2)}`;
  },
  // Show a message in a .msg element. kind: error / ok / info. Empty text hides it.
  show(el, text, kind = "error") {
    el.textContent = text || "";
    el.className = "msg " + kind;
    el.classList.toggle("hidden", !text);
  },
};

// Home icon at the top left of every page with a top bar. It goes to *your* dashboard,
// so a warden who opened Rounds (the taker page) can get back to the warden page.
(function addHomeButton() {
  const bar = document.querySelector(".topbar");
  const s = NW.session();
  if (!bar || !s) return;
  const a = document.createElement("a");
  a.className = "home-btn";
  a.href = NW.homeFor(s.role);
  a.title = "Home";
  a.setAttribute("aria-label", "Home");
  a.innerHTML = '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V20a1 1 0 0 0 1 1h4v-6h4v6h4a1 1 0 0 0 1-1V9.5"/></svg>';
  bar.prepend(a);
})();
