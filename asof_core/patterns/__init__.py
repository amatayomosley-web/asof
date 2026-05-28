"""Pattern tier system for AsOf.

Three tiers govern what triggers a "time-sensitive phrasing detected"
alert based on regex matching against user prompt text:

- high_confidence (default ON): tightly-bound time + dynamic-content
  patterns. Low false-positive rate.
- medium_confidence (default ON): broader temporal flags. Higher
  false-positive rate but bounded.
- domains (opt-in via config): per-domain pattern packs.

The matcher returns a list of (pattern_name, matched_text, kind) tuples
the watch can render. Pattern compilation happens once at session init.
"""
from __future__ import annotations

import re
from typing import Optional

from asof_core.patterns.high_confidence import HIGH_CONFIDENCE_PATTERNS
from asof_core.patterns.medium_confidence import MEDIUM_CONFIDENCE_PATTERNS


_DOMAIN_PACKS: dict[str, list[tuple[str, str]]] = {}


def _load_domain_pack(name: str) -> list[tuple[str, str]]:
    """Lazy-load a domain pack by name. Returns the pattern list."""
    if name in _DOMAIN_PACKS:
        return _DOMAIN_PACKS[name]
    try:
        mod = __import__(f"asof_core.patterns.domains.{name}", fromlist=["PATTERNS"])
        patterns = getattr(mod, "PATTERNS", [])
        _DOMAIN_PACKS[name] = patterns
        return patterns
    except (ImportError, AttributeError):
        return []


class PatternMatcher:
    """Compiled pattern matcher for time-sensitive phrasing in prompts.

    Construction is one-time per session (or per config change). Matching
    is cheap regex evaluation against incoming prompt text.
    """

    def __init__(
        self,
        *,
        high_confidence: bool = True,
        medium_confidence: bool = True,
        domains: Optional[list[str]] = None,
    ):
        self._compiled: list[tuple[str, re.Pattern, str]] = []  # (name, regex, tier)

        if high_confidence:
            for name, pat in HIGH_CONFIDENCE_PATTERNS:
                self._compiled.append((name, re.compile(pat, re.IGNORECASE), "high"))

        if medium_confidence:
            for name, pat in MEDIUM_CONFIDENCE_PATTERNS:
                self._compiled.append((name, re.compile(pat, re.IGNORECASE), "medium"))

        if domains:
            for d in domains:
                for name, pat in _load_domain_pack(d):
                    self._compiled.append((f"{d}:{name}", re.compile(pat, re.IGNORECASE), f"domain:{d}"))

    def match_all(self, text: str) -> list[dict]:
        """Return all pattern matches in `text`. Each match is a dict:
            {
                "pattern": pattern_name,
                "matched": the substring that matched,
                "tier": "high" | "medium" | "domain:<name>",
                "span": (start, end) byte offsets,
            }
        Deduplicates overlapping matches by preferring higher-confidence tiers.
        """
        matches: list[dict] = []
        for name, pat, tier in self._compiled:
            for m in pat.finditer(text):
                matches.append({
                    "pattern": name,
                    "matched": m.group(0),
                    "tier": tier,
                    "span": m.span(),
                })
        # Sort by span start, deduplicate overlapping by preferring earlier (higher-tier) entries
        matches.sort(key=lambda x: (x["span"][0], _tier_rank(x["tier"])))
        deduped: list[dict] = []
        last_end = -1
        for m in matches:
            if m["span"][0] >= last_end:
                deduped.append(m)
                last_end = m["span"][1]
        return deduped

    def has_match(self, text: str) -> bool:
        """Cheap boolean check — return True if any pattern matches."""
        for _, pat, _ in self._compiled:
            if pat.search(text):
                return True
        return False


def _tier_rank(tier: str) -> int:
    """Lower rank = higher priority for dedup."""
    if tier == "high":
        return 0
    if tier == "medium":
        return 1
    return 2  # domain:*
