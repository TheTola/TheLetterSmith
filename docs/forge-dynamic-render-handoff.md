# Forge dynamic rendering handoff — updated 2026-10-16

## Scope and starting state

Branch: `codex/forge-dynamic-render`.
Worktree: `C:\Users\Oluwatola Ayedun\.codex\worktrees\forge-dynamic-render\SmithLetter`.
Initial verified foundation snapshot: `5daa5e3b29bf1b6ca3019f56f1bb9c0d2aca6954`.
Current shared foundation incorporated: `1612235e5f5bf0c02e84cead87fc84242d30cac5`.
Forge implementation commit: `5e1d02163f80ed41d20accad5ce7f9913ff7cdd7`.
Foundation merge: `0e6e7ed`.
Continuation starting revision: `758c0db`.

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
The October 16 continuation explicitly authorizes completing both Foundation
geometry and portable export here. Their narrow shared changes are documented
below and in foundation-contract.md. No new serializer, migration, page registry,
message store, shell, or sibling editor was added. No dependency installation, remote push, provider
image generation, authentication, or live publication occurred.

## Changed files

| Path | Change |
| --- | --- |
| `forge_render.py` | Canonical snapshot adapter, validation, active-resource collector, staging, manifest, public-output allowlist and guarded export service. |
| `project_model.py` | Shared versioned geometry resolver, inherited values, snapshot geometry and page-specific readiness errors; authored schema unchanged. |
| `generate.py` | Existing build/cache APIs dispatch to canonical rendering; explicit legacy message overrides remain compatible. |
| `Template.py` | Independent composition, shared artwork/message canvas, crop/position, scrolling diagnostics, ID navigation and retained viewer/media controls. |
| `Forge_Tab.py` | Canonical capture, page-specific readiness, preview currency, staged publishing, ZIP/HTML/Website ZIP menu and guarded completion. |
| `portable_export.py` | Public-resource collection, payload hash verification, transactional export and nonce-preserving Single HTML with embedded audio. |
| `readiness.py` | Canonical page-specific issues and separate preview/final readiness. |
| `publishing/github_pages.py` | Optional `persist_publication=True`; Forge passes False and persists verified success under its captured session guard. |
| `test/test_forge_render.py` | Real synthetic render/output/resource/concurrency and frozen-publish regression checks. |
| `test/test_forge_session_output.py` | Deferred preview/publish/session/revision/close safety checks. |
| `test/test_page_viewer.py` | Real WebEngine navigation, distinct layouts/crop/scrolling, GIFs, portable audio and independent output after source deletion. |
| `test/test_project_model.py` | Geometry inheritance, nonmutating resolution, bounds, unknown-layout retention and shared artwork canvas. |
| `test/test_readiness_editor_paths.py` | Canonical readiness, existing viewer assertion and real Forge export-menu/button dispatch. |
| `test/test_github_publishing.py` | Existing local transport double verifies deferred publication persistence. |
| `docs/forge-dynamic-render-handoff.md` | This report. |
| `docs/foundation-contract.md` | Coordinated geometry contract and consumer APIs. |

The only provider API addition is optional and preserves existing callers.
When integrating it, retain unrelated TLS/authentication work in the primary
checkout. Foundation's project_model.py and the existing portable_export.py were
extended under the October 16 authorization; Saved Letters cards remain unchanged.

## Shared rendering adapter

Forge owns `forge_render.py`, `generate.py`, `Template.py`, `Forge_Tab.py`, and
renderer readiness. Messages should consume this adapter, not fork templates.

`capture_render(session, output_mode="preview" | "display") -> RenderInput`
uses `session.project.render_snapshot()`, `session.capture_target(...)`, and
the session's project/asset roots. The frozen dataclass contains a recursively
read-only snapshot, session/project identity, revision, output mode, dependency
fingerprint and captured targets. Pages retain canonical IDs/order/types,
artwork, active message documents, resolved settings and resolved layout. It is derived output
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
| `export_render(session, destination, *, output_format="zip", render_input=None, cancelled=None)` | Builds an isolated final display snapshot, packages `zip`, `html` or `publish_zip`, and guards the final transaction against cancellation, changed inputs and project replacement. Leaves the active preview untouched. |

Messages can capture its shared session in preview mode, validate, build to its
own temporary generation, load index.html in its existing view, and call
`window.LetterSmithViewer.goToPage(page_id)`. `currentPageId()` reports playback
position. `layoutIssues()` returns browser-measured overflow diagnostics;
`lettersmith-layout` emits changes. Navigation never calls `session.select_page`: editor selection and
playback remain separate. Before activating a delayed Messages preview, check
`render_is_current`; release that view before removing its generation.

`Template.template_for_project_pages(pages, *, template_html=None)` is the lower
level renderer DTO adapter: each item has `id`, canonical `type`, `name`,
`source`, `message_html`, `overlay_style`, and optional normalized `layout`
(`message`/`artwork`). It validates geometry through `resolve_page_layout`, then emits HTML/CSS/JS using stable
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
without truncation. Empty geometry retains default placement and readability.
Version-1 `css_px` geometry uses an explicit logical canvas and text rectangle,
per-edge padding, alignment and scrolling. Artwork shares that canvas with
`contain`/`cover` and normalized position. See foundation-contract.md for exact
inheritance, bounds and error behavior. Unknown prior layouts remain savable
but cannot be rendered by guessing units. Visible scrolling notices and
page-specific diagnostics prevent silent overflow clipping.

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

Forge's Export menu calls `export_render` for ZIP Package, Single HTML and
Website ZIP. A save dialog captures the requested destination; the display
snapshot is captured before the dialog and revalidated before commit. An edit
after successful commit is reported as an export of an earlier revision.
The existing portable functions retain their public names/default output names
and add optional `destination`, `cancelled`, `commit_output` keyword arguments.
Canonical ZIP contents use the manifest allowlist; legacy bundles use validated
runtime references. Hashes are checked on actual archived/inlined bytes. Single
HTML embeds active images/fonts/audio and trusted script resources, preserves
both script nonces and permits data media without allowing inline script
generally. Page-turn sound paths are explicit literals so they are embedded too.
Single HTML retains the existing 60 MB limit; larger output uses ZIP.

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
resources and font bytes, media bytes/settings, resolved readability/geometry,
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

## October 15 foundation continuation

The reviewed foundation was merged without sibling feature work. The
shared-artwork/message-resource regression verifies save/reload, removal of the
original fixture, and real public rendering with both consumers retaining exact
bytes. The foundation/renderer selection passed 80 tests; the subsequent
resource/publication selection passed 58; the separate viewer suite passed 4.
Counts overlap the earlier results. The previously proposed geometry and export
changes were still blocked on ownership then; the user's October 16 instruction
explicitly authorized completing both here. They are now implemented.

## October 16 final verification

Profiles use the bootstrap above; runtime data is isolated from live projects.
The exact final suite selections and results are:

| Profile | Selection | Result |
| --- | --- | --- |
| `C:\lsf-forge-1016-final` | `test_project_model`, `test_project_repository`, `test_project_session`, `test_foundation_consumers`, `test_forge_render` | 89/89 passed. Includes 22 real renderer/export cases and 67 foundation/consumer cases. |
| `C:\lsf-forge-1016-last` | `test_project_model`, `test_forge_session_output`, `test_readiness_editor_paths.CanonicalReadinessTests`, `test_github_publishing.GitHubPublishingTests`, `test_github_publishing.GitHubAccountUITests`, `test_message_overlay`, `test_message_security`, `test_image_animation.ImageAnimationTests.test_generated_gallery_controls_frames_and_survives_export` | 117/117 passed, including the final numeric-bounds change and legacy Single HTML export. Overlaps the first row by the 26 model tests. |
| `C:\lsf-forge-1016-browser3` | `test_page_viewer` | 5/5 passed in a separate real WebEngine process. |
| `C:\lsf-forge-1016-ui` | `test_readiness_editor_paths.ReadinessEditorAndProjectPathTests.test_preview_options_follow_preview_readiness` | 1/1 passed. Actual Forge widget/button opens the menu, gates export by readiness and dispatches Single HTML. The renderer service/action path is separately exercised by the real export test. |

The five-page fixture has a red Letter A at logical box (40,130,320,170), blue
Letter B at (80,330,280,190), distinct fonts/alignment, scrolling text, an animated
GIF, two valid PCM tracks and bundled intro/page-turn effects. The browser test
renders a public directory, extracted ZIP and Single HTML after deleting the
synthetic source project. Each output navigates the actual page IDs; layout
ratios and authored 24px/30px fonts survive 1000x700 and 390x844 viewports. A wide
1200x600 image uses cover crop at 80%/30% on the 400x600 logical canvas. GIF
sources advance. The project audio keeps its source/time across navigation;
intro and flip audio load without media errors, including data URLs in Single
HTML. All three formats report no JavaScript or CSP violations.

Eight rendered screenshots and `layout-export-observations.json` are under
`C:\lsf-forge-1016-browser3`. Representative portrait/landscape images were
visually inspected. The flat colors/gray control images are intentional synthetic
fixtures. These offscreen checks establish real composition and playback
mechanics, not audible quality or whole-application native acceptance.

Additional output cases verify private-file exclusion, retained message/artwork
bytes, shared fonts, source hash changes, reorder/save/reload, invalid layouts,
canceled/replaced/edited exports preserving an existing destination, and actual
Forge export dispatch with only its file dialog mocked. Website ZIP includes
root index.html, .nojekyll and the existing security headers. No provider request,
remote push, authentication or live publication ran.

Early attempts encountered missing sound-effect fixture setup, mistyped suite
names, a shell placeholder typo and an insufficient six-second intro timeout.
They were corrected before the successful selections above. Trusted input plays
the real intro effect before navigation unlocks; the browser test now allows
that existing behavior. Screenshot capture waits for compositing and overlay
fade completion. Offscreen GPU fallback and expected fake-provider failure
messages were diagnostic output, not failed final checks.

Final syntax parsing passed for all nine Python files changed in this
continuation, and `git diff --check` passed. The primary checkout remains at
`1612235e5f5bf0c02e84cead87fc84242d30cac5` with its unrelated dirty work preserved.

## Sequential integration and remaining application verification

1. Merge this branch onto the reviewed foundation, preserving independently
   advancing primary-checkout work. The coordinated shared changes are limited
   to project_model.py's resolver/readiness/snapshot additions and the existing
   portable_export.py service. Schema version, migration and persistence stay
   unchanged. The provider's optional persistence flag must coexist with the
   primary checkout's unrelated TLS/authentication work.
2. Messages consumes `ProjectModel.resolve_layout`, `capture_render`,
   `validate_render`, `build_render_to` and the viewer's page/diagnostic hooks.
   Its editor should write the documented versioned layout through
   `update_message`/`update_artwork`/settings operations; older unknown geometry
   must remain visible as an actionable error. Merge its page-bound editor and
   verify pending edit flush before capture/save/replace/close through the
   existing shell lifecycle. Forge does not add competing Messages controls.
3. Sound owns audition playback. Stop audition on leaving Sound; Forge's existing
   view/shell lifecycle owns display audio while visible and releases it on
   restore/reset/shutdown. Merge canonical Sound changes, then verify audition
   exclusivity, playlist/crossfade, mute/volume and restart in the merged app.
4. Rerun the focused selections in fresh profiles after merge. In the actual
   application, edit A/B independently, reorder, save/reopen/reset, preview in
   supported modes, export ZIP/HTML and close during delayed work. Verify no
   editor, preview or audio state leaks into a replacement project. This branch
   does not claim sibling UI, packaged Windows/macOS/device or live-site
   acceptance. The historical unrelated geometry failures above are not fixed
   by this task.

- Feature implementation: **Complete on codex/forge-dynamic-render.** Canonical
  dynamic composition, shared geometry, crop/scrolling, guarded preview/publish
  preparation and real portable export actions are implemented and exercised.
- Ready for sequential integration: **Yes.** No remaining ownership decision or
  implementation blocker for the two authorized integrations. Preserve sibling
  changes and perform the merged application checks listed above.
- Cross-feature verification: Foundation persistence/consumers, legacy image and
  message helpers, real exported viewer/media, Forge menu dispatch and local
  fake publishing were verified. Merged Messages/Sound/Saved Letters workflows,
  native/package acceptance, audible listening and production publication remain
  integration/release checks.
