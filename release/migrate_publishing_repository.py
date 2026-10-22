"""Explicit administration tool: rename a Pages repository and retain its old links.

Requires Git and an authenticated GitHub CLI. The desktop application does not
depend on either tool. No repository or source file is deleted by this command.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from project_paths import ApplicationPaths
from transactional_io import atomic_write_json
from publishing.destinations import DestinationStore, PublishingDestination, default_pages_url, repository_name
from publishing.github_pages import _fetch_public_bytes, _git_blob_sha, PUBLICATION_MARKER


def run(args, *, payload=None, binary=False):
    result = subprocess.run(args, input=json.dumps(payload).encode() if payload is not None else None,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr.decode("utf-8", errors="replace").strip())
    return result.stdout if binary else result.stdout.decode("utf-8").strip()


def api(path, *, method="GET", payload=None, missing=False):
    args = ["gh", "api", path, "--method", method]
    if payload is not None:
        args += ["--input", "-"]
    try:
        body = run(args, payload=payload)
        return json.loads(body) if body else {}
    except RuntimeError as error:
        if missing and "HTTP 404" in str(error):
            return None
        raise


def git(backup, *args, binary=False):
    return run(["git", "-C", str(backup), *args], binary=binary)


def inventory(backup, head):
    entries = []
    for raw in git(backup, "ls-tree", "-rz", "-l", head, binary=True).split(b"\0"):
        if not raw:
            continue
        fields, name = raw.split(b"\t", 1)
        mode, kind, sha, size = fields.decode().split()
        path = name.decode("utf-8")
        if kind != "blob" or mode not in ("100644", "100755"):
            raise RuntimeError("The site contains linked or unsupported objects. Nothing was renamed.")
        entries.append({"path": path, "sha": sha, "size": int(size)})
    return entries


def verify_pages(backup, head, base, entries, *, timeout=900):
    checks = {item["path"]: item for item in entries if item["path"].endswith("/" + PUBLICATION_MARKER)
              or item["path"] == "index.html"}
    cards = []
    for path in tuple(checks):
        if not path.endswith("/" + PUBLICATION_MARKER):
            continue
        prefix = path.rsplit("/", 1)[0] + "/"
        index = next((item for item in entries if item["path"] == prefix + "index.html"), None)
        if not index:
            raise RuntimeError("A published letter is missing its index. Migration verification stopped.")
        checks[index["path"]] = index
        marker = json.loads(git(backup, "show", f"{head}:{path}", binary=True))
        cards.append({"project_id": marker["project_id"], "path": prefix.rstrip("/")})
        # Verify each card's entry points plus representative deployed resources.
        for extensions in ((".js",), (".css",), (".png", ".jpg", ".jpeg", ".webp"), (".mp3", ".ogg", ".wav")):
            candidates = [item for item in entries if item["path"].startswith(prefix) and item["path"].lower().endswith(extensions)]
            if candidates:
                item = min(candidates, key=lambda x: x["size"])
                checks[item["path"]] = item
    pending = dict(checks)
    deadline = time.monotonic() + timeout
    def check(item):
        path, entry = item
        url = base.rstrip("/") + "/" + "/".join(quote(part, safe="") for part in path.split("/"))
        try:
            data = _fetch_public_bytes(url + "?lettersmith-migration=" + head[:12], 20)
            return path, data is not None and _git_blob_sha(data) == entry["sha"]
        except Exception:
            return path, False
    while pending and time.monotonic() < deadline:
        with ThreadPoolExecutor(max_workers=6) as pool:
            for path, verified in pool.map(check, tuple(pending.items())):
                if verified:
                    del pending[path]
        if pending:
            print(f"Waiting for Pages at {base}: {len(pending)}/{len(checks)} checks pending", flush=True)
            time.sleep(10)
    if pending:
        raise RuntimeError(f"Pages verification pending at {base}: {len(pending)} resources. Rerun to resume; no source was deleted.")
    return {"base_url": base, "verified_resources": len(checks), "cards": cards}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--old-name", help="Current repository name; defaults to the account's personal site.")
    parser.add_argument("--new-name", required=True)
    parser.add_argument("--display-name", required=True)
    parser.add_argument("--backup-root", type=Path, required=True)
    parser.add_argument("--execute", action="store_true", help="Explicitly rename, recreate the retained site, and verify both sites.")
    args = parser.parse_args()
    owner, target_name = args.owner, repository_name(args.new_name)
    old_name = repository_name(args.old_name or owner + ".github.io")
    if target_name.casefold() == old_name.casefold():
        parser.error("The new repository name must differ from the current name.")
    user = api("user")
    if user["login"].casefold() != owner.casefold():
        raise RuntimeError("The GitHub CLI is connected to a different account. Nothing was changed.")
    root = args.backup_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    journal_path = root / "migration.json"
    backup = root / "original.git"
    record = json.loads(journal_path.read_text(encoding="utf-8")) if journal_path.exists() else None
    if record and (record.get("version") != 1 or record.get("owner") != owner or record.get("target") != target_name
                   or record.get("source", owner + ".github.io") != old_name):
        raise RuntimeError("This backup directory belongs to a different migration.")
    if not record:
        original = api(f"repos/{owner}/{old_name}")
        if original["name"].casefold() != old_name.casefold() or original["owner"]["id"] != user["id"] or original["private"]:
            raise RuntimeError("The source repository does not match the expected public site.")
        if api(f"repos/{owner}/{target_name}", missing=True):
            raise RuntimeError("The new repository name is already occupied.")
        pages = api(f"repos/{owner}/{old_name}/pages")
        if pages.get("cname") or pages.get("build_type") == "workflow" or pages.get("source", {}).get("path") != "/":
            raise RuntimeError("This tool requires a site without a custom domain, using a branch root.")
        if not backup.exists():
            run(["git", "clone", "--mirror", original["clone_url"], str(backup)])
        branch = pages["source"]["branch"]
        head = git(backup, "rev-parse", f"refs/heads/{branch}")
        remote = api(f"repos/{owner}/{old_name}/git/ref/heads/{quote(branch, safe='')}")["object"]["sha"]
        if head != remote:
            raise RuntimeError("The remote source changed since backup. Refresh and inspect the backup before retrying.")
        git(backup, "fsck", "--full")
        entries = inventory(backup, head)
        atomic_write_json(root / "files.json", {"files": entries})
        record = {"version": 1, "owner": owner, "account_id": user["id"], "source": old_name, "target": target_name,
                  "original_id": original["id"], "branch": branch, "head": head,
                  "tree": git(backup, "rev-parse", head + "^{tree}"), "state": "backed_up",
                  "files": len(entries), "bytes": sum(e["size"] for e in entries)}
        atomic_write_json(journal_path, record)
    entries = json.loads((root / "files.json").read_text(encoding="utf-8"))["files"]
    if not args.execute:
        print(json.dumps(record, indent=2))
        print("Backup verified. Use --execute only for the explicitly approved rename and retained-site restoration.")
        return
    branch, head = record["branch"], record["head"]
    original = api(f"repositories/{record['original_id']}")
    if original["owner"]["id"] != record["account_id"]:
        raise RuntimeError("The original repository changed owner. Migration stopped.")
    if original["name"] == old_name:
        # Recheck the source immediately before the irreversible name transition.
        if api(f"repos/{owner}/{old_name}/git/ref/heads/{quote(branch, safe='')}")["object"]["sha"] != head:
            raise RuntimeError("The source changed. No rename was performed.")
        print(f"Renaming {owner}/{old_name} to {target_name}", flush=True)
        original = api(f"repos/{owner}/{old_name}", method="PATCH", payload={"name": target_name})
    if original["id"] != record["original_id"] or original["name"] != target_name:
        raise RuntimeError("The original repository has an unexpected name. Migration stopped.")
    record["state"] = "renamed"
    atomic_write_json(journal_path, record)
    retained = api(f"repos/{owner}/{old_name}", missing=True)
    retained_description = f"Retained Letter Smith links. Migration {record['original_id']}-{head[:12]}."
    # GitHub's old API URL may redirect to the renamed repository until the old name is recreated.
    if not retained or retained["id"] == record["original_id"]:
        retained = api("user/repos", method="POST", payload={"name": old_name, "private": False, "auto_init": False,
            "description": retained_description})
        record["retained_id"] = retained["id"]
        atomic_write_json(journal_path, record)
    if retained["name"] != old_name or retained["owner"]["id"] != record["account_id"] or retained["private"]:
        raise RuntimeError("The retained repository does not match this migration. No files were overwritten.")
    if record.get("retained_id") and retained["id"] != record["retained_id"]:
        raise RuntimeError("The retained repository identity changed. Migration stopped.")
    if not record.get("retained_id") and retained.get("description") != retained_description:
        raise RuntimeError("The old repository name is occupied by an unrecognized repository. No files were overwritten.")
    record["retained_id"] = retained["id"]
    atomic_write_json(journal_path, record)
    try:
        ref = api(f"repos/{owner}/{old_name}/git/ref/heads/{quote(branch, safe='')}", missing=True)
    except RuntimeError as error:
        if "HTTP 409" not in str(error):
            raise
        ref = None
    if not ref:
        print("Restoring the original Git history and files at the old site name", flush=True)
        run(["git", "-C", str(backup), "push", f"https://github.com/{owner}/{old_name}.git",
             f"refs/heads/{branch}:refs/heads/{branch}"])
    elif ref["object"]["sha"] != head:
        raise RuntimeError("The retained site contains a different commit. Nothing was overwritten.")
    record["state"] = "retained_content_restored"
    atomic_write_json(journal_path, record)
    for name in (old_name, target_name):
        ref = api(f"repos/{owner}/{name}/git/ref/heads/{quote(branch, safe='')}")
        if ref["object"]["sha"] != head:
            raise RuntimeError(f"The {name} branch changed. Review it before continuing.")
        pages = api(f"repos/{owner}/{name}/pages", missing=True)
        if not pages:
            pages = api(f"repos/{owner}/{name}/pages", method="POST", payload={"build_type": "legacy", "source": {"branch": branch, "path": "/"}})
        if pages.get("source") != {"branch": branch, "path": "/"}:
            raise RuntimeError("Unexpected Pages configuration; it was not overwritten.")
        if pages.get("status") not in ("built", "building"):
            latest = api(f"repos/{owner}/{name}/pages/builds/latest", missing=True)
            if not latest or latest.get("status") not in ("built", "building"):
                try:
                    api(f"repos/{owner}/{name}/pages/builds", method="POST")
                except RuntimeError as error:
                    print(f"Build request outcome uncertain for {name}; verifying deployed content before retrying: {error}", flush=True)
    record["state"] = "verifying_pages"
    atomic_write_json(journal_path, record)
    old_base = default_pages_url(owner, old_name)
    new_base = default_pages_url(owner, target_name)
    record["old_site"] = verify_pages(backup, head, old_base, entries)
    atomic_write_json(journal_path, record)
    record["new_site"] = verify_pages(backup, head, new_base, entries)
    original_destination = PublishingDestination(user["id"], owner, old_name, retained["id"],
        "Original letters — retained links", branch=branch, base_url=old_base, confirmed=True)
    new_destination = replace(original_destination, repository=target_name, repository_id=record["original_id"],
                              display_name=args.display_name, base_url=new_base)
    runtime = ApplicationPaths.for_runtime()
    stores = [DestinationStore(ROOT), DestinationStore()]
    runtime_store = DestinationStore(ROOT)
    runtime_store.path = runtime.settings_root / "publishing-collections.json"
    stores.append(runtime_store)
    for store in {str(s.path): s for s in stores}.values():
        store.save(original_destination)
        store.save(new_destination, make_default=True)
        for card in record["new_site"]["cards"]:
            store.record_location(card["project_id"], original_destination, card["path"])
            store.record_location(card["project_id"], new_destination, card["path"], primary=True)
    record["state"] = "complete"
    record["completed_at"] = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
    atomic_write_json(journal_path, record)
    print(json.dumps(record, indent=2), flush=True)


if __name__ == "__main__":
    main()
