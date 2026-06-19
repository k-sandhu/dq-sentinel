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
    const typeEl = e.target.closest(".type-opt");
    if (typeEl) { typeEl.parentElement.querySelectorAll(".type-opt").forEach(o => o.classList.remove("on")); typeEl.classList.add("on"); return; }
    if (e.target.closest("[data-close-drawer]") || e.target.id === "scrim") { window.dqw.closeDrawer(); return; }
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") window.dqw.closeDrawer(); });

  /* ---- lightweight SVG charts ----
     <div class="chartbox" data-chart="area|line|bars"
          data-values="3,5,4,…" [data-values2="…"] [data-band="lo:hi"]></div>
     Colours come from CSS vars (style attrs), so charts auto-recolour on
     direction/theme switch with no re-render. */
  let cid = 0;
  function drawChart(el) {
    const type = el.dataset.chart || "area";
    const W = 100, H = 38, pad = 3;
    const vals = (el.dataset.values || "").split(",").map(Number).filter(n => !isNaN(n));
    const vals2 = (el.dataset.values2 || "").split(",").map(Number).filter(n => !isNaN(n));
    const all = vals.concat(vals2);
    if (!all.length) return;
    const lo = Math.min(...all), hi = Math.max(...all), span = (hi - lo) || 1;
    const X = (i, n) => pad + (i / (n - 1 || 1)) * (W - 2 * pad);
    const Y = v => H - pad - ((v - lo) / span) * (H - 2 * pad);
    const id = "g" + (cid++);
    let svg = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img">`;
    svg += `<defs><linearGradient id="${id}" x1="0" x2="0" y1="0" y2="1">
      <stop offset="0" style="stop-color:var(--brand);stop-opacity:.28"/>
      <stop offset="1" style="stop-color:var(--brand);stop-opacity:0"/></linearGradient></defs>`;
    // baseline
    svg += `<line x1="0" y1="${H - pad}" x2="${W}" y2="${H - pad}" style="stroke:var(--border-soft)" stroke-width="0.5"/>`;
    if (el.dataset.band) {
      const [blo, bhi] = el.dataset.band.split(":").map(Number);
      svg += `<rect x="0" y="${Y(bhi)}" width="${W}" height="${Y(blo) - Y(bhi)}" style="fill:var(--brand);opacity:.06"/>`;
    }
    if (type === "bars") {
      const n = vals.length, bw = (W - 2 * pad) / n * 0.62;
      vals.forEach((v, i) => {
        const x = X(i, n) - bw / 2, y = Y(v);
        const hot = el.dataset.hot && el.dataset.hot.split(",").map(Number).includes(i);
        svg += `<rect x="${x}" y="${y}" width="${bw}" height="${H - pad - y}" rx="0.8" style="fill:var(${hot ? "--danger" : "--brand"})"/>`;
      });
    } else {
      const pts = vals.map((v, i) => `${X(i, vals.length)},${Y(v)}`).join(" ");
      if (type === "area")
        svg += `<polygon points="${pad},${H - pad} ${pts} ${W - pad},${H - pad}" fill="url(#${id})"/>`;
      svg += `<polyline points="${pts}" fill="none" style="stroke:var(--brand)" stroke-width="1.4" stroke-linejoin="round" stroke-linecap="round"/>`;
      if (vals2.length) {
        const p2 = vals2.map((v, i) => `${X(i, vals2.length)},${Y(v)}`).join(" ");
        svg += `<polyline points="${p2}" fill="none" style="stroke:var(--danger)" stroke-width="1.2" stroke-dasharray="2 1.5" stroke-linejoin="round"/>`;
      }
      const lv = vals[vals.length - 1];
      svg += `<circle cx="${X(vals.length - 1, vals.length)}" cy="${Y(lv)}" r="1.6" style="fill:var(--brand)"/>`;
    }
    svg += `</svg>`;
    el.innerHTML = svg;
  }
  window.dqw.charts = () => document.querySelectorAll("[data-chart]").forEach(drawChart);
  window.dqw.charts();

  // open route from hash on load
  const h = location.hash.replace("#", "");
  if (h && document.querySelector(`[data-route="${h}"]`)) route(h);
})();
