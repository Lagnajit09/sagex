/* sagex website behaviour (every page). No dependencies. */
(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ── Theme ──────────────────────────────────────────────────────────────
     The initial theme is set by the inline <head> script (no flash); this
     only handles the toggle. */
  $$(".theme-toggle").forEach((btn) =>
    btn.addEventListener("click", () => {
      const next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
      document.documentElement.dataset.theme = next;
      try { localStorage.setItem("sagex-theme", next); } catch (_) { /* private mode */ }
    })
  );

  /* ── Mobile menu ────────────────────────────────────────────────────── */
  const header = $(".site-header");
  const menuBtn = $(".menu-toggle");
  if (header && menuBtn) {
    menuBtn.addEventListener("click", () => {
      const open = header.classList.toggle("menu-open");
      menuBtn.setAttribute("aria-expanded", String(open));
    });
    $$(".nav a", header).forEach((a) => a.addEventListener("click", () => header.classList.remove("menu-open")));
  }

  /* ── Install tabs: OS-aware default, kept in sync across the page ───── */
  const platform = (navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || navigator.userAgent;
  const defaultTab = /win/i.test(platform) ? "windows" : "unix";

  function selectTab(name) {
    $$("[data-tabs]").forEach((group) => {
      if (!$(`[data-tab="${name}"]`, group)) return;
      $$("[data-tab]", group).forEach((tab) => {
        const on = tab.dataset.tab === name;
        tab.setAttribute("aria-selected", String(on));
        tab.tabIndex = on ? 0 : -1;
      });
      $$("[data-panel]", group).forEach((panel) => { panel.hidden = panel.dataset.panel !== name; });
    });
  }

  $$("[data-tabs]").forEach((group) => {
    const tabs = $$("[data-tab]", group);
    tabs.forEach((tab, i) => {
      tab.addEventListener("click", () => selectTab(tab.dataset.tab));
      tab.addEventListener("keydown", (e) => {
        if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
        const next = tabs[(i + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length];
        selectTab(next.dataset.tab);
        next.focus();
      });
    });
  });
  selectTab(defaultTab);

  /* ── Copy buttons ───────────────────────────────────────────────────── */
  function textOf(code) {
    const clone = code.cloneNode(true);
    $$(".p, .t-dim.comment", clone).forEach((el) => el.remove());   // prompts aren't copied
    return clone.textContent.replace(/ /g, " ").trim();
  }

  async function copy(text) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (_) {
      const area = Object.assign(document.createElement("textarea"), { value: text });
      area.style.cssText = "position:fixed;opacity:0";
      document.body.appendChild(area);
      area.select();
      const ok = document.execCommand("copy");
      area.remove();
      return ok;
    }
  }

  document.addEventListener("click", async (e) => {
    const btn = e.target.closest(".copy-btn");
    if (!btn) return;
    const code = btn.dataset.copy ? null : $("code", btn.closest("[data-copyable]") || btn.parentElement);
    const text = btn.dataset.copy || (code && textOf(code));
    if (!text || !(await copy(text))) return;
    const label = $(".lbl", btn);
    btn.classList.add("copied");
    if (label) label.textContent = "Copied";
    clearTimeout(btn._t);
    btn._t = setTimeout(() => {
      btn.classList.remove("copied");
      if (label) label.textContent = "Copy";
    }, 1600);
  });

  /* ── Reveal on scroll ───────────────────────────────────────────────── */
  if ("IntersectionObserver" in window && !reducedMotion) {
    const io = new IntersectionObserver((entries) => {
      entries.forEach((en) => {
        if (en.isIntersecting) { en.target.classList.add("in"); io.unobserve(en.target); }
      });
    }, { rootMargin: "0px 0px -8% 0px" });
    $$(".reveal").forEach((el) => io.observe(el));
  } else {
    $$(".reveal").forEach((el) => el.classList.add("in"));
  }

  /* ── Cron countdown: "0 2 * * *" (UTC) ──────────────────────────────── */
  const tickers = $$("[data-cron-ticker]");
  if (tickers.length) {
    const pad = (n) => String(n).padStart(2, "0");
    const tick = () => {
      const now = new Date();
      const next = new Date(now);
      next.setUTCHours(2, 0, 0, 0);
      if (next <= now) next.setUTCDate(next.getUTCDate() + 1);
      const s = Math.floor((next - now) / 1000);
      const text = `${Math.floor(s / 3600)}h ${pad(Math.floor(s / 60) % 60)}m ${pad(s % 60)}s`;
      tickers.forEach((t) => { $("b", t).textContent = text; });
    };
    tick();
    setInterval(tick, 1000);
  }

  /* ── Docs: highlight the current section in the contents list ───────── */
  const tocLinks = $$(".toc a[href^='#']");
  if (tocLinks.length && "IntersectionObserver" in window) {
    const byId = new Map(tocLinks.map((a) => [a.getAttribute("href").slice(1), a]));
    const spy = new IntersectionObserver((entries) => {
      entries.forEach((en) => {
        if (!en.isIntersecting) return;
        tocLinks.forEach((a) => a.classList.remove("is-current"));
        const link = byId.get(en.target.id);
        if (link) link.classList.add("is-current");
      });
    }, { rootMargin: "-20% 0px -70% 0px" });
    byId.forEach((_, id) => { const el = document.getElementById(id); if (el) spy.observe(el); });
  }

  /* ── System map: wires between sources → hub → targets ──────────────── */
  const map = $("[data-map]");
  if (map) initMap(map);

  function initMap(map) {
    const svg = $(".map-wires", map);
    const hub = $(".map-hub", map);
    const NS = "http://www.w3.org/2000/svg";

    // Horizontal-first orthogonal route with rounded corners (a → b, b to the right).
    function hRoute(a, b, r = 12) {
      const mx = Math.round((a.x + b.x) / 2);
      const dy = b.y - a.y;
      if (Math.abs(dy) < 1.5) return `M${a.x},${a.y} H${b.x}`;
      const s = Math.sign(dy);
      const rr = Math.min(r, Math.abs(dy) / 2, Math.abs(mx - a.x));
      return `M${a.x},${a.y} H${mx - rr} Q${mx},${a.y} ${mx},${a.y + s * rr} V${b.y - s * rr} Q${mx},${b.y} ${mx + rr},${b.y} H${b.x}`;
    }
    // Vertical-first route (a above b), for the stacked mobile layout.
    function vRoute(a, b, r = 12) {
      const my = Math.round((a.y + b.y) / 2);
      const dx = b.x - a.x;
      if (Math.abs(dx) < 1.5) return `M${a.x},${a.y} V${b.y}`;
      const s = Math.sign(dx);
      const rr = Math.min(r, Math.abs(dx) / 2, Math.abs(my - a.y));
      return `M${a.x},${a.y} V${my - rr} Q${a.x},${my} ${a.x + s * rr},${my} H${b.x - s * rr} Q${b.x},${my} ${b.x},${my + rr} V${b.y}`;
    }

    function el(name, attrs) {
      const node = document.createElementNS(NS, name);
      Object.entries(attrs).forEach(([k, v]) => node.setAttribute(k, v));
      return node;
    }

    function draw() {
      const box = map.getBoundingClientRect();
      const h = hub.getBoundingClientRect();
      const stacked = getComputedStyle(map).gridTemplateColumns.split(" ").length === 1;
      svg.setAttribute("viewBox", `0 0 ${box.width} ${box.height}`);
      svg.replaceChildren();

      const wires = [];
      const ins = $$("[data-wire='in']", map);
      const outs = $$("[data-wire='out']", map);
      const rel = (x, y) => ({ x: Math.round(x - box.left), y: Math.round(y - box.top) });
      const spread = (i, n, from, size) => from + size * (n === 1 ? 0.5 : 0.22 + (0.56 * i) / (n - 1));

      // Each wire: [keys, from, to]. Side by side, every source and target gets its
      // own wire. Stacked (narrow screens), one trunk runs in and one out of the
      // hub: per-node wires would cross the stacked cards.
      if (stacked) {
        const cols = $$(".map-col", map).map((c) => c.getBoundingClientRect());
        wires.push([ins.map((n) => n.dataset.key), rel(cols[0].left + cols[0].width / 2, cols[0].bottom), rel(h.left + h.width / 2, h.top)]);
        wires.push([outs.map((n) => n.dataset.key), rel(h.left + h.width / 2, h.bottom), rel(cols[1].left + cols[1].width / 2, cols[1].top)]);
      } else {
        ins.forEach((node, i) => {
          const r = node.getBoundingClientRect();
          wires.push([[node.dataset.key], rel(r.right, r.top + r.height / 2), rel(h.left, spread(i, ins.length, h.top, h.height))]);
        });
        outs.forEach((node, i) => {
          const r = node.getBoundingClientRect();
          wires.push([[node.dataset.key], rel(h.right, spread(i, outs.length, h.top, h.height)), rel(r.left, r.top + r.height / 2)]);
        });
      }

      wires.forEach(([keys, a, b], i) => {
        const d = stacked ? vRoute(a, b) : hRoute(a, b);
        svg.appendChild(el("path", { d, class: "wire" }));
        const flow = el("path", { d, class: "flow", "data-keys": keys.join(" ") });
        flow.style.animationDelay = `${-i * 0.37}s`;
        svg.appendChild(flow);
        svg.appendChild(el("circle", { cx: a.x, cy: a.y, r: 3, class: "joint" }));
        svg.appendChild(el("circle", { cx: b.x, cy: b.y, r: 3, class: "joint" }));
      });
      setActive(activeKeys);
    }

    let activeKeys = new Set();
    function setActive(keys) {
      activeKeys = new Set(keys);
      $$(".flow", svg).forEach((p) =>
        p.classList.toggle("is-active", p.dataset.keys.split(" ").some((k) => activeKeys.has(k))));
      $$("[data-key]", map).forEach((n) => {
        if (n.classList.contains("map-node")) n.classList.toggle("is-active", activeKeys.has(n.dataset.key));
      });
    }

    draw();
    if ("ResizeObserver" in window) new ResizeObserver(() => draw()).observe(map);
    else window.addEventListener("resize", draw);
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(draw);

    const term = $("[data-hub-term]", map);
    if (term) runDemo(term, setActive);
  }

  /* ── Hub terminal: a run that mirrors `sagex`'s live graph ──────────── */
  function runDemo(body, setActive) {
    const PULSE = "·✢✶✻✽✻✶✢";
    const RUN_ID = "46a2ba05";
    const SOURCES = [
      { key: "hook", label: "Webhook", cmd: 'sagex trigger fire "Deploy Production"', via: "via HTTP trigger · Idempotency‑Key 3f9c0a" },
      { key: "cron", label: "Schedule", cmd: 'sagex trigger watch "Deploy Production"', via: "via schedule" },
      { key: "git", label: "Git push", cmd: 'sagex run workflow "Deploy Production" -p VERSION=8f3c2e1', note: "# .github/workflows/deploy.yml · on: push", params: "VERSION=8f3c2e1" },
      { key: "manual", label: "Manual", cmd: 'sagex run workflow "Deploy Production" -p VERSION=1.4.2', params: "VERSION=1.4.2" },
    ];
    // [id, label, kind icon, seconds, final details, target key]
    const NODES = [
      ["trigger", null, "▸", 0.5, "", null],
      ["build", "Build", "●", 2.3, "exit 0 · 2.3s", "linux"],
      ["migrate", "Migrate", "●", 4.1, "exit 0 · 4.1s", "win"],
      ["check", "Healthy?", "◆", 0.4, "→ true · 0.1s", null],
      ["notify", "Notify team", "■", 0.8, "0.8s", "mail"],
    ];
    const esc = (s) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

    let loop = 0;
    let pending = [];      // [timeoutId, resolve] — stop() resolves them so the loop can exit
    let pulse = null;
    let visible = true;
    let playing = false;
    const later = (ms, fn) => new Promise((resolve) => {
      const id = setTimeout(() => { fn && fn(); resolve(); }, ms);
      pending.push([id, resolve]);
    });

    function frame(src) {
      body.innerHTML = [
        src.note ? `<span class="l t-dim">${esc(src.note)}</span>` : "",
        `<span class="l"><span class="t-cli">❯</span> <span data-cmd></span><span class="t-dim" data-caret>▌</span></span>`,
        `<span class="l" data-head hidden><span class="t-cyan">▶</span> <span class="t-bold">Deploy Production</span><span class="t-dim"> · 6 nodes${src.via ? " · " + esc(src.via) : ""}</span></span>`,
        src.params ? `<span class="l" data-head hidden>  <span class="t-dim">${esc(src.params.split("=")[0])}=</span>${esc(src.params.split("=")[1])}</span>` : "",
        `<div class="run-panel" data-panel hidden><span class="rp-title">Deploy Production</span>`,
        NODES.map(([id, label, ic]) => {
          const lbl = label || src.label;
          if (id === "notify") return row(id, `├ <span class="t-dim">true</span>  `, ic, lbl);
          return row(id, "", ic, lbl);
        }).join(""),
        row("rollback", `└ <span class="t-dim">false</span> `, "●", "Roll back"),
        `<span class="rp-sub" data-sub>live</span></div>`,
        `<span class="l" data-activity></span>`,
        `<span class="l" data-summary></span>`,
      ].join("");
    }

    function row(id, prefix, ic, label) {
      return `<div class="row pending" data-row="${id}"><span class="hd">${prefix}<span class="ic">${ic}</span> <span class="lbl">${esc(label)}</span></span><span class="det"></span></div>`;
    }

    function setRow(id, state, details) {
      const r = body.querySelector(`[data-row="${id}"]`);
      if (!r) return;
      r.className = `row ${state}`;
      const ic = r.querySelector(".ic");
      const lbl = r.querySelector(".lbl");
      lbl.className = "lbl";
      if (state === "success") { ic.className = "ic t-ok"; ic.textContent = "✓"; }
      if (state === "skipped") { ic.className = "ic t-dim"; ic.textContent = "–"; lbl.className = "lbl t-dim"; }
      if (state === "running") { ic.className = "ic t-cli"; }
      r.querySelector(".det").textContent = details || "";
    }

    function activity(t0) {
      const line = body.querySelector("[data-activity]");
      if (!line) return;
      const t = (performance.now() - t0) / 1000;
      const word = "Running…";
      const hot = Math.floor(t * 14) % (word.length + 8) - 4;
      const letters = [...word].map((ch, i) => `<span class="${Math.abs(i - hot) <= 1 ? "t-hot" : "t-cli"}">${ch}</span>`).join("");
      line.innerHTML = `<span class="t-cli">${PULSE[Math.floor(t * 8) % PULSE.length]}</span> ${letters}<span class="t-dim"> (${Math.floor(t)}s · ctrl+c to stop)</span>`;
    }

    async function play() {
      if (playing) return;
      playing = true;
      while (visible) {
        const src = SOURCES[loop % SOURCES.length];
        loop += 1;
        frame(src);
        setActive([src.key]);

        // Type the command.
        const cmdEl = body.querySelector("[data-cmd]");
        for (const ch of src.cmd) {
          if (!visible) break;
          await later(24, () => { cmdEl.textContent += ch; });
        }
        if (!visible) break;
        await later(380);
        body.querySelector("[data-caret]").remove();
        body.querySelectorAll("[data-head]").forEach((n) => { n.hidden = false; });
        await later(260, () => { body.querySelector("[data-panel]").hidden = false; });

        const t0 = performance.now();
        pulse = setInterval(() => {
          activity(t0);
          const cur = body.querySelector(".row.running");
          if (cur) {
            const since = (performance.now() - cur._t0) / 1000;
            cur.querySelector(".ic").textContent = PULSE[Math.floor(since * 8) % PULSE.length];
            cur.querySelector(".det").textContent = `running ${Math.floor(since)}s`;
          }
        }, 125);

        for (const [id, , , secs, details, target] of NODES) {
          if (!visible) break;
          setRow(id, "running", "running 0s");
          body.querySelector(`[data-row="${id}"]`)._t0 = performance.now();
          setActive([src.key, target].filter(Boolean));
          await later(secs * 1000);
          setRow(id, "success", details);
        }
        clearInterval(pulse);
        if (!visible) break;
        setRow("rollback", "skipped", "skipped");
        body.querySelector("[data-activity]").innerHTML = "";
        const panel = body.querySelector("[data-panel]");
        panel.classList.add("done");
        body.querySelector("[data-sub]").textContent = "success";
        body.querySelector("[data-summary]").innerHTML =
          `<span class="t-ok t-bold">✓ success</span><span class="t-dim"> · 9.8s · run ${RUN_ID}\n  logs: logs/workflow/${RUN_ID}…/</span>`;
        setActive([src.key, "logs"]);
        await later(3800);
      }
      playing = false;
    }

    function stop() {
      clearInterval(pulse);
      pending.forEach(([id, resolve]) => { clearTimeout(id); resolve(); });
      pending = [];
    }

    // A finished run: shown straight away (so the box is never empty before the
    // animation reaches it), and the only frame with reduced motion.
    function drawFinished() {
      const src = SOURCES[3];
      frame(src);
      body.querySelector("[data-cmd]").textContent = src.cmd;
      body.querySelector("[data-caret]").remove();
      body.querySelectorAll("[data-head]").forEach((n) => { n.hidden = false; });
      body.querySelector("[data-panel]").hidden = false;
      NODES.forEach(([id, , , , details]) => setRow(id, "success", details));
      setRow("rollback", "skipped", "skipped");
      body.querySelector("[data-panel]").classList.add("done");
      body.querySelector("[data-sub]").textContent = "success";
      body.querySelector("[data-summary]").innerHTML = `<span class="t-ok t-bold">✓ success</span><span class="t-dim"> · 9.8s · run ${RUN_ID}</span>`;
      setActive(["manual", "logs"]);
    }

    drawFinished();
    if (reducedMotion) return;

    // Only animate while the hero is on screen.
    if ("IntersectionObserver" in window) {
      new IntersectionObserver(([en]) => {
        visible = en.isIntersecting;
        if (visible) play();
        else stop();
      }).observe(body);
    } else {
      play();
    }
  }
})();
