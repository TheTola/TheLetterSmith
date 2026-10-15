# Prompt Writer page targets — implementation handoff

Completed 2026-10-15. Prompt 4 builds, edits, saves and restores independent artwork prompts for canonical page IDs, including repeated types. This supersedes the earlier partial palette-only report.

## Provenance and isolation

- Original checkout: `C:\Users\Oluwatola Ayedun\Desktop\SmithLetter`, branch `main`, starting HEAD `6d015d2e1d93d4773dc99df6ac2a2eca31bf52c1`. Existing uncommitted foundation, publishing, release, diagnostics, requirements, settings and tests were preserved. This task did not edit or commit the original checkout.
- With the user's authorization to choose the baseline, documented foundation files were copied and hash-compared in a managed worktree. Only the foundation startup migration removal was carried into `Main.py`; unrelated startup edits and private runtime data were excluded.
- Foundation snapshot: `7b988f735394213cd01dfadddd60e5e8afeed246`, also named by `codex/foundation-for-prompt-writer`. Palette correction: `490a688`.
- The user subsequently authorized the missing foundation contracts. `5156560` adds recovery, shared resolution and artwork/text geometry; `5163650` adds coherent batch capture and order-revision guards. These extend existing schema-3 objects and APIs, without another persistence system.
- Feature branch: `codex/prompt-writer-page-targets`. Worktree: `C:\Users\Oluwatola Ayedun\.codex\worktrees\prompt-writer-page-targets\SmithLetter`.
- Tests configure a unique `LETTER_SMITH_PROFILE_ROOT` and `ApplicationPaths` before application imports, and use synthetic temporary projects. The original Python environment is used only as an interpreter. No private project, palette, browser profile or credential data was copied. An early baseline import changed only the isolated checkout's settings; that generated change was removed. Final Git state contains no runtime settings changes.
- No remote push, publication, external image generation or new dependency.

## Changed paths and public integration

| Paths | Responsibility |
| --- | --- |
| `PromptWriterPanel.py` | Page selector; shared style, page overrides and editable output; deliberate builds/recovery/batches; palette interaction; exact text and lifecycle handling. |
| `prompt_writer_project.py` | Composition using existing libraries/assembler; canonical models; guarded per-page batch outcomes. |
| `Nexus.py` | Narrow launcher hooks and page-settings navigation; provider sign-in becomes an explicit image-service action. |
| `Image_tab.py` | Narrow launcher bridge passes the selected canonical page ID. |
| `project_model.py`, `project_session.py` | Authorized foundation extensions described in `foundation-contract.md`. |
| `test/test_project_model.py`, `test/test_project_session.py` | Recovery, geometry, shared resolution and mutation-time order guards. |
| `test/test_prompt_writer_pages.py` | Five-page composer/widget/persistence acceptance and batch lifecycle checks. |
| `test/test_prompt_writer_hardening.py`, `test/test_page_layout.py`, `test/test_window_sizing.py` | Updated canonical-state regressions; palette, editing, theme, Images and launcher coverage. |
| `docs/foundation-contract.md`, `docs/prompt-writer-page-targets-handoff.md` | Contracts and this handoff. |

`PromptWriterPanel(parent=None, project_root=..., page_id=None)` and `panel.select_page(page_id)` select a canonical target. `Nexus.open_prompt_writer` and `toggle_prompt_writer` accept the optional ID for new and cached windows. No-argument shortcuts/tray requests remain supported. Images resolves its adapter key through `page_id_for_key(root, key)` before calling the launcher. Adapter keys, positions and names never own prompt data. Initial controller projection is refreshed before editing, preventing duplicate pages after a legacy count change.

`page_settings_requested(str)` returns to existing Images page controls. `provider_requested()` opens the existing optional service connection. Building prompt text never opens sign-in or submits a request. Existing copy, service approval and image-import paths remain; delayed image approval retains its initiating target token. No new image-generation implementation was added.

## Models, state and lifecycle

| Canonical type | Existing template | Fresh composition |
| --- | --- | --- |
| `cover` | `cover` | Dedicated decorative opening composition and visual identity. |
| `illustration` | `letter` | Subjects, characters, scenery and action; artwork-only, without a message area. |
| `letter` | `wall` | Illustrated background with local readability in its text region; no message body in the prompt. |
| `end_page` | `back` | Dedicated closure and cover continuity, explicitly requesting “The End” in the artwork. |

The last position does not imply End Page. Repeated End Pages work independently. Fresh prompts adapt library fixed-four wording to the current count and use `prompt_geometry` for pixel dimensions, orientation and aspect ratio. Useful quality, framing, margin, border and selected style guidance is retained. Unselected style/framing bias is not added. Historical output is never rewritten.

`prompt_settings` holds shared subject/type/palette/global direction/checks and resolved wording. Each page holds directions, overrides, options, canonical template, resolved inputs and exact text. Removing an override restores inheritance without changing the shared value. Historical library choices remain visible. Existing guidance controls and reusable library paths are retained. Add Palette distinguishes Project Style and This Page; typing is independent of selection and activation follows successful library persistence. New Project clears active content, not reusable palettes.

Edits update the canonical model immediately; debounced saves call `ProjectSession.save`. Selection, close and project replacement cannot flush a stale whole-window snapshot over another page/project. Restoration blocks editing signals. Session events refresh the panel and invalidate old-session pending work. `ProjectRepository` owns saving; the legacy prompt file is only a foundation-owned compatibility view. Exact Unicode, whitespace and historical CRLF survive save/load/copy; Qt normalizes displayed newlines without writing that display back until an actual edit. Semantic highlights use display selections, preserving text, cursor and undo history. Proofreading remains an explicit project/page direction action, never an automatic load/close/build rewrite.

Style/layout/type/order changes mark output stale while preserving text. Rebuilds require replacement confirmation and use `replace_prompt`; Restore Previous Prompt restores exact output/resolved snapshots while retaining current controls. Shared wording resolves once through `resolve_prompt_components`; explicit reroll marks existing output stale without rebuilding it.

Selected/all-page batches use `capture_prompt_batch` for a coherent detached snapshot and original-page tokens. `apply_result` checks session/project/page, prompt/type/message/design/shared versions and order changes, including reverted edits. Selection alone is harmless. Each item reports success, failure, skip or conflict. Cancellation retains previous output; failed/conflicting pages can be retried without replacing successful ones. Save failures remain visible.

## Messages/layout contract and boundaries

Only `message.layout` geometry enters composition; HTML and resources are never copied into prompts. Empty layouts use the existing responsive centered viewer panel. Explicit normalized/pixel rectangles and `presentation.artwork_output` are defined in the foundation contract. Ambiguous historical layouts remain saved but fresh builds fail with an explanation instead of guessing.

This branch does not implement Messages or Forge. Existing guards still prevent legacy export of independent messages and custom text geometry. Empty canonical drafts save and show a disabled builder; page creation belongs to Images/foundation, whose compatibility controls currently require 2–10 pages. No unsupported count is truncated. These are downstream editor/viewer boundaries, not alternate prompt stores.

## Validation

Before feature edits, the verified snapshot passed **62 foundation** and **36 Prompt Writer** tests. The palette change passed **38 Prompt Writer** tests. The initial authorized contract extension passed **64 foundation** tests. Tests assuming fixed slots, automatic proofreading and the legacy save file were updated to assert required canonical behavior. Actual integration failures in initial ID projection and theme refresh were fixed.

Final combined run: **119 tests passed**. Coverage uses the actual composer, model, repository, widgets, keyboard/mouse palette interaction and Nexus → Images launcher. The five-page fixture has distinct Letter regions, directions, palettes and output markers. It covers recovery, reorder/rename, repeated endings, last-page Illustration, removed library choices, shared staleness, exact copy/save/load, image/message edits, cancel/conflict/failure/retry, project replacement and reusable libraries. Existing page-layout tests also exercised synthetic GIF manifests and two/ten-page export/restore; this is not native animated-media playback acceptance.

Run from the feature worktree in PowerShell:

```powershell
$env:LETTER_SMITH_PROFILE_ROOT = Join-Path $env:TEMP ('lsp4-' + [guid]::NewGuid().ToString('N').Substring(0,8))
$env:QT_QPA_PLATFORM = 'offscreen'
$env:PYTHONDONTWRITEBYTECODE = '1'
& 'C:\Users\Oluwatola Ayedun\Desktop\SmithLetter\.venv\Scripts\python.exe' -c "import sys,unittest; from pathlib import Path; from project_paths import ApplicationPaths,configure_application_paths; configure_application_paths(ApplicationPaths.for_runtime(Path.cwd())); sys.path.insert(0,'test'); names=['test_project_model','test_project_repository','test_project_session','test_prompt_writer_hardening','test_prompt_writer_pages','test_prompt_writer_legacy_removal','test_page_layout','test_window_sizing.WindowSizingTests.test_prompt_writer_button_toggles_one_instance_and_show_requests_focus','test_window_sizing.WindowSizingTests.test_prompt_writer_images_launcher_targets_each_page_without_provider_sign_in']; result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromNames(names)); sys.exit(not result.wasSuccessful())"
git diff --check
```

`git diff --check` passed. Production Qt widgets were rendered and visually inspected at **1000 × 700**, with long editable text, batch results and collapsed/expanded controls. Registered Segoe UI fonts avoided the offscreen missing-font fallback. The editor remained visible and scrollable. Evidence: `prompt-writer-collapsed.png`, `prompt-writer-expanded.png`, and the earlier palette panel/dialog renders under `C:\Users\Oluwatola Ayedun\.codex\visualizations\2026\10\13\01a13763-a0d5-7e40-b537-d7c971470ba7`.

These are offscreen production-widget checks, not native desktop manual acceptance. No packaged build, external model artwork, publication or sibling-branch/full-release acceptance was performed.

## Sequential integration

1. Use verified foundation snapshot `7b988f7` or reconcile its equivalent on the destination. Integrate authorized contracts `5156560` and `5163650` before feature consumers. Do not duplicate schema or migration systems.
2. Integrate palette commit `490a688` and the completed feature commit containing this report. This branch already includes all dependencies. Resolve only the narrow Nexus/Image launcher hooks against sibling edits.
3. Preserve Messages' canonical geometry fields. The page-aware Images/Forge tasks own their creation/layout/export support; keep compatibility guards until those consumers support the geometry and independent messages.
4. Rerun the command above on the merged checkout, then perform native desktop and packaged acceptance. No merge into the user's dirty checkout was attempted.

- Feature implementation: **Complete** — page-aware editor, all four models, exact persistence, recovery, shared resolution, scoped palettes and guarded batches are implemented and verified on this branch.
- Ready for sequential integration: **Yes** — include the foundation extensions and narrow shell hooks; no remaining Prompt Writer blocker.
- Cross-feature verification: actual Images/Prompt Writer launcher, shared page controller, canonical persistence and legacy export/media regressions passed. Merged sibling features, page-aware Messages/Forge rendering, native desktop and packaged acceptance remain integration work.
