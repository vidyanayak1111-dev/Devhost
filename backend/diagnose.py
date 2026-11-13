"""
diagnose.py - writes the DIAGNOSTIC REPORT.

It combines two kinds of evidence:
  1. Quality evidence     : evaluator.py (ground-truth dataset + judge LLM)
  2. Behaviour fingerprints: agent.py (canaries: tokenizer, speed, refusals, our settings)

and produces a report with four parts:
  Observed changes  -> facts we measured
  Supporting evidence -> which questions failed and why
  Possible explanations -> ranked layers, with confidence (high / medium / low)
  Uncertainty        -> what we CANNOT know from black-box testing

Usage:
  python diagnose.py baseline current              # quality evidence only
  python diagnose.py baseline current --fingerprint # also run the canary agent
"""

import argparse
import json
import os
import time

import evaluator

LAYER_NAMES = {"model": "Model layer", "serving": "Serving setup",
               "safety": "Safety layer", "request_config": "Request configuration"}
CONFIRM = {
    "model": "Ask the provider which model/version the alias points to; compare tokenizer counts on more inputs.",
    "serving": "Compare speed and outputs on a fixed seed over more runs; ask the provider about quantisation, "
               "context window or inference-server updates.",
    "safety": "Check refused questions manually; ask the provider about safety-filter or policy updates.",
    "request_config": "Diff your own app_config.json / deployment settings against the baseline.",
}


def eval_points(c):
    """Quality evidence -> points per layer, with the reason for each point."""
    pts = {k: 0 for k in LAYER_NAMES}
    why = {k: [] for k in LAYER_NAMES}

    def add(layer, n, reason):
        pts[layer] += n
        why[layer].append(reason)

    same_tokenizer = c["prompt_token_changes"].startswith("0/") or c.get("constant_token_offset", False)
    ref_a, ref_b = c["refusal_rate"]
    tr_a, tr_b = c["truncated_rate"]

    if c["settings_changed"]:
        add("request_config", 10, "our own request settings differ from the baseline (directly observed)")
    if ref_b - ref_a > 0.05:
        add("safety", 4, f"refusals on evaluation questions rose {ref_a:.0%} -> {ref_b:.0%}")
    if tr_b - tr_a > 0.1:
        if c["settings_changed"]:
            add("request_config", 2, f"answers cut off early {tr_a:.0%} -> {tr_b:.0%}")
        else:
            add("serving", 2, f"answers cut off early {tr_a:.0%} -> {tr_b:.0%} with unchanged settings")
    tokens_moved = not c["prompt_token_changes"].startswith("0/")
    if tokens_moved and not c["settings_changed"]:
        if c.get("constant_token_offset"):
            if ref_b - ref_a > 0.05:
                add("safety", 2, "every question gained the same number of tokens: a hidden instruction "
                                 "(likely a safety policy) was added by the provider")
            else:
                add("serving", 1, "every question gained the same number of tokens: a hidden instruction was added")
                add("safety", 1, "a hidden provider instruction could be a safety policy")
        else:
            add("model", 4, f"prompt token counts changed for {c['prompt_token_changes']} questions "
                            "by different amounts (different tokenizer)")
    quality_moved = c.get("answers_changed", 0) > 0.1 * max(1, len(c.get("answered_ids", [])) or 1)
    if c["speed_change"] is not None and abs(c["speed_change"]) > 0.15 and same_tokenizer \
            and not c["settings_changed"] and ref_b - ref_a <= 0.05:
        if quality_moved:
            add("model", 1, f"speed changed {c['speed_change']:+.0%} together with the answers = different weights")
        else:
            add("serving", 2, f"speed changed {c['speed_change']:+.0%} but answers stayed the same "
                              "= same model on different hardware or settings")
    if c["delta_points"] < 0 and ref_b - ref_a <= 0.05 and not c["settings_changed"]:
        if same_tokenizer:
            add("model", 2, "quality dropped with the same tokenizer: consistent with a smaller, distilled "
                            "or more heavily quantised model of the same family")
        else:
            add("model", 2, "quality dropped and the tokenizer changed")
    return pts, why


def confidence_label(share, evidence_count):
    if share >= 0.7 and evidence_count >= 2:
        return "high"
    if share >= 0.45:
        return "medium"
    return "low"


def build_report(c, fingerprint=None):
    pts, why = eval_points(c)
    if fingerprint and fingerprint.get("changed"):
        for layer, n in fingerprint["points"].items():
            pts[layer] += n
        for layer in fingerprint["points"]:
            if layer == fingerprint["likely_cause"]:
                why[layer] += [f"canary fingerprint: {r}" for r in fingerprint["reasons"]]

    total = sum(pts.values())
    ranked = sorted(((l, p) for l, p in pts.items() if p > 0), key=lambda x: -x[1])
    now = time.strftime("%Y-%m-%d %H:%M:%S")

    L = [f"# SilentShift diagnostic report", f"_Generated {now}_", "",
         "## 1. Summary", "",
         f"- Baseline: **{c['baseline']}** ({c['baseline_model']}) — pass rate **{c['baseline_pass_rate']:.0%}**",
         f"- Current: **{c['current']}** ({c['current_model']}) — pass rate **{c['current_pass_rate']:.0%}**",
         f"- Change: **{c['delta_points']:+.1f} percentage points** — {c['verdict']} (McNemar p = {c['p_value']})", ""]

    L += ["## 2. Observed changes (measured facts)", "",
          "| Measurement | Baseline | Current |", "|---|---|---|",
          f"| Pass rate | {c['baseline_pass_rate']:.0%} | {c['current_pass_rate']:.0%} |",
          f"| Refusal rate (eval questions) | {c['refusal_rate'][0]:.0%} | {c['refusal_rate'][1]:.0%} |",
          f"| Answers cut off early | {c['truncated_rate'][0]:.0%} | {c['truncated_rate'][1]:.0%} |",
          f"| Speed change | - | {c['speed_change']:+.0%} |" if c["speed_change"] is not None
          else "| Speed change | - | n/a |",
          f"| Questions with different prompt token count | - | {c['prompt_token_changes']} |",
          f"| Our request settings changed | - | {'yes' if c['settings_changed'] else 'no'} |", ""]
    moved = {k: v for k, v in c["category_deltas"].items() if abs(v) > 0.001}
    if moved:
        L += ["Per-category change:", ""] + [f"- {k}: {v * 100:+.0f} points" for k, v in
                                               sorted(moved.items(), key=lambda x: x[1])] + [""]
    if fingerprint:
        L += ["Canary fingerprint check:", ""]
        L += [f"- {t}" for t in fingerprint["what_i_saw"]] or ["- no fingerprint change detected"]
        L += [""]

    L += ["## 3. Supporting evidence (questions that newly failed)", ""]
    if c["failed_details"]:
        L += ["| Question | Category | Missing facts | Judge's reason |", "|---|---|---|---|"]
        for f in c["failed_details"]:
            L.append(f"| {f['id']} | {f['category']} | {', '.join(f['missing']) or '-'} | {f['reason']} |")
    else:
        L.append("No question changed from pass to fail.")
    if c["fail_to_pass"]:
        L += ["", f"Questions that improved: {', '.join(c['fail_to_pass'])}"]
    L.append("")

    L += ["## 4. Possible explanations (ranked)", ""]
    if not ranked:
        L.append("No layer is implicated: measurements are within normal variation.")
    for i, (layer, p) in enumerate(ranked, 1):
        share = p / total
        L += [f"**{i}. {LAYER_NAMES[layer]}** — confidence: **{confidence_label(share, len(why[layer]))}** "
              f"({share:.0%} of evidence points)", ""]
        L += [f"- {w}" for w in why[layer]]
        L += [f"- _How to confirm:_ {CONFIRM[layer]}", ""]

    L += ["## 5. Uncertainty and limitations", "",
          "- This is black-box testing: we observe behaviour, not the provider's internals. "
          "Explanations above are *consistent with* the evidence, not proof.",
          f"- The dataset has {len(c['pass_to_fail']) + len(c['fail_to_pass'])} flipped questions out of the "
          "evaluation set; small datasets give wide confidence ranges (see p-value).",
          "- A smaller, distilled or quantised model of the same family keeps the same tokenizer, so black-box "
          "tests can say 'a different variant of the same model' but cannot always say which kind.",
          "- The judge LLM can make mistakes; failed items are listed above so a person can check them.",
          "- Several changes at the same time will spread the evidence across layers.", ""]
    explanations = [{"layer": layer, "name": LAYER_NAMES[layer], "share": round(p / total, 2),
                     "confidence": confidence_label(p / total, len(why[layer])),
                     "reasons": why[layer], "how_to_confirm": CONFIRM[layer]} for layer, p in ranked]
    uncertainty = [l[2:] for l in L[L.index("## 5. Uncertainty and limitations") + 2:] if l.startswith("- ")]
    return "\n".join(L), {"points": pts, "ranked": ranked, "explanations": explanations,
                          "uncertainty": uncertainty}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("baseline")
    ap.add_argument("current")
    ap.add_argument("--fingerprint", action="store_true", help="also run the canary agent (needs baseline.json)")
    a = ap.parse_args()

    c = evaluator.compare_runs(evaluator.load_run(a.baseline), evaluator.load_run(a.current))
    fp = None
    if a.fingerprint:
        import agent
        fp = agent.check()
    text, _ = build_report(c, fp)
    os.makedirs("reports", exist_ok=True)
    path = os.path.join("reports", f"diagnostic_{time.strftime('%Y%m%d_%H%M%S')}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text + "\n")
    with open(path.replace(".md", ".json"), "w") as f:
        json.dump({"comparison": c, "fingerprint": fp}, f, indent=2)
    print(text)
    print(f"\nSaved {path}")


if __name__ == "__main__":
    main()
