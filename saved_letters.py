from __future__ import annotations

import json
import logging
import os
import re
import shutil
import stat
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from typing import Any, Optional
from urllib.parse import unquote, urlsplit

from image_animation import (
    IMAGE_MANIFEST_NAME,
    validate_runtime_image_manifest,
)
from config import (
    CONTROL_FILES,
    PLAY_METADATA_FILE,
    REQUIRED_SLIDES,
    USER_MESSAGE_DIR,
    USER_PAGES_DIR,
    USER_SOUNDS_DIR,
    canonical_play_root,
    canonical_recovery_root,
    canonical_stock_letters_root,
)
from project_paths import (
    PROJECT_METADATA_SCHEMA_VERSION,
    ProjectContext,
    ProjectPathResolver,
    application_paths,
)
from project_state import (
    PROJECT_SCHEMA_KEY,
    RECIPIENT_DISPLAY_NAME_KEY,
    RECIPIENT_ID_KEY,
    RECIPIENT_NORMALIZED_KEY,
    ProjectIdentity,
    ensure_project_identity,
)
from recipient_registry import RecipientRegistry
from readiness import ReadinessResult
from publishing.expiration import publication_status as get_publication_status
from save_schema import (
    CURRENT_SAVE_SCHEMA_VERSION,
    SAVED_LETTER_DOCUMENT_TYPE,
    SaveSchemaError,
    has_complete_saved_state,
    is_current_save_schema,
    stamp_current_save_schema,
    validate_saved_letter_metadata,
)
from settings_store import (
    ACTIVE_PLAY_DIR_KEY,
    PUBLICATION_PROVIDER_KEY,
    PUBLICATION_VERIFIED_KEY,
    PUBLISHED_AT_KEY,
    PUBLISHED_EXPIRES_AT_KEY,
    PUBLISHED_GITHUB_OWNER_KEY,
    PUBLISHED_GITHUB_REPOSITORY_KEY,
    PUBLISHED_PAGE_URL_KEY,
    PUBLISHED_PUBLIC_PATH_KEY,
    PUBLISHED_SOURCE_FINGERPRINT_KEY,
    SettingsStore,
    normalize_published_page_url,
)
from sound_model import (
    BUILD_SOUND_MANIFEST_NAME,
    ProjectSoundState,
    current_manifest_path,
    current_music_path,
    display_title_from_name,
    hash_file,
    import_runtime_track,
    library_path,
    load_library,
    originals_dir,
    processed_dir,
    project_sound_path,
    resolve_project_tracks,
    save_project_state,
    sync_current_compatibility,
)
from transactional_io import (
    PathTransaction,
    atomic_write_bytes,
    atomic_write_json,
    create_staging_directory,
    recover_stale_transactions,
)


METADATA_VERSION = CURRENT_SAVE_SCHEMA_VERSION
LAST_ACTIVITY_AT_KEY = "last_activity_at"
PROMPT_WRITER_STATE_FILE = "prompt_writer_state.json"
RESTORABLE_SETTING_KEYS = (
    "starting_volume",
    "music_volume",
    "curtain_style",
    "message_overlay_preset",
    "message_overlay_opacity",
    "required_features",
    "forge_preview_mode",
)
PUBLICATION_METADATA_KEYS = (
    PUBLISHED_PAGE_URL_KEY,
    PUBLISHED_PUBLIC_PATH_KEY,
    PUBLISHED_AT_KEY,
    PUBLISHED_EXPIRES_AT_KEY,
    PUBLICATION_PROVIDER_KEY,
    PUBLICATION_VERIFIED_KEY,
    PUBLISHED_SOURCE_FINGERPRINT_KEY,
    PUBLISHED_GITHUB_OWNER_KEY,
    PUBLISHED_GITHUB_REPOSITORY_KEY,
)
_LOGGER = logging.getLogger(__name__)
_ACTIVE_LETTER_LOAD_WORKSPACES: set[Path] = set()


def _publication_metadata(state: dict[str, Any]) -> dict[str, Any]:
    return {
        PUBLISHED_PAGE_URL_KEY: normalize_published_page_url(
            state.get(PUBLISHED_PAGE_URL_KEY, "")
        ),
        PUBLISHED_PUBLIC_PATH_KEY: str(
            state.get(PUBLISHED_PUBLIC_PATH_KEY, "")
        ).strip(),
        PUBLISHED_AT_KEY: str(state.get(PUBLISHED_AT_KEY, "")).strip(),
        PUBLISHED_EXPIRES_AT_KEY: str(
            state.get(PUBLISHED_EXPIRES_AT_KEY, "")
        ).strip(),
        PUBLICATION_PROVIDER_KEY: str(
            state.get(PUBLICATION_PROVIDER_KEY, "")
        ).strip(),
        PUBLICATION_VERIFIED_KEY: state.get(PUBLICATION_VERIFIED_KEY) is True,
        PUBLISHED_SOURCE_FINGERPRINT_KEY: str(
            state.get(PUBLISHED_SOURCE_FINGERPRINT_KEY, "")
        ).strip(),
        PUBLISHED_GITHUB_OWNER_KEY: str(
            state.get(PUBLISHED_GITHUB_OWNER_KEY, "")
        ).strip(),
        PUBLISHED_GITHUB_REPOSITORY_KEY: str(
            state.get(PUBLISHED_GITHUB_REPOSITORY_KEY, "")
        ).strip(),
    }


@dataclass(frozen=True)
class _FileSnapshot:
    path: Path
    data: Optional[bytes]

    @classmethod
    def capture(cls, path: str | Path) -> "_FileSnapshot":
        target = Path(path).resolve()
        return cls(target, target.read_bytes() if target.is_file() else None)

    def restore(self) -> None:
        if self.data is None:
            self.path.unlink(missing_ok=True)
            return
        atomic_write_bytes(self.path, self.data)


@dataclass(frozen=True)
class _SoundArchiveSnapshot:
    library: _FileSnapshot
    directory_files: tuple[tuple[Path, frozenset[str]], ...]

    @classmethod
    def capture(cls, project_root: str | Path) -> "_SoundArchiveSnapshot":
        directories = (
            originals_dir(project_root),
            processed_dir(project_root),
        )
        return cls(
            library=_FileSnapshot.capture(library_path(project_root)),
            directory_files=tuple(
                (
                    directory,
                    frozenset(
                        child.name
                        for child in directory.iterdir()
                        if child.is_file() or child.is_symlink()
                    )
                    if directory.is_dir()
                    else frozenset(),
                )
                for directory in directories
            ),
        )

    def restore(self) -> None:
        self.library.restore()
        for directory, existing_names in self.directory_files:
            if not directory.is_dir():
                continue
            for child in directory.iterdir():
                if (
                    child.name not in existing_names
                    and (child.is_file() or child.is_symlink())
                ):
                    child.unlink(missing_ok=True)


def _remove_readonly_and_retry(function, path: str, error: BaseException) -> None:
    """Clear a copied Windows read-only attribute before retrying removal."""
    if not isinstance(error, PermissionError):
        raise error
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    function(path)


def _remove_tree(path: str | Path) -> None:
    shutil.rmtree(path, onexc=_remove_readonly_and_retry)


def cleanup_stale_letter_load_workspaces(project_root: str | Path) -> tuple[Path, ...]:
    """Remove only interrupted Letter Smith load workspaces at startup."""
    root = Path(project_root).resolve()
    recover_stale_transactions(
        (
            root / USER_PAGES_DIR,
            root / USER_MESSAGE_DIR,
        )
    )
    output_root = application_paths(root).temporary_root.resolve()
    if not output_root.is_dir():
        return ()
    removed: list[Path] = []
    for candidate in output_root.iterdir():
        if not candidate.name.startswith(".letter-load-") or not candidate.is_dir():
            continue
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(output_root)
            if resolved == output_root:
                continue
            if resolved in _ACTIVE_LETTER_LOAD_WORKSPACES:
                continue
            _remove_tree(resolved)
            removed.append(resolved)
        except (OSError, ValueError):
            _LOGGER.exception("Could not clean stale Letter Smith load workspace: %s", candidate)
    return tuple(removed)


def _valid_uuid(value: object) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return ""


def _activity_datetime(metadata: dict[str, Any], path: Path) -> datetime:
    raw_value = str(metadata.get(LAST_ACTIVITY_AT_KEY, "")).strip()
    if raw_value:
        try:
            parsed = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return datetime.fromtimestamp(parsed.timestamp())
        except (ValueError, OverflowError, OSError):
            pass
    return datetime.fromtimestamp(path.stat().st_mtime)


@dataclass(frozen=True)
class SavedLetter:
    path: Path
    recipient: str
    title: str
    modified_at: datetime
    published_url: str
    cover_path: Optional[Path]
    published_public_path: str = ""
    published_at: str = ""
    published_expires_at: str = ""
    publication_provider: str = ""
    publication_verified: bool = False
    published_source_fingerprint: str = ""
    recipient_id: str = ""
    project_id: str = ""
    recovery: bool = False
    example: bool = False

    @property
    def published(self) -> bool:
        return self.publication_status == "published"

    @property
    def expired(self) -> bool:
        return self.publication_status == "expired"

    @property
    def publication_status(self) -> str:
        return get_publication_status(
            {
                PUBLISHED_PAGE_URL_KEY: self.published_url,
                PUBLISHED_PUBLIC_PATH_KEY: self.published_public_path,
                PUBLISHED_AT_KEY: self.published_at,
                PUBLISHED_EXPIRES_AT_KEY: self.published_expires_at,
                PUBLICATION_PROVIDER_KEY: self.publication_provider,
                PUBLICATION_VERIFIED_KEY: self.publication_verified,
                PUBLISHED_SOURCE_FINGERPRINT_KEY: self.published_source_fingerprint,
            }
        )

    @property
    def needs_recipient_assignment(self) -> bool:
        return (
            not self.recipient_id
            and (
                not self.recipient
                or self.recipient.casefold() == "unknown recipient"
            )
        )


@dataclass(frozen=True)
class RestoredProject:
    play_dir: Path
    project_id: str
    recipient_id: str
    recipient: str
    recipient_normalized_key: str
    title: str
    published_url: str
    published_public_path: str = ""
    published_at: str = ""
    published_expires_at: str = ""
    publication_provider: str = ""
    publication_verified: bool = False
    published_source_fingerprint: str = ""

    @property
    def identity(self) -> ProjectIdentity:
        return ProjectIdentity(
            recipient_id=self.recipient_id,
            recipient_display_name=self.recipient,
            recipient_normalized_key=self.recipient_normalized_key,
            project_id=self.project_id,
        )

    def as_payload(self) -> dict[str, object]:
        return {
            "play_dir": str(self.play_dir),
            "project_id": self.project_id,
            "recipient_id": self.recipient_id,
            "recipient_display_name": self.recipient,
            "recipient_normalized_key": self.recipient_normalized_key,
            "recipient_name": self.recipient,
            "recipient_title": self.title,
            "published_page_url": self.published_url,
            PUBLISHED_PUBLIC_PATH_KEY: self.published_public_path,
            PUBLISHED_AT_KEY: self.published_at,
            PUBLISHED_EXPIRES_AT_KEY: self.published_expires_at,
            PUBLICATION_PROVIDER_KEY: self.publication_provider,
            PUBLICATION_VERIFIED_KEY: self.publication_verified,
            PUBLISHED_SOURCE_FINGERPRINT_KEY: self.published_source_fingerprint,
        }


class SavedLetterRestoreError(RuntimeError):
    pass


class RecipientAssignmentRequired(SavedLetterRestoreError):
    def __init__(self, entry: SavedLetter) -> None:
        super().__init__(
            "This saved letter needs a recipient before it can be loaded."
        )
        self.entry = entry


class SavedLetterDeleteError(RuntimeError):
    pass


def _read_metadata(
    path: Path,
    *,
    strict: bool = False,
) -> dict[str, Any]:
    candidate = path / PLAY_METADATA_FILE
    if candidate.is_symlink():
        if strict:
            raise SavedLetterRestoreError(
                "The saved-letter metadata cannot be a link."
            )
        return {}
    if not candidate.is_file():
        return {}
    try:
        value = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        if strict:
            raise SavedLetterRestoreError(
                "The saved-letter metadata is unreadable."
            ) from error
        return {}
    if isinstance(value, dict):
        return value
    if strict:
        raise SavedLetterRestoreError(
            "The saved-letter metadata must contain an object."
        )
    return {}


def _empty_prompt_writer_state() -> dict[str, Any]:
    # Import lazily so saved-letter catalog operations do not initialize the
    # Prompt Writer UI module.
    from PromptWriterPanel import empty_prompt_writer_state

    return empty_prompt_writer_state()


def _read_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SavedLetterRestoreError(f"The {label} is unreadable.") from error
    if not isinstance(value, dict):
        raise SavedLetterRestoreError(f"The {label} is invalid.")
    return value


def _active_prompt_writer_state(project_root: Path) -> dict[str, Any]:
    state_path = project_root / PROMPT_WRITER_STATE_FILE
    if not state_path.is_file():
        return _empty_prompt_writer_state()
    return _read_json_object(state_path, label="active Prompt Writer state")


def _saved_prompt_writer_state(
    play_dir: Path,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    editable_assets = metadata.get("editable_assets", {})
    configured_path = (
        editable_assets.get("prompt_writer_state", "")
        if isinstance(editable_assets, dict)
        else ""
    )
    state_path = _runtime_file(
        play_dir,
        configured_path,
        PROMPT_WRITER_STATE_FILE,
    )
    if state_path is not None:
        return _read_json_object(state_path, label="saved Prompt Writer state")

    current_format = has_complete_saved_state(metadata)
    if configured_path or current_format:
        raise SavedLetterRestoreError("The saved Prompt Writer state is missing.")

    return _empty_prompt_writer_state()


def _backfill_legacy_prompt_writer_state(
    play_dir: Path,
    metadata: dict[str, Any],
    state: dict[str, Any],
) -> None:
    editable_assets = metadata.get("editable_assets", {})
    configured_path = (
        editable_assets.get("prompt_writer_state", "")
        if isinstance(editable_assets, dict)
        else ""
    )
    if _runtime_file(
        play_dir,
        configured_path,
        PROMPT_WRITER_STATE_FILE,
    ) is not None:
        return

    current_format = has_complete_saved_state(metadata)
    if configured_path or current_format:
        return

    try:
        atomic_write_json(play_dir / PROMPT_WRITER_STATE_FILE, state)
    except OSError:
        _LOGGER.warning(
            "Could not save default Prompt Writer state for legacy letter %s.",
            play_dir,
            exc_info=True,
        )
        return
    _LOGGER.info(
        "Saved default Prompt Writer state for legacy letter %s.",
        play_dir,
    )


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


def _legacy_optional_sound_payload(sounds: Path) -> dict[str, Any]:
    try:
        music_files = sorted(
            (
                path
                for path in sounds.iterdir()
                if re.fullmatch(
                    r"music(?:-\d+)?\.mp3",
                    path.name,
                    re.IGNORECASE,
                )
                and path.is_file()
                and not path.is_symlink()
                and _readable_file(path)
            ),
            key=lambda path: (
                0 if path.name.casefold() == "music.mp3" else 1,
                path.name.casefold(),
            ),
        )
        tracks = [
            {
                "filename": path.name,
                "display_title": display_title_from_name(path.name).capitalize(),
                "duration_seconds": 0.0,
                "content_hash": hash_file(path),
                "original_name": path.name,
            }
            for path in music_files
        ]
    except OSError as error:
        raise SavedLetterRestoreError(
            "The legacy saved music is unreadable."
        ) from error
    return {
        "version": 2,
        "mode": "playlist" if len(tracks) > 1 else "single",
        "crossfade_ms": 1000 if len(tracks) > 1 else 0,
        "tracks": tracks,
    }


def _legacy_optional_sound_migration_allowed(
    metadata: dict[str, Any],
) -> bool:
    editable_assets = metadata.get("editable_assets", {})
    configured_manifest = (
        editable_assets.get("sound_manifest", "")
        if isinstance(editable_assets, dict)
        else ""
    )
    current_format = has_complete_saved_state(metadata)
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
    return not configured_manifest and not current_format and not music_required


def _backfill_legacy_optional_sound_manifest(
    play_dir: Path,
    metadata: dict[str, Any],
) -> None:
    if not _legacy_optional_sound_migration_allowed(metadata):
        return
    sounds = _runtime_directory(
        play_dir,
        "gallery/sounds",
        "gallery/user/sounds",
    )
    if sounds is None:
        return
    manifest = sounds / BUILD_SOUND_MANIFEST_NAME
    if manifest.exists() or manifest.is_symlink():
        return
    try:
        payload = _legacy_optional_sound_payload(sounds)
        atomic_write_json(manifest, payload)
    except (OSError, SavedLetterRestoreError):
        _LOGGER.warning(
            "Could not save the legacy optional-sound manifest for %s.",
            play_dir,
            exc_info=True,
        )
        return
    _LOGGER.info(
        "Saved the legacy optional-sound manifest for %s.",
        play_dir,
    )


def _readable_file(path: Path) -> bool:
    try:
        with path.open("rb") as stream:
            stream.read(1)
        return True
    except OSError:
        return False


class SavedLetterCatalog:
    def __init__(
        self,
        project_root: str | Path,
        *,
        stock_only: bool = False,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        cleanup_stale_letter_load_workspaces(self.project_root)
        self.stock_only = bool(stock_only)
        self.play_root = canonical_play_root(self.project_root)
        self.recovery_root = canonical_recovery_root(self.project_root)
        self.stock_root = canonical_stock_letters_root(self.project_root)
        self.example_root = application_paths(
            self.project_root
        ).examples_root.resolve()
        self.managed_roots = (
            (self.stock_root,)
            if self.stock_only
            else (self.play_root, self.recovery_root)
        )
        self._entries: tuple[SavedLetter, ...] | None = None

    @property
    def is_loaded(self) -> bool:
        return self._entries is not None

    def invalidate(self) -> None:
        """Require one reconciliation before the catalog is read again."""
        self._entries = None

    def list_entries(self, *, force_refresh: bool = False) -> tuple[SavedLetter, ...]:
        if self._entries is not None and not force_refresh:
            return self._entries
        entries: list[SavedLetter] = []
        seen: set[Path] = set()
        validator = SavedLetterRestorer(self.project_root)
        sources = (
            ((self.stock_root, False, False),)
            if self.stock_only
            else (
                (self.play_root, False, False),
                (self.recovery_root, True, False),
                (self.example_root, False, True),
            )
        )
        for root, recovery, example in sources:
            if not root.is_dir():
                continue
            for index in root.rglob("index.html"):
                path = index.parent.resolve()
                try:
                    relative = path.relative_to(root)
                except ValueError:
                    continue
                if any(
                    ".build-staging" in part
                    or ".build-backup" in part
                    or part.startswith(".letter-load-")
                    for part in relative.parts
                ):
                    continue
                if path in seen:
                    continue
                try:
                    if not self._is_valid_candidate(path):
                        continue
                    validator._validated_saved_letter_content(path)
                    entry = self._entry(
                        path,
                        recovery=recovery,
                        example=example,
                    )
                except Exception:
                    _LOGGER.warning(
                        "Skipping unloadable saved-letter candidate: %s",
                        path,
                        exc_info=True,
                    )
                    continue
                seen.add(path)
                entries.append(entry)
        if self.stock_only:
            entries.sort(
                key=lambda entry: (
                    self._natural_sort_key(entry.title),
                    self._natural_sort_key(entry.path.name),
                )
            )
        else:
            entries.sort(
                key=lambda entry: (
                    entry.example,
                    entry.modified_at,
                    entry.title.casefold(),
                ),
                reverse=True,
            )
        self._entries = tuple(entries)
        return self._entries

    def refresh_entry(self, path: str | Path) -> tuple[SavedLetter, ...] | None:
        """Update one known build without re-enumerating historical letters."""
        if self._entries is None:
            return None
        candidate = Path(path).resolve()
        recovery: bool | None = None
        for root, is_recovery in (
            (self.play_root, False),
            (self.recovery_root, True),
        ):
            try:
                relative = candidate.relative_to(root)
            except ValueError:
                continue
            if relative.parts:
                recovery = is_recovery
                break

        entries = [entry for entry in self._entries if entry.path != candidate]
        if recovery is not None and self._is_valid_candidate(candidate):
            entries.append(self._entry(candidate, recovery=recovery))
        entries.sort(
            key=lambda entry: (entry.modified_at, entry.title.casefold()),
            reverse=True,
        )
        self._entries = tuple(entries)
        return self._entries

    def search(self, query: str) -> tuple[SavedLetter, ...]:
        needle = (query or "").strip().casefold()
        if not needle:
            return self.list_entries()
        return tuple(
            entry
            for entry in self.list_entries()
            if needle in f"{entry.recipient} {entry.title}".casefold()
        )

    @staticmethod
    def _natural_sort_key(value: str) -> tuple[tuple[int, object], ...]:
        return tuple(
            (1, int(part)) if part.isdigit() else (0, part.casefold())
            for part in re.split(r"(\d+)", str(value))
            if part
        )

    def delete(self, entry: SavedLetter) -> Path:
        if not isinstance(entry, SavedLetter):
            raise SavedLetterDeleteError("The saved letter is invalid.")
        if self.stock_only:
            raise SavedLetterDeleteError("Stock letters cannot be deleted.")
        if entry.example:
            raise SavedLetterDeleteError(
                "The bundled Example Letter cannot be deleted."
            )
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
        for root in self.managed_roots:
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
        if self._entries is not None:
            self._entries = tuple(
                candidate
                for candidate in self._entries
                if candidate.path != target
            )
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
                not (pages / name).is_symlink()
                and (pages / name).is_file()
                and _readable_file(pages / name)
                for name in REQUIRED_SLIDES
            )
            and all(
                not (controls / name).is_symlink()
                and (controls / name).is_file()
                and _readable_file(controls / name)
                for name in CONTROL_FILES
            )
            and not (message / "message.html").is_symlink()
            and (message / "message.html").is_file()
            and _readable_file(message / "message.html")
        )

    def _entry(
        self,
        path: Path,
        *,
        recovery: bool = False,
        example: bool = False,
    ) -> SavedLetter:
        metadata = _read_metadata(path)
        recipient = self._display_text(metadata.get("recipient_name"))
        title = self._display_text(metadata.get("recipient_title"))
        if not title:
            title = self._html_title(path / "index.html") or "Untitled Letter"
        if not recipient:
            parent_is_category = path.parent in set(self.managed_roots)
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
            modified_at=_activity_datetime(metadata, path),
            published_url=normalize_published_page_url(
                metadata.get("published_page_url", "")
            ),
            cover_path=cover,
            published_public_path=str(
                metadata.get(PUBLISHED_PUBLIC_PATH_KEY, "")
            ).strip(),
            published_at=str(metadata.get(PUBLISHED_AT_KEY, "")).strip(),
            published_expires_at=str(
                metadata.get(PUBLISHED_EXPIRES_AT_KEY, "")
            ).strip(),
            publication_provider=str(
                metadata.get(PUBLICATION_PROVIDER_KEY, "")
            ).strip(),
            publication_verified=(
                metadata.get(PUBLICATION_VERIFIED_KEY) is True
            ),
            published_source_fingerprint=str(
                metadata.get(PUBLISHED_SOURCE_FINGERPRINT_KEY, "")
            ).strip(),
            recipient_id=_valid_uuid(metadata.get("recipient_id")),
            project_id=_valid_uuid(metadata.get("project_id")),
            recovery=recovery,
            example=example,
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

    def __init__(
        self,
        project_root: str | Path,
        *,
        resolver: ProjectPathResolver | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.settings = SettingsStore(self.project_root)
        self.resolver = resolver or ProjectPathResolver(
            self.project_root
        )
        self.registry = RecipientRegistry(self.project_root)
        self.allowed_roots = (
            canonical_play_root(self.project_root),
            canonical_recovery_root(self.project_root),
            canonical_stock_letters_root(self.project_root),
            application_paths(self.project_root).examples_root.resolve(),
        )

    def restore(self, entry: SavedLetter) -> RestoredProject:
        # Validate the selected catalog path before identity migration or any
        # active-project mutation. Autosave storage is write-only here.
        (
            play_dir,
            pages,
            message,
            sounds,
            metadata,
            prompt_writer_state,
            sound_payload,
            sound_tracks,
        ) = self._validated_saved_letter_content(
            entry.path,
            report_optional_sound_error=True,
        )
        entry = self.ensure_entry_identity(entry)
        settings_before = self.settings.snapshot()
        restored_settings = self._prepare_settings(
            metadata,
            entry,
            play_dir,
            settings_before,
        )

        staged_root = create_staging_directory(
            application_paths(self.project_root).temporary_root,
            prefix=".letter-load-",
        )
        _ACTIVE_LETTER_LOAD_WORKSPACES.add(staged_root)
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
        transactions = (pages_tx, message_tx)
        committed: list[PathTransaction] = []
        settings_committed = False
        file_snapshots = tuple(
            _FileSnapshot.capture(path)
            for path in (
                current_music_path(self.project_root),
                current_manifest_path(self.project_root),
                project_sound_path(self.project_root),
                self.project_root / PROMPT_WRITER_STATE_FILE,
            )
        )
        sound_archive_snapshot = _SoundArchiveSnapshot.capture(
            self.project_root
        )

        try:
            staged_pages = staged_root / USER_PAGES_DIR
            staged_message = staged_root / USER_MESSAGE_DIR
            shutil.copytree(pages, staged_pages)
            shutil.copytree(message, staged_message)

            shutil.copytree(staged_pages, pages_tx.prepare())
            shutil.copytree(staged_message, message_tx.prepare())

            for transaction in transactions:
                transaction.commit(keep_backup=True)
                committed.append(transaction)

            imported_ids: list[str] = []
            for track in sound_tracks:
                source = sounds / track["filename"] if sounds else None
                if source is None:
                    continue
                record = import_runtime_track(
                    self.project_root,
                    source,
                    display_title=track["display_title"],
                    original_name=track["original_name"],
                    # The manifest hash is metadata, not an identity assertion.
                    # Re-hash the source so same-name/different-content files
                    # cannot overwrite or alias one another.
                    content_hash="",
                    duration_seconds=track["duration_seconds"],
                )
                imported_ids.append(record.track_id)

            mode = (
                "playlist"
                if str(sound_payload.get("mode", "single")) == "playlist"
                and imported_ids
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
            save_project_state(self.project_root, state)
            sync_current_compatibility(
                self.project_root,
                state,
                load_library(self.project_root),
            )
            atomic_write_json(
                self.project_root / PROMPT_WRITER_STATE_FILE,
                prompt_writer_state,
            )

            self.settings.replace_snapshot(restored_settings)
            settings_committed = True
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
            for snapshot in file_snapshots:
                try:
                    snapshot.restore()
                except Exception:
                    _LOGGER.exception("Could not restore previous project file: %s", snapshot.path)
            try:
                sound_archive_snapshot.restore()
            except Exception:
                _LOGGER.exception(
                    "Could not roll back imported Music Archive files."
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
            try:
                _remove_tree(staged_root)
            except FileNotFoundError:
                pass
            except OSError:
                _LOGGER.exception("Could not clean Letter Smith load workspace: %s", staged_root)
            finally:
                _ACTIVE_LETTER_LOAD_WORKSPACES.discard(staged_root)

        for transaction in transactions:
            try:
                transaction.finalize()
            except OSError:
                _LOGGER.exception(
                    "Could not clean restoration backup for %s",
                    transaction.final_path,
                )
        _backfill_legacy_prompt_writer_state(
            play_dir,
            metadata,
            prompt_writer_state,
        )
        _backfill_legacy_optional_sound_manifest(
            play_dir,
            metadata,
        )
        return RestoredProject(
            play_dir=play_dir,
            project_id=str(restored_settings["project_id"]),
            recipient_id=str(restored_settings["recipient_id"]),
            recipient=str(restored_settings.get("recipient_name", "")),
            recipient_normalized_key=str(
                restored_settings.get(
                    "recipient_normalized_key",
                    "",
                )
            ),
            title=str(restored_settings.get("recipient_title", "")),
            published_url=str(
                restored_settings.get("published_page_url", "")
            ),
            published_public_path=str(
                restored_settings.get(PUBLISHED_PUBLIC_PATH_KEY, "")
            ),
            published_at=str(restored_settings.get(PUBLISHED_AT_KEY, "")),
            published_expires_at=str(
                restored_settings.get(PUBLISHED_EXPIRES_AT_KEY, "")
            ),
            publication_provider=str(
                restored_settings.get(PUBLICATION_PROVIDER_KEY, "")
            ),
            publication_verified=(
                restored_settings.get(PUBLICATION_VERIFIED_KEY) is True
            ),
            published_source_fingerprint=str(
                restored_settings.get(PUBLISHED_SOURCE_FINGERPRINT_KEY, "")
            ),
        )

    def ensure_entry_identity(
        self,
        entry: SavedLetter,
    ) -> SavedLetter:
        if entry.example:
            record = self.registry.get_or_create(
                entry.recipient or "A Friend",
                custom_capitalization=True,
            )
            return replace(
                entry,
                recipient_id=record.recipient_id,
                project_id=str(uuid.uuid4()),
            )
        if entry.recipient_id and entry.project_id:
            recipient = self.registry.find_by_id(entry.recipient_id)
            if recipient is not None:
                return entry
            if entry.recipient:
                return self.assign_recipient(
                    entry,
                    entry.recipient,
                    custom_capitalization=True,
                )
            raise RecipientAssignmentRequired(entry)
        if entry.needs_recipient_assignment:
            raise RecipientAssignmentRequired(entry)
        return self.assign_recipient(
            entry,
            entry.recipient,
            custom_capitalization=True,
        )

    def assign_recipient(
        self,
        entry: SavedLetter,
        recipient_name: str,
        *,
        custom_capitalization: bool = False,
    ) -> SavedLetter:
        source = self._validated_play_directory(entry.path)
        metadata = _read_metadata(source)
        record = self.registry.get_or_create(
            recipient_name,
            custom_capitalization=custom_capitalization,
            recipient_id=entry.recipient_id or None,
        )
        project_id = (
            entry.project_id
            or _valid_uuid(metadata.get("project_id"))
            or str(uuid.uuid4())
        )
        title = str(
            metadata.get("recipient_title")
            or entry.title
            or "Untitled Letter"
        ).strip()
        self.resolver.context_from_settings(
            {
                "recipient_id": record.recipient_id,
                "recipient_display_name": record.display_name,
                "recipient_normalized_key": record.normalized_key,
                "recipient_name": record.display_name,
                "project_id": project_id,
                "recipient_title": title,
            }
        )
        prompt_writer_state = _saved_prompt_writer_state(source, metadata)
        identity_metadata = dict(metadata)
        identity_metadata.update(
            {
                "project_schema_version": (
                    PROJECT_METADATA_SCHEMA_VERSION
                ),
                "project_id": project_id,
                "recipient_id": record.recipient_id,
                "recipient_display_name": record.display_name,
                "recipient_normalized_key": record.normalized_key,
                "recipient_name": record.display_name,
                "recipient_title": title,
            }
        )

        transaction = PathTransaction(
            source,
            staging_suffix=".identity-staging",
            backup_suffix=".identity-backup",
            unique_staging=True,
        )
        staging = transaction.prepare()
        try:
            shutil.copytree(source, staging)
            atomic_write_json(
                staging / PLAY_METADATA_FILE,
                identity_metadata,
            )
            atomic_write_json(
                staging / PROMPT_WRITER_STATE_FILE,
                prompt_writer_state,
            )
            self._validated_saved_letter_content(staging)
            transaction.commit(keep_backup=True)
            transaction.finalize()
        except Exception:
            transaction.abort()
            raise

        return SavedLetterCatalog(
            self.project_root
        )._entry(
            source,
            recovery=entry.recovery,
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

    def _validated_saved_letter_content(
        self,
        source: Path,
        *,
        report_optional_sound_error: bool = False,
    ) -> tuple[
        Path,
        Path,
        Path,
        Optional[Path],
        dict[str, Any],
        dict[str, Any],
        dict[str, Any],
        list[dict[str, Any]],
    ]:
        play_dir = self._validated_play_directory(source)
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
        metadata = _read_metadata(play_dir, strict=True)
        try:
            metadata = validate_saved_letter_metadata(metadata)
        except SaveSchemaError as error:
            raise SavedLetterRestoreError(
                f"The saved-letter metadata is invalid: {error}."
            ) from error
        self._validate_pages(
            pages,
            require_manifest=is_current_save_schema(metadata),
        )
        self._validate_message(play_dir, message)
        prompt_writer_state = _saved_prompt_writer_state(play_dir, metadata)
        legacy_optional_sound = _legacy_optional_sound_migration_allowed(
            metadata
        )
        try:
            sound_payload, sound_tracks = self._validate_sound(
                sounds,
                allow_legacy_optional=legacy_optional_sound,
            )
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
            if report_optional_sound_error:
                _LOGGER.warning(
                    "Ignoring invalid optional sound data while restoring %s",
                    play_dir,
                )
            sound_payload, sound_tracks = {"mode": "single", "tracks": []}, []
        return (
            play_dir,
            pages,
            message,
            sounds,
            metadata,
            prompt_writer_state,
            sound_payload,
            sound_tracks,
        )

    @staticmethod
    def _validate_pages(
        pages: Path,
        *,
        require_manifest: bool = False,
    ) -> None:
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
        manifest = pages / IMAGE_MANIFEST_NAME
        if require_manifest or manifest.exists():
            try:
                validate_runtime_image_manifest(pages)
            except ValueError as error:
                raise SavedLetterRestoreError(
                    "The saved image manifest is invalid."
                ) from error

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
        *,
        allow_legacy_optional: bool = False,
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
        elif allow_legacy_optional:
            payload = _legacy_optional_sound_payload(sounds)
            raw_tracks = payload["tracks"]
        elif any(sounds.iterdir()):
            raise SavedLetterRestoreError(
                "The saved sound manifest is missing."
            )
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
                    ).strip() or display_title_from_name(
                        str(raw.get("original_name", "") or filename)
                    ),
                    "original_name": str(
                        raw.get("original_name", filename)
                    ).strip(),
                    "content_hash": content_hash,
                    "duration_seconds": duration_seconds,
                }
            )
        return payload, tracks

    def _prepare_settings(
        self,
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
        recipient = str(metadata.get("recipient_name") or "").strip()
        if not recipient:
            recipient = str(entry.recipient or "").strip()
        if not recipient and play_dir.parent not in set(self.allowed_roots):
            recipient = play_dir.parent.name.replace("_", " ").replace("-", " ").strip()
        title = str(metadata.get("recipient_title") or "").strip()
        if not title:
            title = str(entry.title or "").strip()
        if not title:
            title = SavedLetterCatalog._html_title(play_dir / "index.html")
        if not title:
            title = play_dir.name.replace("_", " ").replace("-", " ").strip()
        restored["recipient_name"] = recipient
        restored["recipient_title"] = title
        restored.update(_publication_metadata(metadata))
        project_id = (
            _valid_uuid(metadata.get("project_id"))
            or entry.project_id
        )
        recipient_id = (
            _valid_uuid(metadata.get("recipient_id"))
            or entry.recipient_id
        )
        record = self.registry.find_by_id(recipient_id)
        if not project_id or record is None:
            raise RecipientAssignmentRequired(entry)
        restored["project_id"] = project_id
        restored["recipient_id"] = record.recipient_id
        restored["recipient_display_name"] = record.display_name
        restored["recipient_normalized_key"] = record.normalized_key
        restored["recipient_name"] = record.display_name
        restored[ACTIVE_PLAY_DIR_KEY] = str(play_dir.resolve())
        restored[PROJECT_SCHEMA_KEY] = PROJECT_METADATA_SCHEMA_VERSION
        return restored

    def _verify_committed_state(self) -> None:
        pages = self.project_root / USER_PAGES_DIR
        self._validate_pages(
            pages,
            require_manifest=(pages / IMAGE_MANIFEST_NAME).is_file(),
        )
        self._validate_message(
            self.project_root,
            self.project_root / USER_MESSAGE_DIR,
        )
        _read_json_object(
            self.project_root / PROMPT_WRITER_STATE_FILE,
            label="restored Prompt Writer state",
        )
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
    publication = _publication_metadata(settings)
    normalized_public_path = str(public_path).strip()
    if normalized_public_path:
        if (
            Path(normalized_public_path).name != normalized_public_path
            or normalized_public_path in {".", ".."}
        ):
            raise ValueError("The published path is invalid.")
        stored_public_path = str(
            publication.get(PUBLISHED_PUBLIC_PATH_KEY, "")
        ).strip()
        if stored_public_path and normalized_public_path != stored_public_path:
            raise ValueError("The published path does not match the active publication.")
        publication[PUBLISHED_PUBLIC_PATH_KEY] = normalized_public_path
    sound_state, sound_tracks = resolve_project_tracks(root)
    prompt_writer_state = _active_prompt_writer_state(root)
    atomic_write_json(
        destination / PROMPT_WRITER_STATE_FILE,
        prompt_writer_state,
    )
    restorable_settings = {
        key: settings[key]
        for key in RESTORABLE_SETTING_KEYS
        if key in settings
    }
    metadata.update(
        {
            "project_id": ensure_project_identity(root),
            "project_schema_version": PROJECT_METADATA_SCHEMA_VERSION,
            "recipient_id": str(
                settings.get("recipient_id", "")
            ).strip(),
            "recipient_display_name": str(
                settings.get("recipient_display_name")
                or settings.get("recipient_name", "")
            ).strip(),
            "recipient_normalized_key": str(
                settings.get("recipient_normalized_key", "")
            ).strip(),
            "recipient_name": str(
                settings.get("recipient_display_name")
                or settings.get("recipient_name", "")
            ).strip(),
            "recipient_title": str(
                settings.get("recipient_title", "")
            ).strip(),
            "build_timestamp": datetime.now(timezone.utc).isoformat(),
            **publication,
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
                "prompt_writer_state": PROMPT_WRITER_STATE_FILE,
                "image_manifest": (
                    "gallery/pages/lettersmith-images.json"
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
    metadata = stamp_current_save_schema(
        metadata,
        document_type=SAVED_LETTER_DOCUMENT_TYPE,
    )
    metadata.pop("public_path", None)
    metadata.pop("build_location", None)
    atomic_write_json(metadata_path, metadata)
    return metadata


def update_saved_publication_metadata(
    play_dir: str | Path,
    project_root: str | Path,
) -> dict[str, Any]:
    destination = Path(play_dir).resolve()
    if not destination.is_dir():
        raise FileNotFoundError(f"Saved letter does not exist: {destination}")
    metadata_path = destination / PLAY_METADATA_FILE
    metadata = _read_metadata(destination, strict=True)
    metadata.update(
        _publication_metadata(SettingsStore(project_root).snapshot())
    )
    metadata.pop("public_path", None)
    metadata = stamp_current_save_schema(
        metadata,
        document_type=SAVED_LETTER_DOCUMENT_TYPE,
    )
    atomic_write_json(metadata_path, metadata)
    return metadata


def record_saved_letter_activity(
    play_dir: str | Path,
    *,
    when: Optional[datetime] = None,
) -> str:
    """Record user-driven Preview, Publish, or public-URL activity."""
    destination = Path(play_dir).resolve()
    if not destination.is_dir():
        raise FileNotFoundError(f"Saved letter does not exist: {destination}")
    moment = when or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    timestamp = moment.astimezone(timezone.utc).isoformat()
    metadata = _read_metadata(destination)
    metadata[LAST_ACTIVITY_AT_KEY] = timestamp
    atomic_write_json(destination / PLAY_METADATA_FILE, metadata)
    return timestamp


def validate_saved_letter_bundle(
    source: str | Path,
    project_root: str | Path,
) -> dict[str, Any]:
    """Validate a saved-letter bundle without changing active project state."""
    validated = SavedLetterRestorer(project_root)._validated_saved_letter_content(
        Path(source).resolve()
    )
    return dict(validated[4])


__all__ = [
    "METADATA_VERSION",
    "LAST_ACTIVITY_AT_KEY",
    "PROMPT_WRITER_STATE_FILE",
    "RESTORABLE_SETTING_KEYS",
    "RecipientAssignmentRequired",
    "RestoredProject",
    "SavedLetter",
    "SavedLetterCatalog",
    "SavedLetterDeleteError",
    "SavedLetterRestoreError",
    "record_saved_letter_activity",
    "SavedLetterRestorer",
    "update_saved_metadata",
    "update_saved_publication_metadata",
    "validate_saved_letter_bundle",
]
