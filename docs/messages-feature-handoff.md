# Messages implementation handoff — Prompt 3

## Baseline and isolation

Started from reviewed foundation commit `1612235e5f5bf0c02e84cead87fc84242d30cac5`,
on `codex/messages-independent-letters`. The managed worktree is
`C:\Users\Oluwatola Ayedun\.codex\worktrees\messages-independent-letters\SmithLetter`.
It started clean. Foundation contract, migration, verification and ownership
documents were consumed; no foundation schema or Forge templates were changed.

The original checkout remains on the foundation commit with its unrelated work
preserved. Hash comparison covered all 42 recorded original files; only the four
reviewed foundation files changed before that commit. Its earlier untracked
Messages blocker report was left untouched. This document describes this branch.

Every validation process used fresh synthetic runtime data beneath an explicit
`LETTER_SMITH_PROFILE_ROOT`, with profile-local Qt settings. No private letters,
provider generation, credentials, publishing, release builds, remote pushes or
sibling commits were used. A test/import startup added one empty provider setting
to this worktree's tracked `settings.json`; its exact sole difference was verified
and restored. No runtime data or settings changes are included in the commit.

## Changed files

| Path | Change |
| --- | --- |
| `messages_workspace.py` | Canonical page selector, one bound editor, guarded autosave/import lifecycle, readability inheritance, combined preview adapter and recovery states. |
| `Editor.py` | Optional bound/embedded mode retaining the existing rich editor, named styles, links, lists, fonts, paragraph controls, proofing and safe undo boundary. Legacy mode remains available. |
| `message_resources.py` | Shared restricted raster loading for the bound editor and existing Qt renderer; no Qt file/network fallback. |
| `message_document_import.py` | Sanitized resource staging around the existing converter, explicit resource commit and temporary cleanup. |
| `Message_tab.py` | Existing feature entry point hosts the workspace, preserves identity fields/locks, routes existing conversion workers, delegates save/restore/shutdown and extends renderer resource/overflow inputs. |
| `Nexus.py` | Plural tab label, explicit page navigation, background routing, retained-draft save guards, and space for the inline preview. |
| `Over_Nexus.py` | Respects the explicit project-details toggle in canonical Messages. |
| `test/test_messages_workspace.py` | 19 production-widget/controller/persistence scenarios. |
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

Preview consumes `ProjectModel.render_snapshot()` and the selected page's actual
`message`, `artwork` and `resolved_settings`, resolving resources through
`session.project_root` and `session.asset_root`. It reuses the existing
`Message_tab._MessageRenderTask` / `_render_message_png` composition path and
cancellation gate; delayed output additionally checks its foundation target.
Switching clears the previous preview immediately. Zoom only scales the display.
Missing backgrounds show a neutral writing-ready state. Source artwork is never
modified or replaced by the composed PNG.

The available renderer uses fixed logical dimensions and text margins. The
foundation stores `message.layout`, `message.settings`, `artwork.placement` and
`artwork.animation`, but does not define text-area coordinate units, bounds,
padding or a page-aware composition API. This branch does **not** invent those
controls. Nonempty custom layout/settings/crop produce an explicit unsupported
preview state. GIF backgrounds are labeled still previews. Fixed-preview overflow
is reported without truncating saved HTML or shrinking fonts; presentation-mode
scrolling remains unverified. Full independent output is still guarded by
`project_consumers.consumer_issue(..., 'output')` pending Forge integration.

Required coordinated work:

1. Foundation/layout owner defines validated text-area position, dimensions,
   padding, units/bounds and inheritance using the existing document fields.
2. Forge supplies the page-aware composition adapter consuming canonical render
   snapshots/resources, artwork crop/animation and presentation-mode overflow.
   Wire this adapter into Messages and expose only its supported layout controls.
3. Sequentially integrate the narrow Nexus/Over_Nexus hooks with other owners.
   Compare Messages and Forge output for A/B with different geometry, crop,
   formatting and GIF backgrounds. Remove output guards only after Forge verifies
   independent documents, resources and presentation behavior.

## Verification — 2026-10-13 through 2026-10-15

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

## Completion report

- **Feature implementation: Partial.** Independent writing, formatting,
  selection, imports, guarded persistence and existing static-preview integration
  work. Required text-area geometry and full crop/GIF/presentation parity await
  the coordinated contracts above.
- **Ready for sequential integration: No for full Prompt 3 acceptance.** The
  isolated implementation can be reviewed, but integration must retain the Forge
  output guards and finish those layout/rendering dependencies.
- **Cross-feature verification:** Actual Nexus navigation/lifecycle and foundation
  consumers were verified. Sibling feature branches, merged Forge output, live
  providers, publishing and packaged platforms were not verified.
