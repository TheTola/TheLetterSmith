from __future__ import annotations

import base64
import mimetypes
import re
import zipfile
from pathlib import Path

from transactions import PathTransaction


DEFAULT_SINGLE_HTML_MAX_MB = 60


class PortableExportError(RuntimeError):
    pass


def create_zip_package(play_dir: str | Path, output_dir: str | Path) -> Path:
    play = _validated_play_dir(play_dir)
    destination = Path(output_dir).resolve() / f"{play.parent.name}-{play.name}.zip"
    return _write_zip(play, destination, include_root=True, publish=False)


def create_publish_package(play_dir: str | Path, output_dir: str | Path) -> Path:
    play = _validated_play_dir(play_dir)
    destination = Path(output_dir).resolve() / f"{play.parent.name}-{play.name}-publish.zip"
    return _write_zip(play, destination, include_root=False, publish=True)


def create_single_html(
    play_dir: str | Path,
    output_dir: str | Path,
    *,
    max_size_mb: int = DEFAULT_SINGLE_HTML_MAX_MB,
) -> Path:
    play = _validated_play_dir(play_dir)
    max_bytes = max(1, int(max_size_mb)) * 1024 * 1024
    asset_paths = tuple(path for path in (play / "gallery").rglob("*") if path.is_file())
    source_bytes = sum(path.stat().st_size for path in asset_paths)
    if source_bytes > max_bytes:
        raise PortableExportError(
            f"Assets total {source_bytes / (1024 * 1024):.1f} MB; "
            f"the Single HTML limit is {max_size_mb} MB. Use ZIP Package instead."
        )

    html = (play / "index.html").read_text(encoding="utf-8")
    css = (play / "styles.css").read_text(encoding="utf-8")
    script = (play / "script.js").read_text(encoding="utf-8")
    html = re.sub(r"<link\b[^>]*\brel=[\"']preload[\"'][^>]*>\s*", "", html, flags=re.I)
    html = re.sub(
        r"<link\b[^>]*\brel=[\"']stylesheet[\"'][^>]*>",
        lambda _match: f"<style>\n{css}\n</style>",
        html,
        count=1,
        flags=re.I,
    )
    html = re.sub(
        r"<script\b[^>]*\bsrc=[\"']script\.js(?:\?[^\"']*)?[\"'][^>]*>\s*</script>",
        lambda _match: f"<script>\n{script}\n</script>",
        html,
        count=1,
        flags=re.I,
    )

    resources = {
        path.relative_to(play).as_posix(): _data_uri(path)
        for path in asset_paths
    }
    for relative, data_uri in sorted(resources.items(), key=lambda item: len(item[0]), reverse=True):
        html = html.replace(relative, data_uri)

    encoded_size = len(html.encode("utf-8"))
    if encoded_size > max_bytes:
        raise PortableExportError(
            f"The inlined letter is {encoded_size / (1024 * 1024):.1f} MB; "
            f"the Single HTML limit is {max_size_mb} MB. Use ZIP Package instead."
        )

    destination = (
        Path(output_dir).resolve()
        / play.parent.name
        / f"{play.name}.html"
    )
    tx = PathTransaction(destination)
    staging = tx.prepare()
    try:
        staging.parent.mkdir(parents=True, exist_ok=True)
        staging.write_text(html, encoding="utf-8", newline="\n")
        if "<html" not in html.casefold() or "data:" not in html:
            raise PortableExportError("The Single HTML export failed validation.")
        tx.commit()
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
) -> Path:
    tx = PathTransaction(destination)
    staging = tx.prepare()
    try:
        staging.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(staging, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(play.rglob("*")):
                if not path.is_file():
                    continue
                relative = path.relative_to(play).as_posix()
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
        tx.commit()
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


def _data_uri(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    payload = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{payload}"
