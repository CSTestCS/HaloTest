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
let catalog, profile, env, saveTimer, logSeen = 0, polling = false;

async function api(path, body) {
  const res = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}
const say = (m) => { $("#status").textContent = m; };
const save = () => { clearTimeout(saveTimer); saveTimer = setTimeout(() => api("/api/profile", profile).catch((e) => say(`Error: ${e.message}`)), 400); };

function pieceSelect(slot, value, { inherit = false, label }) {
  const sel = el("select", { "aria-label": label });
  if (inherit) sel.append(el("option", { value: "" }, "Same as above"));
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
  $("#colors").replaceChildren(...["primary", "secondary"].map((ch) => {
    const input = el("input", { type: "color", id: `c-${ch}`, value: profile.colors[ch].toLowerCase() });
    input.addEventListener("input", () => { profile.colors[ch] = input.value.toUpperCase(); save(); });
    return el("div", { class: "row" }, el("label", { for: input.id }, `${ch[0].toUpperCase()}${ch.slice(1)} color`), input);
  }));
  $("#slots").replaceChildren(...Object.entries(catalog.slots).map(([slot, label]) => {
    const sel = pieceSelect(slot, profile.armor[slot], { label });
    sel.id = `s-${slot}`;
    sel.addEventListener("change", () => { profile.armor[slot] = sel.value; save(); });
    return el("div", { class: "row" }, el("label", { for: sel.id }, label), sel);
  }));
  $("#games").replaceChildren(...Object.entries(catalog.games).map(([gid, g]) => {
    const ov = profile.overrides[gid] || {};
    return el("div", { class: "game" }, el("h3", {}, g.name), ...Object.entries(catalog.slots).map(([slot, label]) => {
      const sel = pieceSelect(slot, ov[slot] ?? "", { inherit: true, label: `${g.name} ${label}` });
      sel.addEventListener("change", () => {
        const o = (profile.overrides[gid] ||= {});
        if (sel.value) o[slot] = sel.value; else delete o[slot];
        save();
      });
      return el("div", { class: "line" }, el("span", {}, label), sel);
    }));
  }));
}

function gameNote(gid) {
  const g = catalog.games[gid];
  if (!env.windows) return ["bad", "Applying needs Windows"];
  if (!env.kits[gid]) return ["bad", `Needs the free ${g.tools_name} (Steam › Library › Tools)`];
  if (gid === "halo1") return ["bad", "Halo CE's tools can't be automated yet; skipped"];
  if (!env.armorytool) return ["bad", "ArmoryTool.exe is missing from the download"];
  return ["ok", "Ready"];
}

function renderGames() {
  $("#gamelist").replaceChildren(...Object.entries(catalog.games).map(([gid, g]) => {
    const box = el("input", { type: "checkbox", id: `camp-${gid}`, "aria-label": `Apply to ${g.name}` });
    box.checked = profile.campaign.games.includes(gid);
    box.addEventListener("change", () => {
      const set = new Set(profile.campaign.games);
      box.checked ? set.add(gid) : set.delete(gid);
      profile.campaign.games = Object.keys(catalog.games).filter((x) => set.has(x));
      save();
    });
    const [cls, text] = gameNote(gid);
    return el("li", {}, box, el("label", { class: "name", for: box.id }, g.name), el("span", { class: `note ${cls}` }, text));
  }));
  const mcc = env.mcc ? "" : "Couldn't find Halo: The Master Chief Collection. Set its folder under Settings.";
  say(mcc);
  $("#paths").replaceChildren(
    pathRow("mcc", "MCC folder", env.mcc),
    ...Object.entries(catalog.games).map(([gid, g]) => pathRow(gid, g.tools_name, env.kits[gid])));
}

function pathRow(key, label, value) {
  const input = el("input", { id: `p-${key}`, value: value || "", placeholder: "Not found" });
  return el("div", { class: "row" }, el("label", { for: input.id }, label), input);
}

function showReport(kind, r) {
  if (!r) return;
  const items = [];
  if (kind === "restore") {
    items.push(el("li", {}, Object.keys(r).length ? `Restored: ${Object.entries(r).map(([g, n]) => `${catalog.games[g].name} (${n} maps)`).join(", ")}` : "Nothing to restore."));
  } else {
    for (const [g, maps] of Object.entries(r.installed)) items.push(el("li", {}, `${catalog.games[g].name}: installed ${maps.length} levels`));
    for (const [g, why] of Object.entries(r.skipped)) items.push(el("li", {}, `${catalog.games[g].name}: skipped — ${why}`));
    for (const [g, why] of Object.entries(r.failed)) items.push(el("li", { class: "bad" }, `${catalog.games[g].name}: failed — ${why}`));
    for (const d of r.dropped) items.push(el("li", {}, `Left off: ${d}`));
  }
  $("#jobstate").after(el("ul", { class: "summary" }, ...items));
}

async function poll() {
  if (polling) return;
  polling = true;
  try {
    for (;;) {
      const j = await api(`/api/job?since=${logSeen}`);
      if (j.lines.length) {
        const log = $("#log");
        log.textContent += j.lines.join("\n") + "\n";
        log.scrollTop = log.scrollHeight;
        logSeen = j.total;
      }
      const busy = j.state === "running";
      $("#apply").disabled = $("#restore").disabled = busy;
      $("#jobstate").textContent = busy ? (j.kind === "restore" ? "Restoring…" : "Working… you can leave this open.")
        : j.state === "done" ? (j.kind === "restore" ? "Originals restored." : "Done. Start MCC with mods (EAC off).")
        : j.state === "error" ? "Stopped with an error (see below)." : "";
      if (!busy) { document.querySelectorAll(".summary").forEach((n) => n.remove()); showReport(j.kind, j.report); break; }
      await new Promise((r) => setTimeout(r, 1000));
    }
  } finally { polling = false; }
}

async function start(path, body) {
  $("#progress").hidden = false;
  $("#log").textContent = "";
  logSeen = 0;
  try { await api(path, body); poll(); } catch (e) { say(`Error: ${e.message}`); }
}

async function init() {
  [catalog, profile, env] = await Promise.all([api("/api/catalog"), api("/api/profile"), api("/api/env")]);
  renderLoadout();
  renderGames();
  $("#apply").addEventListener("click", () => start("/api/apply", profile));
  $("#restore").addEventListener("click", () => {
    if (confirm("Put MCC's original campaign maps back?")) start("/api/restore", {});
  });
  $("#savepaths").addEventListener("click", async () => {
    const kits = {};
    for (const gid of Object.keys(catalog.games)) kits[gid] = $(`#p-${gid}`).value.trim();
    try { env = { ...env, ...(await api("/api/settings", { mcc: $("#p-mcc").value.trim(), kits })) }; renderGames(); say("Folders saved."); }
    catch (e) { say(`Error: ${e.message}`); }
  });
  const j = await api("/api/job");
  if (j.state === "running") { $("#progress").hidden = false; poll(); }
}
init().catch((e) => say(`Error: ${e.message}`));
