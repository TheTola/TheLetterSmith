# Images selection redesign — 2026-10-20

Starting revision: `main`, `9540dcdfba92d21acf4b55b523e1bef97fcdbf2a`.
This work changes the production horizontal Images strip and its existing picker,
history service, and Command route. Existing dirty work was preserved, including
the compact header and help placement. Other application/release changes continued
in this checkout during verification; this document does not claim their ownership
or validation.

## Behavior and ownership

- Clicking artwork opens recent choices; double-click still imports a file.
  Return/Space opens choices from the strip; Ctrl+O imports. The single-click
  timer respects the system double-click interval and captures the session/page
  identity, so an old click cannot open a picker in a replaced project.
- The existing `recent_media.json` service retains ten available, distinct,
  newest-first images independently for `cover`, `illustration`, `letter`, and
  `end_page`. Every project/page in the same application profile shares those lists.
  Successful canonical imports update history; changing selection does not.
- A category with no available history shows its three bundled stock images.
  Old per-page history remains on disk but is no longer used as a fallback or
  written by the Images importer. New history has one application/type scope.
- Clicking Command clears the four history lists, including a repeated click
  while Command is selected. Programmatic tab restoration does not clear them.
  Clearing removes metadata only: source images, cached image files, assigned
  artwork, messages, prompts, saved projects, and audio history remain intact.
  Failed atomic writes leave history intact and report the failure.
- Canonical import/staging/target validation remains in `PageArtworkController`.
  Recent choices reuse that path, retain readable labels, and preserve GIF bytes.
  Imported artwork belongs to the canonical project resource graph, independently
  of disposable history copies.
- Category cache directories use short canonical names. This fixes the observed
  Windows path-length failure for the previously hashed Illustration/End Page
  directories. Existing recorded paths remain readable.
- Rounded artwork cards, explicit type/name/number labels, subtle category colors,
  visible selection/focus, and themed thumbnail grids replace the older plain
  strip/popup presentation. The popup supports ten choices and keyboard activation.
  Stock labels use current type names rather than legacy stock filenames.
- The page context menu retains import, crop, GIF settings, clear artwork,
  prompt/message routes, move, add, delete, and undo; it also restores access to
  canonical Duplicate page and Open Artwork Folder operations.

## Changed files

Production: `images_grid.py`, `Image_tab.py`, `images_workspace.py`,
`recent_media.py`, `Nexus.py`.

Existing tests: `test/test_images_strip.py`, `test/test_images_launcher_layout.py`,
`test/test_image_animation.py`, `test/test_restored_workspaces.py`.
This handoff is the only new repository file for this task.

## Verification environment

Windows; repository `.venv/Scripts/python.exe` (Python 3.13).
All test processes use `release/verify_tests.py`, isolated runtime/TEMP/QSettings
directories, a filesystem write guard, and blocked external networking.
Evidence directory: `C:/Temp/lettersmith-images-20261020`.

Baseline command:

```powershell
.venv/Scripts/python.exe release/verify_tests.py --output C:/Temp/lettersmith-images-20261020/baseline --module test_images_strip --module test_images_workspace --module test_images_launcher_layout --module test_image_animation
```

Baseline: 85 passed, two failed out of 87. Both failed checks expected the obsolete
vertical/hidden selected-card layout. They now test horizontal keyboard scrolling
and non-overlap of the actual visible strip controls.

The native isolated source window was visually inspected at 1200×820 and maximized:
artwork cards, category labels, selection/focus, and the ten-choice two-row popup.
Desktop input intervention prevented claiming an uninterrupted automated native
interaction journey. Actual single-click selection, tenth-choice keyboard
activation, Command clearing, preserved resource bytes, and page/session targeting
are exercised through production Qt widget/controller paths in the test suite.
No packaged build, macOS run, live publication, or audible playback check was
performed for this visual/history change.

Intermediate checks exposed and repaired the category path-length failure. One
intermediate combined shell process crashed in Qt `QAudioOutput` initialization
when constructing its second Nexus window; both shell checks then passed in
separate isolated processes. Its log is retained under `checks2`; it is not counted
as a passing run.

Final command, after the production changes and corrected test fixtures:

```powershell
.venv/Scripts/python.exe release/verify_tests.py --output C:/Temp/lettersmith-images-20261020/verified --module test_images_strip --module test_images_workspace --module test_images_launcher_layout --module test_image_animation --module test_restored_workspaces.RestoredWriterTests.test_type_history_is_limited_and_independent_of_page_identity
```

Final result: **94 passed, zero failures/errors/skips**. Counts: strip 16,
workspace 27, shell/Command 2, animation/history 48, existing cross-project
history regression 1. The final combined shell module also passed. The injected
storage-failure traceback in that module is expected regression evidence.
`git diff --check` passed for all task-modified source/test files.

The tests cover ten-entry eviction/order/deduplication and four-category
independence; shared lists across pages/projects; three-stock fallback after
clearing; retained GIF bytes after original-source removal/project replacement;
real single-click import and keyboard selection of the tenth item; readable
recent labels; rejection of delayed first-session clicks after replacement;
repeated Command clicks and an atomic-write failure with artwork/audio retained.
Existing crop, animation, import-worker, clear, reorder, delete/undo, and layout
regressions remain included. Layout checks exercise all installed themes at
1920×1080, 1200×820, 960×600, and 900×480.

Results and per-module logs: `C:/Temp/lettersmith-images-20261020/verified/`.
Source hashes and working-tree provenance:
`C:/Temp/lettersmith-images-20261020/verified/source-state.json`.
This delivery is working-tree changes on the starting revision, with no commit,
push, dependency installation, or user-library modification performed by this task.
The isolated native review window remains available; it uses synthetic temporary
project data rather than the user's saved letters.

## Follow-up visual polish

`images_grid.py` now uses quieter card borders, larger page titles, compact type
badges, clearer selection/focus, and a grouped selected-page panel. Card widths
fit the available space; field rows stack according to their measured width.
`Image_tab.py` refines the existing picker typography and button spacing. No
history, import, project, or resource semantics changed in this follow-up.

Final verification on the same revision plus working-tree changes:

```powershell
.venv/Scripts/python.exe release/verify_tests.py --output C:/Temp/lettersmith-images-polish-20261020/final-checks --module test_images_strip --module test_images_launcher_layout --module test_image_animation
```

**66 passed, zero failures/errors/skips.** The initial narrow-layout alignment
failure was repaired before this final run; no test assertions were changed.
The native Windows source UI was inspected at 1400×900, including opening the
ten-image picker with a single click and dismissing it with Escape. Screenshots:
`C:/Temp/lettersmith-images-polish-20261020/images-final.png` and
`C:/Temp/lettersmith-images-polish-20261020/picker-final.png`.
All verification used isolated profiles. The final review window remains open;
its exit was canceled to preserve changes made during review. Unrelated
application instances were not touched.

## Minimalism and unique history refinement

Baseline: `main` at `b3386138f9cf380a0a834bfa878225ac76394089`.
Existing Prompt Writer changes were preserved. The shared Foundation contract
now records “Minimalist wherever possible”: preserve capabilities and remove
redundant controls. The Images Page actions button and its duplicate launcher
were removed; the existing right-click menu and its operations remain.

Recent images now compare decoded content, not filenames or encoded file bytes.
Equivalent PNG compression/metadata and GIF comments cannot create duplicate
choices. Each category independently keeps ten unique images, newest first;
reusing one moves it to the front, while different images with the same filename
remain separate. GIF comparison includes every frame, duration and loop count.
Existing duplicate lists are filtered on read without rewriting their file.
The bounded comparison cache uses file-change metadata to detect replacements.
The shared history format, project artwork and source resources are unchanged.

Production changes: `images_grid.py`, `recent_media.py`. Existing regression
files updated: `test/test_images_strip.py`, `test/test_image_animation.py`,
`test/test_images_launcher_layout.py`. Documentation: this handoff and
`docs/foundation-contract.md`.

Baseline Images strip/animation checks: 64 passed. Three new content-comparison
regressions reproduced the old behavior before the fix and passed afterward.
An intermediate combined run hit a native Qt access violation during
`QListWidget.clear()` in the existing project-replacement regression; its isolated
rerun passed. Logs are retained under
`C:/Temp/lettersmith-minimalism-20261020/verified/`; that run is not a passing
verification result.

Final command after the implementation and layout expectations were updated:

```powershell
.venv/Scripts/python.exe release/verify_tests.py --output C:/Temp/lettersmith-minimalism-20261020/final --module test_images_strip --module test_images_workspace --module test_images_launcher_layout --module test_image_animation --module test_restored_workspaces.RestoredWriterTests.test_type_history_is_limited_and_independent_of_page_identity
```

**97 passed, zero failures/errors/skips** (19 + 27 + 2 + 48 + 1). The complete
strip suite, including the replacement regression, passed on this final run.
Tests exercise real service/import/widget routes in isolated offscreen processes,
including all installed themes and window sizing. No new native desktop,
packaged-app or macOS check was performed for this refinement. `git diff --check`
passed. This refinement's delivery includes only the seven files listed above;
concurrent Prompt Writer changes are excluded.
