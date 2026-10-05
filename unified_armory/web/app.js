"use strict";
const $ = (s) => document.querySelector(s);
const el = (tag, attrs = {}, ...kids) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) n.setAttribute(k, v);
  }
  n.append(...kids);
  return n;
};
let catalog, profile, statusSeq = 0;

async function api(path, body) {
  const res = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}
const say = (m) => { $("#status").textContent = m; };
const pieceName = (uid) => {
  if (uid === "default") return null;
  const game = uid.split("/")[0];
  for (const list of Object.values(catalog.pieces)) {
    const p = list.find((x) => x.uid === uid);
    if (p) return { name: p.name, game: catalog.games[game].name };
  }
  return { name: uid, game: "" };
};

function pieceSelect(slot, value, { includeInherit = false, label }) {
  const sel = el("select", { "aria-label": label });
  if (includeInherit) sel.append(el("option", { value: "" }, "Same as loadout"));
  sel.append(el("option", { value: "default" }, "Each game's own"));
  for (const [gid, g] of Object.entries(catalog.games)) {
    const opts = catalog.pieces[slot].filter((p) => p.game === gid);
    if (!opts.length) continue;
    const grp = el("optgroup", { label: g.name });
    for (const p of opts) grp.append(el("option", { value: p.uid }, p.name));
    sel.append(grp);
  }
  sel.value = value;
  return sel;
}

function renderLoadout() {
  const colors = $("#colors");
  colors.replaceChildren();
  for (const ch of ["primary", "secondary"]) {
    const hex = el("span", { class: "hex" }, profile.colors[ch]);
    const input = el("input", { type: "color", id: `c-${ch}`, value: profile.colors[ch].toLowerCase() });
    input.addEventListener("input", () => { profile.colors[ch] = input.value.toUpperCase(); hex.textContent = profile.colors[ch]; });
    colors.append(el("div", { class: "row" }, el("label", { for: `c-${ch}` }, `${ch[0].toUpperCase()}${ch.slice(1)} color`),
      el("div", {}, input, hex)));
  }
  const slots = $("#slots");
  slots.replaceChildren();
  for (const [slot, label] of Object.entries(catalog.slots)) {
    const sel = pieceSelect(slot, profile.armor[slot], { label });
    sel.id = `s-${slot}`;
    sel.addEventListener("change", () => { profile.armor[slot] = sel.value; renderGames(); refreshStatus(); });
    slots.append(el("div", { class: "row" }, el("label", { for: sel.id }, label), sel));
  }
}

function renderGames() {
  const root = $("#games");
  root.replaceChildren();
  for (const [gid, g] of Object.entries(catalog.games)) {
    const on = profile.campaign.games.includes(gid);
    const toggle = el("input", { type: "checkbox", id: `camp-${gid}` });
    toggle.checked = on;
    toggle.addEventListener("change", () => {
      const set = new Set(profile.campaign.games);
      toggle.checked ? set.add(gid) : set.delete(gid);
      profile.campaign.games = Object.keys(catalog.games).filter((x) => set.has(x));
      renderGames(); refreshStatus();
    });
    const card = el("article", { class: `game${on ? "" : " off"}` },
      el("div", { class: "head" }, el("h3", {}, g.name), el("label", { class: "toggle", for: toggle.id }, toggle, "Build")));
    const ov = profile.overrides[gid] || {};
    for (const [slot, label] of Object.entries(catalog.slots)) {
      const effective = ov[slot] ?? profile.armor[slot];
      const sel = pieceSelect(slot, ov[slot] ?? "", { includeInherit: true, label: `${g.name} ${label}` });
      sel.addEventListener("change", () => {
        const o = (profile.overrides[gid] ||= {});
        if (sel.value) o[slot] = sel.value; else delete o[slot];
        renderGames(); refreshStatus();
      });
      const p = pieceName(effective);
      card.append(el("div", { class: "line" },
        el("span", { class: "slot" }, label),
        sel,
        el("span", { class: "from" }, p ? `${p.name} · from ${p.game}` : `${g.name}'s own`)));
    }
    if (g.verify) card.append(el("p", { class: "verify" }, g.verify));
    root.append(card);
  }
}

async function refreshStatus() {
  const seq = ++statusSeq;
  try {
    const { models } = await api("/api/status", profile);
    if (seq !== statusSeq) return;
    $("#models").replaceChildren(...models.map((m) =>
      el("li", { class: m.ready ? "ok" : "missing", title: m.ready ? "extracted" : "not extracted yet" },
        `${catalog.games[m.game].name}: ${m.model}`)));
  } catch (e) { say(`Error: ${e.message}`); }
}

function showResult(r) {
  const box = $("#result");
  const items = [];
  if (r.built.length) items.push(el("p", {}, `Ready to install: ${r.built.map((g) => catalog.games[g].name).join(", ")}. `,
    "Each game folder's NOTES.md has the steps."));
  if (r.extract.length) {
    items.push(el("p", {}, "Extract these first, then build again:"));
    items.push(el("ul", {}, ...r.extract.map((l) => el("li", {}, l.replace(/\*\*/g, "").replace(/`/g, "")))));
  }
  for (const [g, e] of Object.entries(r.errors)) items.push(el("p", { class: "verify" }, `${catalog.games[g].name}: ${e}`));
  box.replaceChildren(el("h2", {}, "Build"), el("p", { class: "hint" }, "Output: ", el("code", {}, r.out_dir)), ...items);
  box.hidden = false;
}

async function init() {
  [catalog, profile] = await Promise.all([api("/api/catalog"), api("/api/profile")]);
  $("#name").value = profile.name;
  $("#name").addEventListener("input", (e) => { profile.name = e.target.value; });
  $("#save").addEventListener("click", async () => {
    try { say(`Saved to ${(await api("/api/profile", profile)).saved}`); } catch (e) { say(`Error: ${e.message}`); }
  });
  $("#build").addEventListener("click", async () => {
    say("Building…");
    try { const r = await api("/api/build", profile); showResult(r); say(""); refreshStatus(); }
    catch (e) { say(`Error: ${e.message}`); }
  });
  renderLoadout();
  renderGames();
  refreshStatus();
}
init().catch((e) => say(`Error: ${e.message}`));
