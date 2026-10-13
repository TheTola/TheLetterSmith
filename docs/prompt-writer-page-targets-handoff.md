# Prompt Writer page targets — partial handoff

Date: 2026-10-13. Prompt 4 remains incomplete. This branch fixes the independent
palette interaction; it does not deliver the page-targeted workspace or batch builder.

## Source provenance and isolation

- Original checkout: `C:\Users\Oluwatola Ayedun\Desktop\SmithLetter`, branch
  `main`, HEAD `6d015d2e1d93d4773dc99df6ac2a2eca31bf52c1`.
- That checkout already contained the uncommitted foundation plus unrelated
  publishing, release, diagnostics, requirements, settings and test changes.
  Its source files were not edited or committed by this task.
- After the user delegated baseline selection, foundation files listed in
  `foundation-verification.md` were copied into a managed worktree. Each copied
  file was hash-compared with its source. Only the foundation-owned startup
  bulk-migration removal was applied to `Main.py`; unrelated startup diagnostics
  were excluded. No active projects, palettes, browser profiles, credentials,
  ignored runtime output, or unrelated dirty files were copied.
- Local foundation snapshot: `7b988f735394213cd01dfadddd60e5e8afeed246`, branch
  `codex/foundation-for-prompt-writer`. It contains 30 foundation paths and passed
  the focused checks below. The copied foundation reports describe their earlier
  uncommitted state; this paragraph records the subsequent snapshot.
- Feature branch: `codex/prompt-writer-page-targets`, worktree
  `C:\Users\Oluwatola Ayedun\.codex\worktrees\prompt-writer-page-targets\SmithLetter`.
  The foundation snapshot is a local candidate integration base. Other feature
  branches have not been moved onto it or tested against it.
- Tests use synthetic temporary projects and distinct short
  `LETTER_SMITH_PROFILE_ROOT` directories. Configure `ApplicationPaths.for_runtime`
  before importing application/test modules, as in the command below. The
  environment variable alone does not redirect legacy import-time settings reads.
  The first baseline command added an empty provider default to the isolated
  checkout's `settings.json`; that generated change was removed. Subsequent
  checks use the configured profile. No original user setting was changed.

## Feature changes

- `PromptWriterPanel.py`: allow typing a new palette value while a built-in is
  selected; keep built-in Update/Remove protected; activate a newly added value
  in the panel only after its library save succeeds. Preserve existing source
  directions and prompt snapshots. Failed saves retain the typed candidate and
  previous active selection.
- `test/test_prompt_writer_hardening.py`: keyboard-driven palette regressions,
  active-selection/persistence checks, and failed-save protection, using the real
  dialog and panel paths.
- `docs/prompt-writer-page-targets-handoff.md`: this report.

The palette still has the existing shared scope. No per-page override editor was
introduced. Reusable values retain the established `user_colors.json` path and
normalization/duplicate rules. The `entries_changed(list)` signal remains
compatible. No new serializer, database, dependencies, or foundation schema was
added. Feature changes do not touch shared shell files.

## Foundation capabilities verified in source

`ProjectModel.get_prompt/update_prompt` preserve exact strings and hold
directions, overrides, options, template IDs and resolved components per page.
`prompt_context(page_id)` returns canonical type/template, actual count/order,
position, shared prompt settings and the raw Letter layout. Canonical mapping:

| Page type | Existing artwork template |
| --- | --- |
| Cover | `cover` |
| Illustration | `letter` |
| Letter | `wall` |
| End Page | `back` |

`ProjectSession.capture_target/target_is_current/apply_result` guard writes by
session, project, page and input versions. `selection` and `project_replaced`
events are available. `ProjectRepository` provides exact-string persistence and
retains committed generations. These APIs are verified prerequisites; the palette
patch itself continues through the existing library/state callbacks and does not
claim to replace the legacy Prompt Writer persistence adapter.

## Required coordinated foundation contracts

Do not remove the existing `prompt_generation` compatibility guard until these
contracts and the actual page-aware builder are implemented and verified.

1. **Previous prompt recovery.** Define a supported page-prompt snapshot/restore
   operation or an explicit owned recovery representation. It must preserve the
   exact previous text and associated resolved inputs, survive save/load, and
   restore only the captured page without reverting unrelated edits. Currently
   `update_page_recovery` is a generic provenance merge, and repository `load()`
   reads only the current generation. Retained disk generations alone do not
   supply the required Restore Previous Prompt interface. This branch does not
   invent recovery keys or parse private generation storage.
2. **Project-wide resolved wording.** Specify the canonical shared resolved
   components and explicit reroll/revision semantics. Migration currently omits
   `resolved_instructions` from `prompt_settings` and preserves it on each page;
   the compatibility projection reconstructs a global value only when all page
   snapshots agree. The feature needs one stable shared resolution for individual
   and batch builds, with no automatic historical-text rewrite.
3. **Output and Letter text geometry.** Messages/Forge/foundation must define
   supported text-area fields, units, defaults and coordinate space plus resolved
   orientation, aspect ratio and pixel dimensions. `prompt_context` currently
   returns an opaque `message.layout`; the five-page fixture uses a bare `width`
   without a complete placement contract. Define how presentation changes stale
   prompts and invalidate captured work: `update_project_data('presentation', ...)`
   currently does neither through the shared-input path. Do not guess geometry or
   add another competing layout representation. Only geometry may enter an
   artwork prompt; message HTML must remain private to the separate text layer.

## Integration work still required

- Images currently calls `Nexus.toggle_prompt_writer()` without a page argument;
  `Nexus.open_prompt_writer()` constructs/focuses the cached panel and performs a
  provider sign-in flow first. The integration owner must pass the canonical page
  ID through both new-window and cached-window paths, retaining no-argument
  shortcut/tray behavior, and make text-only Build Prompt available without a
  provider session. `PageLayoutController` adapter keys must not become ownership
  IDs. Provide page-settings navigation through the existing controller.
- Replace the panel's shared legacy controls with the requested Project Style,
  This Page and editable Generated Prompt areas. Bind all page state to canonical
  IDs and guarded restoration; remove stale full-state writes. Preserve historical
  text exactly and show per-page staleness without rerolling. The palette fix does
  not establish page overrides, inheritance, or scope-aware selection.
- Preserve the four composer models while correcting fresh Illustration/Letter
  semantics and End Page's explicit “The End” instruction. Resolve current output
  and text geometry through the agreed contract. Existing template/format text
  still needs this work; no template changes are claimed here.
- Implement deliberate single-page rebuild/recovery and selected/all-page batches
  with coherent shared snapshots, captured IDs/input revisions, cancellation and
  per-page results/retry. Check order/count inputs as well as target tokens.
- Existing `Nexus.flush_prompt_writer_state()` covers several save/shutdown paths.
  The integration owner must verify selection, save, load/reset and project
  replacement flush ordering; session selection/replacement has no registered
  before-change editor flush hook. Immediate canonical edits may avoid adding one.
- No Messages fields are consumed by this palette patch. Future consumption must
  use only the agreed geometry, never copy a message body into a prompt.
- Run the full requested five-page widget/composer/save/load acceptance matrix
  after these changes. Existing foundation tests alone do not establish it.

## Checks

Use the original checkout's installed Python 3.13 environment as an interpreter
only; run from the feature worktree. Configure paths before loading tests:

```powershell
$env:LETTER_SMITH_PROFILE_ROOT = Join-Path $env:TEMP ('lsp4-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
$env:QT_QPA_PLATFORM = 'offscreen'
$env:QTWEBENGINE_CHROMIUM_FLAGS = '--disable-gpu'
$env:PYTHONDONTWRITEBYTECODE = '1'
& 'C:\Users\Oluwatola Ayedun\Desktop\SmithLetter\.venv\Scripts\python.exe' -c "import sys, unittest; from pathlib import Path; from project_paths import ApplicationPaths, configure_application_paths; configure_application_paths(ApplicationPaths.for_runtime(Path.cwd())); sys.path.insert(0, 'test'); result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromName('test_prompt_writer_hardening')); sys.exit(not result.wasSuccessful())"
```

Before feature edits: **62 foundation tests passed** and **36 Prompt Writer
hardening tests passed**, both in the original working baseline and the isolated
snapshot. The 62 foundation tests were also rerun with early runtime-profile
configuration and passed. These cover actual model/repository/session/consumer
paths, including synthetic five-page state and storage-profile isolation. Existing
negative tests log expected errors; they still report a passing unittest summary.

After the palette changes, **38 Prompt Writer hardening tests passed** using the
configured isolated profile. `git diff --check` passed. An independent source
review of the feature diff found no actionable regressions.

Offscreen Qt rendering was inspected at **1000 × 700** with a **500 × 360** palette
dialog and a **3,598-character** generated prompt. The new color was selected in
both editors; prompt strings remained exact and both windows remained open. With
Segoe UI explicitly registered for the offscreen renderer, the left content and
viewport both measured 439 pixels, and controls/text rendered legibly. The initial
fontless render showed missing glyphs and was not treated as visual acceptance.
Images are `prompt-writer-palette-panel.png` and
`prompt-writer-palette-dialog.png` under
`C:\Users\Oluwatola Ayedun\.codex\visualizations\2026\10\13\01a13763-a0d5-7e40-b537-d7c971470ba7`.
This was an offscreen production-widget interaction/render check, not native
desktop manual acceptance or a five-page feature demonstration.

No external image generation, publication, new framework installation, remote
push, packaged build, media acceptance, or sibling-feature verification occurred.

- Feature implementation: **Partial** — independent palette fix only; page-aware
  workspace, specialized fresh composition, recovery and batches remain.
- Ready for sequential integration: **No** for Prompt 4 as a completed feature;
  coordinate the three foundation contracts and finish the listed implementation.
- Cross-feature verification: foundation model/repository/session/consumer tests
  passed; the merged Images → Prompt Writer → Messages/layout workflow remains
  unverified and incomplete.
