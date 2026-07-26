from __future__ import annotations

import html as _html
import time
import re
from pathlib import Path
from html.parser import HTMLParser

BODY_RE = re.compile(r"<body\b[^>]*>(.*?)</body>", re.IGNORECASE | re.DOTALL)
DOC_GUID_RE = re.compile(
    r"<a\b[^>]*name=(['\"])docs-internal-guid-[^'\"]+\1[^>]*>\s*</a>",
    re.IGNORECASE,
)
STYLE_ATTR_RE = re.compile(r"\sstyle=(['\"])(.*?)\1", re.IGNORECASE | re.DOTALL)
FONT_FAMILY_DECL_RE = re.compile(
    r"(font-family\s*:\s*)((?:\"[^\"]*\"|'[^']*'|[^;\"'<>])*)"
    r"(?=;|[\"']\s*(?:[a-zA-Z_:][\w:.-]*\s*=|/?>)|$)",
    re.IGNORECASE,
)
CSS_LENGTH_RE = re.compile(r"^(-?\d+(?:\.\d+)?)(pt|px|em|rem|%)$", re.IGNORECASE)
CSS_LINE_HEIGHT_RE = re.compile(r"^(-?\d+(?:\.\d+)?)%?$", re.IGNORECASE)
FRAGMENT_NOISE_TAG_RE = re.compile(
    r"</?(?:meta|style|link|title|html|head|body)\b[^>]*>",
    re.IGNORECASE,
)
INVISIBLE_TEXT_RE = re.compile(r"[\s\u00a0\u200b\u200c\u200d\u2060\ufeff]+")
WORD_RE = re.compile(r"\b\w+\b", re.UNICODE)

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
    buffer: list[str] = []
    quote = ""

    for char in value or "":
        if quote:
            buffer.append(char)
            if char == quote:
                quote = ""
            continue
        if char in ("'", '"'):
            quote = char
            buffer.append(char)
            continue
        if char == ",":
            part = "".join(buffer).strip()
            if part:
                parts.append(part)
            buffer = []
            continue
        buffer.append(char)

    part = "".join(buffer).strip()
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


def rewrite_font_families(raw: str, family_aliases: dict[str, str]) -> str:
    if not raw or not family_aliases:
        return raw

    alias_by_key = {_font_key(name): alias for name, alias in family_aliases.items() if alias}

    def repl(match: re.Match[str]) -> str:
        rebuilt: list[str] = []
        inserted: set[str] = set()
        for part in _split_css_font_family_list(match.group(2)):
            family = _unquote_css_font_family(part)
            alias = alias_by_key.get(_font_key(family))
            if alias and alias not in inserted:
                rebuilt.append(f"'{alias}'")
                inserted.add(alias)
            rebuilt.append(part.strip())
        return match.group(1) + ", ".join(rebuilt)

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


class _VisibleWordCounter(HTMLParser):
    IGNORED_CONTAINER_TAGS = {"script", "style", "template", "title", "head"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.word_count = 0
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() in self.IGNORED_CONTAINER_TAGS:
            self._ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() in self.IGNORED_CONTAINER_TAGS and self._ignored_depth:
            self._ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth:
            self.word_count += len(WORD_RE.findall(data))


class _FormattingPreservingWordLimiter(HTMLParser):
    IGNORED_CONTAINER_TAGS = _VisibleWordCounter.IGNORED_CONTAINER_TAGS
    VOID_TAGS = {
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr",
    }

    def __init__(self, limit: int) -> None:
        super().__init__(convert_charrefs=False)
        self.limit = max(0, int(limit))
        self.word_count = 0
        self.truncated = False
        self._ignored_depth = 0
        self._stopped = False
        self._open_tags: list[str] = []
        self._output: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._stopped:
            return
        normalized = tag.casefold()
        self._output.append(self.get_starttag_text() or f"<{tag}>")
        if normalized not in self.VOID_TAGS:
            self._open_tags.append(tag)
        if normalized in self.IGNORED_CONTAINER_TAGS:
            self._ignored_depth += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if not self._stopped:
            self._output.append(self.get_starttag_text() or f"<{tag}/>")

    def handle_endtag(self, tag: str) -> None:
        if self._stopped:
            return
        normalized = tag.casefold()
        self._output.append(f"</{tag}>")
        for index in range(len(self._open_tags) - 1, -1, -1):
            if self._open_tags[index].casefold() == normalized:
                del self._open_tags[index]
                break
        if normalized in self.IGNORED_CONTAINER_TAGS and self._ignored_depth:
            self._ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._stopped:
            return
        if self._ignored_depth:
            self._output.append(data)
            return

        matches = list(WORD_RE.finditer(data))
        remaining = self.limit - self.word_count
        if len(matches) <= remaining:
            self._output.append(data)
            self.word_count += len(matches)
            return

        cutoff = matches[max(0, remaining)].start()
        self._output.append(data[:cutoff])
        self.word_count += max(0, remaining)
        self.truncated = True
        self._stopped = True

    def handle_entityref(self, name: str) -> None:
        self._append_entity(f"&{name};", _html.unescape(f"&{name};"))

    def handle_charref(self, name: str) -> None:
        self._append_entity(f"&#{name};", _html.unescape(f"&#{name};"))

    def handle_comment(self, data: str) -> None:
        if not self._stopped:
            self._output.append(f"<!--{data}-->")

    def handle_decl(self, decl: str) -> None:
        if not self._stopped:
            self._output.append(f"<!{decl}>")

    def handle_pi(self, data: str) -> None:
        if not self._stopped:
            self._output.append(f"<?{data}>")

    def _append_entity(self, encoded: str, decoded: str) -> None:
        if self._stopped:
            return
        entity_words = 0 if self._ignored_depth else len(WORD_RE.findall(decoded))
        if self.word_count + entity_words > self.limit:
            self.truncated = True
            self._stopped = True
            return
        self._output.append(encoded)
        self.word_count += entity_words

    def result(self) -> str:
        if self._stopped:
            self._output.extend(f"</{tag}>" for tag in reversed(self._open_tags))
            self._open_tags.clear()
        return "".join(self._output)


def count_message_html_words(raw: str) -> int:
    parser = _VisibleWordCounter()
    parser.feed(raw or "")
    parser.close()
    return parser.word_count


def truncate_message_html_words(raw: str, limit: int) -> str:
    parser = _FormattingPreservingWordLimiter(limit)
    parser.feed(raw or "")
    parser.close()
    return parser.result()


class _MessageContentParser(HTMLParser):
    MEDIA_TAGS = {"img", "svg", "picture", "video", "audio", "iframe", "object", "embed", "canvas"}
    IGNORED_CONTAINER_TAGS = {"script", "style", "template", "title", "head"}
    MEANINGFUL_MEDIA_ATTRS = ("src", "srcset", "data-src", "data-original", "href")

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.has_content = False
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in self.IGNORED_CONTAINER_TAGS:
            self._ignored_depth += 1
            return

        if tag in self.MEDIA_TAGS and self._media_has_source(tag, attrs):
            self.has_content = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self.IGNORED_CONTAINER_TAGS and self._ignored_depth > 0:
            self._ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        if _visible_text(data):
            self.has_content = True

    def handle_entityref(self, name: str) -> None:
        self.handle_data(_html.unescape(f"&{name};"))

    def handle_charref(self, name: str) -> None:
        self.handle_data(_html.unescape(f"&#{name};"))

    def _media_has_source(self, tag: str, attrs: list[tuple[str, str | None]]) -> bool:
        if tag == "svg":
            return True

        attr_map = {name.lower(): (value or "").strip() for name, value in attrs}
        for name in self.MEANINGFUL_MEDIA_ATTRS:
            value = attr_map.get(name, "")
            if value and value.lower() not in {"#", "about:blank", "javascript:void(0)"}:
                return True
        return False


def _visible_text(text: str) -> bool:
    return bool(INVISIBLE_TEXT_RE.sub("", _html.unescape(text or "")))


def _content_detection_fragment(raw: str) -> str:
    text = normalize_message_document_html(raw)
    if not text:
        return ""

    match = BODY_RE.search(text)
    fragment = match.group(1) if match else text
    fragment = DOC_GUID_RE.sub("", fragment)
    fragment = _clean_style_attrs(fragment)
    return fragment.strip()


def message_html_has_content(html: str) -> bool:
    fragment = _content_detection_fragment(html or "")
    if not fragment:
        return False

    parser = _MessageContentParser()
    try:
        parser.feed(fragment)
        parser.close()
    except Exception:
        return _visible_text(re.sub(r"<[^>]*>", "", fragment))
    return parser.has_content


# ─────────────────────────────────────────────────────────────────────────────
# Default empty message fallback
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_EMESSAGE_DOCX_REL = Path("gallery/app/pages/Emessage.docx")
DEFAULT_MESSAGE_HTML_REL = Path("gallery/user/message/message.html")
BLANK_MESSAGE_HTML = "<p></p>"


def _atomic_write_text(path: str | Path, data: str, *, encoding: str = "utf-8") -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + f".tmp.{int(time.time() * 1000)}")
    tmp.write_text(data, encoding=encoding)
    try:
        tmp.replace(p)
    except Exception:
        # Last-resort fallback for transient Windows file-lock behavior.
        p.write_text(data, encoding=encoding)
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


def default_emessage_docx_path(project_root: str | Path) -> Path:
    return Path(project_root) / DEFAULT_EMESSAGE_DOCX_REL


def default_message_html_path(project_root: str | Path) -> Path:
    return Path(project_root) / DEFAULT_MESSAGE_HTML_REL


def docx_to_message_html(docx_path: str | Path) -> str:
    """
    Convert the built-in empty E-message DOCX into a message.html fragment.

    Mammoth is preferred because it preserves simple document structure better.
    python-docx is used as a plain-text fallback. If the DOCX is missing,
    unreadable, or blank, this returns a valid empty HTML fragment.
    """
    source = Path(docx_path)
    if not source.is_file():
        return BLANK_MESSAGE_HTML

    html = ""

    try:
        import mammoth  # type: ignore

        with source.open("rb") as docx_file:
            result = mammoth.convert_to_html(docx_file)
        html = normalize_message_fragment(result.value)
    except Exception:
        html = ""

    if not html.strip():
        try:
            from docx import Document  # type: ignore

            doc = Document(str(source))
            parts: list[str] = []
            for paragraph in doc.paragraphs:
                text = paragraph.text.strip()
                if text:
                    parts.append(f"<p>{_html.escape(text)}</p>")
            html = "\n".join(parts)
        except Exception:
            html = ""

    html = normalize_message_fragment(html)
    return html if html.strip() else BLANK_MESSAGE_HTML


def ensure_message_html_from_emessage(
    project_root: str | Path,
    *,
    overwrite: bool = False,
) -> Path:
    """
    Guarantee gallery/user/message/message.html exists.

    Source priority:
    1. gallery/app/pages/Emessage.docx
    2. a valid blank HTML fragment if the DOCX is unavailable

    Set overwrite=True after Command reset so the wiped message is rebuilt from
    the empty E-message template every time.
    """
    target = default_message_html_path(project_root)
    if target.is_file() and target.stat().st_size > 0 and not overwrite:
        return target

    source = default_emessage_docx_path(project_root)
    html = docx_to_message_html(source)
    _atomic_write_text(target, html)
    return target
