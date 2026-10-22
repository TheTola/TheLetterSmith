"""Read-only collection previews and explicitly confirmed GitHub setup."""
from __future__ import annotations

from dataclasses import dataclass, replace
from urllib.parse import quote

from publishing.destinations import (
    DestinationStore, PublishingDestination, default_pages_url, pages_url, repository_name,
)
from publishing.github_auth import GitHubOperationError
from publishing.github_config import GITHUB_REPOSITORY_NAME


@dataclass(frozen=True)
class CollectionPlan:
    destination: PublishingDestination
    exists: bool
    pages_enabled: bool


class GitHubCollections:
    def __init__(self, session, api, store=None, *, cancelled=lambda: False):
        self.session, self.api = session, api
        self.store = store or DestinationStore()
        self.cancelled = cancelled

    def request(self, method, path, payload=None, **kwargs):
        if self.cancelled():
            raise GitHubOperationError("publication_cancelled", "The account changed or the operation was cancelled.")
        result = self.api.request(method, path, payload, **kwargs)
        if self.cancelled():
            raise GitHubOperationError("publication_cancelled", "The account changed or the operation was cancelled.")
        return result

    def list_repositories(self):
        result = []
        page = 1
        while True:
            values = self.request("GET", f"/user/repos?affiliation=owner&per_page=100&page={page}", allow_list=True)
            if not isinstance(values, list):
                raise ValueError("GitHub returned an invalid repository list.")
            result.extend(v["name"] for v in values if v.get("owner", {}).get("id") == self.session.account.account_id
                          and not v.get("private") and not v.get("archived") and not v.get("disabled"))
            if len(values) < 100:
                return tuple(sorted(result, key=str.casefold))
            page += 1

    def restore_default(self):
        """Remember a previously connected site without changing GitHub."""
        current = self.store.default(self.session.account.account_id)
        if current:
            return current
        name = getattr(self.session, "repository_name", "")
        if not name:
            return None
        plan = self.inspect(name)
        if plan.exists and plan.pages_enabled:
            expected_id = getattr(self.session, "repository_id", 0)
            if expected_id and expected_id != plan.destination.repository_id:
                raise ValueError("The configured repository identity changed. Existing letters are unchanged.")
            self.store.save(plan.destination, make_default=True)
            return plan.destination
        return None

    def inspect(self, name, display_name=""):
        name = repository_name(name)
        account = self.session.account
        base = f"/repos/{quote(account.login, safe='')}/{quote(name, safe='')}"
        try:
            info = self.request("GET", base)
        except GitHubOperationError as error:
            if error.code != "not_found":
                raise
            return CollectionPlan(PublishingDestination(account.account_id, account.login, name,
                display_name=display_name or name, base_url=default_pages_url(account.login, name)), False, False)
        owner = info.get("owner", {})
        if (owner.get("id") != account.account_id or str(owner.get("login", "")).casefold() != account.login.casefold()
                or str(info.get("name", "")).casefold() != name.casefold()):
            raise ValueError("Repository identity changed. Review its owner and name before continuing.")
        if info.get("private") or info.get("archived") or info.get("disabled"):
            raise ValueError("Choose an active public repository. Its visibility has not been changed.")
        permissions = info.get("permissions", {})
        if not permissions.get("push"):
            raise ValueError("The connected account and GitHub App need write access to this repository.")
        destination = PublishingDestination(account.account_id, account.login, info["name"], int(info["id"]),
            display_name or name, str(info.get("default_branch") or "main"),
            card_prefix="letters" if name.casefold() == GITHUB_REPOSITORY_NAME.casefold() else "")
        try:
            pages = self.request("GET", base + "/pages")
        except GitHubOperationError as error:
            if error.code != "not_found":
                raise
            if not permissions.get("admin"):
                raise ValueError("Administration permission is required to enable Pages. Existing content is unchanged.")
            return CollectionPlan(replace(destination, base_url=default_pages_url(account.login, name)), True, False)
        source = pages.get("source") or {}
        if pages.get("build_type") == "workflow" or source.get("path") not in ("/", "/docs") or not source.get("branch"):
            raise ValueError("This repository uses an unsupported Pages deployment. Choose a branch-based site; its configuration is unchanged.")
        return CollectionPlan(replace(destination, branch=source["branch"], source=source["path"],
            base_url=pages_url(pages.get("html_url")), confirmed=True), True, True)

    def configure(self, plan: CollectionPlan, *, confirmed=False):
        if not confirmed:
            raise ValueError("Confirm the collection preview before changing GitHub.")
        current = self.inspect(plan.destination.repository, plan.destination.display_name)
        if current != plan:
            raise ValueError("The repository changed after the preview. Review the new preview before confirming.")
        destination = current.destination
        if not current.exists:
            self.request("POST", "/user/repos", {"name": destination.repository,
                "description": "Letters published with Letter Smith", "private": False,
                "auto_init": True, "has_issues": False, "has_projects": False, "has_wiki": False}, expected=(201,))
            current = self.inspect(destination.repository, destination.display_name)
            if not current.exists:
                raise ValueError("The repository was created but access is pending. Grant the app access and retry setup.")
            destination = current.destination
            self.store.save(destination)
        if not current.pages_enabled:
            base = f"/repos/{quote(destination.owner, safe='')}/{quote(destination.repository, safe='')}"
            self.request("POST", base + "/pages", {"build_type": "legacy",
                "source": {"branch": destination.branch, "path": destination.source}}, expected=(201,))
        verified = self.inspect(destination.repository, destination.display_name)
        if not verified.pages_enabled:
            raise ValueError("The collection exists but Pages setup is incomplete. Retry setup.")
        self.store.save(verified.destination, make_default=True)
        return verified.destination

    def validate(self, destination):
        if destination.account_id != self.session.account.account_id:
            raise ValueError("Reconnect the account that owns this publication.")
        current = self.inspect(destination.repository, destination.display_name)
        if not current.exists or not current.pages_enabled:
            raise ValueError("Set up this publishing collection before publishing. No files were changed.")
        actual = current.destination
        if destination.repository_id and destination.repository_id != actual.repository_id:
            raise ValueError("This repository name now refers to a different repository. No files were changed.")
        if destination.confirmed and (destination.branch, destination.source, destination.base_url) != (
                actual.branch, actual.source, actual.base_url):
            raise ValueError("The Pages source or URL changed. Review collection setup before publishing.")
        return actual
