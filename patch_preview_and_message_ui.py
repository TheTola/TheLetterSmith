#!/usr/bin/env python3
"""Apply the final Message-tab and shared-preview behavior changes.

Changes:
- Image previews never fade while the current tab remains active.
- Switching tabs still clears/replaces the shared preview normally.
- Message HTML updates no longer replace the rendered image with an HTML view.
- Removes explanatory text/tooltips from the four Message background modes.
- Makes the Text background panel compact.

Usage:
    python patch_preview_and_message_ui.py
    python patch_preview_and_message_ui.py /path/to/letter-smith
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path


def _write_with_backup(path: Path, updated: str, suffix: str) -> None:
    original = path.read_text(encoding="utf-8")
    if updated == original:
        print(f"No change needed: {path.name}")
        return

    ast.parse(updated, filename=str(path))
    backup = path.with_suffix(path.suffix + suffix)
    if not backup.exists():
        backup.write_text(original, encoding="utf-8")

    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(updated, encoding="utf-8")
    temp.replace(path)
    print(f"Patched: {path}")
    print(f"Backup:  {backup}")


def patch_nexus(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"Nexus.py not found: {path}")

    text = path.read_text(encoding="utf-8")
    updated = text

    # Keep the rendered Message image in the shared preview. MessageTab still
    # emits text_selected for its internal consumers, but Nexus no longer swaps
    # the top preview to raw HTML whenever that signal fires.
    updated = updated.replace(
        "        self.message_tab.text_selected.connect(self._show_html)\n",
        "        # The Message tab owns its rendered image preview. Raw HTML updates\n"
        "        # must not replace an image that is already visible on this tab.\n",
        1,
    )

    timer_pattern = re.compile(
        r"\n(?P<indent>\s*)# gentle fade after 30s idle\n"
        r"(?P=indent)self\._fade_timer = QtCore\.QTimer\(self\)\n"
        r"(?P=indent)self\._fade_timer\.setSingleShot\(True\)\n"
        r"(?P=indent)self\._fade_timer\.timeout\.connect\(self\._fade_preview\)\n"
        r"(?P=indent)self\._fade_timer\.start\(30000\)\n",
        re.MULTILINE,
    )
    updated, count_a = timer_pattern.subn(
        lambda match: (
            "\n"
            + match.group("indent")
            + "# Keep this image visible until a newer preview replaces it or the user leaves the tab.\n"
        ),
        updated,
        count=1,
    )

    if count_a == 0:
        timer_pattern_no_comment = re.compile(
            r"\n(?P<indent>\s*)self\._fade_timer = QtCore\.QTimer\(self\)\n"
            r"(?P=indent)self\._fade_timer\.setSingleShot\(True\)\n"
            r"(?P=indent)self\._fade_timer\.timeout\.connect\(self\._fade_preview\)\n"
            r"(?P=indent)self\._fade_timer\.start\(30000\)\n",
            re.MULTILINE,
        )
        updated, count_b = timer_pattern_no_comment.subn(
            lambda match: (
                "\n"
                + match.group("indent")
                + "# Keep this image visible until a newer preview replaces it or the user leaves the tab.\n"
            ),
            updated,
            count=1,
        )
    else:
        count_b = 0

    if count_a + count_b == 0 and "Keep this image visible until" not in updated:
        raise RuntimeError("Could not find the 30-second preview timer in Nexus.py.")

    _write_with_backup(path, updated, ".bak_persistent_preview")


def patch_message_tab(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"Message_tab.py not found: {path}")

    text = path.read_text(encoding="utf-8")
    updated = text

    info_pattern = re.compile(
        r"MESSAGE_OVERLAY_PRESET_INFO:\s*dict\[str,\s*tuple\[str,\s*str\]\]\s*=\s*\{.*?\n\}\n",
        re.DOTALL,
    )
    labels = (
        'MESSAGE_OVERLAY_PRESET_LABELS: dict[str, str] = {\n'
        '    "paper": "Warm Paper",\n'
        '    "black": "Dark Panel",\n'
        '    "white": "Light Panel",\n'
        '    "clear": "Transparent",\n'
        '}\n'
    )
    updated, _ = info_pattern.subn(labels, updated, count=1)

    updated = updated.replace("panel.setMaximumHeight(118)", "panel.setMaximumHeight(86)", 1)
    updated = updated.replace("root.setColumnStretch(2, 1)", "root.setColumnStretch(1, 1)", 1)

    combo_pattern = re.compile(
        r'        for key in \("paper", "black", "white", "clear"\):\n'
        r'            display_name, description = MESSAGE_OVERLAY_PRESET_INFO\[key\]\n'
        r'            self\.overlay_preset_combo\.addItem\(display_name, key\)\n'
        r'            index = self\.overlay_preset_combo\.count\(\) - 1\n'
        r'            self\.overlay_preset_combo\.setItemData\(index, description, Qt\.ToolTipRole\)\n'
    )
    updated = combo_pattern.sub(
        '        for key in ("paper", "black", "white", "clear"):\n'
        '            self.overlay_preset_combo.addItem(MESSAGE_OVERLAY_PRESET_LABELS[key], key)\n',
        updated,
        count=1,
    )

    description_widget_pattern = re.compile(
        r'        self\.overlay_description = QtWidgets\.QLabel\(panel\)\n'
        r'        self\.overlay_description\.setWordWrap\(True\)\n'
        r'        self\.overlay_description\.setStyleSheet\([^\n]*\)\n'
        r'        self\.overlay_description\.setMinimumWidth\(280\)\n'
        r'        root\.addWidget\(self\.overlay_description, 0, 2, 2, 1\)\n\n'
    )
    updated = description_widget_pattern.sub("", updated, count=1)

    description_sync_pattern = re.compile(
        r'\n        if hasattr\(self, "overlay_description"\):\n'
        r'            _display_name, description = MESSAGE_OVERLAY_PRESET_INFO\.get\(\n'
        r'                self\.overlay_preset,\n'
        r'                MESSAGE_OVERLAY_PRESET_INFO\[DEFAULT_MESSAGE_OVERLAY_PRESET\],\n'
        r'            \)\n'
        r'            self\.overlay_description\.setText\(description\)\n'
    )
    updated = description_sync_pattern.sub("\n", updated, count=1)

    if "overlay_description" in updated:
        raise RuntimeError("Message_tab.py still contains an overlay description widget.")
    if "MESSAGE_OVERLAY_PRESET_INFO" in updated:
        raise RuntimeError("Message_tab.py still contains mode-description data.")

    _write_with_backup(path, updated, ".bak_compact_background_modes")


def main() -> None:
    root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd().resolve()
    patch_nexus(root / "Nexus.py")
    patch_message_tab(root / "Message_tab.py")
    print("\nDone.")
    print("- Preview images remain visible until the user leaves the tab or another image replaces them.")
    print("- Message background modes contain no explanatory descriptions or description tooltips.")


if __name__ == "__main__":
    main()
