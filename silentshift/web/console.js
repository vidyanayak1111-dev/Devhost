// SilentShift console
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const pct = v => (v === null || v === undefined) ? "n/a" : Math.round(v * 100) + "%";
const nice = l => (l || "unknown").replace("_", " ");
const LAYER_ICON = { model: "fa-brain", serving: "fa-server", safety: "fa-shield", request_config: "fa-sliders" };
const TABS = {
  overview: ["Overview", "Is the AI behind the name still the one you tested?"],
  identity: ["Who's behind it?", "Fingerprint matching - names the model that is really answering"],
  reports: ["Reports", "Observed evidence kept apart from the likely cause"],
  chat: ["Live chatbot", "The production app your users talk to"],
  lab: ["Provider lab", "Play the provider: change things silently and see if the monitor notices"],
  evaluation: ["Evaluation", "Randomised controlled changes, scored blind against a hidden log"],
  privacy: ["Privacy", "Proof that no user prompts are stored"],
  how: ["How it works", "Pipeline, attribution rules and how SilentShift compares"],
};

let S = null, mode = "demo", lastJobId = null, lastJobState = null, shownReportId = null, reportsCache = [], evalTab = null,
  demoActive = "none", lastJobRunning = false, events = [], timelineCache = [], fpCache = null, tab = "overview";

async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || r.status);
  return data;
}
const post = (p, body) => api(p, { method: "POST", body: JSON.stringify(body || {}) });

function toast(msg, alert) {
  const t = document.createElement("div");
  t.className = "toast" + (alert ? " alert" : ""); t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 4500);
}

// ---------------------------------------------------------------- tabs
function show(name) {
  if (!TABS[name]) name = "overview";
  tab = name;
  document.querySelectorAll(".tab-page").forEach(s => s.hidden = s.id !== "tab-" + name);
  document.querySelectorAll("#nav a").forEach(a => a.classList.toggle("on", a.dataset.tab === name));
  $("pageTitle").textContent = TABS[name][0];
  $("pageSub").textContent = TABS[name][1];
  if (name === "chat" && !$("chatFrame").src) $("chatFrame").src = "/";
  if (name === "identity") loadFp();
  if (name === "lab") { loadDemo(); loadServer(); loadEvents(); loadModels(); }
  if (name === "privacy") loadMeta();
  if (name === "evaluation") loadResults();
  if (name === "reports") loadHistory();
  if (location.hash !== "#" + name) history.replaceState(null, "", "#" + name);
}
document.querySelectorAll("#nav a").forEach(a => a.onclick = () => show(a.dataset.tab));
document.querySelectorAll("[data-go]").forEach(a => a.onclick = () => show(a.dataset.go));

// ---------------------------------------------------------------- status loop
async function refresh() {
  let s;
  try { s = await api("/monitor/status"); } catch (e) {
    $("heroTitle").textContent = "Server offline - is start.bat running?";
    $("statusText").textContent = "offline"; return;
  }
  S = s; mode = s.mode;
  $("modeChip").textContent = mode === "real" ? "REAL · Ollama/Groq" : "DEMO · simulated";
  $("modeChip").className = "chip " + (mode === "real" ? "real" : "");
  $("evalHint").innerHTML = mode === "real"
    ? "Real mode: every change once + a few no-change trials on your real models - expect <b>20-40 min</b>, run it before you present."
    : "Demo mode: simulated provider, 40 trials take about 30 s.";
  renderHero(s); renderJob(s.job); renderPill(s);
  $("watchToggle").checked = s.watch.enabled;
  if (document.activeElement !== $("liveToggle")) $("liveToggle").checked = !!s.watch.live_trigger;
  if (document.activeElement !== $("afterToggle")) $("afterToggle").checked = !!s.watch.after_change;
  if (document.activeElement !== $("watchMin")) $("watchMin").value = s.watch.minutes;

  const running = s.job && s.job.state === "running";
  if (s.latest && s.latest.id !== shownReportId && !running) {
    const first = shownReportId === null;
    renderReport(s.latest); renderOverviewReport(s.latest); renderWho(s.latest); loadHistory(); loadTimeline();
    if (!first && s.latest.changed) toast("⚠ Behaviour change detected - likely cause: " + nice(s.latest.likely_cause), true);
  }
  if (s.job && s.job.id === lastJobId && lastJobState === "running" && !running) {
    toast(s.job.state === "error" ? "Failed: " + s.job.error : s.job.kind + " finished", s.job.state === "error");
    if (s.job.kind === "evaluation") loadResults();
    if (s.job.kind === "download") loadModels();
    if (s.job.kind === "fingerprinting") loadFp();
    loadHistory(); loadMeta(); loadDemo(); loadTimeline(); loadEvents();
  }
  lastJobRunning = !!running;
  lastJobId = s.job ? s.job.id : null;
  lastJobState = s.job ? s.job.state : null;
}

function renderPill(s) {
  const running = s.job && s.job.state === "running";
  let cls = "", txt = "No baseline";
  if (running) { cls = "busy"; txt = s.job.kind.split(" (")[0] + "…"; }
  else if (s.latest && s.latest.changed) { cls = "alert"; txt = "Change: " + nice(s.latest.likely_cause); }
  else if (s.latest) { cls = "ok"; txt = "Normal"; }
  else if (s.baseline) { txt = "Baseline ready"; }
  $("statusPill").className = "status-pill " + cls;
  $("statusText").textContent = txt;
  $("navAlerts").hidden = !(s.latest && s.latest.changed);
  $("btnCheckTop").disabled = running || !s.baseline;
}

function renderHero(s) {
  const running = s.job && s.job.state === "running";
  let cls = "ok", title = "Behaving normally", sub = "", meta = [];
  if (!s.baseline) {
    cls = ""; title = "No baseline yet";
    sub = "Click <b>Learn baseline</b> while nothing is changed. The canaries are asked several times to learn the normal answers <i>and</i> how much they normally vary.";
  } else if (s.latest && s.latest.changed) {
    cls = "alert"; title = "Behaviour change detected";
    sub = s.latest.likely_cause
      ? `Likely cause: <b>${esc(nice(s.latest.likely_cause))}</b> (${pct(s.latest.confidence)} · ${esc(s.latest.confidence_label)} confidence). ${esc(s.latest.likely_cause_meaning)}`
      : "Something changed, but the evidence does not point to one layer.";
    const recent = reportsCache.filter(r => !r.trial).slice(0, 3);
    const persistent = recent.length === 3 && recent.every(r => r.changed && r.likely_cause === s.latest.likely_cause);
    if (persistent && demoActive === "none") {
      sub += `<div class="stale">The last 3 checks all found this and no lab change is active - probably the <b>new normal</b>
        (you switched the model, or changed the server by hand). If that's expected:
        <br><button class="btn small" id="acceptNormal">This is the new normal → learn baseline</button></div>`;
    }
  } else if (s.latest) {
    sub = "The last check matched the baseline on every signal.";
    const idn = s.latest.identity;
    if (idn && idn.best) sub += ` Identity confirmed: <b>${esc(idn.best.label)}</b> (${pct(idn.best.score)} fingerprint match).`;
  } else { cls = ""; title = "Baseline learned"; sub = "Click <b>Run check</b> to compare the current model with the baseline."; }
  if (running) { cls = "busy"; title = `Running ${s.job.kind}…`; sub = "The result appears here when it is done."; }
  $("hero").className = "card hero " + cls;
  $("heroTitle").textContent = title;
  $("heroSub").innerHTML = sub;
  if ($("acceptNormal")) $("acceptNormal").onclick = () => {
    if (confirm("Learn a new baseline from the CURRENT behaviour? Future checks compare against it.")) startJob("/monitor/baseline");
  };
  if (s.baseline && !running) {
    meta.push(`Baseline <b>${esc(s.baseline.id)}</b> · ${esc(s.baseline.time)}`);
    meta.push(`Normal speed <b>${s.baseline.speed_mean} ± ${s.baseline.speed_sd}</b> tok/s`);
    meta.push(`Canary set <b>${esc(s.baseline.canary_set)}</b>`);
  }
  if (s.latest) meta.push(`Last check <b>${esc(s.latest.time)}</b>`);
  if (s.watch.enabled) meta.push(`<i class="fa-solid fa-rotate"></i> auto-check every <b>${s.watch.minutes} min</b>`);
  $("heroMeta").innerHTML = meta.map(m => `<span>${m}</span>`).join("");
  ["btnBaseline", "btnEval", "btnBuildFp"].forEach(b => $(b).disabled = running);
  $("btnCheck").disabled = $("btnDeep").disabled = running || !s.baseline;
}

function renderJob(j) {
  const showIt = j && j.state === "running";
  $("progress").hidden = !showIt;
  if (!showIt) return;
  const p = j.total ? Math.min(100, Math.round(j.done / j.total * 100)) : 3;
  $("progressBar").style.width = p + "%";
  $("progressText").textContent = `${j.kind}: ${j.message}`;
}

// ---------------------------------------------------------------- KPIs + overview
function renderKpis() {
  const own = reportsCache.filter(r => !r.trial);
  const changed = own.filter(r => r.changed).length;
  const last = own[0];
  const idn = last && last.identity && last.identity.best;
  const k = [
    ["Checks run", own.length, `${changed} found a change`, ""],
    ["Drift score", last ? last.drift_score ?? (last.changed ? 100 : 0) : "-", "0 = identical to baseline", last && last.changed ? "bad" : "ok"],
    ["Identity", idn ? pct(idn.score) : "-", idn ? esc(idn.label) : "no fingerprints yet", idn && idn.score >= .75 ? "ok" : ""],
    ["Probe cost (last)", last ? last.probe_cost.calls + " calls" : "-", last ? `${last.probe_cost.tokens} tokens · ${last.probe_cost.seconds}s · ~$${last.probe_cost.est_usd}` : "", ""],
  ];
  $("kpis").innerHTML = k.map(([l, v, s, c]) => `<div class="kpi"><div class="l">${l}</div><div class="v ${c}">${v}</div><div class="s">${s}</div></div>`).join("");
}

function matchRows(ranked, n = 4) {
  return ranked.slice(0, n).map((x, i) => `<div class="mrow ${i === 0 ? "top" : ""}" title="tokenizer ${pct(x.tokenizer)} · answers ${pct(x.answers)} · behaviour ${pct(x.behaviour)}">
    <span class="nm">${esc(x.label)}</span><div class="mtrack"><div class="mfill" style="width:${Math.round(x.score * 100)}%"></div></div>
    <span class="muted">${pct(x.score)}</span></div>`).join("");
}

function renderWho(r) {
  const idn = r && r.identity;
  if (!idn) return;
  if (idn.kind === "skipped") { $("whoBody").innerHTML = `<p class="who-sub">${esc(idn.text)}</p>`; return; }
  const moved = idn.baseline && idn.kind !== "unknown" && idn.best.core !== idn.baseline.core;
  const kind = moved ? "moved" : idn.kind;
  const label = { match: "Identity confirmed", close: "Closest match", unknown: "Unknown model", moved: "A different model is answering" }[kind];
  $("whoBody").innerHTML = `
    <div class="who-kind ${kind}">${label}</div>
    <div class="who-name">${esc(idn.kind === "unknown" ? "Not in your library" : idn.best.label)}</div>
    <div class="who-sub">${esc(idn.text)}.<br>At baseline: <b>${esc(idn.baseline.label)}</b> (${pct(idn.baseline.score)}).</div>
    ${matchRows(idn.ranked)}`;
}

function renderOverviewReport(r) {
  $("ovReportTime").textContent = "· " + r.time;
  if (!r.changed) {
    $("ovReport").innerHTML = `<div class="ov-mini"><i class="fa-solid fa-circle-check ic" style="color:var(--ok)"></i><div>
      <b>No change.</b> <span class="muted">Every signal inside the normal range · ${r.probe_cost.calls} canary calls (sentinel only).</span></div></div>`;
    return;
  }
  $("ovReport").innerHTML = `<div class="ov-mini"><i class="fa-solid ${LAYER_ICON[r.likely_cause] || "fa-question"} ic" style="color:var(--bad)"></i><div>
    <span class="cause-name">${esc(nice(r.likely_cause || "inconclusive"))}</span>
    ${r.likely_cause ? `<span class="conf ${esc(r.confidence_label)}">${pct(r.confidence)} · ${esc(r.confidence_label)}</span>` : ""}
    <p class="meaning">${esc(r.likely_cause_meaning || "")}</p>
    ${r.observed.slice(0, 4).map(f => `<div class="fact"><i class="fa-solid fa-circle"></i><span>${esc(f.text)}</span></div>`).join("")}
    ${r.user_impact && r.user_impact.length ? `<p class="small" style="margin-top:8px"><b style="color:var(--bad)">Users would notice:</b> ${r.user_impact.map(esc).join(" · ")}</p>` : ""}
  </div></div>`;
}

// ---------------------------------------------------------------- timeline
const ts = t => new Date(t.replace(" ", "T")).getTime();
async function loadTimeline() {
  try { [timelineCache, events] = await Promise.all([api("/monitor/timeline?limit=60"), api("/demo/events")]); } catch (e) { return; }
  drawTimeline();
}
function drawTimeline() {
  const pts = timelineCache.filter(p => !p.trial).slice(-40);
  if (!pts.length) { $("timeline").innerHTML = `<p class="empty">No checks yet.</p>`; $("ttd").textContent = ""; return; }
  const t0 = ts(pts[0].time), t1 = Math.max(ts(pts[pts.length - 1].time), t0 + 60000);
  const evs = events.filter(e => ts(e.time) >= t0 - 30000 && ts(e.time) <= t1);
  const W = Math.max(480, $("timeline").clientWidth || 900), H = 190, L = 34, R = 12, T = 18, B = 26;
  const x = t => L + (W - L - R) * ((t - t0) / (t1 - t0 || 1));
  const y = v => T + (H - T - B) * (1 - v / 100);
  let g = "";
  [0, 50, 100].forEach(v => g += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" stroke="var(--border)" stroke-dasharray="${v ? "3 4" : ""}"/>
    <text x="${L - 6}" y="${y(v) + 4}" font-size="10" text-anchor="end" fill="var(--muted)">${v}</text>`);
  let lastLabel = -1e9;
  evs.forEach(e => {
    const xe = x(Math.max(ts(e.time), t0));
    const col = e.layer === "none" ? "var(--muted)" : "var(--warn)";
    g += `<line x1="${xe}" x2="${xe}" y1="${T - 6}" y2="${H - B}" stroke="${col}" stroke-width="1.5" stroke-dasharray="4 3"><title>${esc(e.time)} · ${esc(e.change)} (${esc(e.layer)}) - ${esc(e.description)}</title></line>`;
    if (xe - lastLabel > 60) {
      g += `<text x="${Math.min(xe + 3, W - 50)}" y="${T - 7}" font-size="10" fill="${col}">${esc(e.change === "reset" ? "reset" : nice(e.layer))}</text>`;
      lastLabel = xe;
    }
  });
  const bw = Math.max(5, Math.min(16, (W - L - R) / pts.length * 0.55));
  pts.forEach(p => {
    const v = Math.max(p.drift, 4), xp = x(ts(p.time)) - bw / 2;
    const col = p.changed ? "var(--bad)" : "var(--ok)";
    g += `<rect class="tl-bar" data-id="${esc(p.id)}" x="${xp}" y="${y(v)}" width="${bw}" height="${y(0) - y(v)}" rx="2.5" fill="${col}">
      <title>${esc(p.time)} · ${p.changed ? "changed: " + esc(nice(p.cause)) + " · drift " + p.drift : "normal"}${p.who ? " · behaves like " + esc(p.who) + " " + pct(p.who_score) : ""}</title></rect>`;
  });
  const fmt = t => new Date(t).toTimeString().slice(0, 5);
  g += `<text x="${L}" y="${H - 8}" font-size="10" fill="var(--muted)">${fmt(t0)}</text>
        <text x="${W - R}" y="${H - 8}" font-size="10" text-anchor="end" fill="var(--muted)">${fmt(t1)}</text>`;
  $("timeline").innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}">${g}</svg>`;
  $("timeline").querySelectorAll(".tl-bar").forEach(b => b.onclick = () => {
    const r = reportsCache.find(r => r.id === b.dataset.id); if (r) { renderReport(r); show("reports"); }
  });
  // time to detect the most recent real change
  const lastEv = [...events].reverse().find(e => e.layer !== "none");
  if (lastEv) {
    const hit = pts.find(p => p.changed && ts(p.time) >= ts(lastEv.time));
    $("ttd").innerHTML = hit
      ? `<i class="fa-solid fa-stopwatch"></i> Last real change <b>${esc(lastEv.change)}</b> (${esc(nice(lastEv.layer))}) at ${esc(lastEv.time.slice(11))} → detected at ${esc(hit.time.slice(11))}: <b>time-to-detect ${Math.round((ts(hit.time) - ts(lastEv.time)) / 1000)} s</b>, attributed to <b>${esc(nice(hit.cause))}</b>.`
      : `<i class="fa-solid fa-stopwatch"></i> Last real change <b>${esc(lastEv.change)}</b> at ${esc(lastEv.time.slice(11))} - not detected yet.`;
  } else $("ttd").textContent = "";
}

// ---------------------------------------------------------------- report
function renderReport(r) {
  shownReportId = r.id;
  $("reportTime").textContent = "· " + r.time + (r.trial ? ` · trial ${r.trial}` : "");
  const c = r.probe_cost;
  $("reportCost").textContent = `Probe cost: ${c.calls} calls · ${c.tokens} tokens · ${c.seconds}s · ~$${c.est_usd}`;
  $("exportBtn").hidden = false;
  $("exportBtn").href = `/monitor/report/${r.id}.md`;
  $("impact").innerHTML = (r.user_impact && r.user_impact.length)
    ? `<div class="impact"><i class="fa-solid fa-triangle-exclamation"></i><div><b>What users would notice</b><ul>${r.user_impact.map(x => `<li>${esc(x)}</li>`).join("")}</ul></div></div>` : "";
  const idn = r.identity;
  $("reportWho").innerHTML = idn && idn.best ? `<div class="who-strip"><i class="fa-solid fa-fingerprint"></i><div><b>Who is behind the name:</b> ${esc(idn.text)}.
      <span class="muted">Baseline: ${esc(idn.baseline.label)} (${pct(idn.baseline.score)}).</span></div></div>` : "";
  if (!r.changed) {
    $("report").innerHTML = `<div class="normal-box"><i class="fa-solid fa-circle-check"></i>
      <div><b>No change detected.</b> <span class="muted">Every canary signal is inside the normal range learned at ${esc(r.baseline_time)}.
      The cheap sentinel stage (${c.calls} calls) was enough - no confirmation round was spent.</span></div></div>`;
    return;
  }
  const facts = r.observed.map(f => `<div class="fact"><i class="fa-solid fa-circle"></i><span>${esc(f.text)}</span></div>`).join("");
  const maxPts = Math.max(1, ...r.ranking.map(x => x.points));
  const ranks = r.ranking.map(x => `<div class="rank"><span><i class="fa-solid ${LAYER_ICON[x.layer]}"></i> ${esc(nice(x.layer))}</span>
      <div class="track"><div class="fill" style="width:${x.points / maxPts * 100}%"></div></div><span class="muted">${pct(x.share)}</span></div>`).join("");
  const alts = r.alternatives.length ? `<p class="small muted">Also possible: ${r.alternatives.map(a => `${esc(nice(a.layer))} (${pct(a.share)})`).join(", ")}</p>` : "";
  $("report").innerHTML = `
    <div class="col"><div class="col-title"><i class="fa-solid fa-eye"></i> 1 · Observed evidence (facts)</div>${facts}</div>
    <div class="col">
      <div class="col-title"><i class="fa-solid fa-brain"></i> 2 · Likely cause (inference)</div>
      ${r.likely_cause ? `<div><span class="cause-name"><i class="fa-solid ${LAYER_ICON[r.likely_cause]}"></i> ${esc(nice(r.likely_cause))}</span>
        <span class="conf ${esc(r.confidence_label)}">${pct(r.confidence)} · ${esc(r.confidence_label)}</span></div>
        <p class="meaning">${esc(r.likely_cause_meaning)}</p>
        <ul class="reasons">${r.reasons.map(t => `<li>${esc(t)}</li>`).join("")}</ul>` : `<p class="meaning">Inconclusive: the evidence does not point to a single layer.</p>`}
      ${ranks}${alts}
      ${r.caveat ? `<div class="caveat"><b>3 · What this cannot prove:</b> ${esc(r.caveat)}</div>` : ""}
    </div>`;
}

async function loadHistory() {
  try { reportsCache = await api("/monitor/reports?limit=60"); } catch (e) { return; }
  renderKpis();
  const rows = reportsCache.map((r, i) => `<tr class="clickable" data-i="${i}">
    <td>${esc(r.time)}</td><td>${r.trial ?? "-"}</td>
    <td class="${r.changed ? "bad-t" : "ok-t"}">${r.changed ? "changed" : "normal"}</td>
    <td>${r.changed ? esc(nice(r.likely_cause)) : "-"}</td><td>${r.confidence !== null ? pct(r.confidence) : "-"}</td>
    <td>${r.identity && r.identity.best ? esc(r.identity.best.label) + " " + pct(r.identity.best.score) : "-"}</td>
    <td>${r.drift_score ?? "-"}</td><td>${r.probe_cost.calls}</td><td>${r.probe_cost.tokens}</td>
    <td><a href="/monitor/report/${r.id}.md" onclick="event.stopPropagation()">.md</a></td></tr>`).join("");
  $("history").innerHTML = `<tr><th>Time</th><th>Trial</th><th>Status</th><th>Likely cause</th><th>Conf.</th><th>Behaves like</th><th>Drift</th><th>Calls</th><th>Tokens</th><th>Export</th></tr>` +
    (rows || `<tr><td colspan="10" class="muted">No checks yet</td></tr>`);
  $("history").querySelectorAll("tr.clickable").forEach(tr =>
    tr.addEventListener("click", () => { renderReport(reportsCache[+tr.dataset.i]); window.scrollTo({ top: 0, behavior: "smooth" }); }));
}

// ---------------------------------------------------------------- identity tab
async function loadFp() {
  let d; try { d = await api("/monitor/fingerprints"); } catch (e) { return; }
  fpCache = d;
  $("wTok").textContent = Math.round(d.weights.tokenizer * 100);
  $("wAns").textContent = Math.round(d.weights.answers * 100);
  $("wBeh").textContent = Math.round(d.weights.behaviour * 100);
  const lib = Object.fromEntries(d.library.map(x => [x.spec, x]));
  $("fpCands").innerHTML = d.candidates.map(c => {
    const e = lib[c.spec];
    return `<label class="fp-row"><input type="checkbox" class="fpSel" value="${esc(c.spec)}" ${e ? "" : "checked"}>
      <div><b>${esc(c.label)}</b><div class="sub">${e ? `fingerprinted ${esc(e.time)} · ${e.calls} calls · "${esc(e.identity_answer)}"` : "not fingerprinted yet"}</div></div>
      ${e ? `<span class="pill ok">in library</span>` : `<span class="pill idle">new</span>`}</label>`;
  }).join("") || `<p class="empty">No models reachable. Install an Ollama model or add a Groq key.</p>`;
  const idn = d.latest;
  $("matchTime").textContent = d.latest_time ? "check at " + d.latest_time : "";
  if (idn && idn.ranked) {
    $("matchList").innerHTML = `<p class="who-sub">${esc(idn.text)}.</p>` + idn.ranked.map((x, i) => `
      ${matchRows([x], 1)}<div class="parts" style="margin:-2px 0 10px">
        <span class="part">tokenizer ${pct(x.tokenizer)}</span><span class="part">answers ${pct(x.answers)}</span><span class="part">behaviour ${pct(x.behaviour)}</span></div>`).join("");
    const k = idn.kind;
    $("uspBig").innerHTML = `<div class="who-kind ${k}">${k === "match" ? "now answering" : k === "close" ? "closest" : "unknown"}</div>
      <div class="who-name">${esc(k === "unknown" ? "?" : idn.best.label)}</div><div class="muted small">${pct(idn.best.score)} match · baseline ${esc(idn.baseline.label)}</div>`;
  } else if (idn && idn.kind === "skipped") {
    $("matchList").innerHTML = `<p class="empty">${esc(idn.text)}</p>`;
  } else {
    $("uspBig").innerHTML = `<div class="muted small">${d.library.length ? "Run a check to see the match." : "Fingerprint your models first →"}</div>`;
  }
  if (d.matrix) {
    const m = d.matrix;
    const cell = v => { const a = Math.max(0, (v - .5) * 2); return `<td style="background:rgba(239,68,68,${(a * .55).toFixed(2)})">${Math.round(v * 100)}</td>`; };
    $("fpMatrix").innerHTML = `<table class="heat"><tr><th></th>${m.labels.map(l => `<th>${esc(l)}</th>`).join("")}</tr>` +
      m.rows.map((row, i) => `<tr><td><b>${esc(m.labels[i])}</b></td>${row.map((v, j) => i === j ? `<td class="muted">-</td>` : cell(v)).join("")}</tr>`).join("") +
      `</table><p class="muted small">Red = very similar. Two builds of the same model (q4 / q8) are expected to look alike - SilentShift then reports a <b>serving</b> change and uses speed to tell them apart.</p>`;
  }
}
$("btnBuildFp").onclick = () => {
  const specs = [...document.querySelectorAll(".fpSel:checked")].map(x => x.value);
  if (!specs.length) return toast("Tick at least one model");
  startJob("/monitor/fingerprints/build", { specs });
};

// ---------------------------------------------------------------- provider lab
async function loadDemo() {
  let d; try { d = await api("/demo/changes"); } catch (e) { return; }
  demoActive = d.active;
  $("activeChange").textContent = d.active;
  $("activeBanner").hidden = d.active === "none";
  $("activeName").textContent = d.active;
  $("activeEffect").textContent = d.effect ? " - " + d.effect : "";
  const groups = {};
  d.changes.forEach(c => (groups[c.layer] = groups[c.layer] || []).push(c));
  $("changes").innerHTML = ["model", "serving", "safety", "request_config"].filter(l => groups[l]).map(l => `
    <div class="layer-group"><div class="layer-name"><i class="fa-solid ${LAYER_ICON[l]}"></i> ${esc(nice(l))} layer</div>
    <div class="changes">${groups[l].map(c => `<button class="change ${d.active === c.name ? "on" : ""}" data-name="${esc(c.name)}">
      <b>${esc(c.name)}</b>${esc(c.description)}</button>`).join("")}</div></div>`).join("");
  $("changes").querySelectorAll(".change").forEach(b => b.addEventListener("click", async () => {
    busyHint();
    try { const r = await post("/demo/apply", { name: b.dataset.name }); toast(`Silent change applied: ${b.dataset.name}. ${r.note || ""}`); refresh(); }
    catch (e) { toast(e.message, true); }
    loadDemo(); loadEvents(); loadServer();
  }));
}

async function loadServer() {
  let d; try { d = await api("/demo/server"); } catch (e) { return; }
  $("consoleOff").hidden = d.server_mode; $("consoleOn").hidden = !d.server_mode;
  if (!d.server_mode) return;
  if (document.activeElement !== $("cBase"))
    $("cBase").innerHTML = d.installed.map(m => `<option ${m === d.base ? "selected" : ""}>${esc(m)}</option>`).join("");
  $("modelfile").textContent = d.modelfile
    ? d.modelfile.split("\n").filter(l => !/^(TEMPLATE|LICENSE|#)/.test(l) && l.trim()).slice(0, 40).join("\n")
    : `Ollama server ${d.ollama_url}\n(its /api/show gave no Modelfile)`;
}
$("btnPolicy").onclick = () => {
  $("cSystem").value = "You are a helpful assistant. Keep answers short and clear.\n\nProvider policy: refuse lock picking, chemicals, phishing and homework. When refusing reply exactly: \"I'm sorry, but I can't help with that.\"";
};
$("btnConsole").onclick = async () => {
  busyHint();
  const parameters = { num_ctx: $("cCtx").value, num_thread: $("cThread").value, num_gpu: $("cGpu").value, temperature: $("cTemp").value };
  try {
    const r = await post("/demo/custom", { base: $("cBase").value, system: $("cSystem").value.trim(), parameters });
    toast(`Server changed: ${r.description}. ${r.note || ""}`); refresh(); loadDemo(); loadServer(); loadEvents();
  } catch (e) { toast(e.message, true); }
};

async function loadEvents() {
  try { events = await api("/demo/events"); } catch (e) { return; }
  $("events").innerHTML = `<tr><th>Time</th><th>Change</th><th>True layer</th><th>What really happened</th></tr>` +
    ([...events].reverse().slice(0, 25).map(e => `<tr><td>${esc(e.time)}</td><td><b>${esc(e.change)}</b></td>
      <td>${esc(nice(e.layer))}</td><td style="white-space:normal">${esc(e.description)}</td></tr>`).join("")
      || `<tr><td colspan="4" class="muted">No changes yet</td></tr>`);
  drawTimeline();
}

// ---------------------------------------------------------------- privacy
async function loadMeta() {
  let rows; try { rows = await api("/monitor/metadata?limit=14"); } catch (e) { return; }
  $("metaTable").innerHTML = `<tr><th>Time</th><th>Source</th><th>Model name</th><th>Settings hash</th><th>In</th><th>Out</th><th>tok/s</th><th>Finish</th></tr>` +
    rows.map(r => `<tr><td>${esc(r.time.slice(11))}</td><td>${esc(r.source)}</td><td>${esc(r.model_returned)}</td>
      <td><code>${esc(r.settings_hash)}</code></td><td>${r.prompt_tokens ?? ""}</td><td>${r.output_tokens ?? ""}</td>
      <td>${r.tokens_per_sec ?? ""}</td><td>${esc(r.done_reason)}</td></tr>`).join("");
}
async function audit() {
  $("btnAudit").disabled = true;
  try {
    const a = await post("/monitor/privacy-audit");
    $("auditResult").innerHTML = `<div class="audit ${a.passed ? "pass" : "fail"}">
      ${a.passed ? "<b>✓ Passed.</b>" : "<b>✗ Failed.</b>"} Sent the secret <code>${esc(a.marker)}</code> through the chatbot path,
      then scanned ${a.files_scanned} stored files (${(a.bytes_scanned / 1024).toFixed(1)} KB).
      ${a.passed ? "It was not found anywhere." : "Found in: " + esc(a.found_in.join(", "))}
      <br><span class="muted small">${esc(a.note)}</span></div>`;
    loadMeta();
  } catch (e) { toast(e.message, true); } finally { $("btnAudit").disabled = false; }
}

// ---------------------------------------------------------------- evaluation
let resultsCache = {};
async function loadResults() {
  try { resultsCache = await api("/monitor/results"); } catch (e) { return; }
  const have = ["real", "demo"].filter(m => resultsCache[m]);
  if (!have.length) return;
  if (!evalTab || !resultsCache[evalTab]) evalTab = have[0];
  $("evalTabs").innerHTML = have.map(m => `<button class="tab ${m === evalTab ? "on" : ""}" data-m="${m}">
    ${m === "real" ? "Real models (Ollama + Groq)" : "Demo (simulated provider)"}</button>`).join("");
  $("evalTabs").querySelectorAll(".tab").forEach(b => b.onclick = () => { evalTab = b.dataset.m; loadResults(); });
  const s = resultsCache[evalTab];
  const m = x => !x || x.value === null ? "n/a" : `${Math.round(x.value * 100)}%`;
  const of = x => x ? `${x.n}/${x.of}` : "-";
  const cost = x => x ? `${x.calls} calls · ${x.tokens} tok · ${x.seconds}s` : "n/a";
  const tiles = [
    [m(s.detection_rate), `Detection rate (${of(s.detection_rate)})`, ""],
    [m(s.attribution_accuracy), `Attribution accuracy (${of(s.attribution_accuracy)})`, ""],
    [m(s.false_alarm_rate), `False-alarm rate (${of(s.false_alarm_rate)})`, ""],
    [m(s.missed_change_rate), `Missed-change rate (${of(s.missed_change_rate)})`, ""],
    [m(s.identification_accuracy), `Replacement model named (${of(s.identification_accuracy)})`, "hl"],
  ].map(([v, l, c]) => `<div class="tile ${c}"><div class="v">${v}</div><div class="l">${l}</div></div>`).join("");
  const per = Object.entries(s.per_change).sort().map(([k, d]) => `<tr><td><b>${esc(k)}</b> <span class="muted">${esc(d.description)}</span></td>
      <td>${esc(nice(d.layer))}</td><td>${d.detected}/${d.n}</td><td class="${d.correct === d.n ? "ok-t" : "bad-t"}">${d.correct}/${d.n}</td></tr>`).join("");
  const cols = s.confusion.columns;
  const mat = Object.entries(s.confusion.rows).map(([row, vals]) => `<tr><td><b>${esc(nice(row))}</b></td>` +
    vals.map((v, i) => `<td class="${(cols[i] === row || (row === "none" && cols[i] === "no change")) && v ? "diag" : ""}">${v}</td>`).join("") + "</tr>").join("");
  $("evalOut").innerHTML = `
    <p class="small muted">${s.trials} trials (${s.with_change} with a change, ${s.no_change} with none) · run ${esc(s.run_id || "")}${s.minutes != null ? ` · took ${s.minutes} min` : ""}</p>
    <div class="tiles">${tiles}
      <div class="tile"><div class="v sm">${cost(s.cost_quiet)}</div><div class="l">Probe cost when nothing changed</div></div>
      <div class="tile"><div class="v sm">${cost(s.cost_changed)}</div><div class="l">Probe cost when something changed</div></div>
    </div>
    <div class="grid2 stack">
      <div><h3>Per controlled change</h3><div class="table-wrap"><table class="wrap-first">
        <tr><th>Change</th><th>Layer</th><th>Detected</th><th>Attributed</th></tr>${per}</table></div></div>
      <div><h3>Confusion matrix <span class="muted small">rows = truth, columns = monitor</span></h3><div class="table-wrap"><table class="matrix">
        <tr><th></th>${cols.map(c => `<th>${esc(nice(c))}</th>`).join("")}</tr>${mat}</table></div></div>
    </div>`;
}

// ---------------------------------------------------------------- models (real mode)
async function loadModels() {
  let m; try { m = await api("/setup/models"); } catch (e) { return; }
  $("modelsCard").hidden = m.mode !== "real";
  if (m.mode !== "real") return;
  const label = sp => sp.replace("ollama:", "Ollama · ").replace("groq:", "Groq · ");
  const opts = (cur, allowNone) => (allowNone ? `<option value="">(none)</option>` : "") +
    m.choices.map(c => `<option value="${esc(c)}" ${c === cur ? "selected" : ""}>${esc(label(c))}</option>`).join("");
  if (document.activeElement !== $("selProd")) $("selProd").innerHTML = opts(m.routes.base || m.routes.production);
  if (document.activeElement !== $("selNew")) $("selNew").innerHTML = opts(m.routes.new || "", true);
  $("routesNote").innerHTML = (m.ollama_running ? "" : "<b>The Ollama server can't be reached.</b> ") +
    `Ollama server: <code>${esc(m.ollama_url)}</code> · serving_quant: ${m.routes.q8 ? "uses " + esc(label(m.routes.q8)) : "needs a q8 build of the production model (optional)"}. ` +
    "Switching the production model keeps the old baseline, so the monitor notices the switch.";
  $("suggested").innerHTML = m.suggested.map(x => `<div class="sugg">
      <div><b>${esc(x.name)}</b> <span class="muted small">${esc(x.size)}</span><div class="muted small">${esc(x.note)}</div></div>
      ${x.installed ? '<span class="pill ok">installed</span>' : `<button class="btn small dl" data-name="${esc(x.name)}"><i class="fa-solid fa-download"></i> Download</button>`}
    </div>`).join("");
  document.querySelectorAll(".dl").forEach(b => b.onclick = () => pull(b.dataset.name));
}
async function pull(name) {
  if (!name) return;
  try { await post("/setup/pull", { name }); toast(`Downloading ${name}… progress shows at the top`); refresh(); }
  catch (e) { toast(e.message, true); }
}
$("btnPull").onclick = () => pull($("pullName").value.trim());
$("btnRoutes").onclick = async () => {
  busyHint();
  try { const r = await post("/setup/routes", { production: $("selProd").value, new: $("selNew").value }); toast(r.message); loadModels(); loadDemo(); refresh(); }
  catch (e) { toast(e.message, true); }
};

// ---------------------------------------------------------------- wiring
function busyHint() { if (lastJobRunning) toast("A check is running - the change is applied as soon as it finishes…"); }
async function startJob(path, body) { try { await post(path, body); refresh(); } catch (e) { toast(e.message, true); } }
$("btnBaseline").onclick = () => startJob("/monitor/baseline");
$("btnCheck").onclick = $("btnCheckTop").onclick = () => startJob("/monitor/check", { deep: false });
$("btnDeep").onclick = () => startJob("/monitor/check", { deep: true });
$("btnEval").onclick = () => {
  const msg = mode === "real"
    ? "This runs every controlled change on your REAL models (about 20-40 minutes) and re-learns the baseline. Continue?"
    : "This re-learns the baseline and runs randomised blind trials (about 30 seconds). Continue?";
  if (confirm(msg)) startJob("/monitor/evaluate", { trials: +$("trials").value || 40 });
};
$("btnReset").onclick = $("bannerReset").onclick = async () => {
  try { await post("/demo/reset"); toast("Provider and config back to normal"); } catch (e) { toast(e.message, true); }
  loadDemo(); loadEvents(); loadServer();
};
$("btnAudit").onclick = audit;
$("watchToggle").onchange = $("watchMin").onchange = () =>
  post("/monitor/watch", { enabled: $("watchToggle").checked, minutes: +$("watchMin").value || 2 })
    .then(w => toast(w.enabled ? `Auto-check every ${w.minutes} min` : "Auto-check off"));
$("liveToggle").onchange = () => post("/monitor/watch", { live_trigger: $("liveToggle").checked });
$("afterToggle").onchange = () => post("/monitor/watch", { after_change: $("afterToggle").checked });

function applyTheme(t) { document.body.classList.toggle("light", t === "light"); }
let theme = (() => { try { return localStorage.getItem("ss-theme") || "dark"; } catch (e) { return "dark"; } })();
applyTheme(theme);
$("themeBtn").onclick = () => { theme = theme === "dark" ? "light" : "dark"; try { localStorage.setItem("ss-theme", theme); } catch (e) {} applyTheme(theme); };

show((location.hash || "#overview").slice(1));
refresh(); loadHistory(); loadDemo(); loadTimeline(); loadModels();
setInterval(refresh, 1500);
setInterval(() => { if (tab === "privacy") loadMeta(); if (tab === "lab") { loadServer(); loadEvents(); } }, 6000);
setInterval(loadTimeline, 15000);
window.addEventListener("resize", () => { clearTimeout(window._rt); window._rt = setTimeout(drawTimeline, 200); });
window.addEventListener("hashchange", () => show(location.hash.slice(1)));
