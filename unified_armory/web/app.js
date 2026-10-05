"use strict";
const $ = (sel) => document.querySelector(sel);
const el = (tag, attrs = {}, ...kids) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v);
  }
  for (const k of kids) n.append(k);
  return n;
};
const QUALITY = { exact: "", nearest: "", similar: "closest", default: "no match", override: "override" };
const STRATEGY = {
  change_colors: "Campaign: colors applied to the player biped",
  variant_swap: "Campaign: full armor + colors via the multiplayer Spartan",
  native: "Campaign: reads your MCC armor natively",
  bake: "Campaign: armor baked into tags",
};

let catalog, profile, pending;

async function api(path, body) {
  const res = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}
const status = (msg) => { $("#status").textContent = msg; };

function familiesForSlot(slot) {
  const seen = new Set();
  for (const g of Object.values(catalog.games)) {
    for (const o of g.slots[slot]?.options || []) seen.add(o.family);
  }
  return [...seen].sort((a, b) => catalog.families[a].name.localeCompare(catalog.families[b].name));
}

function renderUniversal() {
  const colors = $("#colors");
  colors.replaceChildren();
  for (const ch of ["primary", "secondary"]) {
    const input = el("input", { type: "color", id: `c-${ch}`, value: profile.colors[ch] });
    input.addEventListener("input", () => { profile.colors[ch] = input.value.toUpperCase(); refresh(); });
    colors.append(el("div", { class: "row" }, el("label", { for: `c-${ch}` }, `${ch[0].toUpperCase() + ch.slice(1)} color`), input));
  }
  const slots = $("#slots");
  slots.replaceChildren();
  for (const [slot, label] of Object.entries(catalog.universal_slots)) {
    const sel = el("select", { id: `s-${slot}` });
    const fams = familiesForSlot(slot);
    if (!fams.includes(profile.armor[slot])) fams.unshift(profile.armor[slot]);
    for (const f of fams) sel.append(el("option", { value: f }, catalog.families[f].name));
    sel.value = profile.armor[slot];
    sel.addEventListener("change", () => { profile.armor[slot] = sel.value; refresh(); });
    slots.append(el("div", { class: "row" }, el("label", { for: `s-${slot}` }, label), sel));
  }
}

function overrideSelect(gameId, kind, key, options, current) {
  const sel = el("select", { "aria-label": `Override ${key}` }, el("option", { value: "" }, "auto"));
  for (const o of options) sel.append(el("option", { value: o.id }, o.name));
  sel.value = current || "";
  sel.addEventListener("change", () => {
    const ov = (profile.overrides[gameId] ||= { slots: {}, colors: {} });
    ov[kind] ||= {};
    if (sel.value) ov[kind][key] = sel.value; else delete ov[kind][key];
    refresh();
  });
  return sel;
}

function renderGames(resolved, plans) {
  const root = $("#games");
  root.replaceChildren();
  for (const [gid, game] of Object.entries(catalog.games)) {
    const r = resolved[gid], plan = plans[gid], ov = profile.overrides[gid] || {};
    const toggle = el("input", { type: "checkbox", id: `camp-${gid}` });
    toggle.checked = profile.campaign.games.includes(gid);
    toggle.addEventListener("change", () => {
      const set = new Set(profile.campaign.games);
      toggle.checked ? set.add(gid) : set.delete(gid);
      profile.campaign.games = Object.keys(catalog.games).filter((g) => set.has(g));
      refresh();
    });
    const card = el("article", { class: "game" },
      el("header", {}, el("h3", {}, game.name),
        el("label", { class: "toggle", for: `camp-${gid}` }, toggle, "Campaign")),
      el("p", { class: "strategy" }, STRATEGY[plan.strategy] + (plan.edits.length ? ` · ${plan.edits.length} tag edits` : "")));

    for (const [ch, c] of Object.entries(r.colors)) {
      const q = c.source === "override" ? "override" : "nearest";
      card.append(el("div", { class: "line" },
        el("div", { class: "what" }, el("div", { class: "slot" }, `${ch} color`),
          el("span", { class: "chip", style: `background:${c.swatch.hex}` }), c.swatch.name,
          el("span", { class: `q q-${q}` }, q === "override" ? "override" : `ΔE ${c.delta_e}`)),
        overrideSelect(gid, "colors", ch, game.palette, ov.colors?.[ch])));
    }
    for (const [slot, s] of Object.entries(r.slots)) {
      card.append(el("div", { class: "line" },
        el("div", { class: "what" }, el("div", { class: "slot" }, s.label + (s.campaign ? "" : " · MP only")),
          s.option.name, el("span", { class: `q q-${s.quality}` }, QUALITY[s.quality])),
        overrideSelect(gid, "slots", slot, game.slots[slot].options, ov.slots?.[slot])));
    }
    if (!Object.keys(r.slots).length) card.append(el("p", { class: "empty" }, "No armor pieces in this game, only colors."));
    if (gid === "reach") {
      const bake = el("input", { type: "checkbox", id: "bake-reach" });
      bake.checked = profile.campaign.bake_reach;
      bake.addEventListener("change", () => { profile.campaign.bake_reach = bake.checked; refresh(); });
      card.append(el("label", { class: "toggle wrap", for: "bake-reach" }, bake, "Bake armor into tags instead of reading MCC profile"));
    }
    if (plan.warnings.length) card.append(el("ul", { class: "warns" }, ...plan.warnings.map((w) => el("li", {}, w))));
    root.append(card);
  }
}

async function refresh() {
  const mine = pending = Symbol();
  try {
    const { resolved, plans } = await api("/api/resolve", profile);
    if (mine === pending) { renderGames(resolved, plans); status(""); }
  } catch (e) { status(`Error: ${e.message}`); }
}

async function init() {
  [catalog, profile] = await Promise.all([api("/api/catalog"), api("/api/profile")]);
  $("#name").value = profile.name;
  $("#name").addEventListener("input", (e) => { profile.name = e.target.value; });
  $("#save").addEventListener("click", async () => {
    try { status(`Saved to ${(await api("/api/profile", profile)).saved}`); } catch (e) { status(`Error: ${e.message}`); }
  });
  $("#build").addEventListener("click", async () => {
    try {
      const r = await api("/api/export", profile);
      const n = Object.values(r.edits).reduce((a, b) => a + b, 0);
      status(`Built ${n} campaign tag edits to ${r.out_dir}. Each game's CHECKLIST.md has the steps.`);
    } catch (e) { status(`Error: ${e.message}`); }
  });
  renderUniversal();
  refresh();
}
init().catch((e) => status(`Error: ${e.message}`));
