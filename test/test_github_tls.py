from __future__ import annotations

import io
import json
import os
import re
import ssl
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from unittest import mock

import certifi

from https_transport import CertificateConfigurationError, create_ssl_context
from publishing.github_auth import (
    GitHubAPI,
    GitHubAccount,
    GitHubAuthenticator,
    GitHubConnectionService,
    GitHubConnectionState,
    GitHubFailureKind,
    GitHubOperationError,
    GitHubSession,
    GitHubToken,
    StoredGitHubCredential,
    classify_github_error,
)
from publishing.github_config import (
    GITHUB_DEVICE_CODE_URL,
    GITHUB_TOKEN_URL,
    GitHubApplicationConfiguration,
)
from publishing.github_pages import GitHubPagesPublisher, _fetch_public_bytes


def _response(payload: dict | bytes, status: int = 200) -> io.BytesIO:
    response = io.BytesIO(
        payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
    )
    response.status = status
    return response


class HTTPSCertificateContextTests(unittest.TestCase):
    def test_bundle_supplies_verified_trust_without_system_roots_from_any_cwd(self) -> None:
        previous_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory)
                with mock.patch.object(ssl.SSLContext, "load_default_certs"):
                    context = create_ssl_context()
            finally:
                os.chdir(previous_cwd)

        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        self.assertGreater(context.cert_store_stats()["x509_ca"], 0)

    def test_bundle_adds_to_existing_default_trust(self) -> None:
        certificates = re.findall(
            r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----",
            Path(certifi.where()).read_text(encoding="ascii"),
            re.DOTALL,
        )
        self.assertGreaterEqual(len(certificates), 2)
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory) / "cacert.pem"
            bundle.write_text(certificates[1], encoding="ascii")

            def load_local_trust(context, _purpose) -> None:
                context.load_verify_locations(cadata=certificates[0])

            with (
                mock.patch.object(ssl.SSLContext, "load_default_certs", load_local_trust),
                mock.patch("certifi.where", return_value=str(bundle)),
            ):
                context = create_ssl_context()

        trusted = context.get_ca_certs(binary_form=True)
        for certificate in certificates[:2]:
            self.assertIn(ssl.PEM_cert_to_DER_cert(certificate), trusted)

    def test_missing_or_corrupt_bundle_fails_with_repair_instructions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.pem"
            corrupt = Path(directory) / "corrupt.pem"
            corrupt.write_text("invalid certificate data", encoding="ascii")
            for bundle in (missing, corrupt):
                with self.subTest(bundle=bundle.name), mock.patch(
                    "certifi.where", return_value=str(bundle)
                ):
                    with self.assertRaises(CertificateConfigurationError) as raised:
                        create_ssl_context()
                    self.assertIn("certifi", str(raised.exception))
                    self.assertIn("reinstall", str(raised.exception))

    def test_unreadable_bundle_has_no_insecure_fallback(self) -> None:
        bundle = certifi.where()
        original_load = ssl.SSLContext.load_verify_locations

        def load(context, *args, **kwargs):
            if kwargs.get("cafile") == bundle:
                raise PermissionError("certificate bundle is unreadable")
            return original_load(context, *args, **kwargs)

        with mock.patch.object(ssl.SSLContext, "load_verify_locations", load):
            with self.assertRaises(CertificateConfigurationError) as raised:
                create_ssl_context()
        self.assertIn("reinstall certifi", str(raised.exception))

    def test_missing_certifi_has_actionable_error(self) -> None:
        with mock.patch.dict(sys.modules, {"certifi": None}):
            with self.assertRaises(CertificateConfigurationError) as raised:
                create_ssl_context()
        self.assertIn("reinstall certifi", str(raised.exception))

    def test_invalid_local_trust_settings_fail_safely(self) -> None:
        with mock.patch(
            "ssl.create_default_context", side_effect=ssl.SSLError("invalid local trust")
        ):
            with self.assertRaises(CertificateConfigurationError) as raised:
                create_ssl_context()
        self.assertIn("SSL_CERT_FILE", str(raised.exception))


class GitHubTLSRequestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = create_ssl_context()
        self.configuration = GitHubApplicationConfiguration(
            client_id="Iv1.1234567890abcdef", app_slug="letter-smith"
        )

    def test_device_poll_account_and_refresh_use_context_and_preserve_timeout(self) -> None:
        access_token = "ghu_" + "a" * 20
        refresh_token = "ghr_" + "b" * 20
        device_code = "device-code-must-not-be-logged"
        user_code = "ABCD-1234"
        opener = mock.Mock(
            side_effect=[
                _response({
                    "device_code": device_code,
                    "user_code": user_code,
                    "verification_uri": "https://github.com/login/device",
                    "expires_in": 600,
                    "interval": 1,
                }),
                _response({"access_token": access_token, "refresh_token": refresh_token}),
                _response({"login": "ada", "id": 7}),
                _response({"access_token": access_token, "refresh_token": refresh_token}),
            ]
        )
        authenticator = GitHubAuthenticator(
            self.configuration,
            credential_store=mock.Mock(),
            api=GitHubAPI(opener=opener, timeout=17.5),
            authorized_api_factory=lambda token: GitHubAPI(
                token, opener=opener, timeout=17.5
            ),
            sleeper=lambda _seconds: None,
        )
        with (
            mock.patch("publishing.github_auth.create_ssl_context", return_value=self.context),
            self.assertLogs("publishing.github_auth", level="INFO") as logs,
        ):
            authorization = authenticator.begin_device_authorization()
            session = authenticator.poll_device_authorization(authorization)
            authenticator._refresh(session.token)

        calls = opener.call_args_list
        self.assertEqual(
            [call.args[0].full_url for call in calls],
            [GITHUB_DEVICE_CODE_URL, GITHUB_TOKEN_URL, "https://api.github.com/user", GITHUB_TOKEN_URL],
        )
        for call in calls:
            self.assertIs(call.kwargs["context"], self.context)
            self.assertEqual(call.kwargs["timeout"], 17.5)
        self.assertEqual(
            urllib.parse.parse_qs(calls[1].args[0].data.decode())["device_code"],
            [device_code],
        )
        self.assertEqual(calls[2].args[0].get_header("Authorization"), f"Bearer {access_token}")
        for secret in (access_token, refresh_token, device_code, user_code):
            self.assertNotIn(secret, "\n".join(logs.output))

    def test_publishing_upload_and_public_fetch_use_context(self) -> None:
        opener = mock.Mock(return_value=_response({"sha": "uploaded-blob"}, 201))
        with tempfile.TemporaryDirectory() as directory:
            publisher = GitHubPagesPublisher(
                directory,
                GitHubSession(GitHubAccount("ada", 7), GitHubToken("ghu_example")),
                api=GitHubAPI("ghu_example", opener=opener, timeout=23),
                configuration=self.configuration,
            )
            with mock.patch(
                "publishing.github_auth.create_ssl_context", return_value=self.context
            ):
                result = publisher._upload_blob("ada", "ada.github.io", "letter.txt", b"letter")
        self.assertEqual(result, "uploaded-blob")
        self.assertEqual(opener.call_args.args[0].full_url, "https://api.github.com/repos/ada/ada.github.io/git/blobs")
        self.assertEqual(opener.call_args.kwargs, {"timeout": 23, "context": self.context})

        with (
            mock.patch("publishing.github_pages.create_ssl_context", return_value=self.context),
            mock.patch("urllib.request.urlopen", return_value=_response(b"letter")) as fetch,
        ):
            self.assertEqual(_fetch_public_bytes("https://ada.github.io/letter.txt", 9), b"letter")
        self.assertEqual(fetch.call_args.kwargs, {"timeout": 9, "context": self.context})

    def test_default_urllib_transport_keeps_proxy_support(self) -> None:
        proxies = {"https": "http://proxy.example:8080"}
        built_openers = []
        original_build = urllib.request.build_opener

        def build_opener(*handlers):
            opener = original_build(*handlers)
            built_openers.append(opener)
            return opener

        with (
            mock.patch("publishing.github_auth.create_ssl_context", return_value=self.context),
            mock.patch("urllib.request.getproxies", return_value=proxies),
            mock.patch("urllib.request.OpenerDirector.open", return_value=_response({"id": 7})),
            mock.patch("urllib.request.build_opener", side_effect=build_opener),
        ):
            self.assertEqual(GitHubAPI("ghu_example").request("GET", "/user"), {"id": 7})
        self.assertEqual(len(built_openers), 1)
        https_handler = next(
            handler for handler in built_openers[0].handlers
            if isinstance(handler, urllib.request.HTTPSHandler)
        )
        proxy_handler = next(
            handler for handler in built_openers[0].handlers
            if isinstance(handler, urllib.request.ProxyHandler)
        )
        self.assertIs(https_handler._context, self.context)
        self.assertEqual(proxy_handler.proxies, proxies)

    def test_missing_bundle_stops_api_and_public_requests_before_transport(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch("certifi.where", return_value=str(Path(directory) / "missing.pem")):
                api_opener = mock.Mock()
                public_opener = mock.Mock()
                with mock.patch("urllib.request.urlopen", public_opener):
                    for request in (
                        lambda: GitHubAPI("ghu_example", opener=api_opener).request("GET", "/user"),
                        lambda: _fetch_public_bytes("https://ada.github.io/", 9),
                    ):
                        with self.subTest(request=request), self.assertRaises(GitHubOperationError) as raised:
                            request()
                        self.assertEqual(raised.exception.code, "certificate_setup")
                        self.assertEqual(classify_github_error(raised.exception), GitHubFailureKind.ACTION_REQUIRED)
                        self.assertIn("reinstall certifi", raised.exception.user_message)
                api_opener.assert_not_called()
                public_opener.assert_not_called()

    def test_public_certificate_verification_failure_requires_action(self) -> None:
        for error in (
            ssl.SSLCertVerificationError("CERTIFICATE_VERIFY_FAILED"),
            urllib.error.URLError(ssl.SSLCertVerificationError("CERTIFICATE_VERIFY_FAILED")),
        ):
            with self.subTest(error=type(error).__name__), mock.patch(
                "urllib.request.urlopen", side_effect=error
            ):
                with self.assertRaises(GitHubOperationError) as raised:
                    _fetch_public_bytes("https://ada.github.io/", 9)
                self.assertEqual(raised.exception.code, "certificate_verification")
                self.assertEqual(classify_github_error(raised.exception), GitHubFailureKind.ACTION_REQUIRED)

    def test_refresh_certificate_errors_preserve_saved_credentials(self) -> None:
        stored = StoredGitHubCredential(
            GitHubAccount("ada", 7),
            GitHubToken("ghu_" + "a" * 20, "ghr_" + "b" * 20, 900, 10000),
            installation_id=41,
            repository_owner="ada",
            repository_name="ada.github.io",
        )
        for failure in (
            CertificateConfigurationError("Reinstall certifi to repair the certificate bundle."),
            urllib.error.URLError(ssl.SSLCertVerificationError("CERTIFICATE_VERIFY_FAILED")),
        ):
            with self.subTest(failure=type(failure).__name__):
                store = mock.Mock()
                store.load.return_value = stored
                setup_failure = isinstance(failure, CertificateConfigurationError)
                opener = mock.Mock(side_effect=None if setup_failure else failure)
                authenticator = GitHubAuthenticator(
                    self.configuration,
                    credential_store=store,
                    api=GitHubAPI(opener=opener),
                    now=lambda: 1000,
                )
                with (
                    mock.patch(
                        "publishing.github_auth.create_ssl_context",
                        side_effect=failure if setup_failure else None,
                        return_value=self.context,
                    ),
                    self.assertLogs("publishing.github_auth", level="INFO") as logs,
                ):
                    snapshot = GitHubConnectionService(authenticator).restore()
                self.assertEqual(snapshot.state, GitHubConnectionState.ACTION_REQUIRED)
                self.assertEqual(snapshot.session, stored.session())
                self.assertIn(snapshot.error_code, {"certificate_setup", "certificate_verification"})
                store.clear.assert_not_called()
                store.save.assert_not_called()
                if setup_failure:
                    opener.assert_not_called()
                for secret in (stored.token.access_token, stored.token.refresh_token):
                    self.assertNotIn(secret, "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
