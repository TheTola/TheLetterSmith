from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path
from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt, QUrl

import generate
from config import (
    MESSAGE_HTML_FILE,
    MUSIC_FILE,
    OUTPUT_PLAY_DIR,
    PLAY_METADATA_FILE,
    PUBLISHED_PAGE_URL_KEY,
    REQUIRED_SLIDES,
    USER_MESSAGE_DIR,
    USER_PAGES_DIR,
    USER_SOUNDS_DIR,
    ensure_output_dirs,
)
from message_html import read_text_normalized
from project_state import (
    PROJECT_ID_KEY,
    PROJECT_SCHEMA_KEY,
    PROJECT_SCHEMA_VERSION,
    atomic_write_settings,
    ensure_project_identity,
)
from publishing import GitHubPagesPublisher
from publishing.github_pages import PUBLIC_WARNING_KEY
from readiness import ReadinessResult, evaluate_readiness
from saved_letters import SavedLetter, SavedLetterCatalog, update_saved_metadata
from settings_store import SettingsStore
from sound_model import (
    ARCHIVE_DIR_NAME,
    BUILD_SOUND_MANIFEST_NAME,
    ProjectSoundState,
    import_runtime_track,
    load_library,
    save_project_state,
    sync_current_compatibility,
)
from transactional_io import PathTransaction, create_staging_directory


PREVIEW_MODE_KEY = "forge_preview_mode"
PREVIEW_MODES = (
    ("Portrait", "portrait"),
    ("Landscape", "landscape"),
    ("Window / Browser", "window"),
)
RESTORABLE_SETTING_KEYS = (
    "starting_volume",
    "music_volume",
    "curtain_style",
    "message_overlay_preset",
    "message_overlay_opacity",
)


def _normalize_page_url(value: str) -> str:
    candidate = (value or "").strip()
    if not candidate:
        return ""
    parsed = QUrl.fromUserInput(candidate)
    if (
        not parsed.isValid()
        or parsed.scheme().lower() not in {"http", "https"}
        or not parsed.host()
    ):
        return ""
    return parsed.toString()


def _read_metadata(play_dir: Path) -> dict:
    for name in (
        PLAY_METADATA_FILE,
        "play_metadata.json",
        "recovery_metadata.json",
        "metadata.json",
    ):
        path = play_dir / name
        if not path.is_file():
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            return value
    return {}


class ReadinessWindow(QtWidgets.QDialog):
    fix_requested = QtCore.Signal(str)

    def __init__(self, project_root: str | Path, parent=None) -> None:
        super().__init__(parent)
        self.project_root = Path(project_root).resolve()
        self.user_closed = False
        self._positioned = False
        self.setWindowTitle("Project Readiness")
        self.setWindowFlag(Qt.Tool, True)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, False)
        self.setMinimumWidth(270)
        self.setMaximumWidth(340)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        self.percentage = QtWidgets.QLabel()
        self.percentage.setStyleSheet(
            "color:#dffcff;font:600 13px 'Segoe UI';"
        )
        layout.addWidget(self.percentage)

        self.status = QtWidgets.QLabel()
        self.status.setStyleSheet("font:600 11px 'Segoe UI';")
        layout.addWidget(self.status)

        self.items = QtWidgets.QWidget(self)
        self.items_layout = QtWidgets.QVBoxLayout(self.items)
        self.items_layout.setContentsMargins(0, 2, 0, 0)
        self.items_layout.setSpacing(2)
        layout.addWidget(self.items)

        self._missing_buttons: dict[str, QtWidgets.QPushButton] = {}
        for item in evaluate_readiness(self.project_root).items:
            button = QtWidgets.QPushButton()
            button.setFlat(True)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(
                lambda _checked=False, key=item.key: self.fix_requested.emit(key)
            )
            self.items_layout.addWidget(button)
            self._missing_buttons[item.key] = button

    def refresh(self, result: ReadinessResult) -> None:
        self.percentage.setText(f"{result.completion_percentage}% Complete")
        ready = result.status != "Not Ready"
        self.status.setText(result.status)
        self.status.setStyleSheet(
            f"color:{'#79e092' if ready else '#ff8080'};"
            "font:600 11px 'Segoe UI';"
        )

        missing = {item.key: item for item in result.missing_items}
        for key, button in self._missing_buttons.items():
            item = missing.get(key)
            button.setVisible(item is not None)
            if item is None:
                continue
            required = item.required
            button.setText(f"{'Missing' if required else 'Optional'}: {item.label}")
            button.setToolTip(item.detail)
            button.setStyleSheet(
                "QPushButton{text-align:left;padding:3px 2px;border:none;"
                f"background:transparent;color:{'#ff9090' if required else '#e4c96d'};}}"
                "QPushButton:hover{text-decoration:underline;}"
            )

        if not missing:
            self.items.setVisible(False)
        else:
            self.items.setVisible(True)
        self.adjustSize()

    def position_near_image_area(self) -> None:
        if self._positioned:
            return
        owner = self.parentWidget()
        if owner is None:
            return
        top_left = owner.mapToGlobal(QtCore.QPoint(24, 105))
        screen = owner.screen().availableGeometry() if owner.screen() else None
        target = QtCore.QPoint(top_left.x(), top_left.y())
        if screen is not None:
            target.setX(
                max(screen.left(), min(target.x(), screen.right() - self.width()))
            )
            target.setY(
                max(screen.top(), min(target.y(), screen.bottom() - self.height()))
            )
        self.move(target)
        self._positioned = True

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self.user_closed = True
        super().closeEvent(event)


class ForgeTab(QtWidgets.QWidget):
    letter_loaded = QtCore.Signal(dict)
    fix_requested = QtCore.Signal(str)
    preview_requested = QtCore.Signal(str, str)
    published_url_changed = QtCore.Signal(str)

    def __init__(self, project_root: str | Path) -> None:
        super().__init__()
        self.project_root = Path(project_root).resolve()
        self.settings = SettingsStore(self.project_root)
        self.catalog = SavedLetterCatalog(self.project_root)
        self.saved_page_url = ""
        self._last_play_dir: Optional[Path] = None
        self._preview_mode = self._saved_preview_mode()
        self._readiness_result = evaluate_readiness(self.project_root)
        self.readiness_window = ReadinessWindow(self.project_root, self.window())
        self.readiness_window.fix_requested.connect(self.fix_requested.emit)
        self._init_ui()
        self.refresh_saved_letters()
        self.refresh_saved_page_url()
        self.refresh_readiness()

    def _saved_preview_mode(self) -> str:
        value = str(self.settings.get(PREVIEW_MODE_KEY, "portrait")).strip()
        valid = {mode for _label, mode in PREVIEW_MODES}
        return value if value in valid else "portrait"

    def _init_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(32, 16, 32, 18)
        layout.setSpacing(12)

        heading_row = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("Forge")
        title.setStyleSheet("color:#00d0ff;font:600 20px 'Segoe UI';")
        heading_row.addWidget(title)
        heading_row.addStretch(1)
        self.readiness_btn = self._small_button("Project Readiness")
        self.readiness_btn.clicked.connect(self.show_readiness_window)
        heading_row.addWidget(self.readiness_btn)
        layout.addLayout(heading_row)

        saved_row = QtWidgets.QHBoxLayout()
        saved_row.addWidget(QtWidgets.QLabel("Saved letter:"))
        self.saved_selector = QtWidgets.QComboBox()
        self.saved_selector.setMinimumWidth(420)
        self.saved_selector.setSizeAdjustPolicy(
            QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon
        )
        saved_row.addWidget(self.saved_selector, 1)
        self.load_saved_btn = self._small_button("Load Saved Letter")
        self.load_saved_btn.clicked.connect(self.load_selected_letter)
        saved_row.addWidget(self.load_saved_btn)
        self.refresh_saved_btn = self._small_button("Refresh")
        self.refresh_saved_btn.clicked.connect(self.refresh_saved_letters)
        saved_row.addWidget(self.refresh_saved_btn)
        layout.addLayout(saved_row)

        preview_row = QtWidgets.QHBoxLayout()
        preview_row.addWidget(QtWidgets.QLabel("Preview format:"))
        self.preview_mode = QtWidgets.QComboBox()
        for label, mode in PREVIEW_MODES:
            self.preview_mode.addItem(label, mode)
        current = self.preview_mode.findData(self._preview_mode)
        self.preview_mode.setCurrentIndex(max(0, current))
        self.preview_mode.currentIndexChanged.connect(self._preview_mode_changed)
        preview_row.addWidget(self.preview_mode)
        preview_row.addStretch(1)
        layout.addLayout(preview_row)

        actions = QtWidgets.QHBoxLayout()
        self.preview_btn = self._action_button(
            "Preview Letter", "#d77b00", "#ffad24"
        )
        self.preview_btn.clicked.connect(self.preview_letter)
        actions.addWidget(self.preview_btn)
        self.publish_btn = self._action_button(
            "Publish Letter", "#6551c9", "#8b77ed"
        )
        self.publish_btn.clicked.connect(self.publish_letter)
        actions.addWidget(self.publish_btn)
        self.open_published_btn = self._action_button(
            "Open Published Letter", "#17202a", "#536779"
        )
        self.open_published_btn.clicked.connect(self.open_published_letter)
        actions.addWidget(self.open_published_btn)
        layout.addLayout(actions)

        share = QtWidgets.QFrame()
        share.setObjectName("SharePanel")
        share.setStyleSheet(
            "QFrame#SharePanel{background:#111820;border:1px solid #2b4655;"
            "border-radius:7px;}QLabel{border:none;}"
        )
        share_layout = QtWidgets.QHBoxLayout(share)
        share_layout.setContentsMargins(10, 8, 10, 8)
        share_layout.addWidget(QtWidgets.QLabel("Published link:"))
        self.published_url = QtWidgets.QLineEdit()
        self.published_url.setReadOnly(True)
        self.published_url.setPlaceholderText("Publish the letter or save its URL in Message")
        share_layout.addWidget(self.published_url, 1)
        self.copy_link_btn = self._small_button("Copy Link")
        self.copy_link_btn.clicked.connect(self.copy_published_link)
        share_layout.addWidget(self.copy_link_btn)
        layout.addWidget(share)

        self.status = QtWidgets.QPlainTextEdit()
        self.status.setReadOnly(True)
        self.status.setMaximumHeight(74)
        self.status.setStyleSheet(
            "background:#15191d;border:1px solid #2b4655;border-radius:6px;"
            "color:#d9e4ed;padding:6px;"
        )
        layout.addWidget(self.status)
        layout.addStretch(1)
        self._log("Ready.")

    def show_readiness_window(self) -> None:
        self.refresh_readiness()
        self.readiness_window.user_closed = False
        self.readiness_window.show()
        self.readiness_window.position_near_image_area()
        self.readiness_window.raise_()

    def attach_readiness_window(self, owner: QtWidgets.QWidget) -> None:
        self.readiness_window.setParent(owner, Qt.Tool)
        self.readiness_window._positioned = False

    def refresh_readiness(self) -> ReadinessResult:
        self._readiness_result = evaluate_readiness(self.project_root)
        self.readiness_window.refresh(self._readiness_result)
        self.readiness_btn.setText(
            f"Project Readiness · {self._readiness_result.completion_percentage}%"
        )
        self.preview_btn.setEnabled(self._readiness_result.can_preview)
        self.publish_btn.setEnabled(self._readiness_result.can_preview)
        return self._readiness_result

    def refresh_saved_letters(self) -> None:
        selected_path = ""
        current = self.saved_selector.currentData()
        if isinstance(current, SavedLetter):
            selected_path = str(current.path)
        self.saved_selector.clear()
        selected_index = -1
        for index, entry in enumerate(self.catalog.list_entries()):
            date = entry.modified_at.strftime("%Y-%m-%d %H:%M")
            published = "Published" if entry.published else "Local"
            recovery = "Recovery · " if entry.recovery else ""
            self.saved_selector.addItem(
                f"{recovery}{entry.title} — {entry.recipient} · {date} · {published}",
                entry,
            )
            if str(entry.path) == selected_path:
                selected_index = index
        if selected_index >= 0:
            self.saved_selector.setCurrentIndex(selected_index)
        available = self.saved_selector.count() > 0
        self.saved_selector.setEnabled(available)
        self.load_saved_btn.setEnabled(available)
        if not available:
            self.saved_selector.addItem("No saved letters found")

    def load_selected_letter(self) -> None:
        entry = self.saved_selector.currentData()
        if isinstance(entry, SavedLetter):
            self._load_saved_letter(entry)

    def _load_saved_letter(self, entry: SavedLetter) -> None:
        play_dir = entry.path.resolve()
        src_pages = play_dir / "gallery" / "pages"
        src_message = play_dir / "gallery" / "message"
        src_sounds = play_dir / "gallery" / "sounds"
        missing_pages = [
            name for name in REQUIRED_SLIDES if not (src_pages / name).is_file()
        ]
        if not (play_dir / "index.html").is_file() or missing_pages:
            detail = ", ".join(missing_pages) or "index.html"
            self._log(f"Could not load the saved letter. Missing: {detail}.")
            return
        if not src_message.is_dir() or not (src_message / "message.html").is_file():
            self._log("Could not load the saved letter. Its message is missing.")
            return

        metadata = _read_metadata(play_dir)
        staged_root = create_staging_directory(
            self.project_root / "output", prefix=".letter-load-"
        )
        dst_pages = self.project_root / USER_PAGES_DIR
        dst_message = self.project_root / USER_MESSAGE_DIR
        dst_archive = self.project_root / USER_SOUNDS_DIR / ARCHIVE_DIR_NAME
        dst_music = self.project_root / USER_SOUNDS_DIR / MUSIC_FILE
        pages_tx = PathTransaction(
            dst_pages, staging_suffix=".load-staging", backup_suffix=".load-backup"
        )
        message_tx = PathTransaction(
            dst_message, staging_suffix=".load-staging", backup_suffix=".load-backup"
        )
        archive_tx = PathTransaction(
            dst_archive, staging_suffix=".load-staging", backup_suffix=".load-backup"
        )
        music_tx = PathTransaction(
            dst_music, staging_suffix=".load-staging", backup_suffix=".load-backup"
        )
        transactions = (pages_tx, message_tx, archive_tx, music_tx)
        committed: list[PathTransaction] = []

        try:
            shutil.copytree(src_pages, pages_tx.prepare())
            shutil.copytree(src_message, message_tx.prepare())

            staged_archive = (
                staged_root / USER_SOUNDS_DIR / ARCHIVE_DIR_NAME
            )
            if dst_archive.is_dir():
                shutil.copytree(dst_archive, staged_archive)
            else:
                staged_archive.mkdir(parents=True, exist_ok=True)

            sound_payload = {}
            sound_manifest = src_sounds / BUILD_SOUND_MANIFEST_NAME
            if sound_manifest.is_file():
                value = json.loads(sound_manifest.read_text(encoding="utf-8"))
                if isinstance(value, dict):
                    sound_payload = value
            raw_tracks = sound_payload.get("tracks", [])
            if not isinstance(raw_tracks, list):
                raise ValueError("The saved sound manifest is invalid.")
            if not raw_tracks and (src_sounds / MUSIC_FILE).is_file():
                raw_tracks = [{"filename": MUSIC_FILE, "display_title": "Music"}]

            imported_ids: list[str] = []
            for raw_track in raw_tracks:
                if not isinstance(raw_track, dict):
                    continue
                filename = Path(str(raw_track.get("filename", ""))).name
                source = src_sounds / filename
                if not filename or not source.is_file():
                    raise FileNotFoundError(
                        f"Saved music track is missing: {filename or 'unknown'}"
                    )
                record = import_runtime_track(
                    staged_root,
                    source,
                    display_title=str(raw_track.get("display_title", "")),
                    original_name=str(raw_track.get("original_name", filename)),
                    content_hash=str(raw_track.get("content_hash", "")),
                    duration_seconds=float(
                        raw_track.get("duration_seconds", 0.0) or 0.0
                    ),
                )
                imported_ids.append(record.track_id)

            mode = (
                "playlist"
                if str(sound_payload.get("mode", "single")) == "playlist"
                else "single"
            )
            state = ProjectSoundState(
                mode=mode,
                single_track_id=(
                    imported_ids[0] if mode == "single" and imported_ids else ""
                ),
                playlist=imported_ids if mode == "playlist" else [],
                playlist_expanded=True,
                selected_track_id=imported_ids[0] if imported_ids else "",
            )
            save_project_state(staged_root, state)
            sync_current_compatibility(
                staged_root, state, load_library(staged_root)
            )
            shutil.copytree(staged_archive, archive_tx.prepare())
            staged_music = staged_root / USER_SOUNDS_DIR / MUSIC_FILE
            prepared_music = music_tx.prepare()
            if staged_music.is_file():
                prepared_music.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(staged_music, prepared_music)

            pages_tx.commit(keep_backup=True)
            committed.append(pages_tx)
            message_tx.commit(keep_backup=True)
            committed.append(message_tx)
            archive_tx.commit(keep_backup=True)
            committed.append(archive_tx)
            music_tx.commit(
                replace=staged_music.is_file(), keep_backup=True
            )
            committed.append(music_tx)

            settings_before = self.settings.snapshot()
            restored = dict(settings_before)
            stored_settings = metadata.get("settings", {})
            if isinstance(stored_settings, dict):
                for key in RESTORABLE_SETTING_KEYS:
                    if key in stored_settings:
                        restored[key] = stored_settings[key]
            restored["recipient_name"] = str(
                metadata.get("recipient_name") or entry.recipient
            ).strip()
            restored["recipient_title"] = str(
                metadata.get("recipient_title") or entry.title
            ).strip()
            restored[PUBLISHED_PAGE_URL_KEY] = _normalize_page_url(
                str(metadata.get(PUBLISHED_PAGE_URL_KEY, ""))
            )
            try:
                restored[PROJECT_ID_KEY] = str(
                    uuid.UUID(str(metadata.get(PROJECT_ID_KEY, "")))
                )
            except (ValueError, TypeError, AttributeError):
                try:
                    restored[PROJECT_ID_KEY] = str(
                        uuid.UUID(str(settings_before.get(PROJECT_ID_KEY, "")))
                    )
                except (ValueError, TypeError, AttributeError):
                    restored[PROJECT_ID_KEY] = str(uuid.uuid4())
            restored[PROJECT_SCHEMA_KEY] = PROJECT_SCHEMA_VERSION
            atomic_write_settings(self.project_root, restored)
        except Exception as error:
            for transaction in reversed(committed):
                try:
                    transaction.rollback()
                except Exception:
                    pass
            for transaction in transactions:
                try:
                    transaction.abort()
                except Exception:
                    pass
            self._log(
                "Saved-letter loading failed. The current project was preserved. "
                f"{type(error).__name__}: {error}"
            )
            return
        finally:
            shutil.rmtree(staged_root, ignore_errors=True)

        for transaction in transactions:
            transaction.finalize()
        self._last_play_dir = play_dir
        self.refresh_saved_page_url()
        self.refresh_readiness()
        self.request_preview()
        payload = {
            "recipient_name": str(restored.get("recipient_name", "")),
            "recipient_title": str(restored.get("recipient_title", "")),
            "published_page_url": self.saved_page_url,
            "play_dir": str(play_dir),
        }
        self.letter_loaded.emit(payload)
        self._log(
            f"Loaded saved letter: {payload['recipient_title']} — "
            f"{payload['recipient_name']}."
        )

    def _preview_mode_changed(self) -> None:
        mode = str(self.preview_mode.currentData() or "portrait")
        self._preview_mode = mode
        self.settings.update_fields({PREVIEW_MODE_KEY: mode})
        self.request_preview()

    def _current_play_index(self) -> Optional[Path]:
        if self._last_play_dir is not None:
            index = self._last_play_dir / "index.html"
            if index.is_file():
                return index
        project_id = ensure_project_identity(self.project_root)
        index = self.project_root / OUTPUT_PLAY_DIR / project_id / "index.html"
        return index if index.is_file() else None

    def request_preview(self) -> None:
        index = self._current_play_index()
        if index is not None:
            self.preview_requested.emit(str(index.resolve()), self._preview_mode)

    def preview_letter(self) -> None:
        built = self._build_letter()
        if built is None:
            return
        play_dir, readiness = built
        self._last_play_dir = play_dir
        self.request_preview()
        self._log("Letter preview refreshed.")
        QtCore.QTimer.singleShot(
            0, lambda: self._save_build_metadata(play_dir, readiness)
        )

    def publish_letter(self) -> None:
        built = self._build_letter()
        if built is None:
            return
        play_dir, readiness = built
        self._last_play_dir = play_dir
        self.request_preview()
        metadata = self._save_build_metadata(play_dir, readiness)
        if metadata is None:
            return

        publisher = GitHubPagesPublisher(self.project_root)
        if not publisher.is_configured():
            if not bool(self.settings.get(PUBLIC_WARNING_KEY, False)):
                answer = QtWidgets.QMessageBox.question(
                    self,
                    "Publish Letter",
                    "Published letters are placed in a public GitHub repository. "
                    "Continue?",
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel,
                    QtWidgets.QMessageBox.Cancel,
                )
                if answer != QtWidgets.QMessageBox.Yes:
                    self._log("Publishing canceled. The local preview remains available.")
                    return
                self.settings.update_fields({PUBLIC_WARNING_KEY: True})
            configuration = publisher.configure(self)
            if not configuration.configured:
                self._log(configuration.message or "Publishing is not configured.")
                return

        self._log("Publishing letter…")
        QtWidgets.QApplication.processEvents(
            QtCore.QEventLoop.ExcludeUserInputEvents
        )
        result = publisher.publish(play_dir, metadata)
        if not result.success:
            self._log(result.message or "Publishing failed. The local build was preserved.")
            return

        self.settings.update_fields({PUBLISHED_PAGE_URL_KEY: result.url})
        self.set_saved_page_url(result.url)
        self.published_url_changed.emit(result.url)
        update_saved_metadata(play_dir, self.project_root, evaluate_readiness(self.project_root))
        self.refresh_readiness()
        self.refresh_saved_letters()
        self._log("The letter has been sealed.")

    def _build_letter(self) -> Optional[tuple[Path, ReadinessResult]]:
        ensure_output_dirs(self.project_root)
        readiness = self.refresh_readiness()
        if not readiness.can_preview:
            missing = [
                item.label
                for item in readiness.missing_items
                if item.required
            ]
            self._log("Complete these required items first: " + ", ".join(missing))
            return None
        try:
            message = read_text_normalized(
                self.project_root / MESSAGE_HTML_FILE
            )
            play_dir = Path(
                generate.generate_play_bundle(
                    str(self.project_root),
                    message_html=message,
                    open_in_browser=False,
                )
            ).resolve()
        except Exception as error:
            self._log(
                "Build failed. The previous playable version was preserved. "
                f"{type(error).__name__}: {error}"
            )
            return None
        return play_dir, readiness

    def _save_build_metadata(
        self, play_dir: Path, readiness: ReadinessResult
    ) -> Optional[dict]:
        try:
            return update_saved_metadata(play_dir, self.project_root, readiness)
        except Exception as error:
            self._log(
                "The letter is playable, but its saved metadata could not be "
                f"updated: {error}"
            )
            return None

    def refresh_saved_page_url(self) -> str:
        self.saved_page_url = _normalize_page_url(
            str(self.settings.get(PUBLISHED_PAGE_URL_KEY, ""))
        )
        self._sync_published_url()
        return self.saved_page_url

    def set_saved_page_url(self, url: str) -> None:
        self.saved_page_url = _normalize_page_url(url)
        self._sync_published_url()
        self.refresh_readiness()

    def _sync_published_url(self) -> None:
        available = bool(self.saved_page_url)
        self.published_url.setText(self.saved_page_url)
        self.open_published_btn.setEnabled(available)
        self.copy_link_btn.setEnabled(available)
        self.open_published_btn.setToolTip(
            self.saved_page_url
            if available
            else "No published page URL has been saved."
        )

    def open_published_letter(self) -> None:
        url = self.refresh_saved_page_url()
        if not url:
            self._log("No published page URL is available.")
            return
        if QtGui.QDesktopServices.openUrl(QUrl(url)):
            self._log("Opened the published letter.")
        else:
            self._log("The published letter could not be opened.")

    def go_to_page(self) -> None:
        self.open_published_letter()

    def copy_published_link(self) -> None:
        url = self.refresh_saved_page_url()
        if not url:
            return
        QtWidgets.QApplication.clipboard().setText(url)
        self._log("Published link copied.")

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        self.refresh_saved_letters()
        self.refresh_saved_page_url()
        self.refresh_readiness()
        QtCore.QTimer.singleShot(0, self.request_preview)

    def _log(self, text: str) -> None:
        self.status.setPlainText(text)

    @staticmethod
    def _small_button(text: str) -> QtWidgets.QPushButton:
        button = QtWidgets.QPushButton(text)
        button.setMinimumHeight(30)
        button.setStyleSheet(
            "QPushButton{background:#121a22;color:#e6eef5;border:1px solid #3c5366;"
            "border-radius:7px;padding:5px 10px;}"
            "QPushButton:hover{border-color:#00cfee;background:#17303b;}"
            "QPushButton:disabled{color:#65717d;border-color:#2a333c;}"
        )
        return button

    @staticmethod
    def _action_button(
        text: str, background: str, border: str
    ) -> QtWidgets.QPushButton:
        button = QtWidgets.QPushButton(text)
        button.setMinimumHeight(48)
        button.setStyleSheet(
            f"QPushButton{{background:{background};color:white;border:2px solid {border};"
            "border-radius:9px;padding:10px 16px;font:600 12px 'Segoe UI';}"
            f"QPushButton:hover{{background:{border};}}"
            "QPushButton:disabled{background:#171c21;color:#65717d;border-color:#303840;}"
        )
        return button
