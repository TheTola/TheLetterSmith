"""Forge's read-only canonical composition and portable display adapter.

The foundation owns authored state. This module consumes render_snapshot(),
collects only visible dependencies, and reuses the existing viewer/media helpers.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
import hashlib
import html
import json
import os
import re
from pathlib import Path
import secrets
import tempfile
from threading import RLock
from types import MappingProxyType
from urllib.parse import unquote, urlsplit
from weakref import WeakKeyDictionary

from PIL import Image

from project_repository import ProjectRepositoryError, _safe_file
from transactional_io import PathTransaction, atomic_copy_file, atomic_write_json, atomic_write_text

DISPLAY_MANIFEST = "lettersmith-display.json"
RENDERER_VERSION = 1
_READABILITY = ("message_overlay_preset", "message_overlay_opacity")
_PRESENTATION = ("recipient_name", "recipient_title", "starting_volume", "music_volume", "curtain_style", *_READABILITY)
_GENERATIONS = WeakKeyDictionary()
_GENERATION_LOCK = RLock()


def _io_path(path) -> Path:
    """Use Win32 extended paths for nested staging under long display titles."""
    resolved = Path(path).resolve()
    text = str(resolved)
    if os.name == "nt" and not text.startswith("\\\\?\\"):
        return Path("\\\\?\\UNC\\" + text[2:] if text.startswith("\\\\") else "\\\\?\\" + text)
    return resolved


def _freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _plain(value):
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


@dataclass(frozen=True)
class RenderIssue:
    code: str
    message: str
    page_id: str | None = None
    severity: str = "error"
    feature: str = "forge"
    blocks_output: bool = True
    blocks_preview: bool = True


@dataclass(frozen=True)
class RenderInput:
    snapshot: Mapping
    project_root: Path
    asset_root: Path
    session_id: str
    project_id: str
    revision: int
    output_mode: str
    source_fingerprint: str = ""
    targets: tuple = ()
    presentation_root: Path | None = None
    project_target: object = None


class StaleRenderError(RuntimeError):
    pass


class RenderValidationError(RuntimeError):
    def __init__(self, issues):
        self.issues = tuple(issues)
        super().__init__("\n".join(issue.message for issue in self.issues))


def _resolve(render: RenderInput, reference: str) -> Path:
    if reference in render.snapshot["recovery"].get("missing_resources", ()):
        raise FileNotFoundError(f"Resource is missing: {reference}")
    # Match the foundation consumer's canonical-generation-first resolution.
    candidates = [_safe_file(root, reference) for root in dict.fromkeys((render.asset_root, render.project_root))]
    source = next((path for path in candidates if path.is_file()), None)
    if source is None:
        raise FileNotFoundError(f"Resource is missing: {reference}")
    return source


def _settings(render):
    from settings_store import DEFAULT_SETTINGS
    values = {key: DEFAULT_SETTINGS[key] for key in _PRESENTATION if key in DEFAULT_SETTINGS}
    for field in ("shared_settings", "presentation"):
        values.update({key: value for key, value in render.snapshot.get(field, {}).items() if key in _PRESENTATION})
    metadata = render.snapshot.get("metadata", {})
    values.update({key: metadata[key] for key in ("recipient_name", "recipient_title") if key in metadata})
    return values


def _selected_tracks(render):
    sound = render.snapshot.get("sound", {})
    records = sound.get("tracks", ())
    by_id = {str(item.get("track_id", "")): item for item in records if isinstance(item, Mapping)}
    if "playlist" in sound or "single_track_id" in sound:
        identifiers = sound.get("playlist", ()) if sound.get("mode") == "playlist" else (sound.get("single_track_id"),)
        return tuple(by_id.get(str(key), {"track_id": str(key), "missing": True}) for key in identifiers if key)
    # Imported display manifests carry ordered active tracks, without archive IDs.
    return tuple(item for item in records if isinstance(item, Mapping))


def render_has_music(render: RenderInput) -> bool:
    """Whether the canonical playlist selects any soundtrack (including missing)."""
    return bool(_selected_tracks(render))


def _track_reference(track):
    return str(track.get("asset") or ("gallery/sounds/" + str(track["filename"]) if track.get("filename") else ""))


def _message_sources(render, page):
    """Return sanitized HTML and its active, portable resource map."""
    import generate as legacy
    from message_html import sanitize_message_html, STYLE_ATTR_RE
    message = page.get("message") or {}
    value = sanitize_message_html(str(message.get("html", "")))
    # The existing font exporter consumes semicolon-terminated declarations.
    # Terminate each sanitized attribute so a final font declaration cannot
    # consume another page's HTML when documents are collected in one pass.
    value = STYLE_ATTR_RE.sub(lambda match: ' style=' + match.group(1) + match.group(2).rstrip(';') + ';' + match.group(1), value)
    sources = {}
    for reference in legacy._runtime_asset_references(value):
        if not reference or reference.startswith(("#", "data:")):
            continue
        parsed = urlsplit(html.unescape(reference))
        if parsed.scheme or parsed.netloc:
            raise ValueError("Message media must be stored with the project.")
        path = unquote(parsed.path)
        # Foundation HTML retains portable references; legacy relative message
        # paths use the established message directory, never guessed aliases.
        candidates = [path]
        if not path.startswith("gallery/"):
            candidates.append("gallery/user/message/" + path)
        source = None
        for candidate in candidates:
            try:
                source = _resolve(render, candidate)
                break
            except FileNotFoundError:
                continue
        if source is None:
            raise FileNotFoundError(f"Message resource is missing: {path}")
        sources[reference] = source
    return value, sources


def _active_signature(render):
    import generate as legacy
    import Template
    from message_html import extract_font_families
    from font_export import inspect_font_family
    digest = hashlib.sha256()
    pages = []
    dependencies = {}
    documents = []
    presentation_root = render.presentation_root or render.project_root
    def content_digest(source):
        try:
            return legacy._file_content_digest(source).hex()
        except OSError:
            return "missing"
    for page in render.snapshot["pages"]:
        message = page.get("message")
        active = {key: _plain(page.get(key)) for key in ("id", "type", "name", "design_overrides", "resolved_layout")}
        active["artwork"] = {key: _plain(value) for key, value in page["artwork"].items() if key != "asset"}
        active["readability"] = {key: page.get("resolved_settings", {}).get(key) for key in _READABILITY}
        if message is not None:
            active["message"] = {key: _plain(message.get(key)) for key in ("html", "layout", "settings")}
            try:
                document, resources = _message_sources(render, page)
                documents.append(document)
                by_reference = {reference: "resource-" + content_digest(source) for reference, source in resources.items()}
                def rewrite(match):
                    return match.group("prefix") + by_reference.get(match.group("value"), match.group("value")) + match.group("suffix")
                active["message"]["html"] = legacy._CSS_ASSET.sub(rewrite, legacy._MESSAGE_ATTRIBUTE_ASSET.sub(rewrite, document))
            except (OSError, ValueError, ProjectRepositoryError) as error:
                active["resource_error"] = str(error)
        asset = page.get("artwork", {}).get("asset")
        if asset:
            try:
                active["artwork"]["content"] = content_digest(_resolve(render, str(asset)))
            except (OSError, ValueError, ProjectRepositoryError) as error:
                active["artwork_error"] = str(error)
        pages.append(active)
    tracks = []
    for track in _selected_tracks(render):
        selected = {key: _plain(track.get(key)) for key in ("track_id", "display_title", "missing")}
        reference = _track_reference(track)
        if reference:
            try:
                selected["content"] = content_digest(_resolve(render, reference))
            except (OSError, ValueError, ProjectRepositoryError):
                selected["content"] = "missing"
        tracks.append(selected)
    for family in extract_font_families("\n".join(documents)):
        for face in inspect_font_family(presentation_root, family).faces:
            dependencies[f"font:{family}:{face.weight}:{face.style}:{face.collection_index}"] = content_digest(face.source_path)
    for name in legacy.CONTROL_FILES:
        source = presentation_root / legacy.USER_CONTROLS_DIR / name
        if source.is_file():
            dependencies["control:" + name] = content_digest(source)
    paths = legacy.application_paths(presentation_root)
    banner = paths.resource_path(legacy.APP_BANNER_PATH)
    if banner.is_file():
        dependencies["banner"] = content_digest(banner)
    for name, source in legacy._resolve_sfx_sources(presentation_root).items():
        dependencies["sfx:" + name] = content_digest(source)
    sound = render.snapshot.get("sound", {})
    visible = {"renderer": RENDERER_VERSION, "project": render.project_id, "mode": render.output_mode,
               "pages": pages, "settings": _settings(render), "tracks": tracks,
               "presentation": _plain(render.snapshot.get("presentation", {})), "dependencies": dependencies,
               "audio": {key: _plain(sound.get(key)) for key in ("mode", "crossfade_ms", "volume", "muted")}}
    digest.update(json.dumps(visible, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode())
    for template in (Template.TEMPLATE_HTML, Template.TEMPLATE_CSS, Template.TEMPLATE_JS):
        digest.update(template.encode())
    return digest.hexdigest()


def capture_render(session, output_mode="display", *, presentation_root=None) -> RenderInput:
    """Capture the foundation render snapshot without projection or mutation."""
    if output_mode not in ("preview", "display"):
        raise ValueError("Output mode must be preview or display.")
    if session.project_root is None:
        raise ValueError("The render session requires a project resource root.")
    identity = session.session_id
    model = session.project
    with model.mutation_scope():
        snapshot = model.render_snapshot()
    targets = tuple(session.capture_target(page["id"], fields=("type", "name", "artwork", "message", "design_overrides", "$order"), include_shared=False) for page in snapshot["pages"])
    project_target = session.capture_project_target(fields=("metadata", "shared_settings", "presentation", "sound", "recovery"))
    render = RenderInput(_freeze(snapshot), session.project_root, session.asset_root or session.project_root,
                         identity, snapshot["project_id"], snapshot["revision"], output_mode, targets=targets,
                         presentation_root=Path(presentation_root).resolve() if presentation_root is not None else None,
                         project_target=project_target)
    if session.session_id != identity or session.project is not model or model.revision != render.revision:
        raise StaleRenderError("The project changed while its preview was captured.")
    return replace(render, source_fingerprint=_active_signature(render))


def render_is_current(session, render: RenderInput, *, check_sources=True) -> bool:
    if (session.session_id != render.session_id or session.project.project_id != render.project_id
            or not render.targets
            or (render.project_target is not None and not session.project_target_is_current(render.project_target))
            or (render.project_target is None and session.project.revision != render.revision)
            or not all(session.target_is_current(target) for target in render.targets)):
        return False
    return not check_sources or _active_signature(render) == render.source_fingerprint


def render_content_key(snapshot) -> str:
    """Cheap view dependency key; no filesystem reads or private prompt data."""
    pages = [{key: _plain(page.get(key)) for key in
              ("id", "type", "name", "artwork", "message", "design_overrides", "resolved_layout")}
             for page in snapshot["pages"]]
    state = {key: _plain(snapshot.get(key)) for key in
             ("project_id", "metadata", "shared_settings", "presentation", "sound", "validation")}
    state["pages"] = pages
    return json.dumps(state, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def referenced_resource_stamp(render) -> tuple:
    """Bounded change evidence for this snapshot's authored resource graph."""
    from generate import _file_signature
    references = set()
    for page in render.snapshot["pages"]:
        if page["artwork"].get("asset"):
            references.add(page["artwork"]["asset"])
        message = page.get("message")
        if message is not None:
            references.update(message.get("resources", ()))
    references.update(reference for track in _selected_tracks(render)
                      if (reference := _track_reference(track)))
    result = []
    for reference in sorted(references):
        try:
            result.append((reference, _file_signature(_resolve(render, reference))))
        except (OSError, ValueError, ProjectRepositoryError):
            result.append((reference, None))
    return tuple(result)


def validate_render(render: RenderInput) -> tuple[RenderIssue, ...]:
    from message_format import message_plain_text
    from message_html import extract_font_families
    from font_export import inspect_font_family
    from project_model import PAGE_TYPES
    issues = []
    pages = render.snapshot["pages"]
    unknown_presentation = set(render.snapshot.get("presentation", {})) - set(_PRESENTATION)
    if unknown_presentation:
        issues.append(RenderIssue("unsupported_presentation", "Unsupported presentation settings: " + ", ".join(sorted(unknown_presentation))))
    for raw in render.snapshot.get("validation", ()):
        # Missing retained recovery resources are not dependencies of display.
        if raw["code"] in ("missing_artwork", "empty_message", "missing_resource", "unsupported_layout", "unsupported_placement"):
            continue
        issues.append(RenderIssue(raw["code"], raw["message"], raw.get("page_id")))
    for index, page in enumerate(pages, 1):
        label = f"Page {index} — {page.get('name') or PAGE_TYPES[page['type']].label}: "
        def issue(code, message, feature, *, preview=True):
            issues.append(RenderIssue(code, label + message, page["id"], "error" if preview else "warning", feature, True, preview))
        for raw in render.snapshot.get("validation", ()):
            if raw.get("page_id") == page["id"] and raw["code"] in ("unsupported_layout", "unsupported_placement"):
                issue(raw["code"], raw["message"], "messages" if raw["code"] == "unsupported_layout" else "images")
        artwork = page["artwork"]
        unknown_design = set(page.get("design_overrides", {})) - {*_READABILITY, "message_layout", "artwork_placement"}
        if unknown_design:
            issue("unsupported_page_settings", "Unsupported page presentation settings: " + ", ".join(sorted(unknown_design)), "images")
        from image_animation import default_gif_settings, normalize_gif_settings
        animation = artwork.get("animation", {})
        normalized_animation = normalize_gif_settings(animation)
        if (set(animation) - set(default_gif_settings())
                or any(value != normalized_animation.get(key) for key, value in animation.items())):
            issue("unsupported_animation", "Animation settings are unsupported or outside the established GIF playback limits.", "images")
        asset = artwork.get("asset")
        if not asset:
            issue("missing_artwork", "Background image missing." if page["type"] == "letter" else "Artwork missing.", "images", preview=False)
        else:
            try:
                with Image.open(_resolve(render, str(asset))) as image:
                    if image.format not in ("PNG", "JPEG", "GIF", "WEBP", "BMP"):
                        raise ValueError("Unsupported image format.")
                    image.verify()
            except FileNotFoundError:
                issue("missing_artwork", "Referenced artwork is missing.", "images", preview=False)
            except (OSError, ValueError, ProjectRepositoryError) as error:
                issue("invalid_artwork", f"Artwork cannot be rendered: {error}", "images")
        message = page.get("message")
        if message is not None:
            unknown = set(message.get("settings", {})) - set(_READABILITY)
            if unknown:
                issue("unsupported_message_settings", "Unsupported message settings: " + ", ".join(sorted(unknown)), "messages")
            try:
                value, _ = _message_sources(render, page)
                missing_fonts = [family for family in extract_font_families(value)
                                 if not inspect_font_family(render.presentation_root or render.project_root, family).faces]
                if missing_fonts:
                    issue("missing_font", "Message font files are unavailable: " + ", ".join(missing_fonts), "messages")
            except (OSError, ValueError, ProjectRepositoryError) as error:
                issue("message_resource", str(error), "messages")
    for track in _selected_tracks(render):
        try:
            reference = _track_reference(track)
            if not reference or track.get("missing"):
                raise FileNotFoundError("Selected soundtrack is missing: " + str(track.get("display_title") or track.get("track_id") or "track"))
            _resolve(render, reference)
        except (OSError, ValueError, ProjectRepositoryError) as error:
            issues.append(RenderIssue("missing_sound", str(error), feature="sound"))
    return tuple(issues)


def _page_key(page_id):
    return "page-" + hashlib.sha256(page_id.encode()).hexdigest()[:24]


def _render_artwork(render, page, index, destination, copier, previous=None, reuse_root=None):
    from image_animation import inspect_gif, normalize_gif_settings, _extract_gif_frames, _write_first_frame_png
    key = _page_key(page["id"])
    try:
        source = _resolve(render, str(page["artwork"].get("asset") or ""))
    except FileNotFoundError:
        source = None
    except ProjectRepositoryError:
        if page["artwork"].get("asset"):
            raise
        source = None
    if source is None:
        from PIL import ImageDraw
        relative = f"gallery/pages/{key}/missing.png"
        placeholder = destination / relative
        placeholder.parent.mkdir(parents=True, exist_ok=True)
        artwork = Image.new("RGB", (640, 960), "#292929")
        ImageDraw.Draw(artwork).text((36, 80), "Artwork missing - preview only", fill="white")
        artwork.save(placeholder)
        return relative, None
    with Image.open(source) as image:
        suffix = {"PNG": ".png", "JPEG": ".jpg", "GIF": ".gif", "WEBP": ".webp", "BMP": ".bmp"}[image.format]
        animated = image.format == "GIF" and getattr(image, "n_frames", 1) > 1
    relative = f"gallery/pages/{key}/artwork{suffix}"
    copier(source, destination / relative)
    if not animated:
        return relative, None
    info = inspect_gif(source)
    settings = normalize_gif_settings(page["artwork"].get("animation"))
    poster = destination / f"gallery/pages/{key}/poster.png"
    _write_first_frame_png(source, poster)
    import generate as legacy
    source_digest = legacy._file_content_digest(source).hex()
    old = next((record for record in (previous or {}).get("animations", {}).values()
                if record.get("slot") == page["id"] and record.get("source_digest") == source_digest), None)
    frame_sources = old.get("frames", []) if old else []
    reusable = bool(reuse_root and len(frame_sources) == info.frame_count)
    if reusable:
        try:
            reusable = all(hashlib.sha256(_safe_file(reuse_root, relative).read_bytes()).hexdigest()
                           == previous.get("files", {}).get(relative) for relative in frame_sources)
        except (OSError, ValueError, ProjectRepositoryError):
            reusable = False
    if reusable:
        for frame_relative in frame_sources:
            copier(_safe_file(reuse_root, frame_relative), _safe_file(destination, frame_relative))
        sources = frame_sources
    else:
        frames = _extract_gif_frames(source, destination / f"gallery/pages/{key}/frames", info.frame_count)
        sources = [path.relative_to(destination).as_posix() for path in frames]
    return sources[0], {"slot": page["id"], "render_mode": "frames", "source": relative,
                        "preview_source": poster.relative_to(destination).as_posix(), "frames": sources,
                        "source_digest": source_digest, "durations_ms": list(info.durations_ms),
                        "embedded_play_count": info.embedded_play_count, **settings}


def build_render_to(render: RenderInput, destination, *, seed_sfx=True, reuse_play_directory=None, cancelled=None) -> Path:
    """Build a fresh staging directory; never mutate the source snapshot/project."""
    import generate as legacy
    from Template import template_for_project_pages, TEMPLATE_CSS, TEMPLATE_JS
    from curtain_color import curtain_variant_rgbs, get_curtain_display_colors, write_recolored_banner_image
    from font_export import build_embedded_font_payload
    from letter_page import letter_page_style_from_settings
    presentation_root = render.presentation_root or render.project_root
    target = _io_path(destination)
    if target.exists() and any(target.iterdir()):
        raise ValueError("Render destination must be an empty staging directory.")
    issues = validate_render(render)
    blocked = [issue for issue in issues if (issue.blocks_preview if render.output_mode == "preview" else issue.blocks_output)]
    if blocked:
        raise RenderValidationError(blocked)
    def check_cancelled():
        if cancelled is not None and cancelled():
            raise StaleRenderError("Render cancelled; previous output remains available.")
    check_cancelled()
    target.mkdir(parents=True, exist_ok=True)
    copier = legacy._BuildAssetCopier(target, Path(reuse_play_directory) if reuse_play_directory else None)
    previous = {}
    reuse_root = Path(reuse_play_directory).resolve() if reuse_play_directory else None
    if reuse_root:
        try:
            previous = json.loads((reuse_root / DISPLAY_MANIFEST).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    settings = _settings(render)
    pages, animations = [], {}
    for index, page in enumerate(render.snapshot["pages"]):
        check_cancelled()
        source, animation = _render_artwork(render, page, index, target, copier.copy, previous, reuse_root)
        if animation:
            animations[str(index)] = animation
        message_html, sources = _message_sources(render, page) if page.get("message") is not None else ("", {})
        from message_format import message_plain_text
        if not message_plain_text(message_html).strip() and not re.search(r'<img\b', message_html, re.I):
            message_html = ''
        resource_map = {}
        for reference, resource in sources.items():
            name = hashlib.sha256(resource.read_bytes()).hexdigest()[:24] + resource.suffix.lower()
            relative = f"gallery/message/{_page_key(page['id'])}/{name}"
            copier.copy(resource, target / relative)
            resource_map[reference] = relative
        def rewrite(match):
            return match.group("prefix") + resource_map.get(match.group("value"), match.group("value")) + match.group("suffix")
        message_html = legacy._CSS_ASSET.sub(rewrite, legacy._MESSAGE_ATTRIBUTE_ASSET.sub(rewrite, message_html))
        page_settings = {**settings, **{key: value for key, value in page.get("resolved_settings", {}).items() if key in _READABILITY},
                         **_plain((page.get("message") or {}).get("settings", {}))}
        pages.append({"id": page["id"], "type": page["type"], "name": page.get("name", ""), "source": source,
                      "message_html": message_html, "overlay_style": letter_page_style_from_settings(page_settings),
                      "layout": _plain(page.get("resolved_layout"))})
    # One font pass across the complete document prevents alias collisions and
    # avoids one page's font export deleting another page's emitted font files.
    separator = "<!--forge-document-" + secrets.token_hex(24) + "-->"
    fonts = build_embedded_font_payload(presentation_root, separator.join(page["message_html"] for page in pages), target / "gallery/fonts",
                                        reuse_fonts_dir=Path(reuse_play_directory) / "gallery/fonts" if reuse_play_directory else None)
    for page, document in zip(pages, fonts.html.split(separator)):
        page["message_html"] = document
    cover = next((page for page in render.snapshot["pages"] if page["type"] == "cover" and page["artwork"].get("asset")), None)
    try:
        analysis = _resolve(render, str(cover["artwork"]["asset"])) if cover else target / "missing-cover"
    except FileNotFoundError:
        analysis = target / "missing-cover"
    colors = curtain_variant_rgbs(analysis)
    style = legacy.normalize_curtain_style(settings.get("curtain_style"))
    display = get_curtain_display_colors(style, colors)
    controls = presentation_root / legacy.USER_CONTROLS_DIR
    legacy._copy_control_files(controls, target / "gallery/controls", legacy.CONTROL_FILES, curtain_rgb=display.background, copy_file=copier.copy)
    banner = legacy.application_paths(presentation_root).resource_path(legacy.APP_BANNER_PATH)
    write_recolored_banner_image(banner, target / "gallery/controls" / legacy.BANNER_FILE, display.background)
    playlist, sound_records = [], []
    for index, track in enumerate(_selected_tracks(render)):
        source = _resolve(render, _track_reference(track))
        filename = f"music-{index + 1:03d}{source.suffix.lower()}"
        copier.copy(source, target / "gallery/sounds" / filename)
        playlist.append("gallery/sounds/" + filename)
        sound_records.append({"filename": filename, "display_title": str(track.get("display_title", ""))})
    sound = render.snapshot.get("sound", {})
    crossfade = max(0, int(sound.get("crossfade_ms", 1000 if sound.get("mode") == "playlist" and len(playlist) > 1 else 0)))
    atomic_write_json(target / "gallery/sounds" / legacy.BUILD_SOUND_MANIFEST_NAME, {"version": 1, "tracks": sound_records, "crossfade_ms": crossfade, "mode": sound.get("mode", "single")})
    legacy._seed_sfx_into_build(project_root=presentation_root, sounds_dst=target / "gallery/sounds", seed_sfx=seed_sfx, copy_file=copier.copy)
    title = legacy._title_from_settings(settings, legacy._recipient_from_settings(settings))
    volume = sound.get("volume", legacy._starting_volume_from_settings(settings))
    try:
        volume = max(0, min(100, int(volume)))
    except (ValueError, TypeError):
        volume = legacy._starting_volume_from_settings(settings)
    # Insert sanitized documents only after expanding the trusted shell.
    from Template import TEMPLATE_HTML
    content = TEMPLATE_HTML
    content = content.replace('<body>', '<body data-initial-muted="' + str(bool(sound.get("muted", False))).lower()
                              + '" data-playback-id="' + html.escape(render.project_id + ':' + render.source_fingerprint, quote=True) + '">')
    replacements = {"CSP_NONCE": secrets.token_urlsafe(24), "TITLE": html.escape(title, quote=True),
                    "TITLE_BANNER_TEXT_RGB": legacy._rgb_css_value(display.foreground),
                    "HAS_MESSAGE_JSON": legacy._script_json(any(page["message_html"] for page in pages)),
                    "INITIAL_VOLUME": str(volume), "INITIAL_MUTED": str(bool(sound.get("muted", False))).lower(),
                    "MESSAGE_OVERLAY_STYLE": "", "MESSAGE_HTML": "",
                    "MUSIC_PLAYLIST_JSON": legacy._script_json(playlist), "MUSIC_CROSSFADE_MS": str(crossfade),
                    "MUSIC_PRELOAD_HTML": f'<link rel="preload" as="audio" href="{playlist[0]}">' if playlist else "",
                    "IMAGE_ANIMATIONS_JSON": legacy._script_json(animations)}
    for key, value in replacements.items():
        content = content.replace("{{" + key + "}}", value)
    content = template_for_project_pages(pages, template_html=content)
    atomic_write_text(target / "index.html", content)
    atomic_write_text(target / "styles.css", fonts.css + "\n" + TEMPLATE_CSS)
    atomic_write_text(target / "script.js", TEMPLATE_JS)
    state = {"schema_version": legacy.BUILD_SCHEMA_VERSION, "renderer": "canonical", "project_id": render.project_id,
             "session_id": render.session_id, "revision": render.revision, "output_mode": render.output_mode,
             "source_fingerprint": render.source_fingerprint, "font_export": {key: list(value) for key, value in fonts.report.items()},
             "asset_sources": copier.records}
    atomic_write_json(target / legacy.BUILD_STATE_FILE, state)
    resources = {path.relative_to(target).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                 for path in target.rglob("*") if path.is_file() and path.name != legacy.BUILD_STATE_FILE}
    atomic_write_json(target / DISPLAY_MANIFEST, {"version": RENDERER_VERSION, "project_id": render.project_id,
                      "source_fingerprint": render.source_fingerprint, "output_mode": render.output_mode,
                      "pages": [{key: page[key] for key in ("id", "type", "name", "source")} for page in pages],
                      "animations": animations, "files": resources,
                      "warnings": [{"code": issue.code, "message": issue.message, "page_id": issue.page_id} for issue in issues]})
    check_cancelled()
    validate_display_output(target)
    # Browser URLs and callers retain ordinary paths; extended syntax is I/O-only.
    return Path(destination).resolve()


def validate_display_output(directory) -> Path:
    import generate as legacy
    root = _io_path(directory)
    manifest = json.loads((root / DISPLAY_MANIFEST).read_text(encoding="utf-8"))
    if manifest.get("version") != RENDERER_VERSION or not isinstance(manifest.get("files"), dict):
        raise RuntimeError("Invalid display resource manifest.")
    for required in ("index.html", "styles.css", "script.js", *("gallery/controls/" + name for name in legacy.CONTROL_FILES)):
        if required not in manifest["files"]:
            raise RuntimeError("Missing display resource: " + required)
    for reference, digest in manifest["files"].items():
        source = _safe_file(root, reference)
        if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != digest:
            raise RuntimeError("Display resource changed or is missing: " + reference)
    for filename in ("index.html", "styles.css"):
        for reference in legacy._runtime_asset_references((root / filename).read_text(encoding="utf-8")):
            legacy._validate_runtime_asset_reference(root, root, reference)
    return Path(directory).resolve()


class SavedProjectPreviewAdapter:
    """Read-only snapshots use app controls/fonts without active authored assets."""

    def __init__(self, presentation_root):
        self.presentation_root = Path(presentation_root).resolve()

    def capture_render(self, session, output_mode="preview"):
        return capture_render(session, output_mode, presentation_root=self.presentation_root)

    validate_render = staticmethod(validate_render)
    build_render_to = staticmethod(build_render_to)
    render_is_current = staticmethod(render_is_current)
    render_content_key = staticmethod(render_content_key)
    referenced_resource_stamp = staticmethod(referenced_resource_stamp)
    StaleRenderError = StaleRenderError


def copy_display_output(source, destination) -> Path:
    """Copy the verified public allowlist, excluding private build diagnostics."""
    root = _io_path(validate_display_output(source))
    manifest = json.loads((root / DISPLAY_MANIFEST).read_text(encoding="utf-8"))
    if manifest.get("output_mode") != "display" or manifest.get("warnings"):
        raise RuntimeError("Prepare validated final output before publication.")
    target = Path(destination).resolve()
    transaction = PathTransaction(_io_path(target), unique_staging=True)
    try:
        stage = transaction.prepare()
        for relative in (*manifest["files"], DISPLAY_MANIFEST):
            atomic_copy_file(_safe_file(root, relative), _safe_file(stage, relative))
        transaction.commit(keep_backup=True, validator=lambda path: bool(validate_display_output(path)))
        transaction.finalize()
    except Exception:
        transaction.abort()
        raise
    return target


def export_render(session, destination, *, output_format="zip", render_input=None, cancelled=None) -> Path:
    """Export one captured display revision without replacing the active preview."""
    from portable_export import create_zip_package, create_publish_package, create_single_html
    from project_paths import application_paths

    exporters = {"zip": create_zip_package, "publish_zip": create_publish_package, "html": create_single_html}
    if output_format not in exporters:
        raise ValueError("Unsupported display export format.")
    render = render_input or capture_render(session, "display")
    if render.output_mode != "display":
        raise ValueError("Export requires final display input.")
    if not render_is_current(session, render):
        raise StaleRenderError("The project changed before export preparation.")
    target = Path(destination).resolve()
    temporary_root = application_paths(render.project_root).temporary_root
    temporary_root.mkdir(parents=True, exist_ok=True)

    def commit_output(commit):
        def guarded(_model, _page_id):
            if (cancelled is not None and cancelled()) or not render_is_current(session, render):
                raise StaleRenderError("The project changed or export was canceled; the previous export was preserved.")
            commit()
        if not session.apply_result(render.targets[0], guarded):
            raise StaleRenderError("The project was replaced; the previous export was preserved.")

    with tempfile.TemporaryDirectory(prefix="export-", dir=temporary_root) as directory:
        display = build_render_to(render, Path(directory) / "letter", cancelled=cancelled)
        return exporters[output_format](display, target.parent, destination=target,
                                        cancelled=cancelled, commit_output=commit_output)


def generate_render(session, *, render_input=None, output_mode="display", destination=None, seed_sfx=True, cancelled=None) -> Path:
    """Commit only a current, fully validated generation under the session guard."""
    import generate as legacy
    render = render_input or capture_render(session, output_mode)
    with _GENERATION_LOCK:
        generation = _GENERATIONS.get(session, 0) + 1
        _GENERATIONS[session] = generation
    if not render_is_current(session, render):
        raise StaleRenderError("The captured project changed; update the preview again.")
    target = Path(destination).resolve() if destination else legacy.play_bundle_directory(render.project_root)
    legacy._preserve_published_play_bundle(render.project_root, target)
    transaction = PathTransaction(_io_path(target), staging_suffix=".build-staging", backup_suffix=".build-backup", unique_staging=True)
    try:
        stage = transaction.prepare()
        build_render_to(render, stage, seed_sfx=seed_sfx, reuse_play_directory=target if target.is_dir() else None, cancelled=cancelled)
        def commit(_model, _page_id):
            with _GENERATION_LOCK:
                if (_GENERATIONS.get(session) != generation or (cancelled is not None and cancelled())
                        or not render_is_current(session, render)):
                    raise StaleRenderError("A newer generation, project edit, or resource change superseded this render; previous output is preserved.")
                transaction.commit(keep_backup=True, validator=lambda path: bool(validate_display_output(path)))
        if not session.apply_result(render.targets[0], commit):
            raise StaleRenderError("The project session changed during rendering; previous output is preserved.")
        transaction.finalize()
    except Exception:
        transaction.abort()
        raise
    return target
