# Images page workspace handoff — 2026-10-16

## Provenance and isolation

Branch: `codex/images-page-workspace`.

Starting foundation commit: `5daa5e3b29bf1b6ca3019f56f1bb9c0d2aca6954`.
Its tree is `965ba54e71dc8ea8318d9c3e3cb9fb178e1d65bb`, identical to the
Prompt Writer foundation snapshot `7b988f735394213cd01dfadddd60e5e8afeed246`.
The earlier foundation documents describe an uncommitted state; these later
snapshots resolve that starting-revision blocker. The initial Images implementation
did not copy sibling feature commits. Foundation contract, migration, verification and handoff documents
were read, and the real model/session/repository APIs were verified.

Worktree: `C:\Users\Oluwatola Ayedun\.codex\worktrees\images-page-workspace\SmithLetter`.
The feature worktree started clean. The primary checkout at
`C:\Users\Oluwatola Ayedun\Desktop\SmithLetter` started at `6d015d2e` with
foundation and unrelated dirty work, all preserved. Its earlier Images blocker
report is superseded by this feature handoff, not evidence of merged changes.

Python: the primary checkout's `.venv\Scripts\python.exe`; dependencies were not
installed or changed. Tests and launches used distinct temporary runtime profiles,
explicit `configure_application_paths(ApplicationPaths.for_runtime(...))`, and
INI QSettings under the profile before application imports. Setting only the
environment variable is insufficient for module-level test imports. Initial
test normalization added an empty provider preference in this disposable
worktree's `settings.json`; that incidental change was removed. No live projects,
private fixtures, external generation, publication or remote push were used.

## Changed paths and ownership

| Path | Change |
| --- | --- |
| `images_workspace.py` | Production `ImageTab`, `PageNavigator`, selected artwork controls, guarded import worker and crop dialog. |
| `page_artwork.py` | Page-ID import/staging/apply, actual media validation, safe resolution, clear and derived static crop. |
| `Image_tab.py` | Keeps the public `ImageTab` entry point and existing stock/GIF/card components; replaces old full-card grid with the workspace import. Extends selected preview sizing and GIF behavior. |
| `recent_media.py` | Extends existing import history to canonical page IDs; hashed directory names, unchanged legacy history access. |
| `Nexus.py` | Exact-page launchers, one Images preview, responsive workspace geometry, protected New Project control placement, help and lifecycle integration. |
| `test/test_images_workspace.py` | Real five-page model/widget/media/persistence acceptance checks. |
| `test/test_image_animation.py` | Existing media regressions adapted to new controls, plus relevant settings/stock/playback checks. |
| `test/test_page_layout.py` | Existing cross-editor test adapted to canonical page operations. |
| `test/test_window_sizing.py` | Existing Images geometry and Prompt Writer button checks adapted to the selected workspace. |
| `docs/images-page-workspace-handoff.md` | This handoff. |
| `docs/images-workspace-evidence/{populated,empty}-{1.25,1.5}.png` | Four actual shell renders using synthetic artwork. |

The initial Images implementation did not change foundation contracts, sibling
editors, Forge or dependencies. The authorized integration below consumes the
completed Messages and Prompt Writer commits and their foundation extensions.

## Model, operations and lifecycle

`get_project_session(project_root)` supplies the only project/order/selection.
`PAGE_TYPES`, `MIN_PAGES`, `MAX_PAGES`, `ProjectModel.validate()` and foundation
add/rename/reorder/duplicate/type/delete/restore operations remain authoritative.
Add inserts after the selected ID, or appends without selection. All imported
pages remain visible, including counts outside current creation limits.
Deleting and undoing retain source files; clearing artwork preserves messages,
prompts and page identity. Type changes preserve exact prompt text and use the
foundation's inactive-message recovery. Readiness assesses artwork and active
Letter message independently of prompt presence.

Public page operations on `ImageTab`: `add_page`, `rename_page`, `reorder_pages`,
`move_page`, `duplicate_page`, `change_page_type`, `delete_page`, `undo_delete`,
`clear_page_image`, `import_image`. Names and arguments are visible in the module.
`session` and `artwork` expose the shared session and `PageArtworkController`.
The compatibility `set_image_path(index, path)` resolves the current stable
adapter key to a canonical ID once; legacy completion signals remain available.
New `artwork_import_completed(page_id, path, error)` reports canonical ownership.

Imports capture a `WorkTarget` before a file/stock dialog or worker starts, with
`type` and `artwork` revisions. Selection, name and order changes cannot redirect
results. Deleted, replaced or relevant-field-changed targets are rejected.
Validation stages every frame before model changes; animated GIF source bytes
remain exact GIF bytes, including misnamed source files. Static imports use the
existing PNG conversion path. Immutable content-addressed files avoid modifying
another page's source. Replacements reset only the target's artwork placement
and animation defaults. Static crop derives new media; GIF crop is explicitly
unavailable because that operation does not preserve animation.

Page names enter the shared model immediately; pending persistence flushes on
selection, deactivation, restore and shutdown. `SessionChange` reaches widgets
through a Qt signal. Restore refreshes are signal-guarded. `synchronize_workspace`
merges completed legacy writes before page operations capture content, and again
before `project_legacy_views` updates disposable
views, then the shared repository saves. Out-of-range drafts bypass projection
and remain savable through the canonical repository.

Thumbnail keys include session ID, page ID, asset reference and file metadata.
Only changed assets are decoded again. One reusable `ImageAssetCard` owns active
playback; it releases QMovie handles on replacement, selection, reset and close,
and pauses when Images is inactive. Deletion/project replacement clears unused
caches and undo state. Imports cancel by flag and discard staged results, while
thread cleanup joins the native worker before releasing its Python wrappers.
Closing Nexus during import cancels without blocking the UI, defers destruction,
and retries close after the worker finishes. Direct `shutdown()` returns false
until safe. Late prepared media is discarded. Ping-Pong preview uses cached
frame seeks in both directions, retaining the existing forward-play count,
speed, start/pause timing and final-frame semantics; Original/Loop keep their
existing native playback path.

## Shell and sibling integration

`ImageTab.prompt_requested(str)` and `message_requested(str)` always emit the
exact selected page ID. Edit Letter Text is visible/enabled only for Letter.

`Nexus.open_prompt_writer(page_id=None)` retains existing no-argument callers.
It validates the requested canonical ID and calls `PromptWriterPanel(page_id=...)`
or `panel.select_page(page_id)` for the cached panel. There is no legacy slot/count
gate or sign-in requirement for writing prompts. Page settings returns the exact
ID to Images and focuses its Name control. Page types remain owned by Images.
The optional image service opens only through its explicit action.

`Nexus.open_page_message(page_id)` delegates to the completed Messages hook
`Nexus.open_message_page(page_id)`. That hook validates Letter ownership and calls
`MessageTab.messages_workspace.open_page(page_id)`, which flushes the previous
document and binds the requested independent message. Repeated Letters work.
`MessageTab.background_requested(str)` returns to Images with the same ID.

Prompt Writer artwork approval uses `ImageTab.import_image(page_id, path,
target=captured_target)` and `artwork_import_completed(page_id, path, error)`.
The initiating session/revision token survives staging; selection cannot redirect
the import. The legacy indexed adapter remains available for older consumers.
Imported projects above the creation limit use the canonical route successfully.

Shared `Nexus.py` edits also connect the two signals; hide the former external
preview for Images and embedded Messages; allow their workspaces to expand; apply the same rule on
reset and resize; anchor its help icon to the workspace; replace obsolete help;
and move the protected New Project action into the existing help/tool row so it
does not cover the selected artwork. Its global/tray Prompt Writer action still
opens without a page argument. Resolve these small shared-shell edits manually
when integrating other feature branches; do not overwrite their work wholesale.

No Forge rendering hook is consumed. The selected preview is artwork only.
The foundation's output guard still blocks distinct Letter documents and other
unsupported renderer features. No combined message renderer was introduced.

## Initial Images verification and evidence — 2026-10-15

Before this implementation: foundation 62/62 and page layout 8/8 passed.
The original image suite ran 48 tests with two pre-existing failures: utility
button dimensions and populated-thumbnail dimensions. Obsolete reset/grid tests
were replaced with the new workflow; media/card regression checks remain.

Final combined run: **76/77 passed** in 31.959 seconds: Images acceptance **21/21**,
media **47/48**, page layout **8/8**. The sole failure is the previously recorded
`test_populated_thumbnail_frame_follows_image_in_every_theme`: the old card test
expects 150×165 but gets 168×155. It is retained as a visible baseline failure,
not skipped or reported green. The selected preview has its own geometry checks.
Page-layout acceptance now asserts exact prompt text through deletion/undo and
the correct active-page list, replacing old count-widget global invalidation
assumptions. This caught and fixed merging completed Prompt Writer writes too
late to enter a deleted page's recovery record.

Foundation recheck: **62/62 passed**. Four focused shell checks passed, including
protected controls, hidden-page resize, transition resize and real Prompt Writer
button interactions. The full existing theme/tab/dimension matrix also passed.
After the playback/lifecycle changes, the real Prompt Writer button and held
image-worker shutdown checks both passed again. The real three-frame GIF test
observed `0,1,2,1,0,1,2`, two forward plays, speed-scaled durations, unchanged
source bytes and no restart after completion. `py_compile` for all changed Python
files and `git diff --check` passed. Offscreen WebEngine reported GPU-context
warnings; these checks do not establish browser/GPU rendering acceptance.

Reproducible test commands (from the feature worktree, PowerShell):

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
$env:QTWEBENGINE_CHROMIUM_FLAGS = '--disable-gpu'
$env:LETTER_SMITH_PROFILE_ROOT = Join-Path $env:TEMP ('lsi-check-' + [guid]::NewGuid().ToString('N').Substring(0,8))
@'
import sys, unittest
from pathlib import Path
sys.path[:0] = [str(Path.cwd()), str(Path.cwd() / 'test')]
from project_paths import ApplicationPaths, configure_application_paths
paths = ApplicationPaths.for_runtime(Path.cwd())
configure_application_paths(paths)
paths.initialize()
from PySide6 import QtCore
QtCore.QSettings.setDefaultFormat(QtCore.QSettings.IniFormat)
QtCore.QSettings.setPath(QtCore.QSettings.IniFormat, QtCore.QSettings.UserScope, str(paths.settings_root))
result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromNames(sys.argv[1:]))
sys.exit(not result.wasSuccessful())
'@ | Set-Content -LiteralPath "$env:TEMP\lettersmith-images-check.py" -Encoding utf8
$imagesPython = 'C:\Users\Oluwatola Ayedun\Desktop\SmithLetter\.venv\Scripts\python.exe'
& $imagesPython -X faulthandler "$env:TEMP\lettersmith-images-check.py" test_images_workspace test_image_animation test_page_layout
```

Run the same wrapper in a **new process/profile** for:

```powershell
$env:LETTER_SMITH_PROFILE_ROOT = Join-Path $env:TEMP ('lsi-found-' + [guid]::NewGuid().ToString('N').Substring(0,8))
& $imagesPython -X faulthandler "$env:TEMP\lettersmith-images-check.py" test_project_model test_project_repository test_project_session test_foundation_consumers
$env:LETTER_SMITH_PROFILE_ROOT = Join-Path $env:TEMP ('lsi-shell-' + [guid]::NewGuid().ToString('N').Substring(0,8))
& $imagesPython -X faulthandler "$env:TEMP\lettersmith-images-check.py" test_window_sizing.WindowSizingTests.test_prompt_writer_button_opens_one_instance_and_show_requests_focus test_window_sizing.WindowSizingTests.test_close_waits_for_image_import_without_disposing_live_worker
```

Other executed shell cases were `test_shell_layout_all_themes_and_independent_dimensions`,
`test_protected_new_project_stays_clear_on_main_tabs`,
`test_shell_layout_hidden_pages_and_content_do_not_grow_window` and
`test_shell_layout_resize_during_transitions_settles_in_viewport` in that same class.

The actual-shell launcher harness used a QWidget browser stand-in, without
credentials/providers: exact target IDs; selection changes; type/deletion/project
replacement rejection; sole-Letter routing; multiple-Letter refusal; a receiving
page-aware hook; unmapped adapter initialization; and oversized-project rejection
before/after a modal. This establishes routing behavior, not sibling completion.

Native Windows smoke used the production `ImageTab` and an isolated five-page
fixture. Visually inspected populated/empty artwork, a long name, the four-type
Add menu, and small navigator. Actual clicks selected and moved Letter A with
its artwork, added an empty Cover after selection, and keyboard navigation
selected Letter B. Reloaded canonical data retained both distinct messages.
The attempted native resize drag did not resize the window, so native resize
success is not claimed. Automated real-shell sizing covered resizing instead.

Real `Nexus` offscreen renders at Qt scale factors **1.25 and 1.5** used a
900×600 populated window and a 1000×700 empty window (logical pixels). Actual
sizes, device ratios and control containment were asserted, and the saved
screenshots were visually inspected. Account validation/browser access were
isolated. These are after-change renders; no before screenshot is claimed.

- [Populated, 125%](images-workspace-evidence/populated-1.25.png)
- [Empty, 125%](images-workspace-evidence/empty-1.25.png)
- [Populated, 150%](images-workspace-evidence/populated-1.5.png)
- [Empty, 150%](images-workspace-evidence/empty-1.5.png)

## Completed receiver integration — 2026-10-16

Started clean at Images commit `0a73a0efd55ace83bfc7bddaa3ffd16b6dca09ee`.
Merged the verified Messages editor/preview consumer commit
`ea6c1f0a4457a4dff1691cb33b1599ac893120f7` in `d9a4348`. This also includes the
reviewed foundation resource-preservation fix from `1612235`. Integrated Prompt
Writer `f9739b9d22c43d0793504e00e402d0eb435e4841`, including its authorized
foundation contracts `5156560` and `5163650`. These are actual local Git merges;
no sibling working-tree files were copied. The newer Forge/geometry work remains
separate from this Images integration.

Additional changed paths relative to `0a73a0e`:

- Shell/bridges: `Nexus.py`, `Over_Nexus.py`, `PromptWriterPanel.py`.
- Committed Messages implementation: `Editor.py`, `Message_tab.py`,
  `messages_workspace.py`, `message_document_import.py`, `message_resources.py`,
  `message_shared_preview.py`.
- Committed Prompt Writer composer: `prompt_writer_project.py`.
- Completed foundation changes: `project_model.py`, `project_session.py`,
  `project_repository.py`.
- Tests: `test/test_foundation_consumers.py`, `test/test_identity_field_locking.py`,
  `test/test_message_security.py`, `test/test_message_shared_preview.py`,
  `test/test_messages_workspace.py`, `test/test_page_layout.py`,
  `test/test_project_model.py`, `test/test_project_repository.py`,
  `test/test_project_session.py`, `test/test_prompt_writer_hardening.py`,
  `test/test_prompt_writer_pages.py`, `test/test_window_sizing.py`.
- Documentation: this file, `docs/foundation-contract.md`,
  `docs/foundation-handoff.md`, `docs/foundation-verification.md`,
  `docs/messages-feature-handoff.md`, `docs/prompt-writer-page-targets-handoff.md`.

Conflict resolution retained the selected-page Images implementation, Messages'
retained-draft guards, asynchronous Images close handling, current repository
resource preservation, and the completed Prompt Writer contracts. The integration
changes outside those incoming commits are the narrow shell and artwork approval
bridges, updated regression fixtures, two production integration checks in the
existing sizing test file, and this report. No new storage system or dependency.

### Integration verification

The original 21 Images checks passed before merging. **173 distinct selected
checks passed on the integrated source**, across the core and focused reruns:

- Core selection: 168 checks covering model/repository/session/consumers, Images,
  Messages, Prompt Writer, page layout, launcher caching and import-time close.
- Two additional actual Nexus cases verify independent A/B editing and exact
  prompts after reorder/save/reload; and canonical GIF approval on page 12,
  unchanged source bytes, harmless selection changes and stale-target rejection.
- Three additional shell cases cover all themes and independent window dimensions,
  protected New Project placement, and resizing during transitions.

The first core run passed 167/168: one legacy provider fixture still supplied a
slot key rather than the new public canonical ID. It now supplies a real target
token and retains assertions for the legacy fallback. The first all-theme run
expected the removed external Messages preview; the updated check verifies its
absence and the visible embedded workspace. Its final rerun passed in 44.241 s.
The new reorder fixture explicitly selects the page before exercising Move Up.
No production behavior was bypassed to resolve these fixture assumptions.

Use the isolated wrapper documented above. Core modules were:
`test_project_model test_project_repository test_project_session
 test_foundation_consumers test_images_workspace test_messages_workspace
 test_prompt_writer_pages test_prompt_writer_hardening
 test_prompt_writer_legacy_removal test_page_layout`, plus the three existing
launcher/import-close cases. Focused cases in `WindowSizingTests` were:

- `test_images_messages_and_prompts_keep_independent_letters_after_reorder_and_save`
- `test_prompt_writer_imports_by_id_beyond_legacy_page_limits_and_rejects_stale_target`
- `test_shell_layout_all_themes_and_independent_dimensions`
- `test_protected_new_project_stays_clear_on_main_tabs`
- `test_shell_layout_resize_during_transitions_settles_in_viewport`

Local logs: `%TEMP%\lsi-integration-core.log`, `%TEMP%\lsi-final-routes.log`,
`%TEMP%\lsi-layout-final.log`. The latter two supersede their documented earlier
fixture failures. Changed Python AST parsing and `git diff --check` passed.
Offscreen WebEngine emitted GPU-context warnings; those runs do not establish
GPU or Forge/browser rendering acceptance.

Native Windows used the actual Nexus and the five-page synthetic fixture at
1200 x 850, then maximized to 1920 x 1032. Actual UI input opened Prompt Writer
on Letter B without a provider, returned through Page settings to B, opened B's
message from Images, typed `Native B verification`, returned to B artwork, and
opened A's unchanged message. GIF artwork visibly changed frames in Images.
Normal close flushed the draft. The saved result confirmed B alone contained the
native edit, both exact prompt markers survived, no provider was opened, and
shutdown completed. Native helper contention initially delayed input; only the
subsequent observed successful actions are claimed. Evidence harness/result:
`%TEMP%\lsi-native-integration.py` and
`%TEMP%\lsi-native-5ce2028e\native-result.json`.

The primary checkout remained on `1612235`; all 13 originally dirty tracked
files retained their recorded SHA-256 hashes. Unrelated untracked content and
other feature worktrees were not modified. No push, publication or release build.

## Remaining application verification

1. Sequential Forge/geometry integration remains separate. Its output guards are
   retained. Messages currently labels its fallback preview as static, including
   GIF backgrounds; Images continues animated artwork playback.
2. Packaged application, real provider sign-in/generation, publishing and other
   operating systems were not tested. No whole-application release claim.

- **Feature implementation: Complete.** Selected-page Images operations and both
  required exact-ID receiving interfaces are usable and verified on this branch.
- **Ready for sequential integration: Yes.** Merge this branch with its recorded
  receiver/foundation ancestry; reconcile shared shell hunks when adding Forge.
- **Cross-feature verification:** Integrated Images, Messages, Prompt Writer,
  canonical persistence, GIF approval, stale results, navigation, close lifecycle
  and shell sizing were exercised. Full Forge and packaged acceptance remain.
