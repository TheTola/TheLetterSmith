# Messages implementation handoff — Prompt 3

Updated 2026-10-16: the completed Forge branch at `681a85e` is integrated in this
worktree by merge commit `d22cf41`. Messages now authors the shared geometry
contract and consumes live overflow diagnostics. Earlier partial results below
are retained as history; the completion report describes the integrated branch.

## Baseline and isolation

Started from reviewed foundation commit `1612235e5f5bf0c02e84cead87fc84242d30cac5`,
on `codex/messages-independent-letters`. The managed worktree is
`C:\Users\Oluwatola Ayedun\.codex\worktrees\messages-independent-letters\SmithLetter`.
It started clean. Foundation contract, migration, verification and ownership
documents were consumed. Foundation schema and migration remain unchanged; the
completed Forge implementation and its coordinated geometry extension were merged
without editing its templates or resolver.

The original checkout remains on the foundation commit with its unrelated work
preserved. Hash comparison covered all 42 recorded original files; only the four
reviewed foundation files changed before that commit. Its earlier untracked
Messages blocker report was left untouched. This document describes this branch.

Every validation process used fresh synthetic runtime data beneath an explicit
`LETTER_SMITH_PROFILE_ROOT`, with profile-local Qt settings. No private letters,
provider generation, credentials, publishing, release builds or remote pushes were
used. Initial shared-preview checks consumed an immutable copy of three committed
Forge files outside the repository. The final checks use the actual merged branch;
no unfinished sibling changes were copied. A test/import startup added one empty provider setting
to this worktree's tracked `settings.json`; its exact sole difference was verified
and restored. No runtime data or settings changes are included in the commit.

## Changed files

| Path | Change |
| --- | --- |
| `messages_workspace.py` | Canonical page selector, one bound editor, guarded autosave/import lifecycle, readability inheritance, combined preview adapter and recovery states. |
| `message_shared_preview.py` | Optional consumer of the committed Forge renderer, with guarded background builds, isolated local WebEngine, page-ID navigation, cancellation and file release. |
| `message_layout_dialog.py` | Compact page-local canvas, text position/dimensions, padding and alignment controls; inherited defaults, shared validation and explicit Apply/Cancel. |
| `Editor.py` | Optional bound/embedded mode retaining the existing rich editor, named styles, links, lists, fonts, paragraph controls, proofing and safe undo boundary. Legacy mode remains available. |
| `message_resources.py` | Shared restricted raster loading for the bound editor and existing Qt renderer; no Qt file/network fallback. |
| `message_document_import.py` | Sanitized resource staging around the existing converter, explicit resource commit and temporary cleanup. |
| `Message_tab.py` | Existing feature entry point hosts the workspace, preserves identity fields/locks, routes existing conversion workers, delegates save/restore/shutdown and extends renderer resource/overflow inputs. |
| `Nexus.py` | Plural tab label, explicit page navigation, background routing, retained-draft save guards, and space for the inline preview. |
| `Over_Nexus.py` | Respects the explicit project-details toggle in canonical Messages. |
| `test/test_messages_workspace.py` | 25 production-widget/controller/persistence scenarios, including geometry inheritance, validation, conflicts, retained historical data and restore. |
| `test/test_message_shared_preview.py` | Four real shared-renderer/browser integration cases, enabled when the Forge adapter is installed. |
| `test/test_message_security.py` | Five staging/resource security and portability cases. |
| `test/test_identity_field_locking.py` | Existing worker-test adapters accept the renderer's optional resource/diagnostic arguments; cancellation assertions unchanged. |
| `docs/messages-feature-handoff.md` | This implementation and integration record. |

## Behavior and public integration hooks

- `MessageTab.messages_workspace` hosts `MessagesWorkspace` when the foundation
  session is present. It does not write a global `message.html` or own another
  persisted message store.
- Entries filter canonical `ProjectModel.pages` to `letter` and display actual
  one-based project positions. IDs survive rename, reorder and duplicate names.
  Explicit requested Letter, selected Letter, last valid Letter, then first Letter
  determines the document; the heading shows the actual target.
- `Nexus.open_message_page(page_id) -> bool` is the Images integration hook.
  `MessageTab.background_requested(str)` carries the current ID back to Images;
  `images_requested()` returns there from the empty state. Images owners can wire
  an explicit Write Message action to `open_message_page` without changing storage.
- No-Letter projects show Add Letter Page/Return to Images. Addition uses
  `ProjectModel.add_page` and `MAX_PAGES`; no invisible document is created.
- `Editor(..., save_callback=..., resource_roots=..., embedded=True,
  document_label=..., display_settings=...)` retains the capable editor.
  `flush_document() -> bool`, `dispose_document()` and `bound_error(str)` provide
  explicit lifecycle integration. Unedited source HTML is retained exactly.
- Each edit uses `capture_target(fields=('type','message'), include_shared=False)`
  and `apply_result` against the original page/session. Disk writes coalesce for
  650 ms through `session.save(sync_workspace=False)`. Selection, tab exit,
  restoration and shutdown flush. Disk/conflict failures retain the editor and
  block leaving it; retry or explicit discard is available. An unexpected project
  replacement cannot redirect the retained draft into the replacement project.
  External edits to the same message retain any local draft still awaiting disk
  persistence and require explicit recovery instead of replacing the editor.
- Cursor/selection/scroll are transient per page/session. Rebinding creates an
  explicit undo boundary; undo never reaches another letter. Clear Text only
  changes selected HTML and remains undoable. Type changes retain inactive
  documents through foundation operations. Unassigned migrated text is reported
  without attaching it to any page.
- Imports use the existing `message_import` QProcess conversion path for TXT,
  DOCX, PDF, ODT and HTML. Destination and conversion-loss notice precede
  Replace/Append/Cancel. The original target is captured before conversion.
  Changed/deleted/retyped targets and replaced projects retain the result for
  review; selection changes cannot redirect it. Review remains accessible even
  when the current project has no Letter pages. Applying after a conflict requires
  explicit review of the original target; cross-session apply is disabled.
- Raster resources are bounded, sanitized and staged before the guarded document
  update. Safe local paths are copied beneath `gallery/message_assets`, listed in
  canonical `message.resources`, and persisted by the existing repository. Remote,
  absolute and traversal paths are not loaded. Missing/unsafe import images produce
  review notices. Failed disk saves retain committed resources and the draft.

## Layout, events and renderer boundary

Existing font, paragraph, alignment, list and color controls author the selected
document. Readability changes use `update_page_settings` and
`clear_page_override` for the existing `message_overlay_preset` and
`message_overlay_opacity` keys. The UI labels inherited/customized state and offers
Use project default. Existing shared defaults and other overrides are preserved.
`SessionChange` notifications (`selection`, structure/name/message/design changes,
restoration and `project_replaced`) refresh bindings through a Qt signal. Model
operations invalidate dependent previews/prompts without regenerating prompt text.

On the foundation-only branch, preview consumes `ProjectModel.render_snapshot()` and the selected page's actual
`message`, `artwork` and `resolved_settings`, resolving resources through
`session.project_root` and `session.asset_root`. It reuses the existing
`Message_tab._MessageRenderTask` / `_render_message_png` composition path and
cancellation gate; delayed output additionally checks its foundation target.
Switching clears the previous preview immediately. Zoom only scales the display.
Missing backgrounds show a neutral writing-ready state. Source artwork is never
modified or replaced by the composed PNG.

That legacy path uses fixed logical dimensions and text margins, labels GIFs as
still previews, reports fixed-preview overflow, and rejects unsupported custom
layout/settings/crop. It never truncates saved HTML or shrinks stored fonts.

When the committed `forge_render` module is available, Messages instead uses its
actual generated viewer. `create_shared_preview` imports that adapter; absence of
the module retains the legacy path. Its public contract was verified at Forge
commit `758c0db069c4c632349c6fd6f838f5b925c61b36`:

- `capture_render(session, output_mode="preview")` freezes canonical render inputs.
- `validate_render(render)` supplies warnings and `build_render_to(render,
  destination, seed_sfx=False, cancelled=...)` validates and generates a private
  preview. It never promotes output into the live Play directory.
- `render_is_current(session, render, check_sources=...)` rejects stale generations;
  a separate foundation `WorkTarget` protects the initiating page and session.
- The generated `window.LetterSmithViewer.goToPage(page_id)` and `currentPageId()`
  APIs select and identify the displayed page. Messages uses the viewer's existing
  start action and leaves composition, fonts, animation and scrolling in Forge.

The WebEngine profile is off the record, muted, and restricted to the owned
generation cache plus data/blob URLs. Remote requests and navigation outside that
cache are blocked. Page switches, collapsed previews and tab exit cancel builds
and unload the viewer. Restore/shutdown wait for workers and confirmed browser
unload before releasing generated files. Zoom and resizing change only the view.
Navigation inside the shared viewer does not change the editor's selected page;
the preview label identifies its actual page and states when selection differs.
Missing selected backgrounds retain the neutral writing-ready state. Unsupported
layout still reports the shared renderer's validation error without fake output.

The coordinated extension is now committed and integrated. Its contract is in
`docs/foundation-contract.md`, Shared composition geometry. The Text Layout dialog
uses `resolve_page_layout` to validate version-1 `css_px` canvas size, text box,
per-edge padding, alignment and supported scrolling. It obtains existing values
through the same resolver used by `ProjectModel.resolve_layout` and Forge.
Empty geometry retains responsive presentation until the user explicitly
customizes it. A new custom canvas uses existing artwork canvas dimensions, or
the documented 400×600 example when no canvas exists; no values are persisted on
opening or cancelling the dialog.

Only edited inherited fields become document overrides. Use project default
clears `message.layout` and only the `message_layout` page override, preserving
other settings, authored HTML/resources, artwork and prompt text. Existing opaque
layout data remains intact on open/cancel and exposes the resolver's error.
Invalid boxes/padding cannot be applied. The dialog keeps rejected values visible
for correction. Supported long text scrolls; there is no clipping/shrink toggle.

`MessagesWorkspace.apply_layout(target, geometry, use_default=False)` uses
`capture_target`/`apply_result` with type, message, artwork, page design and shared
inputs. Changed inputs or project replacement reject the original dialog's
commit. It preserves the current editor, updates its binding, and saves through
the existing retained-draft lifecycle. `MessageTab.focus_field(page_id)` now
opens the actual Letter targeted by Forge readiness. These are the only new
entry-point changes in this continuation; Nexus/Over_Nexus need no further edits.

The preview polls Forge's public `layoutIssues()` alongside `currentPageId()` and
shows diagnostics for the displayed page. Messages/Forge composition uses the
same renderer, including crop/GIF behavior. Canonical output now follows the
merged Forge validation path; no manual compatibility guard removal was added.

## Verification — 2026-10-13 through 2026-10-16

Python: existing original-checkout `.venv\Scripts\python.exe`, with isolated
profiles and `QT_QPA_PLATFORM=offscreen`, `QTWEBENGINE_CHROMIUM_FLAGS=--disable-gpu`.
The new tests use actual MessageTab/Editor, ProjectSession and ProjectRepository.
They mock only confirmation choices and intentional failure conditions.

| Final run | Result |
| --- | --- |
| `test_messages_workspace` | 19 passed |
| `test_project_model test_project_repository test_project_session test_foundation_consumers` | 63 passed |
| `test_message_security test_message_overlay` | 38 passed |
| `test_named_text_style_editor test_named_text_style_document test_named_text_styles test_named_text_style_preview` | 42 passed |
| Nine targeted `test_identity_field_locking.IdentityFieldLockingTests` cases below | 9 passed |
| `git diff --check` and changed-Python AST parsing | Passed |

The nine targeted cases cover loaded identity locks, central tab-switch snapshot,
protected-state persistence, stale Prompt Writer avoidance, asynchronous snapshot,
Images tab release/refresh, project-path resolver, newest-render-only commit and
busy-render restoration failure. The two render fixtures initially rejected the
new optional keywords; updating their signatures restored all original assertions.
These final selections total **171 distinct tests**. Separate earlier editor
checks overlap and are not added to this total. The full repository suite and
packaged/macOS application were not run; foundation-documented baseline failures
are not claimed fixed.

Workspace tests cover five canonical pages, actual positions, explicit B entry,
Illustration fallback, rapid typing/switching, rich formatting restoration,
page-local undo, duplicate/delete/restore, import modes and stale results,
Clear Text preservation, type retention, no-Letter/add/reset, complete persistence,
embedded resource portability, missing resources/background, Unicode overflow,
readability inheritance, retained save failures and protected replacement drafts.

Native Windows check used the real MessageTab with a synthetic five-page project:
keyboard Down/Return selected B; distinct typed A/B suffixes remained independent;
the actual renderer switched between the blue and brown backgrounds. Maximizing
from 1200×800 to screen size preserved the editor/preview arrangement. Closing
flushed both rich Unicode documents, verified in the saved canonical result.
This establishes the feature widget's native behavior, not finished Forge parity.

An additional actual Nexus offscreen launch verified B entry, inline/shared
preview visibility, project-details toggle, leaving-tab persistence, explicit A
navigation, Edit Background identity routing and close-time B persistence.
GitHub credential restoration alone was disabled; local WebEngine requested no
external URLs. Offscreen GPU warnings preclude GPU/browser-rendering acceptance.

For repeatable isolated runs, initialize `ApplicationPaths.for_runtime` with a
fresh `LETTER_SMITH_PROFILE_ROOT` **before importing tested modules**, initialize
those paths, change cwd into that profile, and set Qt QSettings to IniFormat under
its settings root. Add this worktree and its `test` directory to `sys.path`, then
run each table row in a separate process with
`unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromNames(names))`.
Local exact runner/logs: `%TEMP%\lsmchecks-1a7fb1a5\run_checks.py` and sibling
`workspace.log`, `foundation.log`, `message.log`, `styles.log`, `shell.log`.

### Shared-preview continuation — 2026-10-16

- Re-ran `test_messages_workspace test_message_security test_message_overlay`
  against this foundation-based branch: **57 passed**. These overlap the 171
  previous checks and are not an additional 57 distinct cases.
- Ran `test_message_shared_preview` using actual production WebEngine and an
  immutable source overlay containing only `forge_render.py`, `Template.py` and
  `generate.py` from Forge commit `758c0db`: **4 passed**. The cases cover independent
  A/B text, changing GIF frames with unchanged source bytes, long-text scrolling,
  view-only zoom/resize, stale/cancelled builds, project replacement, tab exit,
  restoration/file release, missing artwork, and real layout validation. The
  delayed-build case pauses the real renderer; it does not substitute fake output.
  These tests explicitly skip when `forge_render` is absent.
- Native Windows used the actual MessageTab and the same committed renderer
  overlay. Down/Return selected B from A, its long document scrolled in the shared
  viewer, maximizing from 1280×820 preserved the selected document/preview, and
  normal close confirmed no active or retired generated directories remained.
  The native fixture used five synthetic pages with distinct A/B backgrounds and
  rich Unicode text. No external URLs or private projects were used.
- Final `git diff --check` and changed-Python AST parsing passed. Original-checkout
  hash preservation was checked again before committing this continuation.

Exact local evidence is under `%TEMP%\lsm-shared-a1698c96`: `baseline.log`,
`shared.log`, `check_shared.py`, `native_shared.py`, `native-shared-result.json`, and
the immutable `forge-snapshot` directory. Run the four integration cases with the
existing venv Python and `check_shared.py test_message_shared_preview`; the runner
initializes a fresh profile and Qt settings before importing application modules.
This verifies a specific committed adapter composition, not a merged application.
Offscreen WebEngine emitted GPU-context warnings; native validation ran separately.

### Completed geometry and merged verification — 2026-10-16

- Reviewed and merged immutable Forge commit `681a85e` without conflicts. The
  combined `test_project_model test_forge_render test_message_shared_preview`
  baseline passed **52 tests** before adding controls.
- Final `test_messages_workspace test_message_shared_preview test_message_security
  test_message_overlay test_forge_session_output`: **78 passed**. Workspace tests
  now exercise actual shared previews. Their fixture initializes bundled profile
  assets, and dialog cleanup relies on Qt ownership. Browser diagnostics cross
  the Qt bridge as JSON because direct JavaScript object results were not delivered
  as a usable dictionary in this runtime.
- `test_page_viewer.PageViewerTests.test_real_layouts_zip_and_single_html_work_without_source_project`:
  **1 passed**, including portrait/landscape geometry, independent documents,
  crop, GIFs and portable ZIP/HTML after source deletion. These selections total
  **127 distinct tests**; four preview cases overlap the baseline and final groups.
- An actual Nexus integration harness measured identical normalized text geometry
  in Messages and Forge for A/B with different layouts and crop. It verified
  leaving-tab edit flush, reordering, canonical save/reopen through
  `SavedLetterRestorer`, and empty-project replacement with released preview files.
  GitHub account restoration alone was disabled; all project/editor/render paths
  were production code using synthetic content.
- The same complete Nexus harness passed with the native Windows Qt platform
  (`integrated_check.py --native-check`), including the rendered geometry
  comparisons and save/restore/replacement lifecycle. Its log is
  `integration-native.log`. This is automated native evidence, separate from the
  manual desktop interaction below.
- Native Messages displayed its actual canvas, scrolling notice and new Text
  Layout button. Desktop automation stalled before opening the dialog; this is
  not claimed as a completed manual native-control check. Automated control tests
  above passed independently.

Current runners and logs: `%TEMP%\lsm-geometry-20261016\run_checks.py`,
`merged-baseline.log`, `messages-final.log`, `viewer.log`, `integrated_check.py`,
`integration-short.log`, and `integrated-observations.json`. The shell harness
uses a fresh `C:\Temp\lsm-*` profile: a longer initial profile exceeded Windows
path limits in an existing generated-output temporary path. That path-length
limitation was not changed. Restoration rejected premature Forge fingerprint
captures while the session was suspended; subsequent refreshed output and the
restored documents passed the assertions. The original checkout remains on
`1612235`; final SHA-256 comparison of 42 recorded files found no unexpected
changes. Final changed-Python AST parsing and `git diff --check` passed.
No main-branch merge or push occurred.

## Completion report

- **Feature implementation: Complete on this branch.** Independent documents,
  rich editing/imports, guarded persistence, inherited page geometry, real shared
  composition and scrolling diagnostics are implemented and exercised.
- **Ready for sequential integration: Yes.** Messages and completed Forge are
  integrated here. Preserve unrelated primary-checkout work and reconcile any
  overlapping shell changes when bringing in other feature branches.
- **Cross-feature verification:** Combined Messages/Forge in actual Nexus,
  geometry/crop parity, save/reopen and replacement lifecycle were verified with
  synthetic data. Other sibling UIs, packaged platforms, audible playback, live
  providers and publishing remain outside this acceptance. Manual native dialog
  interaction was limited by the desktop automation failure described above.
