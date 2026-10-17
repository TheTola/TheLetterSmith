# Command & Settings lifecycle handoff — 2026-10-16

## Baseline, Git and isolation

Task branch: `codex/command-settings`. Worktree:
`C:\Users\Oluwatola Ayedun\.codex\worktrees\saved-letters-complete-projects\SmithLetter`.
Implementation commit: **`7835055b6d3b5092baafc155f629e58be60b77c2`** (19 files).
This handoff is a subsequent documentation commit, the twentieth changed file.
Starting revision: **`83fb721552aa3c7e75bfabbc91694857817b9d24`**, clean.
This includes Saved Letters implementation `32ce089b6b12c015dade887e1d7236ac8ca3fb27`
and the combined feature baseline `53ec7cfbef76e41ea7cbe7410df45aee4a0fe1b2`.
`git fetch origin` succeeded. At task start, `HEAD...origin/main` was **26 ahead,
0 behind**; there were no upstream commits to pull. A local task branch was
created from this integrated worktree. No remote push, publication, deployment,
history rewrite or primary-checkout merge was performed.

The primary checkout remains at `1612235e5f5bf0c02e84cead87fc84242d30cac5`.
Its 13 pre-existing tracked modifications and existing untracked files were left
alone. SHA-256 comparison against the preserved snapshot: **13 checked, 0 changed**.
See the preceding [Saved Letters handoff](saved-letters-complete-projects-handoff-2026-10-16.md)
for those paths and the integrated branch provenance.

Read and checked against actual code: [Foundation contract](foundation-contract.md),
[migration](foundation-migration.md), [Foundation handoff](foundation-handoff.md),
[Images](images-page-workspace-handoff.md), [Messages](messages-feature-handoff.md),
[Prompt Writer](prompt-writer-page-targets-handoff.md), [Sound](sound-project-playback-handoff.md),
[Forge](forge-dynamic-render-handoff.md), and the Saved Letters handoff above.

Entry points are `Main.main` / `Nexus`, standalone `command.main`, and the existing
`developermode.main` preference shortcut. Release manifests declare Windows
executable/installer and macOS 15+ arm64, x86_64 and universal2. Tests here used
Windows, CPython 3.13 and the existing primary `.venv`; no packages or macOS builds
were executed.

All application/test data used `LETTER_SMITH_PROFILE_ROOT` below `C:\Temp\ls8`
or `C:\Temp\ls8-native1` through `ls8-native3`, with isolated Qt INI settings and
short TEMP/TMP paths. Synthetic media and existing five-page test builders were
used. Bundled Example/stock resources remained read-only.

The baseline's default credential store did not respect profile isolation. The
new guard prevents default keyring reads/writes/deletes whenever the documented
profile variable is set; injected test stores still work. Native verification
used that guard, with no startup account mock. Earlier baseline execution used
the old code, so this report does not retrospectively claim that its automatic
account-restoration path was isolated. No publication was invoked.

## Changed paths and ownership

| Paths | Change |
| --- | --- |
| `project_lifecycle.py` | Compact starter dialog and one New/Open/Exit coordinator |
| `project_save.py` | Canonical candidate creation, activation and discard restoration |
| `Nexus.py`, `Main.py`, `command.py` | Shared buttons/menu/shortcut/close routes, scoped settings groups, guarded replacement and shutdown |
| `Forge_Tab.py`, `saved_projects_ui.py` | One injected save service, revision-aware save completion, shared Open flow, guarded UI completion and preview cleanup |
| `saved_letters.py` | Clear obsolete active-session locks and prompt/output/audio mirrors on canonical restore |
| `curtain_controls.py` | Narrow canonical curtain mutation, preserving page overrides |
| `developermode.py` | Existing reset restricted to appearance preferences |
| `publishing/github_auth.py` | Isolated development profiles cannot access the default system credential store |
| `message_shared_preview.py` | One observed blank-page unload with a bounded remaining timeout |
| `test/test_command_settings.py` | Real factory, persistence and Nexus transition/failure tests |
| `test/test_command_interaction.py`, `test/test_command_immersive_layout.py` | Updated action routing and orderly test-window cleanup |
| `test/test_developer_mode.py`, `test/test_message_shared_preview.py` | Preference retention and repeatable real-browser release |
| `test/test_saved_projects.py` | Real save/open completion retains busy ownership during browser release; failure diagnostics |
| `docs/foundation-contract.md`, this handoff | Authoritative lifecycle/preview contract and implementation evidence |

No schema, migration, serializer, resource database or prompt composer was added.
The Foundation model/repository/session, Saved Letters controller, owner-specific
editors and Forge renderer remain authoritative.

## Action routes and transitions

- Command's labeled New, Save, Open and Exit buttons, its existing hold-GO control,
  Settings menu actions and keyboard shortcuts share Nexus operations. Ctrl+S is
  owned by the shell QAction, so it works while Forge is hidden. Standalone Forge
  retains its shortcut fallback. New uses `QKeySequence.New`, Open uses `Open`,
  and Exit uses `Quit`; window close and native application Quit reach the same
  exit decision. Standalone `command.py` launches `Main.main(open_new_project=True)`.
- The starter dialog changes only candidate choices. Classic uses
  `cover, illustration, letter, end_page`; Custom uses the registry and configured
  **2–10** limits. Repeated types, arbitrary order and no Letter pages are valid.
  Each Letter receives an independent empty document. Factory UUIDs, empty prompt
  strings/artwork, silent sound and empty publication data are used. The existing
  recipient-entry step remains after creation; it retains the chosen pages.
- `ProjectLifecycle._resolve` calls `Nexus.flush_project_edits`: validate the focused
  field, commit page names, save Messages, persist detached Prompt Writer, then
  synchronize canonical workspace state. Invalid input stays open with a repair
  message. Only then does the shared explicit-save binding determine dirty state.
- Save invokes `SavedProjectsController.save(finished=...)`, which captures and
  commits through the canonical service. New/Exit/Open wait for completion, flush
  again, and compare **session generation plus authored revision**. Revision R's
  save cannot discard R+1. The editor stays usable during copying; conflicting
  transition actions are disabled. Repeated invocations cannot start another flow.
- Failed save, conflict, canceled decision, or invalid candidate prevents
  replacement. Existing explicit saves and local work survive. This editable-save
  workflow uses managed UUID destinations and has no file chooser; its controller's
  refusal/failure path cancels the transition. Example/legacy copies retain the
  existing independent-save and publication-detachment policy.
- `prepare_new_project` creates a detached factory candidate. Activation uses
  `SavedLetterRestorer.restore_canonical`, its transactional staging, journal,
  resource validation and rollback, then a fresh unsaved UUID binding. Owner media
  and workers are released before commit; revision is rechecked afterward.
- Views reload under restoration guards; Prompt Writer reloads afterward using
  its own blocked signals. Old title, artwork preview, prompt mirrors, soundtrack,
  locks and output paths are cleared. A post-commit view failure explicitly reports
  recoverable canonical state instead of claiming rollback or successful opening.
- New/Open Discard replaces only active working state. Exit Discard prepares the
  last explicit save, or a fresh unbound classic draft, and restores it canonically
  before closing. Restart therefore does not silently revive the discarded draft.
  Earlier working generations remain recoverable. Cancel occurs before owner
  disposal and leaves editors, selections and subscriptions usable.

## Settings and reset scope

| Scope | Existing values/controls and ownership |
| --- | --- |
| Application preferences | `application_theme`, `theme_family`, existing window geometry/maximized state, UI/picker history, Visionary/provider location; `SettingsStore`, `ThemeService`, Nexus window preference services |
| Reusable resources | Prompt libraries, custom palettes, stock media and Music Archive; their existing library services survive New/Discard/reset |
| Secure account | GitHub account control and secure keyring; never project/reset data; disabled default store in isolated profiles |
| Project | Canonical title/recipient metadata, curtain style, shared design, required features, publication metadata and supported presentation settings; shared operations and feature owners |
| Project sound | Playlist occurrence/order, selected track, silence, volume/mute and playback settings; Sound's canonical state, with existing compatibility mirrors such as `starting_volume`, `music_volume`, `music_muted` |
| Selected page | Type/name/artwork/crop/GIF in Images; independent rich text/layout/overlay in Messages; directions/overrides/exact prompt in Prompt Writer; no second Settings editor |
| Temporary view | Selected page, preview zoom/device view, transport position, active output path, jobs and render handles; not copied into authored formatting or future-project defaults |

Menus show Project, Application Preferences, and Help & Diagnostics. Project
curtain changes update only `shared_settings.curtain_style` and its compatibility
mirror; existing page overrides and exact prompt strings survive. No stale dialog
writes a whole project. Save Appearance Preferences still calls `ThemeService.save`.
Application appearance and picker/window updates no longer mark authored content
dirty. Prompt Writer remains the shared design editor.

The former developer reset (`Ctrl+Alt+Shift+D`) is now **Reset Appearance
Preferences**: only `application_theme` and `theme_family` reset, the default
appearance applies, and startup appearance selection returns next launch.
It does not save, wipe, close a project or clear credentials. Cancel/write failure
retains state. New Project is the sole in-app draft reset; no factory-reset or
duplicate page-reset control was added. Manage Saved Projects routes to Saved
Letters' selected-save deletion with recovery, rather than deleting all local
copies. Clear Image/Text stay with their owners. Historical low-level command
maintenance helpers retain their interfaces for compatibility; no Nexus control
or standalone launcher invokes that old filesystem-wipe path.

## Worker, preview and shutdown ordering

Canceling a decision does not stop media. After confirmation, preflight flushes
edits and finishes workspace recovery, then releases curtain jobs, Messages
imports/rendering, Images imports/GIF resources, Sound jobs/audition and Forge
preview resources through existing owner hooks. Worker waits use 5-second bounds.
The shared browser release observes one blank navigation and spends only the
remaining timeout after build cancellation; it remains reusable after release.
Forge defers thread-finished cleanup until its UI completion callback returns.
Nested browser-release events therefore cannot finish the restore overlay or
unlock operation controls before canonical activation is complete.
Busy owners retain recoverable state and report retry instead of being destroyed.

Confirmed close is queued because Qt ignores recursive `close()` during
`closeEvent`. The exit token is rechecked after preflight. Disposal then stops
animations/timers, Forge operations and saved previews, saves window preferences,
disconnects registered listeners, shuts down detached Prompt Writer and remaining
feature owners, disposes WebEngine, and releases project-state/session ownership.
Cleanup failures are logged and reported; closing itself never claims a save.
No process-name kill or shared-directory cleanup is used. Only explicitly
identified task-owned hung test processes were stopped during verification.

## Verification

Test execution used `C:\Temp\ls8\run.py`, which configures `ApplicationPaths`
before importing tests, changes into the isolated workspace, isolates QSettings
as INI, and invokes `unittest`. The code root is passed separately. Example:

```powershell
$env:LETTER_SMITH_PROFILE_ROOT='C:\Temp\ls8\fresh-profile'
$env:QT_QPA_PLATFORM='offscreen'
$env:QTWEBENGINE_CHROMIUM_FLAGS='--disable-gpu'
$env:PYTHONDONTWRITEBYTECODE='1'
$env:TEMP='C:\Temp\ls8'; $env:TMP=$env:TEMP
$python='C:\Users\Oluwatola Ayedun\Desktop\SmithLetter\.venv\Scripts\python.exe'
& $python C:\Temp\ls8\run.py (Get-Location).Path test_command_settings
```

Actual recorded runs (overlapping suites; counts are not additive):

| Log under `C:\Temp\ls8` | Modules / outcome |
| --- | --- |
| `baseline.log` | Command interaction/hold, application paths, persistence hardening, SavedProjectTests: 76 tests, 1 pre-existing failure (stale preview-format panel after old reset); fixed |
| `systems.log` | Project model/session/repository, Foundation consumers, SavedProjectTests, saved sound restore, application paths, persistence hardening, Prompt Writer pages: **190 passed**, 47.354s |
| `final-actions.log` | Command Settings, developer mode, Command interaction/hold: **42 passed**, 66.036s |
| `owners-final.log` | Command Settings/interaction, Sound project playback, Images workspace, native transport subprocess scenarios: **79 passed**, 97.592s |
| `audio-retry.log` | Complete Sound playback lifecycle module: **14 passed**, 9.823s |
| `base-audio.log` | Previously stalled stop test alone on untouched 83fb721 source: **1 passed**, 0.083s |
| `final-command.log` | Command suites plus immersive layout: 61 tests; 4 failures/1 error in existing layout tests |
| `geometry-base.log` | Exact five failing layout cases on archived untouched 83fb721 source: same 4 failures/1 error, 11.529s |
| `restore-final.log` | Latest nine Command Settings UI cases passed; one of two Saved Letters UI cases failed on shared preview release. This motivated the single-navigation repair above |
| `preview-final.log` | 28 tests; 27 passed, the same Saved Letters Open case still failed. The five real shared-preview tests passed. Forge's early completion during nested release was then guarded |
| `open-final.log` | Final real Saved Letters UI and Command Settings UI paths, including the busy-ownership assertion, Save/Discard/Cancel, races/conflicts and recoverable view failure: **11 passed**, 162.149s |

`git diff --cached --check` passed before the implementation commit. The final
Open/preview regression passed after both shared release/completion repairs;
earlier failed runs are retained above rather than counted as passing evidence.

Commands for the main runs, each using its own profile root as above:

```powershell
# systems.log
& $python C:\Temp\ls8\run.py (Get-Location).Path test_project_model test_project_session test_project_repository test_foundation_consumers test_saved_projects.SavedProjectTests test_saved_letter_restore_sound test_application_paths test_persistence_hardening test_prompt_writer_pages
# final-actions.log
& $python C:\Temp\ls8\run.py (Get-Location).Path test_command_settings test_developer_mode test_command_interaction test_command_hold_interaction
# owners-final.log
& $python C:\Temp\ls8\run.py (Get-Location).Path test_command_settings test_command_interaction test_sound_project_playback test_images_workspace test_sound_playback_lifecycle.SoundPlaybackLifecycleTests.test_native_crossfade_repeats_with_cached_source_duration test_sound_playback_lifecycle.SoundPlaybackLifecycleTests.test_native_single_track_repeats_after_end_delay
# audio-retry.log
& $python C:\Temp\ls8\run.py (Get-Location).Path test_sound_playback_lifecycle
# preview-final.log
& $python C:\Temp\ls8\run.py (Get-Location).Path test_message_shared_preview test_saved_projects.SavedProjectUiTests test_command_interaction test_developer_mode
# open-final.log
& $python C:\Temp\ls8\run.py (Get-Location).Path test_saved_projects.SavedProjectUiTests test_command_settings.CommandSettingsUiTests
```

The five reproduced baseline cases are `test_maximized_sound_preview_keeps_native_height_cap`
(Mock margin TypeError), `test_forge_fullscreen_keeps_web_preview_visible`,
`test_forge_tab_switch_keeps_controls_below_preview_and_accessible`,
`test_image_preview_border_follows_the_complete_source`, and
`test_images_preview_is_visible_on_first_project_ready_show`.

An earlier combined run was interrupted at a legacy test-window close dialog;
that fixture now calls shutdown before close. Another combined media run stalled
inside `QMediaPlayer.stop` (`sound_tab.py:2671`); its two identified task-owned
Python processes were stopped. The unchanged backend module passes separately,
including real play/pause/stop and bounded native-event-loop crossfade/repeat
subprocesses. This intermittent combined-run problem is still open; no complete
serial-suite pass is claimed. Cold catalog/render/file-stat latency also produced
diagnostic stacks during otherwise progressing tests.

Native Windows GUI checks used the real Nexus and synthetic saved projects:
1200×850 at 100%, and 1000×760 at 125%. Checked starter classic/custom choices,
10-row custom list, keyboard focus/cancel, grouped settings menus, long-title
Save/Discard/Cancel dialogs, Ctrl+S from Images, cancel-exit continued use,
Command Exit, window close, New/Discard, fresh blank artwork and corrected title.
Three `native-result.json` records show successful shutdown, no provider browser
opened and no publication binding. Native2 saved a new four-page draft while
retaining the original five-page save; Native3 discarded the empty starter while
retaining the original save. Screenshots were inspected through computer use;
no screenshot files are claimed in the repository. Audible manual acceptance,
macOS, packaged builds and scales other than 100%/125% were not run.

## Prompts 9–10

UI & Synchronization should consume this branch's real lifecycle and finish the
combined shell audit: the five baseline layout cases, long-title presentation,
busy/late-callback behavior across all feature combinations, detached focus and
additional display scales. Preserve session-generation/revision checks and the
single save service; do not reconnect the old filesystem reset helpers.

Final Integration & Testing must run the full suite serially, investigate the
intermittent Qt audio-stop stall and cold catalog/render latency, and verify
installed Windows/macOS shutdown, playback and crash/lock recovery. This task
does not establish release readiness or authorize remote publication.

- **Command & Settings implementation: Complete on the integrated Windows source
  branch.** The remaining platform, media and broad shell acceptance checks are
  listed above; no required action is an unwired placeholder.
- **Verified on integrated baseline:** clean `83fb721552aa3c7e75bfabbc91694857817b9d24`,
  implementation `7835055b6d3b5092baafc155f629e58be60b77c2`; actual test runs and native
  evidence are recorded above, including baseline failures and interrupted runs.
- **Ready for UI & Synchronization: Yes**, using `codex/command-settings` and its
  shared lifecycle, save binding, guarded view restore and owner cleanup APIs.
- **Remaining cross-application checks:** five baseline layout failures; full
  serial suite; intermittent Qt audio stop and cold render/catalog latency;
  cross-feature callback/focus/scaling audit; installed Windows/macOS playback,
  shutdown and crash/lock recovery. No remote action was performed.
