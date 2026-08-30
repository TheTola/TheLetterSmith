# macOS 13+ native release acceptance

Record the Mac model, OS version, CPU architecture, Python version, FFmpeg
source/version/checksums, artifact checksum, signing Team ID, notary submission
ID, and tester for each run. Do not record credentials or private user data.

## Required environments

- Clean macOS 13 user/profile on Apple Silicon.
- Native Apple Silicon smoke test of the packaged application.
- Native Intel macOS smoke test of the x86_64 package.
- Current light and dark appearance, Retina and external display coverage.

## Package and Gatekeeper

- Run the full test suite and focused release-input validation from source.
- Publish the applicable FFmpeg license notices and exact Corresponding
  Source/build materials with access equivalent to the GPL binaries.
- Build with `release/build_macos.py --build --confirm-package --notarize`.
- Confirm the release report, app, and architecture-qualified DMG paths.
- Verify `codesign --verify --deep --strict --verbose=2` on the app.
- Verify `spctl --assess --type execute --verbose=2` on the mounted app.
- Verify `xcrun stapler validate` and `hdiutil verify` on the DMG.
- Open the downloaded DMG through Finder, copy the app to Applications, and
  launch it through Gatekeeper without bypass instructions.
- Replace the app and DMG, relaunch, and confirm existing user data remains.
- Confirm no runtime writes occur inside the signed `.app`.

## Clean-profile application behavior

- First launch, normal relaunch, shutdown, and crash-free startup.
- Application Support, Caches, Documents, temporary, autosave, recovery, Saved
  Letters, and project paths use writable user locations.
- New/open/edit/save/autosave/restore/delete/export/relaunch persistence.
- Stock and example letters remain unsavable through ordinary and recovery paths.
- Bundled fonts and representative TTF, OTF, TTC, and variable normal/italic
  exports. Resolve the Papyrus redistribution/export policy before release; do
  not copy an operating-system font into the package without explicit rights.
- Images, animation, music, QMediaPlayer playback/crossfade, waveform analysis,
  FFmpeg conversion, and FFprobe inspection.
- WebEngine previews, local media, publication rendering, fullscreen, links,
  and clean shutdown.
- GitHub App/device authentication, Keychain allow/deny/locked behavior,
  credential persistence/refresh/sign-out, repository access, publish, and retry.
- Finder folder/file opening and native file dialogs.
- Frameless-window drag/resize/minimize/maximize/full screen, title bars, popups,
  multiple monitors, focus, shortcuts, accessibility names, and VoiceOver.

Any failed or unavailable item is a release blocker and must be recorded in the
release report or accompanying QA record before distribution.
