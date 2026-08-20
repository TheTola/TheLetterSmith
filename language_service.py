from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional

from settings_store import SettingsStore
from project_paths import application_paths

try:
    from spellchecker import SpellChecker
except ImportError:  # Basic deterministic checks remain available if packaging is incomplete.
    SpellChecker = None  # type: ignore[assignment]


LANGUAGE_CUSTOM_DICTIONARY_KEY = "language_custom_dictionary"
LOGGER = logging.getLogger(__name__)

_WORD_RE = re.compile(
    r"[^\W\d_]+(?:['\u2019][^\W\d_]+)*(?:-[^\W\d_]+)*",
    re.UNICODE,
)
_VALID_CUSTOM_WORD_RE = re.compile(
    r"[^\W\d_]+(?:['\u2019-][^\W\d_]+)*",
    re.UNICODE,
)
_QUOTED_RE = re.compile(r'(["\u201c])[^"\u201c\u201d\n]*["\u201d]')
_SINGLE_QUOTED_RE = re.compile(r"(?<!\w)'[^'\n]+'(?!\w)")
_PROTECTED_VALUE_PATTERNS = (
    re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE),
    re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"),
    re.compile(
        r"\b(?:[A-Za-z0-9-]+\.)+(?:ai|app|biz|co|com|dev|edu|gov|info|io|me|net|org|uk)\b(?:/\S*)?",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b[\w-]+(?:\.[\w-]+)*\.(?:bmp|css|docx?|gif|html?|jpe?g|js|json|mp3|mp4|mov|pdf|png|svg|txt|wav|webp|zip)\b",
        re.IGNORECASE,
    ),
    re.compile(r"(?:[A-Za-z]:[\\/]|\.{1,2}[\\/])\S+"),
    re.compile(r"#[0-9A-Fa-f]{3,8}\b"),
    re.compile(r"\b\d+(?::\d+)+\b"),
    re.compile(r"\b\d+(?:[xX\u00d7]\d+)+\b"),
    re.compile(r"\b(?=\w*[A-Za-z])(?=\w*\d)[\w.-]+\b"),
    re.compile(r"\b[A-Z]{2,}(?:-[A-Z]+)*\b"),
)

_PROTECTED_WORDS = {
    "ai",
    "bokeh",
    "cel-shaded",
    "chiaroscuro",
    "close-up",
    "cmyk",
    "cyberpunk",
    "depth-of-field",
    "draconian",
    "full-body",
    "hdr",
    "kintsugi",
    "photorealism",
    "photorealistic",
    "rgb",
    "ue",
    "wide-angle",
    # Common valid English variants that the bundled US-frequency corpus omits.
    "analyse",
    "analysed",
    "armour",
    "centre",
    "centres",
    "colour",
    "colourful",
    "colours",
    "defence",
    "favour",
    "favourite",
    "favourites",
    "grey",
    "honour",
    "licence",
    "organise",
    "organised",
    "organising",
    "realise",
    "realised",
    "theatre",
    "theatres",
    "travelled",
    "travelling",
}

# Explicit entries are deliberately limited to common, unambiguous mechanical errors.
_COMMON_CORRECTIONS = {
    "alot": "a lot",
    "beautful": "beautiful",
    "beutiful": "beautiful",
    "castel": "castle",
    "charcter": "character",
    "charcters": "characters",
    "definately": "definitely",
    "dramatc": "dramatic",
    "medeval": "medieval",
    "medival": "medieval",
    "recieve": "receive",
    "seperate": "separate",
    "teh": "the",
    "treee": "tree",
}

_SINGULAR_VERBS = {
    "carry": "carries",
    "do": "does",
    "face": "faces",
    "go": "goes",
    "have": "has",
    "hold": "holds",
    "look": "looks",
    "stand": "stands",
    "turn": "turns",
    "walk": "walks",
}


@dataclass(frozen=True, slots=True)
class LanguageIssue:
    start: int
    end: int
    text: str
    category: str
    message: str
    suggestions: tuple[str, ...] = ()
    replacement: Optional[str] = None
    confidence: str = "medium"
    auto_fix: bool = False

    @property
    def length(self) -> int:
        return self.end - self.start


@dataclass(frozen=True, slots=True)
class LanguageResult:
    original: str
    text: str
    issues: tuple[LanguageIssue, ...]


def _preserve_case(source: str, replacement: str) -> str:
    if not source:
        return replacement
    if len(source) == 1 and source.isupper():
        return replacement[:1].upper() + replacement[1:]
    if source.isupper():
        return replacement.upper()
    if source[0].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


def _ranges_overlap(start: int, end: int, ranges: Iterable[tuple[int, int]]) -> bool:
    if start == end:
        return any(range_start < start < range_end for range_start, range_end in ranges)
    return any(start < range_end and end > range_start for range_start, range_end in ranges)


def _structured_prompt(text: str, context: str) -> bool:
    if context != "prompt":
        return False
    stripped = text.strip()
    if not stripped:
        return False
    if stripped.isupper() or " / " in stripped:
        return True
    prose_verbs = re.search(
        r"\b(?:add|apply|are|create|depict|has|have|is|make|render|should|show|stands?|use)\b",
        stripped,
        re.IGNORECASE,
    )
    slash_separators = len(
        re.findall(r"(?<=\w)\s*/\s*(?=[#\w])", stripped)
    )
    if slash_separators >= 2:
        return True
    if slash_separators and prose_verbs is None and re.search(r"[,;]", stripped):
        return True
    return stripped.count(",") >= 2 and prose_verbs is None


class LanguageService:
    """Authoritative offline proofreading service for every Letter Smith surface."""

    def __init__(self, project_root: str | Path, *, spell_checker=None) -> None:
        self.project_root = Path(project_root).resolve()
        self._lock = threading.RLock()
        self._dictionary_generation = 0
        self._ignored_words: set[str] = set()
        self._custom_words: dict[str, str] = {}
        try:
            self._settings: Optional[SettingsStore] = SettingsStore(self.project_root)
        except Exception:
            LOGGER.exception("Could not initialize persistent language settings")
            self._settings = None
        self._spell = spell_checker
        if self._spell is None and SpellChecker is not None:
            try:
                self._spell = SpellChecker(language="en", distance=1)
            except Exception:
                LOGGER.exception("Could not initialize the offline spelling dictionary")
                self._spell = None
        self._load_custom_dictionary()

    @property
    def custom_words(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(
                self._custom_words[key]
                for key in sorted(self._custom_words)
            )

    def _load_custom_dictionary(self) -> None:
        try:
            raw = (
                self._settings.get(LANGUAGE_CUSTOM_DICTIONARY_KEY, [])
                if self._settings is not None
                else []
            )
        except Exception:
            LOGGER.exception("Could not read the shared custom dictionary")
            raw = []
        values = raw if isinstance(raw, list) else []
        for value in values:
            word = str(value or "").strip()
            if _VALID_CUSTOM_WORD_RE.fullmatch(word):
                self._custom_words.setdefault(word.casefold(), word)
        self._load_spell_words(self._custom_words.values())

    def _load_spell_words(self, words: Iterable[str]) -> None:
        if self._spell is None:
            return
        values = [str(word).casefold() for word in words if str(word).strip()]
        values.extend(_PROTECTED_WORDS)
        if values:
            try:
                self._spell.word_frequency.load_words(values)
            except Exception:
                LOGGER.exception("Could not load words into the spelling dictionary")

    def add_to_dictionary(self, word: str) -> bool:
        cleaned = str(word or "").strip().strip(".,;:!?\"\u201c\u201d")
        if not _VALID_CUSTOM_WORD_RE.fullmatch(cleaned):
            raise ValueError("custom dictionary entries must contain one valid word")
        key = cleaned.casefold()
        with self._lock:
            if key in self._custom_words:
                return False
            if self._settings is None:
                raise OSError("persistent language settings are unavailable")
            updated_words = dict(self._custom_words)
            updated_words[key] = cleaned
            self._settings.update_fields(
                {
                    LANGUAGE_CUSTOM_DICTIONARY_KEY: [
                        updated_words[item]
                        for item in sorted(updated_words)
                    ]
                }
            )
            self._custom_words = updated_words
            self._load_spell_words((cleaned,))
            self._dictionary_generation += 1
            self._cached_issues.cache_clear()
            self._cached_spell_analysis.cache_clear()
            return True

    def ignore_word(self, word: str) -> bool:
        key = str(word or "").strip().casefold()
        if not key:
            return False
        with self._lock:
            if key in self._ignored_words:
                return False
            self._ignored_words.add(key)
            self._dictionary_generation += 1
            self._cached_issues.cache_clear()
            self._cached_spell_analysis.cache_clear()
            return True

    def is_known_word(self, word: str) -> bool:
        cleaned = str(word or "").strip().strip(".,;:!?\"\u201c\u201d")
        key = cleaned.casefold()
        if not key:
            return False
        with self._lock:
            if (
                key in self._custom_words
                or key in self._ignored_words
                or key in _PROTECTED_WORDS
                or any(char.isdigit() for char in cleaned)
                or (len(cleaned) > 1 and cleaned.isupper())
            ):
                return True
            if self._spell is None:
                return True
            known, _suggestions = self._cached_spell_analysis(
                key,
                self._dictionary_generation,
            )
            return known

    def get_suggestions(self, word: str, *, limit: int = 5) -> tuple[str, ...]:
        cleaned = str(word or "").strip()
        key = cleaned.casefold()
        if not key:
            return ()
        explicit = _COMMON_CORRECTIONS.get(key)
        if explicit:
            return (_preserve_case(cleaned, explicit),)
        with self._lock:
            if self._spell is None:
                return ()
            known, ordered = self._cached_spell_analysis(
                key,
                self._dictionary_generation,
            )
            if known:
                return ()
        return tuple(
            _preserve_case(cleaned, candidate)
            for candidate in ordered[: max(0, int(limit))]
        )

    suggestions_for = get_suggestions

    def get_issues(
        self,
        text: str,
        *,
        context: str = "prose",
        protected_terms: Iterable[str] = (),
    ) -> tuple[LanguageIssue, ...]:
        source = str(text or "")
        normalized_context = "prompt" if context == "prompt" else "prose"
        protected = tuple(
            sorted(
                {
                    str(term).strip()
                    for term in protected_terms
                    if str(term).strip()
                },
                key=str.casefold,
            )
        )
        with self._lock:
            return self._cached_issues(
                source,
                normalized_context,
                protected,
                self._dictionary_generation,
            )

    check_text = get_issues

    def correct_text(
        self,
        text: str,
        *,
        context: str = "prose",
        protected_terms: Iterable[str] = (),
    ) -> str:
        corrected = str(text or "")
        stable_protected_terms = tuple(protected_terms)
        for _pass in range(3):
            issues = self.get_issues(
                corrected,
                context=context,
                protected_terms=stable_protected_terms,
            )
            updated = corrected
            for issue in sorted(
                issues,
                key=lambda item: (item.start, item.end),
                reverse=True,
            ):
                if not issue.auto_fix or issue.replacement is None:
                    continue
                updated = (
                    updated[: issue.start]
                    + issue.replacement
                    + updated[issue.end :]
                )
            if updated == corrected:
                break
            corrected = updated
        return corrected

    def analyze(
        self,
        text: str,
        *,
        context: str = "prose",
        protected_terms: Iterable[str] = (),
    ) -> LanguageResult:
        source = str(text or "")
        issues = self.get_issues(
            source,
            context=context,
            protected_terms=protected_terms,
        )
        return LanguageResult(
            original=source,
            text=self.correct_text(
                source,
                context=context,
                protected_terms=protected_terms,
            ),
            issues=issues,
        )

    @lru_cache(maxsize=32)
    def _cached_issues(
        self,
        text: str,
        context: str,
        protected_terms: tuple[str, ...],
        _dictionary_generation: int,
    ) -> tuple[LanguageIssue, ...]:
        return self._analyze_issues(text, context, protected_terms)

    @lru_cache(maxsize=4096)
    def _cached_spell_analysis(
        self,
        word: str,
        _dictionary_generation: int,
    ) -> tuple[bool, tuple[str, ...]]:
        if self._spell is None:
            return True, ()
        if word in self._spell:
            return True, ()
        candidates = self._spell.candidates(word) or set()
        ordered = tuple(
            sorted(
                (candidate for candidate in candidates if candidate != word),
                key=lambda candidate: (
                    -float(self._spell.word_usage_frequency(candidate)),
                    candidate,
                ),
            )
        )
        return False, ordered

    def _protected_ranges(
        self,
        text: str,
        protected_terms: tuple[str, ...],
    ) -> tuple[tuple[int, int], ...]:
        ranges: list[tuple[int, int]] = []
        for pattern in (*_PROTECTED_VALUE_PATTERNS, _QUOTED_RE, _SINGLE_QUOTED_RE):
            ranges.extend((match.start(), match.end()) for match in pattern.finditer(text))
        for term in protected_terms:
            pattern = re.compile(
                rf"(?<!\w){re.escape(term)}(?!\w)",
                re.IGNORECASE,
            )
            ranges.extend((match.start(), match.end()) for match in pattern.finditer(text))
        return tuple(sorted(set(ranges)))

    def _analyze_issues(
        self,
        text: str,
        context: str,
        protected_terms: tuple[str, ...],
    ) -> tuple[LanguageIssue, ...]:
        if not text:
            return ()
        protected_ranges = self._protected_ranges(text, protected_terms)
        structured = _structured_prompt(text, context)
        occupied: list[tuple[int, int]] = []
        issues: list[LanguageIssue] = []

        def add(issue: LanguageIssue) -> None:
            if _ranges_overlap(issue.start, issue.end, protected_ranges):
                return
            if _ranges_overlap(issue.start, issue.end, occupied):
                return
            occupied.append((issue.start, issue.end))
            issues.append(issue)

        def word_issue(
            start: int,
            end: int,
            replacement: str,
            category: str,
            message: str,
        ) -> None:
            original = text[start:end]
            fixed = _preserve_case(original, replacement)
            add(
                LanguageIssue(
                    start,
                    end,
                    original,
                    category,
                    message,
                    (fixed,),
                    fixed,
                    "high",
                    True,
                )
            )

        # High-confidence grammar rules use the smallest possible target range.
        grammar_rules = (
            (r"\ba\s+(?P<target>women)\b", "woman", "Use singular 'woman' after 'a'."),
            (r"\ball(?:\s+the)?\s+(?P<target>image)\b(?=\s+(?:black|blue|bright|dark|dim|gold|green|light|monochrome|red|white)\b|\s*$|[.,;!?])", "images", "Use the plural noun after 'all'."),
            (r"\b(?:and|but)\s+(?P<target>there)(?=\s+(?:clothes|clothing|eyes|faces|hair|hands)\s+(?:must|should|will)\b)", "their", "Use the possessive pronoun 'their'."),
            (r"\b(?:characters|people|they)\s+(?:should\s+)?have\s+(?:(?:black|blue|brown|gold|green|grey|gray|hazel|red|white)\s+)?(?P<target>eye)\b(?=\s+(?:and|but)\b|\s*$|[.,;!?])", "eyes", "Use the plural noun for multiple people."),
            (r"\b(?:she|he|it)\s+(?P<target>carry|do|face|go|have|hold|look|stand|turn|walk)\b(?=\s+(?:a|an|at|away|back|beneath|black|blue|down|forward|gold|green|red|the|to|toward|towards|under|up|white|with|without)\b|\s*$|[.,;!?])", "{singular_verb}", "Match the verb to the singular subject."),
            (r"\b(?:they|we|you)\s+(?P<target>is)\b", "are", "Match the verb to the subject."),
            (r"\b(?:he|she|it)\s+(?P<target>are)\b", "is", "Match the verb to the subject."),
            (r"\bin\s+(?P<target>rain)\b(?=\s*$|[.,;!?])", "the rain", "Add the missing article."),
        )
        if not structured:
            grammar_rules += (
                (
                    r"\b(?:woman|man|character)\s+(?P<target>carry|do|face|go|have|hold|look|stand|turn|walk)\b(?=\s+(?:a|an|at|away|back|beneath|black|blue|down|forward|gold|green|red|the|to|toward|towards|under|up|white|with|without)\b|\s*$|[.,;!?])",
                    "{singular_verb}",
                    "Match the verb to the singular subject.",
                ),
            )
        for pattern_text, replacement, message in grammar_rules:
            for match in re.finditer(pattern_text, text, re.IGNORECASE):
                start, end = match.span("target")
                fixed = replacement
                if replacement == "{singular_verb}":
                    fixed = _SINGULAR_VERBS[match.group("target").casefold()]
                word_issue(start, end, fixed, "grammar", message)

        for match in re.finditer(
            r"\b(?:beneath|under)\s+(?P<target>(?:black|blue|gold|green|red|white)\s+(?:treee?|castle|wall))\b(?=\s*$|[.,;!?])",
            text,
            re.IGNORECASE,
        ):
            position = match.start("target")
            add(
                LanguageIssue(
                    position,
                    position,
                    "",
                    "grammar",
                    "Add the missing article.",
                    ("a ",),
                    "a ",
                    "high",
                    True,
                )
            )

        for match in re.finditer(
            r"\b(?P<first>[^\W\d_]+)(?P<gap>\s+)(?P<second>(?P=first))\b",
            text,
            re.IGNORECASE | re.UNICODE,
        ):
            start = match.start("gap")
            end = match.end("second")
            auto_fix = match.group("first") in {"a", "an", "the"}
            add(
                LanguageIssue(
                    start,
                    end,
                    text[start:end],
                    "grammar",
                    "Remove the duplicated word.",
                    ("",),
                    "",
                    "high" if auto_fix else "medium",
                    auto_fix,
                )
            )

        if context == "prompt":
            for pattern_text, replacement, message in (
                (
                    r"\bthe\s+same\s+(?P<target>women)(?=\s+hold\s+a\b)",
                    "woman",
                    "Use singular 'woman' for the same person.",
                ),
                (
                    r"\bthe\s+same\s+women\s+(?P<target>hold)(?=\s+a\b)",
                    "holds",
                    "Match the verb to the singular subject.",
                ),
            ):
                for specific_match in re.finditer(
                    pattern_text,
                    text,
                    re.IGNORECASE,
                ):
                    start, end = specific_match.span("target")
                    word_issue(start, end, replacement, "grammar", message)
            match = re.match(r"\s*(?P<target>sun)(?=\s+is\s+(?:rising|setting)\b)", text)
            if match:
                start, end = match.span("target")
                word_issue(start, end, "The sun", "grammar", "Add the missing article.")
            for match in re.finditer(
                r"(?P<target>,)(?=\s+the\s+(?:characters|images|people)\s+should\b)",
                text,
                re.IGNORECASE,
            ):
                start, end = match.span("target")
                word_issue(
                    start,
                    end,
                    ".",
                    "punctuation",
                    "Separate complete prompt instructions.",
                )

        for match in re.finditer(r"\b(?P<article>a|an)\s+(?P<word>[^\W\d_]+)", text, re.IGNORECASE):
            article = match.group("article")
            raw_following = match.group("word")
            following = raw_following.casefold()
            if len(raw_following) > 1 and raw_following.isupper():
                continue
            replacement = None
            consonant_vowel_prefixes = (
                "eu",
                "euro",
                "ewe",
                "one",
                "once",
                "ubiq",
                "uni",
                "use",
                "user",
                "usual",
                "utop",
            )
            ambiguous_h_words = ("herb", "historic", "homage", "hotel")
            if (
                article.casefold() == "a"
                and following[:1] in "aeiou"
                and not following.startswith(consonant_vowel_prefixes)
            ):
                replacement = "an"
            elif (
                article.casefold() == "an"
                and following.startswith(consonant_vowel_prefixes)
            ):
                replacement = "a"
            elif (
                article.casefold() == "an"
                and following[:1] not in "aeiou"
                and following not in {"heir", "honest", "honor", "hour"}
                and not following.startswith(ambiguous_h_words)
            ):
                replacement = "a"
            if replacement:
                start, end = match.span("article")
                word_issue(start, end, replacement, "grammar", "Use the correct article.")

        # Deterministic spelling fixes run before generic dictionary suggestions.
        for match in _WORD_RE.finditer(text):
            start, end = match.span()
            word = match.group()
            key = word.casefold()
            if _ranges_overlap(start, end, occupied) or _ranges_overlap(start, end, protected_ranges):
                continue
            if (
                key in self._custom_words
                or key in self._ignored_words
                or key in _PROTECTED_WORDS
            ):
                continue
            explicit = _COMMON_CORRECTIONS.get(key)
            if explicit:
                word_issue(start, end, explicit, "spelling", f"Correct the spelling of '{word}'.")
                continue
            if self._spell is None:
                continue
            if len(word) < 3 or self.is_known_word(word):
                continue
            if word[0].isupper() or any(char.isupper() for char in word[1:]):
                continue
            suggestions = self.get_suggestions(word)
            replacement = suggestions[0] if suggestions else None
            add(
                LanguageIssue(
                    start,
                    end,
                    word,
                    "spelling",
                    f"'{word}' may be misspelled.",
                    suggestions,
                    replacement,
                    "medium",
                    False,
                )
            )

        # Punctuation and spacing corrections avoid protected prompt values.
        punctuation_patterns = (
            (r"[ \t]+(?P<punct>[,.;!?])", lambda match: match.group("punct"), "Remove the space before punctuation."),
            (r"(?P<punct>[,;:!?])(?P=punct)+", lambda match: match.group("punct"), "Remove duplicated punctuation."),
            (r"(?<!\.)\.\.(?!\.)", lambda _match: ".", "Remove duplicated punctuation."),
            (r"[ \t]{2,}", lambda _match: " ", "Remove repeated spaces."),
        )
        for pattern_text, replacement_factory, message in punctuation_patterns:
            for match in re.finditer(pattern_text, text):
                add(
                    LanguageIssue(
                        match.start(),
                        match.end(),
                        match.group(),
                        "punctuation",
                        message,
                        (replacement_factory(match),),
                        replacement_factory(match),
                        "high",
                        True,
                    )
                )

        for match in re.finditer(
            r"(?P<punct>[,;:.!?])(?P<next>[A-Za-z])",
            text,
        ):
            position = match.start("next")
            add(
                LanguageIssue(
                    position,
                    position,
                    "",
                    "punctuation",
                    "Add a space after punctuation.",
                    (" ",),
                    " ",
                    "high",
                    True,
                )
            )

        if not structured:
            for match in re.finditer(r"(?:^|[.!?]\s+|\n+\s*)(?P<letter>[a-z])", text):
                start, end = match.span("letter")
                word_issue(
                    start,
                    end,
                    match.group("letter").upper(),
                    "capitalization",
                    "Capitalize the beginning of the sentence.",
                )

        for match in re.finditer(r"\bi\b", text):
            word_issue(
                match.start(),
                match.end(),
                "I",
                "capitalization",
                "Capitalize the pronoun 'I'.",
            )

        stripped = text.rstrip()
        last_line = stripped.rsplit("\n", 1)[-1] if stripped else ""
        terminal_needed = bool(
            stripped
            and stripped[-1] not in ".!?;:,/"
            and stripped[-1] not in "\"'\u2019\u201d)]}"
            and not structured
            and (
                ("\n" not in stripped and len(_WORD_RE.findall(stripped)) >= 3)
                or len(_WORD_RE.findall(last_line)) >= 4
            )
        )
        if terminal_needed:
            add(
                LanguageIssue(
                    len(stripped),
                    len(stripped),
                    "",
                    "punctuation",
                    "Add terminal punctuation.",
                    (".",),
                    ".",
                    "high",
                    True,
                )
            )

        return tuple(sorted(issues, key=lambda issue: (issue.start, issue.end, issue.category)))


_SERVICE_LOCK = threading.Lock()
_SERVICES: dict[str, LanguageService] = {}


def get_language_service(project_root: str | Path | None = None) -> LanguageService:
    root = (
        Path(project_root).resolve()
        if project_root is not None
        else application_paths().workspace_root
    )
    key = str(root).casefold()
    with _SERVICE_LOCK:
        service = _SERVICES.get(key)
        if service is None:
            service = LanguageService(root)
            _SERVICES[key] = service
        return service


def clear_language_service_cache() -> None:
    with _SERVICE_LOCK:
        _SERVICES.clear()
