"""Page-local controls for the foundation's versioned text geometry."""
from __future__ import annotations

from copy import deepcopy

from PySide6 import QtWidgets

from project_model import ProjectLayoutError, resolve_page_layout


class MessageLayoutDialog(QtWidgets.QDialog):
    def __init__(self, parent, page, settings, shared_settings, label, commit):
        super().__init__(parent)
        self.setWindowTitle(f"Text Layout — {label}")
        self.page = deepcopy(page)
        self.settings = deepcopy(settings)
        self.shared_settings = deepcopy(shared_settings)
        self.commit = commit
        self._resolved = None
        problem = ""
        try:
            resolved = resolve_page_layout(page, settings)
            self._resolved = resolved["message"]
            canvas = (resolved["artwork"] or {}).get("canvas", {"width": 400, "height": 600})
        except ProjectLayoutError as error:
            canvas = {"width": 400, "height": 600}
            problem = f"Saved layout cannot be rendered: {error} Applying a custom layout replaces its text geometry."
        initial = self._resolved or {
            "version": 1, "units": "css_px", "canvas": canvas,
            "box": {"x": 0, "y": 0, **canvas},
            "padding": {side: 0 for side in ("top", "right", "bottom", "left")},
            "text_align": "inherit", "overflow": "scroll",
        }
        layout = QtWidgets.QVBoxLayout(self)
        self.scope = QtWidgets.QLabel()
        layout.addWidget(self.scope)
        self.custom = QtWidgets.QCheckBox("Customize this page")
        self.custom.setChecked(bool(page["message"]["layout"]) or "message_layout" in page["design_overrides"])
        layout.addWidget(self.custom)
        self.fields = QtWidgets.QWidget()
        grid = QtWidgets.QGridLayout(self.fields)
        grid.setContentsMargins(0, 0, 0, 0)
        self.values = {}
        definitions = (
            ("canvas", "width", "Canvas width", 1), ("canvas", "height", "Canvas height", 1),
            ("box", "x", "Text left", 0), ("box", "y", "Text top", 0),
            ("box", "width", "Text width", 1), ("box", "height", "Text height", 1),
            ("padding", "left", "Left padding", 0), ("padding", "right", "Right padding", 0),
            ("padding", "top", "Top padding", 0), ("padding", "bottom", "Bottom padding", 0),
        )
        for index, (group, key, title, minimum) in enumerate(definitions):
            spin = QtWidgets.QDoubleSpinBox()
            spin.setRange(minimum, 16384)
            spin.setDecimals(4)
            spin.setSuffix(" px")
            spin.setValue(initial[group][key])
            spin.setAccessibleName(title)
            spin.setObjectName(f"{group}_{key}")
            label_widget = QtWidgets.QLabel(title)
            label_widget.setBuddy(spin)
            row, column = index // 2, (index % 2) * 2
            grid.addWidget(label_widget, row, column)
            grid.addWidget(spin, row, column + 1)
            self.values[(group, key)] = spin
        self.alignment = QtWidgets.QComboBox()
        self.alignment.setAccessibleName("Text area alignment")
        for key, title in (("inherit", "Document alignment"), ("left", "Left"), ("center", "Center"),
                           ("right", "Right"), ("justify", "Justify"), ("start", "Start"), ("end", "End")):
            self.alignment.addItem(title, key)
        self.alignment.setCurrentIndex(self.alignment.findData(initial["text_align"]))
        grid.addWidget(QtWidgets.QLabel("Alignment"), 5, 0)
        grid.addWidget(self.alignment, 5, 1, 1, 3)
        layout.addWidget(self.fields)
        note = QtWidgets.QLabel("Sizes use logical pixels. Preview zoom does not change them. Long messages scroll inside the text area.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.error = QtWidgets.QLabel(problem)
        self.error.setWordWrap(True)
        self.error.setAccessibleName("Text layout validation")
        layout.addWidget(self.error)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Cancel)
        self.apply_button = buttons.addButton("Apply", QtWidgets.QDialogButtonBox.AcceptRole)
        self.default_button = buttons.addButton("Use project default", QtWidgets.QDialogButtonBox.ResetRole)
        self.default_button.clicked.connect(lambda: self.custom.setChecked(False))
        buttons.accepted.connect(self.apply)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._initial_values = {key: spin.value() for key, spin in self.values.items()}
        self._initial_alignment = self.alignment.currentData()
        self.custom.toggled.connect(self._scope_changed)
        self._scope_changed(self.custom.isChecked())

    def _scope_changed(self, custom):
        self.fields.setEnabled(custom)
        self.fields.setVisible(custom)
        self.default_button.setEnabled(custom)
        self.scope.setText("Customized for this page" if custom else "Using project text layout")

    def candidate(self):
        """Keep untouched inherited fields absent from the authored override."""
        if not self.custom.isChecked():
            return {}
        if self._resolved is None:
            result = {"version": 1, "units": "css_px", "canvas": {}, "box": {}, "padding": {},
                      "overflow": "scroll", "text_align": self.alignment.currentData()}
            for (group, key), spin in self.values.items():
                result[group][key] = spin.value()
            return result
        result = deepcopy(self.page["message"]["layout"])
        for (group, key), spin in self.values.items():
            if spin.value() != self._initial_values[(group, key)]:
                result.setdefault(group, {})[key] = spin.value()
        if self.alignment.currentData() != self._initial_alignment:
            result["text_align"] = self.alignment.currentData()
        return result

    def apply(self):
        geometry = self.candidate()
        use_default = not self.custom.isChecked()
        page = deepcopy(self.page)
        page["message"]["layout"] = geometry
        settings = deepcopy(self.settings)
        if use_default:
            settings.pop("message_layout", None)
            if "message_layout" in self.shared_settings:
                settings["message_layout"] = deepcopy(self.shared_settings["message_layout"])
        try:
            resolve_page_layout(page, settings)
            self.commit(geometry, use_default=use_default)
        except (ValueError, RuntimeError, OSError, KeyError) as error:
            self.error.setText(str(error))
            return
        self.accept()
