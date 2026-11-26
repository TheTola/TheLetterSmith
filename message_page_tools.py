"""Text edits against the visible content of a Letter page."""
from __future__ import annotations

from dataclasses import dataclass
import re

from PySide6.QtGui import QTextCursor


@dataclass(frozen=True)
class TextEdit:
    start: int
    end: int
    text: str
    replacement: str


def find_text(text: str, query: str, *, match_case: bool) -> tuple[TextEdit, ...]:
    if not query:
        return ()
    return tuple(TextEdit(match.start(), match.end(), match.group(), "")
                 for match in re.finditer(re.escape(query), text, 0 if match_case else re.IGNORECASE))


def recipient_name_edits(text: str, previous: str, new: str) -> tuple[TextEdit, ...]:
    old_parts, new_parts = previous.split(), new.split()
    if not old_parts or not new_parts or old_parts == new_parts:
        return ()
    # Try the full name first. The word boundaries leave possessives and
    # punctuation intact, while excluding parts of other names and words.
    alternatives = [r"(?P<full>" + r"[^\S\r\n\u2028\u2029]+".join(map(re.escape, old_parts)) + ")"]
    if len(old_parts) > 1:
        alternatives += [f"(?P<first>{re.escape(old_parts[0])})", f"(?P<last>{re.escape(old_parts[-1])})"]
    pattern = re.compile(r"(?<!\w)(?<!\w['’\-])(?:" + "|".join(alternatives)
                         + r")(?!\w|['’](?!s\b)\w|-\w)", re.IGNORECASE)
    result = []

    def replace_part(start, end, old_part, new_part):
        source = text[start:end]
        if old_part == new_part:
            return
        replacement = new_part
        if source != old_part and source.isupper():
            replacement = new_part.upper()
        elif source != old_part and source.islower():
            replacement = new_part.lower()
        if source != replacement:
            result.append(TextEdit(start, end, source, replacement))

    for match in pattern.finditer(text):
        if match.lastgroup == "full" and len(old_parts) == len(new_parts):
            for word, old_part, new_part in zip(re.finditer(r"\S+", match.group()), old_parts, new_parts):
                replace_part(match.start() + word.start(), match.start() + word.end(), old_part, new_part)
        elif match.lastgroup == "first":
            replace_part(match.start(), match.end(), old_parts[0], new_parts[0])
        elif match.lastgroup == "last":
            replace_part(match.start(), match.end(), old_parts[-1], new_parts[-1])
        else:
            replace_part(match.start(), match.end(), " ".join(old_parts), " ".join(new_parts))
    return tuple(result)


def replace_text_ranges(editor, edits: tuple[TextEdit, ...]) -> None:
    """Apply reviewed Python offsets as one rich-text edit."""
    text = editor.toPlainText()
    ordered = sorted(edits, key=lambda edit: (edit.start, edit.end), reverse=True)
    previous = len(text) + 1
    for edit in ordered:
        if not (0 <= edit.start <= edit.end <= len(text) and edit.end <= previous
                and text[edit.start:edit.end] == edit.text):
            raise ValueError("The text changed. Review the current page before replacing it.")
        previous = edit.start
    # Qt cursor positions count UTF-16 code units.
    offsets = editor._python_to_qt_offsets(text)
    cursor = QTextCursor(editor.document())
    cursor.beginEditBlock()
    try:
        for edit in ordered:
            start, end = offsets[edit.start], offsets[edit.end]
            cursor.setPosition(start)
            if start != end:
                cursor.setPosition(offsets[edit.start + 1], QTextCursor.KeepAnchor)
            char_format = cursor.charFormat()
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.KeepAnchor)
            if edit.replacement:
                cursor.insertText(edit.replacement, char_format)
            else:
                cursor.removeSelectedText()
    finally:
        cursor.endEditBlock()
