"""
judge.py - decides whether a model's answer is correct, by MEANING not exact words.

Two judges:
  LLM judge (default) : a separate, fixed LLM reads the question, the expected
                        answer, the key facts and the model's answer, and returns
                        PASS/FAIL + score + missing facts + reason (as JSON).
  keyword judge       : simple offline fallback that checks the key facts appear.
                        Used if the judge LLM is unreachable or returns bad JSON.
                        Every result records which judge was used.

IMPORTANT: the judge model must NOT be the model under test (bot-latest),
otherwise a change in the tested model would also change the judge.

Settings (environment variables):
  JUDGE_PROVIDER   default "ollama"   (or "groq" / "gemini")
  JUDGE_MODEL      default "gemma3:4b" on ollama (a different family from the tested Qwen models)
"""

import json
import os
import re

from harness import DEFAULT_MODELS, ProviderError, generate

JUDGE_PROVIDER = os.environ.get("JUDGE_PROVIDER", "ollama")
JUDGE_MODEL = os.environ.get("JUDGE_MODEL") or (
    "gemma3:4b" if JUDGE_PROVIDER == "ollama" else DEFAULT_MODELS[JUDGE_PROVIDER])
PASS_SCORE = 0.7

JUDGE_PROMPT = """You are a strict but fair evaluator of AI answers.
Judge the MODEL ANSWER against the EXPECTED ANSWER and KEY FACTS.
Judge MEANING, not exact wording: paraphrases and extra correct detail are fine.
An answer FAILS if it is wrong, refuses, or leaves out any key fact
(for example a medicine dose, a number, or a required format).

QUESTION:
{question}

EXPECTED ANSWER:
{expected}

KEY FACTS (all must be present in meaning):
{facts}

MODEL ANSWER:
{answer}

Reply with ONLY this JSON, nothing else:
{{"verdict": "PASS" or "FAIL", "score": number from 0 to 1, "missing_facts": [list of missing key facts], "reason": "one short sentence"}}"""

STOP = {"the", "and", "a", "an", "of", "to", "in", "is", "for", "at", "or", "with", "be", "it", "its"}


# ------------------------------------------------------------------ keyword judge
def _fact_present(fact, answer):
    low = answer.lower()
    if fact.lower() == "valid json":
        try:
            json.loads(answer)
            return True
        except Exception:
            return False
    for option in re.split(r"\s+or\s+", fact.lower()):
        words = [w for w in re.findall(r"[a-z0-9]+", option) if w not in STOP]
        if words and all(w in low for w in words):
            return True
    return False


def keyword_judge(item, answer):
    facts = item.get("key_facts") or [item["expected"]]
    missing = [f for f in facts if not _fact_present(f, answer)]
    score = 1 - len(missing) / len(facts)
    return {"verdict": "PASS" if score >= PASS_SCORE else "FAIL", "score": round(score, 2),
            "missing_facts": missing, "reason": "keyword check of key facts", "method": "keyword"}


# ------------------------------------------------------------------ LLM judge
def _parse(text):
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("no JSON in judge reply")
    d = json.loads(match.group(0))
    verdict = str(d.get("verdict", "")).upper()
    if verdict not in ("PASS", "FAIL"):
        raise ValueError("bad verdict")
    score = float(d.get("score", 1.0 if verdict == "PASS" else 0.0))
    return {"verdict": verdict, "score": round(max(0.0, min(1.0, score)), 2),
            "missing_facts": d.get("missing_facts", []) or [],
            "reason": str(d.get("reason", ""))[:200]}


def llm_judge(item, answer):
    prompt = JUDGE_PROMPT.format(
        question=item["question"], expected=item["expected"],
        facts="\n".join("- " + f for f in item.get("key_facts", [])) or "- (none)",
        answer=answer or "(empty answer)")
    last_error = None
    for _ in range(2):                                  # one retry for bad JSON
        try:
            r = generate(JUDGE_PROVIDER, JUDGE_MODEL, prompt,
                         options={"temperature": 0, "seed": 1, "num_predict": 800}, fmt="json")
            result = _parse(r["text"])
            result["method"] = f"llm:{JUDGE_PROVIDER}/{JUDGE_MODEL}"
            return result
        except (ProviderError, ValueError, json.JSONDecodeError) as e:
            last_error = e
    result = keyword_judge(item, answer)
    result["method"] = "keyword_fallback"
    result["reason"] += f" (LLM judge failed: {str(last_error)[:80]})"
    return result


def judge(item, answer, mode="llm"):
    return keyword_judge(item, answer) if mode == "keyword" else llm_judge(item, answer)
