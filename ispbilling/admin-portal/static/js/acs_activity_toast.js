/* Phase-C — Provisioning Activity Toast Widget
 * Subscribes to /api/admin/acs/activity/stream (SSE) and pops live toasts
 * for ACS push, ZTP discovery, RPC reboot/factory-reset, and outage events.
 * Auto-reconnects with exponential back-off. Persists last 50 events
 * in a slide-in sidebar drawer.
 */
(function () {
  if (window.__acsActivityToastLoaded__) return;
  window.__acsActivityToastLoaded__ = true;

  const ENDPOINT = "/api/admin/acs/activity/stream";
  const RECENT_ENDPOINT = "/api/admin/acs/activity/recent?limit=50";
  const MAX_HISTORY = 50;
  const TOAST_TTL_MS = 6000;

  // ───────────────────────────────────────────────────────────────
  // Style injection (kept inline so no template needs editing)
  // ───────────────────────────────────────────────────────────────
  const css = `
    .acs-toast-container { position:fixed; right:18px; bottom:18px; z-index:9999;
        display:flex; flex-direction:column; gap:8px; pointer-events:none;
        max-width:380px; font-family: ui-sans-serif, system-ui, -apple-system,
        "Segoe UI", Roboto, sans-serif; }
    .acs-toast { background:#0f172a; color:#e2e8f0; border-left:4px solid #38bdf8;
        padding:10px 14px; border-radius:8px; box-shadow:0 10px 25px rgba(0,0,0,.25);
        font-size:13px; line-height:1.35; pointer-events:auto;
        animation:acs-toast-in .35s cubic-bezier(.2,.8,.2,1); }
    .acs-toast.kind-acs_push.ok    { border-left-color:#10b981; }
    .acs-toast.kind-acs_push.fail  { border-left-color:#ef4444; }
    .acs-toast.kind-ztp_discovery  { border-left-color:#a78bfa; }
    .acs-toast.kind-outage         { border-left-color:#f97316;
                                     background:#451a03; color:#fed7aa; }
    .acs-toast .acs-toast-meta { color:#94a3b8; font-size:11px; margin-top:2px; }
    .acs-toast .acs-toast-close { float:right; cursor:pointer; opacity:.6;
                                  padding-left:8px; font-weight:700; }
    .acs-toast .acs-toast-close:hover { opacity:1; }
    @keyframes acs-toast-in {
      from { transform: translateY(20px); opacity:0; }
      to   { transform: translateY(0);    opacity:1; }
    }
    .acs-activity-fab { position:fixed; right:18px; bottom:18px; z-index:9998;
        background:#0f172a; color:#fff; border:none; border-radius:999px;
        padding:10px 16px; font-size:13px; cursor:pointer;
        box-shadow:0 8px 22px rgba(0,0,0,.25); display:none; }
    .acs-activity-fab:hover { background:#1e293b; }
    .acs-activity-fab .badge { background:#ef4444; padding:2px 6px;
        margin-left:6px; border-radius:9px; font-size:11px; }
    .acs-drawer { position:fixed; right:0; top:0; height:100vh; width:380px;
        background:#0f172a; color:#e2e8f0; box-shadow:-10px 0 30px rgba(0,0,0,.3);
        z-index:10000; transform: translateX(100%); transition: transform .25s ease;
        display:flex; flex-direction:column; }
    .acs-drawer.open { transform: translateX(0); }
    .acs-drawer-header { padding:14px 16px; border-bottom:1px solid #1e293b;
        font-weight:600; display:flex; justify-content:space-between; align-items:center; }
    .acs-drawer-list { overflow-y:auto; flex:1; padding:8px 12px; }
    .acs-drawer-item { padding:8px 6px; border-bottom:1px solid #1e293b;
        font-size:12px; }
    .acs-drawer-item .acs-meta { color:#64748b; font-size:11px; }
  `;
  const styleEl = document.createElement("style");
  styleEl.textContent = css;
  document.head.appendChild(styleEl);

  // ───────────────────────────────────────────────────────────────
  // DOM scaffolding
  // ───────────────────────────────────────────────────────────────
  const toastBox = document.createElement("div");
  toastBox.className = "acs-toast-container";
  toastBox.setAttribute("data-testid", "acs-toast-container");
  document.body.appendChild(toastBox);

  const fab = document.createElement("button");
  fab.className = "acs-activity-fab";
  fab.setAttribute("data-testid", "acs-activity-fab");
  fab.innerHTML = `Activity <span class="badge" data-testid="acs-fab-badge">0</span>`;
  document.body.appendChild(fab);

  const drawer = document.createElement("aside");
  drawer.className = "acs-drawer";
  drawer.setAttribute("data-testid", "acs-activity-drawer");
  drawer.innerHTML = `
    <div class="acs-drawer-header">
      <span>Recent Activity</span>
      <span style="cursor:pointer;" data-testid="acs-drawer-close">×</span>
    </div>
    <div class="acs-drawer-list" data-testid="acs-drawer-list"></div>`;
  document.body.appendChild(drawer);

  const drawerList = drawer.querySelector(".acs-drawer-list");
  drawer.querySelector('[data-testid="acs-drawer-close"]').onclick =
        () => drawer.classList.remove("open");
  fab.onclick = () => {
    drawer.classList.add("open");
    badgeCount = 0; updateFab();
    refreshDrawer();
  };

  // ───────────────────────────────────────────────────────────────
  // State
  // ───────────────────────────────────────────────────────────────
  let evSrc = null;
  let retryDelay = 1000;
  let history = [];
  let badgeCount = 0;
  function updateFab() {
    fab.style.display = history.length ? "inline-block" : "none";
    fab.querySelector(".badge").textContent = badgeCount;
  }

  function spawnToast(ev) {
    const t = document.createElement("div");
    let cls = `acs-toast kind-${ev.kind}`;
    if (ev.kind === "acs_push") cls += (ev.ok ? " ok" : " fail");
    t.className = cls;
    t.setAttribute("data-testid", `acs-toast-${ev.kind}-${ev.id}`);
    const msg = ev.message || JSON.stringify(ev);
    t.innerHTML = `
      <span class="acs-toast-close" data-testid="acs-toast-dismiss">×</span>
      <div class="acs-toast-body">${msg}</div>
      <div class="acs-toast-meta">${ev.kind} · ${new Date().toLocaleTimeString()}</div>`;
    toastBox.appendChild(t);
    t.querySelector(".acs-toast-close").onclick = () => t.remove();
    setTimeout(() => t.remove(), TOAST_TTL_MS);
  }

  function pushHistory(ev) {
    history.unshift(ev);
    if (history.length > MAX_HISTORY) history.length = MAX_HISTORY;
    badgeCount++;
    updateFab();
  }

  function renderDrawerItem(ev) {
    const li = document.createElement("div");
    li.className = "acs-drawer-item";
    li.setAttribute("data-testid", `acs-drawer-item-${ev.id}`);
    li.innerHTML = `
      <div>${ev.message || ev.kind}</div>
      <div class="acs-meta">${ev.kind} · #${ev.id}</div>`;
    return li;
  }
  function refreshDrawer() {
    drawerList.innerHTML = "";
    history.forEach(ev => drawerList.appendChild(renderDrawerItem(ev)));
  }

  function handleEvent(ev) {
    pushHistory(ev);
    spawnToast(ev);
    if (drawer.classList.contains("open")) {
      drawerList.prepend(renderDrawerItem(ev));
    }
  }

  // ───────────────────────────────────────────────────────────────
  // SSE connect + reconnect
  // ───────────────────────────────────────────────────────────────
  function connect() {
    if (evSrc) { try { evSrc.close(); } catch (_) {} }
    evSrc = new EventSource(ENDPOINT, { withCredentials: true });
    evSrc.addEventListener("hello", () => { retryDelay = 1000; });
    ["acs_push", "ztp_discovery", "outage"].forEach(k => {
      evSrc.addEventListener(k, (m) => {
        try { handleEvent(JSON.parse(m.data)); }
        catch (e) { console.warn("[acs-toast] parse err", e); }
      });
    });
    evSrc.onerror = (e) => {
      if (evSrc.readyState === EventSource.CLOSED) {
        retryDelay = Math.min(retryDelay * 2, 30000);
        setTimeout(connect, retryDelay);
      }
    };
  }

  // ───────────────────────────────────────────────────────────────
  // Bootstrap on logged-in admin pages only
  // ───────────────────────────────────────────────────────────────
  // We assume the include is only rendered into admin/sub-lco/employee bases.
  // If unauthorized, EventSource will receive 401 + close → exponential
  // back-off kicks in but no toasts. Safe no-op otherwise.
  document.addEventListener("DOMContentLoaded", () => {
    fetch(RECENT_ENDPOINT, { credentials: "include" })
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d && d.events) { history = d.events.slice(0, MAX_HISTORY)
                                       .map(e => ({...e, message: e.subject || e.reason || e.onu_serial || JSON.stringify(e)})); 
                                       updateFab(); } })
      .catch(() => {});
    connect();
  });
})();
