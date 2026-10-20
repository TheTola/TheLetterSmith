"""Account-scoped discovery, verified downloads and private editable backups.

Public presentations and private canonical projects use separate repositories.
Every download reads immutable Git blobs; local imports never replace a project.
"""
from __future__ import annotations

import base64
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
from urllib.parse import quote
import uuid

from project_repository import ProjectRepository, _safe_file, _writer_lock
from transactional_io import atomic_write_json, is_link_or_reparse_point
from publishing.github_auth import GitHubOperationError
from publishing.github_config import GITHUB_REPOSITORY_NAME, github_pages_repository_name
from publishing.github_pages import (
    PUBLICATION_MARKER, MAX_BLOB_BYTES, MAX_BUNDLE_BYTES, _git_blob_sha,
    _publication_url, _valid_public_path,
)


BACKUP_REPOSITORY = "LetterSmith-Projects"
BACKUP_MARKER = "lettersmith-backup.json"
DOWNLOAD_RECEIPT = ".lettersmith-download.json"
MAX_FILES = 10000
_SHA = re.compile(r"[0-9a-f]{40}")


def _document(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _path(value: str) -> str:
    path = PurePosixPath(value)
    # Reject Windows aliases as well as traversal, links and absolute paths.
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                *(f"LPT{i}" for i in range(1, 10))}
    if (not value or path.is_absolute() or "\\" in value or ":" in value
            or any(part in ("", ".", "..") or part.endswith((" ", "."))
                   or part.split(".")[0].upper() in reserved
                   or any(ord(c) < 32 or c in '<>"|?*' for c in part)
                   for part in value.split("/"))):
        raise ValueError("GitHub returned an unsafe file path.")
    return value


@dataclass(frozen=True)
class RemoteCopy:
    repository: str
    prefix: str
    project_id: str
    title: str
    recipient: str
    timestamp: str
    fingerprint: str
    files: tuple[tuple[str, str, int], ...]
    marker_sha: str
    editable: bool = False
    url: str = ""
    digest: str = ""


@dataclass(frozen=True)
class LibraryProject:
    project_id: str
    presentation: RemoteCopy | None = None
    backup: RemoteCopy | None = None

    @property
    def title(self):
        return (self.backup or self.presentation).title

    @property
    def recipient(self):
        return (self.backup or self.presentation).recipient


def project_digest(stored) -> str:
    """Compare authored state/resources without machine-specific save timestamps."""
    value = stored.project.to_dict()
    value.pop("revision", None)
    value.get("metadata", {}).pop("saved_at", None)
    resources = json.loads((stored.asset_root / "resources.json").read_text(encoding="utf-8"))
    return hashlib.sha256(_document({"project": value, "resources": resources})).hexdigest()


def downloaded_presentations(root):
    """List managed downloads offline, without contacting or signing in to GitHub."""
    root = Path(root)
    result = {}
    if not root.is_dir() or is_link_or_reparse_point(root):
        return result
    for receipt in root.glob(f"*/*/*/{DOWNLOAD_RECEIPT}"):
        try:
            if receipt.stat().st_size > 4 * 1024 * 1024:
                continue
            value = json.loads(_safe_file(root, receipt.relative_to(root).as_posix()).read_text(encoding="utf-8"))
            if not isinstance(value, dict) or value.get("format") != "lettersmith.download" or value.get("version") != 1:
                continue
            owner, data = value["owner"], value["presentation"]
            repository = github_pages_repository_name(owner)
            if data["repository"] not in (repository, GITHUB_REPOSITORY_NAME) or data["editable"]:
                continue
            project_id = str(uuid.UUID(data["project_id"]))
            sha = data["marker_sha"]
            if not _SHA.fullmatch(sha):
                continue
            expected = _safe_file(root, f"{owner.casefold()}/{project_id}/{sha}")
            if receipt.parent.resolve() != expected.resolve():
                continue
            files = tuple(tuple(item) for item in data["files"])
            if len(files) > MAX_FILES or not all(len(item) == 3 and _path(item[0]) and _SHA.fullmatch(item[1])
                    and isinstance(item[2], int) and 0 <= item[2] <= MAX_BLOB_BYTES for item in files):
                continue
            if sum(item[2] for item in files) > MAX_BUNDLE_BYTES:
                continue
            slug = _valid_public_path(data["prefix"].split("/")[-1])
            data.update(files=files, url=_publication_url(owner, data["repository"], slug))
            copy = RemoteCopy(**data)
            _safe_file(expected, "index.html")
            result[f"download:{owner.casefold()}:{project_id}:{sha}"] = (copy, expected, owner)
        except (OSError, ValueError, TypeError, KeyError, GitHubOperationError):
            continue
    return result


class GitHubLibrary:
    def __init__(self, session, api, *, cancelled=lambda: False):
        self.session = session
        self.owner = session.account.login
        github_pages_repository_name(self.owner)  # Validate before constructing API paths.
        self.api = api
        self.cancelled = cancelled

    def _check(self):
        if self.cancelled():
            raise GitHubOperationError("cancelled", "The GitHub library operation was cancelled.")

    def _request(self, method, path, payload=None, **kwargs):
        self._check()
        result = self.api.request(method, path, payload, **kwargs)
        self._check()
        return result

    def _base(self, repository):
        if repository not in (github_pages_repository_name(self.owner), GITHUB_REPOSITORY_NAME, BACKUP_REPOSITORY):
            raise ValueError("This is not a Letter Smith repository.")
        return f"/repos/{quote(self.owner)}/{quote(repository)}"

    def _snapshot(self, repository, *, missing_ok=True):
        base = self._base(repository)
        try:
            info = self._request("GET", base)
        except GitHubOperationError as error:
            if missing_ok and error.code == "not_found":
                return None
            raise
        if str(info.get("owner", {}).get("login", "")).casefold() != self.owner.casefold():
            raise ValueError("GitHub returned a repository belonging to another account.")
        if repository == BACKUP_REPOSITORY and info.get("private") is not True:
            raise ValueError("LetterSmith-Projects must be private. No editable project was uploaded or downloaded.")
        branch = str(info.get("default_branch") or "main")
        try:
            ref = self._request("GET", f"{base}/git/ref/heads/{quote(branch, safe='')}")
        except GitHubOperationError as error:
            if error.code == "not_found" or error.status == 409:
                return info, branch, "", "", []
            raise
        head = ref.get("object", {}).get("sha", "")
        if not _SHA.fullmatch(head):
            raise ValueError("GitHub returned an invalid branch.")
        commit = self._request("GET", f"{base}/git/commits/{head}")
        tree_sha = commit.get("tree", {}).get("sha", "")
        if not _SHA.fullmatch(tree_sha):
            raise ValueError("GitHub returned an invalid tree.")
        tree = self._request("GET", f"{base}/git/trees/{tree_sha}?recursive=1")
        if tree.get("truncated") or not isinstance(tree.get("tree"), list):
            raise ValueError("GitHub's file listing is incomplete. Retry after reducing the repository size.")
        return info, branch, head, tree_sha, tree["tree"]

    def _blob(self, repository, sha, size):
        if not _SHA.fullmatch(sha) or not isinstance(size, int) or not 0 <= size <= MAX_BLOB_BYTES:
            raise ValueError("GitHub returned invalid file metadata.")
        value = self._request("GET", f"{self._base(repository)}/git/blobs/{sha}")
        content = value.get("content", "")
        if value.get("encoding") != "base64" or not isinstance(content, str) or len(content) > MAX_BLOB_BYTES * 2:
            raise ValueError("GitHub returned invalid file contents.")
        data = base64.b64decode("".join(content.split()), validate=True)
        if len(data) != size or _git_blob_sha(data) != sha:
            raise ValueError("GitHub file verification failed. Nothing was imported.")
        return data

    def _files(self, tree, prefix):
        result, seen = [], set()
        for entry in tree:
            name = str(entry.get("path", ""))
            if not name.startswith(prefix + "/") or entry.get("type") == "tree":
                continue
            relative = _path(name[len(prefix) + 1:])
            if (entry.get("type") != "blob" or entry.get("mode") not in ("100644", "100755")
                    or relative.casefold() in seen):
                raise ValueError("GitHub contains linked, unsupported or duplicate files.")
            sha, size = entry.get("sha", ""), entry.get("size")
            if not _SHA.fullmatch(sha) or not isinstance(size, int) or not 0 <= size <= MAX_BLOB_BYTES:
                raise ValueError("GitHub returned invalid file metadata.")
            seen.add(relative.casefold())
            result.append((relative, sha, size))
        if len(result) > MAX_FILES or sum(item[2] for item in result) > MAX_BUNDLE_BYTES:
            raise ValueError("This letter exceeds the supported download size.")
        return tuple(sorted(result))

    def discover(self):
        projects, warnings = {}, []
        for repository in (github_pages_repository_name(self.owner), GITHUB_REPOSITORY_NAME, BACKUP_REPOSITORY):
            try:
                snapshot = self._snapshot(repository)
                if snapshot is None:
                    continue
                tree = snapshot[4]
                marker_name = BACKUP_MARKER if repository == BACKUP_REPOSITORY else PUBLICATION_MARKER
                for entry in tree:
                    parts = str(entry.get("path", "")).split("/")
                    # Older publishing used letters/<slug>; current publishing uses <slug>.
                    valid_depth = ((len(parts) == 3 and parts[0] == "letters") if repository == GITHUB_REPOSITORY_NAME
                                   else len(parts) == 2)
                    if not valid_depth or parts[-1] != marker_name or entry.get("type") != "blob":
                        continue
                    try:
                        if entry.get("size", MAX_BLOB_BYTES) > 65536:
                            raise ValueError("The letter marker is too large.")
                        marker = json.loads(self._blob(repository, entry["sha"], entry["size"]))
                        project_id = str(uuid.UUID(marker["project_id"]))
                        prefix = "/".join(parts[:-1])
                        editable = repository == BACKUP_REPOSITORY
                        if editable:
                            if prefix != project_id or marker.get("format") != "lettersmith.editable-backup" or marker.get("version") != 1:
                                raise ValueError("Unsupported editable backup.")
                        elif marker.get("provider") != "github_pages" or marker.get("schema_version") not in (1, 2):
                            raise ValueError("Unsupported publication marker.")
                        else:
                            _valid_public_path(parts[-2])
                            if marker.get("public_path") not in (None, parts[-2]):
                                raise ValueError("The publication marker belongs to another path.")
                        files = self._files(tree, prefix)
                        if not editable and "index.html" not in {item[0] for item in files}:
                            raise ValueError("The presentation is missing index.html.")
                        copy = RemoteCopy(repository, prefix, project_id,
                            str(marker.get("title") or parts[-2].replace("-", " ").title()),
                            str(marker.get("recipient") or ""),
                            str(marker.get("saved_at") or marker.get("published_at") or ""),
                            str(marker.get("source_fingerprint") or ""), files, entry["sha"], editable,
                            "" if editable else _publication_url(self.owner, repository, parts[-2]),
                            str(marker.get("project_digest") or ""))
                        previous = projects.get(project_id, LibraryProject(project_id))
                        if (editable and previous.backup) or (not editable and previous.presentation):
                            raise ValueError("Multiple GitHub copies share this project identity.")
                        projects[project_id] = LibraryProject(project_id,
                            previous.presentation if editable else copy, copy if editable else previous.backup)
                    except (ValueError, KeyError, TypeError) as error:
                        warnings.append(f"{repository}/{entry.get('path', '')}: {error}")
            except (GitHubOperationError, ValueError) as error:
                if isinstance(error, GitHubOperationError) and error.code == "cancelled":
                    raise
                warnings.append(f"{repository}: {error}")
        return tuple(sorted(projects.values(), key=lambda p: (p.title.casefold(), p.project_id))), tuple(warnings)

    def _download(self, copy, destination):
        # Validate again even when a caller supplies a RemoteCopy directly.
        files = self._files([{"path": copy.prefix + "/" + path, "sha": sha, "size": size,
                              "mode": "100644", "type": "blob"} for path, sha, size in copy.files], copy.prefix)
        for relative, sha, size in files:
            path = _safe_file(destination, relative)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(self._blob(copy.repository, sha, size))

    def presentation_path(self, copy, root):
        project_id = str(uuid.UUID(copy.project_id))
        if not _SHA.fullmatch(copy.marker_sha):
            raise ValueError("GitHub returned an invalid letter identity.")
        return _safe_file(Path(root), f"{self.owner.casefold()}/{project_id}/{copy.marker_sha}")

    def download_presentation(self, copy, root):
        if copy.editable:
            raise ValueError("Choose a published presentation.")
        if DOWNLOAD_RECEIPT in {relative for relative, _, _ in copy.files}:
            raise ValueError("The publication uses a reserved download metadata name.")
        destination = self.presentation_path(copy, root)
        receipt = {"format": "lettersmith.download", "version": 1, "owner": self.owner,
                   "presentation": asdict(copy)}
        if destination.exists():
            for relative, sha, size in copy.files:
                self._check()
                data = _safe_file(destination, relative).read_bytes()
                if len(data) != size or _git_blob_sha(data) != sha:
                    raise ValueError("The downloaded presentation has damaged files. Keep it for recovery and choose another download folder.")
            atomic_write_json(destination / DOWNLOAD_RECEIPT, receipt)
            return destination
        destination.parent.mkdir(parents=True, exist_ok=True)
        if is_link_or_reparse_point(destination.parent) or not destination.resolve().is_relative_to(Path(root).resolve()):
            raise ValueError("The download folder must not be a link.")
        with tempfile.TemporaryDirectory(prefix=".download-", dir=destination.parent) as temporary:
            staging = Path(temporary) / "letter"
            staging.mkdir()
            self._download(copy, staging)
            atomic_write_json(staging / DOWNLOAD_RECEIPT, receipt)
            self._check()
            os.rename(staging, destination)
        return destination

    def download_project(self, copy, editable_root, *, as_copy=False):
        if not copy.editable:
            raise ValueError("This publication has no editable backup. Download its presentation instead.")
        root = Path(editable_root)
        if is_link_or_reparse_point(root):
            raise ValueError("The project library must not be a link.")
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".github-", dir=root) as temporary:
            staging = Path(temporary) / "source"
            staging.mkdir()
            self._download(copy, staging)
            stored = ProjectRepository(staging / ".lettersmith-project").load()
            if stored.project.project_id != copy.project_id or project_digest(stored) != copy.digest:
                raise ValueError("The editable backup does not match its GitHub marker.")
            project = stored.project.independent_copy() if as_copy else stored.project
            destination = root / project.project_id
            with _writer_lock(root / ".library-lock"):
                if destination.exists() or is_link_or_reparse_point(destination):
                    raise ValueError("A local project already has this identity. Download a separate copy to keep both versions.")
                imported = Path(temporary) / "imported"
                ProjectRepository(imported / ".lettersmith-project").save(
                    project, asset_root=stored.asset_root, migration_report=stored.migration_report)
                self._check()
                os.rename(imported, destination)
            return destination

    def backup_project(self, capture, *, expected_marker=""):
        """Explicitly back up a captured project; never overwrite an unseen remote version."""
        with tempfile.TemporaryDirectory(prefix="lettersmith-backup-") as temporary:
            repository = ProjectRepository(Path(temporary) / ".lettersmith-project")
            stored = repository.save(capture.project, asset_root=capture.asset_root,
                fallback_asset_root=capture.fallback_asset_root, migration_report=capture.migration_report)
            snapshot = self._snapshot(BACKUP_REPOSITORY)
            if snapshot is None:
                try:
                    self._request("POST", "/user/repos", {
                        "name": BACKUP_REPOSITORY, "private": True, "auto_init": True,
                        "description": "Private editable Letter Smith project backups.",
                    }, expected=(201,))
                except GitHubOperationError as error:
                    if error.status in (403, 404, 422):
                        raise GitHubOperationError("repository_access",
                            "Allow the Letter Smith GitHub App to access the private LetterSmith-Projects repository, then refresh and retry.",
                            status=error.status) from error
                    raise
                snapshot = self._snapshot(BACKUP_REPOSITORY, missing_ok=False)
            info, branch, head, tree_sha, tree = snapshot
            if not head:
                raise ValueError("The private backup repository needs an initial commit. Initialize it on GitHub and retry.")
            project_id = stored.project.project_id
            prefix = project_id + "/"
            old = {entry["path"]: entry for entry in tree if entry.get("type") == "blob" and entry.get("path", "").startswith(prefix)}
            remote_marker = old.get(prefix + BACKUP_MARKER, {}).get("sha", "")
            if remote_marker != expected_marker:
                raise ValueError("The GitHub backup changed. Refresh the library and compare versions before backing up again.")
            if old and not remote_marker:
                raise ValueError("The GitHub destination contains unrecognized files.")
            payload = stored.project.to_dict()
            marker = {
                "format": "lettersmith.editable-backup", "version": 1, "project_id": project_id,
                "title": payload["metadata"].get("title") or payload["metadata"].get("recipient_title", "Untitled Project"),
                "recipient": payload["metadata"].get("recipient_name", ""),
                "saved_at": datetime.now(timezone.utc).isoformat(),
                "project_digest": project_digest(stored),
            }
            files = {BACKUP_MARKER: _document(marker), ".lettersmith-project/current.json": repository.pointer.read_bytes()}
            for path in stored.asset_root.rglob("*"):
                self._check()
                if path.is_file():
                    relative = ".lettersmith-project/g/" + stored.revision + "/" + path.relative_to(stored.asset_root).as_posix()
                    files[_path(relative)] = path.read_bytes()
            if len(files) > MAX_FILES or any(len(data) > MAX_BLOB_BYTES for data in files.values()) or sum(map(len, files.values())) > MAX_BUNDLE_BYTES:
                raise ValueError("This editable project exceeds GitHub's supported backup size.")
            base = self._base(BACKUP_REPOSITORY)
            updates = []
            for relative, data in files.items():
                path, sha = prefix + relative, _git_blob_sha(data)
                if old.get(path, {}).get("sha") != sha:
                    blob = self._request("POST", f"{base}/git/blobs",
                        {"encoding": "base64", "content": base64.b64encode(data).decode("ascii")}, expected=(201,))
                    if blob.get("sha") != sha:
                        raise ValueError("GitHub did not confirm the uploaded file.")
                    updates.append({"path": path, "mode": "100644", "type": "blob", "sha": sha})
            updates.extend({"path": path, "mode": "100644", "type": "blob", "sha": None}
                           for path in old if path[len(prefix):] not in files)
            # Recheck privacy immediately before making uploaded blobs reachable.
            latest = self._request("GET", base)
            if latest.get("private") is not True:
                raise ValueError("The backup repository is no longer private. The backup was not committed.")
            new_tree = self._request("POST", f"{base}/git/trees", {"base_tree": tree_sha, "tree": updates}, expected=(201,))
            commit = self._request("POST", f"{base}/git/commits",
                {"message": "Back up editable Letter Smith project", "tree": new_tree["sha"], "parents": [head]}, expected=(201,))
            ref_path = f"{base}/git/refs/heads/{quote(branch, safe='')}"
            self._request("PATCH", ref_path, {"sha": commit["sha"], "force": False})
            ref = self._request("GET", f"{base}/git/ref/heads/{quote(branch, safe='')}")
            if ref.get("object", {}).get("sha") != commit["sha"]:
                raise ValueError("GitHub changed during backup verification. Refresh the library to check the saved copy.")
            return project_id
