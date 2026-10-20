"""Verified HTTPS trust shared by Letter Smith's GitHub requests."""

from __future__ import annotations

import ssl


class CertificateConfigurationError(RuntimeError):
    """Local certificate configuration needs repair before HTTPS can be used."""


def create_ssl_context() -> ssl.SSLContext:
    """Add the bundled public roots without replacing system/custom trust."""
    try:
        import certifi

        bundle = certifi.where()
    except (ImportError, OSError) as error:
        raise CertificateConfigurationError(
            "Letter Smith's trusted certificate bundle is unavailable. Update or "
            "reinstall Letter Smith; when running from source, reinstall certifi "
            "in this Python environment. Your letter is still saved locally."
        ) from error

    try:
        # Preserve platform trust and OpenSSL's SSL_CERT_FILE/SSL_CERT_DIR support.
        context = ssl.create_default_context()
    except (OSError, ValueError) as error:
        raise CertificateConfigurationError(
            "Letter Smith could not load the local HTTPS certificate settings. "
            "Check SSL_CERT_FILE and SSL_CERT_DIR or reinstall Letter Smith, "
            "then retry. Your letter is still saved locally."
        ) from error

    try:
        # certifi resolves its package resource in source and frozen applications;
        # never use the working directory or substitute unverified connections.
        context.load_verify_locations(cafile=bundle)
    except (OSError, ValueError) as error:
        raise CertificateConfigurationError(
            "Letter Smith's trusted certificate bundle could not be loaded. "
            "Update or reinstall Letter Smith; when running from source, "
            "reinstall certifi in this Python environment. "
            "Your letter is still saved locally."
        ) from error
    return context
