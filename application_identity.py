from __future__ import annotations


APPLICATION_NAME = "Letter Smith"
APPLICATION_VERSION = "1.0.0"
PUBLISHER_NAME = "Infini Works"
CREATOR_NAME = "Oluwatola Ayedun"
APPLICATION_DESCRIPTION = (
    "Letter Smith is a desktop application for creating, previewing, saving, "
    "and publishing interactive digital letters using artwork, messages, "
    "sound, and presentation effects."
)
ORGANIZATION_DOMAIN = "infini.works"
APP_USER_MODEL_ID = "InfiniWorks.LetterSmith"
EXECUTABLE_NAME = "LetterSmith.exe"
INSTALLER_NAME = "LetterSmith-Setup-1.0.0.exe"
INSTALLER_APP_ID = "{44498786-B309-5F44-9BDB-E2B7250E6304}"
MACOS_BUNDLE_IDENTIFIER = "works.infini.lettersmith"
MACOS_APP_NAME = "Letter Smith.app"
MACOS_EXECUTABLE_NAME = "LetterSmith"
MACOS_DISK_IMAGE_NAME = "LetterSmith-1.0.0.dmg"

# Public-release metadata is deliberately conspicuous until the creator
# replaces each value here. PLACEHOLDER_COLOR is not theme-derived.
PLACEHOLDER_COLOR = "#FF4F00"
BUILD_ID = "⟦PLACEHOLDER: BUILD ID⟧"
SUPPORT_EMAIL = "⟦PLACEHOLDER: SUPPORT EMAIL NOT CONFIGURED⟧"
OFFICIAL_WEBSITE = "⟦PLACEHOLDER: OFFICIAL WEBSITE⟧"
REPOSITORY_URL = "⟦PLACEHOLDER: REPOSITORY URL⟧"
COPYRIGHT_NOTICE = "⟦PLACEHOLDER: COPYRIGHT NOTICE⟧"
APPLICATION_LICENSE = "⟦PLACEHOLDER: APPLICATION LICENSE⟧"
LICENSE_FILE_OR_URL = "⟦PLACEHOLDER: LICENSE FILE OR URL⟧"
THIRD_PARTY_NOTICES = "⟦PLACEHOLDER: THIRD-PARTY NOTICES⟧"
THIRD_PARTY_ASSET_ATTRIBUTION = (
    "⟦PLACEHOLDER: THIRD-PARTY ASSET ATTRIBUTION⟧"
)
PRIVACY_POLICY_URL = "⟦PLACEHOLDER: PRIVACY POLICY URL⟧"
TERMS_OR_EULA_URL = "⟦PLACEHOLDER: TERMS / EULA URL⟧"

PUBLIC_METADATA = {
    "build_id": BUILD_ID,
    "support_email": SUPPORT_EMAIL,
    "official_website": OFFICIAL_WEBSITE,
    "repository_url": REPOSITORY_URL,
    "copyright_notice": COPYRIGHT_NOTICE,
    "application_license": APPLICATION_LICENSE,
    "license_file_or_url": LICENSE_FILE_OR_URL,
    "third_party_notices": THIRD_PARTY_NOTICES,
    "third_party_asset_attribution": THIRD_PARTY_ASSET_ATTRIBUTION,
    "privacy_policy_url": PRIVACY_POLICY_URL,
    "terms_or_eula_url": TERMS_OR_EULA_URL,
}


def is_placeholder(value: object) -> bool:
    return "PLACEHOLDER" in str(value).upper()


def unresolved_public_metadata() -> tuple[str, ...]:
    return tuple(
        value
        for value in PUBLIC_METADATA.values()
        if is_placeholder(value)
    )


__all__ = [
    "APPLICATION_NAME",
    "APPLICATION_VERSION",
    "APPLICATION_DESCRIPTION",
    "APPLICATION_LICENSE",
    "BUILD_ID",
    "COPYRIGHT_NOTICE",
    "CREATOR_NAME",
    "LICENSE_FILE_OR_URL",
    "OFFICIAL_WEBSITE",
    "PLACEHOLDER_COLOR",
    "PRIVACY_POLICY_URL",
    "PUBLIC_METADATA",
    "PUBLISHER_NAME",
    "REPOSITORY_URL",
    "SUPPORT_EMAIL",
    "TERMS_OR_EULA_URL",
    "THIRD_PARTY_ASSET_ATTRIBUTION",
    "THIRD_PARTY_NOTICES",
    "ORGANIZATION_DOMAIN",
    "APP_USER_MODEL_ID",
    "EXECUTABLE_NAME",
    "INSTALLER_NAME",
    "INSTALLER_APP_ID",
    "MACOS_BUNDLE_IDENTIFIER",
    "MACOS_APP_NAME",
    "MACOS_EXECUTABLE_NAME",
    "MACOS_DISK_IMAGE_NAME",
    "is_placeholder",
    "unresolved_public_metadata",
]
