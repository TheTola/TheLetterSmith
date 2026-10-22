"""Nonsecret, account-scoped publishing destinations and portable identities."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import json
from pathlib import Path
import re
import unicodedata
from urllib.parse import quote, urlsplit

from project_paths import application_paths
from project_repository import _writer_lock
from transactional_io import atomic_write_json
from publishing.github_config import GITHUB_REPOSITORY_NAME, github_pages_repository_name


DESTINATION_KEY = "published_destination"
_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]{1,100}")
_DEVICES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
            *(f"LPT{i}" for i in range(1, 10)), "COM¹", "COM²", "COM³", "LPT¹", "LPT²", "LPT³"}
_RESERVED = {"index.html", "404.html", "cname", ".nojekyll", ".git", ".github",
             ".lettersmith-publishing.json", "lettersmith-publication.json"}


def repository_name(value: object) -> str:
    name = str(value or "")
    if not _REPOSITORY.fullmatch(name) or name in (".", "..") or name.endswith("."):
        raise ValueError("Use 1–100 letters, numbers, dots, underscores or hyphens for the repository name.")
    return name


def repository_display_name(value: object) -> str:
    """Format a label only; never use it as a repository identifier."""
    return " ".join(re.sub(r"[_/\\-]+", " ", str(value or "")).split()).title()


def public_path(value: object, *, normalize: bool = False) -> str:
    name = str(value or "")
    if normalize:
        name = unicodedata.normalize("NFC", name)
    if (not name or name != name.strip() or name.endswith(".") or name in (".", "..")
            or len(name.encode("utf-8")) > 180 or name.casefold() in _RESERVED
            or name.split(".")[0].upper() in _DEVICES
            or any(c in '/\\<>:"|?*%' or unicodedata.category(c) in ("Cc", "Cf", "Cs") for c in name)):
        raise ValueError("Choose a single folder name without reserved characters, trailing spaces/dots or device names (maximum 180 UTF-8 bytes).")
    return name


def path_key(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold()


def pages_url(value: object) -> str:
    url = str(value or "").rstrip("/")
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or "\\" in url or any(c.isspace() for c in url)
            or parsed.port not in (None, 443)):
        raise ValueError("GitHub did not return a valid HTTPS Pages address.")
    return url


def default_pages_url(owner: str, repository: str) -> str:
    personal = github_pages_repository_name(owner)
    repository_name(repository)
    suffix = "" if repository.casefold() == personal.casefold() else "/" + quote(repository, safe="")
    return f"https://{personal.casefold()}{suffix}"


@dataclass(frozen=True)
class PublishingDestination:
    account_id: int
    owner: str
    repository: str
    repository_id: int = 0
    display_name: str = ""
    branch: str = "main"
    source: str = "/"
    card_prefix: str = ""
    base_url: str = ""
    confirmed: bool = False
    version: int = 1

    def __post_init__(self):
        github_pages_repository_name(self.owner)
        repository_name(self.repository)
        if (self.version != 1 or self.account_id <= 0 or self.repository_id < 0
                or self.source not in ("/", "/docs") or self.card_prefix not in ("", "letters")
                or not self.branch or any(ord(c) < 32 for c in self.branch)):
            raise ValueError("Unsupported publishing destination. Its stored data was preserved.")
        if self.base_url:
            pages_url(self.base_url)

    @property
    def key(self) -> str:
        return f"{self.account_id}:{self.repository_id or self.repository.casefold()}"

    @property
    def root(self) -> str:
        return self.source.strip("/") + "/" if self.source != "/" else ""

    @property
    def prefix(self) -> str:
        return self.root + (self.card_prefix + "/" if self.card_prefix else "")

    def url(self, path: str) -> str:
        base = pages_url(self.base_url or default_pages_url(self.owner, self.repository))
        prefix = self.card_prefix + "/" if self.card_prefix else ""
        return f"{base}/{prefix}{quote(public_path(path), safe='')}/"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict):
        if not isinstance(value, dict):
            raise ValueError("Invalid publishing destination.")
        return cls(**value)


class DestinationStore:
    def __init__(self, project_root=None):
        self.path = application_paths(project_root).settings_root / "publishing-collections.json"

    def read(self):
        if not self.path.exists():
            return {"version": 1, "collections": {}, "defaults": {}, "locations": {}}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if (not isinstance(data, dict) or data.get("version") != 1 or not all(isinstance(data.get(k), dict)
                for k in ("collections", "defaults", "locations"))):
            raise ValueError("Unsupported publishing collections file. It has not been changed.")
        return data

    def all(self, account_id: int):
        return tuple(PublishingDestination.from_dict(v) for v in self.read()["collections"].values()
                     if v.get("account_id") == account_id)

    def default(self, account_id: int):
        data = self.read()
        value = data["collections"].get(data["defaults"].get(str(account_id), ""))
        return PublishingDestination.from_dict(value) if value and value.get("account_id") == account_id else None

    def find(self, account_id: int, repository: str):
        return next((d for d in self.all(account_id) if d.repository.casefold() == repository.casefold()), None)

    def save(self, destination: PublishingDestination, *, make_default=False):
        with _writer_lock(self.path.parent / ".publishing-lock"):
            data = self.read()
            # Stable repository IDs survive renames; names never become credentials.
            data["collections"][destination.key] = destination.to_dict()
            if make_default:
                data["defaults"][str(destination.account_id)] = destination.key
            atomic_write_json(self.path, data)

    def record_location(self, project_id, destination, path, *, primary=False):
        import uuid
        project_id = str(uuid.UUID(project_id))
        location = {"destination": destination.to_dict(), "path": public_path(path), "url": destination.url(path)}
        with _writer_lock(self.path.parent / ".publishing-lock"):
            data = self.read()
            key = f"{destination.account_id}:{project_id}"
            record = data["locations"].setdefault(key, {"copies": []})
            record["copies"] = [c for c in record["copies"] if c["url"] != location["url"]] + [location]
            if primary:
                record["primary"] = location
            atomic_write_json(self.path, data)

    def primary(self, account_id, metadata):
        record = self.read()["locations"].get(f"{account_id}:{metadata.get('project_id', '')}", {})
        primary = record.get("primary")
        known = {copy["url"] for copy in record.get("copies", [])}
        return primary if primary and metadata.get("published_page_url") in known else None


def destination_for(session, metadata: dict, store: DestinationStore):
    saved = metadata.get(DESTINATION_KEY)
    owner = str(metadata.get("published_github_owner") or session.account.login)
    if owner.casefold() != session.account.login.casefold():
        raise ValueError(f"Reconnect {owner} to change this publication.")
    migrated = store.primary(session.account.account_id, metadata)
    if migrated:
        result = PublishingDestination.from_dict(migrated["destination"])
    elif saved:
        result = PublishingDestination.from_dict(saved)
    elif metadata.get("published_github_repository"):
        name = repository_name(metadata["published_github_repository"])
        result = store.find(session.account.account_id, name) or PublishingDestination(
            session.account.account_id, owner, name,
            card_prefix="letters" if name.casefold() == GITHUB_REPOSITORY_NAME.casefold() else "")
    else:
        result = store.default(session.account.account_id) or PublishingDestination(
            session.account.account_id, session.account.login, github_pages_repository_name(session.account.login))
    if result.account_id != session.account.account_id or result.owner.casefold() != session.account.login.casefold():
        raise ValueError("Reconnect the account that owns this publishing collection.")
    return result
