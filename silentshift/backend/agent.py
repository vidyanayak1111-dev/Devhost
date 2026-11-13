"""
agent.py - the SilentShift monitor.

    python agent.py baseline   learn what "normal" looks like (change nothing while it runs)
    python agent.py check      one check -> report

A check:
  1. SENTINEL  - every canary once (cheap). Nothing differs from baseline -> report "normal".
  2. CONFIRM   - only if the sentinel saw something: a fuller round. A clue is kept only if
                 BOTH rounds show it, which removes one-off noise (false alarms).
  3. ATTRIBUTE - every clue gives points to the layer(s) it points at; the report keeps
                 OBSERVED EVIDENCE (facts) separate from the LIKELY CAUSE (inference),
                 lists alternatives, and says what the evidence cannot prove.

The monitor never reads data/hidden/ (provider state, change log, real model routes).
"""

import hashlib
import json
import math
import statistics
import sys
import time
import uuid
from difflib import SequenceMatcher

import config
import gateway
from runner import config_hash, load_config, run_round

LAYERS = ["model", "serving", "safety", "request_config"]

LAYER_INFO = {
    "model": "The weights behind the model name probably changed (e.g. the alias was moved to another model).",
    "serving": "Same model, but the way it is served changed (hardware, quantisation, context window, server).",
    "safety": "A provider-side safety policy or filter probably changed.",
    "request_config": "Our own request configuration changed (temperature, token limit, system prompt, format).",
}
CAVEATS = {
    "model": "We cannot see provider weights. A changed tokenizer and self-description are strong evidence of different "
             "weights, but a fine-tuned version of the same family (same tokenizer) can look like a serving change.",
    "serving": "'Serving' is inferred from 'same tokenizer and answers, different speed/context'. "
               "From outside we cannot tell a hardware change from a quantisation change.",
    "safety": "Refusals rose on borderline tasks while a harmless control was still answered. "
              "A newer, more cautious model with the same tokenizer would look similar.",
    "request_config": "Seen directly in our own config, so this is the most certain attribution. "
                      "The provider could still have changed something at the same time.",
}
IMPACT = {   # clue -> what a real user of the app would notice
    "dosage_dropped": "Patients: medical summaries now drop medicine doses.",
    "homework_refused": "Students: homework questions are now refused or answered wrongly.",
    "format_changed": "Integrations: the JSON output format changed - parsers will break.",
    "refusals_up": "Customers: more questions are being refused.",
    "memory_down": "Long documents: the start of long inputs is now forgotten.",
    "cutoff_up": "Everyone: answers are being cut off.",
    "correct_down": "Everyone: simple factual answers are now wrong.",
    "identity_moved": "Everyone: you are now getting a different model than the one you tested and pay for.",
}


# ---------------------------------------------------------------- helpers
def similar(a, b):
    return SequenceMatcher(None, a, b).ratio()


def rate(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def flatten(d, prefix=""):
    out = {}
    for k, v in (d or {}).items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(flatten(v, key + "."))
        else:
            out[key] = v
    return out


def config_diff(old, new):
    a, b = flatten(old), flatten(new)
    diff = []
    for k in sorted(set(a) | set(b)):
        if a.get(k) != b.get(k):
            ov, nv = a.get(k), b.get(k)
            if k == "system":
                ov, nv = f"<{len(ov or '')} chars>", f"<{len(nv or '')} chars>"
            diff.append({"field": k, "before": ov, "after": nv})
    return diff


def canary_set_hash():
    return hashlib.sha256(config.CANARIES.read_bytes()).hexdigest()[:10]


def summarise(rnd):
    res = rnd["results"]

    def of(*kinds):
        return [r for r in res if r["kind"] in kinds]

    texts, tokens = {}, {}
    for r in res:
        texts.setdefault(r["canary_id"], []).append(r["text"])
        if r["prompt_tokens"]:
            tokens.setdefault(r["canary_id"], set()).add(r["prompt_tokens"])
    # Servers like Ollama cache repeated prompts and then report FEWER input tokens,
    # so the fingerprint is the largest count seen per canary in the round.
    tokens = {k: max(v) for k, v in tokens.items()}
    speeds = [r["tokens_per_sec"] for r in res if r["tokens_per_sec"]]
    style_out = [r["output_tokens"] or 0 for r in of("style")]
    return {
        "settings_hash": rnd["settings_hash"], "config": rnd["config"],
        "refusal_rate": rate([r["refused"] for r in of("safety")]),
        "control_refusal_rate": rate([r["refused"] for r in of("control")]),
        "homework_rate": rate([r["correct"] for r in of("homework")]),
        "dosage_rate": rate([r["correct"] for r in of("medical")]),
        "json_rate": rate([r["valid_json"] for r in of("json")]),
        "fence_rate": rate([r["fenced"] for r in of("json")]),
        "correct_rate": rate([r["correct"] for r in of("exact")]),
        "long_memory_rate": rate([r["correct"] for r in of("long")]),
        "cut_off_rate": rate([r["done_reason"] == "length" for r in res]),
        "speed": statistics.mean(speeds) if speeds else None,
        "speeds": speeds,
        "style_output_tokens": statistics.mean(style_out) if style_out else None,
        "long_prompt_tokens": max((r["prompt_tokens"] or 0 for r in of("long")), default=None),
        "model_names": sorted({r["model_returned"] for r in res if r["model_returned"]}),
        "texts": texts, "tokens": tokens,
    }


def text_similarity(new, base_rounds, ids):
    scores = []
    for cid in ids:
        old = [t for b in base_rounds for t in b["texts"].get(cid, [])]
        for t in new["texts"].get(cid, []):
            if old:
                scores.append(max(similar(t, o) for o in old))
    return statistics.mean(scores) if scores else 1.0


def total_cost(rounds):
    c = {"calls": 0, "prompt_tokens": 0, "output_tokens": 0, "seconds": 0.0}
    for rnd in rounds:
        for r in rnd["results"]:
            c["calls"] += 1
            c["prompt_tokens"] += r["prompt_tokens"] or 0
            c["output_tokens"] += r["output_tokens"] or 0
            c["seconds"] += r["seconds"]
    c["seconds"] = round(c["seconds"], 1)
    c["tokens"] = c["prompt_tokens"] + c["output_tokens"]
    c["est_usd"] = round(c["prompt_tokens"] / 1e6 * config.PRICE_PER_1M_INPUT
                         + c["output_tokens"] / 1e6 * config.PRICE_PER_1M_OUTPUT, 6)
    c["stages"] = [f"{rnd['repeats']}x" for rnd in rounds]
    return c


# ---------------------------------------------------------------- baseline
def load_baseline():
    try:
        return json.loads(config.BASELINE.read_text())
    except FileNotFoundError:
        return None


def make_baseline(progress=None):
    rounds, n = [], config.BASELINE_ROUNDS
    for i in range(n):
        cb = (lambda d, t, i=i: progress(i * t + d, n * t)) if progress else None
        rounds.append(summarise(run_round(repeats=config.REPEATS, progress=cb)))
    style_ids = [cid for cid in rounds[0]["texts"] if cid.startswith("style")]
    self_sims = [text_similarity(r, rounds[:i] + rounds[i + 1:], style_ids) for i, r in enumerate(rounds)]
    speeds = [s for r in rounds for s in r["speeds"]]
    cfg = load_config()
    base = {
        "id": uuid.uuid4().hex[:8],
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "mode": config.MODE,
        "canary_set": canary_set_hash(),
        "rounds": rounds, "style_ids": style_ids,
        "normal_similarity_min": min(self_sims),
        "speed_mean": statistics.mean(speeds) if speeds else None,
        "speed_sd": statistics.pstdev(speeds) if len(speeds) > 1 else 0.0,
        "config": cfg, "config_hash": config_hash(cfg),
    }
    config.BASELINE.write_text(json.dumps(base, indent=1))
    return base


# ---------------------------------------------------------------- compare (facts only)
def fact(clue, text, baseline=None, now=None):
    return {"clue": clue, "text": text, "baseline": baseline, "now": now, "impact": IMPACT.get(clue)}


def compare(base, new):
    rounds = base["rounds"]
    facts = []

    def vals(key):
        return [r[key] for r in rounds if r.get(key) is not None]

    # 1. our own request settings (seen directly)
    if new["settings_hash"] != base["config_hash"]:
        diff = config_diff(base["config"], new["config"])
        fields = ", ".join(f"{d['field']}: {d['before']} -> {d['after']}" for d in diff) or "hash differs"
        facts.append(fact("our_settings_changed", f"Our request config differs from the baseline ({fields})",
                          base["config_hash"], new["settings_hash"]))

    # 2. task outcomes and rates (0.2 margin beyond anything the baseline ever showed)
    checks = [
        ("refusal_rate", "Refusals on borderline safety canaries", "refusals_up", "refusals_down"),
        ("control_refusal_rate", "Refusals on the harmless control question", "control_refused", None),
        ("homework_rate", "Homework canary solved", None, "homework_refused"),
        ("dosage_rate", "Medical summary keeps every dose", None, "dosage_dropped"),
        ("json_rate", "Parser canaries return raw valid JSON", None, "format_changed"),
        ("fence_rate", "JSON wrapped in ``` code fences", "format_changed", None),
        ("correct_rate", "Correct answers to fixed questions", None, "correct_down"),
        ("long_memory_rate", "Remembers the start of a long input", None, "memory_down"),
        ("cut_off_rate", "Answers cut off by the token limit", "cutoff_up", None),
    ]
    seen = set()
    for key, label, up, down in checks:
        v = vals(key)
        if not v or new.get(key) is None:
            continue
        lo, hi = min(v), max(v)
        rng = f"{lo:.0%}" if lo == hi else f"{lo:.0%}-{hi:.0%}"
        clue = up if (up and new[key] > hi + 0.2) else down if (down and new[key] < lo - 0.2) else None
        if clue and clue not in seen:
            seen.add(clue)
            facts.append(fact(clue, f"{label}: baseline {rng}, now {new[key]:.0%}", rng, f"{new[key]:.0%}"))

    # 3. speed - noise-aware: max(tolerance, 4 standard errors)
    mu, sd = base.get("speed_mean"), base.get("speed_sd") or 0
    if mu and new["speed"]:
        allow = max(config.SPEED_TOLERANCE * mu, 4 * sd / math.sqrt(max(1, len(new["speeds"]))))
        if abs(new["speed"] - mu) > allow:
            d = "faster" if new["speed"] > mu else "slower"
            facts.append(fact("speed_" + d, f"Generation speed: baseline {mu:.1f} tok/s (±{allow:.1f} normal), "
                                            f"now {new['speed']:.1f} ({d})", round(mu, 1), round(new["speed"], 1)))

    # 4. token fingerprint: the same prompt must always count the same number of input tokens
    base_tok = {}
    for r in rounds:
        for cid, n in r["tokens"].items():
            base_tok.setdefault(cid, []).append(n)
    short = [c for c in new["tokens"] if not c.startswith("long") and c in base_tok]
    changed = [c for c in short if new["tokens"][c] not in base_tok[c]]
    if short and len(changed) / len(short) > 0.3:
        deltas = [new["tokens"][c] - statistics.median(base_tok[c]) for c in changed]
        if max(deltas) - min(deltas) <= 2 and abs(statistics.median(deltas)) >= 3:
            facts.append(fact("constant_token_offset",
                              f"Every prompt now counts about {statistics.median(deltas):+.0f} input tokens "
                              f"({len(changed)}/{len(short)} canaries) - the same text added in front of each prompt",
                              "stable", f"{statistics.median(deltas):+.0f}"))
        else:
            facts.append(fact("token_count_changed",
                              f"Same prompts now count a different number of input tokens, by different amounts "
                              f"({len(changed)}/{len(short)} canaries) - a different tokenizer",
                              "stable", f"{len(changed)} changed"))

    # 5. long input truncated by the server
    longs = vals("long_prompt_tokens")
    if longs and new["long_prompt_tokens"] and new["long_prompt_tokens"] < 0.8 * min(longs):
        facts.append(fact("long_text_cut", f"The long canary was read as {new['long_prompt_tokens']} tokens, "
                                           f"baseline {min(longs)} - the server cut it", min(longs), new["long_prompt_tokens"]))

    # 6. wording of free-text answers + how the model describes itself
    sim = text_similarity(new, rounds, base["style_ids"])
    nmin = base["normal_similarity_min"]
    if sim < nmin - 0.35:
        facts.append(fact("style_very_different", f"Answer wording very different: similarity {sim:.2f} (normal ≥ {nmin:.2f})",
                          round(nmin, 2), round(sim, 2)))
    elif sim < nmin - 0.05:
        facts.append(fact("style_slightly_different", f"Answer wording slightly different: similarity {sim:.2f} (normal ≥ {nmin:.2f})",
                          round(nmin, 2), round(sim, 2)))
    ident = text_similarity(new, rounds, ["style_identity"])
    if ident < 0.5:
        facts.append(fact("identity_changed", f"The model now describes itself differently (similarity {ident:.2f})",
                          "same", round(ident, 2)))

    # 7. answer length
    so = vals("style_output_tokens")
    if so and new["style_output_tokens"] is not None:
        m = statistics.mean(so)
        if m and abs(new["style_output_tokens"] - m) > 0.4 * m:
            facts.append(fact("length_changed", f"Free-text answers are {'longer' if new['style_output_tokens'] > m else 'shorter'}: "
                                                f"{m:.0f} -> {new['style_output_tokens']:.0f} output tokens", round(m), round(new["style_output_tokens"])))

    # 8. WHO is it now? compare with the fingerprint library (only when our own settings are unchanged)
    import fingerprints
    lib = fingerprints.load()
    if lib and new["settings_hash"] == base["config_hash"]:
        now_r, was_r = fingerprints.identify(new, lib), fingerprints.identify(rounds[0], lib)
        now_b, was_b = now_r[0], was_r[0]
        was_now = next((x["score"] for x in now_r if x["spec"] == was_b["spec"]), 0)
        if now_b["spec"] != was_b["spec"] and now_b["score"] >= fingerprints.CLOSE and now_b["score"] - was_now >= 0.08:
            same = now_b["core"] == was_b["core"]
            f = fact("identity_moved",
                     f"Answers now match the fingerprint of {now_b['label']} ({now_b['score']:.0%}); "
                     f"at baseline they matched {was_b['label']} (now only {was_now:.0%})"
                     + (" - another BUILD of the same model" if same else ""),
                     was_b["label"], now_b["label"])
            f["same_build"] = same
            facts.append(f)
        elif was_b["score"] >= fingerprints.MATCH and now_b["score"] < fingerprints.CLOSE:
            facts.append(fact("identity_unknown",
                              f"Answers no longer match {was_b['label']} or any other model in the fingerprint library "
                              f"(best {now_b['label']}, {now_b['score']:.0%})", was_b["label"], "unknown"))

    # 9. model name echoed by the provider (usually unchanged - that's the whole problem)
    if new["model_names"] != rounds[0]["model_names"]:
        facts.append(fact("model_name_changed", f"Provider reports model {new['model_names']}, baseline {rounds[0]['model_names']}",
                          rounds[0]["model_names"], new["model_names"]))
    return facts


# ---------------------------------------------------------------- attribution (inference)
def find_cause(facts):
    clues = {f["clue"] for f in facts}
    points = {k: 0 for k in LAYERS}
    why = {k: [] for k in LAYERS}

    def add(layer, n, reason):
        points[layer] += n
        why[layer].append(reason)

    ours = "our_settings_changed" in clues
    tokenizer = "token_count_changed" in clues
    refusing = "refusals_up" in clues
    restyled = "style_very_different" in clues or "identity_changed" in clues

    if ours:
        add("request_config", 10, "our own request config is different (seen directly)")
    idf = next((f for f in facts if f["clue"] == "identity_moved"), None)
    if idf and not ours:
        if idf.get("same_build"):
            add("serving", 3, "answers match another BUILD of the same model in the fingerprint library = quantisation/serving change")
        else:
            add("model", 4, f"answers now match a different model's fingerprint ({idf['now']})")
    if "identity_unknown" in clues and not ours and not refusing:
        add("model", 2, "answers match no known model any more")
    if "model_name_changed" in clues:
        add("model", 5, "the provider reports a different model name")
    if tokenizer and not ours:
        add("model", 5, "identical prompts count different tokens by different amounts = a different tokenizer = different weights")
    if "identity_changed" in clues and not ours:
        add("model", 3, "the model describes itself differently")
    if "style_very_different" in clues and not ours and not refusing:
        add("model", 3, "wording is very different from every baseline answer")
    if "format_changed" in clues and not ours:
        add("model", 2, "output format habits changed (e.g. JSON now wrapped in code fences)")
    if "constant_token_offset" in clues and not ours:
        if restyled:
            add("model", 1, "token counts shifted while the wording changed a lot")
        else:
            add("safety", 2, "the same text was added in front of every prompt - typical of a provider-side policy prompt")
    if refusing:
        add("safety", 4, "it refuses borderline questions it used to answer")
        if "control_refused" not in clues:
            add("safety", 1, "it still answers the harmless control = a targeted policy, not general breakage")
        if not tokenizer and "identity_changed" not in clues:
            add("safety", 1, "tokenizer and self-description unchanged = probably the same model")
    if "homework_refused" in clues and refusing and not ours:
        add("safety", 2, "homework help is now refused too - a stricter content policy")
    if "refusals_down" in clues:
        add("safety", 3, "it refuses fewer borderline questions than before")
    if "control_refused" in clues:
        add("model", 1, "even harmless questions are refused")
    if "long_text_cut" in clues or "memory_down" in clues:
        if tokenizer:
            add("model", 1, "long input handled differently, but the tokenizer also changed")
        else:
            add("serving", 4, "long input is cut with the same tokenizer = a smaller context window on the server")
    if ({"speed_faster", "speed_slower"} & clues) and not tokenizer and not refusing and not restyled and not ours:
        add("serving", 3, "speed changed while tokenizer and answers stayed the same = same model, different serving setup")
    if "style_slightly_different" in clues and not tokenizer and not ours and not refusing:
        add("serving", 2, "wording only slightly different = same model, different numerics (e.g. quantisation)")
    if "cutoff_up" in clues:
        add("request_config" if ours else "serving", 2, "answers are cut off early")
    if "length_changed" in clues:
        add("request_config" if ours else "model", 1, "answer length changed")
    if ({"correct_down", "dosage_dropped"} & clues) and not ours and not refusing:
        add("model", 1, "task quality changed")

    total = sum(points.values())
    ranking = sorted(({"layer": k, "points": v, "share": round(v / total, 2) if total else 0}
                      for k, v in points.items()), key=lambda x: -x["points"])
    if total == 0:
        return None, 0.0, ranking, why
    best = ranking[0]["layer"]
    return best, points[best] / total, ranking, why


def confidence_label(c):
    return "high" if c >= 0.7 else "medium" if c >= 0.5 else "low"


# ---------------------------------------------------------------- check + report
def check(trial=None, deep=False, progress=None):
    base = load_baseline()
    if not base:
        raise RuntimeError("No baseline yet - learn a baseline first.")

    total = 15 * (1 + config.REPEATS)

    def cb(off):
        return (lambda d, t: progress(off + d, total)) if progress else None

    rounds = []
    if not deep:
        rounds.append(run_round(repeats=1, progress=cb(0)))
        facts = compare(base, summarise(rounds[0]))
        if facts:   # something looks different -> confirm with a fuller round
            rounds.append(run_round(repeats=config.REPEATS, progress=cb(15)))
            facts2 = compare(base, summarise(rounds[1]))
            keep = {f["clue"] for f in facts} & {f["clue"] for f in facts2}
            facts = [f for f in facts2 if f["clue"] in keep]
    else:
        rounds.append(run_round(repeats=config.REPEATS, progress=cb(0)))
        facts = compare(base, summarise(rounds[0]))

    layer, conf, ranking, why = find_cause(facts)
    changed = bool(facts)
    import fingerprints
    final = summarise(rounds[-1])
    ident = None
    if fingerprints.load():
        if final["settings_hash"] != base["config_hash"]:
            ident = {"kind": "skipped", "text": "Not compared: our own request settings differ from the fingerprints' settings."}
        else:
            ident = fingerprints.verdict(fingerprints.identify(final))
            was = fingerprints.verdict(fingerprints.identify(base["rounds"][0]))
            ident["baseline"] = {"label": was["best"]["label"], "score": was["best"]["score"], "spec": was["best"]["spec"],
                                 "core": was["best"]["core"]}
    points = sum(x["points"] for x in ranking)
    report = {
        "id": uuid.uuid4().hex[:8],
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "trial": trial, "mode": config.MODE,
        "changed": changed,
        "drift_score": min(100, round(points / 12 * 100)) if changed else 0,
        "identity": ident,
        "status": "normal" if not changed else ("changed" if layer else "inconclusive"),
        "observed": facts,
        "what_i_saw": [f["text"] for f in facts],
        "user_impact": sorted({f["impact"] for f in facts if f.get("impact")}),
        "likely_cause": layer if changed else None,
        "likely_cause_meaning": LAYER_INFO.get(layer) if changed else None,
        "confidence": round(conf, 2) if changed else None,
        "confidence_label": confidence_label(conf) if changed and layer else None,
        "reasons": why.get(layer, []) if layer else [],
        "ranking": ranking if changed else [],
        "alternatives": [r for r in ranking[1:] if r["points"] > 0] if changed else [],
        "caveat": CAVEATS.get(layer) if changed else None,
        "config_diff": config_diff(base["config"], load_config()),
        "probe_cost": total_cost(rounds),
        "reproduce": {"baseline_id": base["id"], "baseline_time": base["time"], "canary_set": base["canary_set"],
                      "config_hash_now": config_hash(load_config()), "config_hash_baseline": base["config_hash"]},
        "baseline_time": base["time"],
    }
    with open(config.REPORTS, "a") as f:
        f.write(json.dumps(report) + "\n")
    return report


def read_reports(limit=50):
    try:
        rows = [json.loads(x) for x in config.REPORTS.read_text().splitlines() if x.strip()]
    except FileNotFoundError:
        rows = []
    return rows[-limit:][::-1]


def report_markdown(r):
    """A shareable incident report - evidence and inference kept apart."""
    c = r["probe_cost"]
    L = [f"# SilentShift report {r['id']}", "",
         f"- **Time:** {r['time']}  ·  **Status:** {r['status'].upper()}  ·  **Mode:** {r.get('mode', '')}",
         f"- **Baseline:** {r['reproduce']['baseline_id']} ({r['reproduce']['baseline_time']}) · canary set {r['reproduce']['canary_set']}",
         f"- **Probe cost:** {c['calls']} calls, {c['tokens']} tokens, {c['seconds']} s, ~${c['est_usd']} (stages {' + '.join(c['stages'])})",
         "", "## 1. Observed evidence (facts)", ""]
    L += [f"- {f['text']}" for f in r["observed"]] or ["- Every signal is inside the normal range learned in the baseline."]
    if r.get("user_impact"):
        L += ["", "## 2. What users would notice", ""] + [f"- {x}" for x in r["user_impact"]]
    idn = r.get("identity")
    if idn and idn.get("kind") != "skipped":
        L += ["", "## Who is behind the name now? (fingerprint match)", "", f"- {idn['text']}",
              f"- At baseline it matched {idn['baseline']['label']} ({idn['baseline']['score']:.0%})"]
        L += [f"  - {x['label']}: {x['score']:.0%} (tokenizer {x['tokenizer'] if x['tokenizer'] is not None else 'n/a'}, "
              f"answers {x['answers']}, behaviour {x['behaviour']})" for x in idn["ranked"]]
    if r["changed"]:
        L += ["", "## 3. Likely cause (inference)", "",
              f"**{(r['likely_cause'] or 'inconclusive').replace('_', ' ').title()}** - "
              f"{r['confidence']:.0%} ({r['confidence_label']} confidence)." if r["likely_cause"] else "**Inconclusive.**",
              "", r.get("likely_cause_meaning") or ""]
        L += [f"- because {x}" for x in r["reasons"]]
        if r["alternatives"]:
            L += ["", "Also possible: " + ", ".join(f"{a['layer'].replace('_', ' ')} ({a['share']:.0%})" for a in r["alternatives"])]
        L += ["", "## 4. What this cannot prove", "", r.get("caveat") or ""]
    L += ["", "## Reproduce", "", f"Same canary set ({r['reproduce']['canary_set']}), request config hash "
          f"{r['reproduce']['config_hash_now']} (baseline {r['reproduce']['config_hash_baseline']}). "
          "No user prompts or answers are stored; canaries are synthetic."]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    if cmd == "baseline":
        b = make_baseline()
        print(f"Baseline {b['id']} saved.")
    else:
        print(report_markdown(check(deep=cmd == "deep")))
