# Letter Smith 1.0.0 Release Automation

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

The installer output is `release\installer\LetterSmith-Setup-1.0.0.exe`. Its
stable AppId supports upgrades. Installation and uninstall do not remove the
Letter Smith data under Local AppData or Documents. Installer creation re-runs
the frozen-distribution sanitation gate before invoking Inno Setup.
