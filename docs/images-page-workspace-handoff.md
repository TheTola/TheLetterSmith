# Images page workspace handoff — 2026-10-15

## Provenance and isolation

Branch: `codex/images-page-workspace`.

Starting foundation commit: `5daa5e3b29bf1b6ca3019f56f1bb9c0d2aca6954`.
Its tree is `965ba54e71dc8ea8318d9c3e3cb9fb178e1d65bb`, identical to the
Prompt Writer foundation snapshot `7b988f735394213cd01dfadddd60e5e8afeed246`.
The earlier foundation documents describe an uncommitted state; these later
snapshots resolve that starting-revision blocker. No sibling feature commits
were copied. Foundation contract, migration, verification and handoff documents
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

No foundation schema, serializer, registry, message store, Forge renderer,
Messages editor, Prompt Writer internals or dependencies were changed.

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
For page routes it checks `consumer_issue(root, 'prompts')`, projects supported
views, verifies `page_id_for_key`, then uses the existing shared
`PageLayoutController.reload()/select(key)` interface. The target is checked
again after provider/sign-in modal work. The shell retains that shared controller
to avoid dangling session listeners. Imported counts above the adapter limit
receive a clear compatibility message before provider UI; they are not truncated.
Prompt Writer's own type controls and broader page-aware behavior remain owned
by its feature branch.

`Nexus.open_page_message(page_id)` validates Letter ownership before opening.
The baseline Messages editor supports only a sole active Letter, so it uses
`open_message_editor()` only when that sole ID matches. Multiple Letters receive
an explicit integration message rather than opening the wrong document.
The required Messages integration entry point is
`MessageTab.open_page_message(page_id: str)`: flush the previous document, select
that ID through the same session, bind its independent message and open/focus
the existing editor. The shell consumes this hook if present. It is **not**
implemented by the baseline or fabricated as a working feature here.

Shared `Nexus.py` edits also connect the two signals; hide the former external
preview only for Images; allow its workspace to expand; apply the same rule on
reset and resize; anchor its help icon to the workspace; replace obsolete help;
and move the protected New Project action into the existing help/tool row so it
does not cover the selected artwork. Its global/tray Prompt Writer action still
opens without a page argument. Resolve these small shared-shell edits manually
when integrating other feature branches; do not overwrite their work wholesale.

No Forge rendering hook is consumed. The selected preview is artwork only.
The foundation's output guard still blocks distinct Letter documents and other
unsupported renderer features. No combined message renderer was introduced.

## Verification and evidence

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

## Remaining integration requirements

1. Integrate the Messages page-ID entry point and verify independent Letter A/B
   editing end to end. The enabled Images route currently explains this valid
   baseline limitation for multiple Letters.
2. Integrate Prompt Writer's canonical page interface, including imported counts
   outside its legacy adapter limits and removal of its independent type assignment.
   Current supported-count routing is checked against the baseline's real API.
3. Verify the merged Forge, Messages and Prompt Writer branches using the same
   five-page fixture; current Forge output correctly refuses distinct messages.
4. Packaged application, real provider sign-in/generation, publication and other
   operating systems were not tested. No release acceptance is claimed.

- **Feature implementation: Partial.** Page/artwork operations and selected-page
  UI are implemented; multi-Letter text navigation and unrestricted Prompt Writer
  navigation require the receiving feature integrations described above.
- **Ready for sequential integration: Yes, with conditions.** Apply only this
  isolated feature commit onto the matching foundation, reconcile the listed
  shell hunks, then integrate and verify those receiving interfaces before final
  feature acceptance. Do not release this as fully integrated.
- **Cross-feature verification:** Foundation, baseline page adapter, selected
  Prompt Writer routing, safe Messages gating and shell geometry were exercised.
  Independent Messages editing, sibling prompt behavior and full Forge output
  remain integration work.
