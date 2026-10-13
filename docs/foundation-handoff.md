# Parallel feature handoff

The checkout started at `6d015d2e1d93d4773dc99df6ac2a2eca31bf52c1`.
The foundation is currently uncommitted alongside pre-existing changes; that
commit alone does **not** contain this foundation. Review the working-tree
changes and establish one shared foundation commit before creating feature
worktrees. Do not discard unrelated work or describe the starting commit as a
tested foundation revision. No dependent feature tasks were started here.

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
Canonical shared-setting/sound changes whose current compatibility views differ
must pass the complete canonical save/load path before legacy preview/export;
the guard prevents output using previous settings or audio. Unsupported
presentation settings remain blocked pending the Forge integration.

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
