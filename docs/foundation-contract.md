# Flexible page foundation contract

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
| `prompt_context(page_id)` | Template, ID/type, actual order/count/one-based position, Letter text area, shared selections and exact page prompt. |
| `validate()` | Tuple of `ValidationIssue(code,message,page_id,severity)`; these are output-readiness issues. Structural validation occurs on construction/mutation. |
| `render_snapshot()` | Detached project with per-page resolved settings, validation and readiness. Resource availability is also checked by the repository/consumer. |

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

`SessionChange(kind,page_ids,revision,session_id)` adds session identity to model
events. `session.synchronize_workspace()` applies only changed legacy fields;
`session.save(*, sync_workspace=True) -> StoredProject` uses the transaction and
expected disk revision. Saves are rejected while restoring; successful save
normalization updates the same active model object instead of detaching existing
consumers. Saving and compatibility synchronization serialize with activation
across processes. An interrupted-activation marker blocks synchronization until
`get_project_session` recovers committed views. Failed loading must not call
`replace`.

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
