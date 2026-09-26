"""QTextDocument-backed named paragraph styles for the letter editor.

Qt's HTML format omits custom QTextFormat properties.  The editor therefore
exports those properties separately via ``serialize`` and restores them after
``setHtml``.  While editing, the active definitions live on the document's
root frame so Qt's own undo stack includes definition-only changes.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import (
    QColor,
    QPalette,
    QTextBlock,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QTextFormat,
    QTextListFormat,
)
from PySide6.QtWidgets import QTextEdit

from named_text_styles import (
    DOCUMENT_STYLE_SCHEMA_VERSION,
    STYLE_KEYS,
    STYLE_PROPERTIES,
    NamedStyleSet,
    ParagraphStyleDefinition,
    StyleDefinition,
    validate_document_style_state,
)


_ROOT_SET_PROPERTY = int(QTextFormat.UserProperty) + 40
_BLOCK_STYLE_PROPERTY = int(QTextFormat.UserProperty) + 41
_CHAR_OVERRIDE_PROPERTY = int(QTextFormat.UserProperty) + 42
_BITS = {name: 1 << index for index, name in enumerate(STYLE_PROPERTIES)}
_ALL_BITS = sum(_BITS.values())
_ROOT_TABLE_OPEN = re.compile(
    r'(?is)(<body\b[^>]*>\s*)<table\b[^>]*-qt-table-type:\s*root\b[^>]*>'
    r'\s*<tr\b[^>]*>\s*<td\b[^>]*>'
)
_ROOT_TABLE_CLOSE = re.compile(
    r'(?is)</td>\s*</tr>\s*</table>(\s*</body>)'
)
_LIST_ITEM_OPEN = re.compile(r'(?is)<li\b[^>]*>')
_STYLE_ATTRIBUTE = re.compile(r'(?is)\sstyle="([^"]*)"')


def _merge_list_item_styles(match: re.Match[str]) -> str:
    tag = match.group()
    styles = _STYLE_ATTRIBUTE.findall(tag)
    if len(styles) < 2:
        return tag
    # Qt emits separate character and paragraph style attributes on <li>.
    # Combine them before HTML consumers discard the duplicate attribute.
    combined = "; ".join(style.strip().rstrip(";") for style in styles)
    return _STYLE_ATTRIBUTE.sub("", tag)[:-1] + f' style="{combined}">'


@dataclass
class _PasteContext:
    work: QTextCursor
    start: int
    rich: bool
    prefix_style: str | None
    suffix_style: str | None
    prefix_survives: bool
    suffix_survives: bool


def _style_key(block: QTextBlock) -> str | None:
    value = block.blockFormat().property(_BLOCK_STYLE_PROPERTY)
    return value if value in STYLE_KEYS else None


def _override_bits(fmt: QTextCharFormat) -> int:
    value = fmt.property(_CHAR_OVERRIDE_PROPERTY)
    return value & _ALL_BITS if type(value) is int and value >= 0 else 0


def _set_override_bits(fmt: QTextCharFormat, bits: int) -> None:
    fmt.setProperty(_CHAR_OVERRIDE_PROPERTY, bits & _ALL_BITS)


def _apply_definition(
    fmt: QTextCharFormat,
    definition: StyleDefinition,
    *,
    skip_bits: int = 0,
) -> None:
    if not skip_bits & _BITS["font_family"]:
        fmt.setFontFamilies([definition.font_family])
    if not skip_bits & _BITS["font_size"]:
        fmt.setFontPointSize(float(definition.font_size))
    if not skip_bits & _BITS["font_color"]:
        fmt.setForeground(QColor(definition.font_color))
    if not skip_bits & _BITS["font_weight"]:
        fmt.setFontWeight(definition.font_weight)
    if not skip_bits & _BITS["italic"]:
        fmt.setFontItalic(definition.italic)
    if not skip_bits & _BITS["underline"]:
        fmt.setFontUnderline(definition.underline)
    if not skip_bits & _BITS["strikethrough"]:
        fmt.setFontStrikeOut(definition.strikethrough)


def _list_properties(fmt: QTextListFormat) -> dict[str, object]:
    return {
        "list_style": fmt.style().value,
        "list_indent": fmt.indent(),
        "list_prefix": fmt.numberPrefix() if fmt.hasProperty(QTextFormat.ListNumberPrefix) else None,
        "list_suffix": fmt.numberSuffix() if fmt.hasProperty(QTextFormat.ListNumberSuffix) else None,
        "list_start": fmt.start(),
    }


def _paragraph_definition(block: QTextBlock) -> ParagraphStyleDefinition:
    fmt = block.blockFormat()
    text_list = block.textList()
    return ParagraphStyleDefinition(
        alignment=int(fmt.alignment()),
        direction=fmt.layoutDirection().value,
        line_height=fmt.lineHeight(),
        line_height_type=fmt.lineHeightType(),
        top_margin=fmt.topMargin(),
        bottom_margin=fmt.bottomMargin(),
        left_margin=fmt.leftMargin(),
        right_margin=fmt.rightMargin(),
        text_indent=fmt.textIndent(),
        indent=fmt.indent(),
        **(_list_properties(text_list.format()) if text_list is not None else {}),
    )


class NamedTextStyleDocument:
    """Manage six style definitions and explicit per-paragraph assignments."""

    def __init__(self, editor: QTextEdit, initial_set: NamedStyleSet) -> None:
        if not isinstance(initial_set, NamedStyleSet):
            raise TypeError("initial_set must be a validated NamedStyleSet")
        self.editor = editor
        self.editor.named_style_controller = self
        self.active_set = initial_set
        self._mutating = False
        self._install_initial_set(initial_set)
        self.editor.document().contentsChanged.connect(self._sync_active_set)

    def _install_initial_set(self, style_set: NamedStyleSet) -> None:
        # Construction/restoration is not a user edit and must not create an
        # Undo item or autosave.  The controller is installed before use.
        doc = self.editor.document()
        enabled = doc.isUndoRedoEnabled()
        doc.setUndoRedoEnabled(False)
        try:
            self._write_root_set(style_set)
        finally:
            doc.setUndoRedoEnabled(enabled)

    def _write_root_set(self, style_set: NamedStyleSet) -> None:
        root = self.editor.document().rootFrame()
        fmt = root.frameFormat()
        fmt.setProperty(
            _ROOT_SET_PROPERTY,
            json.dumps(style_set.to_dict(), sort_keys=True, separators=(",", ":")),
        )
        root.setFrameFormat(fmt)

    def _sync_active_set(self) -> None:
        if self._mutating:
            return
        raw = self.editor.document().rootFrame().frameFormat().property(
            _ROOT_SET_PROPERTY
        )
        if not isinstance(raw, str):
            return
        try:
            self.active_set = NamedStyleSet.from_dict(json.loads(raw))
        except (ValueError, TypeError):
            # Keep the last known-good definition set while a malformed
            # in-memory property is ignored.  Saved data is validated first.
            return

    def _run_edit(self, action: Callable[[QTextCursor], None]) -> None:
        original = QTextCursor(self.editor.textCursor())
        work = QTextCursor(self.editor.document())
        self._mutating = True
        work.beginEditBlock()
        try:
            action(work)
        finally:
            work.endEditBlock()
            self._mutating = False
            self.editor.setTextCursor(original)
            self._sync_active_set()

    def _target_blocks(self) -> list[QTextBlock]:
        cursor = self.editor.textCursor()
        document = self.editor.document()
        start = cursor.selectionStart() if cursor.hasSelection() else cursor.position()
        end = cursor.selectionEnd() if cursor.hasSelection() else start
        # QTextCursor's selection end is exclusive.  If it is exactly at a
        # block boundary, that next paragraph is outside the selection.
        last_position = end - 1 if end > start else end
        block = document.findBlock(start)
        last = document.findBlock(last_position)
        blocks: list[QTextBlock] = []
        while block.isValid():
            blocks.append(block)
            if block == last:
                break
            block = block.next()
        return blocks

    @staticmethod
    def _text_fragments(block: QTextBlock) -> list[tuple[int, int, QTextCharFormat]]:
        fragments: list[tuple[int, int, QTextCharFormat]] = []
        iterator = block.begin()
        while not iterator.atEnd():
            fragment = iterator.fragment()
            if fragment.isValid() and fragment.length() and not fragment.charFormat().isImageFormat():
                fragments.append(
                    (
                        fragment.position(),
                        fragment.position() + fragment.length(),
                        QTextCharFormat(fragment.charFormat()),
                    )
                )
            iterator += 1
        return fragments

    @staticmethod
    def _replace_span(
        start: int, end: int, fmt: QTextCharFormat, document
    ) -> None:
        if end <= start:
            return
        cursor = QTextCursor(document)
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.KeepAnchor)
        cursor.setCharFormat(fmt)

    def _set_block_style(self, block: QTextBlock, key: str | None) -> None:
        cursor = QTextCursor(block)
        block_format = cursor.blockFormat()
        block_format.setProperty(_BLOCK_STYLE_PROPERTY, key or "")
        cursor.setBlockFormat(block_format)

    def _format_block(
        self,
        block: QTextBlock,
        definition: StyleDefinition,
        *,
        clear_overrides: bool,
        apply_paragraph: bool = False,
        promote_range: tuple[int, int] | None = None,
    ) -> None:
        if clear_overrides or apply_paragraph:
            self._format_paragraph(block, definition.paragraph)
        for start, end, old_format in self._text_fragments(block):
            fmt = QTextCharFormat(old_format)
            bits = 0 if clear_overrides else _override_bits(fmt)
            # A direct override that now equals the new baseline has become
            # redundant only when Update sampled this same assigned text.
            if bits and promote_range is not None and self._intersects_source(
                start, end, promote_range
            ):
                bits = self._remove_redundant_bits(fmt, definition, bits)
            _apply_definition(fmt, definition, skip_bits=bits)
            _set_override_bits(fmt, bits)
            self._replace_span(start, end, fmt, self.editor.document())

        # The paragraph separator owns the insertion format for an empty
        # block.  Updating only QTextEdit.currentCharFormat is lost as soon as
        # focus/cursor moves away, so record the baseline on the block itself.
        block_cursor = QTextCursor(block)
        fmt = QTextCharFormat(block_cursor.blockCharFormat())
        bits = 0 if clear_overrides else _override_bits(fmt)
        if bits and promote_range is not None and self._intersects_source(
            block.position(), block.position() + block.length(), promote_range
        ):
            bits = self._remove_redundant_bits(fmt, definition, bits)
        _apply_definition(fmt, definition, skip_bits=bits)
        _set_override_bits(fmt, bits)
        block_cursor.setBlockCharFormat(fmt)
        if self.editor.textCursor().block() == block and not self.editor.textCursor().hasSelection():
            self.editor.setCurrentCharFormat(fmt)

    @staticmethod
    def _format_paragraph(
        block: QTextBlock, definition: ParagraphStyleDefinition | None
    ) -> None:
        if definition is None:
            return
        cursor = QTextCursor(block)
        current_list = block.textList()
        if definition.list_style is None:
            if current_list is not None:
                current_list.remove(block)
        else:
            list_format = QTextListFormat()
            list_format.setStyle(QTextListFormat.Style(definition.list_style))
            list_format.setIndent(definition.list_indent)
            # An unset suffix uses Qt's default period; an explicit empty
            # suffix removes it. Keep that distinction when copying a list.
            if definition.list_prefix is not None:
                list_format.setNumberPrefix(definition.list_prefix)
            if definition.list_suffix is not None:
                list_format.setNumberSuffix(definition.list_suffix)
            list_format.setStart(definition.list_start)
            wanted = _list_properties(list_format)
            if current_list is None or _list_properties(current_list.format()) != wanted:
                if current_list is not None:
                    current_list.remove(block)
                # Consecutive paragraphs share numbering. Do not change the
                # format of an existing list containing unrelated paragraphs.
                previous = block.previous()
                previous_list = previous.textList() if previous.isValid() else None
                if (
                    previous_list is not None
                    and _list_properties(previous_list.format()) == wanted
                    and QTextCursor(previous).currentFrame() == cursor.currentFrame()
                ):
                    previous_list.add(block)
                else:
                    cursor.createList(list_format)
        fmt = cursor.blockFormat()
        fmt.setAlignment(Qt.Alignment(definition.alignment))
        fmt.setLayoutDirection(Qt.LayoutDirection(definition.direction))
        fmt.setLineHeight(definition.line_height, definition.line_height_type)
        fmt.setTopMargin(definition.top_margin)
        fmt.setBottomMargin(definition.bottom_margin)
        fmt.setLeftMargin(definition.left_margin)
        fmt.setRightMargin(definition.right_margin)
        fmt.setTextIndent(definition.text_indent)
        fmt.setIndent(definition.indent)
        cursor.setBlockFormat(fmt)

    def sync_empty_insertion_format(self) -> None:
        """Restore an empty styled paragraph's insertion format after navigation."""
        cursor = self.editor.textCursor()
        block = cursor.block()
        if cursor.hasSelection() or block.length() != 1 or _style_key(block) is None:
            return
        fmt = QTextCursor(block).blockCharFormat()
        if self.editor.currentCharFormat() != fmt:
            self.editor.setCurrentCharFormat(fmt)

    @staticmethod
    def _intersects_source(
        start: int, end: int, source: tuple[int, int]
    ) -> bool:
        source_start, source_end = source
        if source_start == source_end:
            return start <= source_start <= end
        return start < source_end and end > source_start

    def _remove_redundant_bits(
        self, fmt: QTextCharFormat, definition: StyleDefinition, bits: int
    ) -> int:
        sampled = self._definition_from_format(fmt)
        for name, bit in _BITS.items():
            if bits & bit and getattr(sampled, name) == getattr(definition, name):
                bits &= ~bit
        return bits

    def apply_style(self, key: str) -> None:
        definition = self.active_set.get(key)
        blocks = self._target_blocks()

        def change(_work: QTextCursor) -> None:
            for block in blocks:
                self._set_block_style(block, key)
                self._format_block(block, definition, clear_overrides=True)

        self._run_edit(change)

    def insert_salutation(
        self, greeting: str, body_block_format: QTextBlockFormat
    ) -> None:
        """Insert a Title greeting and a Normal body as one undoable edit."""
        if not isinstance(greeting, str) or not greeting:
            raise ValueError("Salutation text is required")
        title = self.active_set.get("title")
        normal = self.active_set.get("normal_text")
        title_format = QTextCharFormat()
        normal_format = QTextCharFormat()
        _apply_definition(title_format, title)
        _apply_definition(normal_format, normal)
        cursor = QTextCursor(self.editor.document())
        cursor.movePosition(QTextCursor.Start)
        self._mutating = True
        cursor.beginEditBlock()
        try:
            cursor.insertText(greeting, title_format)
            cursor.insertBlock(body_block_format, normal_format)
            greeting_block = self.editor.document().begin()
            body_block = greeting_block.next()
            self._set_block_style(greeting_block, "title")
            self._format_block(greeting_block, title, clear_overrides=True)
            self._set_block_style(body_block, "normal_text")
            self._format_block(body_block, normal, clear_overrides=True)
        finally:
            cursor.endEditBlock()
            self._mutating = False
        self.editor.setTextCursor(cursor)
        self.editor.setCurrentCharFormat(normal_format)
        self._sync_active_set()

    def selected_style(self) -> str | None:
        styles = {_style_key(block) for block in self._target_blocks()}
        return next(iter(styles)) if len(styles) == 1 else "mixed"

    def _definition_from_format(self, fmt: QTextCharFormat) -> StyleDefinition:
        families = fmt.fontFamilies() or []
        family = str(families[0]).strip() if families else ""
        family = family or self.editor.document().defaultFont().family().strip()
        family = family or self.active_set.get("normal_text").font_family
        size = fmt.fontPointSize()
        if size <= 0:
            size = self.editor.document().defaultFont().pointSizeF()
        if size <= 0:
            size = self.active_set.get("normal_text").font_size
        size = min(100.0, max(1.0, float(size)))
        if fmt.hasProperty(QTextFormat.ForegroundBrush):
            color = fmt.foreground().color()
        else:
            color = self.editor.palette().color(QPalette.Text)
        if not color.isValid():
            color = QColor(self.active_set.get("normal_text").font_color)
        color_value = color.name(QColor.HexArgb if color.alpha() < 255 else QColor.HexRgb)
        return StyleDefinition(
            font_family=family,
            font_size=size,
            font_color=color_value,
            font_weight=max(1, min(1000, fmt.fontWeight() or 400)),
            italic=fmt.fontItalic(),
            underline=fmt.fontUnderline(),
            strikethrough=fmt.fontStrikeOut(),
        )

    def _sample_definition(self) -> StyleDefinition | None:
        cursor = self.editor.textCursor()
        if not cursor.hasSelection():
            return replace(
                self._definition_from_format(self.editor.currentCharFormat()),
                paragraph=_paragraph_definition(cursor.block()),
            )
        # A heading can contain links or emphasized words. Those runs must not
        # disable Update to Match. Sample its first selected character and
        # paragraph consistently, regardless of selection direction.
        sample = QTextCursor(self.editor.document())
        sample.setPosition(cursor.selectionStart())
        block = sample.block()
        if block.length() > 1:
            sample.movePosition(QTextCursor.NextCharacter, QTextCursor.KeepAnchor)
        return replace(
            self._definition_from_format(sample.charFormat()),
            paragraph=_paragraph_definition(block),
        )

    def can_sample(self) -> bool:
        return self._sample_definition() is not None

    def update_style(self, key: str) -> None:
        self.active_set.get(key)
        sampled = self._sample_definition()
        if sampled is None:
            raise ValueError("Select uniformly formatted text or place the cursor in representative text")
        updated = self.active_set.update_style(key, sampled)
        cursor = self.editor.textCursor()
        source_range = (cursor.selectionStart(), cursor.selectionEnd())
        if updated == self.active_set and not self._has_redundant_source_override(
            key, source_range, sampled
        ):
            return
        self._load_definitions(
            updated,
            promote_key=key,
            source_range=source_range,
        )

    def _has_redundant_source_override(
        self,
        key: str,
        source_range: tuple[int, int],
        baseline: StyleDefinition,
    ) -> bool:
        for block in self._target_blocks():
            if _style_key(block) != key:
                continue
            for start, end, fmt in self._text_fragments(block):
                bits = _override_bits(fmt)
                if (
                    bits
                    and self._intersects_source(start, end, source_range)
                    and self._remove_redundant_bits(fmt, baseline, bits) != bits
                ):
                    return True
        cursor = self.editor.textCursor()
        if not cursor.hasSelection() and _style_key(cursor.block()) == key:
            fmt = self.editor.currentCharFormat()
            bits = _override_bits(fmt)
            return bool(
                bits
                and self._remove_redundant_bits(fmt, baseline, bits) != bits
            )
        return False

    def load_set(self, style_set: NamedStyleSet) -> None:
        if not isinstance(style_set, NamedStyleSet):
            raise TypeError("style_set must be a validated NamedStyleSet")
        if style_set == self.active_set:
            return
        # Loading is verbatim: it does not run Normal-text font propagation.
        self._load_definitions(style_set)

    def _load_definitions(
        self,
        style_set: NamedStyleSet,
        *,
        promote_key: str | None = None,
        source_range: tuple[int, int] | None = None,
    ) -> None:
        previous_set = self.active_set

        def change(_work: QTextCursor) -> None:
            self._write_root_set(style_set)
            self.active_set = style_set
            block = self.editor.document().begin()
            while block.isValid():
                key = _style_key(block)
                if key is not None:
                    self._format_block(
                        block,
                        style_set.get(key),
                        clear_overrides=False,
                        apply_paragraph=(
                            style_set.get(key).paragraph != previous_set.get(key).paragraph
                        ),
                        promote_range=(
                            source_range if key == promote_key else None
                        ),
                    )
                block = block.next()

        self._run_edit(change)

    def apply_direct_format(self, fmt: QTextCharFormat) -> None:
        """Apply a toolbar format and mark only the properties it changed."""
        bits = 0
        for name, property_id in (
            ("font_family", QTextFormat.FontFamilies),
            ("font_size", QTextFormat.FontPointSize),
            ("font_color", QTextFormat.ForegroundBrush),
            ("font_weight", QTextFormat.FontWeight),
            ("italic", QTextFormat.FontItalic),
            ("underline", QTextFormat.FontUnderline),
            ("strikethrough", QTextFormat.FontStrikeOut),
        ):
            if fmt.hasProperty(property_id):
                bits |= _BITS[name]
        cursor = QTextCursor(self.editor.textCursor())
        if not cursor.hasSelection():
            self.editor.mergeCurrentCharFormat(fmt)
            if bits:
                marker = QTextCharFormat()
                _set_override_bits(
                    marker, _override_bits(self.editor.currentCharFormat()) | bits
                )
                self.editor.mergeCurrentCharFormat(marker)
            if cursor.block().length() == 1 and _style_key(cursor.block()) is not None:
                block_cursor = QTextCursor(cursor.block())
                block_cursor.setBlockCharFormat(self.editor.currentCharFormat())
            return

        start, end = cursor.selectionStart(), cursor.selectionEnd()

        def change(_work: QTextCursor) -> None:
            cursor.mergeCharFormat(fmt)
            block = self.editor.document().findBlock(start)
            while block.isValid() and block.position() < end:
                for span_start, span_end, old_format in self._text_fragments(block):
                    lo, hi = max(start, span_start), min(end, span_end)
                    if lo >= hi:
                        continue
                    marker = QTextCharFormat()
                    _set_override_bits(marker, _override_bits(old_format) | bits)
                    work = QTextCursor(self.editor.document())
                    work.setPosition(lo)
                    work.setPosition(hi, QTextCursor.KeepAnchor)
                    work.mergeCharFormat(marker)
                block = block.next()

        self._run_edit(change)

    mark_direct_format = apply_direct_format

    def clear_direct_format(self) -> None:
        cursor = self.editor.textCursor()
        if not cursor.hasSelection():
            key = _style_key(cursor.block())
            if key is None:
                self.editor.setCurrentCharFormat(QTextCharFormat())
                return
            fmt = QTextCharFormat(self.editor.currentCharFormat())
            _apply_definition(fmt, self.active_set.get(key))
            _set_override_bits(fmt, 0)
            self.editor.setCurrentCharFormat(fmt)
            if cursor.block().length() == 1:
                QTextCursor(cursor.block()).setBlockCharFormat(fmt)
            return
        start, end = cursor.selectionStart(), cursor.selectionEnd()

        def change(_work: QTextCursor) -> None:
            block = self.editor.document().findBlock(start)
            while block.isValid() and block.position() < end:
                key = _style_key(block)
                for span_start, span_end, old_format in self._text_fragments(block):
                    lo, hi = max(start, span_start), min(end, span_end)
                    if lo >= hi:
                        continue
                    if key is None:
                        fmt = QTextCharFormat()
                    else:
                        fmt = QTextCharFormat(old_format)
                        _apply_definition(fmt, self.active_set.get(key))
                        _set_override_bits(fmt, 0)
                    self._replace_span(lo, hi, fmt, self.editor.document())
                block = block.next()

        self._run_edit(change)

    def handle_enter(self) -> bool:
        """Start Normal text after a nonempty styled heading/title paragraph."""
        cursor = QTextCursor(self.editor.textCursor())
        key = _style_key(cursor.block())
        if (
            key in (None, "normal_text")
            or not cursor.block().text()
            or cursor.currentList() is not None
            or cursor.hasSelection()
        ):
            return False

        def change(_work: QTextCursor) -> None:
            cursor.insertBlock()
            self._set_block_style(cursor.block(), "normal_text")
            self._format_paragraph(cursor.block(), self.active_set.get("normal_text").paragraph)
            fmt = QTextCharFormat(cursor.charFormat())
            _apply_definition(fmt, self.active_set.get("normal_text"))
            _set_override_bits(fmt, 0)
            cursor.setCharFormat(fmt)

        self._run_edit(change)
        self.editor.setTextCursor(cursor)
        self.editor.setCurrentCharFormat(cursor.charFormat())
        return True

    def handle_key_press(self, event) -> bool:
        if (
            event.key() in (Qt.Key_Return, Qt.Key_Enter)
            and event.modifiers() in (Qt.NoModifier, Qt.KeypadModifier)
        ):
            return self.handle_enter()
        return False

    def before_paste(self, source) -> _PasteContext:
        """Begin one undo action for a rich paste and its style reconciliation."""
        cursor = self.editor.textCursor()
        start, end = cursor.selectionStart(), cursor.selectionEnd()
        document = self.editor.document()
        first = document.findBlock(start)
        last = document.findBlock(end)
        work = QTextCursor(self.editor.document())
        work.beginEditBlock()
        self._mutating = True
        return _PasteContext(
            work=work,
            start=start,
            rich=bool(source.hasHtml()),
            prefix_style=_style_key(first),
            suffix_style=_style_key(last),
            prefix_survives=start > first.position(),
            suffix_survives=end < last.position() + last.length() - 1,
        )

    def after_paste(self, context: _PasteContext) -> None:
        try:
            if context.rich:
                start = context.start
                end = max(start, self.editor.textCursor().position())
                document = self.editor.document()
                first = document.findBlock(start)
                last = document.findBlock(end - 1 if end > start else end)
                block = first
                touched: list[QTextBlock] = []
                while block.isValid():
                    touched.append(block)
                    self._set_block_style(block, None)
                    if block == last:
                        break
                    block = block.next()
                if context.prefix_survives:
                    self._set_block_style(first, context.prefix_style)
                if context.suffix_survives and (last != first or not context.prefix_survives):
                    self._set_block_style(last, context.suffix_style)
                for block in touched:
                    key = _style_key(block)
                    if key is not None:
                        self._mark_pasted_overrides(block, start, end, self.active_set.get(key))
        finally:
            context.work.endEditBlock()
            self._mutating = False
            self._sync_active_set()

    def _mark_pasted_overrides(
        self, block: QTextBlock, start: int, end: int, baseline: StyleDefinition
    ) -> None:
        for span_start, span_end, old_format in self._text_fragments(block):
            lo, hi = max(span_start, start), min(span_end, end)
            if lo >= hi:
                continue
            actual = self._definition_from_format(old_format)
            bits = _override_bits(old_format)
            for name, bit in _BITS.items():
                if getattr(actual, name) != getattr(baseline, name):
                    bits |= bit
            marker = QTextCharFormat()
            _set_override_bits(marker, bits)
            cursor = QTextCursor(self.editor.document())
            cursor.setPosition(lo)
            cursor.setPosition(hi, QTextCursor.KeepAnchor)
            cursor.mergeCharFormat(marker)

    def serialize(self) -> dict[str, object]:
        self._sync_active_set()
        blocks: list[dict[str, object]] = []
        block = self.editor.document().begin()
        while block.isValid():
            spans: list[dict[str, object]] = []
            for start, end, fmt in self._text_fragments(block):
                bits = _override_bits(fmt)
                if not bits:
                    continue
                mask = [name for name in STYLE_PROPERTIES if bits & _BITS[name]]
                offset_start, offset_end = start - block.position(), end - block.position()
                if spans and spans[-1]["end"] == offset_start and spans[-1]["mask"] == mask:
                    spans[-1]["end"] = offset_end
                else:
                    spans.append({"start": offset_start, "end": offset_end, "mask": mask})
            blocks.append({"style": _style_key(block), "overrides": spans})
            block = block.next()
        return {
            "schema_version": DOCUMENT_STYLE_SCHEMA_VERSION,
            "definitions": self.active_set.to_dict(),
            "blocks": blocks,
        }

    def restore(self, payload: object) -> None:
        """Restore validated metadata without changing the visible HTML."""
        document = self.editor.document()
        blocks: list[QTextBlock] = []
        block = document.begin()
        while block.isValid():
            blocks.append(block)
            block = block.next()
        canonical = validate_document_style_state(
            payload,
            block_lengths=[max(0, block.length() - 1) for block in blocks],
        )
        definitions = NamedStyleSet.from_dict(canonical["definitions"])
        enabled = document.isUndoRedoEnabled()
        self._mutating = True
        document.setUndoRedoEnabled(False)
        try:
            self._write_root_set(definitions)
            for block, state in zip(blocks, canonical["blocks"]):
                self._set_block_style(block, state["style"])
                for start, end, old_format in self._text_fragments(block):
                    fmt = QTextCharFormat(old_format)
                    _set_override_bits(fmt, 0)
                    self._replace_span(start, end, fmt, document)
                for span in state["overrides"]:
                    bits = sum(_BITS[name] for name in span["mask"])
                    lo, hi = block.position() + span["start"], block.position() + span["end"]
                    marker = QTextCharFormat()
                    _set_override_bits(marker, bits)
                    cursor = QTextCursor(document)
                    cursor.setPosition(lo)
                    cursor.setPosition(hi, QTextCursor.KeepAnchor)
                    cursor.mergeCharFormat(marker)
            self.active_set = definitions
        finally:
            document.setUndoRedoEnabled(enabled)
            self._mutating = False

    def export_html(self) -> str:
        """Return Qt HTML without the root-frame table used for undo state."""
        html = _LIST_ITEM_OPEN.sub(_merge_list_item_styles, self.editor.toHtml())
        opened = _ROOT_TABLE_OPEN.search(html)
        if opened is None:
            return html
        closed = None
        for match in _ROOT_TABLE_CLOSE.finditer(html, opened.end()):
            closed = match
        if closed is None:
            return html
        return (
            html[: opened.start()]
            + opened.group(1)
            + html[opened.end() : closed.start()]
            + closed.group(1)
            + html[closed.end() :]
        )
