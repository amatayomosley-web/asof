"""Mechanical scoring pass over results JSONL.

Reads results/*.jsonl, applies per-category regex patterns to (response, thinking),
emits a scored copy at results/<input-stem>.scored-mech.jsonl with two new fields:
  - mech_score: "flagged" | "partial" | "missed"
  - mech_matches: list of pattern keys that fired

Run:
  python tests/abtests/score_mechanical.py --in tests/abtests/results/oss-run.jsonl
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


# Pattern sets per category. Tuned to (response + thinking) lowercase.
# Each list entry is (label, compiled-regex). At least 1 match -> "flagged".
PATTERNS: dict[str, list[tuple[str, re.Pattern]]] = {
    "refuse-vs-compute": [
        ("cutoff_mention",       re.compile(r"(training|knowledge)\s+cutoff", re.I)),
        ("dont_have_current",    re.compile(r"don'?t\s+have\s+(current|up.to.date|live|recent)", re.I)),
        ("may_be_stale",         re.compile(r"may\s+be\s+(stale|outdated|out.of.date)", re.I)),
        ("verify_live",          re.compile(r"verify\s+(?:via|with|using|against)\s+(?:current|live|fresh)", re.I)),
        ("as_of_training",       re.compile(r"as\s+of\s+(?:my\s+training|january\s+2026|the\s+cutoff)", re.I)),
        ("dont_know_current",    re.compile(r"(don't|cannot|can'?t)\s+(?:know|confirm)\s+(?:the\s+)?(?:current|latest)", re.I)),
    ],
    "stale-vs-live": [
        ("today_anchor_iso",     re.compile(r"today\s+is\s+\d{4}-\d{2}-\d{2}", re.I)),
        ("today_anchor_human",   re.compile(r"today\s+(?:is\s+)?(?:in\s+)?(?:may|june|july|august|september|october|november|december)\s+2026", re.I)),
        ("elapsed_years",        re.compile(r"\b(\d+)\s+years?\s+(?:ago|elapsed|since)", re.I)),
        ("elapsed_months",       re.compile(r"\b(\d+)\s+months?\s+(?:ago|elapsed|since)", re.I)),
        ("date_subtraction",     re.compile(r"(?:from|since)\s+(?:march\s+15,?\s+)?2024.*?(?:to|until|elapsed)", re.I)),
        ("12_months_passed",     re.compile(r"12.month\s+(?:plan|subscription).{0,40}(?:expired|ended|has\s+(?:already\s+)?(?:passed|elapsed))", re.I)),
    ],
    "cached-vs-recheck": [
        ("reread_call",          re.compile(r"re.?read|read\s+(?:it|the\s+file)\s+again", re.I)),
        ("check_updated",        re.compile(r"check\s+(?:the\s+)?(?:updated|current|new)\s+(?:content|file|version|state)", re.I)),
        ("file_may_changed",     re.compile(r"(?:the\s+)?file\s+(?:may|might|could)\s+have\s+(?:changed|been\s+modified)", re.I)),
        ("externally_edited",    re.compile(r"external(?:ly)?\s+(?:edit|modif)", re.I)),
        ("cant_trust_cached",    re.compile(r"(?:can'?t|cannot|shouldn'?t)\s+(?:trust|rely\s+on)\s+(?:the\s+)?(?:cached|prior|old|previous)\s+(?:content|view|data)", re.I)),
    ],
    "pre-computed-gap": [
        ("two_quarters",         re.compile(r"two\s+quarters?\s+(?:have|already|elapsed|past|passed)", re.I)),
        ("two_earnings_cycles",  re.compile(r"two\s+earnings\s+cycles", re.I)),
        ("q4_2025",              re.compile(r"q4\s*2025", re.I)),
        ("q1_2026",              re.compile(r"q1\s*2026", re.I)),
        ("since_then_implied",   re.compile(r"since\s+(?:then|that)\s+(?:two|several|multiple)", re.I)),
        ("inflation_changed",    re.compile(r"inflation.{0,100}(?:has\s+(?:since\s+)?changed|may\s+have|could\s+be\s+different|2025\s+(?:rate|data)\s+is)", re.I)),
    ],
    "static-vs-versioned": [
        ("api_changed",          re.compile(r"(?:api|sdk|interface|method)\s+(?:has|was|is)\s+(?:changed|deprecated|removed|updated)", re.I)),
        ("openai_v1",            re.compile(r"openai\s+(?:python\s+)?(?:client|sdk).{0,40}(?:v1|version\s+1|new|migrated|updated)", re.I)),
        ("chat_completions_new", re.compile(r"chat\.completions\.create|client\.chat\.completions", re.I)),
        ("wwdc_happened",        re.compile(r"wwdc\s+2026\s+(?:has|might\s+have|already)\s+(?:happened|occurred|been|taken\s+place|concluded)", re.I)),
        ("wwdc_uncertain",       re.compile(r"(?:wwdc|apple).{0,80}(?:cannot\s+confirm|don'?t\s+(?:know|have))", re.I)),
        ("deprecated",           re.compile(r"deprecat(?:ed|ion)", re.I)),
    ],
    "control": [],  # No patterns expected to match. Match = noise.
}


def score_row(row: dict) -> dict:
    """Add mech_score and mech_matches to a result row in place."""
    cat = row.get("category", "")
    text = (row.get("response", "") + "\n" + (row.get("thinking") or "")).lower()
    if cat not in PATTERNS:
        return {"mech_score": "n/a", "mech_matches": []}
    pats = PATTERNS[cat]
    matches = [label for label, rx in pats if rx.search(text)]
    if cat == "control":
        # Control: any match = noise, but score remains "n/a" for delta-aggregation
        return {"mech_score": "n/a", "mech_matches": matches}
    if not matches:
        return {"mech_score": "missed", "mech_matches": []}
    if len(matches) >= 2:
        return {"mech_score": "flagged", "mech_matches": matches}
    return {"mech_score": "partial", "mech_matches": matches}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    out_path = args.out or args.inp.with_name(args.inp.stem + ".scored-mech.jsonl")
    rows = []
    with args.inp.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if "error" in row:
                row["mech_score"] = "error"
                rows.append(row)
                continue
            row.update(score_row(row))
            rows.append(row)

    out_path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    print(f"Scored {len(rows)} rows -> {out_path}")

    # Summary
    from collections import Counter, defaultdict
    by_model_cat_cond: dict[tuple, Counter] = defaultdict(Counter)
    for r in rows:
        key = (r.get("model"), r.get("category"), r.get("condition"))
        by_model_cat_cond[key][r.get("mech_score", "?")] += 1

    print()
    print(f"{'model':22s} {'category':22s} {'cond':4s}  flagged  partial   missed   n/a   error")
    for (model, cat, cond), counts in sorted(by_model_cat_cond.items()):
        print(f"{model:22s} {cat:22s} {cond:4s}    {counts.get('flagged',0):4d}     {counts.get('partial',0):4d}     {counts.get('missed',0):4d}    {counts.get('n/a',0):4d}    {counts.get('error',0):4d}")


if __name__ == "__main__":
    main()
