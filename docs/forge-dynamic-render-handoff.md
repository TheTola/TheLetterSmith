# Forge dynamic rendering handoff — updated 2026-10-15

## Scope and starting state

Branch: `codex/forge-dynamic-render`.
Worktree: `C:\Users\Oluwatola Ayedun\.codex\worktrees\forge-dynamic-render\SmithLetter`.
Initial verified foundation snapshot: `5daa5e3b29bf1b6ca3019f56f1bb9c0d2aca6954`.
Current shared foundation incorporated: `1612235e5f5bf0c02e84cead87fc84242d30cac5`.
Forge implementation commit: `5e1d02163f80ed41d20accad5ce7f9913ff7cdd7`.
Foundation merge: `0e6e7ed`.

The primary checkout initially had HEAD
`6d015d2e1d93d4773dc99df6ac2a2eca31bf52c1` plus uncommitted foundation and
unrelated work. The foundation documents still describe that earlier state.
During inspection, the parallel Sound task established the foundation-only
snapshot above. All 29 foundation file blobs were compared with the primary
checkout; Main.py's foundation portion was the three-line removal of bulk
startup migration, excluding unrelated diagnostics. This branch starts from
that verified snapshot, without cherry-picking sibling feature work.

Pre-existing work included Main.py diagnostics, publishing authentication and
Pages changes, release scripts/specification/checklists/manifest, requirements,
settings, application-path/publishing tests, fault_diagnostics.py,
https_transport.py and their tests, Prompt Writer data, and .test-output.
The primary checkout was not edited, reset, cleaned, or committed by this task.
It advanced independently to `1612235e5f5bf0c02e84cead87fc84242d30cac5` during
the initial work. On October 15, that committed foundation was compared with
the initial snapshot and merged into this isolated branch. Its only code change
preserves a resource's original reference when the same image is also artwork
normalized to a hashed path. The accompanying repository regression and updated
foundation reports were included. Existing Forge changes were retained during
the duplicate-foundation merge conflicts. No dirty primary-checkout changes or
sibling features were incorporated.

Read before implementation: foundation-contract.md, foundation-migration.md,
foundation-verification.md, foundation-handoff.md and actual public APIs.
No schema, migration, page registry, message store, shell, or sibling editor
implementation was added. No dependency installation, remote push, provider
image generation, authentication, or live publication occurred.

## Changed files

| Path | Change |
| --- | --- |
| `forge_render.py` | Canonical snapshot adapter, validation, active-resource collector, staging, manifest, public-output allowlist. |
| `generate.py` | Existing build/cache APIs dispatch to canonical rendering; explicit legacy message overrides remain compatible. |
| `Template.py` | Independent page/message composition, ID-based navigation, per-message readability, retained viewer/media controls. |
| `Forge_Tab.py` | Canonical capture, session events, page-specific readiness navigation, preview currency, staged publishing and guarded completion. |
| `readiness.py` | Canonical page-specific issues and separate preview/final readiness. |
| `publishing/github_pages.py` | Optional `persist_publication=True`; Forge passes False and persists verified success under its captured session guard. |
| `test/test_forge_render.py` | Real synthetic render/output/resource/concurrency and frozen-publish regression checks. |
| `test/test_forge_session_output.py` | Deferred preview/publish/session/revision/close safety checks. |
| `test/test_page_viewer.py` | Real WebEngine dynamic navigation, independent overlays, mute/volume/restart. |
| `test/test_readiness_editor_paths.py` | Canonical readiness cases; updated obsolete single-message viewer assertion. |
| `test/test_github_publishing.py` | Existing local transport double verifies deferred publication persistence. |
| `docs/forge-dynamic-render-handoff.md` | This report. |

The only provider API addition is optional and preserves existing callers.
When integrating it, retain unrelated TLS/authentication work in the primary
checkout. No foundation-owned or Saved Letters-owned source was changed.

## Shared rendering adapter

Forge owns `forge_render.py`, `generate.py`, `Template.py`, `Forge_Tab.py`, and
renderer readiness. Messages should consume this adapter, not fork templates.

`capture_render(session, output_mode="preview" | "display") -> RenderInput`
uses `session.project.render_snapshot()`, `session.capture_target(...)`, and
the session's project/asset roots. The frozen dataclass contains a recursively
read-only snapshot, session/project identity, revision, output mode, dependency
fingerprint and captured targets. Pages retain canonical IDs/order/types,
artwork, active message documents and resolved settings. It is derived output
input, not another authored-state serializer.

Public hooks:

| Hook | Behavior |
| --- | --- |
| `validate_render(render)` | Returns `RenderIssue(code,message,page_id,severity,feature,blocks_output,blocks_preview)`. |
| `build_render_to(render, destination, *, seed_sfx=True, reuse_play_directory=None, cancelled=None)` | Builds/validates into a caller-owned staging directory. It does not promote the live Play directory. |
| `generate_render(session, *, render_input=None, output_mode="display", destination=None, seed_sfx=True, cancelled=None)` | Builds and transactionally promotes only a current generation under the foundation guard. |
| `render_is_current(session, render, check_sources=True)` | Checks ownership, captured input revision/targets and actual source content. |
| `render_has_music(render)` | Reports whether the canonical project selects soundtrack entries. |
| `validate_display_output(directory)` | Validates required resources, manifest hashes and relative references. |
| `copy_display_output(source, destination)` | Transactional public allowlist copy; requires final display mode and no warnings. |

Messages can capture its shared session in preview mode, validate, build to its
own temporary generation, load index.html in its existing view, and call
`window.LetterSmithViewer.goToPage(page_id)`. `currentPageId()` reports playback
position. Navigation never calls `session.select_page`: editor selection and
playback remain separate. Before activating a delayed Messages preview, check
`render_is_current`; release that view before removing its generation.

`Template.template_for_project_pages(pages, *, template_html=None)` is the lower
level renderer DTO adapter: each item has `id`, canonical `type`, `name`,
`source`, `message_html`, and `overlay_style`. It emits HTML/CSS/JS using stable
hashed DOM identities. It is not an authored-message editor API. The full
adapter resolves/sanitizes resources before calling it. User HTML is inserted
after trusted template substitutions, so literal template markers remain text.
`template_for_pages` remains available for legacy consumers.

Existing `generate.build_play_bundle_to`, `generate_play_bundle`,
`ensure_play_bundle`, source fingerprint and currency APIs use this adapter
when a canonical session exists. Explicit legacy `message_html` overrides
retain their historical path; Forge passes the captured `render_input` and
does not supply a global message for canonical output.

## Composition, media and output

All four canonical types use their own artwork. Only active Letter documents
produce separate text overlays; retained inactive messages never render.
Actual ordered page count drives navigation, including End Page in the middle,
repeated types/names and projects with no Letter. Foundation bounds are used
without truncation. Default text placement, scrolling and existing readability
presets remain; unsupported custom geometry is explicitly blocked below.

Artwork paths are scoped by stable page ID. Original static/GIF bytes are copied;
GIF frames/settings are derived with existing animation helpers and validated
frames can be reused. Hidden pages stop their animation. Each Letter has its
own sanitized rich HTML, resource references and contrast/ultralink controller.
Fonts are collected/embedded in one pass across documents to avoid overwriting
another page's embedded face. Resizing does not rewrite saved fonts or text.

One project soundtrack uses the canonical `sound.tracks` references and
playlist/single-track selection, order, volume, mute and crossfade. Missing
selected tracks are reported; unselected library tracks are not required.
Navigation does not restart soundtrack playback. Restart and the existing
interaction-to-start behavior remain. Mute state is independent of volume and
browser persistence is scoped to the project/output identity.

`lettersmith-display.json` identifies pages, active files and their hashes.
Public output contains only manifest-listed display dependencies, including
legitimate active embedded media/fonts, controls, curtains/banner and selected
audio. The private `lettersmith-build.json` diagnostic manifest, canonical
editable repository, metadata added by saved-letter persistence, inactive
messages, prompt history, logs, credentials and unrelated working files are
excluded by `copy_display_output`. Portable references use existing containment
checks. Build/promotion and public copy use `PathTransaction`; failure or
cancellation preserves the previous committed output.

Preview and final output share composition. Preview can show an explicitly
labeled missing-artwork placeholder or empty-message warning. Final output
blocks those conditions. Unsupported settings/geometry fail with an actual
page ID and repair feature, instead of silently changing the authored project.

## Events, invalidation and publication

Forge listens to `ProjectSession` events through a Qt signal; `selection` is
ignored. `project_replaced` clears pending metadata/publish context and active
preview paths and emits `preview_files_release_requested`. Other events
schedule existing currency/readiness refresh without restarting playback.
The existing shell's tab-deactivation and Prompt Writer flush hooks remain.
There is no newly invented global editor-flush registry.

The content fingerprint covers ordered page IDs/types/names, active rich text,
resources and font bytes, media bytes/settings, resolved readability,
presentation, selected audio and viewer templates. It excludes prompts,
recovery and publication metadata. Repository path normalization alone does
not change it. External media changes are detected by existing refresh/final
preparation checks. No library-wide polling was added. A rebuild recomposes
HTML and active resources, reusing valid GIF frames; page-level incremental
HTML patching is not implemented. In-flight revision checks are deliberately
conservative: even an unrelated revision change can cancel that build.

Workers capture the original session/project and immutable render input.
Promotion rejects replaced projects, edited/deleted targets, cancellation and
older concurrent generations. UI completion rechecks current content before
opening a preview; an intervening edit remains stale and requests another
update. Page-specific problem links select the canonical ID, then use the
existing shell correction signal/legacy slot adapter at the boundary.

Publish preparation freezes a verified public allowlist into an isolated
TemporaryDirectory before waiting for sign-in/reconnection. The pending
operation owns that directory through retries. Preview regeneration is deferred
while publication is active. Upload validates the captured project/fingerprint
again; later Play rebuilds cannot alter the payload. Clearing the pending
operation and worker releases its temporary directory.

The existing GitHub provider runs with `persist_publication=False`. Forge
persists only verified transport success under the initiating session guard,
including `session.save(sync_workspace=False)`, before the worker returns.
Closing before the UI callback therefore cannot lose confirmed publication
state. An older successful output retains its original fingerprint and never
marks a newer draft current. Replaced projects receive no old success state.
Recovery copies are attempted only if the live Play and current draft still
match the published fingerprint; otherwise the existing recovery warning is
shown. Failed transport preserves previous publication metadata.

## Validation performed

Every final run configured a unique short `LETTER_SMITH_PROFILE_ROOT`, TEMP and
TMP, offscreen Qt and disabled GPU before imports. Explicitly bootstrap paths:
setting the environment alone is insufficient for tests importing config.py.
An initial test added the empty default `prompt_writer_provider` to this
worktree's settings.json; that task-induced change was restored and the Git
content hash verified equal to baseline. It was not committed. The primary
checkout and live project data were not used for fixtures.

Example command used from this worktree (change the profile for a new run):

```powershell
$env:LETTER_SMITH_PROFILE_ROOT='C:\ls-forge-final-7174'
$env:TEMP=$env:LETTER_SMITH_PROFILE_ROOT
$env:TMP=$env:LETTER_SMITH_PROFILE_ROOT
$env:QT_QPA_PLATFORM='offscreen'
$env:QTWEBENGINE_CHROMIUM_FLAGS='--disable-gpu'
$env:PYTHONDONTWRITEBYTECODE='1'
New-Item -ItemType Directory -Force -Path $env:LETTER_SMITH_PROFILE_ROOT | Out-Null
@'
from project_paths import ApplicationPaths, configure_application_paths
configure_application_paths(ApplicationPaths.for_runtime())
import sys, unittest
sys.path.insert(0, 'test')
names = ['test_forge_session_output',
         'test_github_publishing.GitHubPublishingTests',
         'test_github_publishing.GitHubAccountUITests',
         'test_readiness_editor_paths.CanonicalReadinessTests',
         'test_readiness_editor_paths.ReadinessEditorAndProjectPathTests.test_forge_does_not_retry_a_preview_after_leaving_the_tab',
         'test_readiness_editor_paths.ReadinessEditorAndProjectPathTests.test_forge_builds_a_missing_preview_and_reuses_a_current_one']
result = unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.loadTestsFromNames(names))
sys.exit(not result.wasSuccessful())
'@ | & 'C:\Users\Oluwatola Ayedun\Desktop\SmithLetter\.venv\Scripts\python.exe' -
```

Run browser suites in separate bootstrapped processes. Actual results (counts
overlap; do not sum into a unique total):

| Check | Result |
| --- | --- |
| Foundation model/repository/session/consumers on this baseline | 62/62 passed. |
| `test_forge_render` final | 17/17 passed. Real five-page build/reorder/save/reload, GIF bytes, independent rich text/fonts, bounds/no truncation, active-resource privacy, cancellation/failure/stale/concurrent promotion, frozen publish payload. |
| Final command above | 59/59 passed. Forge delayed state, local fake GitHub transport/UI handling, canonical readiness and deferred-preview regression. |
| `test_page_viewer` | 4/4 real WebEngine tests passed; baseline legacy viewer was 1/1. |
| `test_message_overlay` / `test_message_security` | 13/13 and 20/20 passed. |
| `test_saved_letter_activity_order` | 23/23 passed. |
| Entire `test_readiness_editor_paths`, isolated case processes | 58/59 passed; existing theme geometry assertion 118x40 versus 125x40 fails. |
| Combined readiness/editor process | Native access violation at existing editor cleanup/processEvents; reproduced with baseline source/test overlays. Not a successful suite run. |
| Broader existing image animation suite | Two known baseline geometry failures reproduced: reset button 243x73 versus 308x103, thumbnail 168x155 versus 150x165. |

The two initial compatibility regressions encountered during implementation
(explicit legacy message override dispatch and an obsolete single-message
template assertion) were corrected before the results above.

Real generated-output browser smoke artifacts are at
`C:\ls-forge-smoke-7169\`: `smoke.py`, `smoke-result.json`, `public-display`, and
four `generated-letter-{a,b}-{landscape,portrait}.png` screenshots. The smoke
used the production renderer and public allowlist, then removed the synthetic
source project before loading the copied output. Screenshots were inspected.
Message A/red background and Message B/blue background remain independent at
1000x700 and 390x844, with distinct readability and formatting. Two GIF frame
sources were observed and hidden animation stopped. No JavaScript errors.
Two valid four-second PCM WAVs played/advanced/looped after real Qt interaction;
audio time continued across navigation (0.802295 to 0.804836), canonical mute
and volume 23 were preserved, and unmute worked. These zero-valued synthetic
samples verify playback mechanics, not audible quality. Offscreen GPU fallback
diagnostics occurred. No packaged build, macOS/device hardware run, native
whole-application acceptance, audible listening or live-site test was run.

Final syntax parsing covered all 11 changed Python files; `git diff --check`
passed.

## Required coordinated integration

1. **Foundation geometry contract:** `message.layout`, artwork `placement` and
   arbitrary design/presentation objects are opaque storage. No authoritative
   units, coordinate system, keys, inheritance rules or bounds are defined.
   Existing foundation fixtures contain arbitrary values such as width 100/300,
   top A/B and x 0.4; they do not establish rendering meaning. Coordinate these
   semantics and validation with Foundation and Messages, then implement/test
   distinct Layout A/B, crop/placement and overflow. This branch preserves and
   explicitly blocks nonempty custom geometry; default placement/readability
   is implemented. The full required custom-layout acceptance is unfinished.
2. **Saved Letters portable export:** its owned `portable_export.py` functions
   still recursively include the Play tree for ZIP. Repository-wide Python
   call-site inspection on October 15 found no application callers of
   `create_zip_package`, `create_publish_package`, or `create_single_html`;
   only the existing Single HTML test calls that service. The earlier wording
   about existing standalone export UI paths was incorrect. These functions must first
   call `generate_render(..., output_mode="display")`, then
   `copy_display_output(play, isolated_display)`, and give only that directory
   to archive/single-file packaging. Never export editable .lettersmith-project
   or private build diagnostics. Single HTML additionally needs its existing
   CSP/inline-script nonce handling repaired and data audio allowed narrowly:
   it currently removes the script nonce while inlining and does not allow
   data media. Test the standalone HTML/ZIP service entry points in a fresh
   directory and wire a real caller through the agreed export workflow. The
   Forge publish path is integrated; standalone export UI is absent in this
   baseline. No Saved Letters source was rewritten.
3. **Messages integration:** merge its page-bound document editor; the baseline
   UI still guards multiple independent Letters. Use the shared adapter and
   original page ID. Ensure pending edits flush through the existing shell
   lifecycle before canonical capture/save/replace/close. Verify A/B editing,
   reordering, embedded resources, fonts, geometry and combined preview after
   the geometry contract is agreed. Do not clone Template.py.
4. **Sound integration:** keep its audition player stopped on leaving Sound;
   Forge's view owns output audio while visible and existing shell visibility
   release handles hiding/restoration. Merge canonical sound changes, then
   verify audition/Forge exclusivity, playlist order, mute/volume/crossfade,
   ordinary navigation, restart and close in the actual merged application.
   Browser transport mechanics here do not prove sibling audition behavior.
5. **Sequential merge:** resolve Forge/generate/readiness/provider/test changes
   against the current foundation and sibling branches, preserving their
   owned changes. Run the focused suites above in fresh profiles and perform
   native end-to-end save/reopen/reset, Messages/Sound and portable export
   acceptance. This branch's checks do not certify the independently advancing
   primary checkout or a release.

## October 15 continuation and coordination request

The shared foundation still defines no geometry units or resolution API.
The primary Messages handoff independently identifies the same missing contract.
No Saved Letters feature branch/worktree or export integration was present in
the inspected local repository. Ownership remains as stated in the original
request and foundation-handoff.md; absence of an owner branch does not transfer
ownership automatically.

Completed on this branch:

- Merged the reviewed shared foundation, preserving all Forge changes and all
  unrelated primary-checkout work.
- Ran 80 tests: the updated foundation's 63 and the existing renderer's 17.
- Added a real regression in `test/test_forge_render.py`: use one image as both
  page artwork and an embedded message resource, save/reload through the
  foundation, remove the original fixture file, render and copy public output,
  then verify both consumers retain the exact image bytes.
- Ran that regression with Forge delayed-operation, fake publishing and
  canonical readiness checks: 58/58 passed. Counts overlap earlier runs.
- Reran `test_page_viewer` in a separate process using
  `C:\lsf-forge-1015-viewer`: 4/4 real WebEngine tests passed. Expected offscreen
  GPU fallback diagnostics occurred. Syntax checks for the foundation delta
  and new regression, plus `git diff --check`, passed.
- Corrected the earlier export UI claim after checking actual call sites.

The October 15 test commands used the same bootstrap shown above, profiles
`C:\lsf-forge-1015-b2` and `C:\lsf-forge-1015-shared`, and these selections:

```python
foundation_and_renderer = [
    'test_project_model', 'test_project_repository', 'test_project_session',
    'test_foundation_consumers', 'test_forge_render',
]
shared_resource_and_publication = [
    'test_forge_render.ForgeRenderTests.test_shared_artwork_and_message_image_survives_save_and_portable_render',
    'test_forge_session_output', 'test_github_publishing.GitHubPublishingTests',
    'test_github_publishing.GitHubAccountUITests',
    'test_readiness_editor_paths.CanonicalReadinessTests',
]
```

The first attempted test invocation encountered merge-conflict markers before
loading suites; it was not a test result. Both successful selections above ran
after all conflicts were resolved and the staged diff was checked.

The remaining coordinated changes are concrete:

1. **Foundation + Forge geometry:** define one documented, versioned layout
   interpretation in the existing canonical message/artwork objects, with a
   public resolver and validation. Proposed semantics for review: explicit
   logical canvas dimensions and a top-left text rectangle in CSS pixels;
   per-edge padding in the same units; uniform viewport scaling that never
   edits stored text/font values; scroll or explicit overflow error; existing
   empty layout retains legacy behavior. Resolve inherited defaults through
   the existing shared/page override mechanism, and validate artwork fit and
   position separately. Preserve unknown prior objects without guessing their
   units. This is a proposal, not a current foundation schema or implemented
   renderer capability. Foundation, Forge and Messages must use the same
   resolver before custom-layout acceptance can pass.
2. **Saved Letters export service + Forge caller:** narrowly update
   `portable_export.py` to use the canonical public allowlist, retain its public
   function names/output naming, preserve CSP nonce protection while inlining
   trusted script, and permit only required data media/font sources for Single
   HTML. Add export actions to the existing Forge action area that capture and
   validate the same immutable display input; do not redesign Saved Letters
   cards or invent another persistence system. Verify real ZIP/Single HTML
   output, resource privacy, cancellation, stale-project handling and a fresh
   browser load before marking those paths complete.

The user's original prompt expressly reserves foundation contracts and
Saved Letters source to their owners. Implementing the two items above here
requires a narrow reassignment/coordination decision; the continuation request
alone has not been treated as permission to override that boundary.

- Feature implementation: **Partial.** Real canonical default-layout rendering,
  preview, clean public assembly and guarded publish preparation are implemented
  and tested. Custom geometry and Saved Letters export wiring remain.
- Ready for sequential integration: **No.** Review this isolated branch, agree
  the foundation geometry contract, and integrate/verify the sibling export
  hooks and editor/audio lifecycle before marking Prompt 6 complete.
- Cross-feature verification: Foundation persistence, legacy media/message
  helpers, real generated viewer and local fake publishing were exercised on
  this branch. Merged Messages/Sound/Saved Letters UI, native app acceptance,
   custom layouts, standalone export UI and production publication remain unverified.
