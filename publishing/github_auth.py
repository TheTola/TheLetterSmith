from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable, Mapping

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

from application_identity import APPLICATION_NAME, PUBLISHER_NAME
from publishing.github_config import (
    GITHUB_API_URL,
    GITHUB_API_VERSION,
    GITHUB_DEVICE_CODE_URL,
    GITHUB_TOKEN_URL,
    GitHubApplicationConfiguration,
    github_application_configuration,
)


_TOKEN_PATTERN = re.compile(r"\bgh[a-z]_[A-Za-z0-9]{16,}\b", re.IGNORECASE)
_BEARER_PATTERN = re.compile(r"(?i)\bBearer\s+[^\s,;\"'}\]]+")


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
    ) -> None:
        super().__init__(user_message)
        self.code = str(code).strip() or "github_error"
        self.user_message = str(user_message).strip()
        self.technical_details = _safe_details(technical_details)
        self.status = status


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
        try:
            retry_after = str(headers.get("Retry-After", "")).strip()
        except AttributeError:
            pass
        if status == 401:
            code = "authentication"
            user = "GitHub sign-in has expired. Please sign in again."
        elif status in {403, 429} and (
            status == 429
            or "rate limit" in message.casefold()
            or bool(retry_after)
        ):
            code = "rate_limit"
            user = "GitHub is receiving too many requests. Please try publishing later."
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
        raise GitHubOperationError(
            code,
            user,
            technical_details=detail,
            status=status,
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
            )
            account = GitHubAccount(
                login=str(value["login"]),
                account_id=int(value["account_id"]),
                avatar_url=str(value.get("avatar_url", "")),
            )
            if not token.access_token or not account.login or account.account_id <= 0:
                raise ValueError("stored GitHub authorization is incomplete")
            return StoredGitHubCredential(account=account, token=token)
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
                "login": session.account.login,
                "account_id": session.account.account_id,
                "avatar_url": session.account.avatar_url,
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
        try:
            self.configuration.require_configured()
        except RuntimeError as error:
            raise GitHubOperationError(
                "app_not_configured",
                str(error),
            ) from error
        response = self.api.request(
            "POST",
            GITHUB_DEVICE_CODE_URL,
            {"client_id": self.configuration.client_id},
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
        return authorization

    def poll_device_authorization(
        self,
        authorization: GitHubDeviceAuthorization,
        *,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> GitHubSession:
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
                return self._session_from_token_response(response)
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

    def validate_stored(self) -> GitHubSession | None:
        stored = self.credential_store.load()
        if stored is None:
            return None
        token = stored.token
        if token.access_expired(now=self.now()):
            if not token.refresh_token or token.refresh_expired(now=self.now()):
                self.credential_store.clear()
                return None
            try:
                token = self._refresh(token)
            except GitHubOperationError as error:
                if error.code == "authentication":
                    self.credential_store.clear()
                    return None
                raise
        try:
            return self._validated_session(token, save=True)
        except GitHubOperationError as error:
            if error.code == "authentication":
                self.credential_store.clear()
                return None
            raise

    def sign_out(self) -> None:
        self.credential_store.clear()

    def installation_present(self, session: GitHubSession) -> bool:
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
                return True
        return False

    def wait_for_installation(
        self,
        session: GitHubSession,
        *,
        cancelled: Callable[[], bool] = lambda: False,
        timeout_seconds: int = 600,
    ) -> GitHubSession:
        elapsed = 0
        while elapsed <= timeout_seconds:
            if cancelled():
                raise GitHubOperationError(
                    "authorization_cancelled",
                    "GitHub sign-in was canceled.",
                )
            if self.installation_present(session):
                return session
            self._wait(3, cancelled)
            elapsed += 3
        raise GitHubOperationError(
            "installation_timeout",
            "GitHub setup was not completed in time. Please try publishing again.",
        )

    @property
    def installation_url(self) -> str:
        return self.configuration.installation_url

    def _session_from_token_response(self, response: Mapping[str, object]) -> GitHubSession:
        token = self._token_from_response(response)
        return self._validated_session(token, save=True)

    def _validated_session(self, token: GitHubToken, *, save: bool) -> GitHubSession:
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
        session = GitHubSession(account=account, token=token)
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
                "authentication",
                "GitHub sign-in has expired. Please sign in again.",
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
        return GitHubToken(
            access_token=access_token,
            refresh_token=str(response.get("refresh_token", "")).strip(),
            expires_at=now + expires_in if expires_in else 0.0,
            refresh_token_expires_at=(
                now + refresh_expires_in if refresh_expires_in else 0.0
            ),
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


__all__ = [
    "GitHubAPI",
    "GitHubAccount",
    "GitHubAuthenticator",
    "GitHubCredentialStore",
    "GitHubDeviceAuthorization",
    "GitHubOperationError",
    "GitHubSession",
    "GitHubToken",
    "StoredGitHubCredential",
]
