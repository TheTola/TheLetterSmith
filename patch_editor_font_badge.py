# patch_editor_font_badge.py
# Purpose: remove the visible Bundled/Fallback/Mixed font badge from Editor.py
# and replace it with a tiny bottom-row info button that only shows details on hover/click.

from __future__ import annotations

from pathlib import Path
import re
import sys


def patch_editor(path: Path) -> None:
    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Editor.py not found: {path}")

    text = path.read_text(encoding="utf-8", errors="ignore")
    original = text

    # Backup once.
    backup = path.with_suffix(path.suffix + ".bak_font_badge")
    if not backup.exists():
        backup.write_text(original, encoding="utf-8")

    # 1) Remove the visible toolbar badge widget construction.
    text = re.sub(
        r"\n        self\.font_export_badge = QLabel\(\"Font\"\)\n"
        r"        self\.font_export_badge\.setObjectName\(\"fontExportBadge\"\)\n"
        r"        self\.font_export_badge\.setAlignment\(Qt\.AlignCenter\)\n"
        r"        self\.font_export_badge\.setMinimumWidth\(78\)\n",
        "\n",
        text,
        count=1,
    )

    # 2) Remove the badge from the format toolbar.
    text = text.replace("\n        self.format_toolbar.addWidget(self.font_export_badge)\n", "\n")

    # 3) Add a tiny bottom-row info button after the bottom stretch.
    old_bottom = (
        "        self.word_label = QLabel()\n"
        "        hb.addWidget(self.word_label)\n"
        "        hb.addStretch()\n"
    )
    new_bottom = (
        "        self.word_label = QLabel()\n"
        "        hb.addWidget(self.word_label)\n"
        "        hb.addStretch()\n"
        "\n"
        "        self.font_info_button = QToolButton(self)\n"
        "        self.font_info_button.setObjectName(\"fontInfoButton\")\n"
        "        self.font_info_button.setText(\"i\")\n"
        "        self.font_info_button.setCursor(Qt.PointingHandCursor)\n"
        "        self.font_info_button.setToolTip(\"Font export info\")\n"
        "        self.font_info_button.clicked.connect(\n"
        "            lambda: QtWidgets.QToolTip.showText(\n"
        "                self.font_info_button.mapToGlobal(QtCore.QPoint(self.font_info_button.width() // 2, 0)),\n"
        "                self.font_info_button.toolTip(),\n"
        "                self.font_info_button,\n"
        "            )\n"
        "        )\n"
        "        hb.addWidget(self.font_info_button)\n"
    )
    if "self.font_info_button = QToolButton" not in text:
        text = text.replace(old_bottom, new_bottom, 1)

    # 4) Replace the old badge updater with a quiet tooltip updater.
    text = re.sub(
        r"\n    def _update_font_export_badge\(self, family: str, \*, mixed: bool = False\) -> None:\n"
        r"(?:        .*\n)+?"
        r"        self\.font_export_badge\.style\(\)\.polish\(self\.font_export_badge\)\n",
        """
    def _update_font_export_badge(self, family: str, *, mixed: bool = False) -> None:
        \"\"\"
        Kept for existing sync calls, but no longer displays a toolbar badge.
        It only updates the tiny bottom info button tooltip.
        \"\"\"
        if not hasattr(self, "font_info_button"):
            return

        if mixed:
            detail = "Mixed font families selected."
        else:
            try:
                _state, detail = describe_font_export_family(family)
            except Exception:
                detail = f"Font: {family}"

        self.font_info_button.setToolTip(detail)
""",
        text,
        count=1,
    )

    # 5) Remove old visible badge stylesheet lines and add tiny info-button style.
    text = re.sub(
        r'\n            "QLabel#fontExportBadge\{[^\n]+\}\"'
        r'\n            "QLabel#fontExportBadge\[exportState=\'bundled\'\]\{[^\n]+\}\"'
        r'\n            "QLabel#fontExportBadge\[exportState=\'fallback\'\]\{[^\n]+\}\"'
        r'\n            "QLabel#fontExportBadge\[exportState=\'mixed\'\]\{[^\n]+\}\"',
        "",
        text,
        count=1,
    )

    style_insert = (
        '            "QToolButton#fontInfoButton{min-width:18px;max-width:18px;min-height:18px;max-height:18px;border-radius:9px;padding:0;color:#8290a3;border:1px solid #2f3744;background:#161a20;font-size:10px;font-weight:700;}"\n'
        '            "QToolButton#fontInfoButton:hover{color:#e6edf6;border-color:#536477;background:#202833;}"\n'
    )
    if "QToolButton#fontInfoButton" not in text:
        text = text.replace('            "QCheckBox{color:#ddd;}"\n', style_insert + '            "QCheckBox{color:#ddd;}"\n', 1)

    if text == original:
        print("No changes were made. The file may already be patched or the layout is different.")
        return

    path.write_text(text, encoding="utf-8")
    print(f"Patched: {path}")
    print(f"Backup:  {backup}")


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("Editor.py")
    patch_editor(target)
