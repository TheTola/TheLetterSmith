# Publishing implementation and migration evidence

## Scope and status

**October 22 correction:** The user replaced the repository-management interface with one-time repository setup and a minimal repository-name display. The current source follows that narrower interface; the earlier management-dialog descriptions below are historical. The migration and retained hosting remain intact.

The approved implementation adds account-scoped publishing collections, explicit repository setup, URL previews, custom letter folders, and journaled copy/rename operations. Existing publication snapshots remain independent of the default for new letters. Explicitly verified migrations record a new primary location and retain previous locations. Retained copies are not automatically synchronized.

The user's final collection name is **Letters of Encouragement**, repository `TheTola/Letters-of-Encouragement`. The intermediate name was `Letters-to-Keep`. The original personal-site hostname remains `thetola.github.io`; the repository name does not change that hostname. Customer destinations are derived from their authenticated account and selected repository, not this developer account.

Implementation phases 1–4 are complete. Phase 5 has focused regression and live migration evidence below; the remaining acceptance gates are listed separately. This is not a declaration that a new packaged release is ready for distribution.

## Changed files

- `publishing/destinations.py`: validated destination snapshots, account-specific defaults, atomic collection registry, retained-location records.
- `publishing/github_collections.py`: read-only discovery and setup previews; confirmed setup; repository identity/source validation.
- `publishing/collections_ui.py`: collection setup/management, publication address review, copy previews and migration recovery.
- `publishing/github_migration.py`: pinned presentation copies, verification, journals, repository rename reconciliation.
- `publishing/github_auth.py`: selected destination access checks and installation pagination.
- `publishing/github_pages.py`: destination-aware publication/removal, exact raw paths and encoded URLs, collision checks, read-only previews, stable republishing.
- `publishing/github_library.py` and `github_library_ui.py`: configured and retained collection discovery and destination-aware downloads.
- `publishing/models.py`, `settings_store.py`, `saved_letters.py`: publication snapshot persistence. The new SavedLetter field is appended to preserve positional constructor compatibility.
- `Forge_Tab.py`: collection management entry point and confirmed publication preview.
- `release/migrate_publishing_repository.py`: explicit backup, repository rename, old-name hosting restoration, content verification, and local reconciliation. `--old-name` supports project repositories as well as personal sites.
- `.gitignore` and `release/release_manifest.json`: keep the personal collection registry/lock out of source control and reject the registry in release distributions.
- `test/test_publishing_destinations.py`, `test/test_github_migration.py`, `test/test_github_publishing.py`: destination isolation, setup confirmation, paths, copy recovery, and republishing coverage. Recursive Git trees include a directory entry for an existing letter; this is permitted while conflicting files and case/Unicode aliases remain rejected.
- `docs/github-publishing-assessment-2026-10-20.md`, `docs/github-publishing-implementation-prompt-2026-10-20.md`, and this report: assessment, implementation plan, and evidence.

Pre-existing unrelated working-tree changes were preserved. No source commit or source push was made by this task.

## Live repository migration

Original repository ID: `1350082412`, now named `TheTola/Letters-of-Encouragement`.

Retained hosting repositories:

- `TheTola/TheTola.github.io`, ID `1427545597`, keeps original personal-site URLs.
- `TheTola/Letters-to-Keep`, ID `1427554060`, keeps intermediate collection URLs.

All three `main` refs were verified equal to `abb49ba088e17426c72042d20b5cec67e1fc1069`; tree `7668e47abf0c69b5838853363f10f1fc0648f533`. The original Git history, 28 letter folders, and 1,763 blobs totaling 1,959,047,578 bytes are preserved. No repository or original letter was deleted. Duplicate historical project identities remain unchanged.

Complete bare backups and resumable journals are under:

`%LOCALAPPDATA%/Infini Works/Letter Smith/migrations/2026-10-20-letters-to-keep/`

`%LOCALAPPDATA%/Infini Works/Letter Smith/migrations/2026-10-20-letters-of-encouragement/`

Each contains `original.git`, `files.json`, and `migration.json`. Backup `git fsck --full` and remote-head comparison passed before renaming. Remote updates are not forced. An uncertain response is reconciled by immutable repository ID before a retry.

The first migration verified 169 HTTP resources at both the personal-site address and intermediate collection address. Checks include every letter's marker/index plus representative JavaScript, CSS, image, and audio resources, compared to backed-up Git blob hashes. This samples hosted media; the complete repository tree comparison covers all retained source files.

The final-name migration completed at `2026-10-20T21:00:03.256563+00:00`. Its journal records 169 verified HTTP resources at each of the intermediate and final collection addresses. Both development and runtime profiles now use Letters of Encouragement as their default, with previous locations retained. Browser validation at `https://thetola.github.io/Letters-of-Encouragement/valley-shepard/` confirmed the rendered cover, message overlay, and next-page navigation; the original personal-site link also opened successfully. Audio was muted; audible playback was not tested.

## Checks

Using `.venv/Scripts/python.exe`, `PYTHONPATH=test;.`, and `QT_QPA_PLATFORM=offscreen`:

- `-m unittest test_publishing_destinations test_github_migration test_github_publishing test_github_library test_github_tls test_application_paths -q`: **144 passed**.
- After fixing the recursive-tree directory collision, `-m unittest test_github_publishing test_github_migration -q`: **76 passed** (a subset of the above coverage).
- `release.build_release.validate_release_inputs()`: passed; 111 source files, 481 payload files, 12 stock images, 3 stock music tracks, 3 stock letters, 1 example. This does not build or sign a package. Release orchestration messages printed by mocked tests are not actual release builds.
- Compilation of the changed publishing/migration modules and targeted `git diff --check`: passed.
- `-m unittest test_saved_projects -q`: the first final run failed with Direct3D device-loss/context-reset messages. Rerunning with `QTWEBENGINE_CHROMIUM_FLAGS=--disable-gpu` passed **18 tests in 128.961 seconds**. This is offscreen persistence/UI evidence, not native GPU acceptance.
- Live read-only previews through the production collection/publisher classes passed against repository ID `1350082412`: a new `Valley & Shepard` folder preview encodes as `Valley%20%26%20Shepard/`, and a previously published `valley-shepard` path remains stable after its title changes. The isolated validation made 40 GET requests and zero remote mutations. An initial validation attempt omitted the existing publication's provider field; it was corrected to use the actual publication metadata contract before this passing run.

## Phase 5 continuation: October 20, 2026

Live acceptance used only two explicitly allowlisted synthetic repositories: `TheTola/LetterSmith-Publishing-Validation` (ID `1427643853`) and `TheTola/LetterSmith-Publishing-Validation-Copy` (ID `1427643961`). The adapter rejected mutations outside these repositories and prohibited repository deletion. Authentication for this automated lifecycle used the existing GitHub CLI, while production collection, publishing, migration and library classes performed the operations. This does not substitute for the packaged GitHub App onboarding gate below.

Passed live checks:

- Confirmed setup created the test collections; unconfirmed setup issued zero remote requests.
- Published `Valley & Shepard/`, verified its encoded Pages URL, and updated it at the same address despite a title change.
- A different project requesting that folder previewed `Valley & Shepard-2/` without replacing the original.
- Copied the synthetic presentation to `Encouragement Café/`. The test deliberately lost the response after a real target-branch update, then resumed from its journal using a new migration service instance. The original source commit remained unchanged.
- Discovered both presentations through the library, downloaded them, and verified every downloaded file's Git blob hash/size. Hosted HTML, JavaScript, CSS, SVG and WAV bytes matched the downloaded copies.
- Rendered both encoded addresses in the browser; artwork and audio controls loaded, JavaScript changed the message, and reload displayed the updated version.
- Unpublished both synthetic presentations through the production removal code, then independently confirmed HTTP absence of their indexes and markers. Test repositories and Git history were retained.
- Rechecked the three real letter repositories: each still points to `abb49ba088e17426c72042d20b5cec67e1fc1069`; the normal default remains Letters of Encouragement.

Native Windows dialogs were exercised in a separate profile with a read-only GitHub adapter: existing repository preview/selection, friendly-label save, title-based Unicode address preview, and cancellation. The actual encoded URL was visible and publish confirmation stayed disabled until the changed address had been previewed.

Two implementation fixes were required by acceptance:

1. Windows' locale-default text decoding corrupted non-ASCII migration journal paths during recovery. `publishing/github_migration.py`, `publishing/collections_ui.py`, and `release/migrate_publishing_repository.py` now read journals as UTF-8. The existing interrupted-copy regression now includes `Café`.
2. Isolated development profiles previously refused all credential storage, preventing a genuine second-account sign-in. `publishing/github_auth.py` now supports explicit `LETTER_SMITH_PROFILE_CREDENTIALS=1` opt-in only alongside `LETTER_SMITH_PROFILE_ROOT`. It uses a separate OS-keyring service derived from the resolved profile path, preserving the normal account. Profiles without opt-in remain disconnected and cannot save credentials. Two new tests verify isolation, persistence and default refusal.

Final focused checks after these fixes: **87** publishing/destination/migration tests plus **59** TLS/library/application-path tests passed, **146 total**. Targeted `git diff --check` passed. The earlier 18 saved-project tests were not repeated because this continuation did not change project persistence.

### Isolated Windows package

An immutable copy of the current dirty working tree was built under `.test-output/publishing-phase5/source`; hashes and base HEAD are recorded in `source-snapshot.json`. Shared checkout work and existing packages were preserved. The shared PyInstaller installation lacked its Windows bootloader, so the same version, **6.22.3**, was installed from its official wheel into the isolated `build-tools` directory. No shared dependency installation was replaced.

The normal `release/build_release.py --build --confirm-package` command passed source/resource validation, built the executable, and passed distribution validation, including sanitation and CA-bundle checks. The distribution contains **3,580 files**. It was not signed or installed system-wide.

Executable: `.test-output/publishing-phase5/source/release/dist/LetterSmith/LetterSmith.exe`

SHA-256: `dd1b89671d42a9f390591044e66008fbd4f0e063cd946b6eb27b7cf8e4bdcbdd`

The isolated launcher is `.test-output/publishing-phase5/Start-Second-Account-Validation.ps1`. It uses `second-account-profile` and its separate OS-keyring service. Initial launch reached the fresh beta-diagnostics prompt; the visible app was left for the user to dismiss that prompt and complete second-account authorization. Do not launch the executable directly for this acceptance step, because the launcher supplies the isolation settings.

Evidence: `.test-output/publishing-phase5/live-results.json`, `phase5-evidence.json`, `package-build-retry.log`, `final-tests.log`, and the isolated migration journal.

## Phase 5 recheck: October 22, 2026

- Reran the six focused test modules against the current checkout: **146 passed in 33.739 seconds**. Targeted `git diff --check` passed. The test log is `.test-output/publishing-phase5/final-tests-2026-10-22.log`; its mocked release messages do not represent an installer build.
- All three protected repository heads still equal `abb49ba088e17426c72042d20b5cec67e1fc1069`. The original, intermediate and final `valley-shepard/` addresses each returned HTTP 200 with identical 9,523-byte HTML (SHA-256 `0265327d2f88faede9d33aa010d44328a54ea59113420065ad3e1a5b6021302e`). The full migration verification remains recorded above.
- The seven publishing service/UI source files in the isolated build snapshot still match the current checkout byte-for-byte.
- The previous isolated session had exited cleanly. Relaunched it through the isolated launcher and observed the Forge page displaying **Connected as TheTola**. This verifies restored credentials for the developer account in the isolated packaged profile; it does not verify independent-account onboarding.
- Left the isolated app visible on Forge and requested sign-in with a different account. No original letters, hosting repositories, normal-profile credentials, or publication settings were changed during this recheck.

## One-time setup correction: October 22, 2026

- `Main.py` starts repository onboarding after the main window opens. `Forge_Tab.py` checks the connected account, skips configured repositories, and offers naming only when setup is missing. A declined prompt is not repeated during that session; publishing can resume incomplete setup. Existing connected branch-based sites can be remembered using read-only GitHub checks.
- `publishing/collections_ui.py` now contains a single-name initial setup dialog with an empty name field, name check, explicit create/use confirmation, and Not now. Removed collection management/copy dialogs and the collection-selection button from publication review.
- `publishing/github_collections.py` restores an existing configured site without creating or renaming it. Successful setup persists the chosen repository identifier in the existing account-scoped destination registry.
- `publishing/destinations.py` formats display names by replacing underscores, slashes, backslashes and hyphens with spaces, collapsing whitespace and applying title case. It does not change the stored repository identifier.
- `command.py` displays a small repository label in the existing GO/save area; `Nexus.py` connects it to Forge's repository state. Forge's former Collection button is also replaced by plain repository text. Save, load and reset actions retain their existing handlers.
- Extended `test/test_publishing_destinations.py` with six regressions covering formatting, exact chosen names, persistence/account isolation, configured-user skipping, legacy-site adoption, blank initial input/confirmation invalidation, and one prompt per session.
- Verification: **150** publishing/destination/migration/library/TLS/path tests, **23** existing save/reset tests and **2** additional setup UI tests passed (**175 total**). Compilation and targeted diff checks passed. Offscreen rendered setup and save-area screenshots were inspected under `.test-output/publishing-phase5/repository-setup.png` and `repository-label.png`. An initial combined run aborted while onboarding was attached to generic connection notifications; onboarding was scoped to application startup and explicit sign-in, then publishing and save checks passed separately.
- This correction made no GitHub mutations. Existing personal letters, migration records and hosted links were preserved. The October 20 packaged executable predates this correction and has not been rebuilt.

## Updated packaged app: October 22, 2026

The corrected interface is now packaged and open for user testing. The source-only limitation above describes the earlier delivery, not this build.

- Final executable: `release/dist/LetterSmith-Repository-Setup-20261022-Updated/LetterSmith.exe`.
- SHA-256: `62675beba826bb8fd135f66649895f107adc0e1f14b389db6eaf9de73cb52843`.
- Built from an immutable snapshot of 610 source/resource/build-input files under `.test-output/repository-setup-20261022-fixed/source`. The standard `release/build_release.py --build --confirm-package` passed input and distribution validation. All 3,580 files copied into the final package matched the build output by SHA-256. Existing packages were retained; this is an unsigned portable test build, not an installer.
- The first native launch exposed an initialization-order error: `Main.py` accessed `forge_tab` before Nexus had created its lazy feature tabs. `Main.py` now invokes `Nexus.start_repository_setup()`, which uses the existing tab initializer before starting onboarding. The current saved-project launch arguments were preserved. `test/test_publishing_destinations.py` now covers this startup order; all 16 tests in that module passed after the fix. Targeted diff checks passed.
- Final native launch used the normal runtime profile, displayed the recipient page, and restored TheTola's connection to `Letters-of-Encouragement` without another setup prompt. The app was left open for the user. No test letter was published or saved.
- All 1,414 files in the normal Saved Letters directory matched their pre-launch SHA-256 inventory after the successful launch. All three protected hosting repositories still point to `abb49ba088e17426c72042d20b5cec67e1fc1069`.
- A verified ZIP backup of 1,697 active-project/settings files is retained at `.test-output/repository-setup-20261022/user-data-backup.zip`. Source and package hash manifests and the successful build log are under `.test-output/repository-setup-20261022-fixed/`.

## Earlier independent-account acceptance gate

- Live independent-account onboarding remains unverified. The user redirected the task to the interface correction above; second-account sign-in is not a prerequisite for delivering that source change. No second-account success is claimed.

Product GitHub App homepage/release branding still requires a real product URL. This is separate distribution work; no invented URL or app-ownership change was introduced.

## Saved Letters loading fix — 2026-10-22

The existing library refresh used Forge's global project-operation worker, disabling Saved Letters until discovery, local download verification, and every cover fetch had finished. It repeated immutable metadata reads across retained-link repositories and had no overall deadline. Successful authentication in the runtime log did not imply that this subsequent library work had finished. This is the confirmed unbounded loading mechanism; the exact network condition during the reported ten-minute wait was not captured.

- Read-only listing now has its own tracked Qt worker. Local controls remain available, entries arrive before covers, and opening a download still uses the existing verification/import path.
- Discovery reuses verified small blobs and immutable commit/tree metadata across repositories. Refresh no longer hashes every downloaded presentation or forces another full local catalog scan.
- Requests use a ten-second socket timeout; listing/sign-in waiting has a sixty-second UI deadline. Cancellation suppresses late results, failures show their reason, and the contextual Retry GitHub button retries without overlapping workers. Shutdown waits for the tracked worker. Account changes discard old results.
- Validation: 25 library tests passed, including slow covers, timeout, stalled authentication, expired authorization, retry during cancellation, deduplicated reads, existing download/open behavior, and preservation of edits. Another 112 publishing, destination, migration, and saved-project tests passed. Targeted diff checks passed.
- Live read-only discovery using the existing account took 27.72 seconds and 41 GET requests, including 28 marker reads. A separate live/offscreen Qt check rendered 26 enabled project cards in 26.76 seconds with Forge's global busy flag false. Two pre-existing duplicate project-ID warnings account for the difference between 28 publication paths and 26 project IDs; no remote paths were removed or renamed.
- All 1,414 saved files matched the baseline SHA-256 inventory. Repository configuration stayed unchanged. All three hosting repositories remained at `abb49ba088e17426c72042d20b5cec67e1fc1069`.
- Updated portable package: `release/dist/LetterSmith-Saved-Letters-Fix-20261022/LetterSmith.exe`, SHA-256 `ffdbfe230f2cecfd14bc0f1c1240d17eaf531c4f870989487d7f263323c8bd6a`. Standard package validation passed and all 3,580 copied package files matched the build output. All 610 snapshotted build inputs still matched the checkout after building.
- Logs, source/package manifests, and live-check results are under `.test-output/github-library-fix/`. Native interaction with this new package remains pending while older LetterSmith instances are open; the live UI check above used an isolated offscreen fixture, not the user's active project.
