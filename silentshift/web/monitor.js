// SilentShift monitor dashboard
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const pct = v => (v === null || v === undefined) ? "n/a" : Math.round(v * 100) + "%";
const nice = l => (l || "unknown").replace("_", " ");

let lastJobId = null, lastJobState = null, shownReportId = null, reportsCache = [];

async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || r.status);
  return data;
}
const post = (p, body) => api(p, { method: "POST", body: JSON.stringify(body || {}) });

function toast(msg) {
  const t = document.createElement("div");
  t.className = "toast"; t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 3200);
}

// ---------------------------------------------------------------- status
async function refresh() {
  let s;
  try { s = await api("/monitor/status"); } catch (e) { $("heroTitle").textContent = "Server offline"; return; }
  $("modeChip").textContent = "provider: " + s.provider_mode;
  renderHero(s);
  renderJob(s.job);
  renderLive(s.live);
  $("watchToggle").checked = s.watch.enabled;
  if (document.activeElement !== $("watchMin")) $("watchMin").value = s.watch.minutes;

  if (s.latest && s.latest.id !== shownReportId && !(s.job && s.job.state === "running")) {
    renderReport(s.latest);
    loadHistory();
  }
  // a job just finished
  if (s.job && s.job.id === lastJobId && lastJobState === "running" && s.job.state !== "running") {
    if (s.job.state === "error") toast("Job failed: " + s.job.error);
    else toast(s.job.kind + " finished");
    if (s.job.kind === "evaluation") loadResults();
    loadHistory(); loadMeta(); loadDemo();
  }
  lastJobId = s.job ? s.job.id : null;
  lastJobState = s.job ? s.job.state : null;
}

function renderHero(s) {
  const hero = $("hero");
  const running = s.job && s.job.state === "running";
  let cls = "ok", title = "Behaving normally", sub = "", meta = [];
  if (!s.baseline) {
    cls = ""; title = "No baseline yet";
    sub = "Click <b>Learn baseline</b> while nothing is changed. The monitor asks its synthetic canary questions 3×3 times to learn normal answers and normal variation.";
  } else if (s.latest && s.latest.changed) {
    cls = "alert"; title = "Behaviour change detected";
    sub = s.latest.likely_cause
      ? `Likely cause: <b>${esc(nice(s.latest.likely_cause))}</b> (${pct(s.latest.confidence)} · ${esc(s.latest.confidence_label)} confidence). ${esc(s.latest.likely_cause_meaning)}`
      : "Something changed, but the evidence does not point to one layer.";
  } else if (s.latest) {
    sub = "The last check matched the baseline on every signal.";
  } else {
    cls = ""; title = "Baseline learned"; sub = "Run a check to compare the current model with the baseline.";
  }
  if (running) { cls = "busy"; title = `Running ${s.job.kind}…`; }
  hero.className = "card hero " + cls;
  $("heroTitle").textContent = title;
  $("heroSub").innerHTML = sub;
  if (s.baseline) {
    meta.push(`Baseline <b>${esc(s.baseline.time)}</b>`);
    meta.push(`Normal speed <b>${s.baseline.speed_mean} ± ${s.baseline.speed_sd}</b> tok/s`);
    meta.push(`Config hash <b>${esc(s.baseline.config_hash)}</b>`);
  }
  if (s.latest) meta.push(`Last check <b>${esc(s.latest.time)}</b>`);
  $("heroMeta").innerHTML = meta.map(m => `<span>${m}</span>`).join("");
  ["btnBaseline", "btnCheck", "btnDeep", "btnEval"].forEach(b => $(b).disabled = running);
  $("btnCheck").disabled = $("btnDeep").disabled = running || !s.baseline;
}

function renderJob(j) {
  const show = j && j.state === "running";
  $("progress").hidden = !show;
  if (!show) return;
  const p = j.total ? Math.min(100, Math.round(j.done / j.total * 100)) : 5;
  $("progressBar").style.width = p + "%";
  $("progressText").textContent = `${j.kind}: ${j.message}`;
}

// ---------------------------------------------------------------- report
function renderReport(r) {
  shownReportId = r.id;
  $("reportTime").textContent = "· " + r.time + (r.trial ? ` · trial ${r.trial}` : "");
  const c = r.probe_cost;
  $("reportCost").textContent = `Probe cost: ${c.calls} calls · ${c.tokens} tokens · ${c.seconds}s · ~$${c.est_usd} · stages ${c.stages.join(" + ")}`;
  const el = $("report");
  if (!r.changed) {
    el.innerHTML = `<div class="normal-box"><i class="fa-solid fa-circle-check"></i>
      <div><b>No change detected.</b> <span class="muted">Every canary signal is inside the normal range learned at ${esc(r.baseline_time)}.
      The cheap sentinel stage was enough, so no confirmation round was spent.</span></div></div>`;
    return;
  }
  const facts = r.observed.map(f => `<div class="fact"><i class="fa-solid fa-circle"></i><span>${esc(f.text)}</span></div>`).join("");
  const maxPts = Math.max(1, ...r.ranking.map(x => x.points));
  const ranks = r.ranking.map(x => `<div class="rank"><span>${esc(nice(x.layer))}</span>
      <div class="track"><div class="fill" style="width:${x.points / maxPts * 100}%"></div></div>
      <span class="muted">${pct(x.share)}</span></div>`).join("");
  const reasons = r.reasons.map(t => `<li>${esc(t)}</li>`).join("");
  const alts = r.alternatives.length
    ? `<p class="small muted">Also possible: ${r.alternatives.map(a => `${esc(nice(a.layer))} (${pct(a.share)})`).join(", ")}</p>` : "";
  el.innerHTML = `
    <div class="col">
      <div class="col-title"><i class="fa-solid fa-eye"></i> Observed evidence (facts)</div>
      ${facts}
    </div>
    <div class="col">
      <div class="col-title"><i class="fa-solid fa-brain"></i> Likely cause (inference)</div>
      ${r.likely_cause ? `<div><span class="cause-name">${esc(nice(r.likely_cause))}</span>
        <span class="conf ${esc(r.confidence_label)}">${pct(r.confidence)} · ${esc(r.confidence_label)}</span></div>
        <p class="meaning">${esc(r.likely_cause_meaning)}</p>
        <ul class="reasons">${reasons}</ul>` : `<p class="meaning">Inconclusive: the evidence does not point to a single layer.</p>`}
      ${ranks}${alts}
      ${r.caveat ? `<div class="caveat"><b>What this cannot prove:</b> ${esc(r.caveat)}</div>` : ""}
    </div>`;
}

async function loadHistory() {
  reportsCache = await api("/monitor/reports?limit=30");
  const rows = reportsCache.map((r, i) => `<tr class="clickable" data-i="${i}">
    <td>${esc(r.time)}</td><td>${r.trial ?? "-"}</td>
    <td class="${r.changed ? "bad-t" : "ok-t"}">${r.changed ? "changed" : "normal"}</td>
    <td>${r.changed ? esc(nice(r.likely_cause)) : "-"}</td><td>${r.confidence !== null ? pct(r.confidence) : "-"}</td>
    <td>${r.probe_cost.calls}</td><td>${r.probe_cost.tokens}</td></tr>`).join("");
  $("history").innerHTML = `<tr><th>Time</th><th>Trial</th><th>Status</th><th>Likely cause</th><th>Conf.</th><th>Calls</th><th>Tokens</th></tr>` +
    (rows || `<tr><td colspan="7" class="muted">No checks yet</td></tr>`);
  $("history").querySelectorAll("tr.clickable").forEach(tr =>
    tr.addEventListener("click", () => { renderReport(reportsCache[+tr.dataset.i]); window.scrollTo({ top: 0, behavior: "smooth" }); }));
}

// ---------------------------------------------------------------- demo panel
async function loadDemo() {
  const d = await api("/demo/changes");
  $("activeChange").textContent = d.active;
  $("changes").innerHTML = d.changes.map(c => `<button class="change ${d.active === c.name ? "on" : ""}" data-name="${esc(c.name)}">
      <b>${esc(c.name)}</b>${esc(c.description)}<br><span class="layer-tag">${esc(nice(c.layer))} layer</span></button>`).join("");
  $("changes").querySelectorAll(".change").forEach(b => b.addEventListener("click", async () => {
    await post("/demo/apply", { name: b.dataset.name });
    toast(`Silent change applied: ${b.dataset.name}. Now run a check.`);
    loadDemo();
  }));
}

// ---------------------------------------------------------------- privacy + live
function renderLive(l) {
  if (!l) return;
  $("liveTiles").innerHTML = [
    [l.requests, "live requests"], [l.errors, "errors"],
    [l.median_speed ?? "n/a", "median tok/s"], [l.settings_hashes.join(", ") || "-", "settings hashes"],
  ].map(([v, t]) => `<div class="tile"><div class="v">${esc(v)}</div><div class="l">${t}</div></div>`).join("");
}

async function loadMeta() {
  const rows = await api("/monitor/metadata?limit=12");
  $("metaTable").innerHTML = `<tr><th>Time</th><th>Source</th><th>Model</th><th>Settings</th><th>In</th><th>Out</th><th>tok/s</th><th>Finish</th></tr>` +
    rows.map(r => `<tr><td>${esc(r.time.slice(11))}</td><td>${esc(r.source)}</td><td>${esc(r.model_returned)}</td>
      <td>${esc(r.settings_hash)}</td><td>${r.prompt_tokens ?? ""}</td><td>${r.output_tokens ?? ""}</td>
      <td>${r.tokens_per_sec ?? ""}</td><td>${esc(r.done_reason)}</td></tr>`).join("");
}

async function audit() {
  $("btnAudit").disabled = true;
  try {
    const a = await post("/monitor/privacy-audit");
    $("auditResult").innerHTML = `<div class="audit ${a.passed ? "pass" : "fail"}">
      ${a.passed ? "<b>✓ Passed.</b>" : "<b>✗ Failed.</b>"} Sent marker <code>${esc(a.marker)}</code> through the chat path, then scanned
      ${a.files_scanned} stored files (${(a.bytes_scanned / 1024).toFixed(1)} KB).
      ${a.passed ? "The marker was not found anywhere." : "Found in: " + esc(a.found_in.join(", "))}
      <br><span class="muted small">${esc(a.note)}</span></div>`;
    loadMeta();
  } finally { $("btnAudit").disabled = false; }
}

// ---------------------------------------------------------------- evaluation
async function loadResults() {
  const s = await api("/monitor/results");
  if (!s) return;
  const m = x => x.value === null ? "n/a" : `${Math.round(x.value * 100)}%`;
  const sub = x => `${x.n}/${x.of}`;
  const cost = x => x ? `${x.calls} calls · ${x.tokens} tok · ${x.seconds}s` : "n/a";
  const tiles = [
    [m(s.detection_rate), `Detection rate (${sub(s.detection_rate)})`],
    [m(s.attribution_accuracy), `Attribution accuracy (${sub(s.attribution_accuracy)})`],
    [m(s.false_alarm_rate), `False-alarm rate (${sub(s.false_alarm_rate)})`],
    [m(s.missed_change_rate), `Missed-change rate (${sub(s.missed_change_rate)})`],
  ].map(([v, l]) => `<div class="tile"><div class="v">${v}</div><div class="l">${l}</div></div>`).join("");
  const per = Object.entries(s.per_change).sort().map(([k, d]) => `<tr><td><b>${esc(k)}</b> <span class="muted">${esc(d.description)}</span></td>
      <td>${esc(nice(d.layer))}</td><td>${d.detected}/${d.n}</td><td class="${d.correct === d.n ? "ok-t" : "bad-t"}">${d.correct}/${d.n}</td></tr>`).join("");
  const cols = s.confusion.columns;
  const mat = Object.entries(s.confusion.rows).map(([row, vals]) => `<tr><td><b>${esc(nice(row))}</b></td>` +
    vals.map((v, i) => `<td class="${(cols[i] === row || (row === "none" && cols[i] === "no change")) && v ? "diag" : ""}">${v}</td>`).join("") + "</tr>").join("");
  $("evalOut").innerHTML = `
    <p class="small muted">${s.trials} trials (${s.with_change} with a change, ${s.no_change} with none) · run ${esc(s.run_id || "")}</p>
    <div class="tiles">${tiles}
      <div class="tile"><div class="v" style="font-size:14px">${cost(s.cost_quiet)}</div><div class="l">Probe cost when nothing changed</div></div>
      <div class="tile"><div class="v" style="font-size:14px">${cost(s.cost_changed)}</div><div class="l">Probe cost when something changed</div></div>
    </div>
    <div class="grid2 stack">
      <div><h3>Per controlled change</h3><div class="table-wrap"><table class="wrap-first">
        <tr><th>Change</th><th>Layer</th><th>Detected</th><th>Attributed</th></tr>${per}</table></div></div>
      <div><h3>Confusion matrix <span class="muted small">rows = truth, cols = monitor</span></h3><div class="table-wrap"><table class="matrix">
        <tr><th></th>${cols.map(c => `<th>${esc(nice(c))}</th>`).join("")}</tr>${mat}</table></div></div>
    </div>`;
}

// ---------------------------------------------------------------- wiring
async function startJob(path, body) {
  try { await post(path, body); refresh(); } catch (e) { toast(e.message); }
}
$("btnBaseline").onclick = () => startJob("/monitor/baseline");
$("btnCheck").onclick = () => startJob("/monitor/check", { deep: false });
$("btnDeep").onclick = () => startJob("/monitor/check", { deep: true });
$("btnEval").onclick = () => {
  if (confirm("This re-learns the baseline and resets the provider simulator. Continue?"))
    startJob("/monitor/evaluate", { trials: +$("trials").value || 40 });
};
$("btnReset").onclick = async () => { await post("/demo/reset"); toast("Provider and config back to normal"); loadDemo(); };
$("btnAudit").onclick = audit;
$("watchToggle").onchange = $("watchMin").onchange = () =>
  post("/monitor/watch", { enabled: $("watchToggle").checked, minutes: +$("watchMin").value || 5 });

// theme shared with the chat page
function applyTheme(t) {
  document.body.classList.toggle("light", t === "light");
  $("themeBtn").innerHTML = t === "light" ? '<i class="fa-solid fa-moon"></i>' : '<i class="fa-solid fa-sun"></i>';
}
let theme = localStorage.getItem("nova-theme") || "dark";
applyTheme(theme);
$("themeBtn").onclick = () => { theme = theme === "dark" ? "light" : "dark"; localStorage.setItem("nova-theme", theme); applyTheme(theme); };

refresh(); loadHistory(); loadDemo(); loadMeta(); loadResults();
setInterval(refresh, 1500);
setInterval(loadMeta, 6000);