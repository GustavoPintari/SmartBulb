"use strict";
const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];

const PRESETS = ["#ff0000", "#ff6400", "#ffc800", "#c8ff00", "#00ff00", "#00ffaa",
                 "#00ffff", "#0080ff", "#0000ff", "#8c00ff", "#ff00ff", "#ff0078"];
const DAYS = ["S", "T", "Q", "Q", "S", "S", "D"];  // seg..dom
const DAY_FULL = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"];
const ROLE_LABEL = { admin: "Administrador", user: "Usuário", guest: "Convidado" };
const SCENE_CATEGORIES = { all: "Todas", work: "Trabalho", relax: "Relaxar", fun: "Diversão", mine: "Minhas" };
const ACTION_LABEL = {
  on: "Ligar", off: "Desligar", white: "Branco", color: "Cor", brightness: "Brilho", scene: "Cena",
  favorite: "Favorito", wake: "Despertar", sleep: "Adormecer", circadian: "Ritmo circadiano",
};
const ACTIVITY_LABEL = {
  on: "ligou", off: "desligou", color: "mudou a cor", white: "luz branca", brightness: "brilho",
  scene: "cena", favorite: "favorito", timer: "timer", wake: "despertar gradual", sleep: "adormecer gradual",
  circadian: "ritmo circadiano", pomodoro: "pomodoro", flash: "alerta", undo: "desfez", presence: "simulação de presença",
  login: "entrou", password: "alterou senha", user_add: "criou usuário", user_role: "mudou papel",
  user_delete: "excluiu usuário", scene_add: "criou cena", favorite_add: "salvou favorito",
};

const ACCENTS = [
  ["#e8a557", "Âmbar suave"], ["#f59e0b", "Laranja"], ["#d9b56c", "Dourado"], ["#ee8b6f", "Coral"],
  ["#f07fae", "Rosa"], ["#a08cf0", "Lavanda"], ["#5b9cf5", "Azul"], ["#4cc3a0", "Menta"],
];
let info = null, state = null, target = null, tab = "white", sceneFilter = "all", busy = false, userTab = false;
let editingScenes = false, hiddenScenes = new Set();

// accent colour: the user's choice, with dark or light text depending on its brightness
function applyAccent(hex) {
  const root = document.documentElement.style;
  if (!hex) { root.removeProperty("--accent"); root.removeProperty("--accent-text"); return; }
  const [r, g, b] = [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16) / 255);
  const luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b;
  root.setProperty("--accent", hex);
  root.setProperty("--accent-text", luminance > 0.5 ? "#1a1a1a" : "#ffffff");
}
try { applyAccent(localStorage.getItem("sb.accent")); } catch {}

async function savePrefs(patch) {
  const prefs = await api("/api/me/prefs", "PUT", { ...info.prefs, ...patch });
  info.prefs = prefs;
  return prefs;
}
let mainPicker, scenePicker;

// --- utilities

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") node.className = v;
    else if (k === "style") node.style.cssText = v;
    else if (k.startsWith("on")) node[k] = v;
    else if (k === "dataset") Object.assign(node.dataset, v);
    else if (v !== undefined && v !== null && v !== false) node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c !== null && c !== undefined) node.append(c);
  return node;
}

function toast(msg, type = "err") {
  const t = $("#toast");
  t.textContent = msg;
  t.className = type;
  // a popover sits in the top layer, so the toast also shows above open dialogs
  if (t.showPopover) {
    if (t.matches(":popover-open")) t.hidePopover();
    t.showPopover();
  }
  requestAnimationFrame(() => requestAnimationFrame(() => t.classList.add("show")));
  clearTimeout(toast.h);
  toast.h = setTimeout(() => t.classList.remove("show"), type === "ok" ? 1800 : 3800);
}

function flash(node, cls) {
  if (!node) return;
  node.classList.remove(cls);
  void node.offsetWidth;  // restart the animation
  node.classList.add(cls);
  setTimeout(() => node.classList.remove(cls), 700);
}

function withTarget(path) {
  return target ? `${path}${path.includes("?") ? "&" : "?"}target=${encodeURIComponent(target)}` : path;
}

async function api(path, method = "GET", body) {
  const res = await fetch(path, {
    method, headers: body !== undefined ? { "Content-Type": "application/json" } : {},
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401) { location.replace("/login"); throw new Error("sessão expirada"); }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = typeof j.detail === "string" ? j.detail : j.detail.map(d => d.msg).join("; ");
    } catch {}
    throw new Error(detail);
  }
  return res.status === 204 ? null : res.json();
}

/* Run an action with visible feedback: spinner while it runs, a green pulse and
   a short confirmation when it worked, a shake and the error when it didn't. */
async function act(node, path, body, { ok, method = "POST" } = {}) {
  if (busy) { flash(node, "shake"); return null; }
  busy = true;
  node?.classList.add("loading");
  node?.setAttribute("aria-busy", "true");
  try {
    const result = await api(withTarget(path), method, body);
    if (result && "on" in result) render(result);
    flash(node, "done");
    if (ok) toast(typeof ok === "function" ? ok(result) : ok, "ok");
    navigator.vibrate?.(12);
    return result;
  } catch (e) {
    flash(node, "shake");
    toast(e.message);
    return null;
  } finally {
    busy = false;
    node?.classList.remove("loading");
    node?.removeAttribute("aria-busy");
  }
}

function ask({ title, text = "", label = "", type = "text", value = "", confirm = "OK", input = true }) {
  const dlg = $("#dlg-ask");
  $("#ask-title").textContent = title;
  $("#ask-text").textContent = text;
  $("#ask-text").hidden = !text;
  $("#ask-label").hidden = !input;
  $("#ask-label span").textContent = label;
  const field = $("#ask-input");
  field.type = type; field.value = value; field.required = input;
  $("#ask-ok").textContent = confirm;
  dlg.returnValue = "";
  dlg.showModal();
  if (input) field.focus();
  return new Promise(resolve => {
    dlg.onclose = () => resolve(dlg.returnValue === "ok" ? (input ? field.value : true) : null);
  });
}

// --- colour helpers

const clamp = (v, lo = 0, hi = 1) => Math.min(hi, Math.max(lo, v));
const toHex = (rgb) => "#" + rgb.map(v => Math.round(v).toString(16).padStart(2, "0")).join("");
const fromHex = (hex) => [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16));
const validHex = (v) => /^#?[0-9a-f]{6}$/i.test(v.trim());
const normHex = (v) => "#" + v.trim().replace("#", "").toLowerCase();

function hsvToRgb(h, s, v) {
  const f = (n) => { const k = (n + h / 60) % 6; return v - v * s * Math.max(0, Math.min(k, 4 - k, 1)); };
  return [f(5), f(3), f(1)].map(x => x * 255);
}

function rgbToHsv(r, g, b) {
  r /= 255; g /= 255; b /= 255;
  const max = Math.max(r, g, b), d = max - Math.min(r, g, b);
  let h = 0;
  if (d) h = max === r ? ((g - b) / d) % 6 : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
  return [(h * 60 + 360) % 360, max ? d / max : 0, max];
}

function warmToHex(t) {  // 0 = warm, 100 = cold
  const a = [255, 179, 92], b = [207, 230, 255];
  return toHex(a.map((v, i) => v + (b[i] - v) * t / 100));
}

const kelvin = (t) => Math.round((2700 + 3800 * t / 100) / 100) * 100;

// the bulb stores colour as HSV, so a hex read back can differ slightly from what was sent
function colorDistance(a, b) {
  const [x, y] = [fromHex(a), fromHex(b)];
  return Math.max(...x.map((v, i) => Math.abs(v - y[i])));
}

function tempLabel(v) { v = +v; return v < 34 ? "quente" : v > 66 ? "fria" : "neutra"; }
const sceneOf = (id) => info?.scenes.find(s => s.id === id);
const sceneLabel = (id) => sceneOf(id)?.label || id;

function scenePreview(scene) {
  const c = scene.colors.length ? scene.colors : ["#888888"];
  if (scene.transition === "jump" && c.length > 1) {
    const step = 100 / c.length;
    return `linear-gradient(90deg, ${c.map((x, i) => `${x} ${i * step}% ${(i + 1) * step}%`).join(", ")})`;
  }
  if (c.length > 1) return `linear-gradient(120deg, ${c.join(", ")})`;
  return `radial-gradient(circle at 30% 35%, color-mix(in srgb, ${c[0]} 55%, white), ${c[0]} 75%)`;
}

// --- colour picker: saturation/value square, hue bar and hex/RGB fields

class Picker {
  constructor(root, { onChange, onCommit, applyLabel = "Aplicar" } = {}) {
    this.h = 30; this.s = 1; this.v = 1; this.dragging = false;
    this.onChange = onChange; this.onCommit = onCommit;
    this.root = root;
    root.classList.add("picker");
    root.append(
      this.sv = el("div", { class: "sv", tabindex: "0", role: "slider", "aria-label": "Saturação e intensidade" },
                   this.knob = el("div", { class: "sv-knob" })),
      this.hue = el("input", { type: "range", class: "hue-range", min: "0", max: "359", "aria-label": "Matiz" }),
      el("div", { class: "picker-fields" },
        this.preview = el("span", { class: "pk-preview" }),
        el("label", { class: "field hex" }, "HEX",
           this.hexInput = el("input", { maxlength: "7", spellcheck: "false", autocomplete: "off" })),
        ...["R", "G", "B"].map((n, i) => el("label", { class: "field num" }, n,
           el("input", { type: "number", min: "0", max: "255", class: "pk-c", "data-i": i }))),
        onCommit ? (this.apply = el("button", { type: "button", class: "primary" }, applyLabel)) : null,
      ),
    );
    this.rgbInputs = $$(".pk-c", root);

    const move = (e) => {
      const r = this.sv.getBoundingClientRect();
      this.s = clamp((e.clientX - r.left) / r.width);
      this.v = clamp(1 - (e.clientY - r.top) / r.height, 0.02);
      this.draw(); this.onChange?.(this.hex);
    };
    this.sv.addEventListener("pointerdown", (e) => { this.sv.setPointerCapture(e.pointerId); this.dragging = true; move(e); });
    this.sv.addEventListener("pointermove", (e) => { if (this.dragging) move(e); });
    const release = () => { if (!this.dragging) return; this.dragging = false; this.onCommit?.(this.hex, this.sv); };
    this.sv.addEventListener("pointerup", release);
    this.sv.addEventListener("pointercancel", release);
    this.sv.addEventListener("keydown", (e) => {
      const d = { ArrowLeft: [-0.03, 0], ArrowRight: [0.03, 0], ArrowUp: [0, 0.03], ArrowDown: [0, -0.03] }[e.key];
      if (d) { e.preventDefault(); this.s = clamp(this.s + d[0]); this.v = clamp(this.v + d[1], 0.02); this.draw(); this.onChange?.(this.hex); }
      if (e.key === "Enter") this.onCommit?.(this.hex, this.sv);
    });
    this.hue.oninput = () => { this.h = +this.hue.value; this.draw(); this.onChange?.(this.hex); };
    this.hue.onchange = () => this.onCommit?.(this.hex, this.hue.parentElement.querySelector(".pk-preview"));
    this.hexInput.oninput = () => {
      if (validHex(this.hexInput.value)) { this.set(normHex(this.hexInput.value), this.hexInput); this.onChange?.(this.hex); }
    };
    this.rgbInputs.forEach(input => input.oninput = () => {
      const rgb = this.rgbInputs.map(i => clamp(+i.value || 0, 0, 255));
      this.set(toHex(rgb), input); this.onChange?.(this.hex);
    });
    [this.hexInput, ...this.rgbInputs].forEach(input => input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); this.onCommit?.(this.hex, this.apply); }
    }));
    if (this.apply) this.apply.onclick = () => this.onCommit(this.hex, this.apply);
    this.draw();
  }

  get hex() { return toHex(hsvToRgb(this.h, this.s, this.v)); }
  get busy() { return this.dragging || this.root.contains(document.activeElement); }

  set(hex, skip) {
    [this.h, this.s, this.v] = rgbToHsv(...fromHex(hex));
    this.draw(skip);
  }

  draw(skip) {
    const hex = this.hex;
    this.sv.style.background = `linear-gradient(to top, #000, transparent), linear-gradient(to right, #fff, transparent), hsl(${this.h} 100% 50%)`;
    this.knob.style.left = `${this.s * 100}%`;
    this.knob.style.top = `${(1 - this.v) * 100}%`;
    this.knob.style.background = hex;
    this.hue.value = Math.round(this.h);
    this.preview.style.background = hex;
    if (skip !== this.hexInput) this.hexInput.value = hex.toUpperCase();
    const rgb = fromHex(hex);
    this.rgbInputs.forEach((input, i) => { if (skip !== input) input.value = rgb[i]; });
  }
}

// --- rendering the light state

function setConn(ok, text) {
  $("#dot").className = "dot " + (ok ? "ok" : "err");
  $("#conn").textContent = text;
}

function currentTarget() {
  return info?.targets.find(t => t.id === target) || info?.targets[0];
}

function render(s) {
  state = s;
  const glow = s.mode === "colour" && s.color ? s.color
             : s.mode === "scene" && sceneOf(s.scene) ? sceneOf(s.scene).colors[0]
             : warmToHex(s.temperature);
  document.documentElement.style.setProperty("--glow", glow);
  $("#power").classList.toggle("on", s.on);
  $("#power").setAttribute("aria-pressed", s.on);
  $("#c-hero").classList.toggle("on", s.on);
  $("#power-label").textContent = s.on ? "Ligada" : "Desligada";

  const parts = [];
  if (s.on) {
    if (s.circadian) parts.push(`Circadiano · ${kelvin(s.temperature)} K · ${s.brightness}%`);
    else if (s.mode === "scene") parts.push(`Cena ${sceneLabel(s.scene) || "personalizada"}`);
    else if (s.mode === "colour") parts.push(`Cor ${s.color?.toUpperCase() || ""} · ${s.brightness}%`);
    else parts.push(`Branco ${tempLabel(s.temperature)} · ${s.brightness}%`);
  }
  if (s.timer_seconds > 0) parts.push(`desliga em ${Math.ceil(s.timer_seconds / 60)} min`);
  $("#power-sub").textContent = parts.join(" · ");
  $("#timer .cancel").hidden = !(s.timer_seconds > 0);
  $("#undo").hidden = !s.can_undo;

  const members = $("#members");
  members.replaceChildren();
  if (s.members.length > 1) {
    for (const m of s.members) {
      members.append(el("span", { class: `member${m.on ? " on" : ""}${m.online ? "" : " offline"}`,
                                  title: m.online ? "" : "sem resposta" }, m.name));
    }
  }

  renderEffect(s.effect);
  $("#circ").checked = s.circadian;

  const modeTab = { white: "white", colour: "colour", scene: "scene" }[s.mode];
  if (modeTab && !userTab) setTab(modeTab, false);

  if (document.activeElement !== $("#bright")) $("#bright").value = s.brightness;
  $("#bright-v").textContent = $("#bright").value + "%";
  if (document.activeElement !== $("#temp")) $("#temp").value = s.temperature;
  $("#temp-v").textContent = `${tempLabel($("#temp").value)} · ${kelvin($("#temp").value)} K`;
  if (s.color && mainPicker && !mainPicker.busy) mainPicker.set(s.color);
  $$(".swatch[data-c]").forEach(b =>
    b.classList.toggle("sel", s.mode === "colour" && !!s.color && colorDistance(b.dataset.c, s.color) < 30));
  $$(".scene").forEach(b => b.classList.toggle("sel", s.mode === "scene" && b.dataset.id === s.scene));
}

let effectTimer = null;
function renderEffect(effect) {
  const banner = $("#effect-banner");
  clearInterval(effectTimer);
  banner.hidden = !effect;
  if (!effect) return;
  const update = () => {
    let text = effect.label;
    if (effect.kind === "pomodoro") {
      text = `Pomodoro · ${effect.phase === "focus" ? "foco" : "pausa"} ${effect.cycle}/${effect.cycles}`;
    }
    if (effect.ends_at) {
      const left = Math.max(0, Math.round((effect.ends_at - Date.now()) / 1000));
      text += ` · ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`;
    }
    $("#effect-text").textContent = text;
  };
  update();
  effectTimer = setInterval(update, 1000);
}

async function refresh() {
  if (busy) return;
  try {
    render(await api(withTarget("/api/status")));
    setConn(true, events?.readyState === 1 ? "ao vivo" : "online");
  } catch (e) {
    setConn(false, "sem resposta");
  }
}

const refreshSoon = (() => {
  let h;
  return () => { clearTimeout(h); h = setTimeout(refresh, 250); };
})();

function setTab(t, fromUser = true) {
  tab = t;
  if (fromUser) userTab = true;  // don't let live updates flip the tab while browsing
  $$(".tabs[role=tablist] button").forEach(b => b.classList.toggle("active", b.dataset.tab === t));
  for (const name of ["white", "colour", "scene", "fav"]) $(`#pane-${name}`).hidden = t !== name;
  $("#bright-wrap").hidden = t === "scene" || t === "fav";
  if (fromUser && t === "white" && state?.mode !== "white") {
    act($(".tabs button[data-tab=white]"), "/api/white", {}, { ok: "Luz branca" });
  }
}

// --- info: targets, scenes, favourites, settings

async function loadInfo() {
  info = await api("/api/info");
  applyAccent(info.prefs.accent);
  try { info.prefs.accent ? localStorage.setItem("sb.accent", info.prefs.accent) : localStorage.removeItem("sb.accent"); } catch {}
  hiddenScenes = new Set(info.prefs.hidden_scenes);
  const role = info.user.role;
  document.body.className = `role-${role}`;
  $("#guest-note").hidden = role !== "guest";
  $("#user-btn").textContent = info.user.name.slice(0, 1);
  $("#user-name").textContent = info.user.name;
  $("#user-role").textContent = ROLE_LABEL[role];
  $("#logout").hidden = !info.auth;

  const saved = localStorage.getItem("sb.target");
  if (!info.targets.some(t => t.id === target)) {
    target = info.targets.some(t => t.id === saved) ? saved : info.default_target;
  }
  const sel = $("#target");
  sel.replaceChildren(...info.targets.map(t => new Option(t.kind === "group" ? `▣ ${t.name}` : t.name, t.id)));
  sel.value = target;
  sel.hidden = info.targets.length < 2;
  const nt = $("#n-target");
  const keep = nt.value;
  nt.replaceChildren(...info.targets.map(t => new Option(t.name, t.id)));
  nt.value = info.targets.some(t => t.id === keep) ? keep : target;
  nt.hidden = info.targets.length < 2;

  const now = info.circadian_now;
  $("#circ-desc").textContent = `Tom e brilho acompanham o dia · agora ${kelvin(now.temperature)} K, ${now.brightness}%`;
  $("#presence").checked = info.presence.enabled;
  const presenceTarget = info.targets.find(t => t.id === info.presence.target);
  $("#presence-desc").textContent = info.presence.enabled
    ? `Ativa em ${presenceTarget?.name || "lâmpada principal"}, do pôr do sol até 23:30`
    : "Acende e apaga ao acaso à noite, para quando você viajar";
  $("#n-trigger").querySelectorAll("option[value^=sun]").forEach(o => {
    o.disabled = !info.location;
    o.textContent = o.value === "sunrise" ? `Nascer do sol${info.sun ? ` (${info.sun.sunrise})` : ""}`
                                          : `Pôr do sol${info.sun ? ` (${info.sun.sunset})` : ""}`;
  });

  renderScenes();
  renderFavorites();
  updateValueField();
  if (role === "admin") renderAdminPlace();
}

function renderScenes() {
  const filter = $("#scene-filter");
  const shown = info.scenes.filter(s => editingScenes || !hiddenScenes.has(s.id));
  const categories = new Set(shown.map(s => s.category));
  if (sceneFilter !== "all" && !categories.has(sceneFilter)) sceneFilter = "all";
  filter.replaceChildren(...Object.entries(SCENE_CATEGORIES)
    .filter(([k]) => k === "all" || categories.has(k))
    .map(([k, label]) => el("button", {
      class: `chip${k === sceneFilter ? " on" : ""}`,
      onclick: () => { sceneFilter = k; renderScenes(); },
    }, label)));

  const list = $("#scenes");
  list.classList.toggle("editing", editingScenes);
  $("#scene-hint").hidden = !editingScenes;
  $("#edit-scenes").textContent = editingScenes ? "Concluir" : "Escolher cenas";
  $("#edit-scenes").classList.toggle("on", editingScenes);
  $("#new-scene").hidden = editingScenes;
  if (!shown.length) {
    list.replaceChildren(el("div", { class: "empty", style: "grid-column:1/-1" },
      "Todas as cenas estão escondidas. Use “Escolher cenas” para mostrar algumas."));
    return;
  }
  list.replaceChildren(...shown
    .filter(s => sceneFilter === "all" || s.category === sceneFilter)
    .map(s => {
      const hidden = hiddenScenes.has(s.id);
      const card = el("div", { class: `scene${state?.mode === "scene" && state.scene === s.id ? " sel" : ""}${hidden ? " hidden-scene" : ""}`,
                               role: editingScenes ? "checkbox" : "button", tabindex: "0", dataset: { id: s.id },
                               "aria-checked": editingScenes ? String(!hidden) : null },
        el("div", { class: "pv", style: `background:${scenePreview(s)}` }),
        el("span", { class: "nm" }, s.label),
        el("span", { class: "check", "aria-hidden": "true" }, "✓"));
      const apply = editingScenes
        ? () => { hidden ? hiddenScenes.delete(s.id) : hiddenScenes.add(s.id); renderScenes(); }
        : () => act(card, "/api/scene", { scene: s.id }, { ok: `Cena ${s.label}` });
      card.onclick = apply;
      card.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); apply(); } };
      if (s.custom) {
        card.append(el("button", {
          class: "del", title: "Excluir cena", "aria-label": `Excluir ${s.label}`,
          onclick: async (e) => {
            e.stopPropagation();
            if (!await ask({ title: "Excluir cena", text: `Excluir “${s.label}”?`, input: false, confirm: "Excluir" })) return;
            try { await api(`/api/scenes/${s.id}`, "DELETE"); toast("Cena excluída", "ok"); }
            catch (err) { toast(err.message); }
          },
        }, "✕"));
      }
      return card;
    }));

  $("#n-choice").dataset.kind = "";  // options are rebuilt by updateValueField
}

function favoritePreview(f) {
  if (f.mode === "colour") return f.color;
  if (f.mode === "scene") return sceneOf(f.scene) ? scenePreview(sceneOf(f.scene)) : "var(--hover)";
  return warmToHex(f.temperature);
}

function favoriteText(f) {
  if (f.mode === "colour") return `Cor ${f.color.toUpperCase()} · ${f.brightness}%`;
  if (f.mode === "scene") return `Cena ${sceneLabel(f.scene)}`;
  return `Branco ${tempLabel(f.temperature)} · ${f.brightness}%`;
}

function renderFavorites() {
  const list = $("#favs");
  if (!info.favorites.length) {
    list.replaceChildren(el("div", { class: "empty", style: "grid-column:1/-1" },
      "Ajuste a luz do jeito que gosta e salve aqui para usar com um toque."));
    return;
  }
  list.replaceChildren(...info.favorites.map(f => {
    const card = el("div", { class: "fav", role: "button", tabindex: "0" },
      el("span", { class: "dotpv", style: `background:${favoritePreview(f)}` }),
      el("span", {}, el("b", {}, f.name), el("small", {}, favoriteText(f))),
      el("button", {
        class: "del", title: "Excluir favorito", "aria-label": `Excluir ${f.name}`,
        onclick: async (e) => {
          e.stopPropagation();
          if (!await ask({ title: "Excluir favorito", text: `Excluir “${f.name}”?`, input: false, confirm: "Excluir" })) return;
          try { await api(`/api/favorites/${f.id}`, "DELETE"); toast("Favorito excluído", "ok"); }
          catch (err) { toast(err.message); }
        },
      }, "✕"));
    const apply = () => act(card, "/api/favorite", { favorite: f.id }, { ok: `Favorito ${f.name}` });
    card.onclick = apply;
    card.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); apply(); } };
    return card;
  }));
}

// --- controls

$("#power").onclick = () => act($("#power"), "/api/power", { on: !state?.on },
  { ok: (s) => s.on ? "Luz ligada" : "Luz desligada" });
$$(".tabs[role=tablist] button").forEach(b => b.onclick = () => setTab(b.dataset.tab));
$("#bright").oninput = (e) => $("#bright-v").textContent = e.target.value + "%";
$("#bright").onchange = (e) => act($("#bright-v"), "/api/brightness", { value: +e.target.value },
  { ok: `Brilho ${e.target.value}%` });
$("#temp").oninput = (e) => $("#temp-v").textContent = `${tempLabel(e.target.value)} · ${kelvin(e.target.value)} K`;
$("#temp").onchange = (e) => act($("#temp-v"), "/api/white", { temperature: +e.target.value },
  { ok: `Branco ${tempLabel(e.target.value)}` });
$$("[data-temp]").forEach(b => b.onclick = () =>
  act(b, "/api/white", { temperature: +b.dataset.temp }, { ok: `Branco ${b.textContent.toLowerCase()}` }));
$$("#timer .chip").forEach(b => b.onclick = () => act(b, "/api/timer", { minutes: +b.dataset.min },
  { ok: +b.dataset.min ? `Desliga em ${b.textContent}` : "Timer cancelado" }));
$("#undo").onclick = () => act($("#undo"), "/api/undo", undefined, { ok: "Desfeito" });
$("#effect-stop").onclick = () => act($("#effect-stop"), "/api/effects", undefined, { method: "DELETE", ok: "Rotina parada" });

const keepBrightness = () => (state?.on && state.mode !== "scene" ? state.brightness : undefined);
$("#swatches").replaceChildren(...PRESETS.map(c => {
  const b = el("button", { class: "swatch", style: `background:${c}`, title: c.toUpperCase(), "aria-label": c, dataset: { c } });
  b.onclick = () => act(b, "/api/color", { color: c, brightness: keepBrightness() }, { ok: `Cor ${c.toUpperCase()}` });
  return b;
}));
mainPicker = new Picker($("#picker"), {
  onCommit: (hex, node) => act(node, "/api/color", { color: hex }, { ok: `Cor ${hex.toUpperCase()}` }),
});

$("#save-fav").onclick = async () => {
  const name = await ask({ title: "Salvar favorito", label: "Nome", value: "", confirm: "Salvar" });
  if (!name) return;
  const b = $("#save-fav");
  b.classList.add("loading");
  try { await api(withTarget("/api/favorites"), "POST", { name }); flash(b, "done"); toast(`Favorito “${name}” salvo`, "ok"); }
  catch (e) { flash(b, "shake"); toast(e.message); }
  finally { b.classList.remove("loading"); }
};

// --- routines

$("#circ").onchange = async (e) => {
  const enabled = e.target.checked;
  const res = await act(e.target.closest(".switch"), "/api/circadian", { enabled },
    { method: "PUT", ok: enabled ? "Ritmo circadiano ativado" : "Ritmo circadiano desativado" });
  if (!res) e.target.checked = !enabled;
};
$("#pm-start").onclick = () => act($("#pm-start"), "/api/effects/pomodoro", {
  work: +$("#pm-work").value, rest: +$("#pm-rest").value, cycles: +$("#pm-cycles").value,
}, { ok: "Pomodoro iniciado, bom foco!" });
$$("[data-fade]").forEach(b => b.onclick = () => act(b, "/api/effects/fade",
  { direction: b.dataset.fade, minutes: +$("#fade-min").value },
  { ok: `${b.dataset.fade === "wake" ? "Despertar" : "Adormecer"} em ${$("#fade-min").value} min` }));
$("#presence").onchange = async (e) => {
  const enabled = e.target.checked;
  const sw = e.target.closest(".switch");
  sw.classList.add("loading");
  try {
    await api("/api/presence", "PUT", { enabled, target });
    flash(sw, "done");
    toast(enabled ? "Simulação de presença ativada" : "Simulação de presença desativada", "ok");
  } catch (err) { e.target.checked = !enabled; flash(sw, "shake"); toast(err.message); }
  finally { sw.classList.remove("loading"); }
};

// --- custom scene editor

const sceneDraft = { colors: ["#ff7a18", "#ff3d77", "#7a5cff"], selected: 0, transition: "fade" };
scenePicker = new Picker($("#sc-picker"), {
  onChange: (hex) => { sceneDraft.colors[sceneDraft.selected] = hex; renderSlots(); },
});

function renderSlots() {
  const slots = sceneDraft.colors.map((c, i) => el("button", {
    type: "button", class: `slot${i === sceneDraft.selected ? " sel" : ""}`, style: `background:${c}`,
    title: c.toUpperCase(), "aria-label": `Cor ${i + 1}`,
    onclick: () => { sceneDraft.selected = i; scenePicker.set(c); renderSlots(); },
  }));
  if (sceneDraft.colors.length < 8 && sceneDraft.transition !== "static") {
    slots.push(el("button", { type: "button", class: "slot add", title: "Adicionar cor", onclick: () => {
      sceneDraft.colors.push(sceneDraft.colors[sceneDraft.selected]);
      sceneDraft.selected = sceneDraft.colors.length - 1;
      renderSlots();
    } }, "+"));
  }
  if (sceneDraft.colors.length > 1) {
    slots.push(el("button", { type: "button", class: "chip small", onclick: () => {
      sceneDraft.colors.splice(sceneDraft.selected, 1);
      sceneDraft.selected = Math.max(0, sceneDraft.selected - 1);
      scenePicker.set(sceneDraft.colors[sceneDraft.selected]);
      renderSlots();
    } }, "Remover cor"));
  }
  $("#sc-slots").replaceChildren(...slots);
}

function sceneBody() {
  return {
    name: $("#sc-name").value.trim() || "Prévia",
    colors: sceneDraft.transition === "static" ? sceneDraft.colors.slice(0, 1) : sceneDraft.colors,
    transition: sceneDraft.transition, speed: +$("#sc-speed").value, brightness: +$("#sc-bright").value,
  };
}

$("#new-scene").onclick = () => {
  $("#sc-name").value = "";
  scenePicker.set(sceneDraft.colors[sceneDraft.selected]);
  renderSlots();
  $("#sc-speed-v").textContent = $("#sc-speed").value + "%";
  $("#sc-bright-v").textContent = $("#sc-bright").value + "%";
  $("#dlg-scene").showModal();
};
$$("#sc-transition button").forEach(b => b.onclick = () => {
  sceneDraft.transition = b.dataset.tr;
  $$("#sc-transition button").forEach(x => x.classList.toggle("active", x === b));
  if (b.dataset.tr === "static") sceneDraft.selected = 0;
  renderSlots();
});
$("#sc-speed").oninput = (e) => $("#sc-speed-v").textContent = e.target.value + "%";
$("#sc-bright").oninput = (e) => $("#sc-bright-v").textContent = e.target.value + "%";
$("#sc-test").onclick = () => act($("#sc-test"), "/api/scenes/preview", sceneBody(), { ok: "Prévia na lâmpada" });
$("#sc-save").onclick = async () => {
  const b = $("#sc-save");
  if (!$("#sc-name").value.trim()) { flash($("#sc-name"), "shake"); $("#sc-name").focus(); return; }
  b.classList.add("loading");
  try {
    const scene = await api("/api/scenes", "POST", sceneBody());
    $("#dlg-scene").close();
    toast(`Cena “${scene.name}” criada`, "ok");
    sceneFilter = "mine";
    await loadInfo();
    setTab("scene");
  } catch (e) { flash(b, "shake"); toast(e.message); }
  finally { b.classList.remove("loading"); }
};

$("#edit-scenes").onclick = async () => {
  if (!editingScenes) { editingScenes = true; renderScenes(); return; }
  const b = $("#edit-scenes");
  b.classList.add("loading");
  try {
    await savePrefs({ hidden_scenes: [...hiddenScenes] });
    editingScenes = false;
    renderScenes();
    flash(b, "done");
    toast(hiddenScenes.size ? `${hiddenScenes.size} cena(s) escondida(s)` : "Todas as cenas visíveis", "ok");
  } catch (e) { flash(b, "shake"); toast(e.message); }
  finally { b.classList.remove("loading"); }
};

function renderAccents() {
  const current = info.prefs.accent || ACCENTS[0][0];
  $("#accents").replaceChildren(...ACCENTS.map(([hex, name]) => el("button", {
    type: "button", class: `accent${hex === current ? " sel" : ""}`, style: `background:${hex}`,
    title: name, "aria-label": name, "aria-pressed": String(hex === current),
    onclick: async (e) => {
      const previous = info.prefs.accent;
      applyAccent(hex);
      e.currentTarget.classList.add("loading");
      try {
        await savePrefs({ accent: hex });
        try { localStorage.setItem("sb.accent", hex); } catch {}
        renderAccents();
        toast(`Cor de destaque: ${name}`, "ok");
      } catch (err) { applyAccent(previous); renderAccents(); toast(err.message); }
    },
  })));
}

// --- schedules

const newDays = new Set([0, 1, 2, 3, 4, 5, 6]);
$("#n-days").replaceChildren(...DAYS.map((d, i) => {
  const b = el("button", { type: "button", class: "chip on", title: DAY_FULL[i] }, d);
  b.onclick = () => { newDays.has(i) ? newDays.delete(i) : newDays.add(i); b.classList.toggle("on", newDays.has(i)); };
  return b;
}));

$("#n-trigger").onchange = () => {
  const sun = $("#n-trigger").value !== "time";
  $("#n-time").hidden = sun;
  $("#n-offset").hidden = !sun;
};

function updateValueField() {
  const a = $("#n-action").value, input = $("#n-value"), choice = $("#n-choice");
  const needsChoice = a === "scene" || a === "favorite";
  choice.hidden = !needsChoice;
  input.hidden = needsChoice;
  if (needsChoice && choice.dataset.kind !== a) {
    const items = a === "scene" ? info.scenes.map(s => [s.id, s.label]) : info.favorites.map(f => [f.id, f.name]);
    choice.replaceChildren(...items.map(([v, l]) => new Option(l, v)));
    if (!items.length) choice.append(new Option("nenhum favorito salvo", ""));
    choice.dataset.kind = a;
  }
  input.disabled = !["white", "color", "brightness", "wake", "sleep"].includes(a);
  input.placeholder = {
    white: "temperatura 0-100 (vazio = manter)", color: "#ff8800 ou vermelho", brightness: "brilho 1-100",
    wake: "duração em minutos (15)", sleep: "duração em minutos (15)",
  }[a] || "sem valor";
}
$("#n-action").onchange = () => { $("#n-value").value = ""; updateValueField(); };

function describeDays(days) {
  if (days.length === 7) return "todos os dias";
  if (days.join() === "0,1,2,3,4") return "dias úteis";
  if (days.join() === "5,6") return "fim de semana";
  return days.map(d => DAY_FULL[d]).join(", ");
}

function describeAction(s) {
  const label = ACTION_LABEL[s.action] || s.action;
  if (s.value === null || s.value === undefined || s.value === "") return label;
  if (s.action === "brightness") return `${label} ${s.value}%`;
  if (s.action === "white") return `${label} ${tempLabel(s.value)}`;
  if (s.action === "scene") return `${label} ${sceneLabel(s.value)}`;
  if (s.action === "favorite") return `${label} ${info.favorites.find(f => f.id === s.value)?.name || "removido"}`;
  if (s.action === "wake" || s.action === "sleep") return `${label} em ${s.value} min`;
  return `${label} ${s.value}`;
}

function describeWhen(s) {
  if (s.trigger === "time") return [s.time, ""];
  const icon = s.trigger === "sunrise" ? "☀" : "☾";
  const off = s.offset ? `${s.offset > 0 ? "+" : "−"}${Math.abs(s.offset)} min` : "";
  return [icon, `${s.trigger === "sunrise" ? "nascer" : "pôr"} do sol ${off}`.trim()];
}

function formatNext(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  const today = new Date();
  const day = d.toDateString() === today.toDateString() ? "hoje"
            : d.toDateString() === new Date(today.getTime() + 864e5).toDateString() ? "amanhã"
            : DAY_FULL[(d.getDay() + 6) % 7];
  return `próx.: ${day} ${d.toTimeString().slice(0, 5)}`;
}

async function loadSchedules() {
  if (info?.user.role === "guest") return;
  try {
    const list = await api("/api/schedules");
    const box = $("#sched-list");
    if (!list.length) { box.replaceChildren(el("div", { class: "empty" }, "Nenhum agendamento")); return; }
    const multi = info.targets.length > 1;
    box.replaceChildren(...list.map(s => {
      const [when, sub] = describeWhen(s);
      const cb = el("input", { type: "checkbox" });
      cb.checked = s.enabled;
      const row = el("div", { class: "sched" + (s.enabled ? "" : " off") },
        el("div", { class: "t" }, when, sub ? el("small", {}, sub) : null),
        el("div", { class: "d" }, el("b", {}, describeAction(s)),
           el("span", {}, [describeDays(s.days), multi && s.target ? info.targets.find(t => t.id === s.target)?.name : null,
                           s.enabled ? formatNext(s.next_run) : null].filter(Boolean).join(" · "))),
        el("label", { class: "switch", title: "Ativar/desativar" }, cb, el("i")),
        el("button", { class: "icon-btn", title: "Excluir", "aria-label": "Excluir" }, "✕"));
      cb.onchange = async () => {
        try {
          await api(`/api/schedules/${s.id}`, "PUT", { ...s, enabled: cb.checked });
          toast(cb.checked ? "Agendamento ativado" : "Agendamento pausado", "ok");
        } catch (e) { toast(e.message); cb.checked = !cb.checked; }
      };
      row.querySelector(".icon-btn").onclick = async () => {
        if (!await ask({ title: "Excluir agendamento", text: `${describeAction(s)} · ${describeDays(s.days)}`, input: false, confirm: "Excluir" })) return;
        try { await api(`/api/schedules/${s.id}`, "DELETE"); toast("Agendamento excluído", "ok"); }
        catch (e) { toast(e.message); }
      };
      return row;
    }));
  } catch (e) { toast("Erro ao carregar agendamentos: " + e.message); }
}

$("#new").onsubmit = async (e) => {
  e.preventDefault();
  const button = $("#new button[type=submit]");
  if (!newDays.size) { flash(button, "shake"); return toast("Escolha pelo menos um dia"); }
  const action = $("#n-action").value;
  const raw = action === "scene" || action === "favorite" ? $("#n-choice").value : $("#n-value").value.trim();
  const body = {
    trigger: $("#n-trigger").value, time: $("#n-time").value, offset: +$("#n-offset").value || 0,
    days: [...newDays].sort(), action, value: raw === "" ? null : raw, target: $("#n-target").value || target,
  };
  button.classList.add("loading");
  try { await api("/api/schedules", "POST", body); flash(button, "done"); toast("Agendamento criado", "ok"); }
  catch (err) { flash(button, "shake"); toast(err.message); }
  finally { button.classList.remove("loading"); }
};

// --- stats and activity

async function loadStats() {
  if (info?.user.role === "guest") return;
  try {
    const { days, watts } = await api(withTarget("/api/stats"));
    const max = Math.max(1, ...days.map(d => d.hours));
    const today = days.length - 1;
    $("#stats-bars").replaceChildren(...days.map((d, i) => {
      const date = new Date(d.date + "T12:00");
      return el("div", { class: `bar${i === today ? " today" : ""}`, title: `${d.hours.toFixed(1)} h · ${d.wh.toFixed(0)} Wh` },
        el("span", {}, d.hours >= 0.05 ? `${d.hours.toFixed(1)}h` : ""),
        el("i", { style: `height:${(d.hours / max) * 70}%` }),
        DAYS[(date.getDay() + 6) % 7]);
    }));
    const hours = days.reduce((a, d) => a + d.hours, 0);
    const kwh = days.reduce((a, d) => a + d.wh, 0) / 1000;
    $("#stats-sum").textContent = `${hours.toFixed(1)} h acesa · ${kwh.toFixed(2)} kWh (estimativa com ${watts} W)`;
  } catch {}
}

function formatWhen(iso) {
  const d = new Date(iso);
  const time = d.toTimeString().slice(0, 5);
  const today = new Date().toDateString();
  const yesterday = new Date(Date.now() - 864e5).toDateString();
  if (d.toDateString() === today) return time;
  if (d.toDateString() === yesterday) return `ontem ${time}`;
  return `${String(d.getDate()).padStart(2, "0")}/${String(d.getMonth() + 1).padStart(2, "0")} ${time}`;
}

function activityRow(a) {
  const what = [ACTIVITY_LABEL[a.action] || a.action, a.detail].filter(Boolean).join(" ");
  return el("div", { class: "act" },
    el("time", { datetime: a.ts }, formatWhen(a.ts)),
    el("span", {}, el("b", {}, a.actor), " ", what, a.target ? el("span", { class: "t" }, ` · ${a.target}`) : null));
}

async function loadActivity() {
  if (info?.user.role === "guest") return;
  try {
    const list = await api("/api/activity?limit=15");
    $("#activity").replaceChildren(...(list.length ? list.map(activityRow) : [el("div", { class: "empty" }, "Nada por aqui ainda")]));
  } catch {}
}

// --- account dialog

$$("[data-open]").forEach(b => b.onclick = () => {
  closeMenu();
  const dlg = $(`#${b.dataset.open}`);
  if (dlg.id === "dlg-account") { loadTokens(); renderAccents(); }
  if (dlg.id === "dlg-admin") { loadUsers(); renderGroups(); renderAdminPlace(); }
  dlg.showModal();
});
$$("[data-close]").forEach(b => b.onclick = () => b.closest("dialog").close());
$$("dialog").forEach(d => d.addEventListener("click", (e) => { if (e.target === d) d.close(); }));

$("#pw-form").onsubmit = async (e) => {
  e.preventDefault();
  const button = e.target.querySelector("button");
  if ($("#pw-new").value !== $("#pw-confirm").value) { flash(button, "shake"); return toast("As senhas novas não conferem"); }
  button.classList.add("loading");
  try {
    await api("/api/me/password", "POST", { current: $("#pw-current").value, new: $("#pw-new").value });
    e.target.reset();
    flash(button, "done");
    toast("Senha alterada. Outras sessões foram encerradas.", "ok");
  } catch (err) { flash(button, "shake"); toast(err.message); }
  finally { button.classList.remove("loading"); }
};

async function loadTokens() {
  $("#token-new").hidden = true;
  try {
    const tokens = await api("/api/me/tokens");
    $("#token-list").replaceChildren(...(tokens.length ? tokens.map(t => el("div", { class: "list-row" },
      el("div", { class: "grow" }, el("b", {}, t.label),
         el("small", {}, `criado ${formatWhen(t.created)}${t.last_used ? ` · usado ${formatWhen(t.last_used)}` : " · nunca usado"}`)),
      el("button", { class: "icon-btn", title: "Revogar", "aria-label": `Revogar ${t.label}`, onclick: async () => {
        if (!await ask({ title: "Revogar token", text: `Scripts usando “${t.label}” deixarão de funcionar.`, input: false, confirm: "Revogar" })) return;
        try { await api(`/api/me/tokens/${t.id}`, "DELETE"); toast("Token revogado", "ok"); loadTokens(); }
        catch (err) { toast(err.message); }
      } }, "✕"))) : [el("p", { class: "note" }, "Nenhum token criado.")]));
  } catch (err) { toast(err.message); }
}

$("#token-form").onsubmit = async (e) => {
  e.preventDefault();
  const button = e.target.querySelector("button");
  button.classList.add("loading");
  try {
    const t = await api("/api/me/tokens", "POST", { label: $("#token-label").value });
    $("#token-label").value = "";
    await loadTokens();
    $("#token-value").textContent = t.token;
    $("#token-new").hidden = false;
    flash(button, "done");
  } catch (err) { flash(button, "shake"); toast(err.message); }
  finally { button.classList.remove("loading"); }
};
$("#token-copy").onclick = async () => {
  try { await navigator.clipboard.writeText($("#token-value").textContent); toast("Token copiado", "ok"); }
  catch {
    getSelection().selectAllChildren($("#token-value"));
    toast("Selecionado: use Ctrl+C para copiar", "ok");
  }
};

// --- admin dialog

$$("#admin-tabs button").forEach(b => b.onclick = () => {
  $$("#admin-tabs button").forEach(x => x.classList.toggle("active", x === b));
  for (const name of ["users", "groups", "place"]) $(`#atab-${name}`).hidden = b.dataset.atab !== name;
});

async function loadUsers() {
  try {
    const users = await api("/api/users");
    $("#user-list").replaceChildren(...users.map(u => {
      const role = el("select", { "aria-label": `Papel de ${u.name}` },
        ...Object.entries(ROLE_LABEL).map(([v, l]) => new Option(l, v)));
      role.value = u.role;
      role.onchange = async () => {
        try { await api(`/api/users/${u.name}`, "PATCH", { role: role.value }); flash(role, "done"); toast(`${u.name} agora é ${ROLE_LABEL[role.value].toLowerCase()}`, "ok"); }
        catch (err) { role.value = u.role; flash(role, "shake"); toast(err.message); }
      };
      const me = u.name === info.user.name;
      return el("div", { class: "list-row" },
        el("div", { class: "grow" }, el("b", {}, u.name), me ? el("small", {}, "você") : null),
        role,
        el("button", { class: "chip small", onclick: async (e) => {
          const password = await ask({ title: `Nova senha para ${u.name}`, label: "Senha (mín. 8 caracteres)", type: "password", confirm: "Salvar" });
          if (!password) return;
          const b = e.target;
          b.classList.add("loading");
          try { await api(`/api/users/${u.name}`, "PATCH", { password }); flash(b, "done"); toast(`Senha de ${u.name} alterada`, "ok"); }
          catch (err) { flash(b, "shake"); toast(err.message); }
          finally { b.classList.remove("loading"); }
        } }, "Senha"),
        me ? null : el("button", { class: "icon-btn", title: "Excluir", "aria-label": `Excluir ${u.name}`, onclick: async () => {
          if (!await ask({ title: "Excluir usuário", text: `Excluir “${u.name}”? As sessões e tokens dele serão encerrados.`, input: false, confirm: "Excluir" })) return;
          try { await api(`/api/users/${u.name}`, "DELETE"); toast("Usuário excluído", "ok"); loadUsers(); }
          catch (err) { toast(err.message); }
        } }, "✕"));
    }));
  } catch (err) { toast(err.message); }
}

$("#user-form").onsubmit = async (e) => {
  e.preventDefault();
  const button = e.target.querySelector("button[type=submit]");
  button.classList.add("loading");
  try {
    const u = await api("/api/users", "POST", { username: $("#nu-name").value, password: $("#nu-pass").value, role: $("#nu-role").value });
    e.target.reset();
    flash(button, "done");
    toast(`Usuário ${u.name} criado`, "ok");
    loadUsers();
  } catch (err) { flash(button, "shake"); toast(err.message); }
  finally { button.classList.remove("loading"); }
};

function renderGroups() {
  const bulbs = info.targets.filter(t => t.kind === "bulb");
  const groups = info.targets.filter(t => t.kind === "group" && t.id !== "all");
  const name = (id) => bulbs.find(b => b.id === id)?.name || id;
  $("#group-list").replaceChildren(...(groups.length ? groups.map(g => el("div", { class: "list-row" },
    el("div", { class: "grow" }, el("b", {}, g.name), el("small", {}, g.members.map(name).join(", "))),
    el("button", { class: "icon-btn", title: "Excluir", "aria-label": `Excluir ${g.name}`, onclick: async () => {
      if (!await ask({ title: "Excluir grupo", text: `Excluir “${g.name}”? As lâmpadas não são afetadas.`, input: false, confirm: "Excluir" })) return;
      try { await api(`/api/groups/${g.id}`, "DELETE"); toast("Grupo excluído", "ok"); }
      catch (err) { toast(err.message); }
    } }, "✕"))) : [el("p", { class: "note" }, bulbs.length > 1 ? "Nenhum grupo ainda." : "Configure mais de uma lâmpada (BULBS_FILE) para criar grupos.")]));
  $("#ng-members").replaceChildren(...bulbs.map(b => el("label", {}, el("input", { type: "checkbox", value: b.id }), b.name)));
}

$("#group-form").onsubmit = async (e) => {
  e.preventDefault();
  const button = e.target.querySelector("button[type=submit]");
  const members = $$("#ng-members input:checked").map(i => i.value);
  button.classList.add("loading");
  try {
    await api("/api/groups", "POST", { name: $("#ng-name").value, members });
    e.target.reset();
    flash(button, "done");
    toast("Grupo criado", "ok");
  } catch (err) { flash(button, "shake"); toast(err.message); }
  finally { button.classList.remove("loading"); }
};

function renderAdminPlace() {
  if (document.activeElement?.closest("#loc-form")) return;
  $("#loc-lat").value = info.location?.lat ?? "";
  $("#loc-lon").value = info.location?.lon ?? "";
  $("#loc-sun").textContent = info.sun ? `Hoje: nascer do sol às ${info.sun.sunrise}, pôr do sol às ${info.sun.sunset}.`
                                       : "Sem localização: horários do sol indisponíveis.";
  $("#mqtt-status").textContent = !info.mqtt.enabled
    ? "MQTT / Home Assistant: desativado (defina MQTT_HOST para ativar)."
    : `MQTT / Home Assistant: ${info.mqtt.connected ? "conectado" : "sem conexão com o broker"}.`;
}

$("#loc-geo").onclick = () => {
  if (!navigator.geolocation) return toast("Geolocalização indisponível (precisa de HTTPS)");
  const b = $("#loc-geo");
  b.classList.add("loading");
  navigator.geolocation.getCurrentPosition((pos) => {
    b.classList.remove("loading");
    $("#loc-lat").value = pos.coords.latitude.toFixed(4);
    $("#loc-lon").value = pos.coords.longitude.toFixed(4);
    flash(b, "done");
  }, (err) => { b.classList.remove("loading"); flash(b, "shake"); toast(`Localização: ${err.message}`); });
};

$("#loc-form").onsubmit = async (e) => {
  e.preventDefault();
  const button = e.target.querySelector("button[type=submit]");
  const lat = $("#loc-lat").value, lon = $("#loc-lon").value;
  button.classList.add("loading");
  try {
    await api("/api/settings/location", "PUT", lat && lon ? { lat: +lat, lon: +lon } : {});
    flash(button, "done");
    toast(lat && lon ? "Localização salva" : "Localização removida", "ok");
  } catch (err) { flash(button, "shake"); toast(err.message); }
  finally { button.classList.remove("loading"); }
};

// --- user menu

function closeMenu() { $("#user-menu").hidden = true; $("#user-btn").setAttribute("aria-expanded", "false"); }
$("#user-btn").onclick = (e) => {
  e.stopPropagation();
  const open = $("#user-menu").hidden;
  $("#user-menu").hidden = !open;
  $("#user-btn").setAttribute("aria-expanded", String(open));
};
document.addEventListener("click", (e) => { if (!e.target.closest(".menu-wrap")) closeMenu(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeMenu(); });
$("#logout").onclick = async () => {
  try { await api("/api/logout", "POST"); } catch {}
  location.replace("/login");
};

$("#target").onchange = (e) => {
  target = e.target.value;
  localStorage.setItem("sb.target", target);
  userTab = false;
  $("#n-target").value = target;
  refresh(); loadStats();
};

// --- live updates (Server-Sent Events)

let events = null;
function connectEvents() {
  events = new EventSource("/api/events");
  events.onopen = () => { setConn(true, "ao vivo"); refreshSoon(); };
  events.onerror = () => {
    setConn(false, "reconectando…");
    api("/api/me").catch(() => {});  // redirects to the login page if the session ended
  };
  events.onmessage = (ev) => {
    const { type, data } = JSON.parse(ev.data);
    const members = currentTarget()?.members || [];
    if ((type === "status" || type === "effect") && members.includes(data.device)) refreshSoon();
    else if (type === "schedules") loadSchedules();
    else if (type === "activity") {
      if (info?.user.role !== "guest") {
        $("#activity .empty")?.remove();
        $("#activity").prepend(activityRow(data));
        while ($("#activity").children.length > 15) $("#activity").lastElementChild.remove();
      }
    } else if (type === "library" || type === "targets" || type === "settings") {
      loadInfo().then(() => { refreshSoon(); if (type === "settings") loadSchedules(); });
      if (type === "targets" && $("#dlg-admin").open) setTimeout(renderGroups, 300);
    }
  };
}

(async () => {
  try { await loadInfo(); }
  catch (e) { toast(e.message); return; }
  await refresh();
  loadSchedules(); loadStats(); loadActivity();
  connectEvents();
})();
// safety net in case the event stream is blocked by a proxy
setInterval(() => { if (!document.hidden && events?.readyState !== 1) refresh(); }, 10000);
setInterval(() => { if (!document.hidden) { loadStats(); loadInfo().catch(() => {}); } }, 10 * 60000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) { refresh(); loadSchedules(); } });
