from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from urllib.parse import unquote, urlsplit
import uuid

from test_github_library import MemoryGit
from publishing.destinations import DestinationStore, PublishingDestination
from publishing.github_auth import GitHubAccount, GitHubSession, GitHubToken
from publishing.github_migration import GitHubMigration
from publishing.github_pages import PUBLICATION_MARKER, _git_blob_sha


class CollectionGit(MemoryGit):
    def request(self, method, path, payload=None, **kwargs):
        parts = unquote(path).split("/")
        name = parts[3] if len(parts) > 3 else ""
        if name in self.repos and path.endswith("/pages"):
            self.calls.append((method, path, payload))
            return {"html_url": f"https://ada.github.io/{name}" if name != "ada.github.io" else "https://ada.github.io",
                    "source": {"branch": "main", "path": "/"}}
        if method == "POST" and path.endswith("/git/trees"):
            payload = dict(payload, tree=[dict(item, sha=self.blob(item["content"].encode())) if "content" in item else item for item in payload["tree"]])
        result = super().request(method, path, payload, **kwargs)
        if len(parts) == 4:
            result.update(id=11 if name == "ada.github.io" else 22, owner={"login": "ada", "id": 7},
                          permissions={"push": True, "admin": True})
        return result

    def fetch(self, url, timeout):
        path = unquote(urlsplit(url).path).lstrip("/")
        name = "Letters-to-Keep" if path.startswith("Letters-to-Keep/") else "ada.github.io"
        if name != "ada.github.io":
            path = path.removeprefix(name + "/")
        files = self.trees[self.commits[self.repos[name]["head"]][0]]
        return self.blobs.get(files.get(path))


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.session = GitHubSession(GitHubAccount("ada", 7), GitHubToken("test"))
        self.project_id = str(uuid.uuid4())
        self.api = CollectionGit()
        marker = {"schema_version": 2, "provider": "github_pages", "project_id": self.project_id,
                  "public_path": "original", "title": "Original & Title"}
        self.original = {"original/index.html": b"<html><img src='image.png'></html>",
            "original/image.png": b"preserved bytes", "original/" + PUBLICATION_MARKER: json.dumps(marker).encode(),
            "index.html": b"existing site"}
        self.api.add_repo("ada.github.io", self.original)
        self.api.add_repo("Letters-to-Keep", {"index.html": b"target site"})
        self.source = PublishingDestination(7, "ada", "ada.github.io", 11, "Originals", base_url="https://ada.github.io", confirmed=True)
        self.target = PublishingDestination(7, "ada", "Letters-to-Keep", 22, "Letters", base_url="https://ada.github.io/Letters-to-Keep", confirmed=True)
        self.store = DestinationStore(self.root)
        self.store.save(self.source)
        self.store.save(self.target, make_default=True)
        self.migration = GitHubMigration(self.root, self.session, self.api, public_fetcher=self.api.fetch)

    def test_copy_is_pinned_verified_and_preserves_original_links(self):
        head, copies = self.migration.sources(self.source)
        plan = self.migration.preview_copy(copies[0], head, self.target, "Original & Title")
        self.assertTrue(all(method == "GET" for method, *_ in self.api.calls))
        with self.assertRaises(ValueError):
            self.migration.copy(plan)
        source_head = self.api.repos["ada.github.io"]["head"]
        result = self.migration.copy(plan, confirmed=True)
        self.assertTrue(result.success)
        self.assertEqual(self.api.repos["ada.github.io"]["head"], source_head)
        self.assertEqual(self.api.fetch(result.url + "image.png", 1), b"preserved bytes")
        self.assertEqual(self.api.fetch("https://ada.github.io/original/index.html", 1), self.original["original/index.html"])
        journal = json.loads((self.migration.journal_root / f"{plan.operation_id}.json").read_text())
        self.assertEqual(journal["state"], "complete")
        primary = self.store.primary(7, {"project_id": self.project_id, "published_page_url": copies[0].url})
        self.assertEqual(primary["url"], result.url)

    def test_failed_copy_retains_source_and_journal_and_can_resume(self):
        head, copies = self.migration.sources(self.source)
        plan = self.migration.preview_copy(copies[0], head, self.target, "Original & Café")
        source_head = self.api.repos["ada.github.io"]["head"]
        self.api.ref_conflict = True
        with self.assertRaises(ValueError):
            self.migration.copy(plan, confirmed=True)
        self.assertEqual(self.api.repos["ada.github.io"]["head"], source_head)
        self.assertIsNone(self.store.primary(7, {"project_id": self.project_id, "published_page_url": copies[0].url}))
        self.api.ref_conflict = False
        self.assertTrue(self.migration.copy(plan, confirmed=True).success)


if __name__ == "__main__":
    unittest.main()
