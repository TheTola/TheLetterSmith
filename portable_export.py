from __future__ import annotations

import base64
import hashlib
import html as html_module
import json
import mimetypes
import re
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlsplit

from transactional_io import PathTransaction


DEFAULT_SINGLE_HTML_MAX_MB = 60


class PortableExportError(RuntimeError):
    pass


def create_zip_package(play_dir: str | Path, output_dir: str | Path, *, destination=None, cancelled=None, commit_output=None) -> Path:
    play = _validated_play_dir(play_dir)
    destination = Path(destination).resolve() if destination is not None else Path(output_dir).resolve() / f"{play.parent.name}-{play.name}.zip"
    return _write_zip(play, destination, include_root=True, publish=False, cancelled=cancelled, commit_output=commit_output)


def create_publish_package(play_dir: str | Path, output_dir: str | Path, *, destination=None, cancelled=None, commit_output=None) -> Path:
    play = _validated_play_dir(play_dir)
    destination = Path(destination).resolve() if destination is not None else Path(output_dir).resolve() / f"{play.parent.name}-{play.name}-publish.zip"
    return _write_zip(play, destination, include_root=False, publish=True, cancelled=cancelled, commit_output=commit_output)


def create_single_html(
    play_dir: str | Path,
    output_dir: str | Path,
    *,
    max_size_mb: int = DEFAULT_SINGLE_HTML_MAX_MB,
    destination=None,
    cancelled=None,
    commit_output=None,
) -> Path:
    play = _validated_play_dir(play_dir)
    _check_cancelled(cancelled)
    files = _public_files(play)
    max_bytes = max(1, int(max_size_mb)) * 1024 * 1024
    asset_paths = tuple(path for relative, path in files.items() if relative.startswith("gallery/"))
    source_bytes = sum(path.stat().st_size for path in asset_paths)
    if source_bytes > max_bytes:
        raise PortableExportError(
            f"Assets total {source_bytes / (1024 * 1024):.1f} MB; "
            f"the Single HTML limit is {max_size_mb} MB. Use ZIP Package instead."
        )

    contents = {}
    for relative, path in files.items():
        _check_cancelled(cancelled)
        contents[relative] = path.read_bytes()
    _check_payload_hashes(contents.__getitem__, set(contents))
    document = contents["index.html"].decode("utf-8")
    css = contents["styles.css"].decode("utf-8")
    script = contents["script.js"].decode("utf-8")
    document = re.sub(r"<link\b[^>]*\brel=[\"']preload[\"'][^>]*>\s*", "", document, flags=re.I)
    resources = {}
    for path in asset_paths:
        _check_cancelled(cancelled)
        relative = path.relative_to(play).as_posix()
        resources[relative] = _data_uri(path, contents[relative])

    def reference(value):
        parsed = urlsplit(html_module.unescape(value))
        if parsed.scheme or parsed.netloc:
            return value
        replacement = resources.get(unquote(parsed.path))
        return replacement + ("#" + parsed.fragment if parsed.fragment else "") if replacement else value

    import generate
    def rewrite(match):
        return match.group("prefix") + reference(match.group("value")) + match.group("suffix")

    css = generate._CSS_ASSET.sub(rewrite, css)
    document = generate._MESSAGE_ATTRIBUTE_ASSET.sub(rewrite, document)
    document = generate._CSS_ASSET.sub(rewrite, document)
    # Only trusted script literals are rewritten, never arbitrary message text.
    def inline_script_assets(value):
        return _SCRIPT_ASSET.sub(lambda match: match.group(1) + reference(match.group(2)) + match.group(1), value)

    document = re.sub(r"(<script\b[^>]*>)(.*?)(</script\s*>)",
                      lambda match: match.group(1) + inline_script_assets(match.group(2)) + match.group(3),
                      document, flags=re.I | re.S)
    script = re.sub(r"</script", lambda _match: "<\\/script", inline_script_assets(script), flags=re.I)
    css = re.sub(r"</style", lambda _match: "\\3c /style", css, flags=re.I)
    document = re.sub(
        r"<link\b[^>]*\brel=[\"']stylesheet[\"'][^>]*>",
        lambda _match: f"<style>\n{css}\n</style>",
        document,
        count=1,
        flags=re.I,
    )
    def inline_script(match):
        # Keep the existing nonce; adding unsafe-inline would weaken the viewer.
        opening = re.sub(r"\s+src=[\"']script\.js(?:\?[^\"']*)?[\"']", "", match.group(0).split(">", 1)[0], flags=re.I)
        return opening + ">\n" + script + "\n</script>"

    document = re.sub(
        r"<script\b[^>]*\bsrc=[\"']script\.js(?:\?[^\"']*)?[\"'][^>]*>\s*</script>",
        inline_script,
        document,
        count=1,
        flags=re.I,
    )

    document = re.sub(
        r'<meta\b[^>]*http-equiv=["\']Content-Security-Policy["\'][^>]*>',
        lambda match: re.sub(r"media-src\s+'self'(?!\s+data:)", "media-src 'self' data:", match.group(0)),
        document, flags=re.I,
    )

    encoded_size = len(document.encode("utf-8"))
    if encoded_size > max_bytes:
        raise PortableExportError(
            f"The inlined letter is {encoded_size / (1024 * 1024):.1f} MB; "
            f"the Single HTML limit is {max_size_mb} MB. Use ZIP Package instead."
        )

    destination = Path(destination).resolve() if destination is not None else (
        Path(output_dir).resolve()
        / play.parent.name
        / f"{play.name}.html"
    )
    _validate_destination(play, destination)
    tx = PathTransaction(destination, unique_staging=True)
    staging = tx.prepare()
    try:
        staging.parent.mkdir(parents=True, exist_ok=True)
        staging.write_text(document, encoding="utf-8", newline="\n")
        if "<html" not in document.casefold() or "data:" not in document:
            raise PortableExportError("The Single HTML export failed validation.")
        _check_cancelled(cancelled)
        _commit(tx, commit_output)
    except Exception:
        tx.abort()
        raise
    return destination


def _write_zip(
    play: Path,
    destination: Path,
    *,
    include_root: bool,
    publish: bool,
    cancelled=None,
    commit_output=None,
) -> Path:
    _validate_destination(play, destination)
    files = _public_files(play)
    _check_cancelled(cancelled)
    tx = PathTransaction(destination, unique_staging=True)
    staging = tx.prepare()
    try:
        staging.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(staging, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for relative, path in sorted(files.items()):
                _check_cancelled(cancelled)
                arcname = f"{play.name}/{relative}" if include_root else relative
                archive.write(path, arcname)
            if publish:
                archive.writestr(".nojekyll", "")
                archive.writestr(
                    "_headers",
                    "/*\n  X-Content-Type-Options: nosniff\n  Referrer-Policy: strict-origin-when-cross-origin\n",
                )
        with zipfile.ZipFile(staging, "r") as archive:
            names = set(archive.namelist())
            expected = f"{play.name}/index.html" if include_root else "index.html"
            if expected not in names:
                raise PortableExportError("ZIP export is missing index.html.")
            prefix = f"{play.name}/" if include_root else ""
            _check_payload_hashes(lambda relative: archive.read(prefix + relative),
                                 {name[len(prefix):] for name in names if name.startswith(prefix)})
        _check_cancelled(cancelled)
        _commit(tx, commit_output)
    except Exception:
        tx.abort()
        raise
    return destination


def _validated_play_dir(play_dir: str | Path) -> Path:
    play = Path(play_dir).resolve()
    for name in ("index.html", "styles.css", "script.js"):
        if not (play / name).is_file():
            raise PortableExportError(f"Play bundle is missing {name}.")
    return play


_SCRIPT_ASSET = re.compile(r'''(["'])(gallery/[^"'\r\n<>]+)\1''')


def _public_files(play: Path) -> dict[str, Path]:
    """Collect a canonical allowlist or references from a validated legacy viewer."""
    import generate
    from forge_render import DISPLAY_MANIFEST, validate_display_output
    from project_repository import _safe_file

    manifest_path = play / DISPLAY_MANIFEST
    if manifest_path.exists():
        validate_display_output(play)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("output_mode") != "display" or manifest.get("warnings"):
            raise PortableExportError("Prepare final display output before exporting.")
        return {relative: _safe_file(play, relative) for relative in (*manifest["files"], DISPLAY_MANIFEST)}

    generate.validate_play_bundle(play)
    references = {"index.html", "styles.css", "script.js"}
    document = (play / "index.html").read_text(encoding="utf-8")
    css = (play / "styles.css").read_text(encoding="utf-8")
    script = (play / "script.js").read_text(encoding="utf-8")
    for value in generate._runtime_asset_references(document + "\n" + css):
        parsed = urlsplit(html_module.unescape(value))
        if value.startswith(("#", "data:")):
            continue
        if parsed.scheme or parsed.netloc:
            raise PortableExportError("Display media must be stored with the letter.")
        references.add(unquote(parsed.path))
    scripts = "\n".join(re.findall(r"<script\b[^>]*>(.*?)</script\s*>", document, re.I | re.S)) + "\n" + script
    for match in _SCRIPT_ASSET.finditer(scripts):
        relative = unquote(urlsplit(match.group(2)).path)
        if _safe_file(play, relative).is_file():  # Legacy sound effects can be optional.
            references.add(relative)
    files = {relative: _safe_file(play, relative) for relative in references}
    if any(not path.is_file() for path in files.values()):
        raise PortableExportError("A referenced display resource is missing.")
    return files


def _validate_destination(play: Path, destination: Path) -> None:
    if destination.is_relative_to(play):
        raise PortableExportError("Choose an export destination outside the generated presentation.")


def _check_cancelled(cancelled) -> None:
    if cancelled is not None and cancelled():
        raise PortableExportError("Export canceled; the previous export was preserved.")


def _commit(transaction, commit_output) -> None:
    if commit_output is None:
        transaction.commit()
    else:
        commit_output(transaction.commit)
        if transaction.staging_path.exists():
            raise PortableExportError("The export was not committed.")


def _check_payload_hashes(read, names) -> None:
    from forge_render import DISPLAY_MANIFEST
    if DISPLAY_MANIFEST not in names:
        return
    manifest = json.loads(read(DISPLAY_MANIFEST).decode("utf-8"))
    for relative, digest in manifest["files"].items():
        if relative not in names or hashlib.sha256(read(relative)).hexdigest() != digest:
            raise PortableExportError("Display resources changed while exporting; prepare the letter again.")


def _data_uri(path: Path, content: bytes | None = None) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    payload = base64.b64encode(path.read_bytes() if content is None else content).decode("ascii")
    return f"data:{mime};base64,{payload}"
