# Letter Smith 1.0.0 Beta 1 Release Automation

## Complete production release

Run the complete signed release pipeline from the repository root:

```powershell
py -3.13 .\release\run_release.py --confirm-release
```

On macOS, use the same entrypoint with `python3`. The command validates the
release environment, runs the complete test suite, provisions checksum-pinned
FFmpeg tools, validates release inputs, builds and validates the frozen app,
signs it, builds the installer or DMG, and verifies the final package. Windows
requires Inno Setup, SignTool, and `LETTER_SMITH_WINDOWS_CODESIGN_SHA1`. macOS
requires the architecture, Developer ID, and notary profile environment
variables documented below. Any failed gate stops the release.

The lower-level commands below remain available for validation and local
package testing. They do not replace the complete production release command.

Run the non-packaging release gates:

```powershell
.\release\build_release.ps1
```

These gates validate the explicit resource allowlist, production source privacy,
stock and example content, dependencies, and bundled media without creating a
package. A confirmed build also verifies the frozen resource tree byte-for-byte
and rejects user data, tests, caches, logs, credentials, and development paths.

After packaging is explicitly approved, install `requirements.txt` and run:

```powershell
.\release\build_release.ps1 -Build -ConfirmPackage
```

The build is a one-folder Windows distribution under `release\dist\LetterSmith`.
The script only removes `release\build` and `release\dist` when a confirmed build
starts. It does not build the Inno Setup installer.

Validate the Inno Setup configuration without packaging:

```powershell
.\release\build_installer.ps1
```

After the one-folder distribution is verified and installer packaging is
explicitly approved, install Inno Setup 6.3 or newer and run:

```powershell
.\release\build_installer.ps1 -Build -ConfirmPackage
```

The installer output is
`release\installer\LetterSmith-Beta-Setup-1.0.0.exe`. Its
stable AppId supports upgrades. Installation and uninstall do not remove the
Letter Smith data under Local AppData or Documents. Installer creation re-runs
the frozen-distribution sanitation gate before invoking Inno Setup.

For a public Windows release, install the Windows SDK, import the code-signing
certificate into the Windows certificate store, and set its exact SHA-1
thumbprint:

```powershell
$env:LETTER_SMITH_WINDOWS_CODESIGN_SHA1 = "0123456789ABCDEF0123456789ABCDEF01234567"
py -3.13 .\release\run_release.py --confirm-release
```

The signed build uses SHA-256 Authenticode and RFC 3161 timestamping, verifies
`LetterSmith.exe`, and has Inno Setup sign the uninstaller and final installer.
Set `LETTER_SMITH_SIGNTOOL` only when SignTool is outside the Windows SDK paths;
set `LETTER_SMITH_WINDOWS_TIMESTAMP_URL` only to override the default timestamp
service. Certificate files and passwords are never stored in the repository.

## macOS 13+

The macOS application is a direct-download Developer ID distribution, not a Mac
App Store package. Native build, signing, notarization, Gatekeeper, and clean-user
testing must finish before macOS compatibility is advertised.

Run the cross-platform metadata check from any host:

```bash
python3 release/build_macos.py --configuration-only
```

On macOS, install Xcode command-line tools, Python 3.13, and
`requirements.txt`. The shared provisioner stages the checksum-pinned FFmpeg
inputs described in `tools/macos/README.md`; `--ffmpeg-source-dir PATH` selects
reviewed local inputs instead and requires the `ffmpeg-source.json` schema
documented there. Select an architecture and run the strict native input gate:

```bash
export LETTER_SMITH_MACOS_TARGET_ARCH="arm64"
python3 release/build_macos.py --validate-only
```

Build an ad-hoc-signed `.app` and DMG for local testing:

```bash
python3 release/build_macos.py --build --confirm-package
```

For public distribution, import a Developer ID Application certificate, create
an `xcrun notarytool` keychain profile, and run:

```bash
export LETTER_SMITH_MACOS_CODESIGN_IDENTITY="Developer ID Application: ... (...)"
export LETTER_SMITH_MACOS_NOTARY_PROFILE="lettersmith-notary"
export LETTER_SMITH_MACOS_TARGET_ARCH="arm64"
python3 release/run_release.py --confirm-release
```

Valid targets are `arm64`, `x86_64`, and `universal2`. Universal2 requires a
universal2 Python and every native dependency to contain both slices. If that
gate cannot pass, build and test the two thin targets separately. Thin outputs
are architecture-qualified:

- `release/macos/LetterSmith-Beta-1.0.0-arm64.dmg`
- `release/macos/LetterSmith-Beta-1.0.0-x86_64.dmg`

Universal2 keeps `release/macos/LetterSmith-Beta-1.0.0.dmg`. Each successful build
also writes `release/macos/release-report-<architecture>.json` with artifact
sizes, checksums, tool provenance, signing summary, architecture sweep, and
notarization result. Generated artifacts and local third-party tools are ignored
by Git.

The pinned macOS 9.0.1 release builds are architecture-specific and declare
GPL-3.0-or-later. Before publishing the DMG, provide the applicable license
notices and equivalent access to the exact Corresponding Source/build materials
alongside the download. Provider binary URLs and checksums alone do not complete
that redistribution obligation.

The builder checks every Mach-O slice and deployment target, all nested code
signatures, the Cocoa plugin, WebEngine helper/resources, resource sanitation,
DMG integrity/mounting, and—when requested—notary acceptance, stapling, and
Gatekeeper assessment. Complete the packaged-app and clean-profile checks in
`release/MACOS_MANUAL_TEST_CHECKLIST.md` on both Apple Silicon and Intel. A real
macOS 13 system is required to prove the stated minimum OS; newer CI runners do
not substitute for that test.
