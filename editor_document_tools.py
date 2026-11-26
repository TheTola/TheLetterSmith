"""Operations on the editor's native rich-text document."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRectF
from PySide6.QtGui import QTextCursor


def link_spans(document):
    """Group the runs of each link while keeping separate occurrences."""
    spans = []
    block = document.begin()
    while block.isValid():
        for iterator in block:
            fragment = iterator.fragment()
            if not fragment.isValid():
                continue
            fmt = fragment.charFormat()
            href = fmt.anchorHref() if fmt.isAnchor() else ""
            if not href:
                continue
            start = fragment.position()
            end = start + fragment.length()
            if spans and spans[-1][1:] == (start, href):
                spans[-1] = (spans[-1][0], end, href)
            else:
                spans.append((start, end, href))
        block = block.next()
    return spans


def visible_text_runs(editor):
    """Yield formatted runs and their visible bounds in the viewport."""
    document = editor.document()
    document_layout = document.documentLayout()
    viewport = QRectF(editor.viewport().rect())
    horizontal = editor.horizontalScrollBar().value()
    vertical = editor.verticalScrollBar().value()
    block = editor.cursorForPosition(QPoint(0, 0)).block()
    while block.isValid():
        bounds = document_layout.blockBoundingRect(block).translated(-horizontal, -vertical)
        if bounds.top() > viewport.bottom():
            break
        layout = block.layout()
        origin = block.position()
        for row in range(layout.lineCount()):
            line = layout.lineAt(row)
            y = bounds.top() + line.y()
            if y + line.height() < 0 or y > viewport.bottom():
                continue
            start = origin + line.textStart()
            end = start + line.textLength()
            for iterator in block:
                fragment = iterator.fragment()
                if not fragment.isValid():
                    continue
                lo = max(start, fragment.position())
                hi = min(end, fragment.position() + fragment.length())
                fmt = fragment.charFormat()
                if hi <= lo or fmt.isImageFormat() or fragment.text().isspace():
                    continue
                left, right = sorted((line.cursorToX(lo - origin)[0], line.cursorToX(hi - origin)[0]))
                rect = QRectF(bounds.left() + left, y, right - left, line.height()).intersected(viewport)
                if not rect.isEmpty():
                    yield lo, hi, fmt, rect
        block = block.next()


def _list_move(editor, cursor, direction):
    if cursor.isNull():
        return None
    block = editor.document().findBlock(cursor.selectionStart())
    if not block.isValid():
        return None
    items = block.textList()
    if items is None:
        return None
    neighbor_index = items.itemNumber(block) + direction
    if not 0 <= neighbor_index < items.count():
        return None
    neighbor = items.item(neighbor_index)
    first, second = (neighbor, block) if direction < 0 else (block, neighbor)
    frame = QTextCursor(first).currentFrame()
    if QTextCursor(second).currentFrame() != frame:
        return None
    indent = items.format().indent()

    def is_child(candidate):
        nested = candidate.textList() if candidate.isValid() else None
        return (nested is not None and nested.format().indent() > indent
                and QTextCursor(candidate).currentFrame() == frame)

    blocks = [first]
    child = first.next()
    while is_child(child):
        blocks.append(child)
        child = child.next()
    if child != second:
        return None
    pivot = len(blocks)
    blocks.append(second)
    child = second.next()
    while is_child(child):
        blocks.append(child)
        child = child.next()
    last = blocks[-1] if direction < 0 else blocks[pivot - 1]
    if cursor.selectionEnd() > last.position() + last.length() - 1:
        return None
    return blocks, pivot


def can_move_list_item(editor, direction):
    return _list_move(editor, editor.textCursor(), direction) is not None


def move_list_item(editor, direction):
    cursor = editor.textCursor()
    move = _list_move(editor, cursor, direction)
    if move is None:
        return False
    blocks, pivot = move
    active = blocks[pivot] if direction < 0 else blocks[0]
    anchor = cursor.anchor() - active.position()
    position = cursor.position() - active.position()
    snapshots = []
    for block in blocks[pivot:] + blocks[:pivot]:
        runs = [(fragment.text(), fragment.charFormat()) for iterator in block
                if (fragment := iterator.fragment()).isValid()]
        snapshots.append((block.blockFormat(), QTextCursor(block).blockCharFormat(), runs))

    cursor.beginEditBlock()
    try:
        # Keep the paragraph slots and move their content and list formats.
        # Qt numbers each list in its new document order.
        for block, (block_format, char_format, runs) in zip(blocks, snapshots):
            item = QTextCursor(block)
            item.movePosition(QTextCursor.EndOfBlock, QTextCursor.KeepAnchor)
            item.removeSelectedText()
            item.setBlockFormat(block_format)
            item.setBlockCharFormat(char_format)
            for text, fmt in runs:
                item.insertText(text, fmt)
    finally:
        cursor.endEditBlock()
    moved = blocks[0] if direction < 0 else blocks[len(blocks) - pivot]
    cursor.setPosition(moved.position() + anchor)
    cursor.setPosition(moved.position() + position, QTextCursor.KeepAnchor)
    editor.setTextCursor(cursor)
    editor.ensureCursorVisible()
    return True
