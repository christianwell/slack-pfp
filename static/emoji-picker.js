// Searchable workspace-emoji autocomplete with live preview.
// Attaches to any <input data-emoji> and validates against the Slack
// workspace's real custom emoji (via /api/emojis server-side search), so you
// can tell instantly whether a status emoji shortcode will actually render.
// Search is server-side because the workspace can hold 50k+ emojis.
(function () {
  function clean(v) { return (v || "").trim().replace(/^:|:$/g, ""); }
  function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }

  function loadMeta() {
    fetch("/api/emojis").then((r) => r.json()).then((d) => {
      const meta = document.getElementById("emoji-meta");
      if (!meta) return;
      const when = d.updated_at ? new Date(d.updated_at * 1000).toLocaleString() : "never";
      meta.textContent = d.count
        ? `${d.count.toLocaleString()} workspace emojis · updated ${when}`
        : "No emojis loaded — add the emoji:read scope, then refresh.";
    }).catch(() => {});
  }

  function attach(input) {
    const wrap = document.createElement("div");
    wrap.style.cssText = "position:relative; display:flex; align-items:center; gap:8px;";
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);

    const preview = document.createElement("span");
    preview.style.cssText = "width:26px; height:26px; flex:0 0 26px; display:flex; align-items:center; justify-content:center; font-size:18px;";
    wrap.appendChild(preview);

    const menu = document.createElement("div");
    menu.style.cssText = "position:absolute; top:100%; left:0; right:0; z-index:50; background:#11131a;" +
      "border:1px solid #2b2f3d; border-radius:9px; margin-top:4px; max-height:240px; overflow:auto; display:none;";
    wrap.appendChild(menu);

    function setPreview(url, name, known) {
      preview.innerHTML = "";
      if (url) {
        const img = document.createElement("img");
        img.src = url; img.style.cssText = "max-width:26px; max-height:26px;";
        preview.appendChild(img);
        preview.title = `:${name}: (custom emoji ✓)`;
      } else if (known === false && name) {
        preview.textContent = "?"; preview.style.color = "#c79ad0";
        preview.title = "Not a custom emoji in this workspace (may be a standard emoji, or will show as unknown)";
      } else {
        preview.title = "";
      }
    }

    function resolvePreview() {
      const name = clean(input.value);
      if (!name) { setPreview("", "", null); return; }
      fetch("/api/emojis?name=" + encodeURIComponent(name))
        .then((r) => r.json())
        .then((d) => setPreview(d.url, name, !!d.url))
        .catch(() => {});
    }

    const search = debounce(() => {
      const q = clean(input.value);
      if (!q) { menu.style.display = "none"; return; }
      fetch("/api/emojis?q=" + encodeURIComponent(q))
        .then((r) => r.json())
        .then((d) => {
          const matches = d.matches || [];
          if (!matches.length) { menu.style.display = "none"; return; }
          menu.innerHTML = "";
          matches.forEach((m) => {
            const row = document.createElement("div");
            row.style.cssText = "display:flex; align-items:center; gap:8px; padding:6px 10px; cursor:pointer; font-size:.85rem;";
            row.onmouseenter = () => (row.style.background = "#232838");
            row.onmouseleave = () => (row.style.background = "transparent");
            const img = document.createElement("img");
            img.src = m.url; img.loading = "lazy";
            img.style.cssText = "width:20px; height:20px; object-fit:contain;";
            const label = document.createElement("span");
            label.textContent = `:${m.name}:`;
            row.appendChild(img); row.appendChild(label);
            row.onmousedown = (e) => {
              e.preventDefault();
              input.value = `:${m.name}:`;
              menu.style.display = "none";
              setPreview(m.url, m.name, true);
              input.dispatchEvent(new Event("change", { bubbles: true }));
            };
            menu.appendChild(row);
          });
          menu.style.display = "block";
        })
        .catch(() => {});
    }, 180);

    input.addEventListener("input", () => { resolvePreview(); search(); });
    input.addEventListener("focus", search);
    input.addEventListener("blur", () => setTimeout(() => (menu.style.display = "none"), 150));
    resolvePreview();
  }

  function initAll() {
    document.querySelectorAll("input[data-emoji]").forEach(attach);
    loadMeta();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initAll);
  } else {
    initAll();
  }
})();
