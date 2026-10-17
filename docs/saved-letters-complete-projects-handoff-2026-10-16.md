# Saved Letters complete-project handoff — 2026-10-16

## Revision, scope and isolation

Implementation commit: `32ce089b6b12c015dade887e1d7236ac8ca3fb27`.
Branch: `codex/saved-letters-complete-projects`.
Worktree: `C:\Users\Oluwatola Ayedun\.codex\worktrees\saved-letters-complete-projects\SmithLetter`.
This handoff is a subsequent documentation commit. No remote push, deployment,
publication, image generation, installer build or primary-checkout merge occurred.

The primary checkout started and remains at
`1612235e5f5bf0c02e84cead87fc84242d30cac5`, on `main` ahead of its recorded remote
by one commit. Its 13 pre-existing tracked modifications were preserved: `Main.py`,
`publishing/github_auth.py`, `publishing/github_pages.py`, `release/LetterSmith.spec`,
`release/MACOS_MANUAL_TEST_CHECKLIST.md`, `release/README.md`, `release/build_macos.py`,
`release/build_release.py`, `release/release_manifest.json`, `requirements.txt`,
`settings.json`, `test/test_application_paths.py`, `test/test_github_publishing.py`.
Final SHA-256 comparison: **13 checked, 0 changed**. Existing untracked `.test-output/`,
`Prompt Writer/`, Images/Messages handoffs, diagnostics/TLS modules and their tests
were also left in place. No destructive Git cleanup or history rewriting was used.

The combined branch was inspected and assembled before Saved Letters work:

| Input | Actual code consumed |
| --- | --- |
| Foundation | `1612235`; schema 3 model, migration, repository, sessions and adapters |
| Images + Prompt Writer integration | `c5cfd7d1759eb90451068e52ac61e2ae80af115a`; includes Images `0a73a0e` and Prompt Writer `f9739b9` |
| Messages + Forge integration | `7c9d698795258fbecabe35d7ec572371686d80b0`; includes Forge `681a85e`; merged by `4dc6584` |
| Sound integration | `7a8386f3b6806377ea8c7f79682abcdeb0d3a17c`; merged by `9e304a7` |
| Checked combined starting revision | **`53ec7cfbef76e41ea7cbe7410df45aee4a0fe1b2`**, clean worktree |

The last baseline commit reconciled shared layout resolution, renderer mute
substitution/persistence and the authoritative shared-input version assertion.
Read contracts and handoffs: [Foundation](foundation-contract.md),
[migration](foundation-migration.md), [Images](images-page-workspace-handoff.md),
[Messages](messages-feature-handoff.md), [Prompt Writer](prompt-writer-page-targets-handoff.md),
[Sound](sound-project-playback-handoff.md), [Forge](forge-dynamic-render-handoff.md).
The implementation uses their actual APIs, not just these documents.

Declared release platforms are Windows executable/installer and macOS 15+
arm64, x86_64 and universal2. Verification here used Windows source execution,
CPython 3.13 and the existing primary `.venv`; macOS and packaged releases were not run.

All authored/runtime test files, native launches, settings, caches, saved projects
and recovery used short isolated `LETTER_SMITH_PROFILE_ROOT` paths under
`C:\Temp\ls7-base\` or `C:\Temp\ls7-native1` through `ls7-native4`.
Qt settings used isolated INI paths. Bundled resources were read-only inputs.

**Credential isolation exception:** older catalog tests initialized Forge's
existing automatic GitHub account restoration. A diagnostic stack captured the
system credential-store/HTTP validation path. No publication ran, but whether
secure credential metadata was refreshed is unverified; this report does not
claim that credential access was isolated. Those catalog tests now disable the
unrelated startup check, as the new Nexus tests and native fixtures already did.
Profile isolation for system keyring access remains an explicit Prompt 8/10 item.

## Modified paths

The implementation commit changes these 27 files; this handoff is the 28th file:

- UI and shell: `Forge_Tab.py`, `Nexus.py`, `saved_projects_ui.py`, `images_workspace.py`, `ui_theme.py`.
- Canonical services: `project_model.py`, `project_repository.py`, `project_save.py`, `project_consumers.py`, `saved_letters.py`, `sound_model.py`.
- Shared resource/preview consumers: `Editor.py`, `Message_tab.py`, `message_resources.py`, `message_shared_preview.py`, `messages_workspace.py`, `page_artwork.py`, `forge_render.py`.
- Authoritative documentation: `docs/foundation-contract.md`, `docs/foundation-migration.md`.
- Checks: `test/test_saved_projects.py`, `test/test_project_repository.py`, `test/test_forge_render.py`, `test/test_saved_letter_activity_order.py`, `test/test_saved_letter_restore_sound.py`, `test/test_sound_playback_lifecycle.py`, `test/test_ui_theme.py`.
- Handoff: `docs/saved-letters-complete-projects-handoff-2026-10-16.md`.

## Authoritative operations and lifecycle

Saved Letters remains in Forge's existing collection panel. Cards show title,
thumbnail, actual page/Letter counts, saved time and labeled states. One primary
**Open Project** action serves the selection. Project Actions contains saved
preview, saved-version duplication, rename, recoverable deletion, current-project
copy and recovery assignment. Existing collection search/filter/sort behavior,
keyboard activation, card reuse and scroll preservation remain in place.
Save Project is in Forge's action row and has the standard Save shortcut.

`SavedProjectsController` delegates to the existing `ProjectSaveService`,
`ProjectRepository`, `SavedLetterCatalog` and `SavedLetterRestorer`; it introduces
no serializer, migration engine, top-level workspace or independently writable
authored representation.

Save sequence:

1. `Nexus.flush_project_edits()` commits the focused valid field, pending Images
   name, bound Messages document and detached Prompt Writer, then synchronizes
   the established compatibility views. A failure keeps edits open and prevents
   the operation; the status identifies the field/repair or retry path.
2. `capture_editable_save()` captures one detached canonical model, content
   revision, live session ID, source roots, destination and expected storage
   revision. Snapshot capture is short; asset copying occurs in the worker.
3. `commit_editable_save()` uses the canonical repository's staging, validation,
   writer lock and atomic generation pointer. A library lock serializes library
   writes. Expected-revision conflicts preserve both disk and local work.
4. `complete_editable_save()` binds only the originating project/session. Saving
   revision R does not clean later edits. `ProjectDirtyController.mark_saved`
   also receives its expected dirty revision. Index/card updates follow commit;
   thumbnail failures cannot invalidate that save.

Explicit saves live at `Saved Letters/Editable Projects/<project UUID>/` and use
the normal `.lettersmith-project` repository. Display titles never supply writable
paths. Active-workspace `editable-save.json` caches only the destination/revision
binding; it is not an authored project. Existing workspace autosave remains
separate and cannot overwrite the explicit editable destination.

Opening flushes current editors and offers Save/Discard/Cancel for unsaved work.
`read_saved_project()` validates the selected snapshot in a worker without
activation or mutation. The GUI checks originating session and content revision,
releases existing consumer resources, calls `open_saved_project()` /
`restore_canonical()`, then emits the established `project_restored`/`letter_loaded`
hooks. `Nexus` suppresses editing callbacks while rehydrating views and starts
render work only after restoration ends. Failed candidates preserve the current
project. A → B → A gets a new session token and rejects A's older callbacks.

## Identity, resources, recovery and publication

- **Save Project:** same project identity/destination and supported publication
  association. Incomplete, empty and no-Letter drafts are savable.
- **Save a Copy:** canonical `independent_copy()`, new project UUID/destination;
  the original remains active. **Duplicate Saved Version** always reads the
  selected committed snapshot, including when its active session has newer edits.
- Page IDs are project-scoped and retained with the entire reference graph.
  Mutable state is deep-copied. `publication` and writable publishing/active-output
  bindings are detached centrally; retained source-ID provenance is inert.
  Application credentials are not project resources.
- The repository collects the full supported authored/recovery resource graph,
  including displaced recovered message resources. Rich HTML, layout/settings,
  exact Unicode/whitespace prompt strings, old choice snapshots, GIF source bytes,
  animation settings, repeated playlist occurrences and extension data survive.
  No prompt rebuilding or global historical type/string replacement occurs.
- `load(allow_missing=True)` permits absent committed media while preserving its
  reference and warning; strict loading remains the default. Unsupported schemas,
  unsafe paths and changed/corrupt bytes remain errors. Declared missing resources
  cannot resolve to same-named leftovers from another workspace or become silently
  filled on save. Images, Messages and Forge enforce this shared rule. Explicit
  resource replacement is the repair path. Ambiguous sound aliases cannot reuse
  unrelated archive entries; existing archive media remain under content identity.
- Generation promotion now uses `transactional_io`'s existing bounded sharing-
  violation retry. Exhausted failure leaves the old pointer intact. Implicit
  migration-report reuse requires the same project ID; explicit copies retain
  their supplied report, while a fresh project cannot inherit another report.
- Legacy, Example, Stock and Recovery sources open as independent editable copies.
  Selection/opening never upgrades the source in place. Real bundled Example
  hashes were checked through open/edit/save/reset; its master and permanent
  custom-palette resource remained intact.
- `recoverable_items()` and `assign_recovered()` expose explicit canonical repair.
  Messages require a selected Letter page; prompts can target any selected page.
  The UI previews the item and offers Replace (keep previous), Copy to a new page,
  or Cancel for occupied destinations. Source content, provenance and displaced
  versions remain recoverable after saving. Nothing is guessed from page position.
- Rename commits display metadata with identity and resource references preserved.
  Delete confirms the project ID/scope, protects active work and moves the entire
  owned save into Recovery. Cancel/failure preserve the save. No source/library
  deletion, remote deletion or global garbage collection is introduced.

## Index, previews and shared-service repairs

Catalog schema 4 records project ID, storage/content revisions, counts and warning
state. Invalid, unsupported and missing known entries remain identifiable. A
thumbnail is the first usable Cover artwork in project order, otherwise the first
usable artwork, otherwise a labeled placeholder. Existing generation/path tokens
reject stale or canceled decodes. No thumbnail is used as an animated source.
Root-only directory watching avoids holding every saved folder open on Windows.

Forge output marked by `lettersmith-display.json` is excluded from the editable
collection. Compatibility metadata/activity callbacks no longer write prompts or
another canonical save into those generated folders. Older mixed folders are
retained on disk. Existing legacy package metadata behavior remains available.
Forge's public export still uses its verified presentation-resource allowlist.

Preview Saved Version uses a separate `ProjectSession`, a temporary profile cache
and `SharedMessagePreview` with Forge's `SavedProjectPreviewAdapter`. Application
controls/banner/fonts/UI sounds come from its presentation root; authored artwork,
message assets and soundtrack come from the selected immutable generation. The
editor stays unchanged and the browser page is muted. The shared preview now shows
its view before requesting startup, respecting Sound's hidden-document guard and
allowing GIF animation. Forge uses extended Windows paths for internal staging
while retaining ordinary browser-compatible public paths. Theme refresh now
coalesces its own style events instead of repeatedly restyling status updates.

Publication labels use stored confirmation metadata and Forge's display fingerprint:
unpublished, matching last published display, local display changes not published,
or unverified. Private prompt-only changes do not masquerade as public content.
Library refresh performs no remote probe. Local save/rename does not publish or
erase a known URL on failure.

## Verification actually performed

Production repositories, controllers, Qt widgets and WebEngine were exercised with
synthetic five-page/media fixtures; persistence was not mocked as a whole. The new
tests reuse `canvas_layout` and `legacy_fixture` from `test/test_project_model.py`.
Only account restoration, UI sound/prewarm and deliberate failure cases are mocked
in the Nexus tests. Legacy fixture sizes 4, 6 and 12 verify version-aware migration,
no double conversion/truncation and unchanged source bytes.

| Run / local log under `C:\Temp\ls7-base` | Actual outcome |
| --- | --- |
| `baseline.log` at `9e304a7` | 277 tests, 3 failures, 145.868 s: shared-input expected version and two Messages previews with unresolved mute substitution |
| `checked.log` after combined baseline repairs | 34 tests passed, 2.694 s; baseline repairs committed in `53ec7cf` |
| `final-all.log` | 357 tests, 1 failure + 1 error, 372.382 s: overly strict missing-preview assertion and Windows generation-promotion sharing failure; both subsequently repaired |
| `final-repairs.log` | 88 tests passed, 47.658 s |
| `resource-guards.log` | 156 tests passed, 100.477 s: repository, consumers, Images, Messages, Sound, Forge and Saved Projects services |
| `final-paths.log` | 15 tests passed, 3.680 s: known-missing path validation, resource isolation/archive preservation and persistence hardening |
| `final-ui.log` | 101 tests, 100 passed, 1 GIF-startup failure, 238.259 s; both real Nexus Saved Letters tests passed |
| `preview-isolated.log` | Reproduced that GIF failure independently: 4 tests, 1 failure |
| `preview-visible.log` | Formerly failing GIF/scrolling/document-integrity check passed after visibility-order repair, 13.573 s |
| `preview-final.log` | All 4 shared-preview checks passed, 37.736 s, including cancellation, replacement, file release and real GIF frame advancement |
| `final-clean.log` | Broader repeat stopped after a persistent wait inside unchanged `SoundPlayer.stop()` / Qt multimedia at `sound_tab.py:2671`; no complete-suite pass is claimed |
| `git diff --check` | Passed before commit |

Intermediate failing runs also exposed long Windows paths, missing presentation
controls in saved previews, recursive theme refresh and legacy tests expecting
invalid entries to disappear. Their production fixes and updated contract
assertions were included in the later runs above. Periodic `Timeout (0:00:45)!`
stack dumps are diagnostic output from the runner, not unittest failures by
themselves. Offscreen Chromium reported unavailable GLES contexts; assertions and
native observations are reported separately rather than treating those logs as
graphical acceptance.

The actual runner was `C:\Temp\ls7-base\run.py`: it adds this checkout and `test/`
to `sys.path`, initializes/configures `ApplicationPaths.for_runtime`, changes into
the isolated active workspace, isolates `QSettings` to profile INI storage and
runs `unittest.defaultTestLoader.loadTestsFromNames`. Representative actual commands:

```powershell
$env:LETTER_SMITH_PROFILE_ROOT='C:\Temp\ls7-base\resource-guards'
$env:QT_QPA_PLATFORM='offscreen'
$env:QTWEBENGINE_CHROMIUM_FLAGS='--disable-gpu'
$env:PYTHONDONTWRITEBYTECODE='1'
$env:TEMP='C:\Temp\ls7-base'
$env:TMP=$env:TEMP
$python='C:\Users\Oluwatola Ayedun\Desktop\SmithLetter\.venv\Scripts\python.exe'
& $python C:\Temp\ls7-base\run.py (Get-Location).Path test_project_repository test_foundation_consumers test_images_workspace test_messages_workspace test_sound_project_playback test_sound_playback_lifecycle test_forge_render test_saved_projects.SavedProjectTests

# These runs used their own final-ui, final-paths and preview-final profile roots.
& $python C:\Temp\ls7-base\run.py (Get-Location).Path test_saved_projects.SavedProjectUiTests test_saved_projects.SavedProjectTests.test_missing_saved_resources_do_not_use_previous_workspace_names test_message_overlay test_message_security test_message_shared_preview test_saved_letter_activity_order test_saved_letter_recipient_restore test_saved_letter_restore_sound
& $python C:\Temp\ls7-base\run.py (Get-Location).Path test_project_repository.ProjectRepositoryTests.test_missing_resources_are_repairable_and_escape_is_rejected test_saved_projects.SavedProjectTests.test_missing_saved_resources_do_not_use_previous_workspace_names test_persistence_hardening
& $python C:\Temp\ls7-base\run.py (Get-Location).Path test_message_shared_preview
```

The production Nexus tests type final characters into both Letter documents and a
detached Prompt Writer, save without visiting every tab, reopen a saved copy,
verify exact strings, and preview the original without changing the open copy.
They also exercise Space activation, delete cancellation and visible save failure.
Service tests cover edit-during-save, conflict/interruption, A → B → A, recovery
assignment/replacement, same titles, rename/delete failure, missing/unsupported
sources, copy publication detachment, placeholder/stale thumbnails, Example and
permanent-resource survival. Repository/Forge checks cover reorder/duplicate,
rich/embedded assets, retained inactive content and editable/public separation.

Native Windows checks used synthetic profiles and the real Nexus window at
1200×850 and 1000×760: long/duplicate titles, selected/current labels, a missing-
artwork placeholder with explicit warning, compact action menu, Example discovery,
read-only preview navigation through both distinct Letter messages/backgrounds,
and normal shutdown. Final library showed the expected four cards without an
extra generated-output card. `native-result.json` in `ls7-native3` and `ls7-native4`
records `shutdown: true`, five pages and no provider browser opened. Screenshots
were observed through computer use; no screenshot files are claimed in this repo.
Audible output, alternate display scaling, installed builds and macOS were not
manually accepted. The final visibility repair was verified by real WebEngine
tests, not another native-window screenshot run.

## Prompts 8–10 and completion boundary

- **Command & Settings:** use this integrated branch/implementation revision.
  Reuse `Nexus.flush_project_edits`, canonical explicit-save/copy policy and the
  destination binding. Keep permanent libraries/master templates separate from
  workspace reset. Audit profile isolation of the system credential store before
  any development account-restoration test.
- **UI & Synchronization:** audit all remaining shell New/Close/reset/transition
  routes, detached-window focus, busy/late-result handling and high-DPI/keyboard
  behavior. Investigate cold renderer/catalog latency seen in test waits; avoid
  holding a broad UI lock or weakening session/revision guards.
- **Final Integration & Testing:** rerun the complete suite serially and investigate
  the observed Qt multimedia stop hang. The affected Sound lifecycle tests passed
  in `resource-guards.log`, so intermittent native-backend behavior remains open.
  Verify installed Windows and supported macOS builds, native playback lifecycle,
  scaling and crash/lock recovery. No full-release or live-publication acceptance
  is implied by this handoff.

Saved Letters implementation: **Complete on the integrated Windows source branch**;
the cross-application acceptance items above remain.

Verified on integrated baseline: **`53ec7cfbef76e41ea7cbe7410df45aee4a0fe1b2` plus
implementation `32ce089b6b12c015dade887e1d7236ac8ca3fb27`**, with actual run results above.

Ready for Command & Settings: **Yes**, using this branch and its documented shared
APIs; system-keyring profile isolation must be addressed there.

Remaining cross-application checks: **shell lifecycle/scaling/latency audit, full
serial suite and Qt audio-stop investigation, Windows/macOS package acceptance**.
