# Flexible page migration

This is a foundation-owned boundary. Do not independently reinterpret types or
add another migration in a feature branch.

## Detected formats

`project_repository.legacy_workspace_payload(root, *, saved=False)` inspects
actual workspace/package files. `read_legacy_project(root, *, saved=False)`
converts without changing source files. It reads the configured image slots and
actual missing-manifest image files, `lettersmith-pages.json`,
`lettersmith-images.json`, exact `message.html`, `prompt_writer_state.json`,
the existing sound manifest and restorable settings. Saved packages also supply
`lettersmith-metadata.json`. It does not invoke the historical bulk library
upgrader or convert libraries/custom palettes into active selections.

| Source marker | Meaning and behavior |
| --- | --- |
| `format=lettersmith.project`, `schema_version=3` | Current canonical document; strict validation, no legacy conversion, IDs/prompts unchanged. |
| Saved metadata `schema_version="1.0"`, `document_type=saved_letter` | Existing saved package envelope; metadata schema remains distinct from canonical project schema. |
| Pre-release numeric saved metadata 1–4 or absent version | Legacy metadata generations accepted only through an explicitly inspected legacy workspace/package; not inferred from canonical version numbers. |
| `project_schema_version` 0–2/absent | Existing project/recipient identity generation, not page-schema semantics. |
| Page-layout `version=1` | Configurable page array; preserve every entry in its actual order. |
| No page-layout document | Classic historical slot keys inferred from actual image references. Missing/optional Wall is not inserted. |
| Prompt Writer version 0–6 | Preserve current/historical exact strings, choices and resolved random components. |
| Unsupported future marker/type | Raise, preserve source and previous active project; never coerce and overwrite. |

An unmarked dictionary is not sufficient evidence: callers must use the directory
reader or explicitly supply `source_format="legacy_active"`/`"legacy_saved"`
after verifying provenance. Current documents use their format marker, even if
a caller supplies a legacy source hint.

## Conversion API and ownership

`project_migration.migrate_project(payload, *, source_format=None) ->
MigrationResult(project: ProjectModel, report: MigrationReport)` is pure. It
does not open files, commit, generate artwork, flatten GIFs, normalize strings or
regenerate prompts. The reader envelope contains `metadata`, `settings`,
`page_layout`, `page_layout_version`, `images`, `asset_prefix`, `message_html`,
`message_resources`, `resources`, `prompt_writer_state`, and `sound`.
Optional `messages` maps IDs/legacy keys to documents; `message_page_id` explicitly
associates the old global message. `message_layout`/`message_settings` preserve
separate documented settings if present.

Legacy role translation happens only here:
`cover → cover`, old `letter → illustration`, `wall → letter`, `back → end_page`.
Every migrated page retains `recovery.legacy_key`, `legacy_type`, original page
record and original image manifest record. Valid stored page IDs survive;
existing unique stable slot keys serve as IDs when no separate page ID exists.
Missing project IDs and duplicate page IDs receive new IDs once, and the report
records the original association and repair. Re-importing an unchanged original
is a new migration; loading its committed canonical working copy never generates
new IDs.

Explicit page messages take precedence. The current legacy viewer's
`Template.template_for_pages` creates one shared message overlay outside its
slides; `isWallPage()` activates it on every legacy Wall. Therefore a global
document demonstrably rendered on several Walls becomes independent copies,
with `legacy_shared_wall_overlay` provenance and a report warning. It is never
silently linked. A message without a provable target remains
`recovery.unassigned_message`; unresolved associations prevent final readiness.
Formatted HTML, embedded-resource paths, layout and settings remain intact.

Prompt Writer state is keyed by stable historical page key. Preserve that direct
association, exact whitespace/Unicode, directions and resolved components. Known
historical display labels are aliases only for their matching actual slot.
A global `Wall Prompt` is not guessed onto an arbitrary differently keyed Wall.
Unassignable prompts stay in `recovery.unassigned_prompts` and the report.
A page with no saved prompt remains null; no prompt is generated during conversion.
Missing reusable library entries do not replace saved choices.

Unknown source metadata is inert under `extensions.legacy_source` and is never
executed or applied as configuration. The reader filters project settings to
the existing restorable allowlist. Source resources and GIF playback settings
are retained; absent artwork remains missing and repairable.

## Reports, transactions and recovery

`MigrationReport.to_dict()` includes source/target schemas, role mappings,
preserved/generated/repaired identities, asset references/media handling,
warnings, unresolved associations, validation, and source/backup/destination
locations. Conversion is not a committed migration. Only a successful repository
save advances the active generation; the caller reports commit afterward.

`ProjectRepository` writes a complete isolated generation with `project.json`,
resource bytes and SHA-256 manifest, and `migration.json`, validates serialized
state, then atomically replaces `current.json`. A process lock and expected
revision reject concurrent/conflicting writers. Old committed generations remain
available for recovery; partial staging is never the active reader generation.
All assets in a committed generation have portable relative references. Missing
resources are recorded, never replaced by stock artwork. Source/static/GIF bytes
are copied without conversion by this foundation.

The precise layout is `<repository>/current.json` plus immutable
`g/<16-hex-revision>/project.json`, `resources.json`, `migration.json`, and resource
files. Artwork uses `a/<32-hex-content-hash>.<original-extension>`; the manifest
stores full SHA-256 digests and detects a shortened-name collision. Selected
audio uses portable `audio/` references. Message resources retain relative paths.
Deleted-page, inactive-message and unassigned-message resources are included in a
portable save. Unrelated library audio is excluded. References must be listed as
present or missing in the committed manifest. Missing resources also populate
`recovery.missing_resources`, blocking output readiness without blocking drafts.

`ProjectRepository(directory).load() -> StoredProject` returns `project`,
`revision`, `asset_root`, and `migration_report`. `save(project, *, asset_root,
expected_revision=None, fallback_asset_root=None, migration_report=None) ->
StoredProject` requires the currently loaded storage revision for replacement;
`None` means a new destination. The authored integer revision and storage token
are separate. Adopt the returned model and asset root after saving because artwork
references become content-addressed. `active_project_repository(root)` selects
`<root>/.lettersmith-project`.

`ProjectSaveService.save_project(directory=None, *, expected_revision=None) ->
StoredProject` saves incomplete drafts without the legacy output-readiness gate.
Omit the directory for the active session; specify a repository directory for a
portable copy. `load_project(directory) -> StoredProject` accepts a repository or
viewer package containing `.lettersmith-project`. Its full activation boundary,
`SavedLetterRestorer.restore_canonical(loaded, *, entry=None)`, restores supported
page/message/prompt/resource views, project settings, publication fields and
selected sound, with rollback on failure. Absent loaded fields clear previous
project state. Zero/one-page and over-limit drafts remain preserved with old
consumers blocked. Canonical viewer packages use this same boundary; legacy
viewer metadata does not override canonical authored state. Both restore routes
suspend intermediate session notifications. Service loading also refreshes the
existing `ProjectStateController` identity.

The commit pointer is the sole authority. Interrupted staging and older committed
generations remain recoverable but are not loaded automatically. This foundation
does not garbage-collect them. Per-tree rollback backups are cleaned only after
successful restoration; failed cleanup is logged without undoing the committed
generation. Win32 extended paths and compact generation names accommodate nested
recipient/title build and snapshot staging paths.

Legacy loading uses a migrated working copy; it does not rewrite the original
saved package. Bundled originals remain read-only. An explicit save produces a
user-owned generation. The active-session repository is
`<runtime-root>/.lettersmith-project`; no bulk library rewrite is part of startup.
Reset/new project replaces active authored content and leaves saved projects,
libraries, palettes and preferences untouched.

Multi-file activation and reset write `.lettersmith-project/activation.json`
before changing compatibility views. The separate `.activation/.writer.lock`
serializes activation, workspace synchronization and saves across processes.
The marker is cleared only after successful canonical/session activation; failed
activation retains it conservatively. On the next session lookup, recovery
rebuilds compatibility views from the current committed generation before any
legacy synchronization. Replay preserves the exact canonical document, page
revisions and pointer on disk; it does not import partially written views.
The recovered session records freshly assigned legacy slot associations and a
baseline of the restored views for its next save, without changing authored
page revisions. Recovery
failure keeps the marker, drops the incomplete cached session and reports the
error. Reset commits a fresh generation without deleting the previous head's
resources. Corrupt optional reusable sound catalogs leave selected IDs marked
missing instead of changing the library or blocking project migration.

Unsafe absolute/traversing resource paths, links/reparse points, malformed JSON,
unsupported versions and corrupt committed resources produce
`ProjectRepositoryError(code, message, *, path=None)` or a model/migration error before
activation. `ProjectConflictError` covers a busy writer, changed source resource,
or outdated expected revision. The caller retains its current session on failure.
Repository codes include `not_found`, `invalid_document`, `unsupported_schema`,
`invalid_project`, `unsafe_path`, `missing_resource`, `resource_changed`,
`resource_write_failed`, `unlisted_resource`, `incomplete_activation`, `writer_busy`, `revision_conflict`,
and `asset_collision`. Filesystem commit errors preserve the previous pointer and
propagate their original exception; they never report successful persistence.

## Verification and limits

Saved Letters now opens legacy sources through `read_legacy_project(saved=True)`
and activates an independent user-owned copy through the same canonical restore
boundary. Listing, previewing and opening do not rewrite the source. Save Project
commits that copy under its UUID, retaining the migration report and unresolved
content. Recover Content in Open Project requires a destination and explicit
replacement/copy decisions; originals and displaced resources remain retained
after saving. Deleting an owned save moves its complete generations into Recovery.
Opening a recovery copy creates another independent project and detaches publication
authority. No bulk upgrade, permanent-resource deletion or garbage collection is
part of this workflow. See the Saved Projects contract extension for exact APIs.

`test/test_project_model.py` has reusable `legacy_fixture` and
`five_page_project` builders. It verifies classic/configurable mappings, optional
Wall absence, independent repeated messages, exact prompts, current idempotence,
duplicate-ID repair, explicit message references, ambiguous prompt ownership,
missing-artwork drafts, no truncation over ten pages, type recovery and shared
overrides. Repository/session tests exercise real file transactions and stale
work guards; see the verification report for actual commands and results.

Canonical storage supports independent messages now. Existing tab/viewer
consumers still have compatibility boundaries; unsupported configurations must
remain preserved and visibly blocked from incorrect legacy editing/export.
This migration does not imply completion of future selectors or page-aware
viewer redesigns. Template content, per-page prompt-generation UI, and final
consumer acceptance belong to their later tasks.
