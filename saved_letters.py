from __future__ import annotations

import json
import io
import logging
import os
import re
import shutil
import stat
import uuid
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from html import unescape
from pathlib import Path
from typing import Any, Optional
from urllib.parse import unquote, urlsplit

from PIL import Image, ImageOps

from image_animation import (
    IMAGE_MANIFEST_NAME,
    validate_runtime_image_manifest,
)
from config import (
    CONTROL_FILES,
    MESSAGE_ASSETS_DIR,
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
from message_html import sanitize_message_html
from protected_projects import (
    EXAMPLE_PROJECT_KIND,
    PROTECTED_PROJECT_KIND_KEY,
    PROTECTED_PROJECT_MASTER_PATH_KEY,
    STOCK_PROJECT_KIND,
)
from performance_trace import performance_timed
from project_state import (
    PROJECT_SCHEMA_KEY,
    RECIPIENT_DISPLAY_NAME_KEY,
    RECIPIENT_ID_KEY,
    RECIPIENT_NORMALIZED_KEY,
    ProjectIdentity,
    ensure_project_identity,
)
from project_timestamps import (
    PROJECT_CREATED_AT_KEY,
    PROJECT_PUBLISHED_AT_KEY,
    current_project_timestamp,
    parse_project_timestamp,
    project_timestamp_date,
    valid_project_timestamp,
)
from recipient_registry import RecipientRegistry
from readiness import ReadinessResult
from publishing.expiration import publication_status as get_publication_status
from save_schema import (
    CURRENT_SAVE_SCHEMA_VERSION,
    SAVED_LETTER_DOCUMENT_TYPE,
    SaveSchemaError,
    is_current_save_schema,
    stamp_current_save_schema,
    validate_prompt_writer_state_payload,
    validate_saved_letter_metadata,
)
from settings_store import (
    ACTIVE_PLAY_DIR_KEY,
    DEFAULT_CURTAIN_STYLE,
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
    normalize_curtain_style,
    normalize_published_page_url,
)
from sound_model import (
    BUILD_SOUND_MANIFEST_NAME,
    ProjectSoundState,
    current_manifest_path,
    current_music_path,
    display_title_from_name,
    import_runtime_track,
    library_path,
    load_library,
    originals_dir,
    processed_dir,
    project_sound_path,
    resolve_project_tracks,
    save_library,
    save_project_state,
    sync_current_compatibility,
)
from transactional_io import (
    PathTransaction,
    atomic_write_bytes,
    atomic_write_json,
    atomic_write_text,
    copy_directory_tree_no_links,
    enforce_internal_tree_visibility,
    file_change_token,
    is_link_or_reparse_point,
    recover_stale_transactions,
)


METADATA_VERSION = CURRENT_SAVE_SCHEMA_VERSION
LAST_ACTIVITY_AT_KEY = "last_activity_at"
PROMPT_WRITER_STATE_FILE = "prompt_writer_state.json"
PROMPT_WRITER_METADATA_KEY = "prompt_writer"
PROMPT_WRITER_SNAPSHOT_SCHEMA_VERSION = 1
RESTORABLE_SETTING_KEYS = (
    "starting_volume",
    "music_volume",
    "curtain_style",
    "message_overlay_preset",
    "message_overlay_opacity",
    "required_features",
)
PUBLICATION_METADATA_KEYS = (
    PROJECT_PUBLISHED_AT_KEY,
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
_COVER_THUMBNAIL_RELATIVE = Path("gallery/pages/cover.thumbnail.png")
_COVER_THUMBNAIL_SOURCE_KEY = "cover_thumbnail_source_signature"
_COVER_THUMBNAIL_CACHE_VERSION = 1
_COVER_THUMBNAIL_SIZE = (336, 184)
_SAVED_CATALOG_INDEX_SCHEMA_VERSION = 3
_SAVED_CATALOG_INDEX_RELATIVE = Path(
    "saved_letters",
    "catalog-v3.json",
)


def _ensure_saved_cover_thumbnail(
    destination: Path,
    metadata: dict[str, Any],
) -> tuple[str, dict[str, int] | None]:
    source = destination / "gallery" / "pages" / "cover.png"
    fallback = "gallery/pages/cover.png"
    if not source.is_file():
        return fallback, None
    try:
        source_stat = source.stat()
        source_signature = {
            "version": _COVER_THUMBNAIL_CACHE_VERSION,
            "source_size": int(source_stat.st_size),
            "source_mtime_ns": int(source_stat.st_mtime_ns),
            "source_change_token": file_change_token(
                source,
                stat_result=source_stat,
            ),
            "maximum_width": _COVER_THUMBNAIL_SIZE[0],
            "maximum_height": _COVER_THUMBNAIL_SIZE[1],
        }
        thumbnail = destination / _COVER_THUMBNAIL_RELATIVE
        stored_signature = metadata.get(_COVER_THUMBNAIL_SOURCE_KEY)
        reusable = not thumbnail.is_symlink() and thumbnail.is_file()
        if reusable:
            try:
                thumbnail_stat = thumbnail.stat()
                expected_signature = {
                    **source_signature,
                    "thumbnail_size": int(thumbnail_stat.st_size),
                    "thumbnail_change_token": file_change_token(
                        thumbnail,
                        stat_result=thumbnail_stat,
                    ),
                }
                reusable = stored_signature == expected_signature
                with Image.open(thumbnail) as cached:
                    reusable = reusable and (
                        cached.format == "PNG"
                        and 0 < cached.width <= _COVER_THUMBNAIL_SIZE[0]
                        and 0 < cached.height <= _COVER_THUMBNAIL_SIZE[1]
                    )
                    if reusable:
                        cached.verify()
            except (OSError, SyntaxError, ValueError):
                reusable = False
        if reusable:
            return _COVER_THUMBNAIL_RELATIVE.as_posix(), expected_signature

        with Image.open(source) as opened:
            image = ImageOps.exif_transpose(opened)
            if image.mode not in {"RGB", "RGBA"}:
                image = image.convert("RGBA")
            image.thumbnail(_COVER_THUMBNAIL_SIZE, Image.Resampling.LANCZOS)
            payload = io.BytesIO()
            image.save(payload, format="PNG", optimize=True)
        atomic_write_bytes(thumbnail, payload.getvalue())
        thumbnail_stat = thumbnail.stat()
        signature = {
            **source_signature,
            "thumbnail_size": int(thumbnail_stat.st_size),
            "thumbnail_change_token": file_change_token(
                thumbnail,
                stat_result=thumbnail_stat,
            ),
        }
        return _COVER_THUMBNAIL_RELATIVE.as_posix(), signature
    except (OSError, ValueError):
        _LOGGER.warning(
            "Saved-letter cover thumbnail could not be generated: %s",
            source,
            exc_info=True,
        )
        return fallback, None


def _publication_metadata(state: dict[str, Any]) -> dict[str, Any]:
    return {
        PROJECT_PUBLISHED_AT_KEY: str(
            valid_project_timestamp(state.get(PROJECT_PUBLISHED_AT_KEY))
            or valid_project_timestamp(state.get(PUBLISHED_AT_KEY))
        ),
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
            root / MESSAGE_ASSETS_DIR,
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
    project_created_at: str = ""
    project_published_at: str = ""
    published_public_path: str = ""
    published_at: str = ""
    published_expires_at: str = ""
    publication_provider: str = ""
    publication_verified: bool = False
    published_source_fingerprint: str = ""
    published_github_owner: str = ""
    published_github_repository: str = ""
    recipient_id: str = ""
    project_id: str = ""
    recovery: bool = False
    example: bool = False
    stock: bool = False

    @property
    def created_sort_date(self) -> date:
        return project_timestamp_date(self.project_created_at)

    @property
    def saved_sort_date(self) -> date:
        created = self.created_sort_date
        published = project_timestamp_date(
            self.project_published_at,
            legacy_default=False,
        )
        return max(created, published) if published is not None else created

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
                PUBLISHED_GITHUB_OWNER_KEY: self.published_github_owner,
                PUBLISHED_GITHUB_REPOSITORY_KEY: self.published_github_repository,
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
    project_created_at: str = ""
    project_published_at: str = ""
    published_public_path: str = ""
    published_at: str = ""
    published_expires_at: str = ""
    publication_provider: str = ""
    publication_verified: bool = False
    published_source_fingerprint: str = ""
    published_github_owner: str = ""
    published_github_repository: str = ""

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
            PROJECT_CREATED_AT_KEY: self.project_created_at,
            PROJECT_PUBLISHED_AT_KEY: self.project_published_at,
            "published_page_url": self.published_url,
            PUBLISHED_PUBLIC_PATH_KEY: self.published_public_path,
            PUBLISHED_AT_KEY: self.published_at,
            PUBLISHED_EXPIRES_AT_KEY: self.published_expires_at,
            PUBLICATION_PROVIDER_KEY: self.publication_provider,
            PUBLICATION_VERIFIED_KEY: self.publication_verified,
            PUBLISHED_SOURCE_FINGERPRINT_KEY: self.published_source_fingerprint,
            PUBLISHED_GITHUB_OWNER_KEY: self.published_github_owner,
            PUBLISHED_GITHUB_REPOSITORY_KEY: self.published_github_repository,
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
    prompt_writer_metadata = metadata.get(PROMPT_WRITER_METADATA_KEY)
    if prompt_writer_metadata is not None:
        if not isinstance(prompt_writer_metadata, dict):
            raise SavedLetterRestoreError(
                "The saved Prompt Writer metadata is invalid."
            )
        if (
            prompt_writer_metadata.get("snapshot_schema_version")
            != PROMPT_WRITER_SNAPSHOT_SCHEMA_VERSION
        ):
            raise SavedLetterRestoreError(
                "The saved Prompt Writer snapshot version is unsupported."
            )
        embedded_state = prompt_writer_metadata.get("state")
        if not isinstance(embedded_state, dict):
            raise SavedLetterRestoreError(
                "The saved Prompt Writer snapshot is invalid."
            )
        return embedded_state

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
    raise SavedLetterRestoreError("The saved Prompt Writer state is missing.")


def _runtime_directory(
    play_dir: Path,
    relative_path: str,
) -> Optional[Path]:
    play_root = play_dir.resolve()
    candidate = play_dir / relative_path
    if not candidate.is_dir() or is_link_or_reparse_point(candidate):
        return None
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(play_root)
    except (OSError, ValueError):
        return None
    cursor = play_dir
    for part in Path(relative_path).parts:
        cursor = cursor / part
        if is_link_or_reparse_point(cursor):
            return None
    return candidate


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
        if not candidate.is_file() or is_link_or_reparse_point(candidate):
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
            if is_link_or_reparse_point(cursor):
                unsafe = True
                break
        if not unsafe:
            return resolved
    return None


def _required_feature_enabled(
    metadata: dict[str, Any],
    feature: str,
) -> bool:
    stored_settings = metadata.get("settings", {})
    raw_features = (
        stored_settings.get("required_features", [])
        if isinstance(stored_settings, dict)
        else []
    )
    normalized_feature = str(feature).strip().casefold()
    if isinstance(raw_features, dict):
        return bool(raw_features.get(feature, False))
    if isinstance(raw_features, str):
        raw_features = [raw_features]
    if not isinstance(raw_features, (list, tuple, set)):
        return False
    return normalized_feature in {
        str(value).strip().casefold()
        for value in raw_features
        if str(value).strip()
    }


def _bundle_matches_publication(path: Path, metadata: dict[str, Any]) -> bool:
    published_fingerprint = str(
        metadata.get(PUBLISHED_SOURCE_FINGERPRINT_KEY, "")
    ).strip()
    if not published_fingerprint:
        return True
    try:
        build = json.loads(
            (path / "lettersmith-build.json").read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return True
    if not isinstance(build, dict):
        return True
    source_fingerprint = str(build.get("source_fingerprint", "")).strip()
    return not source_fingerprint or source_fingerprint == published_fingerprint


def _readable_file(path: Path) -> bool:
    try:
        with path.open("rb") as stream:
            stream.read(1)
        return True
    except OSError:
        return False


def _sanitize_staged_message_tree(message_root: Path) -> None:
    """Sanitize the editable message and revisions in private staging."""
    candidates = [message_root / "message.html"]
    revisions = message_root / "revisions"
    if revisions.is_dir():
        candidates.extend(
            sorted(
                revisions.glob("*.html"),
                key=lambda path: path.name.casefold(),
            )
        )
    for path in candidates:
        if not path.is_file() or is_link_or_reparse_point(path):
            continue
        content = path.read_text(encoding="utf-8")
        atomic_write_text(path, sanitize_message_html(content))


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
        self.index_path = (
            application_paths(self.project_root).cache_root
            / _SAVED_CATALOG_INDEX_RELATIVE
        )
        self._entries: tuple[SavedLetter, ...] | None = None
        self._force_reconcile = False

    @property
    def is_loaded(self) -> bool:
        return self._entries is not None

    @property
    def requires_reconciliation(self) -> bool:
        return self._force_reconcile

    def load_persisted_entries(self) -> tuple[SavedLetter, ...] | None:
        """Load the bounded catalog index without falling back to a scan."""
        if self.stock_only or self._force_reconcile:
            return None
        if self._entries is not None:
            return self._entries
        cached = self._load_index()
        if cached is None:
            return None
        self._entries = cached
        return cached

    def accept_reconciled_entries(
        self,
        entries: tuple[SavedLetter, ...],
    ) -> tuple[SavedLetter, ...]:
        """Adopt entries produced by an authoritative background scan."""
        self._entries = tuple(entries)
        self._force_reconcile = False
        return self._entries

    def invalidate(self) -> None:
        """Require one reconciliation before the catalog is read again."""
        self._entries = None
        self._force_reconcile = True

    @performance_timed("saved_letters.list_entries")
    def list_entries(self, *, force_refresh: bool = False) -> tuple[SavedLetter, ...]:
        if self._entries is not None and not force_refresh:
            return self._entries
        if (
            not self.stock_only
            and not force_refresh
            and not self._force_reconcile
        ):
            cached = self._load_index()
            if cached is not None:
                self._entries = cached
                return self._entries
        entries: list[SavedLetter] = []
        seen: set[Path] = set()
        validator = SavedLetterRestorer(self.project_root)
        sources = (
            ((self.stock_root, False, False, True),)
            if self.stock_only
            else (
                (self.play_root, False, False, False),
                (self.recovery_root, True, False, False),
                (self.example_root, False, True, False),
            )
        )
        for root, recovery, example, stock in sources:
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
                        stock=stock,
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
            entries.sort(key=self._saved_sort_key)
        self._entries = tuple(entries)
        self._force_reconcile = False
        if not self.stock_only:
            self._persist_index(self._entries)
        return self._entries

    def refresh_entry(self, path: str | Path) -> tuple[SavedLetter, ...] | None:
        """Update one known build without re-enumerating historical letters."""
        if self.stock_only:
            return None
        if self._entries is None:
            if self._force_reconcile:
                return None
            self._entries = self._load_index()
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
        entries.sort(key=self._saved_sort_key)
        self._entries = tuple(entries)
        self._force_reconcile = False
        self._persist_index(self._entries)
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

    @staticmethod
    def _saved_sort_key(entry: SavedLetter) -> tuple[object, ...]:
        return (
            not entry.example,
            -entry.saved_sort_date.toordinal(),
            entry.title.casefold(),
            entry.recipient.casefold(),
            str(entry.path).casefold(),
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
        entries = self._entries
        if entries is None and not self._force_reconcile:
            entries = self._load_index()
        if entries is not None:
            self._entries = tuple(
                candidate
                for candidate in entries
                if candidate.path != target
            )
            self._force_reconcile = False
            self._persist_index(self._entries)
        return target

    def _persist_index(self, entries: tuple[SavedLetter, ...]) -> None:
        serialized: list[dict[str, Any]] = []
        for entry in entries:
            payload = self._serialize_index_entry(entry)
            if payload is None:
                _LOGGER.warning(
                    "Saved-letter index skipped an out-of-root entry: %s",
                    entry.path,
                )
                continue
            serialized.append(payload)
        try:
            atomic_write_json(
                self.index_path,
                {
                    "schema_version": _SAVED_CATALOG_INDEX_SCHEMA_VERSION,
                    "project_root": str(self.project_root),
                    "source_roots": {
                        source: str(root)
                        for source, root, _recovery, _example
                        in self._index_sources()
                    },
                    "entries": serialized,
                },
            )
        except (OSError, TypeError, ValueError):
            _LOGGER.warning(
                "Saved-letter catalog index could not be written: %s",
                self.index_path,
                exc_info=True,
            )

    def _load_index(self) -> tuple[SavedLetter, ...] | None:
        if not self.index_path.is_file() or self.index_path.is_symlink():
            return None
        try:
            payload = json.loads(self.index_path.read_text(encoding="utf-8"))
            if (
                not isinstance(payload, dict)
                or payload.get("schema_version")
                != _SAVED_CATALOG_INDEX_SCHEMA_VERSION
                or Path(str(payload.get("project_root", ""))).resolve()
                != self.project_root
                or payload.get("source_roots")
                != {
                    source: str(root)
                    for source, root, _recovery, _example
                    in self._index_sources()
                }
            ):
                return None
            raw_entries = payload.get("entries")
            if not isinstance(raw_entries, list):
                return None
            entries: list[SavedLetter] = []
            seen: set[Path] = set()
            for raw_entry in raw_entries:
                entry = self._deserialize_index_entry(raw_entry)
                if entry is None or entry.path in seen:
                    return None
                seen.add(entry.path)
                entries.append(entry)
            return tuple(entries)
        except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
            _LOGGER.warning(
                "Saved-letter catalog index is unreadable and will be rebuilt: %s",
                self.index_path,
                exc_info=True,
            )
            return None

    def _serialize_index_entry(
        self,
        entry: SavedLetter,
    ) -> dict[str, Any] | None:
        source = ""
        relative: Path | None = None
        for (
            candidate_source,
            candidate_root,
            recovery,
            example,
        ) in self._index_sources():
            if entry.recovery != recovery or entry.example != example:
                continue
            try:
                relative = entry.path.relative_to(candidate_root)
            except ValueError:
                continue
            source = candidate_source
            break
        if (
            relative is None
            or entry.stock
            or not self._safe_index_relative(relative)
        ):
            return None

        cover_relative = ""
        if entry.cover_path is not None:
            try:
                cover = entry.cover_path.relative_to(entry.path)
            except ValueError:
                return None
            if not self._safe_index_relative(cover):
                return None
            cover_relative = cover.as_posix()

        return {
            "source": source,
            "relative_path": relative.as_posix(),
            "recipient": entry.recipient,
            "title": entry.title,
            "modified_at": entry.modified_at.isoformat(),
            PROJECT_CREATED_AT_KEY: entry.project_created_at,
            PROJECT_PUBLISHED_AT_KEY: entry.project_published_at,
            "published_url": entry.published_url,
            "cover_path": cover_relative,
            "published_public_path": entry.published_public_path,
            "published_at": entry.published_at,
            "published_expires_at": entry.published_expires_at,
            "publication_provider": entry.publication_provider,
            "publication_verified": entry.publication_verified,
            "published_source_fingerprint": (
                entry.published_source_fingerprint
            ),
            "published_github_owner": entry.published_github_owner,
            "published_github_repository": (
                entry.published_github_repository
            ),
            "recipient_id": entry.recipient_id,
            "project_id": entry.project_id,
        }

    def _deserialize_index_entry(
        self,
        raw_entry: object,
    ) -> SavedLetter | None:
        if not isinstance(raw_entry, dict):
            return None
        source = str(raw_entry.get("source", ""))
        source_spec = next(
            (
                candidate
                for candidate in self._index_sources()
                if candidate[0] == source
            ),
            None,
        )
        if source_spec is None:
            return None
        _source, root, recovery, example = source_spec
        relative = Path(str(raw_entry.get("relative_path", "")))
        if not self._safe_index_relative(relative):
            return None
        path = root if str(relative) == "." else root / relative

        raw_cover = str(raw_entry.get("cover_path", ""))
        cover_path: Path | None = None
        if raw_cover:
            cover_relative = Path(raw_cover)
            if not self._safe_index_relative(cover_relative):
                return None
            cover_path = path / cover_relative

        try:
            modified_at = datetime.fromisoformat(
                str(raw_entry.get("modified_at", ""))
            )
        except (ValueError, TypeError):
            return None
        return SavedLetter(
            path=path,
            recipient=str(raw_entry.get("recipient", "")),
            title=str(raw_entry.get("title", "")),
            modified_at=modified_at,
            published_url=normalize_published_page_url(
                raw_entry.get("published_url", "")
            ),
            cover_path=cover_path,
            project_created_at=str(
                raw_entry.get(PROJECT_CREATED_AT_KEY, "")
            ).strip(),
            project_published_at=str(
                raw_entry.get(PROJECT_PUBLISHED_AT_KEY, "")
            ).strip(),
            published_public_path=str(
                raw_entry.get("published_public_path", "")
            ).strip(),
            published_at=str(raw_entry.get("published_at", "")).strip(),
            published_expires_at=str(
                raw_entry.get("published_expires_at", "")
            ).strip(),
            publication_provider=str(
                raw_entry.get("publication_provider", "")
            ).strip(),
            publication_verified=(
                raw_entry.get("publication_verified") is True
            ),
            published_source_fingerprint=str(
                raw_entry.get("published_source_fingerprint", "")
            ).strip(),
            published_github_owner=str(
                raw_entry.get("published_github_owner", "")
            ).strip(),
            published_github_repository=str(
                raw_entry.get("published_github_repository", "")
            ).strip(),
            recipient_id=_valid_uuid(raw_entry.get("recipient_id")),
            project_id=_valid_uuid(raw_entry.get("project_id")),
            recovery=recovery,
            example=example,
        )

    def _index_sources(
        self,
    ) -> tuple[tuple[str, Path, bool, bool], ...]:
        return (
            ("play", self.play_root, False, False),
            ("recovery", self.recovery_root, True, False),
            ("example", self.example_root, False, True),
        )

    @staticmethod
    def _safe_index_relative(relative: Path) -> bool:
        return bool(
            not relative.is_absolute()
            and not relative.drive
            and ".." not in relative.parts
            and bool(relative.parts)
            and str(relative) != "."
        )

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
        )
        message = _runtime_directory(
            path,
            "gallery/message",
        )
        controls = _runtime_directory(
            path,
            "gallery/controls",
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
        stock: bool = False,
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
            project_created_at=str(
                metadata.get(PROJECT_CREATED_AT_KEY, "")
            ).strip(),
            project_published_at=str(
                valid_project_timestamp(
                    metadata.get(PROJECT_PUBLISHED_AT_KEY)
                )
                or valid_project_timestamp(metadata.get(PUBLISHED_AT_KEY))
            ),
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
                and _bundle_matches_publication(path, metadata)
            ),
            published_source_fingerprint=str(
                metadata.get(PUBLISHED_SOURCE_FINGERPRINT_KEY, "")
            ).strip(),
            published_github_owner=str(
                metadata.get(PUBLISHED_GITHUB_OWNER_KEY, "")
            ).strip(),
            published_github_repository=str(
                metadata.get(PUBLISHED_GITHUB_REPOSITORY_KEY, "")
            ).strip(),
            recipient_id=_valid_uuid(metadata.get("recipient_id")),
            project_id=_valid_uuid(metadata.get("project_id")),
            recovery=recovery,
            example=example,
            stock=stock,
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
            message_assets,
        ) = self._validated_saved_letter_content(
            entry.path,
        )
        created_timestamp = str(
            metadata.get(PROJECT_CREATED_AT_KEY, "")
        ).strip()
        needs_created_timestamp = (
            parse_project_timestamp(created_timestamp) is None
        )
        if needs_created_timestamp:
            created_timestamp = current_project_timestamp()
            metadata = dict(metadata)
            metadata[PROJECT_CREATED_AT_KEY] = created_timestamp
        project_published_timestamp = (
            valid_project_timestamp(
                metadata.get(PROJECT_PUBLISHED_AT_KEY)
            )
            or valid_project_timestamp(metadata.get(PUBLISHED_AT_KEY))
        )
        metadata[PROJECT_PUBLISHED_AT_KEY] = project_published_timestamp
        entry = self.ensure_entry_identity(entry)
        settings_before = self.settings.snapshot()
        restored_settings = self._prepare_settings(
            metadata,
            entry,
            play_dir,
            settings_before,
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
        message_assets_tx = PathTransaction(
            self.project_root / MESSAGE_ASSETS_DIR,
            staging_suffix=".load-staging",
            backup_suffix=".load-backup",
            unique_staging=True,
        )
        transactions = (pages_tx, message_tx, message_assets_tx)
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
        message_assets_source = (
            message_assets
            if message_assets is not None
            and any(
                is_link_or_reparse_point(path) or not path.is_dir()
                for path in message_assets.rglob("*")
            )
            else None
        )

        try:
            copy_directory_tree_no_links(pages, pages_tx.prepare())
            message_staging = message_tx.prepare()
            copy_directory_tree_no_links(message, message_staging)
            _sanitize_staged_message_tree(message_staging)
            message_assets_staging = message_assets_tx.prepare()
            if message_assets_source is not None:
                copy_directory_tree_no_links(
                    message_assets_source,
                    message_assets_staging,
                )

            for transaction in transactions:
                transaction.commit(
                    replace=(
                        transaction is not message_assets_tx
                        or message_assets_source is not None
                    ),
                    keep_backup=True,
                )
                committed.append(transaction)

            imported_ids: list[str] = []
            library_records = load_library(self.project_root)
            original_library_ids = set(library_records)
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
                    records=library_records,
                    persist=False,
                )
                imported_ids.append(record.track_id)
            if set(library_records) != original_library_ids:
                save_library(self.project_root, library_records)

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
                playlist_expanded=bool(
                    sound_payload.get("playlist_expanded", True)
                ),
                selected_track_id=(
                    imported_ids[
                        int(sound_payload.get("selected_track_index", 0))
                    ]
                    if imported_ids
                    else ""
                ),
            )
            save_project_state(self.project_root, state)
            sync_current_compatibility(
                self.project_root,
                state,
                library_records,
            )
            atomic_write_json(
                self.project_root / PROMPT_WRITER_STATE_FILE,
                prompt_writer_state,
            )

            self.settings.replace_snapshot(restored_settings)
            settings_committed = True
            self._verify_committed_state()
            if needs_created_timestamp and not (
                entry.example or entry.stock or entry.recovery
            ):
                persisted_metadata = _read_metadata(play_dir, strict=True)
                persisted_metadata[PROJECT_CREATED_AT_KEY] = created_timestamp
                atomic_write_json(
                    play_dir / PLAY_METADATA_FILE,
                    persisted_metadata,
                )
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
            recipient_id=str(restored_settings["recipient_id"]),
            recipient=str(restored_settings.get("recipient_name", "")),
            recipient_normalized_key=str(
                restored_settings.get(
                    "recipient_normalized_key",
                    "",
                )
            ),
            title=str(restored_settings.get("recipient_title", "")),
            project_created_at=str(
                restored_settings.get(PROJECT_CREATED_AT_KEY, "")
            ),
            project_published_at=str(
                restored_settings.get(PROJECT_PUBLISHED_AT_KEY, "")
            ),
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
            published_github_owner=str(
                restored_settings.get(PUBLISHED_GITHUB_OWNER_KEY, "")
            ),
            published_github_repository=str(
                restored_settings.get(PUBLISHED_GITHUB_REPOSITORY_KEY, "")
            ),
        )

    def ensure_entry_identity(
        self,
        entry: SavedLetter,
    ) -> SavedLetter:
        if entry.example or entry.stock:
            record = self.registry.get_or_create(
                entry.recipient
                or ("A Friend" if entry.example else "Stock"),
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
        if entry.example or entry.stock:
            record = self.registry.get_or_create(
                recipient_name,
                custom_capitalization=custom_capitalization,
            )
            return replace(
                entry,
                recipient=record.display_name,
                recipient_id=record.recipient_id,
                project_id=str(uuid.uuid4()),
            )
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
        if entry.recovery:
            return replace(
                entry,
                recipient=record.display_name,
                recipient_id=record.recipient_id,
                project_id=project_id,
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
            copy_directory_tree_no_links(source, staging)
            staged_message = staging / "gallery" / "message"
            if staged_message.is_dir():
                _sanitize_staged_message_tree(staged_message)
            atomic_write_json(
                staging / PLAY_METADATA_FILE,
                identity_metadata,
            )
            atomic_write_json(
                staging / PROMPT_WRITER_STATE_FILE,
                prompt_writer_state,
            )
            enforce_internal_tree_visibility(staging)
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
        if is_link_or_reparse_point(original):
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
    ) -> tuple[
        Path,
        Path,
        Path,
        Optional[Path],
        dict[str, Any],
        dict[str, Any],
        dict[str, Any],
        list[dict[str, Any]],
        Optional[Path],
    ]:
        play_dir = self._validated_play_directory(source)
        pages = _runtime_directory(
            play_dir,
            "gallery/pages",
        )
        message = _runtime_directory(
            play_dir,
            "gallery/message",
        )
        sounds = _runtime_directory(
            play_dir,
            "gallery/sounds",
        )
        message_assets = _runtime_directory(
            play_dir,
            MESSAGE_ASSETS_DIR,
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
        try:
            prompt_writer_state = validate_prompt_writer_state_payload(
                _saved_prompt_writer_state(play_dir, metadata)
            )
        except SaveSchemaError as error:
            raise SavedLetterRestoreError(
                f"The saved Prompt Writer state is invalid: {error}."
            ) from error
        sound_payload, sound_tracks = self._validate_sound(sounds)
        if _required_feature_enabled(metadata, "music") and not sound_tracks:
            raise SavedLetterRestoreError(
                "The saved letter requires music, but no saved track is available."
            )
        return (
            play_dir,
            pages,
            message,
            sounds,
            metadata,
            prompt_writer_state,
            sound_payload,
            sound_tracks,
            message_assets,
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
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        if sounds is None:
            raise SavedLetterRestoreError("The saved sound manifest is missing.")
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
        else:
            raise SavedLetterRestoreError("The saved sound manifest is missing.")

        mode = str(payload.get("mode", "single")).strip()
        if mode not in {"single", "playlist"}:
            raise SavedLetterRestoreError(
                "The saved sound manifest has an invalid playback mode."
            )
        playlist_expanded = payload.get("playlist_expanded", True)
        if not isinstance(playlist_expanded, bool):
            raise SavedLetterRestoreError(
                "The saved sound manifest has an invalid playlist state."
            )

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
        default_selected_index = 0 if tracks else -1
        selected_track_index = payload.get(
            "selected_track_index",
            default_selected_index,
        )
        if (
            isinstance(selected_track_index, bool)
            or not isinstance(selected_track_index, int)
            or (
                tracks
                and not 0 <= selected_track_index < len(tracks)
            )
            or (not tracks and selected_track_index != -1)
        ):
            raise SavedLetterRestoreError(
                "The saved sound manifest has an invalid selected track."
            )
        validated_payload = dict(payload)
        validated_payload.update(
            {
                "mode": mode,
                "playlist_expanded": playlist_expanded,
                "selected_track_index": selected_track_index,
            }
        )
        return validated_payload, tracks

    def _prepare_settings(
        self,
        metadata: dict[str, Any],
        entry: SavedLetter,
        play_dir: Path,
        settings_before: dict[str, Any],
    ) -> dict[str, Any]:
        restored = dict(settings_before)
        restored["curtain_style"] = DEFAULT_CURTAIN_STYLE
        stored_settings = metadata.get("settings", {})
        if isinstance(stored_settings, dict):
            for key in RESTORABLE_SETTING_KEYS:
                if key in stored_settings:
                    restored[key] = (
                        normalize_curtain_style(stored_settings[key])
                        if key == "curtain_style"
                        else stored_settings[key]
                    )
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
        restored[PROJECT_CREATED_AT_KEY] = str(
            metadata.get(PROJECT_CREATED_AT_KEY, "")
        ).strip()
        restored[PROJECT_PUBLISHED_AT_KEY] = str(
            valid_project_timestamp(metadata.get(PROJECT_PUBLISHED_AT_KEY))
            or valid_project_timestamp(metadata.get(PUBLISHED_AT_KEY))
        )
        restored.update(_publication_metadata(metadata))
        protected_entry = entry.stock or entry.example
        project_id = (
            entry.project_id
            if protected_entry
            else (
                _valid_uuid(metadata.get("project_id"))
                or entry.project_id
            )
        )
        recipient_id = (
            entry.recipient_id
            if protected_entry or entry.recovery
            else (
                _valid_uuid(metadata.get("recipient_id"))
                or entry.recipient_id
            )
        )
        record = self.registry.find_by_id(recipient_id)
        if not project_id or record is None:
            raise RecipientAssignmentRequired(entry)
        restored["project_id"] = project_id
        restored["recipient_id"] = record.recipient_id
        restored["recipient_display_name"] = record.display_name
        restored["recipient_normalized_key"] = record.normalized_key
        restored["recipient_name"] = record.display_name
        if entry.stock or entry.example:
            restored[PROTECTED_PROJECT_KIND_KEY] = (
                STOCK_PROJECT_KIND if entry.stock else EXAMPLE_PROJECT_KIND
            )
            restored[PROTECTED_PROJECT_MASTER_PATH_KEY] = str(
                play_dir.resolve()
            )
            restored[ACTIVE_PLAY_DIR_KEY] = ""
        else:
            restored[PROTECTED_PROJECT_KIND_KEY] = ""
            restored[PROTECTED_PROJECT_MASTER_PATH_KEY] = ""
            restored[ACTIVE_PLAY_DIR_KEY] = (
                str(play_dir.resolve())
                if play_dir.is_relative_to(canonical_play_root(self.project_root))
                else ""
            )
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


def _is_bundled_letter_master(path: str | Path) -> bool:
    destination = Path(path).resolve()
    for parent in (destination, *destination.parents):
        if (
            parent.name.casefold() == "examples"
            and parent.parent.name.casefold() == "resources"
        ):
            return True
        if (
            parent.name.casefold() == "letters"
            and parent.parent.name.casefold() == "stock"
            and parent.parent.parent.name.casefold() == "resources"
        ):
            return True
    return False


def update_saved_metadata(
    play_dir: str | Path,
    project_root: str | Path,
    readiness: ReadinessResult,
    *,
    public_path: str = "",
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    destination = Path(play_dir).resolve()
    if _is_bundled_letter_master(destination):
        return _read_metadata(destination)
    metadata_path = destination / PLAY_METADATA_FILE
    metadata = _read_metadata(
        destination,
        strict=metadata_path.exists() or metadata_path.is_symlink(),
    )
    cover_thumbnail_path, cover_thumbnail_signature = (
        _ensure_saved_cover_thumbnail(destination, metadata)
    )
    project_id = ensure_project_identity(root)
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
            "project_id": project_id,
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
            PROJECT_CREATED_AT_KEY: str(
                settings.get(PROJECT_CREATED_AT_KEY, "")
            ).strip(),
            "build_timestamp": datetime.now(timezone.utc).isoformat(),
            **publication,
            "settings": restorable_settings,
            PROMPT_WRITER_METADATA_KEY: {
                "snapshot_schema_version": (
                    PROMPT_WRITER_SNAPSHOT_SCHEMA_VERSION
                ),
                "state_file": PROMPT_WRITER_STATE_FILE,
                "state": prompt_writer_state,
            },
            "editable_assets": {
                "pages": {
                    name: f"gallery/pages/{name}"
                    for name in REQUIRED_SLIDES
                },
                "message": "gallery/message/message.html",
                "message_assets": MESSAGE_ASSETS_DIR,
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
            "cover_thumbnail_path": cover_thumbnail_path,
        }
    )
    if cover_thumbnail_signature is None:
        metadata.pop(_COVER_THUMBNAIL_SOURCE_KEY, None)
    else:
        metadata[_COVER_THUMBNAIL_SOURCE_KEY] = cover_thumbnail_signature
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
    if _is_bundled_letter_master(destination):
        return _read_metadata(destination)
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


def save_published_snapshot(
    play_dir: str | Path,
    project_root: str | Path,
    publication: dict[str, Any],
) -> Path:
    """Keep the verified publication restorable as the working Play bundle changes."""
    root = Path(project_root).resolve()
    source = Path(play_dir).resolve(strict=True)
    if not source.is_relative_to(canonical_play_root(root)):
        raise ValueError("The published source must be a Play bundle.")
    if get_publication_status(publication) != "published":
        raise ValueError("The publication is not verified.")
    metadata = _read_metadata(source, strict=True)
    project_id = _valid_uuid(metadata.get("project_id"))
    if not project_id:
        raise ValueError("The published letter has no valid project identity.")

    destination = canonical_recovery_root(root) / "Published" / project_id
    transaction = PathTransaction(
        destination,
        staging_suffix=".publish-staging",
        backup_suffix=".publish-backup",
        unique_staging=True,
    )
    committed = False
    try:
        staging = transaction.prepare()
        copy_directory_tree_no_links(source, staging)
        metadata.update(_publication_metadata(publication))
        atomic_write_json(staging / PLAY_METADATA_FILE, metadata)
        SavedLetterRestorer(root)._validated_saved_letter_content(staging)
        transaction.commit(keep_backup=True)
        committed = True
    except Exception:
        transaction.abort()
        raise
    if committed:
        try:
            transaction.finalize()
        except OSError:
            _LOGGER.exception("Published snapshot backup cleanup failed: %s", destination)
    return destination


def record_saved_letter_activity(
    play_dir: str | Path,
    *,
    when: Optional[datetime] = None,
) -> str:
    """Record user-driven Preview, Publish, or public-URL activity."""
    destination = Path(play_dir).resolve()
    if not destination.is_dir():
        raise FileNotFoundError(f"Saved letter does not exist: {destination}")
    if _is_bundled_letter_master(destination):
        return ""
    moment = when or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    timestamp = moment.astimezone(timezone.utc).isoformat()
    metadata_path = destination / PLAY_METADATA_FILE
    metadata = _read_metadata(
        destination,
        strict=metadata_path.exists() or metadata_path.is_symlink(),
    )
    metadata[LAST_ACTIVITY_AT_KEY] = timestamp
    atomic_write_json(metadata_path, metadata)
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
    "PROMPT_WRITER_METADATA_KEY",
    "PROMPT_WRITER_SNAPSHOT_SCHEMA_VERSION",
    "PROMPT_WRITER_STATE_FILE",
    "RESTORABLE_SETTING_KEYS",
    "RecipientAssignmentRequired",
    "RestoredProject",
    "SavedLetter",
    "SavedLetterCatalog",
    "SavedLetterDeleteError",
    "SavedLetterRestoreError",
    "save_published_snapshot",
    "record_saved_letter_activity",
    "SavedLetterRestorer",
    "update_saved_metadata",
    "update_saved_publication_metadata",
    "validate_saved_letter_bundle",
]
