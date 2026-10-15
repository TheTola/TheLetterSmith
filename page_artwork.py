"""Page-ID artwork operations using the shared project's target guards.

Call ``capture_target`` before opening a picker or crop dialog, prepare media
without changing authored state, then apply to that same target. The UI owns
session persistence; this module never writes compatibility manifests.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path, PureWindowsPath
import shutil
from typing import Sequence

from PIL import Image

from image_animation import _write_first_frame_png, _write_static_png, default_gif_settings
from project_model import ProjectModel
from project_session import ProjectSession, WorkTarget
from transactional_io import atomic_copy_file, create_staging_directory


_SUPPORTED_FORMATS = frozenset(("PNG", "JPEG", "BMP", "GIF"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class PreparedArtworkImport:
    """Validated private media; ``abort`` also releases a canceled preparation.

    ``source_path`` identifies the input; ``asset_path`` is the staged original
    animated GIF or normalized static PNG. Metadata describes ``asset_path``.
    All staged paths expire after ``apply_import`` or ``abort``.
    """

    source_path: Path
    asset_path: Path
    preview_path: Path
    media_type: str
    animated: bool
    frame_count: int
    pixel_size: tuple[int, int]
    sha256: str
    _directory: Path
    _workspace: Path

    def abort(self) -> None:
        directory = self._directory.resolve()
        directory.relative_to(self._workspace)
        if directory == self._workspace or not directory.name.startswith(".artwork-import-"):
            raise ValueError("Artwork staging directory is invalid.")
        if directory.is_dir():
            shutil.rmtree(directory)


class PageArtworkController:
    """Import, clear and derive artwork without changing another page's asset."""

    def __init__(self, session: ProjectSession):
        if session.project_root is None:
            raise ValueError("Artwork editing requires an isolated project workspace.")
        self.session = session
        self.project_root = session.project_root.resolve()

    def capture_target(self, page_id: str) -> WorkTarget:
        return self.session.capture_target(
            page_id, fields=("type", "artwork"), include_shared=False,
        )

    def _staging_directory(self) -> Path:
        return create_staging_directory(self.project_root, prefix=".artwork-import-")

    def stage_import(self, source: str | Path) -> PreparedArtworkImport:
        """Validate actual media and every frame before preparing replacement.

        Animated GIF bytes are retained exactly, including their loop and frame
        instructions. Static imports follow the established PNG conversion path.
        """
        source_path = Path(source).resolve()
        directory = self._staging_directory()
        try:
            copied_source = atomic_copy_file(source_path, directory / "source")
            with Image.open(copied_source) as image:
                actual_format = image.format
                if actual_format not in _SUPPORTED_FORMATS:
                    raise ValueError("Choose a PNG, JPEG, BMP or GIF image.")
                image.verify()
            with Image.open(copied_source) as image:
                frame_count = int(getattr(image, "n_frames", 1))
                for frame_index in range(frame_count):
                    image.seek(frame_index)
                    image.load()
            animated = actual_format == "GIF" and frame_count > 1
            if frame_count > 1 and not animated:
                raise ValueError("Animated images must be GIF files.")
            asset_path = directory / ("artwork.gif" if animated else "artwork.png")
            preview_path = directory / "preview.png"
            if animated:
                atomic_copy_file(copied_source, asset_path)
                _write_first_frame_png(asset_path, preview_path)
            else:
                _write_static_png(copied_source, asset_path)
                atomic_copy_file(asset_path, preview_path)
            with Image.open(asset_path) as image:
                pixel_size = image.size
            return PreparedArtworkImport(
                source_path=source_path, asset_path=asset_path, preview_path=preview_path,
                media_type="image/gif" if animated else "image/png", animated=animated,
                frame_count=frame_count, pixel_size=pixel_size, sha256=_sha256(asset_path),
                _directory=directory, _workspace=self.project_root,
            )
        except BaseException:
            directory.resolve().relative_to(self.project_root)
            shutil.rmtree(directory)
            raise

    def apply_import(self, target: WorkTarget, prepared: PreparedArtworkImport) -> bool:
        """Commit only a still-current target; always release prepared media.

        Selection, names and unrelated fields do not redirect or reject work.
        Artwork/type edits, deletion or project replacement invalidate it. New
        media uses a content-addressed path; existing source assets stay intact.
        """
        try:
            if prepared._workspace != self.project_root:
                raise ValueError("Prepared artwork belongs to another workspace.")
            if not prepared.asset_path.is_file():
                return False

            def apply(project: ProjectModel, page_id: str) -> None:
                if _sha256(prepared.asset_path) != prepared.sha256:
                    raise ValueError("Prepared artwork changed before it could be applied.")
                suffix = ".gif" if prepared.animated else ".png"
                relative = Path("artwork") / f"{prepared.sha256}{suffix}"
                destination = (self.project_root / relative).resolve()
                destination.relative_to(self.project_root)
                if destination.exists():
                    if not destination.is_file() or _sha256(destination) != prepared.sha256:
                        raise ValueError("An existing artwork asset is damaged.")
                else:
                    atomic_copy_file(prepared.asset_path, destination)
                project.update_artwork(page_id, {
                    "asset": relative.as_posix(), "media_type": prepared.media_type,
                    "placement": {},
                    "animation": default_gif_settings() if prepared.animated else {},
                })

            return self.session.apply_result(target, apply)
        finally:
            prepared.abort()

    def resolve_asset(self, page_id: str) -> Path | None:
        """Resolve a canonical relative reference within saved/workspace roots."""
        raw = self.session.project.get_page(page_id)["artwork"].get("asset")
        if not raw:
            return None
        relative = Path(raw)
        windows_relative = PureWindowsPath(raw)
        if (relative.is_absolute() or windows_relative.drive or windows_relative.root
                or ".." in relative.parts or ".." in windows_relative.parts):
            return None
        roots = (self.session.asset_root, self.project_root)
        for root in roots:
            if root is None:
                continue
            resolved_root = root.resolve()
            candidate = (resolved_root / relative).resolve()
            try:
                candidate.relative_to(resolved_root)
            except ValueError:
                continue
            if candidate.is_file():
                return candidate
        return None

    def clear_image(self, page_id: str) -> bool:
        """Clear only this page's artwork state; retain all source media."""
        target = self.capture_target(page_id)
        return self.session.apply_result(target, lambda project, captured_id: project.update_artwork(
            captured_id, {"asset": None, "media_type": None, "placement": {}, "animation": {}},
        ))

    def crop(self, page_id: str, rectangle: Sequence[int], *, target: WorkTarget | None = None) -> bool:
        """Derive a static PNG from ``(left, top, right, bottom)`` pixel edges.

        Pass a captured target when a dialog precedes this operation. Animated
        GIF cropping is deliberately rejected because the static crop tool does
        not preserve its animation. A duplicate's shared source is never edited.
        """
        if target is None:
            target = self.capture_target(page_id)
        elif target.page_id != page_id:
            raise ValueError("The crop target does not belong to this page.")
        if not self.session.target_is_current(target):
            return False
        source = self.resolve_asset(target.page_id)
        if source is None:
            raise ValueError("Import an image before cropping.")
        if len(rectangle) != 4 or any(isinstance(edge, bool) or not isinstance(edge, int) for edge in rectangle):
            raise ValueError("Crop bounds must be four integer pixel edges.")
        directory = self._staging_directory()
        try:
            with Image.open(source) as original:
                if getattr(original, "n_frames", 1) > 1:
                    raise ValueError("Animated GIF cropping is unavailable; animation and source artwork are preserved.")
            normalized = directory / "normalized.png"
            _write_static_png(source, normalized)
            with Image.open(normalized) as image:
                left, top, right, bottom = rectangle
                if not (0 <= left < right <= image.width and 0 <= top < bottom <= image.height):
                    raise ValueError("Crop bounds must stay inside the image and have positive size.")
                cropped = directory / "cropped.png"
                image.crop(tuple(rectangle)).save(cropped, format="PNG")
            return self.apply_import(target, self.stage_import(cropped))
        finally:
            directory.resolve().relative_to(self.project_root)
            shutil.rmtree(directory)
