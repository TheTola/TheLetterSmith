# Flexible page foundation contract

## Combined feature contract — 2026-10-16

Prompt Writer reads version-1 CSS-pixel geometry through `resolve_layout` and
normalizes the resolved text box for artwork instructions. Shared geometry and
document overrides therefore agree with Forge without rewriting stored layouts.
Presentation changes invalidate shared prompt inputs as well as render inputs.
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
| `update_page_settings(page_id, patch)` | None; top-level override merge; marks prompt/preview stale. |
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
Rename alone does not stale a preview. Shared visual/prompt changes stale derived
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
`legacy_fixture`. They exercise real APIs without private content. The future
multi-message editor UI is not implied by model or storage support.
