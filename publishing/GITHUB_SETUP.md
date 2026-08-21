# GitHub publishing setup

Letter Smith uses one public GitHub App for every distributed copy. Ordinary
users do not create an app or a personal access token.

Before building a release:

1. Register a public GitHub App owned by the Letter Smith developer account.
2. Enable Device Flow and allow installation by any account.
3. Grant repository permissions: Administration (write), Contents (write), and
   Pages (write). Do not request organization or account permissions.
4. Enable expiring user authorization tokens if desired; Letter Smith supports
   GitHub refresh tokens.
5. Copy the public client ID and app slug into `publishing/github_config.py`.
   Never copy the client secret into Letter Smith.
6. Install the app on a test account, publish two letters, republish one, restart
   Letter Smith, and verify both public URLs before distributing the build.

`LETTERSMITH_GITHUB_CLIENT_ID` and `LETTERSMITH_GITHUB_APP_SLUG` are available
only for source-development tests. Release validation requires the public values
to be set in `publishing/github_config.py` so packaged users do not need local
environment configuration.
