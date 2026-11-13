"""
evaluator.py - the EVALUATION ENGINE.

It measures how GOOD the answers are, using a ground-truth dataset (eval_set.json)
and a judge LLM. (canaries.json + agent.py measure BEHAVIOUR FINGERPRINTS; this
file measures QUALITY. SilentShift uses both.)

Commands (PowerShell):
  python evaluator.py run baseline                 # score the current setup, save runs/baseline.json
  python evaluator.py run current                  # score again later
  python evaluator.py compare baseline current     # what changed in quality?
  python evaluator.py variants qwen2.5:3b qwen2.5:1.5b qwen2.5:3b-instruct-q2_K
                                                   # same dataset on several model variants
Options:
  --provider ollama|groq|gemini   (default: from app_config.json, else ollama)
  --model NAME                    (default: from app_config.json)
  --judge llm|keyword             (default llm; keyword = offline fallback)
"""

import argparse
import hashlib
import json
import math
import os
import statistics
import time

from harness import generate
from judge import JUDGE_MODEL, JUDGE_PROVIDER, judge
from runner import REFUSAL_WORDS

RUNS_DIR = "runs"


def load(path):
    with open(path) as f:
        return json.load(f)


def config_hash(cfg):
    return hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:12]


# ------------------------------------------------------------------ statistics
def wilson(passed, n, z=1.96):
    """95% confidence interval for a pass rate (works well for small n)."""
    if n == 0:
        return (0.0, 0.0)
    p = passed / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (max(0.0, centre - half), min(1.0, centre + half))


def mcnemar_p(b, c):
    """Exact McNemar test on the questions that flipped.
    b = pass->fail, c = fail->pass. Small p (< 0.05) = change unlikely to be luck."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    p = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * p)


# ------------------------------------------------------------------ run
def run_eval(label, provider=None, model=None, judge_mode="llm",
             eval_path="eval_set.json", config_path="app_config.json", quiet=False):
    cfg = load(config_path)
    provider = provider or cfg.get("provider", "ollama")
    model = model or cfg["model"]
    items = load(eval_path)
    results = []
    for i, item in enumerate(items, 1):
        r = generate(provider, model, item["question"], system=cfg.get("system"),
                     options=cfg.get("options", {}), fmt=cfg.get("format"))
        verdict = judge(item, r["text"], judge_mode)
        results.append({
            "id": item["id"], "category": item["category"],
            "answer": r["text"],               # synthetic eval questions only, never user data
            **verdict,
            "refused": any(w in r["text"].lower() for w in REFUSAL_WORDS),
            "prompt_tokens": r["prompt_tokens"], "output_tokens": r["output_tokens"],
            "tokens_per_sec": r["tokens_per_sec"], "seconds": round(r["seconds"], 3),
            "done_reason": r["done_reason"], "provider_used": r["provider"],
        })
        if not quiet:
            print(f"  [{i:2d}/{len(items)}] {item['id']:10s} {verdict['verdict']}  {verdict['reason'][:60]}")

    run = {"label": label, "time": time.strftime("%Y-%m-%d %H:%M:%S"),
           "provider": provider, "model": model, "settings_hash": config_hash(cfg),
           "judge": judge_mode if judge_mode == "keyword" else f"{JUDGE_PROVIDER}/{JUDGE_MODEL}",
           "summary": summarise(results), "results": results}
    os.makedirs(RUNS_DIR, exist_ok=True)
    with open(os.path.join(RUNS_DIR, safe(label) + ".json"), "w") as f:
        json.dump(run, f, indent=2)
    s = run["summary"]
    lo, hi = s["pass_rate_ci"]
    print(f"\n{label}: {s['passed']}/{s['n']} passed = {s['pass_rate']:.0%} "
          f"(95% range {lo:.0%}-{hi:.0%}), mean score {s['mean_score']:.2f}")
    return run


def safe(label):
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in label)


def summarise(results):
    n = len(results)
    passed = sum(r["verdict"] == "PASS" for r in results)
    cats = {}
    for r in results:
        c = cats.setdefault(r["category"], [0, 0])
        c[0] += r["verdict"] == "PASS"
        c[1] += 1
    speeds = [r["tokens_per_sec"] for r in results if r["tokens_per_sec"]]
    return {
        "n": n, "passed": passed, "pass_rate": passed / n if n else 0,
        "pass_rate_ci": wilson(passed, n),
        "mean_score": statistics.mean(r["score"] for r in results) if n else 0,
        "per_category": {k: v[0] / v[1] for k, v in cats.items()},
        "refusal_rate": sum(r["refused"] for r in results) / n if n else 0,
        "truncated_rate": sum(r["done_reason"] == "length" for r in results) / n if n else 0,
        "avg_seconds": statistics.mean(r["seconds"] for r in results) if n else 0,
        "avg_tokens_per_sec": statistics.mean(speeds) if speeds else None,
        "avg_output_tokens": statistics.mean((r["output_tokens"] or 0) for r in results) if n else 0,
        "judge_methods": dict(sorted({r["method"]: sum(x["method"] == r["method"] for x in results)
                                      for r in results}.items())),
    }


# ------------------------------------------------------------------ compare
def compare_runs(a, b):
    """Compare run a (baseline) with run b (current). Returns facts, not guesses."""
    sa, sb = a["summary"], b["summary"]
    ra = {r["id"]: r for r in a["results"]}
    rb = {r["id"]: r for r in b["results"]}
    common = [i for i in ra if i in rb]
    pass_to_fail = [i for i in common if ra[i]["verdict"] == "PASS" and rb[i]["verdict"] == "FAIL"]
    fail_to_pass = [i for i in common if ra[i]["verdict"] == "FAIL" and rb[i]["verdict"] == "PASS"]
    p = mcnemar_p(len(pass_to_fail), len(fail_to_pass))
    delta = sb["pass_rate"] - sa["pass_rate"]

    if delta < 0 and p < 0.05:
        verdict = "SIGNIFICANT DROP"
    elif delta > 0 and p < 0.05:
        verdict = "SIGNIFICANT IMPROVEMENT"
    elif pass_to_fail or fail_to_pass:
        verdict = "POSSIBLE CHANGE (not statistically clear with this many questions)"
    else:
        verdict = "NO MEANINGFUL CHANGE"

    cat_deltas = {c: sb["per_category"].get(c, 0) - sa["per_category"][c] for c in sa["per_category"]}
    speed_change = None
    if sa["avg_tokens_per_sec"] and sb["avg_tokens_per_sec"]:
        speed_change = sb["avg_tokens_per_sec"] / sa["avg_tokens_per_sec"] - 1
    answered = [i for i in common if not ra[i]["refused"] and not rb[i]["refused"]]
    offsets = [(rb[i]["prompt_tokens"] or 0) - (ra[i]["prompt_tokens"] or 0) for i in answered
               if ra[i]["prompt_tokens"] != rb[i]["prompt_tokens"]]
    token_changed = len(offsets)
    constant_offset = bool(offsets) and (max(offsets) - min(offsets)
                                         <= max(3, 0.1 * abs(statistics.mean(offsets))))

    return {
        "baseline": a["label"], "current": b["label"],
        "baseline_model": f"{a['provider']}/{a['model']}", "current_model": f"{b['provider']}/{b['model']}",
        "baseline_pass_rate": sa["pass_rate"], "current_pass_rate": sb["pass_rate"],
        "delta_points": round(delta * 100, 1), "p_value": round(p, 4), "verdict": verdict,
        "pass_to_fail": pass_to_fail, "fail_to_pass": fail_to_pass,
        "category_deltas": cat_deltas,
        "refusal_rate": (sa["refusal_rate"], sb["refusal_rate"]),
        "truncated_rate": (sa["truncated_rate"], sb["truncated_rate"]),
        "speed_change": speed_change,
        "prompt_token_changes": f"{token_changed}/{len(answered)}",
        "constant_token_offset": constant_offset,
        "answers_changed": sum(1 for i in answered if ra[i]["answer"] != rb[i]["answer"]),
        "answered_ids": answered,
        "settings_changed": a["settings_hash"] != b["settings_hash"],
        "failed_details": [{"id": i, "category": rb[i]["category"], "reason": rb[i]["reason"],
                            "missing": rb[i]["missing_facts"]} for i in pass_to_fail],
    }


def print_comparison(c):
    print("\n" + "=" * 64)
    print(f"QUALITY COMPARISON  {c['baseline']} -> {c['current']}")
    print("=" * 64)
    print(f"Pass rate : {c['baseline_pass_rate']:.0%} -> {c['current_pass_rate']:.0%}  "
          f"({c['delta_points']:+.1f} points)")
    print(f"Verdict   : {c['verdict']}  (McNemar p = {c['p_value']})")
    print(f"Flipped   : {len(c['pass_to_fail'])} pass->fail, {len(c['fail_to_pass'])} fail->pass")
    for cat, d in sorted(c["category_deltas"].items(), key=lambda x: x[1]):
        if abs(d) > 0.001:
            print(f"  {cat:16s} {d * 100:+.0f} points")
    for f in c["failed_details"][:8]:
        miss = ", ".join(f["missing"]) if f["missing"] else "-"
        print(f"  newly failing: {f['id']} ({f['category']}) missing: {miss}")
    print("=" * 64)


# ------------------------------------------------------------------ variants
def compare_variants(models, provider, judge_mode):
    runs = [run_eval(m, provider, m, judge_mode, quiet=True) for m in models]
    base = runs[0]
    lines = ["# Model variant comparison", "",
             f"Same {base['summary']['n']}-question evaluation dataset, judge: {base['judge']}.",
             f"Reference model: `{models[0]}`", "",
             "| Model | Pass rate (95% range) | Change vs reference | p-value | Avg speed (tok/s) | Verdict |",
             "|---|---|---|---|---|---|"]
    for r in runs:
        s = r["summary"]
        lo, hi = s["pass_rate_ci"]
        speed = f"{s['avg_tokens_per_sec']:.1f}" if s["avg_tokens_per_sec"] else "n/a"
        if r is base:
            lines.append(f"| {r['model']} | {s['pass_rate']:.0%} ({lo:.0%}-{hi:.0%}) | reference | - | {speed} | - |")
            continue
        c = compare_runs(base, r)
        lines.append(f"| {r['model']} | {s['pass_rate']:.0%} ({lo:.0%}-{hi:.0%}) | "
                     f"{c['delta_points']:+.1f} pts | {c['p_value']} | {speed} | {c['verdict']} |")
    text = "\n".join(lines)
    with open("variants_report.md", "w") as f:
        f.write(text + "\n")
    print("\n" + text + "\n\nSaved variants_report.md")


def load_run(label):
    return load(os.path.join(RUNS_DIR, safe(label) + ".json"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["run", "compare", "variants"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--provider")
    ap.add_argument("--model")
    ap.add_argument("--judge", default="llm", choices=["llm", "keyword"])
    a = ap.parse_args()
    if a.command == "run":
        run_eval(a.args[0] if a.args else "run", a.provider, a.model, a.judge)
    elif a.command == "compare":
        print_comparison(compare_runs(load_run(a.args[0]), load_run(a.args[1])))
    else:
        compare_variants(a.args, a.provider or "ollama", a.judge)
