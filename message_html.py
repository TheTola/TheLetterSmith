from __future__ import annotations

import re
from pathlib import Path

BODY_RE = re.compile(r"<body\b[^>]*>(.*?)</body>", re.IGNORECASE | re.DOTALL)
DOC_GUID_RE = re.compile(
    r"<a\b[^>]*name=(['\"])docs-internal-guid-[^'\"]+\1[^>]*>\s*</a>",
    re.IGNORECASE,
)
STYLE_ATTR_RE = re.compile(r"\sstyle=(['\"])(.*?)\1", re.IGNORECASE | re.DOTALL)
FONT_FAMILY_DECL_RE = re.compile(r"(font-family\s*:\s*)([^;]+)", re.IGNORECASE)
CSS_LENGTH_RE = re.compile(r"^(-?\d+(?:\.\d+)?)(pt|px|em|rem|%)$", re.IGNORECASE)
CSS_LINE_HEIGHT_RE = re.compile(r"^(-?\d+(?:\.\d+)?)%?$", re.IGNORECASE)
FRAGMENT_NOISE_TAG_RE = re.compile(
    r"</?(?:meta|style|link|title|html|head|body)\b[^>]*>",
    re.IGNORECASE,
)

GENERIC_FONT_FAMILIES = {
    "serif",
    "sans-serif",
    "monospace",
    "cursive",
    "fantasy",
    "system-ui",
    "ui-serif",
    "ui-sans-serif",
    "ui-monospace",
    "ui-rounded",
    "emoji",
    "math",
    "fangsong",
}

MOJIBAKE_MARKERS = (
    "â€™",
    "â€œ",
    "â€",
    "â€”",
    "â€“",
    "â€¦",
    "â€",
    "Â ",
    "Â ",
    "Ã",
)

TRANSPARENT_STYLE_VALUES = {
    "transparent",
    "#0000",
    "rgba(0,0,0,0)",
    "rgba(0, 0, 0, 0)",
}
NORMALIZED_TRANSPARENT_STYLE_VALUES = {
    entry.replace(" ", "") for entry in TRANSPARENT_STYLE_VALUES
}

DROP_STYLE_PROPS = {
    "margin-left",
    "margin-right",
    "text-indent",
}


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _format_decimal(value: float) -> str:
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return text or "0"


def _font_key(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().strip("\"'")).casefold()


def _split_css_font_family_list(value: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    quote = ""

    for ch in value or "":
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = ""
            continue

        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
            continue

        if ch == ",":
            part = "".join(buf).strip()
            if part:
                parts.append(part)
            buf = []
            continue

        buf.append(ch)

    part = "".join(buf).strip()
    if part:
        parts.append(part)
    return parts


def _unquote_css_font_family(value: str) -> str:
    text = (value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        text = text[1:-1]
    return re.sub(r"\s+", " ", text).strip()


def _mojibake_score(text: str) -> int:
    return sum(text.count(marker) for marker in MOJIBAKE_MARKERS)


def repair_common_mojibake(text: str) -> str:
    best = _normalize_newlines(text or "")
    best_score = _mojibake_score(best)

    for _ in range(2):
        improved = False
        for source_encoding in ("cp1252", "latin-1"):
            try:
                candidate = best.encode(source_encoding).decode("utf-8")
            except UnicodeError:
                continue

            candidate = _normalize_newlines(candidate)
            candidate_score = _mojibake_score(candidate)
            if candidate_score < best_score:
                best = candidate
                best_score = candidate_score
                improved = True
                break

        if not improved:
            break

    return best


def read_text_normalized(path: str | Path) -> str:
    p = Path(path)
    raw = p.read_bytes()

    text: str | None = None
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue

    if text is None:
        text = raw.decode("utf-8", errors="ignore")

    return repair_common_mojibake(text)


def normalize_message_document_html(raw: str) -> str:
    return repair_common_mojibake(raw or "").strip()


def _normalize_font_size(value: str) -> str:
    match = CSS_LENGTH_RE.fullmatch((value or "").strip())
    if not match:
        return value.strip()

    amount = float(match.group(1))
    unit = match.group(2).lower()
    if unit not in {"pt", "px"}:
        return value.strip()

    pixels = amount * (4.0 / 3.0) if unit == "pt" else amount
    rem = max(0.75, pixels / 16.0)
    return f"{_format_decimal(rem)}rem"


def _normalize_line_height(value: str) -> str:
    compact = re.sub(r"\s+", "", value or "")
    match = CSS_LINE_HEIGHT_RE.fullmatch(compact)
    if not match:
        return value.strip()

    amount = float(match.group(1))
    if compact.endswith("%"):
        amount /= 100.0

    return _format_decimal(max(1.35, min(1.72, amount)))


def _normalize_block_margin(value: str) -> str:
    match = CSS_LENGTH_RE.fullmatch((value or "").strip())
    if not match:
        return value.strip()

    amount = float(match.group(1))
    unit = match.group(2).lower()
    if unit not in {"pt", "px"}:
        return value.strip()

    pixels = amount * (4.0 / 3.0) if unit == "pt" else amount
    if abs(pixels) < 0.01:
        return "0"

    em = max(0.35, min(1.2, pixels / 24.0))
    return f"{_format_decimal(em)}em"


def _normalize_style_value(prop: str, value: str) -> str:
    compact = re.sub(r"\s+", " ", (value or "")).strip()
    if not compact:
        return ""
    if prop in DROP_STYLE_PROPS:
        return ""
    if prop == "background-color" and compact.casefold().replace(" ", "") in NORMALIZED_TRANSPARENT_STYLE_VALUES:
        return ""
    if prop == "font-size":
        return _normalize_font_size(compact)
    if prop == "line-height":
        return _normalize_line_height(compact)
    if prop in {"margin-top", "margin-bottom"}:
        return _normalize_block_margin(compact)
    return compact


def _clean_style_attribute(style_value: str) -> str:
    cleaned_parts: list[str] = []

    for raw_part in style_value.split(";"):
        part = raw_part.strip()
        if not part or ":" not in part:
            continue

        prop, value = part.split(":", 1)
        prop = prop.strip().lower()
        value = value.strip()

        if not prop or not value:
            continue
        if prop.startswith("-qt-"):
            continue
        value = _normalize_style_value(prop, value)
        if not value:
            continue

        cleaned_parts.append(f"{prop}:{value}")

    return "; ".join(cleaned_parts)


def _clean_style_attrs(html: str) -> str:
    def repl(match: re.Match[str]) -> str:
        cleaned = _clean_style_attribute(match.group(2))
        if not cleaned:
            return ""
        return f' style="{cleaned}"'

    return STYLE_ATTR_RE.sub(repl, html)


def extract_font_families(raw: str) -> list[str]:
    text = normalize_message_document_html(raw)
    if not text:
        return []

    seen: set[str] = set()
    families: list[str] = []

    for match in FONT_FAMILY_DECL_RE.finditer(text):
        for part in _split_css_font_family_list(match.group(2)):
            family = _unquote_css_font_family(part)
            key = _font_key(family)
            if not family or key in GENERIC_FONT_FAMILIES or key in seen:
                continue
            seen.add(key)
            families.append(family)

    return families


def rewrite_font_families(
    raw: str,
    family_aliases: dict[str, str],
    *,
    raw_css: bool = False,
    prepend: bool = True,
) -> str:
    if not raw or not family_aliases:
        return raw

    alias_by_key = {_font_key(name): alias for name, alias in family_aliases.items() if alias}
    if not alias_by_key:
        return raw

    def repl(match: re.Match[str]) -> str:
        prefix = match.group(1)
        value = match.group(2)
        rebuilt: list[str] = []
        inserted: set[str] = set()

        for part in _split_css_font_family_list(value):
            family = _unquote_css_font_family(part)
            alias = alias_by_key.get(_font_key(family))
            alias_value = alias if raw_css else (f"'{alias}'" if alias else "")
            if alias and alias not in inserted and prepend:
                rebuilt.append(alias_value)
                inserted.add(alias)
            rebuilt.append(part.strip())
            if alias and alias not in inserted and not prepend:
                rebuilt.append(alias_value)
                inserted.add(alias)

        return prefix + ", ".join(rebuilt)

    return FONT_FAMILY_DECL_RE.sub(repl, raw)


def normalize_message_fragment(raw: str) -> str:
    text = normalize_message_document_html(raw)
    if not text:
        return ""

    match = BODY_RE.search(text)
    fragment = match.group(1) if match else text
    fragment = DOC_GUID_RE.sub("", fragment)
    fragment = FRAGMENT_NOISE_TAG_RE.sub("", fragment)
    fragment = _clean_style_attrs(fragment)
    return fragment.strip()


LETTERSMITH_MESSAGE_MARKER = "<!-- lettersmith-message:v2 -->"
LETTERSMITH_MESSAGE_HINTS = (
    LETTERSMITH_MESSAGE_MARKER,
    'name="lettersmith-message"',
    "name='lettersmith-message'",
    'class="ls-linewrap"',
    "class='ls-linewrap'",
)


def is_lettersmith_message_html(raw: str, *, filename: str = "") -> bool:
    text = raw or ""
    lowered = text.casefold()
    if any(hint.casefold() in lowered for hint in LETTERSMITH_MESSAGE_HINTS):
        return True

    # Compatibility with messages saved by earlier Letter Smith editor builds.
    # Those files are normally named message.html and contain Qt rich-text metadata.
    if Path(filename).name.casefold() == "message.html":
        return (
            'name="qrichtext"' in lowered
            or "name='qrichtext'" in lowered
            or "-qt-" in lowered
        )
    return False


def mark_lettersmith_message_html(raw: str) -> str:
    text = raw or ""
    if LETTERSMITH_MESSAGE_MARKER in text:
        return text

    lower = text.casefold()
    html_pos = lower.find("<html")
    if html_pos >= 0:
        tag_end = text.find(">", html_pos)
        if tag_end >= 0:
            return text[: tag_end + 1] + "\n" + LETTERSMITH_MESSAGE_MARKER + text[tag_end + 1 :]
    return LETTERSMITH_MESSAGE_MARKER + "\n" + text
