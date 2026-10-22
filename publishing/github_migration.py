"""Pinned, resumable copies of public presentations. Never removes source letters."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import hashlib
import json
from pathlib import Path
import tempfile
from urllib.parse import quote

from project_paths import application_paths
from project_repository import _safe_file, _writer_lock
from transactional_io import atomic_write_json
from publishing.destinations import DestinationStore, PublishingDestination, public_path, repository_name
from publishing.github_collections import GitHubCollections
from publishing.github_config import github_pages_repository_name
from publishing.github_library import GitHubLibrary, RemoteCopy
from publishing.github_pages import GitHubPagesPublisher, PUBLICATION_MARKER, _git_blob_sha


@dataclass(frozen=True)
class CopyPlan:
    source: dict
    source_head: str
    target: dict
    path: str
    url: str

    @property
    def operation_id(self):
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()[:32]


class GitHubMigration:
    def __init__(self, project_root, session, api, *, cancelled=lambda: False, public_fetcher=None):
        self.project_root, self.session, self.api = Path(project_root), session, api
        self.cancelled, self.public_fetcher = cancelled, public_fetcher
        self.store = DestinationStore(project_root)
        self.collections = GitHubCollections(session, api, self.store, cancelled=cancelled)
        self.journal_root = application_paths(project_root).migration_root / "publishing"

    def publisher(self):
        kwargs = {"public_fetcher": self.public_fetcher} if self.public_fetcher else {}
        return GitHubPagesPublisher(self.project_root, self.session, api=self.api,
            cancelled=self.cancelled, persist_publication=False, **kwargs)

    def sources(self, destination):
        destination = self.collections.validate(destination)
        self.store.save(destination)
        library = GitHubLibrary(self.session, self.api, cancelled=self.cancelled, store=self.store)
        # Inventory one chosen public collection; editable backup storage is never a source.
        snapshot = library._snapshot(destination.repository, missing_ok=False)
        head, tree = snapshot[2], snapshot[4]
        copies = []
        for entry in tree:
            path = str(entry.get("path", ""))
            if not path.startswith(destination.prefix):
                continue
            parts = path[len(destination.prefix):].split("/")
            if len(parts) != 2 or parts[1] != PUBLICATION_MARKER or entry.get("type") != "blob":
                continue
            folder = public_path(parts[0])
            if entry.get("size", 65537) > 65536:
                raise ValueError("A publication marker is too large to migrate safely.")
            data = library._blob(destination.repository, entry["sha"], entry["size"])
            marker = json.loads(data)
            import uuid
            project_id = str(uuid.UUID(marker["project_id"]))
            if marker.get("provider") != "github_pages" or marker.get("schema_version") not in (1, 2) or marker.get("public_path") != folder:
                raise ValueError("A publication marker does not match its folder. Resolve it before migrating.")
            prefix = destination.prefix + folder
            files = library._files(tree, prefix)
            if "index.html" not in {name for name, *_ in files}:
                raise ValueError("A published letter is missing index.html.")
            copies.append(RemoteCopy(destination.repository, prefix, project_id,
                str(marker.get("title") or folder), str(marker.get("recipient") or ""),
                str(marker.get("published_at") or ""), str(marker.get("source_fingerprint") or ""),
                files, entry["sha"], url=destination.url(folder), destination=destination.to_dict()))
        if len({copy.project_id for copy in copies}) != len(copies):
            raise ValueError("Multiple folders share a project identity. Resolve them before migration.")
        return head, tuple(copies)

    def preview_copy(self, source, source_head, target, path=None):
        if source.editable or not source.destination:
            raise ValueError("Choose a verified published presentation, not an editable backup.")
        target = self.collections.validate(target)
        source_destination = PublishingDestination.from_dict(source.destination)
        if source_destination.account_id != target.account_id:
            raise ValueError("Reconnect the account that owns both collections before copying.")
        requested = public_path(path or source.prefix.rsplit("/", 1)[-1], normalize=True)
        metadata = {"project_id": source.project_id, "recipient_title": source.title,
                    "published_destination": target.to_dict(), "_requested_public_path": requested}
        preview = self.publisher().preview(metadata)
        if preview["url"] == source.url:
            raise ValueError("This letter already uses that address. Choose another collection.")
        return CopyPlan(asdict(source), source_head, target.to_dict(), preview["path"], preview["url"])

    def copy(self, plan: CopyPlan, *, confirmed=False):
        if not confirmed:
            raise ValueError("Confirm the source and target URLs before copying.")
        target = self.collections.validate(PublishingDestination.from_dict(plan.target))
        source = RemoteCopy(**plan.source)
        source_destination = PublishingDestination.from_dict(source.destination)
        self.collections.validate(source_destination)
        if target.to_dict() != plan.target or target.url(plan.path) != plan.url:
            raise ValueError("The destination changed. Review migration again.")
        journal = self.journal_root / f"{plan.operation_id}.json"
        with _writer_lock(self.journal_root / ".lock"):
            record = json.loads(journal.read_text(encoding="utf-8")) if journal.exists() else {"version": 1, "plan": json.loads(json.dumps(asdict(plan))), "state": "prepared"}
            if record.get("version") != 1 or record.get("plan") != json.loads(json.dumps(asdict(plan))):
                raise ValueError("The migration journal does not match this operation.")
            atomic_write_json(journal, record)
            library = GitHubLibrary(self.session, self.api, cancelled=self.cancelled, store=self.store)
            with tempfile.TemporaryDirectory(prefix="lettersmith-copy-") as temporary:
                root = Path(temporary)
                expected = {}
                for name, sha, size in source.files:
                    if name == PUBLICATION_MARKER:
                        continue
                    content = library._blob(source.repository, sha, size)
                    path = _safe_file(root, name)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(content)
                    expected[name] = sha
                record.update(state="uploading", expected_files=expected)
                atomic_write_json(journal, record)
                metadata = {"project_id": source.project_id, "recipient_title": source.title,
                    "recipient_name": source.recipient, "source_fingerprint": source.fingerprint,
                    "published_destination": target.to_dict(), "_requested_public_path": plan.path,
                    "_confirmed_publication": {"destination": plan.target, "path": plan.path, "url": plan.url}}
                result = self.publisher().publish(root, metadata)
                record.update(state="verifying" if result.success else "pending", message=result.message)
                atomic_write_json(journal, record)
                if not result.success:
                    raise ValueError(result.message)
                snapshot = library._snapshot(target.repository, missing_ok=False)
                prefix = target.prefix + plan.path + "/"
                actual = {entry["path"][len(prefix):]: entry["sha"] for entry in snapshot[4]
                          if entry.get("type") == "blob" and entry["path"].startswith(prefix)}
                if any(actual.get(name) != sha for name, sha in expected.items()):
                    raise ValueError("Target file verification failed. The original letter remains available; retry migration.")
                self.store.record_location(source.project_id, source_destination, source.prefix.rsplit("/", 1)[-1])
                self.store.record_location(source.project_id, target, plan.path, primary=True)
                record.update(state="complete", target_commit=snapshot[2], old_url=source.url, new_url=result.url)
                atomic_write_json(journal, record)
                return result

    def rename_preview(self, destination, name):
        destination = self.collections.validate(destination)
        name = repository_name(name)
        if name.casefold() == destination.repository.casefold():
            raise ValueError("Choose a different repository name. Edit the collection label separately.")
        if destination.repository.casefold() == github_pages_repository_name(destination.owner).casefold():
            raise ValueError("Renaming a personal site needs a retained-site migration to preserve its links. Use Copy letters, or the documented repository migration tool with a complete backup.")
        candidate = self.collections.inspect(name, destination.display_name)
        if candidate.exists:
            raise ValueError("That repository name is already in use.")
        info = self.collections.request("GET", f"/repos/{quote(destination.owner)}/{quote(destination.repository)}")
        if not info.get("permissions", {}).get("admin"):
            raise ValueError("Repository administration permission is required to rename it.")
        head, copies = self.sources(destination)
        base = destination.base_url if not destination.base_url.startswith(f"https://{destination.owner.casefold()}.github.io/") else candidate.destination.base_url
        renamed = replace(destination, repository=name, base_url=base)
        return {"source": destination.to_dict(), "target": renamed.to_dict(), "source_head": head,
                "copies": [asdict(c) for c in copies],
                "links": [{"old": c.url, "new": renamed.url(c.prefix.rsplit('/', 1)[-1])} for c in copies]}

    def rename(self, plan, *, confirmed=False):
        if not confirmed:
            raise ValueError("Confirm the repository rename and its link changes first.")
        plan = json.loads(json.dumps(plan))
        source = PublishingDestination.from_dict(plan["source"])
        target = PublishingDestination.from_dict(plan["target"])
        operation = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()[:32]
        journal = self.journal_root / f"rename-{operation}.json"
        with _writer_lock(self.journal_root / ".lock"):
            record = json.loads(journal.read_text(encoding="utf-8")) if journal.exists() else {"version": 1, "plan": plan, "state": "prepared"}
            if record.get("version") != 1 or record.get("plan") != plan:
                raise ValueError("The rename journal is incompatible. No changes were made.")
            atomic_write_json(journal, record)
            # Query the immutable ID to reconcile a lost response without renaming twice.
            info = self.collections.request("GET", f"/repositories/{source.repository_id}")
            if info.get("owner", {}).get("id") != source.account_id:
                raise ValueError("Repository ownership changed. Rename stopped.")
            if info.get("name") == source.repository:
                if json.loads(json.dumps(self.rename_preview(source, target.repository))) != plan:
                    raise ValueError("The repository changed after preview. Review the rename again.")
                self.collections.request("PATCH", f"/repos/{quote(source.owner)}/{quote(source.repository)}", {"name": target.repository})
            elif info.get("name") != target.repository:
                raise ValueError("Repository name changed unexpectedly. Rename stopped.")
            record["state"] = "renamed_pending_verification"
            atomic_write_json(journal, record)
            actual = self.collections.inspect(target.repository, target.display_name).destination
            if actual.repository_id != source.repository_id:
                raise ValueError("The renamed repository could not be verified. Retry reconciliation.")
            publisher = self.publisher()
            for value in plan["copies"]:
                copy = RemoteCopy(**value)
                files = {name: sha for name, sha, _size in copy.files}
                marker = publisher._remote_blob_bytes(actual.owner, actual.repository, files[PUBLICATION_MARKER])
                index = publisher._remote_blob_bytes(actual.owner, actual.repository, files["index.html"])
                path = copy.prefix.rsplit("/", 1)[-1]
                if not publisher._verify_publication(actual.url(path), marker, index, operation):
                    raise ValueError("Repository renamed; Pages verification is pending. Resume this operation to finish updating records.")
                self.store.record_location(copy.project_id, source, path)
                self.store.record_location(copy.project_id, actual, path, primary=True)
            self.store.save(actual)
            record["state"] = "complete"
            record["destination"] = actual.to_dict()
            atomic_write_json(journal, record)
            return actual
