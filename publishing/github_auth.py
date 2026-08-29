from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from enum import Enum
from typing import Callable, Mapping

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

from application_identity import APPLICATION_NAME, PUBLISHER_NAME
from publishing.github_config import (
    GITHUB_API_URL,
    GITHUB_API_VERSION,
    GITHUB_DEVICE_CODE_URL,
    GITHUB_OAUTH_REQUIRED_SCOPES,
    GITHUB_TOKEN_URL,
    GitHubApplicationConfiguration,
    github_application_configuration,
    github_pages_repository_name,
)


_TOKEN_PATTERN = re.compile(r"\bgh[a-z]_[A-Za-z0-9]{16,}\b", re.IGNORECASE)
_BEARER_PATTERN = re.compile(r"(?i)\bBearer\s+[^\s,;\"'}\]]+")
_REQUIRED_PUBLISHING_PERMISSIONS = {
    "administration": "write",
    "contents": "write",
    "pages": "write",
}
_LOGGER = logging.getLogger(__name__)


class GitHubCredentialType(str, Enum):
    GITHUB_APP_USER = "github_app_user"
    OAUTH_APP = "oauth_app"


class GitHubConnectionState(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    INSTALL_REQUIRED = "install_required"
    AUTHORIZING = "authorizing"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    GITHUB_UNAVAILABLE = "github_unavailable"
    ACTION_REQUIRED = "action_required"

    # Compatibility aliases for callers migrating to the rebuilt state model.
    CHECKING = CONNECTING
    AUTHENTICATED_INSUFFICIENT_PERMISSION = ACTION_REQUIRED
    CONNECTED_READY = CONNECTED
    UNAVAILABLE = GITHUB_UNAVAILABLE


class GitHubFailureKind(str, Enum):
    TEMPORARY = "temporary"
    ACTION_REQUIRED = "action_required"
    CANCELLED = "cancelled"
    FATAL = "fatal"


def _safe_details(value: object) -> str:
    text = _TOKEN_PATTERN.sub("[REDACTED]", str(value))
    return _BEARER_PATTERN.sub("Bearer [REDACTED]", text)[:1200]


class GitHubOperationError(RuntimeError):
    def __init__(
        self,
        code: str,
        user_message: str,
        *,
        technical_details: str = "",
        status: int | None = None,
        retry_after_seconds: float | None = None,
        rate_limit_reset_at: float | None = None,
    ) -> None:
        super().__init__(user_message)
        self.code = str(code).strip() or "github_error"
        self.user_message = str(user_message).strip()
        self.technical_details = _safe_details(technical_details)
        self.status = status
        self.retry_after_seconds = (
            max(0.0, float(retry_after_seconds))
            if retry_after_seconds is not None
            else None
        )
        self.rate_limit_reset_at = (
            max(0.0, float(rate_limit_reset_at))
            if rate_limit_reset_at is not None
            else None
        )


_TEMPORARY_ERROR_CODES = frozenset(
    {
        "github_unavailable",
        "network",
        "rate_limit",
        "server_error",
    }
)
_ACTION_REQUIRED_ERROR_CODES = frozenset(
    {
        "app_not_configured",
        "app_permissions_not_configured",
        "authentication",
        "authorization_revoked",
        "credential_model_mismatch",
        "installation_permissions",
        "installation_required",
        "installation_timeout",
        "oauth_scope_missing",
        "permission",
        "repository_access",
        "repository_owner_mismatch",
    }
)


def classify_github_error(error: GitHubOperationError) -> GitHubFailureKind:
    if error.code in {"authorization_cancelled", "publication_cancelled"}:
        return GitHubFailureKind.CANCELLED
    if error.code in _TEMPORARY_ERROR_CODES or (
        error.status is not None and error.status >= 500
    ):
        return GitHubFailureKind.TEMPORARY
    if error.code in _ACTION_REQUIRED_ERROR_CODES:
        return GitHubFailureKind.ACTION_REQUIRED
    return GitHubFailureKind.FATAL


@dataclass(frozen=True)
class GitHubAccount:
    login: str
    account_id: int
    avatar_url: str = ""


@dataclass(frozen=True)
class GitHubToken:
    access_token: str
    refresh_token: str = ""
    expires_at: float = 0.0
    refresh_token_expires_at: float = 0.0
    credential_type: GitHubCredentialType = GitHubCredentialType.GITHUB_APP_USER
    granted_scopes: tuple[str, ...] = ()

    def access_expired(self, *, now: float | None = None) -> bool:
        return bool(self.expires_at) and (now or time.time()) >= self.expires_at - 60

    def refresh_expired(self, *, now: float | None = None) -> bool:
        return bool(self.refresh_token_expires_at) and (
            now or time.time()
        ) >= self.refresh_token_expires_at - 60


@dataclass(frozen=True)
class GitHubSession:
    account: GitHubAccount
    token: GitHubToken
    installation_id: int = 0
    repository_owner: str = ""
    repository_name: str = ""
    repository_id: int = 0
    repository_selection: str = ""
    connection_version: int = 2


@dataclass(frozen=True)
class GitHubDeviceAuthorization:
    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int


@dataclass(frozen=True)
class StoredGitHubCredential:
    account: GitHubAccount
    token: GitHubToken
    installation_id: int = 0
    repository_owner: str = ""
    repository_name: str = ""
    repository_id: int = 0
    repository_selection: str = ""
    connection_version: int = 2

    def session(self) -> GitHubSession:
        return GitHubSession(
            account=self.account,
            token=self.token,
            installation_id=self.installation_id,
            repository_owner=self.repository_owner,
            repository_name=self.repository_name,
            repository_id=self.repository_id,
            repository_selection=self.repository_selection,
            connection_version=self.connection_version,
        )


@dataclass(frozen=True)
class GitHubPublishingAccess:
    ready: bool
    code: str
    setup_url: str = ""
    message: str = ""
    installation_id: int = 0
    repository_selection: str = ""
    permissions: tuple[tuple[str, str], ...] = ()
    credential_type: GitHubCredentialType = GitHubCredentialType.GITHUB_APP_USER
    target_owner: str = ""
    target_repository: str = ""
    repository_id: int = 0


@dataclass(frozen=True)
class GitHubConnectionSnapshot:
    state: GitHubConnectionState
    session: GitHubSession | None = None
    access: GitHubPublishingAccess | None = None
    error_code: str = ""
    message: str = ""
    retry_attempt: int = 0
    retry_after_seconds: float = 0.0
    generation: int = 0

    @property
    def account(self) -> GitHubAccount | None:
        return self.session.account if self.session is not None else None


class GitHubAPI:
    def __init__(
        self,
        access_token: str = "",
        *,
        opener: Callable[..., object] = urllib.request.urlopen,
        timeout: float = 30.0,
    ) -> None:
        self.access_token = str(access_token).strip()
        self.opener = opener
        self.timeout = max(1.0, float(timeout))

    def request(
        self,
        method: str,
        path_or_url: str,
        payload: Mapping[str, object] | None = None,
        *,
        expected: tuple[int, ...] = (200,),
        authenticate: bool = True,
        form: bool = False,
    ) -> dict:
        url = (
            path_or_url
            if str(path_or_url).startswith("https://")
            else f"{GITHUB_API_URL}/{str(path_or_url).lstrip('/')}"
        )
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": f"{APPLICATION_NAME}/GitHub-Publisher",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
        }
        if authenticate:
            if not self.access_token:
                raise GitHubOperationError(
                    "authentication",
                    "GitHub sign-in is required.",
                )
            headers["Authorization"] = f"Bearer {self.access_token}"
        data = None
        if payload is not None:
            if form:
                data = urllib.parse.urlencode(payload).encode("utf-8")
                headers["Content-Type"] = "application/x-www-form-urlencoded"
            else:
                data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
                headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            url,
            data=data,
            headers=headers,
            method=str(method).upper(),
        )
        try:
            with self.opener(request, timeout=self.timeout) as response:
                response_status = getattr(response, "status", None)
                status = int(
                    response_status
                    if response_status is not None
                    else response.getcode()
                )
                raw = response.read()
        except urllib.error.HTTPError as error:
            raw = error.read()
            self._raise_http_error(error.code, raw, error.headers)
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise GitHubOperationError(
                "network",
                "GitHub could not be reached. Your letter is still saved locally.",
                technical_details=f"{type(error).__name__}: {error}",
            ) from error
        if status not in expected:
            self._raise_http_error(status, raw, {})
        if not raw:
            return {}
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise GitHubOperationError(
                "invalid_response",
                "GitHub returned an invalid response. Please try again.",
                technical_details=f"HTTP {status}: response was not JSON",
                status=status,
            ) from error
        if not isinstance(value, dict):
            raise GitHubOperationError(
                "invalid_response",
                "GitHub returned an invalid response. Please try again.",
                technical_details=f"HTTP {status}: expected a JSON object",
                status=status,
            )
        return value

    @staticmethod
    def _raise_http_error(status: int, raw: bytes, headers: object) -> None:
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeError, json.JSONDecodeError):
            body = {}
        message = str(body.get("message", "")).strip() if isinstance(body, dict) else ""
        detail = f"GitHub HTTP {status}"
        if message:
            detail += f": {message}"
        retry_after = ""
        retry_after_seconds: float | None = None
        rate_limit_reset_at: float | None = None
        try:
            retry_after = str(headers.get("Retry-After", "")).strip()
        except AttributeError:
            pass
        if retry_after:
            try:
                retry_after_seconds = max(0.0, float(retry_after))
            except ValueError:
                try:
                    retry_at = parsedate_to_datetime(retry_after)
                    if retry_at.tzinfo is None:
                        retry_at = retry_at.replace(tzinfo=timezone.utc)
                    retry_after_seconds = max(
                        0.0,
                        (retry_at - datetime.now(timezone.utc)).total_seconds(),
                    )
                except (TypeError, ValueError, OverflowError):
                    retry_after_seconds = None
        try:
            reset_value = str(headers.get("X-RateLimit-Reset", "")).strip()
        except AttributeError:
            reset_value = ""
        if reset_value:
            try:
                rate_limit_reset_at = max(0.0, float(reset_value))
            except ValueError:
                rate_limit_reset_at = None
        if status == 401:
            code = "authentication"
            user = "GitHub sign-in has expired. Please sign in again."
        elif status in {403, 429} and (
            status == 429
            or "rate limit" in message.casefold()
            or bool(retry_after)
            or bool(reset_value)
        ):
            code = "rate_limit"
            user = "GitHub is receiving too many requests. Please try publishing later."
            if retry_after_seconds is None and rate_limit_reset_at is None:
                retry_after_seconds = 60.0
        elif status == 403:
            code = "permission"
            user = "Letter Smith does not have permission to publish this letter."
        elif status == 404:
            code = "not_found"
            user = "The requested GitHub publishing resource was not found."
        elif status == 409:
            code = "conflict"
            user = "GitHub could not update the published letter because it changed elsewhere."
        elif status == 422:
            code = "request_rejected"
            user = "GitHub rejected the publishing request."
        else:
            code = "github_unavailable"
            user = "GitHub could not complete the request. Your letter is still saved locally."
        if retry_after:
            detail += f"; retry-after={retry_after}"
        try:
            accepted_permissions = str(
                headers.get("X-Accepted-GitHub-Permissions", "")
            ).strip()
        except AttributeError:
            accepted_permissions = ""
        if accepted_permissions:
            detail += f"; required-permissions={accepted_permissions}"
        raise GitHubOperationError(
            code,
            user,
            technical_details=detail,
            status=status,
            retry_after_seconds=retry_after_seconds,
            rate_limit_reset_at=rate_limit_reset_at,
        )


class GitHubCredentialStore:
    SERVICE_NAME = f"{PUBLISHER_NAME} {APPLICATION_NAME} GitHub"
    ACCOUNT_NAME = "github-user-authorization"

    def __init__(self, *, keyring_module=keyring) -> None:
        self.keyring = keyring_module

    def load(self) -> StoredGitHubCredential | None:
        try:
            raw = self.keyring.get_password(self.SERVICE_NAME, self.ACCOUNT_NAME)
        except KeyringError as error:
            raise GitHubOperationError(
                "credential_store",
                "GitHub sign-in could not be read from Windows secure storage.",
                technical_details=f"{type(error).__name__}: {error}",
            ) from error
        if not raw:
            _LOGGER.info("No stored GitHub credential was found.")
            return None
        try:
            value = json.loads(raw)
            token = GitHubToken(
                access_token=str(value["access_token"]),
                refresh_token=str(value.get("refresh_token", "")),
                expires_at=float(value.get("expires_at", 0.0) or 0.0),
                refresh_token_expires_at=float(
                    value.get("refresh_token_expires_at", 0.0) or 0.0
                ),
                credential_type=GitHubCredentialType(
                    str(
                        value.get(
                            "credential_type",
                            GitHubCredentialType.GITHUB_APP_USER.value,
                        )
                    )
                ),
                granted_scopes=tuple(
                    sorted(
                        {
                            str(scope).strip().casefold()
                            for scope in value.get("granted_scopes", ())
                            if str(scope).strip()
                        }
                    )
                ),
            )
            account = GitHubAccount(
                login=str(value["login"]),
                account_id=int(value["account_id"]),
                avatar_url=str(value.get("avatar_url", "")),
            )
            if not token.access_token or not account.login or account.account_id <= 0:
                raise ValueError("stored GitHub authorization is incomplete")
            credential = StoredGitHubCredential(
                account=account,
                token=token,
                installation_id=max(0, int(value.get("installation_id", 0) or 0)),
                repository_owner=str(value.get("repository_owner", "")).strip(),
                repository_name=str(value.get("repository_name", "")).strip(),
                repository_id=max(0, int(value.get("repository_id", 0) or 0)),
                repository_selection=str(
                    value.get("repository_selection", "")
                ).strip().casefold(),
                connection_version=max(
                    1,
                    int(value.get("connection_version", 1) or 1),
                ),
            )
            _LOGGER.info(
                "Stored GitHub credential loaded: account=%s account_id=%s",
                account.login,
                account.account_id,
            )
            return credential
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            self.clear()
            return None

    def save(self, session: GitHubSession) -> None:
        payload = json.dumps(
            {
                "access_token": session.token.access_token,
                "refresh_token": session.token.refresh_token,
                "expires_at": session.token.expires_at,
                "refresh_token_expires_at": session.token.refresh_token_expires_at,
                "credential_type": session.token.credential_type.value,
                "granted_scopes": list(session.token.granted_scopes),
                "login": session.account.login,
                "account_id": session.account.account_id,
                "avatar_url": session.account.avatar_url,
                "installation_id": session.installation_id,
                "repository_owner": session.repository_owner,
                "repository_name": session.repository_name,
                "repository_id": session.repository_id,
                "repository_selection": session.repository_selection,
                "connection_version": session.connection_version,
            },
            ensure_ascii=True,
            separators=(",", ":"),
        )
        try:
            self.keyring.set_password(self.SERVICE_NAME, self.ACCOUNT_NAME, payload)
        except KeyringError as error:
            raise GitHubOperationError(
                "credential_store",
                "GitHub sign-in could not be saved in Windows secure storage.",
                technical_details=f"{type(error).__name__}: {error}",
            ) from error
        _LOGGER.info(
            "GitHub credential persisted: account=%s account_id=%s",
            session.account.login,
            session.account.account_id,
        )

    def clear(self) -> None:
        try:
            self.keyring.delete_password(self.SERVICE_NAME, self.ACCOUNT_NAME)
        except PasswordDeleteError:
            return
        except KeyringError as error:
            raise GitHubOperationError(
                "credential_store",
                "GitHub sign-out could not clear Windows secure storage.",
                technical_details=f"{type(error).__name__}: {error}",
            ) from error
        _LOGGER.info("Stored GitHub credential cleared.")


class GitHubAuthenticator:
    def __init__(
        self,
        configuration: GitHubApplicationConfiguration | None = None,
        *,
        credential_store: GitHubCredentialStore | None = None,
        api: GitHubAPI | None = None,
        authorized_api_factory: Callable[[str], GitHubAPI] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.configuration = configuration or github_application_configuration()
        self.credential_store = credential_store or GitHubCredentialStore()
        self.api = api or GitHubAPI()
        self.authorized_api_factory = (
            authorized_api_factory or (lambda token: GitHubAPI(token))
        )
        self.sleeper = sleeper
        self.now = now

    def begin_device_authorization(self) -> GitHubDeviceAuthorization:
        _LOGGER.info(
            "[GitHub Auth] Requesting device authorization: model=%s",
            self.configuration.credential_model,
        )
        try:
            self.configuration.require_configured()
        except RuntimeError as error:
            raise GitHubOperationError(
                "app_not_configured",
                str(error),
            ) from error
        payload = {"client_id": self.configuration.client_id}
        if self.configuration.credential_model == GitHubCredentialType.OAUTH_APP.value:
            payload["scope"] = " ".join(GITHUB_OAUTH_REQUIRED_SCOPES)
        response = self.api.request(
            "POST",
            GITHUB_DEVICE_CODE_URL,
            payload,
            authenticate=False,
            form=True,
        )
        try:
            authorization = GitHubDeviceAuthorization(
                device_code=str(response["device_code"]),
                user_code=str(response["user_code"]),
                verification_uri=str(response["verification_uri"]),
                expires_in=int(response["expires_in"]),
                interval=max(1, int(response["interval"])),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise GitHubOperationError(
                "invalid_response",
                "GitHub did not return a usable sign-in code.",
            ) from error
        if (
            not authorization.device_code
            or not authorization.user_code
            or not authorization.verification_uri.startswith("https://github.com/")
            or authorization.expires_in <= 0
        ):
            raise GitHubOperationError(
                "invalid_response",
                "GitHub did not return a usable sign-in code.",
            )
        _LOGGER.info(
            "GitHub device authorization created: expires_in=%s interval=%s",
            authorization.expires_in,
            authorization.interval,
        )
        return authorization

    def poll_device_authorization(
        self,
        authorization: GitHubDeviceAuthorization,
        *,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> GitHubSession:
        _LOGGER.info("Polling GitHub for device authorization completion.")
        interval = authorization.interval
        elapsed = 0
        while elapsed < authorization.expires_in:
            self._wait(interval, cancelled)
            elapsed += interval
            response = self.api.request(
                "POST",
                GITHUB_TOKEN_URL,
                {
                    "client_id": self.configuration.client_id,
                    "device_code": authorization.device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
                authenticate=False,
                form=True,
            )
            error_code = str(response.get("error", "")).strip()
            if not error_code:
                session = self._session_from_token_response(response)
                _LOGGER.info(
                    "GitHub device authorization succeeded: account=%s account_id=%s",
                    session.account.login,
                    session.account.account_id,
                )
                return session
            if error_code == "authorization_pending":
                continue
            if error_code == "slow_down":
                try:
                    interval = max(interval + 5, int(response.get("interval", 0)))
                except (TypeError, ValueError):
                    interval += 5
                continue
            messages = {
                "expired_token": "The GitHub sign-in code expired. Please try again.",
                "access_denied": "GitHub sign-in was canceled.",
                "incorrect_client_credentials": (
                    "GitHub publishing is not configured correctly in this Letter Smith build."
                ),
                "incorrect_device_code": "The GitHub sign-in code is no longer valid.",
                "device_flow_disabled": (
                    "GitHub Device Flow is not enabled for the Letter Smith GitHub App."
                ),
            }
            raise GitHubOperationError(
                error_code,
                messages.get(error_code, "GitHub sign-in could not be completed."),
                technical_details=str(response.get("error_description", error_code)),
            )
        raise GitHubOperationError(
            "expired_token",
            "The GitHub sign-in code expired. Please try again.",
        )

    def stored_session(self) -> GitHubSession | None:
        stored = self.credential_store.load()
        return stored.session() if stored is not None else None

    def validate_stored(self, *, force_refresh: bool = False) -> GitHubSession | None:
        _LOGGER.info("Validating stored GitHub authentication state.")
        stored = self.credential_store.load()
        if stored is None:
            return None
        _LOGGER.info("[GitHub Auth] Existing credential found")
        token = stored.token
        if force_refresh or token.access_expired(now=self.now()):
            if not token.refresh_token or token.refresh_expired(now=self.now()):
                self.credential_store.clear()
                raise GitHubOperationError(
                    "authorization_revoked",
                    "Letter Smith's GitHub authorization must be restored.",
                )
            try:
                token = self._refresh(token)
            except GitHubOperationError as error:
                if classify_github_error(error) == GitHubFailureKind.ACTION_REQUIRED:
                    self.credential_store.clear()
                raise
        try:
            session = self._validated_session(
                token,
                save=True,
                connection=stored,
            )
            _LOGGER.info(
                "Stored GitHub authentication is valid: account=%s account_id=%s",
                session.account.login,
                session.account.account_id,
            )
            return session
        except GitHubOperationError as error:
            if error.code == "authentication":
                self.credential_store.clear()
                raise GitHubOperationError(
                    "authorization_revoked",
                    "Letter Smith's GitHub authorization was removed. Reconnect GitHub.",
                    technical_details=error.technical_details,
                    status=error.status,
                ) from error
            raise

    def sign_out(self) -> None:
        self.credential_store.clear()

    def persist_connection(
        self,
        session: GitHubSession,
        access: GitHubPublishingAccess,
    ) -> GitHubSession:
        updated = replace(
            session,
            installation_id=max(0, int(access.installation_id)),
            repository_owner=str(access.target_owner).strip(),
            repository_name=str(access.target_repository).strip(),
            repository_id=max(0, int(access.repository_id)),
            repository_selection=str(access.repository_selection).strip().casefold(),
            connection_version=2,
        )
        if updated != session:
            self.credential_store.save(updated)
            _LOGGER.info(
                "[GitHub Auth] Stored installation connection: installation=%s "
                "repository=%s/%s",
                updated.installation_id,
                updated.repository_owner or "unknown",
                updated.repository_name or "unknown",
            )
        return updated

    def installation_present(self, session: GitHubSession) -> bool:
        return self._matching_installation(session) is not None

    def publishing_access(
        self,
        session: GitHubSession,
        *,
        log_details: bool = True,
    ) -> GitHubPublishingAccess:
        expected_type = GitHubCredentialType(self.configuration.credential_model)
        if session.token.credential_type != expected_type:
            raise GitHubOperationError(
                "credential_model_mismatch",
                "GitHub publishing is configured incorrectly in this Letter Smith build.",
                technical_details=(
                    f"expected={expected_type.value}; "
                    f"received={session.token.credential_type.value}"
                ),
            )
        if log_details:
            _LOGGER.info(
                "[GitHub Auth] Credential type: %s",
                session.token.credential_type.value,
            )
        if session.token.credential_type == GitHubCredentialType.OAUTH_APP:
            return self._oauth_publishing_access(session)
        return self._github_app_publishing_access(
            session,
            log_details=log_details,
        )

    def _github_app_publishing_access(
        self,
        session: GitHubSession,
        *,
        log_details: bool = True,
    ) -> GitHubPublishingAccess:
        repository = github_pages_repository_name(session.account.login)
        if log_details:
            _LOGGER.info(
                "Validating GitHub publishing access: account=%s repository=%s",
                session.account.login,
                repository,
            )
        installation = self._matching_installation(session)
        if installation is None:
            return GitHubPublishingAccess(
                ready=False,
                code="installation_required",
                setup_url=self.installation_url,
                message=(
                    "GitHub is connected. Approve Letter Smith for your account "
                    "to enable publishing."
                ),
                credential_type=session.token.credential_type,
                target_owner=session.account.login,
                target_repository=repository,
            )

        try:
            installation_id = int(installation.get("id", 0) or 0)
        except (TypeError, ValueError):
            installation_id = 0
        permissions_value = installation.get("permissions", {})
        permissions = (
            {
                str(name).strip().casefold(): str(level).strip().casefold()
                for name, level in permissions_value.items()
            }
            if isinstance(permissions_value, dict)
            else {}
        )
        permission_pairs = tuple(sorted(permissions.items()))
        if log_details:
            _LOGGER.info(
                "[GitHub Auth] Granted GitHub App permissions: %s",
                ",".join(f"{name}:{level}" for name, level in permission_pairs)
                or "none",
            )
            _LOGGER.info(
                "[GitHub Auth] Required GitHub App permissions: %s",
                ",".join(
                    f"{name}:{level}"
                    for name, level in sorted(_REQUIRED_PUBLISHING_PERMISSIONS.items())
                ),
            )
        setup_url = str(installation.get("html_url", "")).strip()
        if not setup_url:
            setup_url = self.installation_url
        missing_permissions = tuple(
            name
            for name, required_level in _REQUIRED_PUBLISHING_PERMISSIONS.items()
            if permissions.get(name) != required_level
        )
        selection = str(
            installation.get("repository_selection", "")
        ).strip().casefold()
        if missing_permissions:
            if log_details:
                _LOGGER.warning(
                    "GitHub App installation lacks publishing permissions: missing=%s",
                    ",".join(missing_permissions),
                )
            registration = self.api.request(
                "GET",
                f"/apps/{urllib.parse.quote(self.configuration.app_slug)}",
                authenticate=False,
            )
            registration_value = registration.get("permissions", {})
            registration_permissions = (
                {
                    str(name).strip().casefold(): str(level).strip().casefold()
                    for name, level in registration_value.items()
                }
                if isinstance(registration_value, dict)
                else {}
            )
            missing_registration_permissions = tuple(
                name
                for name, required_level in _REQUIRED_PUBLISHING_PERMISSIONS.items()
                if registration_permissions.get(name) != required_level
            )
            if missing_registration_permissions:
                if log_details:
                    _LOGGER.error(
                        "GitHub App registration lacks publishing permissions: missing=%s",
                        ",".join(missing_registration_permissions),
                    )
                return GitHubPublishingAccess(
                    ready=False,
                    code="app_permissions_not_configured",
                    message=(
                        "GitHub publishing is not configured correctly in this "
                        "Letter Smith build. The Letter Smith developer must enable "
                        "the required GitHub App permissions."
                    ),
                    installation_id=installation_id,
                    repository_selection=selection,
                    permissions=permission_pairs,
                    credential_type=session.token.credential_type,
                    target_owner=session.account.login,
                    target_repository=repository,
                )
            return GitHubPublishingAccess(
                ready=False,
                code="installation_permissions",
                setup_url=setup_url,
                message=(
                    "Letter Smith's GitHub publishing access is incomplete. "
                    "Open GitHub once to approve the access requested by Letter Smith."
                ),
                installation_id=installation_id,
                repository_selection=selection,
                permissions=permission_pairs,
                credential_type=session.token.credential_type,
                target_owner=session.account.login,
                target_repository=repository,
            )
        if selection == "all":
            return self._target_repository_access(
                session,
                setup_url=setup_url,
                installation_id=installation_id,
                repository_selection=selection,
                permissions=permission_pairs,
                allow_missing=True,
            )
        if installation_id <= 0:
            raise GitHubOperationError(
                "invalid_response",
                "GitHub returned invalid application-installation details.",
            )
        repositories = self.authorized_api_factory(
            session.token.access_token
        ).request(
            "GET",
            f"/user/installations/{installation_id}/repositories?per_page=100",
        ).get("repositories", [])
        if not isinstance(repositories, list):
            raise GitHubOperationError(
                "invalid_response",
                "GitHub returned invalid publishing-project access details.",
            )
        repository_name = repository.casefold()
        repository_access = any(
            isinstance(repository, dict)
            and str(repository.get("name", "")).strip().casefold()
            == repository_name
            for repository in repositories
        )
        if repository_access:
            _LOGGER.info(
                "GitHub publishing repository access validated: repository=%s",
                repository,
            )
            return self._target_repository_access(
                session,
                setup_url=setup_url,
                installation_id=installation_id,
                repository_selection=selection,
                permissions=permission_pairs,
                allow_missing=False,
            )
        api = self.authorized_api_factory(session.token.access_token)
        try:
            api.request(
                "GET",
                f"/repos/{urllib.parse.quote(session.account.login)}/"
                f"{urllib.parse.quote(repository)}",
            )
        except GitHubOperationError as error:
            if error.code == "not_found":
                _LOGGER.info(
                    "[GitHub Auth] Target repository access: PASS "
                    "(app-created repository will be added to the installation)"
                )
                return GitHubPublishingAccess(
                    ready=True,
                    code="ready",
                    setup_url=setup_url,
                    installation_id=installation_id,
                    repository_selection=selection,
                    permissions=permission_pairs,
                    credential_type=session.token.credential_type,
                    target_owner=session.account.login,
                    target_repository=repository,
                )
            raise
        _LOGGER.warning(
            "GitHub App installation cannot access publishing repository: "
            "selection=%s repository=%s",
            selection or "unknown",
            repository,
        )
        return GitHubPublishingAccess(
            ready=False,
            code="repository_access",
            setup_url=setup_url,
            message=(
                "GitHub is connected, but Letter Smith cannot access your "
                f"{repository} publishing project. In GitHub, allow all "
                f"repositories or add {repository}."
            ),
            installation_id=installation_id,
            repository_selection=selection,
            permissions=permission_pairs,
            credential_type=session.token.credential_type,
            target_owner=session.account.login,
            target_repository=repository,
        )

    def _oauth_publishing_access(
        self,
        session: GitHubSession,
    ) -> GitHubPublishingAccess:
        repository = github_pages_repository_name(session.account.login)
        granted = set(session.token.granted_scopes)
        required = set(GITHUB_OAUTH_REQUIRED_SCOPES)
        _LOGGER.info(
            "[GitHub Auth] Granted OAuth scopes: %s",
            ",".join(sorted(granted)) or "none",
        )
        _LOGGER.info(
            "[GitHub Auth] Required OAuth scopes: %s",
            ",".join(sorted(required)),
        )
        if not required.issubset(granted):
            return GitHubPublishingAccess(
                ready=False,
                code="oauth_scope_missing",
                message=(
                    "Letter Smith was authorized without publishing access. "
                    "Reconnect once to approve publishing."
                ),
                credential_type=session.token.credential_type,
                target_owner=session.account.login,
                target_repository=repository,
            )
        return self._target_repository_access(
            session,
            setup_url="",
            installation_id=0,
            repository_selection="oauth",
            permissions=(),
            allow_missing=True,
        )

    def _target_repository_access(
        self,
        session: GitHubSession,
        *,
        setup_url: str,
        installation_id: int,
        repository_selection: str,
        permissions: tuple[tuple[str, str], ...],
        allow_missing: bool,
    ) -> GitHubPublishingAccess:
        owner = session.account.login
        repository = github_pages_repository_name(owner)
        api = self.authorized_api_factory(session.token.access_token)
        try:
            metadata = api.request(
                "GET",
                f"/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(repository)}",
            )
        except GitHubOperationError as error:
            if error.code == "not_found" and allow_missing:
                _LOGGER.info(
                    "[GitHub Auth] Target repository access: PASS (create on first publish)"
                )
                return GitHubPublishingAccess(
                    ready=True,
                    code="ready",
                    setup_url=setup_url,
                    installation_id=installation_id,
                    repository_selection=repository_selection,
                    permissions=permissions,
                    credential_type=session.token.credential_type,
                    target_owner=owner,
                    target_repository=repository,
                )
            raise
        repository_owner = metadata.get("owner", {})
        if not isinstance(repository_owner, dict):
            repository_owner = {}
        actual_owner = str(repository_owner.get("login", owner)).strip()
        actual_name = str(metadata.get("name", "")).strip()
        try:
            repository_id = max(0, int(metadata.get("id", 0) or 0))
        except (TypeError, ValueError):
            repository_id = 0
        if (
            actual_owner.casefold() != owner.casefold()
            or actual_name.casefold() != repository.casefold()
        ):
            _LOGGER.warning("[GitHub Auth] Target repository access: FAIL")
            return GitHubPublishingAccess(
                ready=False,
                code="repository_owner_mismatch",
                message=(
                    "The existing Letter Smith publishing project belongs to a "
                    "different GitHub account."
                ),
                setup_url=setup_url,
                installation_id=installation_id,
                repository_selection=repository_selection,
                permissions=permissions,
                credential_type=session.token.credential_type,
                target_owner=owner,
                target_repository=repository,
                repository_id=repository_id,
            )
        _LOGGER.info("[GitHub Auth] Target repository access: PASS")
        _LOGGER.info("[GitHub Auth] Publishing authorization: PASS")
        return GitHubPublishingAccess(
            ready=True,
            code="ready",
            setup_url=setup_url,
            installation_id=installation_id,
            repository_selection=repository_selection,
            permissions=permissions,
            credential_type=session.token.credential_type,
            target_owner=owner,
            target_repository=repository,
            repository_id=repository_id,
        )

    def _matching_installation(self, session: GitHubSession) -> dict | None:
        response = self.authorized_api_factory(session.token.access_token).request(
            "GET",
            "/user/installations?per_page=100",
        )
        installations = response.get("installations", [])
        if not isinstance(installations, list):
            raise GitHubOperationError(
                "invalid_response",
                "GitHub returned invalid application-installation details.",
            )
        for installation in installations:
            if not isinstance(installation, dict):
                continue
            account = installation.get("account", {})
            if not isinstance(account, dict):
                account = {}
            try:
                account_id = int(account.get("id", 0) or 0)
            except (TypeError, ValueError):
                account_id = 0
            if (
                str(installation.get("app_slug", "")).casefold()
                == self.configuration.app_slug.casefold()
                and account_id == session.account.account_id
            ):
                return installation
        return None

    def wait_for_installation(
        self,
        session: GitHubSession,
        *,
        cancelled: Callable[[], bool] = lambda: False,
        timeout_seconds: int = 90,
    ) -> GitHubSession:
        elapsed = 0
        attempts = 0
        while elapsed <= timeout_seconds:
            if cancelled():
                raise GitHubOperationError(
                    "authorization_cancelled",
                    "GitHub sign-in was canceled.",
                )
            if self.installation_present(session):
                return session
            attempts += 1
            self._wait(2, cancelled)
            elapsed += 2
        _LOGGER.warning(
            "[GitHub Auth] Installation confirmation stopped: attempts=%s timeout=%ss",
            attempts,
            timeout_seconds,
        )
        raise GitHubOperationError(
            "installation_timeout",
            "GitHub setup was not completed in time. Please try publishing again.",
        )

    def wait_for_publishing_access(
        self,
        session: GitHubSession,
        *,
        cancelled: Callable[[], bool] = lambda: False,
        timeout_seconds: int = 90,
    ) -> GitHubSession:
        elapsed = 0
        attempts = 0
        _LOGGER.info(
            "[GitHub Auth] Finite installation confirmation started: timeout=%ss",
            timeout_seconds,
        )
        while elapsed <= timeout_seconds:
            if cancelled():
                raise GitHubOperationError(
                    "authorization_cancelled",
                    "GitHub sign-in was canceled.",
                )
            access = self.publishing_access(
                session,
                log_details=attempts == 0,
            )
            attempts += 1
            if access.ready:
                _LOGGER.info(
                    "[GitHub Auth] Installation confirmation succeeded: attempts=%s",
                    attempts,
                )
                return session
            if not access.setup_url:
                raise GitHubOperationError(
                    access.code or "publishing_access",
                    access.message or "GitHub publishing access is incomplete.",
                )
            self._wait(2, cancelled)
            elapsed += 2
        _LOGGER.warning(
            "[GitHub Auth] Installation confirmation stopped: attempts=%s timeout=%ss",
            attempts,
            timeout_seconds,
        )
        raise GitHubOperationError(
            "installation_timeout",
            "GitHub publishing access was not completed in time. Please try again.",
        )

    @property
    def installation_url(self) -> str:
        return self.configuration.installation_url

    def _session_from_token_response(self, response: Mapping[str, object]) -> GitHubSession:
        token = self._token_from_response(response)
        return self._validated_session(token, save=True)

    def _validated_session(
        self,
        token: GitHubToken,
        *,
        save: bool,
        connection: StoredGitHubCredential | None = None,
    ) -> GitHubSession:
        expected_type = GitHubCredentialType(self.configuration.credential_model)
        if token.credential_type != expected_type:
            raise GitHubOperationError(
                "credential_model_mismatch",
                "GitHub publishing is configured incorrectly in this Letter Smith build.",
                technical_details=(
                    f"expected={expected_type.value}; "
                    f"received={token.credential_type.value}"
                ),
            )
        response = self.authorized_api_factory(token.access_token).request(
            "GET",
            "/user",
        )
        try:
            account = GitHubAccount(
                login=str(response["login"]),
                account_id=int(response["id"]),
                avatar_url=str(response.get("avatar_url", "")),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise GitHubOperationError(
                "invalid_response",
                "GitHub did not return a usable account identity.",
            ) from error
        if not account.login or account.account_id <= 0:
            raise GitHubOperationError(
                "invalid_response",
                "GitHub did not return a usable account identity.",
            )
        session = GitHubSession(
            account=account,
            token=token,
            installation_id=(connection.installation_id if connection else 0),
            repository_owner=(connection.repository_owner if connection else ""),
            repository_name=(connection.repository_name if connection else ""),
            repository_id=(connection.repository_id if connection else 0),
            repository_selection=(
                connection.repository_selection if connection else ""
            ),
            connection_version=(connection.connection_version if connection else 2),
        )
        _LOGGER.info("[GitHub Auth] GET /user: 200")
        _LOGGER.info("[GitHub Auth] User: %s", account.login)
        if save:
            self.credential_store.save(session)
        return session

    def _refresh(self, token: GitHubToken) -> GitHubToken:
        response = self.api.request(
            "POST",
            GITHUB_TOKEN_URL,
            {
                "client_id": self.configuration.client_id,
                "grant_type": "refresh_token",
                "refresh_token": token.refresh_token,
            },
            authenticate=False,
            form=True,
        )
        error_code = str(response.get("error", "")).strip()
        if error_code:
            raise GitHubOperationError(
                "authorization_revoked",
                "Letter Smith's GitHub authorization must be restored.",
                technical_details=str(response.get("error_description", error_code)),
            )
        return self._token_from_response(response)

    def _token_from_response(self, response: Mapping[str, object]) -> GitHubToken:
        access_token = str(response.get("access_token", "")).strip()
        if not access_token:
            raise GitHubOperationError(
                "invalid_response",
                "GitHub did not return a usable authorization.",
            )
        now = self.now()
        try:
            expires_in = max(0, int(response.get("expires_in", 0) or 0))
            refresh_expires_in = max(
                0,
                int(response.get("refresh_token_expires_in", 0) or 0),
            )
        except (TypeError, ValueError) as error:
            raise GitHubOperationError(
                "invalid_response",
                "GitHub returned invalid authorization timing information.",
            ) from error
        configured_type = GitHubCredentialType(self.configuration.credential_model)
        detected_type = (
            GitHubCredentialType.GITHUB_APP_USER
            if access_token.startswith("ghu_")
            else GitHubCredentialType.OAUTH_APP
            if access_token.startswith("gho_")
            else configured_type
        )
        scopes = tuple(
            sorted(
                {
                    scope.strip().casefold()
                    for scope in str(response.get("scope", "")).replace(",", " ").split()
                    if scope.strip()
                }
            )
        )
        return GitHubToken(
            access_token=access_token,
            refresh_token=str(response.get("refresh_token", "")).strip(),
            expires_at=now + expires_in if expires_in else 0.0,
            refresh_token_expires_at=(
                now + refresh_expires_in if refresh_expires_in else 0.0
            ),
            credential_type=detected_type,
            granted_scopes=scopes,
        )

    def _wait(self, seconds: int, cancelled: Callable[[], bool]) -> None:
        remaining = max(0.0, float(seconds))
        while remaining > 0:
            if cancelled():
                raise GitHubOperationError(
                    "authorization_cancelled",
                    "GitHub sign-in was canceled.",
                )
            duration = min(0.25, remaining)
            self.sleeper(duration)
            remaining -= duration


class GitHubConnectionService:
    """Process-wide source of truth for GitHub identity and publish readiness."""

    _RETRY_DELAYS_SECONDS = (2.0, 5.0, 10.0, 30.0, 60.0, 300.0)

    def __init__(self, authenticator: GitHubAuthenticator | None = None) -> None:
        self.authenticator = authenticator or GitHubAuthenticator()
        self._snapshot = GitHubConnectionSnapshot(
            GitHubConnectionState.DISCONNECTED
        )
        self._state_lock = threading.RLock()
        self._operation_lock = threading.Lock()
        self._listeners: set[Callable[[GitHubConnectionSnapshot], None]] = set()
        self._generation = 0
        self._force_refresh_requested = False

    @property
    def snapshot(self) -> GitHubConnectionSnapshot:
        with self._state_lock:
            return self._snapshot

    def subscribe(
        self,
        listener: Callable[[GitHubConnectionSnapshot], None],
    ) -> None:
        with self._state_lock:
            self._listeners.add(listener)

    def unsubscribe(
        self,
        listener: Callable[[GitHubConnectionSnapshot], None],
    ) -> None:
        with self._state_lock:
            self._listeners.discard(listener)

    def _set_snapshot(
        self,
        snapshot: GitHubConnectionSnapshot,
        *,
        generation: int | None = None,
    ) -> GitHubConnectionSnapshot:
        with self._state_lock:
            if generation is not None and generation != self._generation:
                _LOGGER.info(
                    "[GitHub Auth] Ignored stale result: generation=%s current=%s",
                    generation,
                    self._generation,
                )
                return self._snapshot
            if generation is not None and snapshot.generation != generation:
                snapshot = replace(snapshot, generation=generation)
            self._snapshot = snapshot
            listeners = tuple(self._listeners)
        _LOGGER.info(
            "[GitHub Auth] State: %s generation=%s",
            snapshot.state.name,
            snapshot.generation,
        )
        for listener in listeners:
            try:
                listener(snapshot)
            except Exception:
                _LOGGER.exception("GitHub state listener failed.")
        return snapshot

    def _next_generation(self) -> int:
        with self._state_lock:
            self._generation += 1
            return self._generation

    def _wait_for_active_operation(self) -> GitHubConnectionSnapshot | None:
        if self._operation_lock.acquire(blocking=False):
            return None
        self._operation_lock.acquire()
        self._operation_lock.release()
        return self.snapshot

    def restore(self) -> GitHubConnectionSnapshot:
        waited = self._wait_for_active_operation()
        if waited is not None:
            return waited
        generation = self._next_generation()
        previous = self.snapshot
        fallback_session = previous.session
        fallback_access = previous.access
        try:
            if fallback_session is None:
                stored_session = getattr(self.authenticator, "stored_session", None)
                if callable(stored_session):
                    fallback_session = stored_session()
            self._set_snapshot(
                GitHubConnectionSnapshot(
                    GitHubConnectionState.CONNECTING,
                    session=fallback_session,
                    access=fallback_access,
                    retry_attempt=previous.retry_attempt,
                ),
                generation=generation,
            )
            force_refresh = self._force_refresh_requested
            if force_refresh:
                session = self.authenticator.validate_stored(force_refresh=True)
            else:
                session = self.authenticator.validate_stored()
            if session is None:
                self._force_refresh_requested = False
                return self._set_snapshot(
                    GitHubConnectionSnapshot(GitHubConnectionState.DISCONNECTED),
                    generation=generation,
                )
            snapshot = self._evaluate_session(session, generation=generation)
            if snapshot.state == GitHubConnectionState.CONNECTED:
                self._force_refresh_requested = False
            return snapshot
        except GitHubOperationError as error:
            return self._record_failure_locked(
                error,
                session=fallback_session,
                access=fallback_access,
                generation=generation,
            )
        finally:
            self._operation_lock.release()

    def authorize(
        self,
        *,
        authorization_ready: Callable[[GitHubDeviceAuthorization], None],
        publishing_access_required: Callable[[GitHubPublishingAccess], None],
        cancelled: Callable[[], bool] = lambda: False,
    ) -> GitHubConnectionSnapshot:
        waited = self._wait_for_active_operation()
        if waited is not None:
            return self._require_completed_authorization(waited)
        generation = self._next_generation()
        session: GitHubSession | None = None
        try:
            current = self.snapshot
            if current.state == GitHubConnectionState.CONNECTED:
                return current
            self._set_snapshot(
                GitHubConnectionSnapshot(GitHubConnectionState.AUTHORIZING),
                generation=generation,
            )
            authorization = self.authenticator.begin_device_authorization()
            authorization_ready(authorization)
            session = self.authenticator.poll_device_authorization(
                authorization,
                cancelled=cancelled,
            )
            snapshot = self._evaluate_session(session, generation=generation)
            if snapshot.state == GitHubConnectionState.CONNECTED:
                return snapshot
            return self._complete_access_locked(
                session,
                snapshot.access,
                publishing_access_required=publishing_access_required,
                cancelled=cancelled,
                generation=generation,
            )
        except GitHubOperationError as error:
            current = self.snapshot
            if current.state == GitHubConnectionState.AUTHORIZING:
                self._record_failure_locked(
                    error,
                    session=session,
                    access=current.access,
                    generation=generation,
                )
            raise
        finally:
            self._operation_lock.release()

    def complete_publishing_access(
        self,
        *,
        publishing_access_required: Callable[[GitHubPublishingAccess], None],
        cancelled: Callable[[], bool] = lambda: False,
    ) -> GitHubConnectionSnapshot:
        waited = self._wait_for_active_operation()
        if waited is not None:
            return self._require_completed_authorization(waited)
        generation = self._next_generation()
        try:
            snapshot = self.snapshot
            if snapshot.state == GitHubConnectionState.CONNECTED:
                return snapshot
            if snapshot.session is None or snapshot.access is None:
                raise GitHubOperationError(
                    "authentication",
                    "GitHub sign-in is required.",
                )
            self._set_snapshot(
                GitHubConnectionSnapshot(
                    GitHubConnectionState.AUTHORIZING,
                    session=snapshot.session,
                    access=snapshot.access,
                ),
                generation=generation,
            )
            return self._complete_access_locked(
                snapshot.session,
                snapshot.access,
                publishing_access_required=publishing_access_required,
                cancelled=cancelled,
                generation=generation,
            )
        finally:
            self._operation_lock.release()

    def _complete_access_locked(
        self,
        session: GitHubSession,
        access: GitHubPublishingAccess | None,
        *,
        publishing_access_required: Callable[[GitHubPublishingAccess], None],
        cancelled: Callable[[], bool],
        generation: int,
    ) -> GitHubConnectionSnapshot:
        if access is None or access.ready:
            return self._evaluate_session(session, generation=generation)
        if not access.setup_url:
            self._set_snapshot(
                GitHubConnectionSnapshot(
                    self._state_for_access(access),
                    session=session,
                    access=access,
                    error_code=access.code,
                    message=access.message,
                ),
                generation=generation,
            )
            raise GitHubOperationError(
                access.code or "publishing_access",
                access.message or "GitHub publishing access is incomplete.",
            )
        self._set_snapshot(
            GitHubConnectionSnapshot(
                GitHubConnectionState.AUTHORIZING,
                session=session,
                access=access,
            ),
            generation=generation,
        )
        publishing_access_required(access)
        try:
            session = self.authenticator.wait_for_publishing_access(
                session,
                cancelled=cancelled,
            )
            return self._evaluate_session(session, generation=generation)
        except GitHubOperationError as error:
            if classify_github_error(error) == GitHubFailureKind.TEMPORARY:
                self._record_failure_locked(
                    error,
                    session=session,
                    access=access,
                    generation=generation,
                )
            else:
                self._set_snapshot(
                    GitHubConnectionSnapshot(
                        self._state_for_access(access),
                        session=session,
                        access=access,
                        error_code=error.code,
                        message=error.user_message,
                    ),
                    generation=generation,
                )
            raise

    def _evaluate_session(
        self,
        session: GitHubSession,
        *,
        generation: int,
    ) -> GitHubConnectionSnapshot:
        access = self.authenticator.publishing_access(session)
        persist_connection = getattr(self.authenticator, "persist_connection", None)
        if callable(persist_connection) and access.installation_id:
            session = persist_connection(session, access)
        if access.ready:
            return self._set_snapshot(
                GitHubConnectionSnapshot(
                    GitHubConnectionState.CONNECTED,
                    session=session,
                    access=access,
                ),
                generation=generation,
            )
        return self._set_snapshot(
            GitHubConnectionSnapshot(
                self._state_for_access(access),
                session=session,
                access=access,
                error_code=access.code,
                message=access.message,
            ),
            generation=generation,
        )

    @staticmethod
    def _state_for_access(access: GitHubPublishingAccess) -> GitHubConnectionState:
        if access.code == "installation_required":
            return GitHubConnectionState.INSTALL_REQUIRED
        return GitHubConnectionState.ACTION_REQUIRED

    def _record_failure_locked(
        self,
        error: GitHubOperationError,
        *,
        session: GitHubSession | None,
        access: GitHubPublishingAccess | None,
        generation: int,
        force_temporary: bool = False,
    ) -> GitHubConnectionSnapshot:
        kind = (
            GitHubFailureKind.TEMPORARY
            if force_temporary
            else classify_github_error(error)
        )
        previous = self.snapshot
        if kind == GitHubFailureKind.TEMPORARY:
            if session is None:
                return self._set_snapshot(
                    GitHubConnectionSnapshot(
                        GitHubConnectionState.GITHUB_UNAVAILABLE,
                        error_code=error.code,
                        message=error.user_message,
                    ),
                    generation=generation,
                )
            attempt = previous.retry_attempt + 1 if previous.retry_attempt else 1
            delay = self._retry_delay(error, attempt)
            _LOGGER.warning(
                "[GitHub] Request failed: code=%s status=%s",
                error.code,
                error.status,
            )
            _LOGGER.info(
                "[GitHub] Retry %s scheduled in %.1fs",
                attempt,
                delay,
            )
            return self._set_snapshot(
                GitHubConnectionSnapshot(
                    GitHubConnectionState.RECONNECTING,
                    session=session,
                    access=access,
                    error_code=error.code,
                    message=(
                        "GitHub is temporarily unavailable. Reconnecting automatically…"
                    ),
                    retry_attempt=attempt,
                    retry_after_seconds=delay,
                ),
                generation=generation,
            )
        if kind == GitHubFailureKind.CANCELLED:
            state = self._state_for_access(access) if access is not None else (
                GitHubConnectionState.DISCONNECTED
            )
        elif kind == GitHubFailureKind.ACTION_REQUIRED:
            state = GitHubConnectionState.ACTION_REQUIRED
        else:
            state = GitHubConnectionState.GITHUB_UNAVAILABLE
        _LOGGER.warning(
            "[GitHub] Automatic retry stopped: code=%s state=%s",
            error.code,
            state.name,
        )
        return self._set_snapshot(
            GitHubConnectionSnapshot(
                state,
                session=session,
                access=access,
                error_code=error.code,
                message=error.user_message,
            ),
            generation=generation,
        )

    @classmethod
    def _retry_delay(cls, error: GitHubOperationError, attempt: int) -> float:
        index = min(max(1, int(attempt)) - 1, len(cls._RETRY_DELAYS_SECONDS) - 1)
        delay = cls._RETRY_DELAYS_SECONDS[index]
        if error.retry_after_seconds is not None:
            delay = max(delay, error.retry_after_seconds)
        if error.rate_limit_reset_at is not None:
            delay = max(delay, error.rate_limit_reset_at - time.time())
        return max(0.0, delay)

    @staticmethod
    def _require_completed_authorization(
        snapshot: GitHubConnectionSnapshot,
    ) -> GitHubConnectionSnapshot:
        if snapshot.state == GitHubConnectionState.CONNECTED:
            return snapshot
        raise GitHubOperationError(
            snapshot.error_code or "authorization_incomplete",
            snapshot.message or "GitHub authorization was not completed.",
        )

    def sign_out(self) -> GitHubConnectionSnapshot:
        with self._operation_lock:
            generation = self._next_generation()
            self.authenticator.sign_out()
            self._force_refresh_requested = False
            return self._set_snapshot(
                GitHubConnectionSnapshot(GitHubConnectionState.DISCONNECTED),
                generation=generation,
            )

    def invalidate_authentication(self) -> GitHubConnectionSnapshot:
        return self.sign_out()

    def record_operation_error(
        self,
        error: GitHubOperationError,
        session: GitHubSession,
        *,
        expected_generation: int | None = None,
    ) -> GitHubConnectionSnapshot:
        with self._operation_lock:
            if (
                expected_generation is not None
                and self.snapshot.generation != expected_generation
            ):
                _LOGGER.info(
                    "[GitHub] Ignored stale operation failure: generation=%s current=%s",
                    expected_generation,
                    self.snapshot.generation,
                )
                return self.snapshot
            generation = self._next_generation()
            force_temporary = bool(
                error.code == "authentication"
                and session.token.refresh_token
                and not session.token.refresh_expired()
            )
            if force_temporary:
                self._force_refresh_requested = True
            elif error.code == "authentication":
                try:
                    self.authenticator.sign_out()
                except GitHubOperationError:
                    pass
            return self._record_failure_locked(
                error,
                session=session,
                access=self.snapshot.access,
                generation=generation,
                force_temporary=force_temporary,
            )

    def record_operation_success(
        self,
        session: GitHubSession,
        *,
        expected_generation: int | None = None,
    ) -> GitHubConnectionSnapshot:
        with self._operation_lock:
            if (
                expected_generation is not None
                and self.snapshot.generation != expected_generation
            ):
                _LOGGER.info(
                    "[GitHub] Ignored stale operation success: generation=%s current=%s",
                    expected_generation,
                    self.snapshot.generation,
                )
                return self.snapshot
            generation = self._next_generation()
            current = self.snapshot
            self._force_refresh_requested = False
            return self._set_snapshot(
                GitHubConnectionSnapshot(
                    GitHubConnectionState.CONNECTED,
                    session=session,
                    access=current.access,
                ),
                generation=generation,
            )

    def authorized_api(self, session: GitHubSession) -> GitHubAPI:
        effective = session
        if session.token.access_expired():
            self._force_refresh_requested = True
            snapshot = self.restore()
            if snapshot.state != GitHubConnectionState.CONNECTED or snapshot.session is None:
                raise GitHubOperationError(
                    snapshot.error_code or "authentication",
                    snapshot.message or "GitHub publishing is temporarily unavailable.",
                )
            effective = snapshot.session
        return self.authenticator.authorized_api_factory(effective.token.access_token)


_CONNECTION_SERVICE: GitHubConnectionService | None = None
_CONNECTION_SERVICE_LOCK = threading.Lock()


def github_connection_service() -> GitHubConnectionService:
    global _CONNECTION_SERVICE
    with _CONNECTION_SERVICE_LOCK:
        if _CONNECTION_SERVICE is None:
            _CONNECTION_SERVICE = GitHubConnectionService()
        return _CONNECTION_SERVICE


__all__ = [
    "GitHubAPI",
    "GitHubAccount",
    "GitHubAuthenticator",
    "GitHubConnectionService",
    "GitHubConnectionSnapshot",
    "GitHubConnectionState",
    "GitHubCredentialStore",
    "GitHubCredentialType",
    "GitHubDeviceAuthorization",
    "GitHubFailureKind",
    "GitHubOperationError",
    "GitHubPublishingAccess",
    "GitHubSession",
    "GitHubToken",
    "StoredGitHubCredential",
    "classify_github_error",
    "github_connection_service",
]
