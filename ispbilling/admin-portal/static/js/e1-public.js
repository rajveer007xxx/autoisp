/* =========================================================================
 *  e1-public.js — Telecom Cloud OS public site animations
 *  - Navbar scroll state
 *  - IntersectionObserver scroll reveals
 *  - Animated KPI counters
 *  - Canvas particle field
 *  - Cursor glow
 *  - Mobile nav toggle
 * ========================================================================= */
(function () {
  "use strict";

  // ───── Navbar scroll state + mobile burger ─────
  document.addEventListener("DOMContentLoaded", function () {
    var nav = document.querySelector(".e1-nav");
    var burger = document.querySelector(".e1-nav-burger");

    function onScroll() {
      if (!nav) return;
      if (window.scrollY > 20) nav.classList.add("scrolled");
      else nav.classList.remove("scrolled");
    }
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();

    if (burger && nav) {
      burger.addEventListener("click", function () { nav.classList.toggle("is-open"); });
      nav.querySelectorAll(".e1-nav-links a").forEach(function (a) {
        a.addEventListener("click", function () { nav.classList.remove("is-open"); });
      });
    }
  });

  // ───── Scroll reveal ─────
  function initReveal() {
    var els = document.querySelectorAll("[data-reveal]");
    if (!els.length) return;
    if (!("IntersectionObserver" in window)) {
      els.forEach(function (el) { el.classList.add("in"); });
      return;
    }
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) {
          e.target.classList.add("in");
          io.unobserve(e.target);
        }
      });
    }, { threshold: 0.12, rootMargin: "0px 0px -10% 0px" });
    els.forEach(function (el) { io.observe(el); });
  }

  // ───── Counter animation ─────
  function animateCounters() {
    var counters = document.querySelectorAll("[data-counter]");
    if (!counters.length || !("IntersectionObserver" in window)) {
      counters.forEach(function (c) { c.textContent = c.getAttribute("data-counter"); });
      return;
    }
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (!e.isIntersecting) return;
        var el = e.target;
        var target = parseFloat(el.getAttribute("data-counter"));
        var prefix = el.getAttribute("data-prefix") || "";
        var suffix = el.getAttribute("data-suffix") || "";
        var decimals = parseInt(el.getAttribute("data-decimals") || "0", 10);
        var duration = 1800;
        var start = performance.now();
        function tick(now) {
          var p = Math.min(1, (now - start) / duration);
          var eased = 1 - Math.pow(1 - p, 4);
          var v = target * eased;
          el.textContent = prefix + (decimals ? v.toFixed(decimals) : Math.floor(v).toLocaleString()) + suffix;
          if (p < 1) requestAnimationFrame(tick);
        }
        requestAnimationFrame(tick);
        io.unobserve(el);
      });
    }, { threshold: 0.4 });
    counters.forEach(function (c) { io.observe(c); });
  }

  // ───── Canvas particle field (hero) ─────
  function initParticles() {
    var canvas = document.getElementById("e1Particles");
    if (!canvas) return;
    if (window.matchMedia && window.matchMedia("(max-width: 600px)").matches) {
      canvas.style.display = "none"; return;
    }
    var ctx = canvas.getContext("2d");
    var dpr = window.devicePixelRatio || 1;
    var w, h;
    var nodes = [];
    var MAX = 50;

    function resize() {
      w = canvas.clientWidth;
      h = canvas.clientHeight;
      canvas.width = w * dpr;
      canvas.height = h * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    resize();
    window.addEventListener("resize", resize);

    for (var i = 0; i < MAX; i++) {
      nodes.push({
        x: Math.random() * w,
        y: Math.random() * h,
        vx: (Math.random() - 0.5) * 0.35,
        vy: (Math.random() - 0.5) * 0.35,
        r: 1 + Math.random() * 1.6,
      });
    }

    function frame() {
      ctx.clearRect(0, 0, w, h);
      // Lines
      ctx.lineWidth = 0.65;
      for (var i = 0; i < nodes.length; i++) {
        for (var j = i + 1; j < nodes.length; j++) {
          var dx = nodes[i].x - nodes[j].x;
          var dy = nodes[i].y - nodes[j].y;
          var d = Math.sqrt(dx * dx + dy * dy);
          if (d < 130) {
            var op = (1 - d / 130) * 0.45;
            ctx.strokeStyle = "rgba(99,102,241," + op + ")";
            ctx.beginPath();
            ctx.moveTo(nodes[i].x, nodes[i].y);
            ctx.lineTo(nodes[j].x, nodes[j].y);
            ctx.stroke();
          }
        }
      }
      // Dots
      for (var k = 0; k < nodes.length; k++) {
        var n = nodes[k];
        n.x += n.vx;
        n.y += n.vy;
        if (n.x < 0 || n.x > w) n.vx *= -1;
        if (n.y < 0 || n.y > h) n.vy *= -1;
        ctx.fillStyle = "rgba(34,211,238,0.85)";
        ctx.beginPath();
        ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2);
        ctx.fill();
      }
      requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
  }

  // ───── Cursor glow ─────
  function initCursorGlow() {
    if (window.matchMedia && window.matchMedia("(max-width: 991px)").matches) return;
    var glow = document.createElement("div");
    glow.className = "cursor-glow";
    document.body.appendChild(glow);
    var x = 0, y = 0, tx = 0, ty = 0;
    document.addEventListener("mousemove", function (e) { tx = e.clientX; ty = e.clientY; });
    function loop() {
      x += (tx - x) * 0.14;
      y += (ty - y) * 0.14;
      glow.style.transform = "translate(" + x + "px," + y + "px) translate(-50%,-50%)";
      requestAnimationFrame(loop);
    }
    requestAnimationFrame(loop);
  }

  // ───── Hero line chart drawing ─────
  function drawHeroChart() {
    var svg = document.getElementById("hvLineChart");
    if (!svg) return;
    var w = svg.clientWidth || 240;
    var h = svg.clientHeight || 110;
    svg.setAttribute("viewBox", "0 0 " + w + " " + h);
    var points = [];
    var N = 24;
    for (var i = 0; i < N; i++) {
      var x = (i / (N - 1)) * w;
      var y = h * 0.7 - Math.sin(i * 0.5) * 18 - Math.random() * 8 + (i * (h * 0.3 / N));
      points.push([x, h - y]);
    }
    var d = "M " + points[0][0] + " " + points[0][1];
    for (var p = 1; p < points.length; p++) {
      var px = (points[p - 1][0] + points[p][0]) / 2;
      var py = (points[p - 1][1] + points[p][1]) / 2;
      d += " Q " + points[p - 1][0] + " " + points[p - 1][1] + " " + px + " " + py;
    }
    d += " L " + w + " " + h + " L 0 " + h + " Z";
    svg.innerHTML =
      '<defs><linearGradient id="hvG" x1="0" x2="0" y1="0" y2="1">' +
      '<stop offset="0%" stop-color="#22d3ee" stop-opacity="0.45"/>' +
      '<stop offset="100%" stop-color="#22d3ee" stop-opacity="0"/></linearGradient></defs>' +
      '<path d="' + d + '" fill="url(#hvG)" stroke="none"/>' +
      '<path d="' + d.replace(/L \d+ \d+ L 0 \d+ Z/, "") + '" fill="none" stroke="#22d3ee" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" style="filter:drop-shadow(0 0 6px #22d3ee)"/>';
  }

  // ───── NOC fiber map SVG ─────
  function drawNocMap() {
    var svg = document.getElementById("fvNocSvg");
    if (!svg) return;
    var nodes = [
      { x: 80,  y: 80,  r: 14, c: "#22d3ee", l: "OLT-1" },
      { x: 240, y: 60,  r: 10, c: "#22d3ee", l: "SP-A" },
      { x: 380, y: 140, r: 10, c: "#22d3ee", l: "SP-B" },
      { x: 180, y: 240, r: 10, c: "#22d3ee", l: "SP-C" },
      { x: 320, y: 320, r: 10, c: "#22d3ee", l: "SP-D" },
      { x: 460, y: 280, r: 10, c: "#22d3ee", l: "SP-E" },
      { x: 540, y: 90,  r: 8,  c: "#34d399", l: "ONU" },
      { x: 540, y: 220, r: 8,  c: "#34d399", l: "ONU" },
      { x: 120, y: 320, r: 8,  c: "#f87171", l: "ONU-X" },
      { x: 60,  y: 200, r: 8,  c: "#34d399", l: "ONU" },
    ];
    var edges = [
      [0,1],[1,2],[0,3],[2,5],[3,4],[2,6],[5,7],[3,8],[0,9],[4,8],
    ];
    var w = 600, h = 380;
    svg.setAttribute("viewBox", "0 0 " + w + " " + h);
    var s =
      '<defs>' +
      '<filter id="glow"><feGaussianBlur stdDeviation="3"/></filter>' +
      '<linearGradient id="fibG" x1="0" x2="1"><stop offset="0%" stop-color="#22d3ee" stop-opacity="0"/>' +
      '<stop offset="50%" stop-color="#22d3ee" stop-opacity="1"/>' +
      '<stop offset="100%" stop-color="#22d3ee" stop-opacity="0"/></linearGradient>' +
      '</defs>';
    // Edges
    edges.forEach(function (e, idx) {
      var a = nodes[e[0]], b = nodes[e[1]];
      s += '<line x1="' + a.x + '" y1="' + a.y + '" x2="' + b.x + '" y2="' + b.y +
        '" stroke="rgba(34,211,238,0.30)" stroke-width="1.3"/>';
      // Animated packet
      s += '<circle r="2.5" fill="#22d3ee" style="filter:drop-shadow(0 0 4px #22d3ee)">' +
        '<animateMotion dur="' + (3 + (idx % 4)) + 's" repeatCount="indefinite" path="M ' +
        a.x + ' ' + a.y + ' L ' + b.x + ' ' + b.y + '"/></circle>';
    });
    // Nodes
    nodes.forEach(function (n) {
      s += '<circle cx="' + n.x + '" cy="' + n.y + '" r="' + (n.r + 4) +
        '" fill="' + n.c + '" opacity="0.18"/>';
      s += '<circle cx="' + n.x + '" cy="' + n.y + '" r="' + n.r +
        '" fill="' + n.c + '" style="filter:drop-shadow(0 0 6px ' + n.c + ')"/>';
      s += '<circle cx="' + n.x + '" cy="' + n.y + '" r="' + (n.r * 0.55) + '" fill="#0a1f23"/>';
    });
    svg.innerHTML = s;
  }

  // ───── Boot ─────
  function boot() {
    initReveal();
    animateCounters();
    initParticles();
    initCursorGlow();
    drawHeroChart();
    drawNocMap();
    window.addEventListener("resize", function () {
      clearTimeout(window.__heroRT);
      window.__heroRT = setTimeout(function () {
        drawHeroChart();
        drawNocMap();
      }, 120);
    });
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();


/* ═════ S55M · Globally-mounted Request Demo modal ═════ */
(function () {
  "use strict";
  function buildModal() {
    if (document.getElementById("e1-demo-modal")) return;
    var html = ''
      + '<div class="e1-modal-bd" id="e1-demo-modal" role="dialog" aria-modal="true" aria-labelledby="e1demoH">'
      +   '<div class="e1-modal">'
      +     '<div class="e1-modal-head">'
      +       '<div class="icobox"><i class="bi bi-rocket-takeoff-fill"></i></div>'
      +       '<div>'
      +         '<h3 id="e1demoH">Request a Demo</h3>'
      +         '<p>See the platform live. Our team replies within 1 business day.</p>'
      +       '</div>'
      +       '<button class="e1-modal-close" aria-label="Close" data-close>&times;</button>'
      +     '</div>'
      +     '<div class="e1-modal-alert" id="e1demoMsg"></div>'
      +     '<form id="e1demoForm" class="e1-modal-body">'
      +       '<div class="e1-modal-grid">'
      +         '<div><label for="e1d_name">Your name <span class="req">*</span></label>'
      +           '<input type="text" id="e1d_name" name="name" required maxlength="120" placeholder="Full name" data-testid="demo-name"></div>'
      +         '<div><label for="e1d_email">Email <span class="req">*</span></label>'
      +           '<input type="email" id="e1d_email" name="email" required maxlength="120" placeholder="name@company.com" data-testid="demo-email"></div>'
      +         '<div><label for="e1d_phone">Phone <span class="req">*</span></label>'
      +           '<input type="tel" id="e1d_phone" name="phone" required maxlength="20" placeholder="+91 XXXXXXXXXX" data-testid="demo-phone"></div>'
      +         '<div><label for="e1d_company">Company / ISP <span class="req">*</span></label>'
      +           '<input type="text" id="e1d_company" name="company" required maxlength="120" placeholder="Your ISP name" data-testid="demo-company"></div>'
      +       '</div>'
      +       '<div style="margin-top:14px;"><label for="e1d_customers">Number of customers <span class="req">*</span></label>'
      +         '<select id="e1d_customers" name="customers" required data-testid="demo-customers">'
      +           '<option value="">Select user count</option>'
      +           '<option value="up_to_100">Up to 100 (Basic)</option>'
      +           '<option value="up_to_300">Up to 300 (Pro)</option>'
      +           '<option value="up_to_500">Up to 500 (Premium)</option>'
      +           '<option value="up_to_1000">Up to 1,000 (Standard)</option>'
      +           '<option value="up_to_5000">Up to 5,000 (Enterprise)</option>'
      +           '<option value="unlimited">5,000+ / Unlimited</option>'
      +         '</select></div>'
      +       '<div style="margin-top:14px;"><label for="e1d_notes">Anything we should know? <span style="color:#64748B;font-weight:500;">(optional)</span></label>'
      +         '<textarea id="e1d_notes" name="notes" rows="3" maxlength="800" placeholder="Migrating from another platform? Specific use case?"></textarea></div>'
      +     '</form>'
      +     '<div class="e1-modal-foot">'
      +       '<button type="button" class="e1-modal-cancel" data-close>Cancel</button>'
      +       '<button type="submit" form="e1demoForm" class="e1-modal-submit" id="e1demoBtn" data-testid="demo-submit">'
      +         '<i class="bi bi-send-fill"></i> Request Demo</button>'
      +     '</div>'
      +     '<div class="e1-modal-foot-note"><i class="bi bi-shield-check"></i> Your details stay private — used only to contact you about this demo.</div>'
      +   '</div>'
      + '</div>';
    var wrap = document.createElement("div");
    wrap.innerHTML = html;
    document.body.appendChild(wrap.firstChild);

    var bd = document.getElementById("e1-demo-modal");
    var form = document.getElementById("e1demoForm");
    var btn = document.getElementById("e1demoBtn");
    var msg = document.getElementById("e1demoMsg");

    function close() { bd.classList.remove("show"); document.body.style.overflow = ""; }
    bd.querySelectorAll("[data-close]").forEach(function (el) { el.addEventListener("click", close); });
    bd.addEventListener("click", function (e) { if (e.target === bd) close(); });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") close(); });

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      msg.className = "e1-modal-alert";
      var fd = new FormData(form);
      var original = btn.innerHTML;
      btn.disabled = true;
      btn.innerHTML = '<i class="bi bi-arrow-repeat"></i> Sending...';
      fetch("/api/demo-request", { method: "POST", body: fd })
        .then(function (r) { return r.json().catch(function () { return { success: true, message: "Request received." }; }); })
        .then(function (j) {
          if (j.success !== false) {
            msg.className = "e1-modal-alert show ok";
            msg.innerHTML = '<i class="bi bi-check-circle-fill"></i> ' + (j.message || "Thanks! We'll be in touch within 1 business day.");
            form.reset();
            setTimeout(close, 2500);
          } else {
            msg.className = "e1-modal-alert show err";
            msg.innerHTML = '<i class="bi bi-exclamation-circle-fill"></i> ' + (j.message || "Could not submit. Please try again.");
          }
        })
        .catch(function () {
          msg.className = "e1-modal-alert show err";
          msg.innerHTML = '<i class="bi bi-wifi-off"></i> Network error. Please try again or email support@autoispbilling.com';
        })
        .finally(function () { btn.disabled = false; btn.innerHTML = original; });
    });
  }

  function isDemoTrigger(el) {
    if (!el) return false;
    if (el.matches && el.matches("[data-demo-trigger]")) return true;
    var href = el.getAttribute && el.getAttribute("href");
    if (href === "#demo" || href === "/#demo") return true;
    var tid = el.getAttribute && el.getAttribute("data-testid");
    if (tid === "hero-trial" || tid === "nav-trial" || tid === "hero-demo") return true;
    return false;
  }

  function openDemo(e) {
    if (e) { e.preventDefault(); e.stopPropagation(); }
    buildModal();
    var bd = document.getElementById("e1-demo-modal");
    if (bd) {
      bd.classList.add("show");
      document.body.style.overflow = "hidden";
      setTimeout(function () {
        var n = document.getElementById("e1d_name");
        if (n) n.focus();
      }, 60);
    }
  }
  // Expose globally so any custom button can call window.e1OpenDemo()
  window.e1OpenDemo = openDemo;

  function bindTriggers() {
    document.addEventListener("click", function (e) {
      var t = e.target;
      while (t && t !== document.body) {
        if (isDemoTrigger(t)) { openDemo(e); return; }
        t = t.parentElement;
      }
    }, true);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bindTriggers);
  } else {
    bindTriggers();
  }
})();
/* ═════ /S55M ═════ */
