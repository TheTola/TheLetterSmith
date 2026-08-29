# GitHub publishing setup

Letter Smith uses one public GitHub App for every distributed copy. Ordinary
users do not create an app or a personal access token.

## Desktop authentication model

Letter Smith uses GitHub App Device Flow and stores the resulting refreshable
GitHub App user access token (`ghu_`) in the operating-system keyring. GitHub
documents Device Flow for desktop applications, and GitHub App user access
tokens support every repository and Pages endpoint used by Letter Smith.

Letter Smith does not generate installation access tokens in the desktop
binary. GitHub requires the App private key to sign a JWT before an installation
token can be requested. Shipping that private key in Letter Smith would expose
the shared GitHub App credential to every user. Installation-token publishing
therefore requires a separately operated token-broker service; no such backend
is part of this repository.

The user access token is still constrained by both the user's own access and
the installed GitHub App's repository permissions. Its OAuth `scope` field is
intentionally empty. Expiring user tokens are refreshed automatically and are
never treated as a reason to reinstall the App.

## Required GitHub App settings

Before building a release:

1. Register a public GitHub App owned by the Letter Smith developer account.
2. Enable Device Flow and allow installation by any account.
3. Set these repository permissions. All three are required by the current
   implementation:
   - Administration: Read and write — creates the managed repository and
     creates or changes its GitHub Pages site.
   - Contents: Read and write — uploads blobs, trees, commits, references, and
     `.nojekyll`.
   - Pages: Read and write — reads, creates, and updates the Pages site.
4. Request no organization, enterprise, or account permissions.
5. Enable expiring user authorization tokens so Letter Smith can rotate them
   with refresh tokens.
6. Keep "Request user authorization (OAuth) during installation" disabled for
   this desktop-only build. That web flow requires a protected callback/token
   exchange. Device Flow is the supported no-client-secret desktop flow.
7. Copy only the public client ID and app slug into
   `publishing/github_config.py`. Never copy the client secret or App private
   key into Letter Smith.

Prefer "All repositories" so Letter Smith can create and maintain the signed-in
user's `<username>.github.io` Pages repository. If that repository does not
exist, Letter Smith creates it on first publish. Published letters live in
title-based folders and use links such as
`https://username.github.io/letter-title/`.

## Existing development installation

After adding the permissions above, GitHub requires the owner of every existing
installation to approve the update. For the current development installation,
either approve the pending permission update once or uninstall and reinstall
Letter Smith after correcting the App registration. New installations display
all current permissions during their first installation.

An installed app reporting no repository permissions is an App-registration or
pending-permission-update problem. Letter Smith enters `ACTION_REQUIRED`, stops
automatic retries, and opens GitHub only after an explicit user action.

## Runtime recovery

The durable keyring record contains the account identity, refreshable user
token, installation ID, repository owner/name/ID, repository selection, and
connection-schema version. No authorization code or installation token is
persisted.

Temporary network, server, and rate-limit failures enter `RECONNECTING` without
clearing this record. Forge retries in the background with 2, 5, 10, 30, and 60
second delays, then every five minutes, while honoring longer GitHub retry
headers. Permission, installation, repository-access, or revocation failures
enter a stable user-action state and are never polled indefinitely.

`LETTERSMITH_GITHUB_CLIENT_ID` and `LETTERSMITH_GITHUB_APP_SLUG` are available
only for source-development tests. Release validation requires the public
values in `publishing/github_config.py` so packaged users do not need local
environment configuration.
