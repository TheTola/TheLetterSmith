# Contextual footer and editor synchronization — 2026-10-20

## Scope and baseline

Starting branch: `main`, revision `a603d11b7c7cda7dc89d7cfdf77bde3e189d8b6e`.
Windows, Python 3.13.13, PySide6 6.11.1.

Pre-existing uncommitted work in `PromptWriterPanel.py`,
`prompt_writer_project.py`, `prompt_writer_workspace.py`,
`test/test_prompt_writer_hardening.py`, and `test/test_restored_workspaces.py`
was left untouched and excluded from this change.
Additional concurrent Saved Letters/GitHub-library changes appeared during
verification, including separate hunks in `Forge_Tab.py`. They are also
preserved and excluded; only the verified hosting-footer hunks are included.

## Implementation

| Files | Change |
|---|---|
| `Nexus.py` | Contextual status-bar views: canonical page count; assigned song/ordered scrollable playlist; Messages readability controls; Forge's existing hosting/account widget. Command keeps its hidden bar. Only the current footer determines its height. |
| `Forge_Tab.py` | Groups the existing provider/account display for reuse in the footer. Account and export actions remain in Forge; no new account lookup or credentials handling. |
| `messages_workspace.py` | Messages gets paper, opacity, contrast and zoom. Editor gets paper, zoom and Normal Text Color. Removes redundant background button/category/summary; retains the artwork route and existing formatting tools. |
| `floating_messages.py` | Separate contextual control views share the existing preview zoom. Editor title follows page name, project title, then derived Letter label. |
| `Message_tab.py` | Restores title, recipient and publication URL into every identity view under signal guards; detached editing retains focus. |
| `test/test_messages_workspace.py`, `test/test_window_sizing.py` | Regression coverage through real widgets, canonical storage, Sound operations and shared WebEngine preview. |
| `docs/foundation-contract.md` | Records view ownership, identity restoration and transient zoom rules. |

The identity defect was reproducible: `sync_from_disk` blocked the primary
field's signals, so its detached mirror never received the restored value.
Restoration now updates all views explicitly without turning a load into an edit.
User edits still use the existing bidirectional bindings and canonical services.

The zoom defect included loss of typed input during unrelated readability
refreshes: even same-value `QSpinBox.setValue` replaces uncommitted text.
Synchronization now writes only a changed numeric value. Direct entry, both
arrow hit areas and the 25–200% limits feed the same existing transient slider
and WebEngine zoom factor. No authored geometry is changed.

## Verification

Evidence directory: `C:/Temp/lettersmith-context-footer-20261020`.
The repository runner redirects runtime data, temporary files and Qt settings,
audits writes, and blocks external networking. No real library was modified.

Final working-tree command:

```powershell
.venv/Scripts/python.exe release/verify_tests.py --output C:/Temp/lettersmith-context-footer-20261020/final --module test_messages_workspace --module test_readiness_editor_paths --module test_saved_projects --module test_saved_letter_recipient_restore --module test_window_sizing.WindowSizingTests.test_context_footer_tracks_pages_music_and_actual_hosting_view --module test_window_sizing.WindowSizingTests.test_context_footer_zoom_arrows_and_entry_across_themes --module test_window_sizing.WindowSizingTests.test_forge_fits_small_windows_without_scaling_text_or_repeating_layout --module test_window_sizing.WindowSizingTests.test_sound_and_message_fit_without_panel_scrolling --module test_window_sizing.WindowSizingTests.test_images_messages_and_prompts_keep_independent_letters_after_reorder_and_save
```

Result: **110 of 113 test cases passed; three cases failed**, no errors or
skips. Subtest failures are not counted as extra test cases.
`final/results.json` records the exact working-tree state and per-module logs.

The focused patch was additionally tested in the clean baseline worktree with
only this task's eight tracked files applied, excluding all concurrent work:
**33 of 33 tests passed**. Its source files match this change; the concurrent
library hunks are absent from that verification copy.

```powershell
& 'C:/Users/Oluwatola Ayedun/Desktop/SmithLetter/.venv/Scripts/python.exe' release/verify_tests.py --output C:/Temp/lettersmith-context-footer-20261020/focused-isolated-candidate --module test_messages_workspace --module test_window_sizing.WindowSizingTests.test_context_footer_tracks_pages_music_and_actual_hosting_view --module test_window_sizing.WindowSizingTests.test_context_footer_zoom_arrows_and_entry_across_themes --module test_window_sizing.WindowSizingTests.test_images_messages_and_prompts_keep_independent_letters_after_reorder_and_save
```

The working directory for that command was
`C:/Users/Oluwatola Ayedun/.codex/worktrees/context-footer-baseline/SmithLetter`.
The final candidate is the commit containing this report; the separate local
`verified-source-hashes.json` records the verified implementation/test files.

Coverage includes:

- Both identity views, editing/focus, missing values, title updates and canonical
  save/reopen; the strengthened restore assertion failed before the fix.
- Typing `125%` while a readability refresh occurs; that regression failed before
  the fix and passed afterward (`zoom-refresh-before` / `zoom-refresh-after`).
- Both zoom arrows, limits and actual WebEngine zoom across all six themes.
- Page-count add/delete/replacement, singular count and contextual visibility.
- Single song, ordered playlist, reorder/remove/rename, long-title scrolling,
  empty music and existing connected/disconnected Forge status.
- Images/Messages/Prompt Writer targeting after reorder and save.

All three failures were separately reproduced in a clean managed worktree at
the starting revision (`baseline-expanded/results.json`):

| Existing failing case | Baseline / final result |
|---|---|
| `test_forge_fits_small_windows_without_scaling_text_or_repeating_layout` | Preview-height assertions: 24 / 18 failing subcases. Remaining failures at 900×480. |
| `test_sound_and_message_fit_without_panel_scrolling` | Same 61 failing layout subcases, including legacy/hidden-control containment checks. |
| `test_last_keystrokes_detached_prompt_save_open_and_saved_preview` | Same `panel.isWindow()` failure. |

These assertions were not weakened. This is not a clean full-suite or release
verification claim.

The reported user's project was checked using a copied canonical generation,
not by saving into its live location. The real repository loader,
`SavedLetterRestorer`, identity views and editable save/open service preserved
its title, recipient, unassigned URL and messages: **one test passed**
(`private-check.json`). Original pointer/metadata hashes were unchanged during
copying. Private fixture content remains outside Git. A separate synthetic
legacy recipient-restoration test also passed.

Native Windows interaction used an isolated synthetic five-page project:
Images count; Sound empty state; Forge provider/disconnected status; Messages
controls; typed 125%, increase/decrease; detached title/identity/controls; and
actual artwork/text in compact and enlarged WebEngine previews were inspected.
This native session preceded the final footer-height refinement; the final
refinement was checked through the widget runner. Screenshots were inspected
in-session, not added as repository artifacts. Audible playback, fresh
packaging, other platforms and live publication were not tested by this task.
