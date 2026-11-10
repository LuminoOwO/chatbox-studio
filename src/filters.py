"""
Text filters applied to the English text before it goes to the VRChat chatbox.

  * Filler words  - drops "um", "uh", "äh", ... that Whisper sometimes writes out
  * Replacements  - find -> replace, plain text or regex, whole word or anywhere
  * Block rules   - if a sentence contains one of these, it is not sent at all
  * Min words     - ignore very short sentences ("Okay.", "Hm.")
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

FILLERS = ["um", "umm", "uh", "uhh", "uhm", "erm", "er", "hmm", "hm", "mhm",
           "äh", "ähm", "öhm", "ehm"]
_FILLER_RE = re.compile(
    r"(?<!\w)(?:" + "|".join(FILLERS) + r")(?!\w)[,.…]*\s*", re.IGNORECASE)


@dataclass
class FilterOutcome:
    text: str
    blocked: bool = False
    reason: str = ""
    changed: bool = False


@dataclass
class FilterSet:
    enabled: bool = True
    remove_fillers: bool = True
    min_words: int = 0
    replace: list[tuple[re.Pattern, str, bool]] = field(default_factory=list)  # (pattern, repl, is_regex)
    block: list[tuple[re.Pattern, str]] = field(default_factory=list)          # (pattern, label)
    errors: list[str] = field(default_factory=list)

    @staticmethod
    def compile_rule(text: str, whole_word: bool, regex: bool) -> re.Pattern:
        if regex:
            re.compile(text)          # report errors against the pattern the user typed
        body = text if regex else re.escape(text.strip())
        if whole_word:
            body = rf"(?<!\w)(?:{body})(?!\w)"
        return re.compile(body, re.IGNORECASE)

    @classmethod
    def from_settings(cls, s) -> "FilterSet":
        fs = cls(enabled=s.filters_enabled, remove_fillers=s.remove_fillers,
                 min_words=int(s.min_words))
        for i, r in enumerate(s.replace_rules, 1):
            if not r.get("enabled", True) or not r.get("find", "").strip():
                continue
            try:
                pat = cls.compile_rule(r["find"], r.get("whole_word", True), r.get("regex", False))
                fs.replace.append((pat, r.get("replace", ""), r.get("regex", False)))
            except re.error as e:
                fs.errors.append(f"Replace rule {i} ({r['find']!r}): {e}")
        for i, r in enumerate(s.block_rules, 1):
            if not r.get("enabled", True) or not r.get("pattern", "").strip():
                continue
            try:
                pat = cls.compile_rule(r["pattern"], r.get("whole_word", True), r.get("regex", False))
                fs.block.append((pat, r["pattern"]))
            except re.error as e:
                fs.errors.append(f"Block rule {i} ({r['pattern']!r}): {e}")
        return fs

    @staticmethod
    def _keep_case(repl: str):
        def sub(m: re.Match) -> str:
            out = repl
            found = m.group(0)
            if found.isupper() and len(found) > 1:
                return out.upper()
            if found[:1].isupper() and out[:1].islower():
                return out[:1].upper() + out[1:]
            return out
        return sub

    def apply(self, text: str) -> FilterOutcome:
        if not self.enabled:
            return FilterOutcome(text)
        original = text

        for pat, label in self.block:
            if pat.search(text):
                return FilterOutcome(text, True, f"contains “{label}”")

        if self.remove_fillers:
            text = _FILLER_RE.sub("", text)

        for pat, repl, is_regex in self.replace:
            text = pat.sub(self._keep_case(repl) if not is_regex else repl, text)

        # tidy up spacing/punctuation left behind by removals
        text = re.sub(r"\s+([,.!?;:])", r"\1", text)
        text = re.sub(r"([,;:])(?:\s*[,;:])+", r"\1", text)
        text = re.sub(r"\s{2,}", " ", text).strip(" ,;:")
        if text[:1].islower() and original[:1].isupper():
            text = text[:1].upper() + text[1:]

        # a replacement could have produced a blocked word
        for pat, label in self.block:
            if pat.search(text):
                return FilterOutcome(text, True, f"contains “{label}”")

        if not any(ch.isalnum() for ch in text):
            return FilterOutcome(text, True, "nothing left after filtering")
        if self.min_words and len(re.findall(r"\w+", text)) < self.min_words:
            return FilterOutcome(text, True, f"shorter than {self.min_words} words")
        return FilterOutcome(text, changed=(text != original))
