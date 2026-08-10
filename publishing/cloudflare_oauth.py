from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import asdict, dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Mapping

from publishing.credentials import (
    _protect_for_current_windows_user,
    _unprotect_for_current_windows_user,
)
from transactional_io import atomic_write_bytes, atomic_write_json


AUTHORIZATION_ENDPOINT = "https://dash.cloudflare.com/oauth2/auth"
TOKEN_ENDPOINT = "https://dash.cloudflare.com/oauth2/token"
REVOKE_ENDPOINT = "https://dash.cloudflare.com/oauth2/revoke"
API_BASE_URL = "https://api.cloudflare.com/client/v4"
DEFAULT_REDIRECT_URI = "http://127.0.0.1:53682/oauth/callback"
DEFAULT_OAUTH_SCOPES = (
    "account-settings.read",
    "workers-r2.write",
    "offline_access",
)
OAUTH_CLIENT_ID_ENV = "LETTERSMITH_CLOUDFLARE_OAUTH_CLIENT_ID"

_TOKEN_SCHEMA_VERSION = 1
_CLIENT_SCHEMA_VERSION = 1


class CloudflareOAuthError(RuntimeError):
    def __init__(self, code: str, user_message: str, technical_details: str = "") -> None:
        super().__init__(user_message)
        self.code = code
        self.user_message = user_message
        self.technical_details = technical_details


@dataclass(frozen=True)
class CloudflareOAuthClientConfiguration:
    client_id: str
    redirect_uri: str = DEFAULT_REDIRECT_URI
    scopes: tuple[str, ...] = DEFAULT_OAUTH_SCOPES

    def validated(self) -> "CloudflareOAuthClientConfiguration":
        client_id = str(self.client_id).strip()
        if not client_id or len(client_id) > 256 or any(ch.isspace() for ch in client_id):
            raise ValueError("A valid Cloudflare OAuth Client ID is required.")
        parsed = urllib.parse.urlsplit(str(self.redirect_uri).strip())
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or parsed.port is None
            or parsed.path != "/oauth/callback"
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("The OAuth callback must use Letter Smith's local callback URL.")
        scopes = tuple(
            dict.fromkeys(str(scope).strip() for scope in self.scopes if str(scope).strip())
        )
        if not scopes or any(any(ch.isspace() for ch in scope) for scope in scopes):
            raise ValueError("Valid Cloudflare OAuth scopes are required.")
        return CloudflareOAuthClientConfiguration(
            client_id=client_id,
            redirect_uri=urllib.parse.urlunsplit(
                (parsed.scheme, parsed.netloc, parsed.path, "", "")
            ),
            scopes=scopes,
        )


def _default_client_configuration_path() -> Path:
    local_app_data = str(os.environ.get("LOCALAPPDATA", "")).strip()
    if not local_app_data:
        raise OSError("Windows Local AppData is unavailable.")
    return Path(local_app_data) / "LetterSmith" / "cloudflare-oauth-client.json"


class CloudflareOAuthClientConfigurationStore:
    """Store the public OAuth Client ID used by this Letter Smith installation."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = (
            Path(path).resolve()
            if path is not None
            else _default_client_configuration_path()
        )

    def load(self) -> CloudflareOAuthClientConfiguration | None:
        environment_client_id = str(os.environ.get(OAUTH_CLIENT_ID_ENV, "")).strip()
        if environment_client_id:
            try:
                return CloudflareOAuthClientConfiguration(environment_client_id).validated()
            except ValueError:
                return None

        bundled_path = Path(__file__).with_name("cloudflare_oauth_client.json")
        for path in (self.path, bundled_path):
            if not path.is_file():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8-sig"))
                if not isinstance(payload, Mapping):
                    continue
                stored_scopes = payload.get("scopes", DEFAULT_OAUTH_SCOPES)
                if not isinstance(stored_scopes, (list, tuple)):
                    continue
                configuration = CloudflareOAuthClientConfiguration(
                    client_id=str(payload.get("client_id", "")),
                    redirect_uri=str(
                        payload.get("redirect_uri", DEFAULT_REDIRECT_URI)
                    ),
                    scopes=tuple(str(scope) for scope in stored_scopes),
                ).validated()
                return configuration
            except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
                continue
        return None

    def save(self, configuration: CloudflareOAuthClientConfiguration) -> None:
        valid = configuration.validated()
        atomic_write_json(
            self.path,
            {
                "schema_version": _CLIENT_SCHEMA_VERSION,
                "client_id": valid.client_id,
                "redirect_uri": valid.redirect_uri,
                "scopes": list(valid.scopes),
            },
        )


@dataclass(frozen=True)
class CloudflareOAuthToken:
    access_token: str
    refresh_token: str = ""
    expires_at: float = 0.0
    token_type: str = "Bearer"
    scope: str = ""

    def validated(self) -> "CloudflareOAuthToken":
        access_token = str(self.access_token).strip()
        refresh_token = str(self.refresh_token).strip()
        token_type = str(self.token_type or "Bearer").strip()
        if not access_token:
            raise ValueError("The Cloudflare OAuth access token is missing.")
        if token_type.casefold() != "bearer":
            raise ValueError("Cloudflare returned an unsupported OAuth token type.")
        return CloudflareOAuthToken(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=max(0.0, float(self.expires_at or 0.0)),
            token_type="Bearer",
            scope=str(self.scope or "").strip(),
        )


def _default_token_path() -> Path:
    local_app_data = str(os.environ.get("LOCALAPPDATA", "")).strip()
    if not local_app_data:
        raise OSError("Windows Local AppData is unavailable.")
    return (
        Path(local_app_data)
        / "LetterSmith"
        / "credentials"
        / "cloudflare-oauth.dpapi"
    )


class CloudflareOAuthTokenStore:
    """Store OAuth access and refresh tokens with Windows DPAPI."""

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        protect: Callable[[bytes], bytes] | None = None,
        unprotect: Callable[[bytes], bytes] | None = None,
    ) -> None:
        self.path = Path(path).resolve() if path is not None else _default_token_path()
        self._protect = protect or _protect_for_current_windows_user
        self._unprotect = unprotect or _unprotect_for_current_windows_user

    def exists(self) -> bool:
        return self.path.is_file()

    def save(self, token: CloudflareOAuthToken) -> None:
        valid = token.validated()
        clear = json.dumps(
            {"schema_version": _TOKEN_SCHEMA_VERSION, **asdict(valid)},
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        atomic_write_bytes(self.path, self._protect(clear))

    def load(self) -> CloudflareOAuthToken | None:
        if not self.path.is_file():
            return None
        try:
            payload = json.loads(self._unprotect(self.path.read_bytes()).decode("utf-8"))
            if not isinstance(payload, Mapping):
                return None
            if int(payload.get("schema_version", 0)) != _TOKEN_SCHEMA_VERSION:
                return None
            return CloudflareOAuthToken(
                access_token=str(payload.get("access_token", "")),
                refresh_token=str(payload.get("refresh_token", "")),
                expires_at=float(payload.get("expires_at", 0.0) or 0.0),
                token_type=str(payload.get("token_type", "Bearer")),
                scope=str(payload.get("scope", "")),
            ).validated()
        except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
            return None

    def clear(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            return


@dataclass(frozen=True)
class CloudflareAccount:
    account_id: str
    name: str


@dataclass(frozen=True)
class CloudflareAuthorization:
    accounts: tuple[CloudflareAccount, ...]


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _authorization_url(
    configuration: CloudflareOAuthClientConfiguration,
    *,
    state: str,
    challenge: str,
) -> str:
    query = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": configuration.client_id,
            "redirect_uri": configuration.redirect_uri,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "scope": " ".join(configuration.scopes),
        }
    )
    return f"{AUTHORIZATION_ENDPOINT}?{query}"


def _form_request(url: str, fields: Mapping[str, str], *, timeout: float = 20.0) -> dict:
    request = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(fields).encode("ascii"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "LetterSmith/1",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        details = error.read().decode("utf-8", errors="replace")[:2000]
        raise CloudflareOAuthError(
            "token_exchange",
            "Cloudflare could not complete the secure login. Try connecting again.",
            f"http_status={error.code}; response={details}",
        ) from error
    except (OSError, urllib.error.URLError, UnicodeError, json.JSONDecodeError) as error:
        raise CloudflareOAuthError(
            "network",
            "Letter Smith could not reach Cloudflare. Check the internet connection and try again.",
            f"type={type(error).__name__}; details={error}",
        ) from error
    if not isinstance(payload, dict):
        raise CloudflareOAuthError(
            "token_exchange",
            "Cloudflare returned an invalid login response. Try connecting again.",
        )
    return payload


class _CallbackState:
    def __init__(self, expected_state: str, expected_path: str) -> None:
        self.expected_state = expected_state
        self.expected_path = expected_path
        self.code = ""
        self.error = ""
        self.completed = False


class _OAuthCallbackServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def _callback_handler(state: _CallbackState):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            parsed = urllib.parse.urlsplit(self.path)
            if parsed.path != state.expected_path:
                self.send_error(404)
                return
            query = urllib.parse.parse_qs(parsed.query)
            returned_state = str(query.get("state", [""])[0])
            if not secrets.compare_digest(returned_state, state.expected_state):
                title = "Connection rejected"
                message = (
                    "This login page belongs to an older connection attempt. "
                    "Return to the newest Cloudflare login page to continue."
                )
            else:
                state.error = str(query.get("error_description", query.get("error", [""]))[0])
                state.code = str(query.get("code", [""])[0])
                if state.error or not state.code:
                    title = "Connection canceled"
                    message = "Return to Letter Smith to try again."
                else:
                    title = "Cloudflare authorization received"
                    message = (
                        "Return to Letter Smith while it verifies R2 storage. "
                        "You can safely close this page, but R2 setup is complete only "
                        "after Letter Smith confirms it."
                    )
                state.completed = True
            body = (
                "<!doctype html><html><head><meta charset='utf-8'>"
                f"<title>{title}</title><style>body{{background:#0d151c;color:#eaf9ff;"
                "font:16px Segoe UI,sans-serif;display:grid;place-items:center;"
                "min-height:100vh;margin:0}.card{max-width:540px;padding:32px;"
                "border:1px solid #31505e;border-radius:12px;background:#111f28}"
                "h1{color:#00d4f4;font-size:24px}</style></head><body><main class='card'>"
                f"<h1>{title}</h1><p>{message}</p></main></body></html>"
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    return Handler


class CloudflareOAuthSession:
    def __init__(
        self,
        configuration: CloudflareOAuthClientConfiguration,
        token_store: CloudflareOAuthTokenStore | None = None,
        *,
        browser_opener: Callable[[str], bool] | None = None,
    ) -> None:
        self.configuration = configuration.validated()
        self.token_store = token_store or CloudflareOAuthTokenStore()
        self.browser_opener = browser_opener or webbrowser.open

    def authorize(
        self,
        *,
        timeout_seconds: float = 300.0,
        cancelled: Callable[[], bool] | None = None,
    ) -> CloudflareAuthorization:
        verifier, challenge = _pkce_pair()
        state_value = secrets.token_urlsafe(32)
        parsed_redirect = urllib.parse.urlsplit(self.configuration.redirect_uri)
        callback = _CallbackState(state_value, parsed_redirect.path)
        try:
            server = _OAuthCallbackServer(
                (str(parsed_redirect.hostname), int(parsed_redirect.port or 0)),
                _callback_handler(callback),
            )
        except OSError as error:
            raise CloudflareOAuthError(
                "callback_unavailable",
                "Letter Smith could not start its secure login callback. Close other Letter Smith windows and try again.",
                f"callback={self.configuration.redirect_uri}; details={error}",
            ) from error
        server.timeout = 0.25
        try:
            authorization_url = _authorization_url(
                self.configuration,
                state=state_value,
                challenge=challenge,
            )
            if not self.browser_opener(authorization_url):
                raise CloudflareOAuthError(
                    "browser",
                    "The Cloudflare login page could not be opened in the default browser.",
                    f"authorization_url={authorization_url}",
                )
            deadline = time.monotonic() + max(30.0, float(timeout_seconds))
            while not callback.completed and time.monotonic() < deadline:
                if cancelled is not None and cancelled():
                    raise CloudflareOAuthError(
                        "canceled",
                        "Cloudflare connection was canceled.",
                    )
                server.handle_request()
        finally:
            server.server_close()

        if not callback.completed:
            raise CloudflareOAuthError(
                "timeout",
                "Cloudflare login timed out. Select Connect Cloudflare to try again.",
            )
        if callback.error:
            raise CloudflareOAuthError(
                "authorization_denied",
                "Cloudflare did not authorize Letter Smith. Select Connect Cloudflare to try again.",
                callback.error,
            )

        payload = _form_request(
            TOKEN_ENDPOINT,
            {
                "grant_type": "authorization_code",
                "client_id": self.configuration.client_id,
                "code": callback.code,
                "redirect_uri": self.configuration.redirect_uri,
                "code_verifier": verifier,
            },
        )
        token = self._token_from_payload(payload)
        self.token_store.save(token)
        return CloudflareAuthorization(self.accounts())

    @staticmethod
    def _token_from_payload(
        payload: Mapping[str, object],
        *,
        previous_refresh_token: str = "",
    ) -> CloudflareOAuthToken:
        try:
            expires_in = max(0.0, float(payload.get("expires_in", 0.0) or 0.0))
            return CloudflareOAuthToken(
                access_token=str(payload.get("access_token", "")),
                refresh_token=str(
                    payload.get("refresh_token", previous_refresh_token) or previous_refresh_token
                ),
                expires_at=time.time() + expires_in if expires_in else 0.0,
                token_type=str(payload.get("token_type", "Bearer")),
                scope=str(payload.get("scope", "")),
            ).validated()
        except (TypeError, ValueError) as error:
            raise CloudflareOAuthError(
                "token_exchange",
                "Cloudflare returned an invalid login token. Try connecting again.",
                str(error),
            ) from error

    def access_token(self) -> str:
        token = self.token_store.load()
        if token is None:
            raise CloudflareOAuthError(
                "not_connected",
                "Connect Cloudflare before using R2 storage.",
            )
        if not token.expires_at or token.expires_at > time.time() + 60:
            return token.access_token
        if not token.refresh_token:
            self.token_store.clear()
            raise CloudflareOAuthError(
                "login_expired",
                "The Cloudflare login expired. Select Connect Cloudflare to reconnect.",
            )
        payload = _form_request(
            TOKEN_ENDPOINT,
            {
                "grant_type": "refresh_token",
                "refresh_token": token.refresh_token,
                "client_id": self.configuration.client_id,
            },
        )
        refreshed = self._token_from_payload(
            payload,
            previous_refresh_token=token.refresh_token,
        )
        self.token_store.save(refreshed)
        return refreshed.access_token

    def accounts(self) -> tuple[CloudflareAccount, ...]:
        request = urllib.request.Request(
            f"{API_BASE_URL}/accounts?per_page=50",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self.access_token()}",
                "User-Agent": "LetterSmith/1",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=20.0) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            details = error.read().decode("utf-8", errors="replace")[:2000]
            raise CloudflareOAuthError(
                "account_access",
                "Cloudflare login succeeded, but Letter Smith could not access an account. Ensure the OAuth client includes Account Settings Read permission.",
                f"http_status={error.code}; response={details}",
            ) from error
        except (OSError, urllib.error.URLError, UnicodeError, json.JSONDecodeError) as error:
            raise CloudflareOAuthError(
                "network",
                "Letter Smith could not retrieve the authorized Cloudflare account.",
                f"type={type(error).__name__}; details={error}",
            ) from error
        raw_accounts = payload.get("result", []) if isinstance(payload, Mapping) else []
        accounts: list[CloudflareAccount] = []
        for raw in raw_accounts:
            if not isinstance(raw, Mapping):
                continue
            account_id = str(raw.get("id", "")).strip()
            if len(account_id) == 32 and all(ch in "0123456789abcdefABCDEF" for ch in account_id):
                accounts.append(
                    CloudflareAccount(account_id=account_id, name=str(raw.get("name", "")).strip())
                )
        if not accounts:
            raise CloudflareOAuthError(
                "account_access",
                "No authorized Cloudflare account was returned. Reconnect and select an account Letter Smith may use.",
            )
        accounts.sort(key=lambda account: (account.name.casefold(), account.account_id))
        return tuple(accounts)


__all__ = [
    "CloudflareAccount",
    "CloudflareAuthorization",
    "CloudflareOAuthClientConfiguration",
    "CloudflareOAuthClientConfigurationStore",
    "CloudflareOAuthError",
    "CloudflareOAuthSession",
    "CloudflareOAuthToken",
    "CloudflareOAuthTokenStore",
    "DEFAULT_REDIRECT_URI",
]
