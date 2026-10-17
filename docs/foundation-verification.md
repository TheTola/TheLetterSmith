# Foundation verification — 2026-10-13

This report concerns the shared foundation, not the complete tab redesign or a
packaged release. All fixtures used temporary directories and synthetic content.
No live publishing, private saved-letter migration, credentials, remote pushes,
or pull requests were used.

## Baseline and scope

Starting Git HEAD: `6d015d2e1d93d4773dc99df6ac2a2eca31bf52c1`, branch
`main`. This revision alone does not include the foundation. The historical
verification below was performed with preserved unrelated working-tree changes.
The later commit review and staged-snapshot checks are recorded separately below.
Use the commit containing this report as the shared foundation baseline, as
described in [foundation-handoff.md](foundation-handoff.md).

The authoritative existing paths were inspected: configurable `page_layout.py`
version 1 (2–10 pages), `PageLayoutController`, image manifests and GIF source
handling, rich HTML Messages, Prompt Writer state version 6, `SettingsStore`,
`ProjectStateController`/`ProjectDirtyController`, `ProjectSaveService`,
`SavedLetterRestorer`, `PathTransaction`, viewer generation and shell refresh.
Project/recipient metadata schema 2 and saved-letter envelope schema `"1.0"`
remain separate from new canonical `lettersmith.project` schema 3.

Implemented one model/session/repository contract, explicit legacy conversion,
independent page documents/prompts/artwork, recovery, guarded delayed work,
complete generation commits, draft save/load, runtime profiles, and minimal
existing-consumer adapters. Startup no longer bulk-upgrades the saved library.
The detailed contract and migration rules are in
[foundation-contract.md](foundation-contract.md) and
[foundation-migration.md](foundation-migration.md).

## Changed files

| Area | Task-owned changes |
| --- | --- |
| New foundation modules | `project_model.py`, `project_migration.py`, `project_repository.py`, `project_session.py`, `project_consumers.py` |
| Existing integration | `Image_tab.py`, `Message_tab.py`, `PromptWriterPanel.py`, `page_controls.py`, `page_layout.py`, `generate.py`, `readiness.py`, `project_save.py`, `saved_letters.py`, `project_paths.py`, `command.py`, `Nexus.py`, `transactional_io.py` |
| Startup | `Main.py`: only removal of the startup bulk-library-upgrade call and its explanatory comment belong to this task; its other changes predate this task. |
| New focused tests | `test/test_project_model.py`, `test/test_project_repository.py`, `test/test_project_session.py`, `test/test_foundation_consumers.py` |
| Existing test updates | `test/test_page_layout.py`, `test/test_prompt_writer_hardening.py`, `test/test_saved_letter_activity_order.py` |
| Handoff documents | `docs/foundation-contract.md`, `docs/foundation-migration.md`, `docs/foundation-handoff.md`, `docs/foundation-verification.md` |

Pre-existing changes outside this task: publishing authentication/Pages files,
release scripts/specification/checklists/manifest, `requirements.txt`,
`settings.json`, application-path and publishing tests, `fault_diagnostics.py`,
`https_transport.py`, their tests, `Prompt Writer/`, and existing `.test-output/`.
Their presence is not evidence that this task tested or completed release work.

## Test environment and baseline findings

Commands used the repository's `.venv\Scripts\python.exe` (Python 3.13.13,
PySide6 6.11.1). The system Python initially produced four `certifi` import errors
in 27 application-path tests; the repository environment passed all 27.
Qt tests used `QT_QPA_PLATFORM=offscreen`; browser/GUI suites ran in separate
processes because a combined baseline WebEngine process exited before producing
a test summary. `QTWEBENGINE_CHROMIUM_FLAGS=--disable-gpu` was used for the final
isolated runs.

Before implementation, relevant suites passed: application paths 27, Command
interaction 17, page layout 8, page viewer 1, Prompt Writer hardening 35,
message overlay 13, sound restore lock 10, and the combined save-schema,
persistence-hardening and recipient-restore selection 20.

Six existing failures were reproduced with original HEAD implementations, using
temporary import overrides rather than rewriting the working tree:

| Suite/test | Existing failure |
| --- | --- |
| `test_readiness_editor_paths.test_forge_actions_and_preview_format_follow_each_theme` | Theme button sizes 118×40 versus 125×40. |
| `test_command_immersive_layout.test_maximized_sound_preview_keeps_native_height_cap` | Mock margins cause an `int`/Mock `TypeError` in preview geometry. |
| `test_command_immersive_layout.test_forge_tab_switch_keeps_controls_below_preview_and_accessible` | Full-module execution produces scrollbar value 37 rather than 0; isolated execution passes. |
| `test_image_animation.test_image_utility_buttons_preserve_artwork_and_do_not_overlap_cards` | Utility button size 243×73 versus 308×103. |
| `test_image_animation.test_populated_thumbnail_frame_follows_image_in_every_theme` | Thumbnail size 168×155 versus 150×165. |
| `test_identity_field_locking.test_message_preview_reserves_the_message_tabs_minimum_height` | Reserved height 776 exceeds available height 704. |

These remain outside this foundation's scope. The full existing test suite is
therefore not reported as green. Introduced regressions in image-settings test
stubs, shutdown test stubs, no-op prompt envelopes and restored sound comparisons
were fixed. The former load-time source-timestamp test was intentionally updated
to require unchanged source bytes and a valid working-copy timestamp.

## Commands and recorded results

From the repository root:

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
$env:QTWEBENGINE_CHROMIUM_FLAGS = '--disable-gpu'
$env:PYTHONPATH = 'test'
.\.venv\Scripts\python.exe -m unittest test_project_model test_project_repository test_project_session test_foundation_consumers -q
.\.venv\Scripts\python.exe -m unittest test_project_model test_project_repository test_project_session test_foundation_consumers test_command_interaction test_application_paths -q
.\.venv\Scripts\python.exe -m unittest test_project_repository test_persistence_hardening test_saved_letter_recipient_restore test_saved_letter_restore_sound test_page_layout test_save_schema_migration -q
.\.venv\Scripts\python.exe -m unittest test_saved_letter_activity_order test_small_improvement_smoke test_project_repository test_saved_letter_restore_sound -q
```

The final foundation/shell selection passed **106 tests** (including all **62
focused foundation tests**: model 22, repository 12, session 21, consumers 7).
The restore/schema selection passed **68 tests**; the final persistence selection
passed **88 tests**. These final runs include activation-journal recovery and
reentrant Windows/process locking. Counts overlap; they are not a combined
unique-test total. Additional existing suites were run individually with
`.\.venv\Scripts\python.exe -m unittest <module>`:

| Module | Recorded result |
| --- | --- |
| `test_application_paths` | 27 passed |
| `test_command_interaction` | 17 passed; malformed optional library lookup preserves missing selections and source bytes |
| `test_page_layout` | 8 passed |
| `test_page_viewer` | 1 passed |
| `test_prompt_writer_hardening` | 36 passed |
| `test_prompt_writer_legacy_removal` | 2 passed |
| `test_message_overlay` | 13 passed |
| `test_message_security` | 20 passed |
| `test_sound_restore_lock` | 10 passed |
| `test_saved_letter_loading_overlay` | 3 passed |
| `test_image_animation` | 46 passed; two baseline failures listed above |
| `test_readiness_editor_paths` | 52 passed; one baseline failure listed above |
| `test_identity_field_locking` | 30 passed; one baseline failure listed above |
| `test_command_immersive_layout` | 18 passed; one baseline failure and one baseline error listed above |

`git diff --check` passed. All 19 changed source modules compiled, and the runnable
contract example passed. Release-environment/build messages printed inside application-path tests
come from mocked test actions; no installer or release build was performed.

## Required behavior exercised

| Requirement | Evidence |
| --- | --- |
| Classic/configurable/current migration | Model fixtures cover explicit old role mapping, configurable order/repeated types, absent Wall, current-schema idempotence and unsupported future formats. |
| Five-page example | Repository and service tests use Cover, Letter A, Illustration, Letter B, End Page; reorder Letters, duplicate/edit one, save/reload, compare independent rich HTML, backgrounds, exact Unicode/whitespace prompts and resolved components. |
| Identity and recovery | Rename/reorder/save preserve IDs; duplicates are independent; type changes retain inactive messages; deleted-page resources survive another portable save and restore. |
| Drafts/limits/ambiguity | Empty/unfinished drafts, maximum pages, imported over-limit pages without truncation, repeated names, no Letter, missing resources and unassigned legacy content are covered. |
| Media/resources | Real Pillow PNG and animated GIF fixtures preserve original bytes/playback settings; attached/unassigned resources and selected audio round-trip without copying unrelated libraries. |
| Safe persistence/load | Revision conflicts, interrupted pointer writes, corrupt/unsafe resources, full settings/sound replacement and failed-load rollback preserve prior state and original sources. |
| Interrupted activation | A durable journal causes startup to replay the committed generation before reading mutable views. Tests simulate partial settings/message writes, recover arbitrary five-page API projects, then save/reopen without duplicate pages or changed authored IDs/prompts. Recovery retains the committed pointer. |
| Conflicting writers | Nested same-thread activation is safe; a real separate process is rejected while the writer lock is held. Session synchronization cannot import incomplete activation views. |
| Async/selection | Reorder/selection do not redirect results; deletion/restore, edit/revert, concurrent guarded apply, delayed notifications and A→B→A reject stale work. |
| Session lifecycle | Closed sessions cannot save/apply work; saving retains active model references; restoration serializes with saves and blocks intermediate work. |
| Reset/isolation | New Project clears authored state while retaining libraries, palettes, saved letters/preferences; independent profiles isolate all writable application paths and avoid private source imports. |
| Existing consumers | Real page controller projection, supported equal legacy messages, visible distinct-message/output guards, exact prompt preservation and cache rollback are exercised. |

## Desktop smoke evidence

Launched the real `Nexus` window in a fresh short temporary runtime profile with
four synthetic PNGs and `<p><b>Foundation smoke message</b></p>`. Credentials were
stubbed unavailable; no authentication or external service was used. Through
native Windows interaction:

1. Moved Cover right; the UI retained Cover selection at position 2 of 4.
2. Opened Message and its rich-text editor; the synthetic message rendered.
3. Closed the editor and application normally.
4. Loaded the saved canonical repository afterward: order was
   `illustration, cover, letter, end_page`, IDs remained
   `letter, cover, wall, back`, and rich HTML remained exact.

The inspected committed generation was `d87b8c8e1ce340d7` in that disposable
profile. The shell and editor were visually inspected, beyond launch-only
verification. No message text was changed in this UI smoke. The independent
multi-message workflow was exercised through real APIs/service tests, not a
future selector interface. GPU fallback diagnostics occurred; the windows and
editor remained usable. An earlier longer temporary path exposed an existing
curtain-cache Windows path-length error; use the short profile example in the
handoff. No packaged build, real provider download, real publication, macOS run,
or full manual acceptance was performed.

A final fresh-profile run after activation journaling/locking changes repeated
page reordering and normal shutdown. Canonical generation `9ad3fcd240ae4d72`
retained the reordered IDs and exact message; no activation marker or Python
exception remained. Interrupted-operation recovery was tested by controlled
fixture state and injected write failures, not by cutting power to the computer.

## Remaining consumer work

Independent Messages selectors, page-aware Prompt Writer controls/generation,
and independent per-page rendering are later feature tasks. Unsupported new
configurations remain stored intact and visibly blocked in old consumers.
No feature worktrees were started. Old generations are retained for recovery;
automatic garbage collection is not implemented.

All four foundation phases are complete. The six reproduced baseline UI failures
and future consumer work above remain separate from this foundation acceptance.
The user subsequently requested the isolated foundation commit; see the review
record below before branching dependent worktrees.

Foundation status: Ready for dependent feature tasks.

## Foundation commit review — 2026-10-13

Reviewed the documented foundation paths and staged only those changes.
`Main.py` contributes only removal of the bulk-upgrade call and its two replacement
comments. Its diagnostics additions, all publishing/release changes, user settings,
unrelated dependencies/tests, runtime files, and feature handoffs were excluded.
The original working-tree bytes outside the two review fixes and these foundation
documents were preserved.

Review found a resource-alias defect: an image used both as artwork and as an
embedded message resource was renamed for artwork without retaining the message's
original path. `ProjectRepository.save()` now retains every still-referenced alias.
The added regression in the existing repository suite verifies immediate reload,
exact message HTML/resources, source bytes, and a second portable save.

The staged source was exported to a clean temporary directory, excluding private
runtime/user directories and settings. Tests used the installed repository Python,
separate isolated runtime profiles, offscreen Qt, and disabled bytecode writes.
No excluded untracked diagnostics/HTTPS modules were available in the export.
The 63-test foundation selection and 33-test message security/overlay selection
passed. Logs are under `%TEMP%\lsbase-eb4721a4`; these checks exercise the staged
foundation source rather than relying on mixed working-tree imports. The separate
69-test persistence/restore/page-layout selection and 36-test Prompt Writer
hardening suite also passed. Counts overlap (the repository suite is in two
selections); they are not a unique-test total. Synthetic corrupt/missing fixture
diagnostics were logged as expected; all four processes returned success.
