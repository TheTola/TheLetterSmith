from __future__ import annotations

import json
import logging
import re
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from typing import Any, Optional
from urllib.parse import unquote, urlsplit

from config import (
    CONTROL_FILES,
    MESSAGE_HTML_FILE,
    MUSIC_FILE,
    PLAY_METADATA_FILE,
    REQUIRED_SLIDES,
    USER_MESSAGE_DIR,
    USER_PAGES_DIR,
    USER_SOUNDS_DIR,
)
from project_state import ensure_project_identity
from readiness import ReadinessResult
from settings_store import SettingsStore, normalize_published_page_url
from sound_model import (
    BUILD_SOUND_MANIFEST_NAME,
    ProjectSoundState,
    import_runtime_track,
    load_library,
    resolve_project_tracks,
    save_project_state,
    sync_current_compatibility,
)
from transactional_io import (
    PathTransaction,
    atomic_write_json,
    create_staging_directory,
)


METADATA_VERSION = 3
RESTORABLE_SETTING_KEYS = (
    "starting_volume",
    "music_volume",
    "curtain_style",
    "message_overlay_preset",
    "message_overlay_opacity",
    "required_features",
    "forge_preview_mode",
)
_METADATA_NAMES = (
    PLAY_METADATA_FILE,
    "play_metadata.json",
    "recovery_metadata.json",
    "metadata.json",
)
_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class SavedLetter:
    path: Path
    recipient: str
    title: str
    modified_at: datetime
    published_url: str
    cover_path: Optional[Path]
    recovery: bool = False

    @property
    def published(self) -> bool:
        return bool(self.published_url)


@dataclass(frozen=True)
class RestoredProject:
    play_dir: Path
    project_id: str
    recipient: str
    title: str
    published_url: str

    def as_payload(self) -> dict[str, str]:
        return {
            "play_dir": str(self.play_dir),
            "project_id": self.project_id,
            "recipient_name": self.recipient,
            "recipient_title": self.title,
            "published_page_url": self.published_url,
        }


class SavedLetterRestoreError(RuntimeError):
    pass


class SavedLetterDeleteError(RuntimeError):
    pass


def _read_metadata(path: Path) -> dict[str, Any]:
    for name in _METADATA_NAMES:
        candidate = path / name
        if not candidate.is_file() or candidate.is_symlink():
            continue
        try:
            value = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            return value
    return {}


def _runtime_directory(
    play_dir: Path,
    current: str,
    legacy: str,
) -> Optional[Path]:
    play_root = play_dir.resolve()
    for relative in (current, legacy):
        candidate = play_dir / relative
        if not candidate.is_dir() or candidate.is_symlink():
            continue
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(play_root)
        except (OSError, ValueError):
            continue
        cursor = play_dir
        unsafe = False
        for part in Path(relative).parts:
            cursor = cursor / part
            if cursor.is_symlink():
                unsafe = True
                break
        if not unsafe:
            return candidate
    return None


def _runtime_file(
    play_dir: Path,
    *relative_paths: object,
) -> Optional[Path]:
    play_root = play_dir.resolve()
    for raw_relative in relative_paths:
        relative_text = str(raw_relative or "").strip()
        if not relative_text:
            continue
        relative = Path(relative_text)
        if relative.is_absolute() or ".." in relative.parts:
            continue
        candidate = play_dir / relative
        if not candidate.is_file() or candidate.is_symlink():
            continue
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(play_root)
        except (OSError, ValueError):
            continue
        cursor = play_dir
        unsafe = False
        for part in relative.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                unsafe = True
                break
        if not unsafe:
            return resolved
    return None


def _readable_file(path: Path) -> bool:
    try:
        with path.open("rb") as stream:
            stream.read(1)
        return True
    except OSError:
        return False


class SavedLetterCatalog:
    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root).resolve()
        self.play_root = (self.project_root / "output" / "Play").resolve()
        self.recovery_root = (self.project_root / "output" / "Recovery").resolve()

    def list_entries(self) -> tuple[SavedLetter, ...]:
        entries: list[SavedLetter] = []
        seen: set[Path] = set()
        seen_project_ids: set[str] = set()
        for root, recovery in (
            (self.play_root, False),
            (self.recovery_root, True),
        ):
            if not root.is_dir():
                continue
            for index in root.rglob("index.html"):
                path = index.parent.resolve()
                try:
                    path.relative_to(root)
                except ValueError:
                    continue
                if path in seen:
                    continue
                try:
                    if not self._is_valid_candidate(path):
                        continue
                    entry = self._entry(path, recovery=recovery)
                except Exception:
                    _LOGGER.warning(
                        "Skipping unreadable saved-letter candidate: %s",
                        path,
                        exc_info=True,
                    )
                    continue
                seen.add(path)
                project_id = str(getattr(entry, "project_id", "") or "").strip()
                if project_id and project_id in seen_project_ids:
                    continue
                if project_id:
                    seen_project_ids.add(project_id)
                entries.append(entry)
        entries.sort(
            key=lambda entry: (entry.modified_at, entry.title.casefold()),
            reverse=True,
        )
        return tuple(entries)

    def search(self, query: str) -> tuple[SavedLetter, ...]:
        needle = (query or "").strip().casefold()
        if not needle:
            return self.list_entries()
        return tuple(
            entry
            for entry in self.list_entries()
            if needle in f"{entry.recipient} {entry.title}".casefold()
        )

    def delete(self, entry: SavedLetter) -> Path:
        if not isinstance(entry, SavedLetter):
            raise SavedLetterDeleteError("The saved letter is invalid.")
        source = Path(entry.path)
        if source.is_symlink():
            raise SavedLetterDeleteError("Saved-letter links cannot be deleted.")
        try:
            target = source.resolve(strict=True)
        except OSError as error:
            raise SavedLetterDeleteError(
                "The saved letter no longer exists."
            ) from error

        allowed_root: Optional[Path] = None
        for root in (self.play_root, self.recovery_root):
            try:
                relative = target.relative_to(root)
            except ValueError:
                continue
            if relative.parts:
                allowed_root = root
                break
        if allowed_root is None or not self._is_valid_candidate(target):
            raise SavedLetterDeleteError(
                "The saved letter is outside the managed letter folders."
            )

        try:
            shutil.rmtree(target)
        except OSError as error:
            raise SavedLetterDeleteError(
                "The saved letter could not be deleted."
            ) from error
        return target

    @staticmethod
    def metadata(path: str | Path) -> dict[str, Any]:
        return _read_metadata(Path(path).resolve())

    @staticmethod
    def _is_valid_candidate(path: Path) -> bool:
        index = path / "index.html"
        if (
            path.is_symlink()
            or index.is_symlink()
            or not index.is_file()
            or not (path / "styles.css").is_file()
            or not (path / "script.js").is_file()
        ):
            return False
        pages = _runtime_directory(
            path,
            "gallery/pages",
            "gallery/user/pages",
        )
        message = _runtime_directory(
            path,
            "gallery/message",
            "gallery/user/message",
        )
        controls = _runtime_directory(
            path,
            "gallery/controls",
            "gallery/user/card/controls",
        )
        return bool(
            pages
            and message
            and controls
            and all(
                (pages / name).is_file()
                for name in REQUIRED_SLIDES
                if name != "cover.png"
            )
            and all((controls / name).is_file() for name in CONTROL_FILES)
            and (message / "message.html").is_file()
        )

    def _entry(self, path: Path, *, recovery: bool) -> SavedLetter:
        metadata = _read_metadata(path)
        recipient = self._display_text(metadata.get("recipient_name"))
        title = self._display_text(metadata.get("recipient_title"))
        if not title:
            title = self._html_title(path / "index.html") or "Untitled Letter"
        if not recipient:
            parent_is_category = path.parent in {
                self.play_root,
                self.recovery_root,
            }
            recipient = (
                "Unknown Recipient"
                if parent_is_category
                else self._humanize(path.parent.name)
            )
        cover = _runtime_file(
            path,
            metadata.get("cover_thumbnail_path"),
            "gallery/pages/cover.png",
            "gallery/user/pages/cover.png",
            "cover.png",
        )
        return SavedLetter(
            path=path,
            recipient=recipient,
            title=title,
            modified_at=datetime.fromtimestamp(path.stat().st_mtime),
            published_url=normalize_published_page_url(
                metadata.get("published_page_url", "")
            ),
            cover_path=cover,
            recovery=recovery,
        )

    @staticmethod
    def _html_title(path: Path) -> str:
        try:
            value = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return ""
        match = re.search(r"<title>\s*(.*?)\s*</title>", value, re.I | re.S)
        return (
            SavedLetterCatalog._display_text(match.group(1))
            if match
            else ""
        )

    @staticmethod
    def _display_text(value: object) -> str:
        return re.sub(r"\s+", " ", unescape(str(value or ""))).strip()

    @staticmethod
    def _humanize(value: str) -> str:
        return SavedLetterCatalog._display_text(
            value.replace("_", " ").replace("-", " ")
        ).title()


class SavedLetterRestorer:
    """Validate, stage, and atomically restore editable project-owned state."""

    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root).resolve()
        self.settings = SettingsStore(self.project_root)
        self.allowed_roots = (
            (self.project_root / "output" / "Play").resolve(),
            (self.project_root / "output" / "Recovery").resolve(),
        )

    def restore(self, entry: SavedLetter) -> RestoredProject:
        play_dir = self._validated_play_directory(entry.path)
        pages = _runtime_directory(
            play_dir,
            "gallery/pages",
            "gallery/user/pages",
        )
        message = _runtime_directory(
            play_dir,
            "gallery/message",
            "gallery/user/message",
        )
        sounds = _runtime_directory(
            play_dir,
            "gallery/sounds",
            "gallery/user/sounds",
        )
        if pages is None or message is None:
            raise SavedLetterRestoreError(
                "The selected saved letter is missing editable content."
            )
        self._validate_pages(pages)
        self._validate_message(play_dir, message)
        metadata = _read_metadata(play_dir)
        try:
            sound_payload, sound_tracks = self._validate_sound(sounds)
        except SavedLetterRestoreError:
            stored_settings = metadata.get("settings", {})
            required_features = (
                stored_settings.get("required_features", {})
                if isinstance(stored_settings, dict)
                else {}
            )
            music_required = bool(
                required_features.get("music", False)
                if isinstance(required_features, dict)
                else False
            )
            if music_required:
                raise
            _LOGGER.warning(
                "Ignoring invalid optional sound data while restoring %s",
                play_dir,
            )
            sound_payload, sound_tracks = {"mode": "single", "tracks": []}, []
        settings_before = self.settings.snapshot()
        restored_settings = self._prepare_settings(
            metadata,
            entry,
            play_dir,
            settings_before,
        )

        staged_root = create_staging_directory(
            self.project_root / "output",
            prefix=".letter-load-",
        )
        pages_tx = PathTransaction(
            self.project_root / USER_PAGES_DIR,
            staging_suffix=".load-staging",
            backup_suffix=".load-backup",
            unique_staging=True,
        )
        message_tx = PathTransaction(
            self.project_root / USER_MESSAGE_DIR,
            staging_suffix=".load-staging",
            backup_suffix=".load-backup",
            unique_staging=True,
        )
        sounds_tx = PathTransaction(
            self.project_root / USER_SOUNDS_DIR,
            staging_suffix=".load-staging",
            backup_suffix=".load-backup",
            unique_staging=True,
        )
        transactions = (pages_tx, message_tx, sounds_tx)
        committed: list[PathTransaction] = []
        settings_committed = False

        try:
            staged_pages = staged_root / USER_PAGES_DIR
            staged_message = staged_root / USER_MESSAGE_DIR
            staged_sounds = staged_root / USER_SOUNDS_DIR
            shutil.copytree(pages, staged_pages)
            shutil.copytree(message, staged_message)
            live_sounds = self.project_root / USER_SOUNDS_DIR
            if live_sounds.is_dir():
                shutil.copytree(live_sounds, staged_sounds)
            else:
                staged_sounds.mkdir(parents=True, exist_ok=True)

            imported_ids: list[str] = []
            for track in sound_tracks:
                source = sounds / track["filename"] if sounds else None
                if source is None:
                    continue
                record = import_runtime_track(
                    staged_root,
                    source,
                    display_title=track["display_title"],
                    original_name=track["original_name"],
                    content_hash=track["content_hash"],
                    duration_seconds=track["duration_seconds"],
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
                    imported_ids[0]
                    if mode == "single" and imported_ids
                    else ""
                ),
                playlist=imported_ids if mode == "playlist" else [],
                playlist_expanded=True,
                selected_track_id=imported_ids[0] if imported_ids else "",
            )
            save_project_state(staged_root, state)
            sync_current_compatibility(
                staged_root,
                state,
                load_library(staged_root),
            )

            shutil.copytree(staged_pages, pages_tx.prepare())
            shutil.copytree(staged_message, message_tx.prepare())
            shutil.copytree(staged_sounds, sounds_tx.prepare())

            for transaction in transactions:
                transaction.commit(keep_backup=True)
                committed.append(transaction)
            settings_committed = True
            self.settings.replace_snapshot(restored_settings)
            self._verify_committed_state()
        except Exception as error:
            _LOGGER.exception(
                "Saved-letter restoration failed for %s",
                play_dir,
            )
            for transaction in reversed(committed):
                try:
                    transaction.rollback()
                except Exception:
                    _LOGGER.exception(
                        "Could not roll back %s",
                        transaction.final_path,
                    )
            for transaction in transactions:
                try:
                    transaction.abort()
                except Exception:
                    _LOGGER.exception(
                        "Could not clean staging for %s",
                        transaction.final_path,
                    )
            if settings_committed:
                try:
                    self.settings.replace_snapshot(settings_before)
                except Exception:
                    _LOGGER.exception("Could not restore previous settings.")
            raise SavedLetterRestoreError(
                "The selected saved letter could not be restored. "
                "The current project was preserved."
            ) from error
        finally:
            shutil.rmtree(staged_root, ignore_errors=True)

        for transaction in transactions:
            try:
                transaction.finalize()
            except OSError:
                _LOGGER.exception(
                    "Could not clean restoration backup for %s",
                    transaction.final_path,
                )
        return RestoredProject(
            play_dir=play_dir,
            project_id=str(restored_settings["project_id"]),
            recipient=str(restored_settings.get("recipient_name", "")),
            title=str(restored_settings.get("recipient_title", "")),
            published_url=str(
                restored_settings.get("published_page_url", "")
            ),
        )

    def _validated_play_directory(self, source: Path) -> Path:
        original = Path(source)
        if ".." in original.parts:
            raise SavedLetterRestoreError(
                "Saved-letter path traversal is not allowed."
            )
        if original.is_symlink():
            raise SavedLetterRestoreError("Saved-letter links are not allowed.")
        try:
            resolved = original.resolve(strict=True)
        except OSError as error:
            raise SavedLetterRestoreError(
                "The selected saved letter no longer exists."
            ) from error
        allowed = False
        for root in self.allowed_roots:
            try:
                relative = resolved.relative_to(root)
            except ValueError:
                continue
            if relative.parts:
                allowed = True
                break
        if not allowed:
            raise SavedLetterRestoreError(
                "The selected folder is outside the saved-letter library."
            )
        viewer_files = tuple(
            resolved / name
            for name in ("index.html", "styles.css", "script.js")
        )
        controls = _runtime_directory(
            resolved,
            "gallery/controls",
            "gallery/user/card/controls",
        )
        if (
            any(
                path.is_symlink()
                or not path.is_file()
                or not _readable_file(path)
                for path in viewer_files
            )
            or controls is None
            or any(
                (controls / name).is_symlink()
                or not (controls / name).is_file()
                or not _readable_file(controls / name)
                for name in CONTROL_FILES
            )
        ):
            raise SavedLetterRestoreError(
                "The selected saved letter has an incomplete viewer."
            )
        return resolved

    @staticmethod
    def _validate_pages(pages: Path) -> None:
        missing = [
            name
            for name in REQUIRED_SLIDES
            if not (pages / name).is_file()
            or (pages / name).is_symlink()
            or not _readable_file(pages / name)
        ]
        if missing:
            raise SavedLetterRestoreError(
                "Required saved images are missing: " + ", ".join(missing)
            )

    @staticmethod
    def _validate_message(play_dir: Path, message: Path) -> None:
        html_path = message / "message.html"
        if html_path.is_symlink() or not html_path.is_file():
            raise SavedLetterRestoreError("The saved message is missing.")
        try:
            html = html_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise SavedLetterRestoreError(
                "The saved message cannot be read."
            ) from error
        if not html.strip():
            raise SavedLetterRestoreError("The saved message is empty.")

        references = re.findall(
            r"""(?:src|poster)\s*=\s*["']([^"']+)["']""",
            html,
            flags=re.I,
        )
        references.extend(
            re.findall(
                r"""\burl\(\s*["']?([^)"']+)["']?\s*\)""",
                html,
                flags=re.I,
            )
        )
        for raw_reference in references:
            reference = raw_reference.strip()
            parsed = urlsplit(reference)
            if not reference or reference.startswith(("#", "data:")):
                continue
            if parsed.scheme or parsed.netloc:
                raise SavedLetterRestoreError(
                    "The saved message contains external media."
                )
            relative = Path(unquote(parsed.path))
            if relative.is_absolute() or ".." in relative.parts:
                raise SavedLetterRestoreError(
                    "The saved message contains an unsafe asset path."
                )
            base = play_dir if relative.parts[:1] == ("gallery",) else message
            unresolved_asset = base / relative
            asset = unresolved_asset.resolve()
            try:
                asset.relative_to(play_dir)
            except ValueError as error:
                raise SavedLetterRestoreError(
                    "The saved message asset escapes its project."
                ) from error
            cursor = base
            unsafe_link = base.is_symlink()
            for part in relative.parts:
                cursor = cursor / part
                if cursor.is_symlink():
                    unsafe_link = True
                    break
            if not asset.is_file() or unsafe_link:
                raise SavedLetterRestoreError(
                    f"A saved message asset is missing: {relative.as_posix()}"
                )

    @staticmethod
    def _validate_sound(
        sounds: Optional[Path],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        if sounds is None:
            return {"mode": "single", "tracks": []}, []
        manifest = sounds / BUILD_SOUND_MANIFEST_NAME
        if manifest.is_file():
            try:
                payload = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as error:
                raise SavedLetterRestoreError(
                    "The saved sound manifest is invalid."
                ) from error
            if not isinstance(payload, dict):
                raise SavedLetterRestoreError(
                    "The saved sound manifest is invalid."
                )
            raw_tracks = payload.get("tracks", [])
            if not isinstance(raw_tracks, list):
                raise SavedLetterRestoreError(
                    "The saved sound manifest is invalid."
                )
        elif (sounds / MUSIC_FILE).is_file():
            payload = {"mode": "single"}
            raw_tracks = [
                {
                    "filename": MUSIC_FILE,
                    "display_title": "Music",
                }
            ]
        else:
            return {"mode": "single", "tracks": []}, []

        tracks: list[dict[str, Any]] = []
        for raw in raw_tracks:
            if not isinstance(raw, dict):
                raise SavedLetterRestoreError(
                    "The saved sound manifest contains an invalid track."
                )
            filename = str(raw.get("filename", "")).strip()
            if not filename or Path(filename).name != filename:
                raise SavedLetterRestoreError(
                    "The saved sound manifest contains an unsafe path."
                )
            source = sounds / filename
            if source.is_symlink() or not source.is_file() or not _readable_file(source):
                raise SavedLetterRestoreError(
                    f"A saved music track is missing: {filename}"
                )
            try:
                duration_seconds = max(
                    0.0,
                    float(raw.get("duration_seconds", 0.0) or 0.0),
                )
            except (TypeError, ValueError) as error:
                raise SavedLetterRestoreError(
                    "The saved sound manifest contains an invalid duration."
                ) from error
            content_hash = str(raw.get("content_hash", "")).strip()
            if content_hash and not re.fullmatch(
                r"[0-9A-Fa-f]{32,128}",
                content_hash,
            ):
                content_hash = ""
            tracks.append(
                {
                    "filename": filename,
                    "display_title": str(
                        raw.get("display_title", "")
                    ).strip(),
                    "original_name": str(
                        raw.get("original_name", filename)
                    ).strip(),
                    "content_hash": content_hash,
                    "duration_seconds": duration_seconds,
                }
            )
        return payload, tracks

    @staticmethod
    def _prepare_settings(
        metadata: dict[str, Any],
        entry: SavedLetter,
        play_dir: Path,
        settings_before: dict[str, Any],
    ) -> dict[str, Any]:
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
        restored["published_page_url"] = normalize_published_page_url(
            metadata.get("published_page_url", "")
        )
        raw_project_id = metadata.get("project_id", play_dir.name)
        try:
            restored["project_id"] = str(uuid.UUID(str(raw_project_id)))
        except (ValueError, TypeError, AttributeError):
            restored["project_id"] = str(uuid.uuid4())
        restored["project_schema_version"] = 1
        return restored

    def _verify_committed_state(self) -> None:
        pages = self.project_root / USER_PAGES_DIR
        if any(not (pages / name).is_file() for name in REQUIRED_SLIDES):
            raise RuntimeError("Restored page verification failed.")
        message = self.project_root / MESSAGE_HTML_FILE
        if not message.is_file() or not message.read_text(
            encoding="utf-8"
        ).strip():
            raise RuntimeError("Restored message verification failed.")
        resolve_project_tracks(self.project_root)


def update_saved_metadata(
    play_dir: str | Path,
    project_root: str | Path,
    readiness: ReadinessResult,
    *,
    public_path: str = "",
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    destination = Path(play_dir).resolve()
    metadata_path = destination / PLAY_METADATA_FILE
    metadata = _read_metadata(destination)
    settings = SettingsStore(root).snapshot()
    sound_state, sound_tracks = resolve_project_tracks(root)
    restorable_settings = {
        key: settings[key]
        for key in RESTORABLE_SETTING_KEYS
        if key in settings
    }
    metadata.update(
        {
            "schema_version": METADATA_VERSION,
            "source_version": METADATA_VERSION,
            "project_id": ensure_project_identity(root),
            "recipient_name": str(
                settings.get("recipient_name", "")
            ).strip(),
            "recipient_title": str(
                settings.get("recipient_title", "")
            ).strip(),
            "build_timestamp": datetime.now(timezone.utc).isoformat(),
            "published_page_url": normalize_published_page_url(
                settings.get("published_page_url", "")
            ),
            "settings": restorable_settings,
            "editable_assets": {
                "pages": {
                    name: f"gallery/pages/{name}"
                    for name in REQUIRED_SLIDES
                },
                "message": "gallery/message/message.html",
                "sound_manifest": (
                    f"gallery/sounds/{BUILD_SOUND_MANIFEST_NAME}"
                ),
            },
            "sound": {
                "mode": sound_state.mode,
                "playlist_order": [
                    track.display_title for track in sound_tracks
                ],
                "track_count": len(sound_tracks),
                "crossfade_ms": (
                    1000
                    if sound_state.mode == "playlist"
                    and len(sound_tracks) > 1
                    else 0
                ),
            },
            "readiness": {
                "percentage": readiness.completion_percentage,
                "status": readiness.status,
            },
            "cover_thumbnail_path": "gallery/pages/cover.png",
        }
    )
    normalized_public_path = str(public_path).strip()
    if normalized_public_path:
        if (
            Path(normalized_public_path).name != normalized_public_path
            or normalized_public_path in {".", ".."}
        ):
            raise ValueError("The published path is invalid.")
        metadata["public_path"] = normalized_public_path
    metadata.pop("build_location", None)
    atomic_write_json(metadata_path, metadata)
    return metadata


__all__ = [
    "METADATA_VERSION",
    "RESTORABLE_SETTING_KEYS",
    "RestoredProject",
    "SavedLetter",
    "SavedLetterCatalog",
    "SavedLetterDeleteError",
    "SavedLetterRestoreError",
    "SavedLetterRestorer",
    "update_saved_metadata",
]
