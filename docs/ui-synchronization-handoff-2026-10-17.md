# UI & Synchronization handoff — 2026-10-17

## Integrated baseline and isolation

Task branch: `codex/ui-synchronization`, in
`C:\Users\Oluwatola Ayedun\.codex\worktrees\saved-letters-complete-projects\SmithLetter`.
Starting revision: **`f2a0a3307b6f568226e09e86a48bfd110f92f8ec`**, clean.
Implementation commit: **`ed5b7258f6c4bf9ca859cef284b7f7bea9754bf5`** (32 files).
This handoff is a subsequent documentation commit, the thirty-third changed file.
This task follows the integrated Command & Settings implementation
`7835055b6d3b5092baafc155f629e58be60b77c2`, Saved Letters implementation
`32ce089b6b12c015dade887e1d7236ac8ca3fb27`, and checked combined feature revision
`53ec7cfbef76e41ea7cbe7410df45aee4a0fe1b2`.

Verified ancestry includes Images/Prompt Writer integration
`c5cfd7d1759eb90451068e52ac61e2ae80af115a`, Messages/Forge integration
`7c9d698795258fbecabe35d7ec572371686d80b0` through merge `4dc6584`, and Sound
`7a8386f3b6806377ea8c7f79682abcdeb0d3a17c` through merge `9e304a7`.
No unfinished feature branch was selected or merged during this task.

The primary checkout remains on `main` at
`1612235e5f5bf0c02e84cead87fc84242d30cac5`. Its 13 pre-existing tracked changes
and untracked work were preserved. Their inventory is in the
[Saved Letters handoff](saved-letters-complete-projects-handoff-2026-10-16.md).
Development used the existing CPython 3.13 virtual environment from that checkout;
source changes were confined to this isolated worktree. No push, deployment,
publication, image generation, account change or destructive Git cleanup occurred.
Final primary-checkout SHA-256 verification: **13 files checked, 0 changed**.
`git diff --cached --check` passed before the implementation commit.

Read and checked against code: [Foundation contract](foundation-contract.md),
[migration guide](foundation-migration.md), [Foundation handoff](foundation-handoff.md),
[Images](images-page-workspace-handoff.md), [Messages](messages-feature-handoff.md),
[Prompt Writer](prompt-writer-page-targets-handoff.md), [Sound](sound-project-playback-handoff.md),
[Forge](forge-dynamic-render-handoff.md), the Saved Letters handoff above, and
[Command & Settings](command-settings-lifecycle-handoff-2026-10-16.md).

`Main.main` / `Nexus` remains the application entry point. Declared release
platforms remain Windows executable/installer and macOS 15+ arm64, x86_64 and
universal2. This task exercised Windows source execution only.

All runs used `LETTER_SMITH_PROFILE_ROOT` below `C:\Temp\ls9`, isolated Qt INI
settings and short TEMP/TMP paths. Existing five-page test builders supplied
synthetic content. Bundled fonts, stock media and Example resources were read-only.
The integrated profile credential guard remained enabled; no provider was opened.

## Modified paths

| Paths | Change |
| --- | --- |
| `Nexus.py`, `window_chrome.py` | Common navigation/commit guards, canonical dirty routing, session-scoped autosave completion, readable dirty title, bounded Forge release and safe event-filter disposal. |
| `project_lifecycle.py` | Reject lifecycle entry while a modal editor has unapplied input; retain its existing Apply/Cancel contract. |
| `Message_tab.py`, `messages_workspace.py` | Visible Letter fallback through canonical selection, complete page labels, retain failed drafts and useful previews; ignore private prompt/old-session notifications. |
| `PromptWriterPanel.py`, `prompt_writer_project.py` | Reusable exact-target writer, persistence failure blocks rebinding, unchanged text keeps cursor/undo on rename/reorder, consistent page labels. |
| `images_workspace.py` | Failed pending names block departure; bounded active-resource checks retain usable thumbnails on invalid replacements. |
| `Forge_Tab.py`, `forge_render.py`, `message_shared_preview.py` | Stable-ID corrections, all-editor output flush, render dependency tokens, deduplicated previews, external resource validation, failed unload blocks replacement. |
| `project_model.py` | Narrow prompt staleness to effective composer inputs and geometry; preserve exact strings and shielded overrides. |
| `test/test_ui_synchronization.py` | Production Nexus five-page routes, detached edits/save/open, fallback, callback rejection, playback handoff, failed-release recovery and bounded subscriptions. |
| `test/test_command_immersive_layout.py`, `test/test_window_sizing.py` | Update obsolete fixtures to canonical Images/real WebEngine, deterministic viewport settling, repeated window-controller disposal. |
| `test/test_page_viewer.py` | Observe actual crossfade handoff before checking volume/mute, replacing a fixed 650ms sleep that sampled an unfinished fade. |
| `test/test_forge_render.py`, `test/test_forge_session_output.py` | Render dependency/revision guards, stable correction IDs, all-editor preparation and release refusal. |
| `test/test_images_workspace.py`, `test/test_message_shared_preview.py` | Same-path resource replacement, invalid-byte retention, pending name failure, private edits produce no preview builds. |
| `test/test_messages_workspace.py`, `test/test_prompt_writer_pages.py`, `test/test_project_model.py` | Current labels, prompt cursor/undo/retry, effective dependency and inheritance rules. |
| `docs/foundation-contract.md`, this handoff, `docs/ui-sync-evidence-2026-10-17/*.png` | Authoritative integration contract and eight actual native captures. |

## Canonical state and action routing

One `ProjectSession` owns the active `ProjectModel`, stable project/page IDs,
selection and fresh session generation. The configured creation bounds remain
2–10 pages; imported collections are not truncated. Types remain Cover,
Illustration, Letter and End Page. Legacy conversion remains version-aware in
Foundation. Private retained text/prompts are not copied into presentation data.

| Actual route | Authoritative destination |
| --- | --- |
| Images Write Image Prompt, Ctrl+Alt+P, tray Prompt Writer | `Nexus.open_prompt_writer` reuses `PromptWriterPanel`; explicit IDs call `select_page` after commit. Shortcut/tray show the current writer without creating another instance. |
| Images Edit Letter Text / message request | `open_page_message` → `open_message_page` → `MessagesWorkspace.open_page(id)`; non-Letter explicit targets are rejected. |
| Ordinary Messages tab / hover entry | `open_page(None)`: selected Letter, otherwise last valid Letter, otherwise first Letter; fallback calls `session.select_page`. No Letters shows the existing Add Letter Page empty state. |
| Messages Edit Background Artwork | Its own page ID → `_edit_message_background` → Images. The owner saves before emitting the request. |
| Prompt Writer Page Settings | `_show_prompt_page_settings`: flush all edits, select the target ID, open Images; Images remains the type/settings owner. |
| Forge artwork/message/layout warning | `_handle_readiness_correction` emits the original ID → `_route_forge_correction`; flush before routing. Queued focus verifies session and tab. |
| New button, Command action, menu, standard New shortcut | `start_new_project` → existing `ProjectLifecycle.new_project`. |
| Save menu/standard shortcut, Saved Letters Save/Copy | Existing `SavedProjectsController.save` and `ProjectSaveService`; copy policy remains Foundation-owned. |
| Open menu/standard shortcut, recipient Load, Saved Letters Open | Existing browser → `ProjectLifecycle.open_saved` → read/validate/activate canonical snapshot. |
| Exit button/menu/shortcut/tray/window close | Existing `request_exit`/`closeEvent` → one lifecycle decision and `Nexus.shutdown`. |
| Forge and Messages viewer Next/Previous | Viewer cursor only. Authoring selection, open message and detached prompt targets remain unchanged. |

Hidden Messages can remember a Letter without changing global selection. Only
deliberate Messages entry resolves its fallback. Reorder/rename updates the
visible `Page N of total — name · type` label, retaining identity and prompt body.
Type/deletion behavior continues through Images' canonical operations and owner
recovery. Prompt Writer resets undo only when binding to another page/session.

Removed competing paths: shell duplicate dirty/Forge-refresh connections from
Images, Sound, Messages and Prompt Writer; Forge's legacy-role/generic-message
translation of validation links. The standalone legacy Messages fallback remains.
No serializer, message renderer, palette library or save-decision layer was added.

## Editing, saving and replacement

`Nexus.flush_project_edits` validates the focused field, commits Images' pending
name, saves the Messages binding, persists detached Prompt Writer, then calls
`session.synchronize_workspace`. Failed input/persistence retains the field and
originating target and stops navigation/save/transition. Prompt Writer persistence
failure remains sticky until a successful retry, even after its timer stops.
Modal crop/layout/library/settings dialogs keep their own Apply/Cancel boundary:
common save/output preparation and lifecycle entry focus the open dialog and
refuse to proceed, retaining invalid or unapplied input. They do not scrape or
silently accept those widgets. This also covers tray/programmatic entry that
would otherwise bypass the main window's modal input protection.

Saved Letters continues to capture one canonical snapshot and content revision,
commit through `ProjectRepository`, and complete only for its originating
session/destination/revision. Foundation conflict checking, copy/publication
detachment, original legacy preservation and recovery assignment are unchanged.
Autosave's queued shell completion now also checks session generation, including
A → B → A; an old callback cannot finalize current metadata or mark it saved.

`ProjectLifecycle` remains the one Save/Discard/Cancel and re-entry barrier.
It prepares candidates before activation, rechecks edits after Save and uses
canonical replacement before guarded view restoration. This task does not add a
second decision prompt. Failed/canceled decisions leave valid editors subscribed.

Forge's existing output-preparation hook now calls the same all-editor flush,
including for a detached writer. Its old Prompt Writer-only hook is retained only
for standalone hosts lacking the shell coordinator.

On committed shutdown the existing owners cancel/finish their work and release
media before subscriptions are removed. Embedded Forge now observes a successful
blank navigation with a five-second bound. Failure is reported through Forge's
existing release-error channel and prevents New/Open/Exit or replacing output.
The next retry waits for the same pending unload. Late preview callbacks cannot
reload the retiring view. Removing the redundant Stop before navigation fixed a
reproduced cold-renderer race that canceled the blank unload itself.

The frame controller now ignores construction/destruction events and inspects
children after polishing. Its application event filter is removed only after
shutdown preflight succeeds. This repairs the observed teardown access violation
at the old unconditional `watched.window()` call; canceled shutdown keeps the
controller and editor subscriptions active.

## Events, dependencies, rendering and media

| Change | Effect |
| --- | --- |
| Selection, tab/view zoom, presentation cursor | Resolve visible targets; no authored revision, save or prompt regeneration. |
| Page collection/order/type | Existing identity rules, selectors and affected preview context refresh; retained inactive content stays private. |
| Rename | Labels and actual output page labels change; exact prompt string/cursor/undo survive. |
| Artwork/GIF/crop | Affected artwork/thumbnail/composition refresh; no message/prompt replacement. |
| Message text/format | Own message composition changes; no image-prompt rebuild. |
| Text-area geometry | Shared composition updates; future background-prompt context becomes stale, old text stays exact. |
| Manual prompt text | Canonical dirty state only; no Forge scheduling or Messages composition build. |
| Shared prompt choices / override | Compare effective composer inputs; a page palette override shields that page from shared palette changes. |
| Curtain/readability setting | Render-dependent state updates; unrelated artwork prompts do not become stale. |
| Sound playlist/volume | Existing Sound project mutation and Forge configuration; no prompt generation or per-page soundtrack restart. |
| Save/replacement | Revision-aware saved state; new session generation invalidates old ownership. |

Forge owns `capture_render`, `validate_render`, `build_render_to`,
`render_is_current`, and `SavedProjectPreviewAdapter`. The new derived
`render_content_key` excludes private prompts. Render targets capture each page's
type/name/artwork/message/overrides/order plus project metadata, shared settings,
presentation, sound and recovery validation dependencies. Relevant mutation
counters reject even changed-then-reverted inputs. Private prompt edits alone do
not invalidate an otherwise usable in-flight composition.

Messages uses that adapter and keeps the last usable generation during updates
or failed external replacement. Its one-second check watches only references in
the current snapshot, requires two stable changed stamps, then validates bytes.
Images' active-tab check uses file change evidence plus successful decode and
before/after evidence. Missing/incomplete same-path replacements retain cached
previews and explicit out-of-date warnings. Timers stop on release; callbacks
from a closed/replaced session have no authority. Final output retains Forge's
resource/hash validation. No entire-library watcher or garbage collector exists.

Sound's existing owner hooks stop audition when leaving Sound. Messages' combined
viewer is muted; Forge uses its existing visibility gate and single soundtrack.
No audio is started by thumbnail generation. Playback/navigation does not change
editor targets. Released generated directories can be removed; original assets,
saved versions and reusable libraries are not removed by view cleanup.

Measured bounded checks: five private prompt edits caused **zero** Messages build
starts; five repeated A/B selection cycles produced **ten** selection events,
unchanged authored revision and no added listeners/writer instances. The frame
test creates/disposes twenty controllers. Repeated shared-preview release removes
the retired generation and allows a new preview. These are bounded checks, not
an unlimited memory or performance guarantee.

## Verification and reproduction

Runner `C:\Temp\ls9\run.py` configures `ApplicationPaths.for_runtime` before
imports, initializes the isolated workspace, changes into it, isolates QSettings
as INI, and calls `unittest`. The code root is supplied separately. To reproduce
without that helper, run equivalent Python setup before loading the test modules.

```powershell
$env:LETTER_SMITH_PROFILE_ROOT='C:\Temp\ls9\fresh-verification'
$env:QT_QPA_PLATFORM='offscreen'
$env:QTWEBENGINE_CHROMIUM_FLAGS='--disable-gpu'
$env:PYTHONDONTWRITEBYTECODE='1'
$env:TEMP='C:\Temp\ls9'; $env:TMP=$env:TEMP
$python='C:\Users\Oluwatola Ayedun\Desktop\SmithLetter\.venv\Scripts\python.exe'
& $python C:\Temp\ls9\run.py (Get-Location).Path test_ui_synchronization
```

Earlier runs are retained here to distinguish baseline, intermediate failures and
later repairs. Counts overlap and must not be added into a unique-test total.

| Log under `C:\Temp\ls9` | Actual outcome |
| --- | --- |
| `baseline.log` | 87 tests, 4 failures/1 error, 112.956s. Five pre-existing immersive-layout fixtures: Mock margins, obsolete external Images preview/aspect assumptions, obsolete size policy, fullscreen visibility. |
| `owners2.log` | Images, Prompt Writer, Forge render, shared preview: 66 passed, 105.710s. |
| `foundation1.log` | Project model/session/repository and Foundation consumers: 96 passed, 9.929s. |
| `combined1.log`, `combined2.log` | Initial integrated tests exposed fixture errors (duplicate collection of imported tests, invalid layout-return expectation, recipient workflow and empty-state visibility); corrected. |
| `final-combined.log` | 51 tests, 3 failures, 246.447s; all 16 Command Settings tests passed. Remaining failures were name-edit signal simulation, artwork-only recipient entry and asynchronous viewport sampling. |
| `final-routes.log` | 36 tests, 1 viewport-sampling failure, 125.123s; four integrated routes and thirteen Forge output cases passed. |
| `owners-final.log` | 92 tests, 1 outdated Messages label assertion, 169.620s. Real settled viewport, Images, Prompt Writer, Forge render and shared previews passed. Label expectations were updated. |
| `acceptance-final.log` | Interrupted by Windows access violation during later window teardown at `window_chrome.eventFilter`; no full-suite pass. The five integrated route/media tests had completed. |
| `cleanup-final.log` | 41 tests, 1 failure/1 error, 179.969s. Actual media teardown exposed canceled blank unload; its retained window interfered with a subsequent hover test. |
| `release-diagnostic.log` | Reproduced the media teardown failure alone: 1 test/error, 26.147s. Recorded URL/load signals exposed Stop canceling the new blank navigation. |
| `release-single-navigation.log` | After removing Stop: actual media handoff/teardown, refused-release recovery and hover case all passed: 3 tests, 25.929s. |
| `integration-verified.log` | UI Synchronization, Command Settings, Saved Letters UI, immersive layout, shared Messages previews, repeated frame disposal and Forge session output: **64 passed**, 370.116s. This run includes the release fixes; the modal-entry guard was added afterward and is checked separately. |
| `owners-verified.log` | Final modal/fallback checks, Command Settings, Messages, Images, hover, Sound project/playback and browser viewer: **120 tests, 119 passed, 1 failure**, 202.761s. The failure sampled browser crossfade volume before its last animation frame after a fixed 650ms sleep; changed to wait for the actual audited handoff. All native Sound lifecycle tests passed, including crossfade/repeat subprocesses. |
| `viewer-verified.log` | Complete browser viewer suite after the deterministic handoff wait: **10 passed**, 61.968s. Includes portrait/landscape/window layouts, ZIP/single-HTML output without source files, independent texts, GIFs and playback ownership. |

Exact command for the combined run:

```powershell
& $python C:\Temp\ls9\run.py (Get-Location).Path test_ui_synchronization test_command_settings test_saved_projects.SavedProjectUiTests test_command_immersive_layout test_message_shared_preview test_window_sizing.WindowSizingTests.test_window_controller_only_observes_live_widgets_and_disposes_repeatedly test_forge_session_output
# owners-verified.log, separate fresh profile
& $python C:\Temp\ls9\run.py (Get-Location).Path test_ui_synchronization.SynchronizationTests.test_modal_layout_keeps_unapplied_input_when_save_new_or_exit_is_requested test_ui_synchronization.SynchronizationTests.test_fallback_warning_routes_and_bounded_subscriptions test_command_settings test_messages_workspace test_images_workspace test_tab_hover_switch test_sound_project_playback test_sound_playback_lifecycle test_page_viewer
# viewer-verified.log, separate fresh profile
& $python C:\Temp\ls9\run.py (Get-Location).Path test_page_viewer
```

The runner's isolated setup is reproducible from these statements before loading
the named test modules (`code` is the absolute source checkout):

```python
import os, sys, unittest
from pathlib import Path
sys.path[:0] = [str(code), str(code / "test")]
from project_paths import ApplicationPaths, configure_application_paths
paths = ApplicationPaths.for_runtime(code)
configure_application_paths(paths)
paths.initialize()
os.chdir(paths.workspace_root)
from PySide6 import QtCore
QtCore.QSettings.setDefaultFormat(QtCore.QSettings.IniFormat)
QtCore.QSettings.setPath(QtCore.QSettings.IniFormat, QtCore.QSettings.UserScope, str(paths.settings_root))
result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromNames(modules))
```

Expected synthetic failure logs and offscreen Chromium GLES fallback warnings are
not themselves unittest failures. Earlier failed/interrupted runs are not claimed
as clean passes. Tests use real canonical persistence and actual widgets/renderers;
dialog choices and explicit failure injection are mocked where necessary. The
real QMediaPlayer handoff is muted, so it proves ownership/state, not audible quality.
Browser playback controller cases audit requested playback in the real JavaScript
viewer; these are separate from native backend tests and audible browser acceptance.

## Native Windows evidence

Actual pointer/keyboard checks used the computer-use skill and two isolated Nexus
launches, at 100% (1200×850 logical) and 125% (1000×760 logical / 1250×950 pixels).
Eight inspected captures are in [ui-sync-evidence-2026-10-17](ui-sync-evidence-2026-10-17/):

- `images-letter-b-100.png`, `prompt-writer-letter-b-100.png`:
  explicit B navigation, real detached writer and Unicode manual input.
- `messages-reordered-letter-b-100.png`: B moved to position 2, renamed, correct
  independent message/background and shared combined preview.
- `images-1000x760-125.png`, `messages-formatting-overflow-125.png`,
  `prompt-writer-125.png`: scaled labels/selection, accessible formatting strip
  via its existing horizontal scroller, and detached window fitting the display.
- `new-project-empty-125.png`, `prompt-writer-new-project-125.png`: successful
  fresh classic project, empty artwork/prompt and the existing writer rebound to Cover.

Native 100% workflow: B → Messages final line → Ctrl+Alt+P → manual
`"  Native B manual prompt — café 雪\n\n  "` → Page Settings → move B twice → rename
→ Messages → New Cancel → further editing → Exit Cancel → Exit Save. The canonical
saved generation `110eb9eae2ae4e98`, project
`32b8351c-77fd-4d3a-b9ba-dbc237e48770`, retained five pages, B ID
`3c80dc45-306b-475f-8eb3-839fbbf9c0e3`, its new name/order, the exact decoded prompt
including trailing whitespace, and the edit made after cancellation. A's historical
prompt remained unchanged. The native process exited through the production path.

Native 125% workflow: inspect Images/Messages/Prompt Writer, choose classic New,
discard only the synthetic active draft, enter the new recipient, reopen the
existing writer, then Save/Exit. New project
`0eb71a1c-c4f8-4c95-b2d2-33aba7cf9b79` had the four correct empty pages and no
publication binding. Both explicit synthetic saves remained in the isolated library.
No old soundtrack/prompt/artwork was inherited. Both helper results recorded
completed shutdown and no provider browser. Screenshots predate the final cleanup
guards; those guards do not change their rendered controls.

## Prompt 10 boundary

Final Integration & Testing must run the broad serial suite, supported release
builds and native acceptance matrix on this branch. The historical Forge handoff's
legacy image-animation geometry assertions were not rerun here. The prior Command
handoff's intermittent native audio-stop stall needs extended combined stress
checking; a successful bounded handoff is not evidence that every backend/device
race has been eliminated.

Unavailable/unrun here: macOS execution, Windows/macOS packaged builds, audible
device quality, sustained memory profiling, all display/scaling combinations and
external publication. Native screenshots and offscreen WebEngine tests are distinct
evidence. This task does not certify the full redesign or a release.

## Completion

- **UI & Synchronization implementation: Complete** on the integrated source branch.
  No missing prerequisite or unconnected control remains in the implemented routes.
  Broad release/platform acceptance remains Prompt 10 work.
- **Combined workflows verified:** `ed5b7258f6c4bf9ca859cef284b7f7bea9754bf5`
  captures the tested implementation. The 64-test combined run predates only the
  modal guard and final test refinements; the subsequent 120-test owner run checked
  that guard and the exact ten-event assertion. Its sole crossfade timing assertion
  was corrected and all ten browser viewer tests then passed. The earlier 96-test
  Foundation run passed. Native five-page editing, canceled transitions, Save/Exit
  and fresh-project rebinding were checked separately at 100% and 125%.
- **Ready for Final Integration & Testing: Yes.** Use `codex/ui-synchronization`
  in this integrated worktree, not the preserved primary checkout. No implementation
  blocker remains in the tested workflows.
- **Known limitations:** no remaining defect reproduced by the final focused
  checks. The historical intermittent native audio-stop issue is not proven
  eliminated by bounded tests; Prompt 10 must stress it. Historical legacy
  animation-layout assertions still need full-suite classification. macOS,
  packaged builds, audible device/browser quality, all scaling combinations and
  sustained resource profiling are unrun acceptance checks, not passing evidence.
