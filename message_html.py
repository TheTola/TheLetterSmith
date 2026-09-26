from __future__ import annotations

import base64
import html as _html
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Mapping
from urllib.parse import quote, unquote, urlsplit

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

ULTRALINK_SCHEME = "ultralink:"
LEGACY_ULTRALINK_SCHEME = "hypernote:"
ULTRALINK_SCHEMES = (ULTRALINK_SCHEME, LEGACY_ULTRALINK_SCHEME)

DROP_STYLE_PROPS = {
    "margin-left",
    "margin-right",
    "text-indent",
}

_PASSIVE_TAGS = frozenset(
    {
        "a",
        "b",
        "blockquote",
        "br",
        "code",
        "del",
        "div",
        "em",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "hr",
        "i",
        "img",
        "ins",
        "li",
        "ol",
        "p",
        "pre",
        "s",
        "small",
        "span",
        "strike",
        "strong",
        "sub",
        "sup",
        "table",
        "tbody",
        "td",
        "tfoot",
        "th",
        "thead",
        "tr",
        "u",
        "ul",
    }
)
_VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)
_BLOCKED_SUBTREES = frozenset(
    {
        "audio",
        "button",
        "canvas",
        "embed",
        "form",
        "head",
        "iframe",
        "math",
        "noscript",
        "object",
        "option",
        "script",
        "select",
        "style",
        "svg",
        "template",
        "textarea",
        "video",
    }
)
_DOCUMENT_WRAPPERS = frozenset({"body", "html"})
_SAFE_CLASSES = frozenset(
    {
        "checked",
        "lettersmith-defaults",
        "ls-linewrap",
        "unchecked",
    }
)
_GLOBAL_ATTRIBUTES = frozenset({"align", "class", "dir", "lang", "style", "title"})
_TAG_ATTRIBUTES = {
    "a": frozenset({"href"}),
    "img": frozenset({"alt", "height", "src", "width"}),
    "li": frozenset({"value"}),
    "ol": frozenset({"start", "type"}),
    "table": frozenset({"border", "cellpadding", "cellspacing", "width"}),
    "td": frozenset({"colspan", "height", "rowspan", "valign", "width"}),
    "th": frozenset({"colspan", "height", "rowspan", "scope", "valign", "width"}),
}
_ANCHOR_INLINE_TAGS = frozenset(
    {
        "b",
        "code",
        "del",
        "em",
        "i",
        "ins",
        "s",
        "small",
        "span",
        "strike",
        "strong",
        "sub",
        "sup",
        "u",
    }
)
_SAFE_STYLE_PROPERTIES = frozenset(
    {
        "-qt-block-indent",
        "-qt-list-indent",
        "-qt-list-number-prefix",
        "-qt-list-number-suffix",
        "background-color",
        "border",
        "border-bottom",
        "border-collapse",
        "border-color",
        "border-left",
        "border-right",
        "border-spacing",
        "border-style",
        "border-top",
        "border-width",
        "color",
        "direction",
        "font-family",
        "font-size",
        "font-stretch",
        "font-style",
        "font-variant",
        "font-weight",
        "height",
        "letter-spacing",
        "line-height",
        "list-style-position",
        "list-style-type",
        "margin",
        "margin-bottom",
        "margin-left",
        "margin-right",
        "margin-top",
        "max-height",
        "max-width",
        "min-height",
        "min-width",
        "overflow-wrap",
        "padding",
        "padding-bottom",
        "padding-left",
        "padding-right",
        "padding-top",
        "text-align",
        "text-decoration",
        "text-indent",
        "text-transform",
        "vertical-align",
        "white-space",
        "width",
        "word-break",
        "word-spacing",
    }
)
_ANCHOR_TEXT_STYLE_PROPERTIES = frozenset(
    {
        "color",
        "direction",
        "font-family",
        "font-size",
        "font-style",
        "font-variant",
        "font-weight",
        "letter-spacing",
        "line-height",
        "text-decoration",
        "text-transform",
        "vertical-align",
        "white-space",
        "word-spacing",
    }
)
_UNSAFE_STYLE_VALUE = re.compile(
    r"(?i)(?:url|expression|var|env|attr)\s*\(|@import|javascript:|vbscript:|"
    r"data:|file:|-moz-binding|\bbehavior\b"
)
_LANGUAGE_VALUE = re.compile(r"[A-Za-z0-9-]{1,35}")
_NUMBER_VALUE = re.compile(r"\d{1,5}(?:\.\d{1,3})?%?")
_RASTER_DATA_URI = re.compile(
    r"data:image/(?:png|jpe?g|gif|webp);base64,[A-Za-z0-9+/=\r\n]+",
    re.IGNORECASE,
)
_RASTER_SUFFIXES = frozenset({".bmp", ".gif", ".jpeg", ".jpg", ".png", ".webp"})
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]+")
MAX_MESSAGE_HTML_INPUT_CHARACTERS = 16 * 1024 * 1024
MAX_MESSAGE_HTML_OUTPUT_CHARACTERS = 16 * 1024 * 1024
MAX_MESSAGE_HTML_TAGS = 200_000
MAX_MESSAGE_HTML_NESTING = 1_024
_FALLBACK_INPUT_CHARACTERS = 1_000_000
_SANITIZER_CHUNK_CHARACTERS = 64 * 1024
_QT_DEFAULTS_STYLE = (
    '<style type="text/css">'
    ".lettersmith-defaults p,.lettersmith-defaults li{white-space:pre-wrap}"
    ".lettersmith-defaults hr{height:1px;border-width:0}"
    '.lettersmith-defaults li.unchecked::marker{content:"\\2610"}'
    '.lettersmith-defaults li.checked::marker{content:"\\2612"}'
    "</style>"
)

_SUPPORTED_STYLE_STATE_VERSIONS = (1, 2)
_STYLE_STATE_COMMENT_RE = re.compile(
    r"<!--\s*lettersmith-style-state:[\s\S]*?-->",
    re.IGNORECASE,
)
_STYLE_STATE_DATA_RE = re.compile(
    r"lettersmith-style-state:v([1-9][0-9]{0,5}):([A-Za-z0-9+/]+={0,2})\Z"
)
MAX_STYLE_STATE_BYTES = 1024 * 1024
_MAX_STYLE_STATE_ENCODED_CHARACTERS = ((MAX_STYLE_STATE_BYTES + 2) // 3) * 4


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate style-state key")
        result[key] = value
    return result


def _reject_nonfinite_json(_value: str) -> object:
    raise ValueError("Non-finite style-state value")


def _decode_style_state_comment(
    data: str,
) -> tuple[int, dict[str, object], str] | None:
    match = _STYLE_STATE_DATA_RE.fullmatch(data.strip())
    if match is None:
        return None
    version = int(match.group(1))
    encoded = match.group(2)
    if len(encoded) > _MAX_STYLE_STATE_ENCODED_CHARACTERS:
        return None
    try:
        payload = base64.b64decode(encoded, validate=True)
        if (
            len(payload) > MAX_STYLE_STATE_BYTES
            or base64.b64encode(payload).decode("ascii") != encoded
        ):
            return None
        state = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_nonfinite_json,
        )
    except (UnicodeError, ValueError, RecursionError):
        return None
    if (
        not isinstance(state, dict)
        or type(state.get("schema_version")) is not int
        or state["schema_version"] != version
    ):
        return None
    return version, state, encoded


def _encode_style_state_comment(state: Mapping[str, object]) -> str:
    if not isinstance(state, Mapping) or type(state.get("schema_version")) is not int:
        raise ValueError("Invalid LetterSmith style state")
    if state["schema_version"] not in _SUPPORTED_STYLE_STATE_VERSIONS:
        raise ValueError("Unsupported LetterSmith style-state version")
    try:
        payload = json.dumps(
            state,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as error:
        raise ValueError("Invalid LetterSmith style state") from error
    if len(payload) > MAX_STYLE_STATE_BYTES:
        raise ValueError("LetterSmith style state is too large")
    encoded = base64.b64encode(payload).decode("ascii")
    return f"<!-- lettersmith-style-state:v{state['schema_version']}:{encoded} -->"


def strip_lettersmith_style_state(raw: str) -> str:
    """Remove embedded editor-only style metadata without changing visible HTML."""
    return _STYLE_STATE_COMMENT_RE.sub("", raw or "")


def embed_lettersmith_style_state(
    raw: str, state: Mapping[str, object]
) -> str:
    """Replace any existing style metadata with one non-rendering comment."""
    comment = _encode_style_state_comment(state)
    content = strip_lettersmith_style_state(raw)
    marker_at = content.find(LETTERSMITH_MESSAGE_MARKER)
    if marker_at >= 0:
        insert_at = marker_at + len(LETTERSMITH_MESSAGE_MARKER)
        return content[:insert_at] + comment + content[insert_at:]
    return comment + content


def extract_lettersmith_style_state(raw: str) -> dict[str, object] | None:
    """Read a valid style-state envelope from safe message HTML, if present."""
    safe = sanitize_message_html(raw)
    match = _STYLE_STATE_COMMENT_RE.search(safe)
    if match is None:
        return None
    parsed = _decode_style_state_comment(match.group()[4:-3])
    return parsed[1] if parsed is not None and parsed[0] in _SUPPORTED_STYLE_STATE_VERSIONS else None


def has_unsupported_lettersmith_style_state(raw: str) -> bool:
    """Identify preserved future-version metadata before an editor save."""
    safe = sanitize_message_html(raw)
    match = _STYLE_STATE_COMMENT_RE.search(safe)
    if match is None:
        return False
    parsed = _decode_style_state_comment(match.group()[4:-3])
    return parsed is not None and parsed[0] not in _SUPPORTED_STYLE_STATE_VERSIONS


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def is_ultralink_href(href: str) -> bool:
    value = (href or "").casefold()
    return any(value.startswith(scheme) for scheme in ULTRALINK_SCHEMES)


def make_ultralink_href(message: str) -> str:
    """Encode tooltip text in the anchor href that Qt preserves in rich HTML."""
    return ULTRALINK_SCHEME + quote(_normalize_newlines(message or ""), safe="")


def ultralink_message_from_href(href: str) -> str | None:
    value = href or ""
    lowered = value.casefold()
    for scheme in ULTRALINK_SCHEMES:
        if lowered.startswith(scheme):
            return unquote(value[len(scheme) :])
    return None


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


def _anchor_text_style_is_safe(name: str, value: str) -> bool:
    if name == "font-size":
        if value.casefold() in {
            "large",
            "medium",
            "small",
            "x-large",
            "x-small",
            "xx-large",
            "xx-small",
            "xxx-large",
        }:
            return True
        match = CSS_LENGTH_RE.fullmatch(value)
        if not match:
            return False
        amount = float(match.group(1))
        unit = match.group(2).casefold()
        if unit == "pt":
            amount *= 4.0 / 3.0
        elif unit in {"em", "rem"}:
            amount *= 16.0
        elif unit == "%":
            amount *= 0.16
        return 8.0 <= amount <= 128.0
    if name == "line-height":
        match = CSS_LINE_HEIGHT_RE.fullmatch(value)
        if value.casefold() == "normal":
            return True
        if not match:
            return False
        amount = float(match.group(1))
        if value.endswith("%"):
            amount /= 100.0
        return 0.8 <= amount <= 3.0
    if name in {"letter-spacing", "word-spacing"}:
        if value.casefold() == "normal":
            return True
        match = CSS_LENGTH_RE.fullmatch(value)
        if not match or match.group(2).casefold() == "%":
            return False
        amount = float(match.group(1))
        unit = match.group(2).casefold()
        if unit == "pt":
            amount *= 4.0 / 3.0
        elif unit in {"em", "rem"}:
            amount *= 16.0
        return abs(amount) <= 32.0
    if name == "vertical-align":
        return value.casefold() in {
            "baseline",
            "bottom",
            "middle",
            "sub",
            "super",
            "text-bottom",
            "text-top",
            "top",
        }
    if name == "white-space":
        return value.casefold() == "normal"
    if name == "color":
        lowered = value.casefold().replace(" ", "")
        if lowered in {
            "currentcolor",
            "inherit",
            "initial",
            "revert",
            "revert-layer",
            "transparent",
            "unset",
        }:
            return False
        if lowered.startswith("#"):
            return bool(re.fullmatch(r"#[0-9a-f]{3}(?:[0-9a-f]{3})?", lowered))
        if re.fullmatch(r"[a-z]{1,32}", lowered):
            return True
        if lowered.startswith(("rgb(", "hsl(")) and "/" not in lowered:
            return bool(re.fullmatch(r"[a-z]+\([0-9.,%+-]+\)", lowered))
        return False
    return True


def _style_value_is_safely_bounded(name: str, value: str) -> bool:
    if name in {"font-size", "letter-spacing", "line-height", "word-spacing"}:
        return _anchor_text_style_is_safe(name, value)
    return True


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


def _sanitize_style_attribute(value: str, *, text_only: bool = False) -> str:
    declarations: list[str] = []
    seen: set[str] = set()
    for raw_declaration in (value or "").split(";"):
        if ":" not in raw_declaration:
            continue
        raw_name, raw_value = raw_declaration.split(":", 1)
        name = raw_name.strip().casefold()
        style_value = re.sub(r"\s+", " ", raw_value).strip()
        if (
            name not in _SAFE_STYLE_PROPERTIES
            or (text_only and name not in _ANCHOR_TEXT_STYLE_PROPERTIES)
            or name in seen
            or not style_value
            or len(style_value) > 512
            or any(char in style_value for char in "<>[]{}\\@!")
            or any(ord(char) < 32 and char not in "\t\r\n" for char in style_value)
            or _UNSAFE_STYLE_VALUE.search(style_value)
        ):
            continue
        if not _style_value_is_safely_bounded(name, style_value):
            continue
        if text_only and not _anchor_text_style_is_safe(name, style_value):
            continue
        if name == "font-family":
            style_value = style_value.replace('"', "'")
        declarations.append(f"{name}:{style_value}")
        seen.add(name)
    return "; ".join(declarations)


def _sanitize_href(value: str) -> str:
    candidate = _CONTROL_CHARACTERS.sub("", (value or "").strip())
    if not candidate or len(candidate) > 16_384 or candidate.startswith("//"):
        return ""
    if candidate.startswith("#"):
        return candidate
    scheme = urlsplit(candidate).scheme.casefold()
    if scheme not in {"http", "https", "mailto", "ultralink", "hypernote"}:
        return ""
    return candidate


def _sanitize_image_source(value: str) -> str:
    candidate = _CONTROL_CHARACTERS.sub("", (value or "").strip())
    if not candidate or candidate.startswith(("//", "/", "\\")):
        return ""
    if candidate.casefold().startswith("data:"):
        if len(candidate) > 8 * 1024 * 1024:
            return ""
        return candidate if _RASTER_DATA_URI.fullmatch(candidate) else ""

    parsed = urlsplit(candidate)
    if parsed.scheme or parsed.netloc or "\\" in parsed.path:
        return ""
    decoded_path = unquote(parsed.path)
    if "\\" in decoded_path or _CONTROL_CHARACTERS.search(decoded_path):
        return ""
    parts = tuple(part for part in decoded_path.split("/") if part not in {"", "."})
    if not parts or any(part == ".." for part in parts):
        return ""
    if Path(parts[-1]).suffix.casefold() not in _RASTER_SUFFIXES:
        return ""
    return candidate


def _sanitize_attribute(
    tag: str,
    name: str,
    value: str,
    *,
    within_anchor: bool = False,
) -> str:
    if name == "class":
        classes = [
            token
            for token in re.split(r"\s+", value.strip())
            if token in _SAFE_CLASSES
        ]
        return " ".join(dict.fromkeys(classes))
    if name == "style":
        return _sanitize_style_attribute(value, text_only=within_anchor)
    if name == "href":
        return _sanitize_href(value)
    if name == "src":
        return _sanitize_image_source(value)
    if name in {"width", "height", "border", "cellpadding", "cellspacing"}:
        return value.strip() if _NUMBER_VALUE.fullmatch(value.strip()) else ""
    if name in {"colspan", "rowspan", "start", "value"}:
        try:
            number = int(value)
        except (TypeError, ValueError):
            return ""
        return str(number) if -10_000 <= number <= 10_000 else ""
    if name == "align":
        candidate = value.strip().casefold()
        return candidate if candidate in {"center", "justify", "left", "right"} else ""
    if name == "valign":
        candidate = value.strip().casefold()
        return candidate if candidate in {"baseline", "bottom", "middle", "top"} else ""
    if name == "dir":
        candidate = value.strip().casefold()
        return candidate if candidate in {"auto", "ltr", "rtl"} else ""
    if name == "scope":
        candidate = value.strip().casefold()
        return candidate if candidate in {"col", "colgroup", "row", "rowgroup"} else ""
    if name == "lang":
        candidate = value.strip()
        return candidate if _LANGUAGE_VALUE.fullmatch(candidate) else ""
    if name == "type" and tag == "ol":
        candidate = value.strip()
        return candidate if candidate in {"1", "A", "a", "I", "i"} else ""
    if name in {"alt", "title"}:
        return value[:2048]
    return ""


class _SanitizationLimitError(ValueError):
    pass


class _PassiveMessageHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.output: list[str] = []
        self.open_tags: list[str] = []
        self.blocked_tags: list[str] = []
        self.marker_emitted = False
        self.style_state_emitted = False
        self.output_characters = 0
        self.tag_count = 0

    def _append_output(self, value: str) -> None:
        updated = self.output_characters + len(value)
        if updated > MAX_MESSAGE_HTML_OUTPUT_CHARACTERS:
            raise _SanitizationLimitError
        self.output.append(value)
        self.output_characters = updated

    def _append_escaped_data(self, value: str) -> None:
        for offset in range(0, len(value), _SANITIZER_CHUNK_CHARACTERS):
            self._append_output(
                _html.escape(
                    value[offset : offset + _SANITIZER_CHUNK_CHARACTERS],
                    quote=False,
                )
            )

    def _record_tag(self) -> None:
        self.tag_count += 1
        if self.tag_count > MAX_MESSAGE_HTML_TAGS:
            raise _SanitizationLimitError

    def _in_blocked_subtree(self) -> bool:
        return bool(self.blocked_tags)

    def _start_blocked(self, tag: str) -> None:
        if tag not in _VOID_TAGS:
            if len(self.blocked_tags) >= MAX_MESSAGE_HTML_NESTING:
                raise _SanitizationLimitError
            self.blocked_tags.append(tag)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._record_tag()
        tag = tag.casefold()
        if self._in_blocked_subtree():
            self._start_blocked(tag)
            return
        if tag in _BLOCKED_SUBTREES:
            self._start_blocked(tag)
            return
        if tag in _DOCUMENT_WRAPPERS or tag not in _PASSIVE_TAGS:
            return
        inside_anchor = "a" in self.open_tags
        if inside_anchor and tag == "a":
            self._start_blocked(tag)
            return
        if inside_anchor and tag not in _ANCHOR_INLINE_TAGS:
            return

        allowed = _GLOBAL_ATTRIBUTES | _TAG_ATTRIBUTES.get(tag, frozenset())
        cleaned: list[tuple[str, str]] = []
        seen: set[str] = set()
        for raw_name, raw_value in attrs:
            name = str(raw_name or "").strip().casefold()
            if (
                not name
                or name in seen
                or name not in allowed
                or name.startswith("on")
                or ":" in name
            ):
                continue
            value = _sanitize_attribute(
                tag,
                name,
                str(raw_value or ""),
                within_anchor=inside_anchor or tag == "a",
            )
            if not value:
                continue
            cleaned.append((name, value))
            seen.add(name)

        if tag == "a":
            style_index = next(
                (index for index, item in enumerate(cleaned) if item[0] == "style"),
                None,
            )
            if style_index is None:
                cleaned.append(("style", "white-space:normal"))
            else:
                style_value = cleaned[style_index][1]
                if "white-space:" not in style_value.casefold():
                    cleaned[style_index] = (
                        "style",
                        f"{style_value}; white-space:normal",
                    )

        rendered_attributes = "".join(
            f' {name}="{_html.escape(value, quote=False).replace(chr(34), "&quot;")}"'
            for name, value in cleaned
        )
        self._append_output(f"<{tag}{rendered_attributes}>")
        if tag not in _VOID_TAGS:
            if len(self.open_tags) >= MAX_MESSAGE_HTML_NESTING:
                raise _SanitizationLimitError
            self.open_tags.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        if self._in_blocked_subtree() or tag in _BLOCKED_SUBTREES:
            return
        before = len(self.open_tags)
        self.handle_starttag(tag, attrs)
        if len(self.open_tags) > before and self.open_tags[-1] == tag:
            self.open_tags.pop()
            self._append_output(f"</{tag}>")

    def handle_endtag(self, tag: str) -> None:
        self._record_tag()
        tag = tag.casefold()
        if self._in_blocked_subtree():
            if tag in self.blocked_tags:
                while self.blocked_tags:
                    blocked = self.blocked_tags.pop()
                    if blocked == tag:
                        break
            return
        if tag not in self.open_tags:
            return
        while self.open_tags:
            opened = self.open_tags.pop()
            self._append_output(f"</{opened}>")
            if opened == tag:
                break

    def handle_data(self, data: str) -> None:
        if not self._in_blocked_subtree() and data:
            self._append_escaped_data(data)

    def handle_comment(self, data: str) -> None:
        if not self._in_blocked_subtree() and not self.style_state_emitted:
            parsed = _decode_style_state_comment(data)
            if parsed is not None:
                version, state, encoded = parsed
                try:
                    comment = (
                        _encode_style_state_comment(state)
                        if version == 1
                        else f"<!-- lettersmith-style-state:v{version}:{encoded} -->"
                    )
                except ValueError:
                    pass
                else:
                    self._append_output(comment)
                    self.style_state_emitted = True
                    return
        if (
            not self._in_blocked_subtree()
            and not self.marker_emitted
            and data.strip().casefold() == "lettersmith-message:v2"
        ):
            self._append_output(LETTERSMITH_MESSAGE_MARKER)
            self.marker_emitted = True

    def finish(self) -> str:
        while self.open_tags:
            self._append_output(f"</{self.open_tags.pop()}>")
        return "".join(self.output).strip()


def _bounded_plain_text_fallback(source: str) -> str:
    parts: list[str] = []
    total = 0
    truncated = len(source) > _FALLBACK_INPUT_CHARACTERS
    value = source[:_FALLBACK_INPUT_CHARACTERS]
    for offset in range(0, len(value), _SANITIZER_CHUNK_CHARACTERS):
        escaped = _html.escape(
            value[offset : offset + _SANITIZER_CHUNK_CHARACTERS],
            quote=False,
        )
        remaining = MAX_MESSAGE_HTML_OUTPUT_CHARACTERS - total
        if len(escaped) > remaining:
            parts.append(escaped[:remaining])
            total += remaining
            truncated = True
            break
        parts.append(escaped)
        total += len(escaped)
    if truncated:
        suffix = "\n[Message content exceeded safe formatting limits.]"
        parts.append(suffix[: MAX_MESSAGE_HTML_OUTPUT_CHARACTERS - total])
    return "".join(parts)


def sanitize_message_html(raw: str) -> str:
    """Return balanced passive message markup safe for editor and viewer use."""
    source = normalize_message_document_html(raw)
    if not source:
        return ""
    if len(source) > MAX_MESSAGE_HTML_INPUT_CHARACTERS:
        return _bounded_plain_text_fallback(source)

    parser = _PassiveMessageHTMLParser()
    try:
        parser.feed(source)
        parser.close()
        sanitized = parser.finish()
    except (AssertionError, ValueError):
        return _bounded_plain_text_fallback(source)

    lowered = source.casefold()
    qt_document = (
        LETTERSMITH_MESSAGE_MARKER.casefold() in lowered
        or 'name="qrichtext"' in lowered
        or "name='qrichtext'" in lowered
        or "-qt-" in lowered
        or "ls-linewrap" in lowered
        or "lettersmith-defaults" in lowered
    )
    if not qt_document:
        return sanitized
    if "lettersmith-defaults" not in lowered:
        sanitized = f'<div class="lettersmith-defaults">{sanitized}</div>'
    return _QT_DEFAULTS_STYLE + sanitized


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
