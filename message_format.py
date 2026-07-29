from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QTextBlockFormat, QTextCharFormat, QTextCursor, QTextDocument

from message_html import is_ultralink_href


DEFAULT_MESSAGE_FONT = "Papyrus"
DEFAULT_MESSAGE_ALIGNMENT = Qt.AlignCenter
DEFAULT_MESSAGE_LINE_SPACING = 2.0
WORDS_PER_MINUTE = 200
_WORD_RE = re.compile(r"\b\w+\b", re.UNICODE)


@dataclass(frozen=True)
class MessageStatistics:
    words: int
    characters: int
    reading_minutes: int


class _PlainTextParser(HTMLParser):
    _IGNORED = {"script", "style", "template", "title", "head"}
    _BLOCKS = {
        "address",
        "article",
        "aside",
        "blockquote",
        "div",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "li",
        "main",
        "p",
        "section",
        "tr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        normalized = tag.casefold()
        if normalized in self._IGNORED:
            self._ignored_depth += 1
        elif not self._ignored_depth and normalized == "br":
            self._newline()

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.casefold()
        if normalized in self._IGNORED and self._ignored_depth:
            self._ignored_depth -= 1
        elif not self._ignored_depth and normalized in self._BLOCKS:
            self._newline()

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth:
            self._parts.append(data)

    def _newline(self) -> None:
        if self._parts and not self._parts[-1].endswith("\n"):
            self._parts.append("\n")

    def result(self) -> str:
        value = "".join(self._parts).replace("\r\n", "\n").replace("\r", "\n")
        lines = [re.sub(r"[ \t\f\v]+", " ", line).strip() for line in value.split("\n")]
        return "\n".join(lines).strip()


def message_plain_text(html: str) -> str:
    parser = _PlainTextParser()
    parser.feed(html or "")
    parser.close()
    return parser.result()


def message_statistics(html: str) -> MessageStatistics:
    plain_text = message_plain_text(html)
    words = len(_WORD_RE.findall(plain_text))
    minutes = max(1, round(words / WORDS_PER_MINUTE)) if words else 0
    return MessageStatistics(words, len(plain_text), minutes)


def _apply_block_defaults(document: QTextDocument) -> None:
    block = document.firstBlock()
    while block.isValid():
        cursor = QTextCursor(block)
        block_format = cursor.blockFormat()
        block_format.setAlignment(DEFAULT_MESSAGE_ALIGNMENT)
        block_format.setLineHeight(
            DEFAULT_MESSAGE_LINE_SPACING * 100,
            QTextBlockFormat.ProportionalHeight.value,
        )
        cursor.setBlockFormat(block_format)
        block = block.next()


def normalize_ultralinks_in_document(document: QTextDocument) -> int:
    """Restore Ultralink styling that Qt does not fully retain on HTML import."""
    spans: list[tuple[int, int, QTextCharFormat]] = []
    block = document.begin()
    while block.isValid():
        iterator = block.begin()
        while not iterator.atEnd():
            fragment = iterator.fragment()
            if fragment.isValid():
                char_format = QTextCharFormat(fragment.charFormat())
                if (
                    char_format.isAnchor()
                    and is_ultralink_href(char_format.anchorHref())
                ):
                    spans.append(
                        (
                            fragment.position(),
                            fragment.position() + fragment.length(),
                            char_format,
                        )
                    )
            iterator += 1
        block = block.next()

    work = QTextCursor(document)
    work.beginEditBlock()
    try:
        for start, end, char_format in spans:
            char_format.setFontItalic(True)
            char_format.setFontUnderline(False)
            char_format.setUnderlineStyle(
                QTextCharFormat.UnderlineStyle.NoUnderline
            )
            work.setPosition(start)
            work.setPosition(end, QTextCursor.KeepAnchor)
            work.setCharFormat(char_format)
    finally:
        work.endEditBlock()
    return len(spans)


def apply_blank_editor_defaults(editor) -> None:
    font = QFont(DEFAULT_MESSAGE_FONT)
    font.setStyleHint(QFont.Fantasy)
    editor.document().setDefaultFont(font)

    char_format = QTextCharFormat()
    char_format.setFontFamilies([DEFAULT_MESSAGE_FONT])
    char_format.setFontStyleHint(QFont.Fantasy)
    editor.setCurrentCharFormat(char_format)
    _apply_block_defaults(editor.document())

    cursor = editor.textCursor()
    cursor.movePosition(QTextCursor.End)
    cursor.setCharFormat(char_format)
    editor.setTextCursor(cursor)


def normalize_imported_message_html(html: str) -> str:
    document = QTextDocument()
    font = QFont(DEFAULT_MESSAGE_FONT)
    font.setStyleHint(QFont.Fantasy)
    document.setDefaultFont(font)
    document.setHtml(html or "<p></p>")

    cursor = QTextCursor(document)
    cursor.select(QTextCursor.Document)
    char_format = QTextCharFormat()
    char_format.setFontFamilies([DEFAULT_MESSAGE_FONT])
    char_format.setFontStyleHint(QFont.Fantasy)
    cursor.mergeCharFormat(char_format)
    _apply_block_defaults(document)
    return document.toHtml()
