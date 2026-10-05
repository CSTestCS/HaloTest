"use strict";
const $ = (s) => document.querySelector(s);
const el = (tag, attrs = {}, ...kids) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (v !== false && v != null) n.setAttribute(k, v);
  }
  n.append(...kids);
  return n;
};
let catalog, env, logSeen = 0, polling = false;
const chosen = new Set();

async function api(path, body) {
  const res = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}
const say = (m) => { $("#status").textContent = m; };

function note(gid) {
  const g = catalog.games[gid];
  if (!env.windows) return ["bad", "Installing needs Windows"];
  if (gid === "halo1") return ["bad", "Not supported yet (its Mod Tools can't be automated)"];
  if (!env.kits[gid]) return ["bad", `Needs the free ${g.tools_name}`];
  if (!env.armorytool) return ["bad", "ArmoryTool.exe is missing from the download"];
  return ["ok", "Ready"];
}

function render() {
  $("#gamelist").replaceChildren(...Object.entries(catalog.games).map(([gid, g]) => {
    const [cls, text] = note(gid);
    const box = el("input", { type: "checkbox", id: `g-${gid}`, "aria-label": g.name });
    box.checked = chosen.has(gid);
    box.disabled = cls !== "ok";
    box.addEventListener("change", () => { box.checked ? chosen.add(gid) : chosen.delete(gid); });
    return el("li", {}, box, el("label", { class: "name", for: box.id }, g.name), el("span", { class: `note ${cls}` }, text));
  }));
  $("#installed").textContent = env.installed ? "Unified Armory is installed. Install again after adding Mod Tools to include more armor." : "";
  say(env.mcc ? "" : "Couldn't find Halo: The Master Chief Collection. Set its folder under Settings.");
  const row = (key, label, value) => {
    const input = el("input", { id: `p-${key}`, value: value || "", placeholder: "Not found" });
    return el("div", { class: "row" }, el("label", { for: input.id }, label), input);
  };
  $("#paths").replaceChildren(row("mcc", "MCC folder", env.mcc),
    ...Object.entries(catalog.games).map(([gid, g]) => row(gid, g.tools_name, env.kits[gid])));
}

function summary(kind, r) {
  if (!r || kind !== "pack") return [];
  const items = [];
  for (const [g, b] of Object.entries(r.built)) items.push(el("li", {}, `${catalog.games[g].name}: ${b.maps} levels, ${b.pieces} armor pieces`));
  for (const [g, why] of Object.entries(r.skipped)) items.push(el("li", {}, `${catalog.games[g].name}: skipped (${why})`));
  for (const [g, why] of Object.entries(r.failed)) items.push(el("li", { class: "bad" }, `${catalog.games[g].name}: failed (${why})`));
  if (r.left_out.length) items.push(el("li", {}, `Not included: ${r.left_out.join("; ")}`));
  return items;
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
      $("#install").disabled = $("#uninstall").disabled = busy;
      $("#jobstate").textContent = busy ? (j.kind === "uninstall" ? "Removing…" : "Building the armor pack… you can leave this running.")
        : j.state === "done" ? (j.kind === "uninstall" ? "Removed." : "Installed. Start MCC with mods and press F8.")
        : j.state === "error" ? "Stopped with an error (see below)." : "";
      if (!busy) {
        document.querySelectorAll(".summary").forEach((n) => n.remove());
        const items = summary(j.kind, j.report);
        if (items.length) $("#jobstate").after(el("ul", { class: "summary" }, ...items));
        env = await api("/api/env");
        render();
        break;
      }
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
  [catalog, env] = await Promise.all([api("/api/catalog"), api("/api/env")]);
  for (const gid of Object.keys(catalog.games)) if (note(gid)[0] === "ok") chosen.add(gid);
  render();
  $("#install").addEventListener("click", () => {
    if (!chosen.size) { say("Choose at least one campaign."); return; }
    start("/api/pack", { games: [...chosen] });
  });
  $("#uninstall").addEventListener("click", () => {
    if (confirm("Remove Unified Armory from MCC?")) start("/api/uninstall", {});
  });
  $("#savepaths").addEventListener("click", async () => {
    const kits = {};
    for (const gid of Object.keys(catalog.games)) kits[gid] = $(`#p-${gid}`).value.trim();
    try { env = { ...env, ...(await api("/api/settings", { mcc: $("#p-mcc").value.trim(), kits })) }; render(); say("Folders saved."); }
    catch (e) { say(`Error: ${e.message}`); }
  });
  const j = await api("/api/job");
  if (j.state === "running") { $("#progress").hidden = false; poll(); }
}
init().catch((e) => say(`Error: ${e.message}`));
