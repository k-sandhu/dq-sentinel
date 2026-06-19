/* DQ Sentinel wireframes — tiny interaction layer (no framework).
   Handles: direction + theme switching (persisted), SPA section routing,
   tabs, drawers, and a couple of demo toggles. */
(function () {
  const root = document.documentElement;
  const LS_DIR = "dqw-dir", LS_THEME = "dqw-theme";

  /* ---- direction + theme, persisted across the whole wireframe set ---- */
  function applyDir(dir) {
    root.setAttribute("data-dir", dir);
    localStorage.setItem(LS_DIR, dir);
    document.querySelectorAll("[data-set-dir]").forEach(b =>
      b.classList.toggle("on", b.getAttribute("data-set-dir") === dir));
  }
  function applyTheme(theme) {
    root.setAttribute("data-theme", theme);
    localStorage.setItem(LS_THEME, theme);
    document.querySelectorAll("[data-theme-icon]").forEach(el => {
      el.textContent = theme === "dark" ? "☀" : "☾";
    });
  }
  window.dqw = {
    setDir: applyDir,
    setTheme: applyTheme,
    toggleTheme: () => applyTheme(root.getAttribute("data-theme") === "dark" ? "light" : "dark"),
  };

  // restore (graphite implies dark unless the user chose otherwise on this device)
  const savedDir = localStorage.getItem(LS_DIR) || root.getAttribute("data-dir") || "aurora";
  const savedTheme = localStorage.getItem(LS_THEME) || root.getAttribute("data-theme") ||
                     (savedDir === "graphite" ? "dark" : "light");
  applyDir(savedDir);
  applyTheme(savedTheme);

  /* ---- SPA routing: [data-nav="x"] shows [data-route="x"] ---- */
  function route(name) {
    document.querySelectorAll("[data-route]").forEach(s =>
      s.classList.toggle("active", s.getAttribute("data-route") === name));
    document.querySelectorAll("[data-nav]").forEach(n =>
      n.classList.toggle("active", n.getAttribute("data-nav") === name));
    const main = document.querySelector(".main");
    if (main) main.scrollTop = 0;
    if (history.replaceState) history.replaceState(null, "", "#" + name);
  }
  window.dqw.route = route;

  /* ---- tabs: [data-tab="g:name"] toggles [data-tabpanel="g:name"] ---- */
  function tab(key) {
    const group = key.split(":")[0];
    document.querySelectorAll(`[data-tab^="${group}:"]`).forEach(t =>
      t.classList.toggle("active", t.getAttribute("data-tab") === key));
    document.querySelectorAll(`[data-tabpanel^="${group}:"]`).forEach(p =>
      p.classList.toggle("active", p.getAttribute("data-tabpanel") === key));
  }
  window.dqw.tab = tab;

  /* ---- drawer ---- */
  window.dqw.openDrawer = (id) => {
    document.getElementById(id)?.classList.add("show");
    document.getElementById("scrim")?.classList.add("show");
  };
  window.dqw.closeDrawer = () => {
    document.querySelectorAll(".drawer.show").forEach(d => d.classList.remove("show"));
    document.getElementById("scrim")?.classList.remove("show");
  };

  /* ---- wire up declaratively after DOM ready ---- */
  document.addEventListener("click", (e) => {
    const navEl = e.target.closest("[data-nav]");
    if (navEl) { e.preventDefault(); route(navEl.getAttribute("data-nav")); return; }
    const tabEl = e.target.closest("[data-tab]");
    if (tabEl) { e.preventDefault(); tab(tabEl.getAttribute("data-tab")); return; }
    const dirEl = e.target.closest("[data-set-dir]");
    if (dirEl) { applyDir(dirEl.getAttribute("data-set-dir")); return; }
    if (e.target.closest("[data-theme-toggle]")) { window.dqw.toggleTheme(); return; }
    const drwEl = e.target.closest("[data-open-drawer]");
    if (drwEl) { window.dqw.openDrawer(drwEl.getAttribute("data-open-drawer")); return; }
    if (e.target.closest("[data-close-drawer]") || e.target.id === "scrim") { window.dqw.closeDrawer(); return; }
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") window.dqw.closeDrawer(); });

  // open route from hash on load
  const h = location.hash.replace("#", "");
  if (h && document.querySelector(`[data-route="${h}"]`)) route(h);
})();
