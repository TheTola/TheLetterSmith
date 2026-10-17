# Flexible page foundation contract

## UI & Synchronization contract extension — 2026-10-17

The combined shell uses one `ProjectSession`. Authoring selection is
`session.selected_page_id`; page numbers, names and type labels never identify a
destination. Images, Messages and the reusable detached Prompt Writer show
`Page N of total — name · type` (unnamed Prompt Writer rows use the type once).
Ordinary Messages entry reconciles selected Letter, last valid Letter in this
session, then first Letter through `select_page`. Hidden Messages refreshes may
remember a valid Letter but never move global selection. No Letter pages means
an empty workspace with the existing canonical Add Letter Page action.

Images' prompt/text buttons, Messages' background button, Prompt Writer's Page
Settings and Forge's readiness corrections route stable IDs through Nexus.
Forge no longer translates a correction into a legacy slot or generic message
key. The shell commits pending edits before routing, and queued focus callbacks
verify the originating session. Presentation navigation stays inside the viewer;
it does not change authoring selection. Messages and Prompt Writer retain an
uncommittable draft instead of rebinding it. A failed Images name commit blocks
selection/tab departure. Renaming/reordering updates Prompt Writer labels without
resetting its unchanged document, cursor or undo history. A new binding/session
gets a fresh document history.

`Nexus.flush_project_edits()` remains the common save/transition preparation:
validate the focused field, commit Images names, flush Messages, flush detached
Prompt Writer, then synchronize canonical compatibility views. Forge output
preparation now uses this same hook, with the existing Prompt Writer-only hook
as a standalone compatibility fallback. Save/Open/New/Exit continue to use the
Saved Letters controller, `ProjectSaveService` and `ProjectLifecycle`; the shell
adds no save format or decision dialog. Autosave completion carries session
generation as well as dirty revision, so a result from the first A in A→B→A
cannot mark the reopened A saved or finalize its metadata.
Modal crop/layout/library/settings editors keep their existing Apply/Cancel
contract: common save/output preparation and lifecycle entry refuse to proceed
while a modal dialog is open, focus it, and retain its unapplied or invalid input.
They do not scrape or silently accept dialog widgets.

Canonical events are the shell's authored dirty source and Forge's currency
source. Duplicate Images/Sound/Messages/Prompt Writer dirty/Forge-refresh signal
connections were removed; legacy Messages without a canonical workspace retains
its fallback. Selection and persisted notifications are not content edits.
Private prompt edits do not refresh Forge or Messages composition. Old-session
notifications are ignored. The title bar spells out Unsaved changes before the
project title so a long title cannot hide the state; its tooltip/accessibility
name retains the complete title.

Prompt staleness follows effective composer choices and `prompt_geometry`,
including page overrides. Shared palette changes do not stale a page whose
palette override shields it; shared Letter geometry affects Letters only.
Curtain/readability changes do not stale image prompts. Exact strings are never
rewritten by these invalidations. Structure/order and type changes retain their
existing context-sensitive staleness rules. Document text alone does not rebuild
artwork prompts.

Forge owns `render_content_key(snapshot)` and `referenced_resource_stamp(render)`.
The former is a cheap derived view key excluding private prompts; the latter
checks only artwork, active message resources and selected soundtrack references.
`RenderInput` captures page render dependencies/order and project render fields
with mutation counters. `render_is_current` checks those plus session identity
and source evidence, rather than rejecting a render solely because an unrelated
private prompt changed. Reverted relevant edits still invalidate work.

Shared Messages previews coalesce equal requests, retain the last usable output
while replacing it, and reject late jobs. While visible, a one-second resource
check requires two equal changed stamps before validating/rebuilding external
bytes. Invalid/incomplete replacements retain the old preview with an out-of-date
warning. Images' active-tab check uses file change tokens, decode validation and
before/after evidence; a failed replacement retains the cached thumbnail and
warning. Both caches are scoped to stable page/session IDs. No library-wide file
watcher is introduced. Final output still performs Forge's hash/resource checks.

Sound's owner hooks stop audition when leaving Sound. Messages remains muted;
Forge uses its existing visibility/shutdown gate and single soundtrack. Ordinary
viewer navigation does not restart sound or redirect editors. Shared preview
release cancels work and unloads owned files before deletion; original resources
and explicit saves are never cleaned up by a view operation.

Embedded Forge release observes a successful blank navigation with a bounded
five-second wait. A timeout cancels New/Open/Exit and output replacement before
editor subscriptions or owned files are disposed. Retrying waits for the same
pending navigation; a blank URL alone is not proof of release. Late preview-load
callbacks cannot reclaim the view while release is pending. Navigation cancels
the previous load; no queued Stop is issued ahead of the blank navigation, which
could otherwise cancel that unload on a starting renderer. The window frame
controller ignores construction/destruction events and removes its application
event filter only after shutdown preflight succeeds.

Authoritative integration coverage: `test_ui_synchronization`, owner tests in
`test_messages_workspace`, `test_images_workspace`, `test_prompt_writer_pages`,
`test_message_shared_preview`, `test_forge_render`, `test_forge_session_output`,
`test_project_model`, and actual shell geometry tests. See the dated UI handoff
for exact runs and native evidence; this contract does not assert release QA.

## Command & Settings contract extension — 2026-10-16

`ProjectLifecycle` in `project_lifecycle.py` owns the shell's one in-flight
New/Open/Exit decision. It consumes `Nexus.flush_project_edits()` and the same
`ProjectSaveService` instance injected into Forge/Saved Letters by Nexus.
`SavedProjectsController.save(finished=callback)` reports whether the originating
revision is still current, including failure. After the worker becomes idle,
the lifecycle flushes again and checks session generation plus content revision;
new edits cancel replacement. An explicit save never authorizes discarding a
later revision. Standalone Forge retains its existing service fallback.

`ProjectSaveService.prepare_new_project(page_types)` validates the configured
`MIN_PAGES`/`MAX_PAGES` and returns an empty `StoredProject` from `create_project`.
Preparation does not mutate active content. `activate_new_project(candidate)`
uses `SavedLetterRestorer.restore_canonical`, its activation journal, resource
staging, expected-revision check and rollback. It binds a fresh UUID destination
as unsaved, with no publication authority. The existing recipient entry workflow
remains; starter pages already exist and are retained when the recipient is set.

`prepare_discard_project()` reads the last explicit save, or prepares a fresh
classic draft when there is no explicit save. `discard_active_changes(prepared)`
uses the same canonical activation and binding. Exit/Discard therefore restarts
from that explicit version, not the abandoned active draft. Older canonical
working generations remain recoverable; no library or saved generation is
collected. New/Open discard by replacing only active state. Cancel performs no
replacement, worker disposal or media stop. A preparation/flush/save/conflict
failure keeps the session open. Shutdown waits are bounded by the existing owner
hooks; busy owners report a retry path before subscriptions are disconnected.

Command buttons, menus, Ctrl+N, Open, Save, Quit and window close share shell
routes. Native application Quit is intercepted before `aboutToQuit`; confirmed
close is queued because Qt suppresses recursive `close()` inside `closeEvent`.
Saved preview release is part of shutdown preparation. Prompt Writer reload runs
after the restoration scope, using its own blocked editing signals.
The standalone `command.py` launcher calls `Main.main(open_new_project=True)`;
it no longer invokes the old filesystem reset dialog. Failed view restoration
is reported separately from a committed canonical activation; the committed
project remains recoverable and no successful-open status hides that failure.

`SharedMessagePreview.release(timeout_ms)` observes the single blank navigation
issued by invalidation, with the remaining owner timeout after build cancellation.
It does not start a second navigation that can cancel unload confirmation. Failure
retains the owner and stops replacement; repeated release remains usable. This
narrow shared-view repair is covered in `test_message_shared_preview` and the real
Saved Letters save/open/preview path.
Forge keeps its operation busy until the UI completion callback returns, including
nested browser-release event loops. A worker's thread-finished signal is deferred
while activation is completing; it cannot unlock controls or finish the restore
overlay early. `test_saved_projects` asserts this in its real save/open path.

Curtain selection commits only `shared_settings.curtain_style` and its existing
settings mirror. It preserves page overrides, exact prompts and documents.
Application theme/family and picker/window preferences do not dirty authored
content. The former developer reset now resets only `application_theme` and
`theme_family`, through `SettingsStore`; it neither saves/deletes a project nor
reads/clears credentials. Restoration clears obsolete project view locks, prompt
dumps and output/audio path fields instead of inheriting another project's state.

With `LETTER_SMITH_PROFILE_ROOT` set, the default `GitHubCredentialStore` returns
no stored account, refuses credential writes and ignores clear. Explicitly
injected test stores remain supported. Normal production profiles continue using
the existing secure keyring. No profile-specific plaintext credential store exists.

Authoritative coverage: `test_command_settings`, `test_command_interaction`,
`test_developer_mode`, plus existing repository/session/Saved Letters/media suites.

## Saved Projects contract extension — 2026-10-16

The existing `ProjectSaveService` owns explicit editable saves as well as its
existing workspace recovery snapshots. `save_project()` retains its previous
workspace/repository meaning. Saved Letters uses `capture_editable_save()`,
`commit_editable_save(capture)`, and `complete_editable_save(capture, stored)`.
The synchronous equivalent is `save_editable(copy=False, title=None)`.

- The shell calls `Nexus.flush_project_edits()` on the GUI thread first: commit
  the focused valid field, pending Images name, Messages document, detached
  Prompt Writer, then synchronize established compatibility views. A failure
  prevents save/open/delete and keeps the editing session available for repair.
- Capture returns one detached model with its content revision, session token,
  destination and expected storage revision. Workers commit only that snapshot
  through `ProjectRepository`. Completion can bind only the originating live
  session. Saving R never marks a later revision saved. Workspace autosave and
  explicit editable-save dirtiness are tracked separately.
- Explicit saves live at `Saved Letters/Editable Projects/<project UUID>/` and
  contain the normal `.lettersmith-project` generation repository. Display
  titles never become editable-save paths. `editable-save.json` is a disposable
  active-workspace binding, not a second authored document or exported resource.
- `ProjectModel.independent_copy(title=None)` creates a new project UUID and
  detached mutable graph. Page IDs are project-scoped and remain unchanged with
  all internal references. The copy has an independent destination/resource
  generation, clears `publication` and writable publication/active-output/protected
  bindings in metadata/settings, and retains only inert source-ID provenance.
  Historical extension data is preserved but is never publishing authority.
- Save a Copy keeps the original session active. `duplicate_saved_project(entry)`
  copies the selected committed version, including when the active editor has
  newer unsaved edits. Neither operation includes another session's pending work.
- `read_saved_project(entry)` validates without activation and rejects a stale
  selected storage revision. `open_saved_project(entry, loaded=...)` uses
  `SavedLetterRestorer.restore_canonical`. Legacy, Example, Stock and Recovery
  sources open as independent user-owned copies. Ordinary editable saves keep
  their identity and publication association. No source is upgraded by listing
  or opening it.
- `rename_saved_project(entry, title)` commits display metadata through the same
  repository with expected-revision checking. `trash_saved_project(entry)` moves
  the complete owned directory into Recovery under the shared library lock.
  Other saves, original sources and permanent libraries are never collected.
- `ProjectRepository.load(allow_missing=True)` tolerates absent resource bytes
  while retaining references and recording warnings without incrementing the
  authored revision. Corrupt bytes, unsafe paths and unsupported schemas remain
  errors. Strict `load()` remains the default. Available source bytes, including
  GIFs, are copied without conversion; migration reports and supported extension
  data survive editable saves and copies.
- Declared missing references remain unavailable through save and consumer
  resolution even when an earlier workspace has a same-named file. Images,
  Messages and Forge honor that canonical missing-resource set. Use explicit
  image/message/audio replacement to relink content. A missing portable sound
  alias cannot reuse an unrelated archive entry without matching content
  identity; prior archive bytes remain under their content identity.
- Generation-directory promotion uses the existing bounded sharing-violation
  retry in `transactional_io`. Exhausted failures leave the old pointer intact.
  Implicit migration-report reuse requires the same project identity; a new
  project cannot inherit the previous session's report. Canonical copies and
  restoration pass their retained report explicitly.
- Stored sound playlists retain repeated occurrence IDs and their order. Sound's
  existing interactive Add action may still prevent inserting a duplicate; it
  must not collapse an already-authored playlist during save or restoration.
- `recoverable_items()` exposes detached unresolved message/prompt descriptions.
  `assign_recovered(kind, key, page_id, replace=False)` requires an actual stable
  page ID; messages require Letter pages, prompts can target any page. Occupied
  destinations require explicit replacement or a new page copy. The original
  item and displaced complete document/prompt remain in recovery; their embedded
  resource graph is included in subsequent canonical saves. Assignment clears
  only corresponding resolved migration associations. No recovery GC is added.

The catalog/index and thumbnails are disposable. Metadata is tagged with project,
storage and content revisions. Thumbnail choice is the first usable Cover artwork
in project order, then first usable artwork of any type, then a labeled placeholder.
Decode generation/path guards reject stale results and canceled cards. A thumbnail
failure cannot roll back a committed document. Invalid or missing entries remain
identifiable. Publication labels use the last confirmed metadata and Forge's
read-only display fingerprint; unavailable comparisons are explicitly unverified.
No catalog operation probes a remote service.
Forge folders marked by `lettersmith-display.json` are derived output and are
excluded from the editable collection, including folders created by older mixed
metadata callbacks. Those callbacks now return publication information without
writing prompts, canonical saves or editable metadata into renderer output.
Workspace autosave also leaves those derived folders unchanged. Existing legacy
packages retain their documented metadata behavior; no existing files are deleted.

Saved Version Preview creates a separate read-only session and uses Forge's
`SavedProjectPreviewAdapter(presentation_root)` through `SharedMessagePreview`.
The adapter supplies only controls, banner, fonts and UI sounds from the application;
authored artwork/message/audio resolution stays inside the saved generation.
It starts muted and never activates
the snapshot in the editor. Forge uses extended Win32 paths internally for long
title staging, while public build paths remain ordinary browser-compatible paths.
The shared preview makes its view visible before invoking the viewer start
action; Sound's hidden-document playback guard otherwise prevents GIF startup.
The shared theme refresh coalesces its own StyleChange events to avoid repeatedly
restyling a root after library/status updates.

## Combined feature contract — 2026-10-16

Prompt Writer reads version-1 CSS-pixel geometry through `resolve_layout` and
normalizes the resolved text box for artwork instructions. Shared geometry and
document overrides therefore agree with Forge without rewriting stored layouts.
Presentation changes invalidate affected render inputs; only changes to effective
composer geometry/settings stale image prompts (see the October 17 extension).
The canonical renderer supplies Sound's `INITIAL_MUTED` value; viewer mute
preferences remain scoped to project and display-content fingerprint.

## Prompt Writer contract extension — 2026-10-13

Authorized alongside Prompt 4. These are optional data within existing schema-3
objects; old documents and exact prompt strings are not rewritten on load.

- `replace_prompt(page_id, patch)` validates a build result, retains the previous
  complete prompt in `page.recovery.previous_prompt`, then applies the patch.
  `restore_previous_prompt(page_id)` swaps exact text/resolved output snapshots,
  leaves current directions/options/overrides intact, and marks the output stale.
  Recovery uses the normal portable project repository, with no separate store.
- `resolve_prompt_components(factory, reroll=False)` records one shared resolution
  in `prompt_settings.resolved_components`. An existing common historical page
  resolution is reused; otherwise the first explicit build calls the factory.
  Only an explicit reroll calls it again. Changing selections never rerolls text.
- `presentation.artwork_output` contains positive integer pixel `width`/`height`.
  Absent dimensions retain the previous 2048 x 3072 default. `prompt_geometry(id)`
  derives orientation and reduced aspect ratio and returns geometry without HTML.
- Empty `message.layout` retains the existing responsive centered viewer panel
  (`mode: responsive_center`), whose height depends on content and viewport and
  whose maximum width is 960 CSS pixels. No fixed artwork rectangle is implied.
  Explicit rectangles have `units: normalized` (0..1 image coordinates) or
  `units: pixels` (artwork pixels), plus finite `x`, `y`, `width`, `height`, measured
  from the top left and fitting inside the artwork. Unknown layouts are retained
  exactly; fresh composition rejects ambiguous geometry instead of guessing.
  Existing viewer readiness guards for custom layouts remain until Forge supports
  those rectangles. Prompt Writer supports drafting artwork instructions for them.
- Presentation changes invalidate prompt snapshots and shared WorkTarget versions,
  including edit-then-revert. They do not modify authored prompt strings.
- `ProjectSession.capture_prompt_batch(page_ids, factory)` validates nonempty
  targets, resolves shared wording once, and atomically returns a detached model,
  shared resolution and page work tokens. Tokens include type, prompt, message,
  page design, shared inputs and `$order`. The optional `$order` target field
  guards the ordered page IDs and a mutation-time structure counter, including
  reorder-then-revert. Ordinary page work without `$order` continues to survive
  reordering. Batch tokens never include selection as an authored input.

Foundation-owned: `project_model.py`, `project_migration.py`,
`project_repository.py`, and `project_session.py`. Coordinate changes to these
files; feature branches must consume their public operations rather than invent
schemas, reinterpret legacy types, or mutate private fields.

## Types and authored state

The canonical document has `format: "lettersmith.project"` and integer
`schema_version: 3`. This is distinct from legacy saved-letter schema `"1.0"`,
identity metadata schema `2`, page-layout version `1`, Prompt Writer state `6`,
image-manifest schema `1`, and application releases.

`project_model.PAGE_TYPES` is the immutable type registry:

| ID | Display label | Active message | Artwork template identity |
| --- | --- | --- | --- |
| `cover` | Cover | No | `cover` |
| `illustration` | Illustration | No | `letter` |
| `letter` | Letter | Yes | `wall` |
| `end_page` | End Page | No | `back` |

Template identities select the existing specialized content in
`PromptWriterPanel.py:PAGE_SPECS` and its prompt assembly pipeline. They do not
rename saved prompt snapshots or add an End Page text overlay.

Project fields: `project_id`, `revision`, `metadata`, ordered `pages`,
`shared_settings`, `prompt_settings`, `sound`, `presentation`, `publication`,
`recovery`, and `extensions`. There is no independently stored page count.
Selection, listeners, pending jobs and widget state belong to `ProjectSession`.
Permanent palettes/libraries remain in their existing application stores.

Each page contains:

| Field | Contract |
| --- | --- |
| `id`, `type`, `name` | Stable nonempty identity, canonical type, optional nonunique name. |
| `artwork` | `asset` portable reference or null; `media_type`; independent `placement` and `animation` objects. |
| `prompt` | `directions`, `overrides`, correct `template_id`, `resolved_components`, exact nullable `text`, `options`, `stale`. |
| `message` | Letter only: `{format: "html", html, resources, layout, settings}`. Other types have null. Rich HTML and resource references are retained exactly. |
| `design_overrides` | Keys absent here inherit from `shared_settings`; empty strings and null are explicit values. |
| `recovery` | Inactive message, legacy provenance, and other page-owned recovery data. `legacy_key` is the compatibility adapter's historical slot association. |
| `revision`, `preview_stale` | Content revision and derived-preview invalidation. |

`MIN_PAGES=2` and `MAX_PAGES=10` are defined in `project_model.py`. Repeated types,
arbitrary ordering, repeated names and no Letter pages are valid. New drafts may
contain zero or one page. Imported documents retain every page outside limits;
validation reports that they are not ready for output. Structural errors such as
unknown types, duplicate current-format IDs or malformed documents reject the
load; missing artwork and empty messages do not prevent draft saves.

## Model operations

`create_project(page_types=CLASSIC_PAGE_TYPES, *, metadata=None,
project_id=None) -> ProjectModel` creates independent state. Pass `[]` for an
empty draft. `new_page(kind, *, name="", page_id=None) -> dict` and
`empty_message() -> dict` return fresh independent values.

`ProjectModel.from_dict(payload) -> ProjectModel` validates and copies current
format only. `to_dict() -> dict`, `pages -> list[dict]`, `get_page(page_id) -> dict`,
`get_message(page_id) -> dict | None`, and `get_prompt(page_id) -> dict` return
detached deep copies. `project_id -> str` and `revision -> int` are read-only.

| Operation | Return and behavior |
| --- | --- |
| `add_page(kind, *, name="", index=None)` | New ID; inserts at zero-based index or appends; rejects above maximum. |
| `duplicate_page(page_id, *, index=None)` | New ID; defaults after source; independently copies all mutable state and removes the old compatibility slot key. Immutable source bytes may be shared. |
| `rename_page(page_id, name)` | None; identity/content ownership unchanged. |
| `reorder_pages(page_ids)` | None; requires every existing ID exactly once; moves complete pages. |
| `deletion_scope(page_id)` | Detached description of content and asset-retention/undo guarantees for a confirmation UI. |
| `delete_page(page_id)` | `DeletionRecord(project_id, page, index)`; retains the page in project recovery; rejects deletion below minimum; never deletes source files. |
| `restore_page(record)` | Restored original ID/position (clamped to end); rejects another project, duplicate ID, or maximum overflow. |
| `change_page_type(page_id, kind)` | None; preserves artwork/exact prompt; retains/restores inactive message; marks prompt/preview stale. |
| `update_artwork(page_id, patch)` | None; top-level artwork merge; nested placement/animation values supplied in patch replace their corresponding objects. |
| `update_message(page_id, document)` | None; replaces complete rich document; rejects non-Letter; a changed text area marks prompt stale. |
| `update_prompt(page_id, patch)` | None; top-level merge without string normalization; directions/overrides/options changes mark stale unless explicitly supplied. |
| `update_page_settings(page_id, patch)` | None; top-level override merge; marks preview stale and prompt stale only when effective composer inputs change. |
| `clear_page_override(page_id, key)` | None; removes override so it inherits again. |
| `update_page_recovery(page_id, patch)` | None; adapter/recovery provenance merge through the same event boundary. |
| `update_shared_settings(patch)` | None; merge shared defaults; preserve authored artwork/message/prompt; mark derived output stale. |
| `update_project_data(field, patch)` | None; merge one of metadata/shared_settings/prompt_settings/sound/presentation/publication/extensions/recovery. |
| `resolve_settings(page_id)` | Detached merged shared defaults and explicit page overrides. |
| `resolve_layout(page_id)` | Detached normalized message/artwork geometry; raises `ProjectLayoutError` for an invalid draft layout. |
| `prompt_context(page_id)` | Template, ID/type, actual order/count/one-based position, Letter text area, shared selections and exact page prompt. |
| `validate()` | Tuple of `ValidationIssue(code,message,page_id,severity)`; these are output-readiness issues. Structural validation occurs on construction/mutation. |
| `render_snapshot()` | Detached project with per-page `resolved_settings`, `resolved_layout`, validation and readiness. Invalid geometry has null `resolved_layout` and a page-specific issue. Resource availability is also checked by the repository/consumer. |

Errors: `UnknownPageError(KeyError)` for missing page; `PageLimitError` for
interactive limits; `UnsupportedProjectError` for type/schema meaning;
`ProjectModelError(ValueError)` for malformed operations. Mutations validate
before updating authored fields. Listener failures are logged and do not undo an
already successful operation or prevent other listeners receiving it.

`add_listener(callback)` / `remove_listener(callback)` use
`ModelChange(kind, page_ids: tuple[str,...], revision, changed_fields=(),
source_model_id=None)`. The
optional `changed_fields` is an immutable tuple of `(page_id, field_names)`;
`"*"` denotes page existence, and `("*", ("shared_settings",))` denotes shared
input changes. The model updates its input counters at mutation commit, before
notifications; `source_model_id` lets sessions reject queued events from an old
model instance. Kinds are `structure`,
`name`, `artwork`, `message`, `prompt`, `page_design`, `shared_design`,
`project_data`. Callbacks see completed state. Structure changes invalidate
order/count-dependent prompt snapshots. No operation regenerates prompt text.
Rename preserves composition and prompt strings; renderers refresh labels that
display the name. Effective shared visual/prompt changes stale their dependent
output; authored strings/documents/assets remain unchanged. Public reads and
mutations use one model lock for coherent snapshots during autosave. Callbacks
run after that lock is released, avoiding inversion with the session lock.
`mutation_scope()` holds that lock across a guard and subsequent public mutations
and defers notifications until the outer scope exits. It provides synchronization,
not rollback. `input_versions(page_id, fields, *, include_shared=True)` returns
mutation-time counters; consumers normally use the session target API instead.
`project_input_versions(fields)` provides independent counters for project
objects (`metadata`, `shared_settings`, `prompt_settings`, `sound`,
`presentation`, `publication`, `recovery`, `extensions`). These counters survive
same-project adoption and do not alter the existing page/shared input counters.
Sound and extension-provenance edits increment the project revision without
changing pages or prompts.
Foundation adapters use `adopt_snapshot(payload, *, kind="workspace")` for
validated same-project updates while preserving the active model object and
its mutation counters. Project replacement belongs to the session.

## Shared sessions and delayed work

`get_project_session(project_root) -> ProjectSession` shares one active context
per resolved runtime root. `peek_project_session(root)` returns an existing one
without loading. `release_project_session(root)` releases only the cache.

`session.project` is the model. `select_page(id | None)` emits `selection` without
editing content. A deleted selection falls back to the first remaining page,
or None. `replace(project, *, storage_revision=None, asset_root=None)` validates
a copy before activation, clears stale jobs by creating a new session identity,
selects the first page, and emits `project_replaced`. A → B → A creates three
distinct sessions. Omitting `storage_revision` preserves the current expected
disk revision; reset uses a committed fresh generation before replacing the
session. `snapshot()` is recursively read-only. `close()` invalidates
captured work and prevents further save/capture operations without changing disk;
`release_project_session` calls it before releasing the cached context.

Use `with session.restoring():` to suppress intermediate notifications while
restoring controls. The session lock spans the whole scope, so an already running
save finishes before restoration begins and competing saves/work wait until it
ends. Nested scopes are supported. Reentrant target capture is rejected and
target checks/results return false while restoration is active. Consumers must skip save/default callbacks when
`session.is_restoring`. Completed batched state is published once.

Capture asynchronous work with
`capture_target(page_id, *, fields=('type','artwork','prompt','message',
'design_overrides'), include_shared=True) -> WorkTarget`.
`target_is_current(target) -> bool` checks session, original page, relevant input
versions and signatures using the model's immediate mutation counters, even if
notifications have not yet arrived. `apply_result(target, callback) -> bool` invokes
`callback(model, original_page_id)` only if current; guard and callback share the
model mutation scope so another thread cannot edit between them. Choose fields relevant to
the operation. Selection/rename do not redirect output; deletion/recreation,
relevant edits (even edit then revert), and project replacement invalidate it.
Order-sensitive work should explicitly incorporate order in its own input
contract; ordinary page-targeted image work must not follow the selected page.

For project-wide work, use `capture_project_target(*, fields=('sound',)) ->
ProjectWorkTarget`, `project_target_is_current(target) -> bool`, and
`apply_project_result(target, callback) -> bool`. The frozen target captures the
session/project identity, relevant project-object signatures and mutation
counters; no page or selection is required, including in an empty draft.
`callback(model)` runs inside the same atomic guard/mutation scope. Unrelated
page, selection, or project-field edits leave it current; relevant edits (even
edit then revert), replacement, close, and entering a restoration scope invalidate
it. Project targets remain invalid after an unchanged restoration; existing page
target restoration behavior is unchanged. Choose the relevant project fields;
an empty `fields` tuple guards only the session and restoration boundary.

`SessionChange(kind,page_ids,revision,session_id)` adds session identity to model
events. `session.synchronize_workspace()` applies only changed legacy fields;
`session.save(*, sync_workspace=True) -> StoredProject` uses the transaction and
expected disk revision. Saves are rejected while restoring; successful save
normalization updates the same active model object instead of detaching existing
consumers. Saving and compatibility synchronization serialize with activation
across processes. An interrupted-activation marker blocks synchronization until
`get_project_session` recovers committed views. Failed loading must not call
`replace`.

## Shared composition geometry (October 16 coordinated extension)

The user authorized Foundation geometry and portable export integration on the
Forge branch. Schema version 3 and the existing `message.layout`,
`artwork.placement`, shared settings and page overrides remain the storage
format. No migration rewrites older layout objects.

`project_model.resolve_page_layout(page, resolved_settings)` is the pure shared
resolver; `ProjectModel.resolve_layout(page_id)` supplies authoritative settings.
It returns `{"message": normalized_or_null, "artwork": normalized_or_null}`.
Both model snapshots and Forge's template adapter use this resolver. Messages
must consume it and the Forge adapter rather than interpreting geometry itself.

Inheritance uses the existing `resolve_settings`: a page's `message_layout` or
`artwork_placement` setting replaces that shared setting as a whole. Explicit
`message.layout` / `artwork.placement` fields then override the selected setting,
merging nested `canvas`, `box`, `padding` and `position` fields. Empty explicit
objects inherit; a null page setting clears that inherited default. Empty total
geometry preserves the existing responsive artwork/message presentation.

Nonempty message geometry requires:

```json
{
  "version": 1,
  "units": "css_px",
  "canvas": {"width": 400, "height": 600},
  "box": {"x": 40, "y": 130, "width": 320, "height": 170},
  "padding": {"top": 12, "right": 12, "bottom": 12, "left": 12},
  "text_align": "left",
  "overflow": "scroll"
}
```

Coordinates start at the logical canvas's top-left. Canvas dimensions are finite
numbers from 1 through 16384; box dimensions are positive and its nonnegative
coordinates must keep it within that canvas. Padding defaults to zero per edge
and must leave positive content space. Alignment defaults to `inherit` and also
supports `left`, `center`, `right`, `justify`, `start`, `end`. Overflow defaults to
`scroll`; clipping and automatic text shrinking are rejected. The viewer scales
the complete canvas uniformly into available space without editing authored
fonts or text. A focusable text viewport and visible overflow notice keep the
entire message accessible.

Nonempty artwork placement requires `version: 1`, optional `fit` (`contain` by
default, or `cover`), and optional normalized `position: {x, y}` (each 0–1,
default 0.5). A `canvas` is required when no message geometry exists. Otherwise
it inherits the message canvas and an explicit canvas must match. Message
geometry alone supplies centered `contain` artwork placement on the same canvas.
Unknown fields, units, versions and out-of-range values are errors, not guesses.

Invalid/old opaque geometry remains savable. `ProjectLayoutError.field` identifies
`message_layout` or `artwork_placement`; `validate()` reports `unsupported_layout`
or `unsupported_placement` with the page ID, blocking preview/final rendering.
Browser-measured overflow is a nonblocking `message_overflow` RenderIssue-shaped
warning returned by `LetterSmithViewer.layoutIssues()` and emitted through the
`lettersmith-layout` window event. Font metrics are measured by the viewer;
Messages owns any editing controls and can use these page-specific diagnostics.

## Persistence and runnable example

`ProjectRepository(directory).save(project, *, asset_root, expected_revision=None,
fallback_asset_root=None, migration_report=None) -> StoredProject` stages an
entire generation and atomically advances its pointer. `load() -> StoredProject`
validates the committed document/resources. `StoredProject` has `project`, opaque
`revision`, `asset_root`, and `migration_report`. Pass the returned revision on
the next save; stale writers raise `ProjectConflictError`. Details and source
preservation rules are in [foundation-migration.md](foundation-migration.md).

Run from the repository root with `.venv\Scripts\python.exe`:

```python
from pathlib import Path
from tempfile import TemporaryDirectory
from project_model import create_project, empty_message
from project_repository import ProjectRepository

with TemporaryDirectory() as temporary:
    root = Path(temporary)
    project = create_project(["cover", "letter", "illustration", "letter", "end_page"])
    page_a, page_b = project.pages[1]["id"], project.pages[3]["id"]
    for page_id, text in ((page_a, "Message A"), (page_b, "Message B")):
        document = empty_message()
        document["html"] = f"<p><b>{text}</b></p>"
        project.update_message(page_id, document)
        project.update_prompt(page_id, {"text": f"  {text} prompt\n雪\n"})
    ids = [page["id"] for page in project.pages]
    ids[1], ids[3] = ids[3], ids[1]
    project.reorder_pages(ids)
    duplicate_id = project.duplicate_page(page_a)
    repository = ProjectRepository(root / "saved-project")
    saved = repository.save(project, asset_root=root, expected_revision=None)
    reopened = repository.load()
    assert reopened.project.get_message(page_b)["html"] == "<p><b>Message B</b></p>"
    assert reopened.project.get_message(duplicate_id) == project.get_message(page_a)
```

Synthetic fixtures are `test/test_project_model.py:five_page_project` and
`legacy_fixture`. They exercise real APIs without private content. This model
example alone does not verify editor wiring; the integrated Messages and shell
checks are recorded in the final integration requirements matrix.

## Final integration lifecycle clarification

Native Sound notifications and import/repair results enter the UI through queued,
generation-checked slots. A worker's `finished` notification is not permission to
destroy its Python/native handles before `QThread.wait(0)` confirms completion.
Cleanup retries through the owning widget's timer, retaining the busy state and
the existing bounded shutdown/cancel policy. No other application's workers are
terminated. `ThemeService.shutdown()` detaches its application event filter only
after shutdown preflight succeeds; canceled transitions retain it. An unloaded
Messages preview releases without creating a browser or restarting an unload.

The isolated profile also applies to runtime-root settings/configuration imports
before Main calls `configure_application_paths`. Explicit other project roots
remain self-contained. Global event filters must reject irrelevant/destruction
notifications before accessing widget content; native worker ownership ends only
after a completed join, not merely receipt of a queued finished signal.

Opening the canonical Prompt Writer is a read-only binding operation. It does not
refresh legacy page projections; canonical pages, their labels and exact prompts
already supply the view. Compatibility projection maintenance must not be added
to a launcher as an incidental authored revision.

A captured artwork result validates its original session/page token after pending
names are flushed. It does not import disposable workspace settings at commit.
Images and Prompt Writer persist already-canonical edits without a second legacy
merge; explicit compatibility ingestion remains at the established input boundary.

Managed display-bundle relocation and case-only rename use the same bounded
transient-lock retry primitive as generation commits. A persistent failure still
raises; it is not a successful save, migration or rename. Existing path and
identity validation precedes the move.
