# Parallel feature handoff

The checkout started at `6d015d2e1d93d4773dc99df6ac2a2eca31bf52c1`.
That starting commit alone does **not** contain this foundation. The foundation
was reviewed and isolated for commit on 2026-10-13 at the user's request. Use the
commit containing this handoff and the five foundation modules as the shared
baseline; record its exact hash when creating dependent worktrees. Unrelated
publishing, release, settings, and startup diagnostics work was excluded.
The verification report separates historical working-tree runs from the staged
foundation snapshot checks. The completed shared baseline is `1612235`.
This copy also documents the authorized Sound/foundation/Forge integration on
`codex/sound-project-playback`, after feature commits `b6d7ced` and `684f2fe`.
The primary checkout and its unrelated dirty work remain untouched; this branch
has not been merged into it or into sibling feature branches. See
[sound-project-playback-handoff.md](sound-project-playback-handoff.md) for current
scope, provenance and acceptance limits.

Read [foundation-contract.md](foundation-contract.md),
[foundation-migration.md](foundation-migration.md), and
[foundation-verification.md](foundation-verification.md)
before changing a consumer. Synthetic builders `legacy_fixture` and
`five_page_project` are in `test/test_project_model.py`; the real controller and
consumer checks are in `test/test_foundation_consumers.py`.

## Isolated runtime data

Use a different absolute `LETTER_SMITH_PROFILE_ROOT` for every worktree and test
run. Source worktree separation alone does not isolate application data.

```powershell
$env:LETTER_SMITH_PROFILE_ROOT = Join-Path $env:TEMP ('lsf-img-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
.\.venv\Scripts\python.exe Main.py
```

`ApplicationPaths.for_runtime` in `project_paths.py` places the active workspace,
settings, provider browser storage, libraries, caches, saved letters, recovery,
autosaves and temporary assets beneath that profile. Bundled resources remain
read-only inputs. Profile initialization does not copy the real user's legacy
workspace into a development profile. Seed only synthetic or explicitly
sanitized projects through the shared APIs. Never copy active runtime folders,
private letters, credentials, browser profiles or private test output between
feature branches. Normal application paths are unchanged when the variable is
unset. Use a short profile path: existing deeply nested generated assets can
exceed Windows path limits. Existing `.venv` availability is local; do not commit
environments.

## Ownership and coordination

| Owner | Files and responsibility |
| --- | --- |
| Foundation | `project_model.py`, `project_migration.py`, `project_repository.py`, `project_session.py`, `project_consumers.py`: canonical types, schema, identities, recovery, transactions, selection, delayed-result guards and compatibility boundaries. Coordinate any changes here. |
| Images | `Image_tab.py`, `image_animation.py`: use page IDs and artwork/animation operations; replace slot-specific UI assumptions incrementally. Preserve source bytes and independent mutable settings. |
| Messages | `Message_tab.py`, `Editor.py`, message document/resource modules: build the selector and bind one independent document/layout to its original page ID. Preserve rich HTML/resources. |
| Prompt Writer | `PromptWriterPanel.py`, `prompt_writer_browser.py`: consume `prompt_context(page_id)` and page prompt APIs; retain the four existing template bodies, exact snapshots and resolved choices. |
| Sound | `sound_tab.py`, `sound_model.py`: project-level playlist/order/settings only. Reuse existing archive, processing and restore guards; no per-page music system. |
| Forge | `Forge_Tab.py`, `generate.py`, `Template.py`, `readiness.py`: consume validated render snapshots and render independent Letter documents/settings. Keep output guards until their replacement is verified. |
| Saved Letters | `saved_letters.py`, `project_save.py`, `portable_export.py`: interface/workflow improvements consume the complete canonical save/load path; do not create another page-storage format or remigrate current schemas. |
| Command/Settings | `command.py`, `command_bar.py`, `settings_store.py`: distinguish project-authored defaults from application preferences; use safe reset and shared-setting operations. |

`Nexus.py`, `Over_Nexus.py`, `project_sync.py`, `page_controls.py`,
`page_layout.py`, `project_paths.py` and readiness/save integration are shared
shell files. Assign a single integration owner for overlapping edits. The
existing `page_layout.py` version-1 objects are compatibility views; their keys
and historical type names are not canonical page IDs or new schema meanings.
The registry in `project_model.py` owns the official labels and template map.

## Current consumer boundaries

Images and Prompt Writer retain their current configurable interface. Their
shared controller accepts all four types, repeated types and arbitrary order
within 2–10 pages. `project_consumers.project_legacy_views(session)` assigns
stable adapter keys through `update_page_recovery`, materializes disposable
views and keeps the canonical model authoritative. A failed projection restores
prior cache files. Original static/GIF bytes remain canonical; GIF posters are
derived previews. Message/prompt edits do not rebuild artwork caches. Drafts
outside the UI count range remain stored intact and visibly blocked.

The current Messages editor can safely edit exactly one active Letter document.
It blocks editing/import/revision restoration when there are zero or multiple
Letter pages. The future Messages task owns the selector and full independent
editing workflow. Repeated legacy message copies are independent in the model;
this interface does not silently relink them.

The existing viewer can render multiple Letter pages only while their complete
message documents are equal. Distinct documents, unresolved migrated ownership,
per-page placement/design and custom message layouts are visibly blocked by
readiness and all Play-bundle generation entry points. Projects remain savable.
Canonical shared-setting changes whose current compatibility views differ still
require the supported activation path before legacy preview/export. Sound now
has a separate incremental projection; it no longer requires a complete
save/load cycle. Unsupported presentation settings and independent page-message
rendering remain blocked pending their Forge work.

Project-wide Sound uses `project_sound_snapshot(session)`,
`update_project_sound(session, patch)` and `project_sound_views(session)` from
`project_consumers.py`. Canonical `sound.volume` is integer 0–100 and
`sound.muted` is boolean. Older projects fall back to shared `music_volume`,
then `starting_volume`, then `STARTING_VOLUME`; absent canonical mute is false.
The projection mirrors effective levels to `music_volume`, `starting_volume`
and `music_muted` and refreshes existing playback files. It preserves unrelated
pages/messages/prompts, shared settings, session identity and page work tokens.
Sound-only and compatibility-provenance edits do not mark prompts stale.
`prepare_consumer` projects canonical Sound before applying compatibility guards.
Empty drafts can edit/project Sound even though page output remains count-blocked.

Portable Sound assets retain authored IDs and missing-resource metadata. Cached
records are checked against resource content hashes, including same-ID resources
from different projects. Canonical saves collect assigned audio before a Sound
widget is opened. Projection and saved restore use the existing file/archive
rollback mechanisms, including audio settings after a failed final restore write.
See the Sound handoff for changed-file groups and focused regression evidence.

Forge reads the projected volume/mute when generating a presentation. Its viewer
mute remains local across next/back navigation; full Restart reloads authored
defaults. `window.letterSmithPlayback.setVisible(false)` cancels owned playback,
playlist timers and transition frames; `shutdown()` also disposes media sources.
Nexus invokes these hooks for hidden/pending previews, late loads and shutdown.
Showing the same stopped preview does not automatically resume playback.

Prompt Writer retains stale/partial saved prompts and resolved components without
regeneration; explicit Erase clears them. Current controls still express shared
selections. New per-page override/options or configured text areas block fresh
generation until the page-aware builder supports them. Existing snapshots remain
preserved. Detailed per-page selections, batch generation and changed prompt
terminology belong to the later Prompt Writer task.

`consumer_issue(root, consumer="output") -> str` reports the compatibility
boundary. `require_consumer(...) -> None` raises `ConsumerCompatibilityError`.
`prepare_consumer(...) -> None` synchronizes completed workspace writes and
materializes supported views. Consumers are `output`, `message`, `images`,
`prompts`, `prompt_generation`, and `layout`. Feature code should use canonical
operations from the contract rather than writing these compatibility files.

## Changes and delayed work

Use one shared `ProjectSession` for each workspace and select by page ID.
`PageLayoutController` relays session events through a Qt signal, so background
autosave notifications do not rebuild widgets on a worker thread. Restoring
controls uses blocked signals; a selection change is not an authored edit.

Capture the initiating session/page and relevant fields before asynchronous
work. Image imports check a target token before committing staged media;
existing restore cancellation and image-fingerprint checks remain active.
Prompt Writer captures a target before provider-image download and validates it
before assigning the image. Reorder/selection preserve the original target;
A → B → A rejects the old result. Message import and editor callbacks also reject
replaced/deleted/type-changed targets. Existing Message render revisions and
Sound generation guards continue to cancel/drain work during restore. Do not
replace these checks with "use whichever page is selected when finished."

Project-wide work uses `capture_project_target(fields=('sound',))`,
`project_target_is_current(target)` and `apply_project_result(target, callback)`.
These APIs require no page, including in empty drafts. They atomically check
session/project identity, restoration epoch and relevant field signatures/counters
before calling `callback(model)`. Relevant edits, even edit-then-revert, restore,
replacement and close invalidate work; unrelated page changes do not. Sound
imports and popup assignment use this contract in addition to cancellation.
`project_input_versions(fields)` keeps these counters independent of page work.

## Focused checks

Run these from the checkout with its installed Python dependencies. `pytest` is
not installed in the inspected `.venv`; the repository uses `unittest`. Its
`test` folder must precede Python's standard-library `test` package.

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
$env:QTWEBENGINE_CHROMIUM_FLAGS = '--disable-gpu'
.\.venv\Scripts\python.exe -c "import sys, unittest; sys.path.insert(0, 'test'); result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromNames(['test_project_model', 'test_project_repository', 'test_project_session', 'test_foundation_consumers'])); sys.exit(not result.wasSuccessful())"
.\.venv\Scripts\python.exe -c "import sys, unittest; sys.path.insert(0, 'test'); result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromName('test_page_layout')); sys.exit(not result.wasSuccessful())"
```

Run Qt/WebEngine suites in separate processes; a combined baseline process
exited without a unittest summary after GPU context failures. Relevant existing
suites are `test_prompt_writer_hardening`, `test_message_security`,
`test_message_overlay`, `test_image_animation`, `test_sound_restore_lock`,
`test_saved_letter_restore_sound`, `test_page_viewer`, and
`test_readiness_editor_paths`. The verification report records actual pass counts
and baseline failures separately from introduced failures. Offscreen/model tests do not
establish future multi-message interface completion, live provider behavior,
packaged application acceptance or real publication.

Recorded Sound integration checks: 56 model/session tests, 16 consumer tests,
13 repository tests and 32 saved-sound restore tests passed. Seven focused
QWebEngine/generator tests passed; the existing native-media viewer opening test
passed all seven input scenarios with output muted. Final full Sound discovery
and integrated native-shell acceptance are still being recorded in the Sound
handoff. These results do not establish audible quality, packaged acceptance,
primary-checkout integration or completion of sibling features.
