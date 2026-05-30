/* SinketVPS Web Terminal - frontend logic
 * - multiple tabs (tmux backed sessions)
 * - next-level glossy black UI, mobile control keys, zoom, copy/paste
 */
(function () {
  "use strict";

  const socket = io({ transports: ["websocket", "polling"] });

  const termwrap = document.getElementById("termwrap");
  const tabbar = document.getElementById("tabbar");
  const addtab = document.getElementById("addtab");
  const dimsEl = document.getElementById("dims");

  let fontSize = parseInt(localStorage.getItem("sk_font") || "14", 10);
  const tabs = {};           // id -> {term, fit, el, tabEl, name}
  let activeId = null;
  let tabCounter = 0;

  const modifiers = { ctrl: false, alt: false };

  // ---------- theme: deep black + cyan accent ----------
  const theme = {
    background: "#08090b",
    foreground: "#e6eaef",
    cursor: "#19e3ff",
    cursorAccent: "#08090b",
    selectionBackground: "#19e3ff55",
    black: "#0a0c0e", red: "#ff5f56", green: "#27c93f", yellow: "#ffbd2e",
    blue: "#0a84ff", magenta: "#bf5af2", cyan: "#19e3ff", white: "#d1d6dc",
    brightBlack: "#5a6068", brightRed: "#ff6e67", brightGreen: "#5af78e",
    brightYellow: "#f4f99d", brightBlue: "#57c7ff", brightMagenta: "#ff6ac1",
    brightCyan: "#5cf0ff", brightWhite: "#ffffff"
  };

  function toast(msg) {
    const t = document.getElementById("toast");
    t.textContent = msg;
    t.classList.add("show");
    clearTimeout(t._t);
    t._t = setTimeout(() => t.classList.remove("show"), 1400);
  }

  // ---------- create a tab + terminal ----------
  function createTab(existingId, name) {
    const id = existingId || ("t" + Date.now() + "_" + (++tabCounter));
    const dispName = name || ("bash " + (Object.keys(tabs).length + 1));

    const el = document.createElement("div");
    el.className = "terminal-instance";
    termwrap.appendChild(el);

    const term = new Terminal({
      fontSize: fontSize,
      fontFamily: 'Menlo, Monaco, "SF Mono", "Courier New", monospace',
      theme: theme,
      cursorBlink: true,
      scrollback: 5000,
      allowProposedApi: true,
      macOptionIsMeta: true,
      convertEol: false,
    });
    const fit = new FitAddon.FitAddon();
    term.loadAddon(fit);
    try { term.loadAddon(new WebLinksAddon.WebLinksAddon()); } catch (e) {}
    term.open(el);

    term.onData((data) => {
      let out = data;
      if (modifiers.ctrl) {
        const code = data.toUpperCase().charCodeAt(0);
        if (code >= 64 && code <= 95) out = String.fromCharCode(code - 64);
        else if (code >= 97 && code <= 122) out = String.fromCharCode(code - 96);
        clearMod("ctrl");
      }
      if (modifiers.alt) {
        out = "\x1b" + out;
        clearMod("alt");
      }
      socket.emit("input", { id: id, data: out });
    });

    term.onSelectionChange(() => {
      const sel = term.getSelection();
      if (sel && sel.length) copyText(sel, true);
    });

    const tabEl = document.createElement("div");
    tabEl.className = "tab";
    tabEl.innerHTML = '<span class="led"></span><span class="tname"></span><span class="x">×</span>';
    tabEl.querySelector(".tname").textContent = dispName;
    tabEl.addEventListener("click", (e) => {
      if (e.target.classList.contains("x")) return;
      activate(id);
      focusKeyboard();
    });
    tabEl.querySelector(".x").addEventListener("click", (e) => {
      e.stopPropagation();
      closeTab(id);
    });
    tabbar.insertBefore(tabEl, addtab);

    tabs[id] = { term, fit, el, tabEl, name: dispName };

    socket.emit("start", { id: id, rows: term.rows || 24, cols: term.cols || 80 });

    activate(id);
    setTimeout(() => doFit(id), 60);
    return id;
  }

  function activate(id) {
    if (!tabs[id]) return;
    Object.keys(tabs).forEach((k) => {
      tabs[k].el.classList.toggle("active", k === id);
      tabs[k].tabEl.classList.toggle("active", k === id);
    });
    activeId = id;
    setTimeout(() => { doFit(id); tabs[id].term.focus(); }, 30);
  }

  function closeTab(id) {
    if (!tabs[id]) return;
    socket.emit("kill", { id: id });
    tabs[id].term.dispose();
    tabs[id].el.remove();
    tabs[id].tabEl.remove();
    delete tabs[id];
    const remaining = Object.keys(tabs);
    if (remaining.length) activate(remaining[remaining.length - 1]);
    else createTab();
  }

  function doFit(id) {
    const t = tabs[id || activeId];
    if (!t) return;
    try {
      t.fit.fit();
      socket.emit("resize", { id: id || activeId, rows: t.term.rows, cols: t.term.cols });
      if ((id || activeId) === activeId && dimsEl) {
        dimsEl.textContent = t.term.cols + "×" + t.term.rows;
      }
    } catch (e) {}
  }

  // ---------- socket handlers ----------
  socket.on("output", (msg) => {
    const t = tabs[msg.id];
    if (t) t.term.write(msg.data);
  });

  socket.on("term_closed", (msg) => {
    if (tabs[msg.id]) {
      tabs[msg.id].term.write("\r\n\x1b[33m[session ended]\x1b[0m\r\n");
    }
  });

  socket.on("ready", () => {
    fetch("/api/sessions").then(r => r.json()).then((list) => {
      if (Array.isArray(list) && list.length) {
        list.forEach((sname) => {
          const id = sname.replace(/^sk_/, "t");
          if (!tabs[id]) createTab(id, "session");
        });
      } else if (Object.keys(tabs).length === 0) {
        createTab();
      }
    }).catch(() => {
      if (Object.keys(tabs).length === 0) createTab();
    });
  });

  socket.on("connect_error", () => toast("Connection error, retrying…"));
  socket.on("disconnect", () => toast("Disconnected"));

  // ---------- copy / paste ----------
  function copyText(text, silent) {
    if (!text) return;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(
        () => { if (!silent) toast("Copied"); },
        () => fallbackCopy(text, silent)
      );
    } else fallbackCopy(text, silent);
  }
  function fallbackCopy(text, silent) {
    const ta = document.createElement("textarea");
    ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.appendChild(ta); ta.select();
    try { document.execCommand("copy"); if (!silent) toast("Copied"); } catch (e) {}
    document.body.removeChild(ta);
  }
  async function pasteText() {
    let text = "";
    try {
      if (navigator.clipboard && navigator.clipboard.readText) {
        text = await navigator.clipboard.readText();
      }
    } catch (e) {}
    if (!text) text = prompt("Paste here:") || "";
    if (text && activeId) {
      socket.emit("input", { id: activeId, data: text });
      toast("Pasted");
    }
  }
  document.getElementById("pasteBtn").addEventListener("click", pasteText);
  document.getElementById("copyBtn").addEventListener("click", () => {
    const t = tabs[activeId];
    if (t) {
      const sel = t.term.getSelection();
      if (sel) copyText(sel);
      else toast("Select text first");
    }
  });

  // ---------- zoom ----------
  function setFont(delta) {
    fontSize = Math.max(8, Math.min(28, fontSize + delta));
    localStorage.setItem("sk_font", fontSize);
    Object.keys(tabs).forEach((k) => { tabs[k].term.options.fontSize = fontSize; });
    setTimeout(() => doFit(activeId), 30);
    toast("Font " + fontSize + "px");
  }
  document.getElementById("zoomIn").addEventListener("click", () => setFont(1));
  document.getElementById("zoomOut").addEventListener("click", () => setFont(-1));

  let pinchDist = 0;
  termwrap.addEventListener("touchstart", (e) => {
    if (e.touches.length === 2) {
      pinchDist = Math.hypot(
        e.touches[0].clientX - e.touches[1].clientX,
        e.touches[0].clientY - e.touches[1].clientY);
    }
  }, { passive: true });
  termwrap.addEventListener("touchmove", (e) => {
    if (e.touches.length === 2) {
      const d = Math.hypot(
        e.touches[0].clientX - e.touches[1].clientX,
        e.touches[0].clientY - e.touches[1].clientY);
      if (Math.abs(d - pinchDist) > 30) { setFont(d > pinchDist ? 1 : -1); pinchDist = d; }
    }
  }, { passive: true });

  // ---------- add tab ----------
  addtab.addEventListener("click", () => { createTab(); focusKeyboard(); });

  // ---------- mobile control keys ----------
  function clearMod(name) {
    modifiers[name] = false;
    document.querySelectorAll('.kb[data-toggle="' + name + '"]').forEach((b) =>
      b.classList.remove("toggled"));
  }
  const seqMap = {
    Escape: "\x1b", Tab: "\t",
    ArrowUp: "\x1b[A", ArrowDown: "\x1b[B",
    ArrowRight: "\x1b[C", ArrowLeft: "\x1b[D",
    Home: "\x1b[H", End: "\x1b[F",
  };
  document.querySelectorAll(".kb").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      const tog = btn.getAttribute("data-toggle");
      if (tog) {
        modifiers[tog] = !modifiers[tog];
        btn.classList.toggle("toggled", modifiers[tog]);
        focusKeyboard();
        return;
      }
      const key = btn.getAttribute("data-key");
      const txt = btn.getAttribute("data-text");
      let data = key ? seqMap[key] : txt;
      if (data == null) return;
      if (modifiers.alt) { data = "\x1b" + data; clearMod("alt"); }
      if (activeId) socket.emit("input", { id: activeId, data: data });
      focusKeyboard();
    });
  });

  // ---------- keyboard focus ----------
  function focusKeyboard() {
    if (activeId && tabs[activeId]) tabs[activeId].term.focus();
  }
  termwrap.addEventListener("click", () => focusKeyboard());

  // ---------- window controls (cosmetic) ----------
  document.getElementById("lightClose").addEventListener("click", () => {
    if (activeId) closeTab(activeId);
  });
  document.getElementById("lightFull").addEventListener("click", () => {
    if (document.fullscreenElement) document.exitFullscreen();
    else document.documentElement.requestFullscreen().catch(() => {});
  });

  // ---------- resize ----------
  let rT;
  function onResize() { clearTimeout(rT); rT = setTimeout(() => doFit(activeId), 120); }
  window.addEventListener("resize", onResize);
  if (window.visualViewport) window.visualViewport.addEventListener("resize", onResize);

})();
