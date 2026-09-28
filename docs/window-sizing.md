# Window sizing investigation and implementation

## Scope and launch path

The active process launches `.venv/Scripts/pythonw.exe Main.py`. `Main.main()`
configures writable runtime paths separately from resources, creates QApplication,
then imports and constructs `Nexus.Nexus`, a PySide6 QMainWindow. `Nexus` owns the
shell, preview budget, feature stack, overlays and saved geometry. Individual tabs
own their layouts. `window_chrome.py` owns native frameless hit testing. `ui_theme.py`
and `image_button.py` own typography, control dimensions and artwork rendering.
Existing uncommitted changes are retained. No exported-letter HTML or artwork is
part of this repair.

## Confirmed failures before changes

* **Native border dragging does nothing:** drag a main-window border or corner on
  Windows. `FramelessWindowController.native_event` returns the correct resize hit
  (for example HTRIGHT=11), but Windows receives no resize system command. The
  actual style is `0x960b0000`, without `WS_THICKFRAME`. Adding that flag in an
  isolated fixture changes the style to `0x960f0000` and the same right-edge drag
  changes width from 1400 to 1300. The controller now restores that native flag
  on Show/WinIdChange, with a Windows-platform guard. Qt still removes the visible
  frame. Noninteractive main-title regions return HTCAPTION for native movement
  and snapping; title buttons retain their Qt click handling.
  This matches [Qt's Windows frameless-window implementation](https://raw.githubusercontent.com/qt/qtbase/v6.11.1/src/plugins/platforms/windows/qwindowswindow.cpp),
  which omits WS_THICKFRAME for FramelessWindowHint and handles WM_NCCALCSIZE.
* **Images overlap and become unreachable:** show Images at 1920x1080, shrink to
  1400x900, then 1180x820. With the real Cyber Forge assets, the last size allocates
  273 px to a panel whose minimum size hint is 657 px. Cards 1/3 and 2/4 intersect;
  the bottom Clear buttons have empty visible regions. `ImageTab.resizeEvent`
  reflows fixed 300 px cards into two rows without coordinating the required height
  with `Nexus._update_preview_geometry`. Card maximum height (280) is also below
  the themed content's 286 px minimum hint.
* **Sound panels overlap:** at 1180x820 the panel receives 431 px but its minimum
  hint is 573 px. The 230 px minimum on `SoundTab.mode_stack` pushes its rectangle
  into the Now Playing label and transport controls. The shell reserves preview
  height independently of the lower panel's requirements.
* **Display bounds are ignored on launch:** native Windows, `QT_SCALE_FACTOR=2`,
  screen 960x540 / usable area 960x516 logical pixels. Nexus opens at 1400x900 and
  retains a minimum of 1180x820, extending beyond the available desktop.
* **Reopening changes a valid normal size:** native 125%, saved rectangle
  (40,6,1180,820), reopened rectangle (40,26,1180,799). A standalone frameless
  QWidget reproduces Qt's extra title-bar allowance. `Nexus` now retains the exact
  normal rectangle in optional `ui_window_normal_geometry`, alongside the existing
  Qt geometry and maximized flag. Old settings still restore through Qt; invalid
  rectangles are ignored, and valid rectangles are bounded to the selected screen.
  Only window-state data is added; no settings schema version or project data changes.
* **Display recovery uses stale geometry during a monitor transition:** Phase 4
  caught this in the initial Phase 2 recovery implementation. A native drag from
  the primary monitor to the left monitor at Qt scale 1.25 emits
  `screenChanged` while QWidget still reports (180,50,1400,900). After the move,
  QWindow reports (-959,40,1120,720). Choosing a screen from the old rectangle
  can send recovery to the primary display, and fitting the old size can enlarge
  the window. `Nexus._window_screen_changed` now queues one recovery using the
  destination screen and QWindow's current logical rectangle. The controller's
  native move/resize lifecycle defers pending recovery until that interaction
  ends. Ordinary same-screen movement and resizing do not trigger recovery.
* **Curtain popup loses its anchor:** width matches the field at 250 and 340 px
  in all six themes. Moving the containing window 20 px while the menu is open
  leaves the menu at its old x coordinate. `_sync_popup_width` handles width but
  not ancestor movement or theme/screen changes.
  Native screen-edge testing additionally reproduced a one-pixel left offset when
  the field's right border touches the desktop edge: field x=1670, width=250;
  menu x=1669, width=250. Qt's menu placement adds the inset. The selector now
  aligns the popup's x coordinate on Show when the whole field fits the screen,
  retaining Qt's vertical placement and submenu behavior.
* **Editor exceeds a scaled desktop:** native Windows at 200%, the Letter Editor
  opens at 1100x720 on the same 960x516 work area. Its formatting row contributes a
  962 px minimum width. Phase 3 therefore also wraps only that row in a horizontal
  scroll viewport (`Editor._build_toolbar_actions`), measures its height after layout,
  and uses the shared screen-recovery helper on opening. Risk: keyboard focus and
  formatting actions must remain accessible; verify these with the existing editor
  tests and a narrow native window. Prompt Writer's existing screen-aware animated
  popup fits at 783x433 and needs no speculative geometry rewrite.
* **Import errors can freeze the interface:** validation with a long audio filename
  exceeded Windows' path limit in the temporary project. A native thread dump
  showed `SoundTab._import_failed` opening `show_lettersmith_message` on the import
  worker thread. `_start_import` connected worker signals to Python lambdas that
  called UI methods directly. Success and failure now pass through explicitly
  queued SoundTab signals and slots, retaining the existing generation guard.
  This is an additional content-state failure discovered during Phase 4, not the
  cause of the image-card overlap. A shorter temporary root allows the genuine
  long-name import to complete; failed imports are also tested independently.

The feature QStackedWidget also reports the largest minimum hint among inactive
pages. This is measured constraint propagation, not proof of spontaneous growth.
Native dragging, transition races and secondary-window recovery were validated
separately from these layout measurements.

## Plan, dependencies and acceptance

| Phase | Files and functions | Change and reason | Risks and verification |
| --- | --- | --- | --- |
| 1: reproduce | Main.main, Nexus, tab layouts, window_chrome, curtain_controls, theme/button code | Trace real imports; measure empty/current layouts and native scale; inspect existing tests and concurrent edits. | Use isolated writable projects with actual read-only resources. Record observations separately from hypotheses. Complete before production edits. |
| 2: shell and content | window_chrome geometry helpers; Nexus._restore_window_preferences, resizeEvent, _make_page_surface, _update_preview_geometry; ImageTab sizing/resize; ImageAssetCard constraints | Recover windows onto the appropriate available screen; nominal minimum 960x600 capped to the screen. Put scrolling only around feature controls, extending the existing Forge design. Allow Images to request height for its current width without making a previous wide grid its permanent minimum. Keep artwork dimensions/aspect and preview outside the scroller. | Scroll ownership can affect floating controls, tab animations and size hints. Verify repeated width/height changes, reachable last controls, six themes, no accumulating minimum, preserved preview aspect/viewport, and unchanged outer size on tab/theme changes. |
| 3: popup and event ownership | CurtainStyleComboBox._sync_popup_width, popup lifecycle/event handling; confirmed secondary paths only if reproduction warrants | Derive root popup width from the field. Close root and submenus if the field/ancestors move or resize or theme/DPI changes. Reopen at current geometry. Keep shared model, calculated colors and readiness behavior. | Avoid stale ancestor filters, duplicate connections, nested menus closing during normal navigation, or changing selection. Test exact border extents, shrink/grow, all themes, submenu selection, unavailable state and screen edges. |
| 4: validation | Existing test infrastructure plus focused window-sizing regressions; native temporary validation app | Use actual resources in isolated projects; empty/populated states, all tabs, independent dimensions, transitions, six themes, 100/125/150/200% process scaling, window persistence and native edge/corner interactions. | Automated native geometry is distinct from injected mouse dragging; process scaling is distinct from mixed-monitor movement. State all unsupported checks explicitly and provide manual steps. |

Phase 4's import failure added a scoped correction in `sound_tab.py`:
`SoundTab._start_import`, the two result slots and their queued signals. Dependencies
and file formats are unchanged. Regression checks exercise real worker success and
failure, assert GUI-thread delivery, and reject results from stale generations.

Native validation also added the controller style/caption correction and the
screen-transition ordering correction above. These extend Phase 2's window owner,
without adding another content-layout owner. Their risks are native maximize
bounds, title-button clicks, early window creation and mixed-scale coordinates;
the validation includes these paths and an early-message recursion regression.

## Geometry ownership

* Window state and screen recovery own only top-level geometry. Recovery runs on
  restore/display changes, not on every ordinary resize or tab switch.
* Qt layouts and per-page scroll viewports own feature rectangles. Content retains
  readable minimum dimensions; excess content scrolls instead of overlapping.
* The shared preview calculates a bounded content size and preserves source aspect.
  Preview refreshes are coalesced after layout. Floating help/loading/Command UI
  remains explicitly owned by the existing shell positioning methods.
* Controls retain semantic theme dimensions. Raster resolution and transparent
  padding do not become top-level window constraints. No artwork is regenerated.

The nominal minimum is 960x600 logical pixels. The widest controls panel has a
912 px minimum hint; the remaining width covers shell margins and the scroll bar.
Image cards retain their readable 300 px width and reflow into one to four columns.
At short heights, the feature controls scroll below the bounded preview; the title,
tabs and preview do not scroll away. A smaller available desktop caps the nominal
minimum, allowing the 960x516 work area at 200% process scaling to remain usable.
Forge's browser preview retains its existing minimum zoom; at 1180x820 this can
require a small amount of scrolling that the old Forge test prohibited.

## Results

All four implementation phases are complete. The final checks and their precise
coverage are listed below; unavailable platform checks remain manual release checks.

### Changed files

| File | Purpose |
| --- | --- |
| `window_chrome.py` | Native Windows resize style, caption hits, drag lifecycle, and shared logical screen-bound geometry helpers. |
| `Nexus.py` | Screen-aware minimum/restore, exact normal-rectangle persistence, deferred display recovery, per-feature scrolling, bounded previews and coalesced refresh. |
| `Image_tab.py` | Remove the contradictory card height cap; coordinate one-to-four-column reflow with scroll content height. |
| `Editor.py` | Fit the editor to its screen and scroll the formatting row horizontally when necessary. |
| `curtain_controls.py` | Keep root popup border width/position tied to the field and close stale popup/submenu anchors. |
| `sound_tab.py` | Deliver real import-worker results and failure dialogs on the interface thread. |
| `test/test_window_sizing.py` | Regressions for content constraints, geometry, transitions, popup anchoring, editor access and worker delivery. |
| `test/test_command_immersive_layout.py` | Preserve the minimum Forge browser viewport and verify accessible scrolling at constrained height. |
| `docs/window-sizing.md` | Evidence, phased plan, ownership, implementation and validation record. |

### Validation coverage

* The final focused sizing suite passed **13 tests**. It exercises all six themes,
  five tabs and six independent
  width/height combinations (180 layout cases), transitions, hidden pages, image
  aspect changes, invalid settings, saved normal/maximized geometry, popup
  submenus/anchors, editor formatting access and actual audio worker success/failure.
* Native Windows automated runs at process scale 100%, 125%, 150% and 200% each
  exercise all six themes and five tabs, three screen-capped sizes, popup border
  alignment including the desktop edge, secondary windows, dismissible import
  failure, maximize/minimize/restore and exact close/reopen geometry. The 100% run
  includes four imported stock images, a cleared image, a real playlist, a long
  imported filename and rendered message content.
  After the native-frame and monitor fixes, the populated 200% run passed **95
  geometry records**, including a 480-word message and the complete state/reopen
  checks. The existing Command layout suite passed 20 tests; the final Command
  interaction run passed 17 tests. Python compilation and scoped `git diff --check`
  also passed. No full-repository test pass is claimed.
* Actual mouse dragging changed all four edges and all four corners. Recorded
  sizes include 1400x900 -> 1300x900 -> 1300x750 -> 1200x750 -> 1200x650, then
  four corner changes to 1040x610. Native keyboard sizing grew width to 1051.
  Main-title dragging moved the rectangle by the requested displacement; maximize
  filled exactly 1920x1032 and restore returned to the preceding rectangle.
* Native top-edge snapping on the left display maximized to its logical work
  area (-1920,0,1536,826) at process scale 1.25. Native dragging into that display
  also exercised the stale-geometry correction.
* Automated native placements completed primary -> left -> right -> left ->
  primary with per-screen Qt factors 1/1.25/2. Each settled rectangle fits the
  destination work area, including the 960x516 right display. These are native
  Qt windows moved by the harness, not five completed manual drags.
* Relevant existing layout, theme, artwork-button, curtain, sound, Message and
  saved-letter overlay tests were run. A combined 244-test run had 243 passes and
  one named-style font assertion failure (Aldrich versus Papyrus). The same failure
  was reproduced using the pre-change Editor after the theme suite; the theme
  suite (49) and named-style suite (17) pass separately. It is an existing
  order-dependent test failure, not claimed fixed here.

The native harness, geometry JSON and per-theme screenshots are retained in
`C:/Users/Oluwatola Ayedun/.codex/visualizations/2026/09/17/01a0b0fd-68b6-7062-8a6f-5209bc02bed7/window-sizing/`.
`interactive-edges.json` and `interactive-snap.json` record mouse interactions;
`monitor-transitions/geometry.json` records the five automated monitor placements.
Pre-change Images/Sound screenshots and geometry are retained under
`C:/Users/OLUWAT~1/AppData/Local/Temp/smithletter-resize-evidence-0ed2qa6q/`.
The `baseline/` copies preserve the already-dirty files from before this task.

### Limits and manual release checks

* Qt process/per-screen scaling was varied; Windows display settings were not
  changed. A complete drag round trip between monitors with different **OS** DPI
  settings and a physical monitor disconnect were not performed. On such a
  machine, drag the restored window in both directions, maximize/restore on each
  monitor, close on the secondary monitor, disconnect it, and reopen. Confirm the
  title/buttons remain reachable and the window fits a remaining work area.
* The input tool confines drag endpoints to the captured window. Outward border
  dragging could not be injected. Inward dragging of every edge/corner and native
  keyboard growth were verified; manually grow each edge/corner and verify no
  jump or accumulating minimum. Repeat with left/right half-screen snapping.
* macOS/Linux native window managers were unavailable. Verify launch, shrink/grow,
  maximize/restore, editor access and popup borders on those supported platforms.
  Windows-only native calls are guarded; cross-platform layout tests run offscreen.
* No artwork, exported web layout, project content or unrelated dirty edits were
  changed. No installer/package was built as part of this source repair.

## Forge follow-up: fit the viewport without outer scrolling

The follow-up request requires Forge's non-text geometry to be 10% smaller and
the workspace to fit the window. This supersedes the earlier decision to allow
Forge controls to scroll at constrained heights; the saved-letter library and
the letter viewer retain their own legitimate content scrolling.

### Evidence and implementation

Before this follow-up, the real `Main.bootstrap_qt` text-fit guard and repository
assets reproduced a horizontal range of 6 and a vertical range of 237 at
960x516 in Cyber Forge. The controls requested 928x389 in a 922x152 viewport.
At 1920x600 the vertical range was still 149. Light also overflowed vertically.

The repair gives each existing layout and sizing system one responsibility:

1. `ui_theme.py` and `image_button.py`: extend the existing button sizing system
   with an opt-in geometry scale. Theme refreshes and artwork hints must honor
   that scale without changing typography. Source bitmap height must not
   override the long action buttons' calculated aspect ratios. Ordinary buttons
   retain the previous default sizing.
2. `Forge_Tab.py`: apply 0.9 to nominal button geometry and reduce control
   spacing, selector dimensions and padding. Standard artwork controls now use
   184x61 instead of 205x68; small controls use 118x40 instead of 131x44. Text
   fit remains a lower bound. Long values wrap within their allocated space,
   with full title/recipient/account values retained in tooltips. One coalesced
   timer handles long-action geometry, responding to width changes without
   accumulating callbacks or feeding height changes back into resizing. In the
   compact arrangement, controls shrink while the centered rows retain their
   order. Long action artwork shrinks in both dimensions with text fit as the
   lower bound.
3. `Nexus.py`: keep the preview above the format row and feature controls at
   every window size. Reserve the controls' actual height-for-width requirement
   and scale the preview into the remaining space. Compact spacing, button
   geometry and margins apply to Forge, Sound and Message; no side column or
   layout reparenting is used. Sound reserves the active music card's minimum
   height without inheriting the playlist list's preferred height. Help shrinks
   beside the preview instead of reserving an extra row in short windows.
   All three panels must have zero outer scroll ranges and reachable controls.
   Window preview mode retains the 1600x760 CSS viewport while WebEngine can
   scale to it. Below a 400x190 logical-pixel preview, its 25% zoom limit means
   the page uses a smaller responsive viewport of the same aspect ratio. This
   does not grow the app, move controls above/beside the preview, or reload it.

The main regression risks are theme refreshes restoring old button minimums,
wrapped labels requiring more height than `minimumSizeHint()`, repeated layout
callbacks, and previews overlapping controls after rapid resizing.
The updated existing tests cover these boundaries, unchanged text sizes,
all six themes, all three preview shapes and repeated shrinking/growing.

### Follow-up validation

* 111 relevant tests passed in separate suites: window sizing (14), themed
  button states (28), theme service (49), and Command/preview layout (20).
  The sizing suite checks long title, recipient and account values; normal
  identity/account labels must also have enough height to display their text.
* Python compilation and the scoped whitespace check passed.
* 216 native geometry checks passed at Qt process scales 1 and 2, across six
  themes, three preview modes, and repeated sizes including 960x516 and
  1920x600. Every check required zero outer scroll ranges and containment of
  the preview, format controls, Help icon and primary actions. Each run loaded
  the stock page once; Window mode retained its 1600x760 CSS viewport. At scale
  2 the available screen clamped every requested size to 960x516 logical pixels;
  the varied larger native sizes were checked at scale 1.
* Native mouse checks opened the stock letter, shrank the window to 960x600,
  maximized to 1920x1032, and restored it. The preview stayed open and controls
  reflowed. Scrolling over the Forge controls did not move the workspace.
* Native checks use the real Qt bootstrap, bundled fonts/artwork, and a loaded
  stock-letter web page. Authentication and fresh bundle generation are isolated
  from the layout fixture. Fresh populated bundle generation exceeded the
  diagnostic timeout; this follow-up does not claim fresh export validation.

Follow-up baselines, diagnostic harnesses, geometry records and screenshots are
in `C:/Users/Oluwatola Ayedun/.codex/visualizations/2026/09/17/01a0b0fd-68b6-7062-8a6f-5209bc02bed7/forge-fit/`.

## Main consolidation validation

The subsequent consolidation retains the other pending editor, media, saved-letter
and theme-artwork work alongside this repair. Named-style previews now keep their
font properties when a parent stylesheet supplies typography. The earlier
order-dependent font assertion also used unavailable system fonts; its fixture
now uses the bundled families. Curtain construction guards early event-filter
callbacks, shutdown fixtures cover the geometry timers, and sizing assertions
wait for queued layout work without relaxing the zero-overflow requirement.

All 40 changed Python files compile and all 26 added/changed PNGs validate.
The final combined run completed 645 tests without an assertion failure, then
stalled during the audio-import interface-thread check; an earlier combined run
hit a native access violation there. The 14 window-sizing tests subsequently
passed in a fresh process (97.886 seconds), including that audio check. A focused
57-test theme/font/audio run also passed. This covers all 659 tests across runs,
but does not establish a successful single-process full-suite run. The native Qt
failure's underlying cause remains unconfirmed; audio-test cleanup and isolation
of live account checks did not eliminate the combined-run problem.

Logs and the complete changed-file list are retained in
`C:/Users/Oluwatola Ayedun/.codex/visualizations/2026/09/17/01a0b0fd-68b6-7062-8a6f-5209bc02bed7/finalize-main/`.

## Maximized-launch restore repair (2026-09-18)

This follow-up targets window state and native borders, without changing content
layouts. The normal `Main.main()` startup was run with the current user profile
and passive Qt/Win32 geometry logging. The custom button toggled between the
1920x1032 work area and a nearly identical 1918x999 normal rectangle at (1921,32).
The profile contained legacy `ui_window_geometry` and `ui_window_maximized=true`,
without `ui_window_normal_geometry`.

Two separate causes were confirmed before their respective repairs:

* **Windows stays maximized:** a native Nexus launched maximized with a valid
  1200x750 saved normal rectangle could not restore it through the existing button.
  `IsZoomed(HWND)` remained true and `WS_MAXIMIZE` remained present, even when Qt
  reported normal state. Windows rejected the requested 1200x750 rectangle and
  kept 1920x1032. Qt 6.11.1's
  [frameless state implementation](https://github.com/qt/qtbase/blob/v6.11.1/src/plugins/platforms/windows/qwindowswindow.cpp#L2657-L2679)
  uses `MoveWindow` instead of `ShowWindow` for this transition. The controller
  now uses native `SW_RESTORE`/`SW_MAXIMIZE` for the visible Windows main window;
  the existing Qt path remains the fallback on other platforms.
* **The saved normal rectangle is effectively maximized:** the restore path
  previously accepted the screen-sized legacy rectangle unchanged. A bounded
  normal rectangle within one title-control height of both work-area dimensions
  now receives a centered, smaller default. Ordinary smaller rectangles retain
  their exact bounds. The handler uses Qt's latest normal rectangle before its
  button cache, so a later native maximize after manual resizing cannot restore
  stale bounds. Saving while maximized also preserves the usable normal bounds.

The minimum-size rule was measured separately: QWidget, QWindow and Windows'
`WM_GETMINMAXINFO` all reported 960x600. Border/corner hit tests returned their
correct native resize codes and `WS_THICKFRAME` was present. Neither the minimum
nor the resize margin was changed. The deferred restore no longer forces normal
state if a newer maximize/minimize/fullscreen transition has occurred.

The toolbar-height gap was also measured separately. The maximized client and
outer rectangles matched both Qt `availableGeometry()` and native monitor
`rcWork` exactly: (1920,0,1920,1032). There was no extra application-reserved strip.
The 32-pixel top gap belonged to the legacy restored rectangle; the 48 pixels
below the work area were excluded by Windows itself. The custom title bar is
inside the client area and remains 48 pixels high.

Evidence is retained in
`C:/Users/Oluwatola Ayedun/.codex/visualizations/2026/09/18/01a0b2c1-1e7a-7611-baab-7f92467b68f1/`.
`native-state-baseline.json` and `native-state-fixed.json` compare the native and
Qt state without a window-activation helper masking the failure. The native
regression test explicitly requires `QT_QPA_PLATFORM=windows`; an offscreen run
cannot validate Windows' maximized flag.

Validation: ten focused geometry/frame checks passed offscreen, the existing
button-click regression passed, and the new native Windows regression passed
three consecutive cycles including changed normal bounds. Native placements on
all three attached monitors maximized to their exact 1920x1032 work areas and
restored to their preceding 1200x750 rectangles. Python compilation and
`git diff --check` passed. The full suite was not run. Live mouse border/corner
checks after restarting the user's main application remain pending while that
application is in use; automated geometry changes do not substitute for them.

## Launcher-based secondary-window placement (2026-09-18)

Native Windows reproduction used the actual Nexus, Prompt Writer, Editor and
theme selector with temporary project/settings storage. With Nexus on the left
monitor, Editor restored at (120,80,1100,720) and the theme selector at
(100,100,1125,630), both on the primary monitor. Editor's screen-fitting helper
ranked the saved rectangle rather than its launcher; the selector explicitly
selected the screen at the saved coordinates. The ordinary shared dialog and
hidden Prompt Writer reopen stayed on the launcher screen in that baseline.
Prompt Writer nevertheless chose its own screen, and its opening/closing slide
ran outside the work area. Stock-image popups followed the cursor, while the
list manager cached its initial screen geometry.

`window_chrome.py` now provides one launcher-screen resolver and placement
utility, installed through an application Show-event guard in `Main.py`.
The guard captures the initiating control during input dispatch, handles parented
dialogs/popups/custom tools, and checks final layout and outer-frame bounds after
Show. It does not constrain later user moves or resizes. Actual QWidget frame
extents are used; QWindow can report obsolete decorative margins for a frameless
popup. Main windows retain their existing state/geometry controller.

Valid positions on the launcher's screen and existing sizes are preserved.
Other-screen/disconnected coordinates relocate and clamp to the launcher's
current available geometry, including negative origins and native decorations.
Only dimensions/minima exceeding that work area are reduced. Prompt Writer uses
its visible launcher button and keeps its short slide/fade on the same screen.
Editor, theme selector, stock-image tray, list manager and Command bar now use
the shared rule instead of competing saved-screen/cursor/cached-screen rules.

Validation:

* Native Windows checks passed on all three attached 1920x1080 displays, each
  with a 1920x1032 work area: Prompt Writer, Editor, shared dialogs, stock images,
  stock music, Archive, list manager, theme selector, and compact/expanded Command
  bar. Editor's cached Find/Replace dialog also followed its owner when reopened.
  Prompt Writer's intermediate animation frames stayed within the work area.
  Editor and selector were tested with saved coordinates on a different, still
  connected monitor. A separate native decorated-dialog check passed.
* Seven offscreen placement/editor/dropdown checks passed. They cover valid
  saved bounds, removed-monitor coordinates, changed desktop arrangements,
  negative/vertical origins, oversize minimums, a clicked control on a different
  screen from its owner's center, custom Show handlers and frame bounds.
  Monitor removal/rearrangement was simulated; Windows display settings were
  not changed. Physically black monitors cannot be detected from Qt's connected
  screen list; choosing the launcher's screen avoids using an unrelated one.
* Existing theme-selector drag/dismiss/persistence (3), Prompt Writer open/close
  proofreading (1), and Command bar compact/restore persistence (1) checks passed.
  Eight existing main-window geometry/restore checks and the native maximized-
  launch button regression also passed. Compilation and `git diff --check` passed.
* In the user's normal `Main.main()` session the actual restore button changed
  the main window from (1920,0,1920,1032) maximized to
  (2001,17,1552,923) normal. Passive logging also observed Prompt Writer wholly
  inside that launch monitor. The computer-use input guard interrupted the
  subsequent manual border drag; final live border/corner checks remain pending.

Evidence: `placement-baseline.json`, `placement-fixed.json`,
`placement-native.log`, `placement-unit.log`, and the passive
`geometry-history.jsonl` in the evidence directory above. The native regression
is `test_native_tool_placement_across_connected_screens` in
`test/test_window_sizing.py`; run it with `QT_QPA_PLATFORM=windows`. The full
repository suite was not run.

## Preview stays above controls (2026-09-18 correction)

The compact side column was removed after user feedback. The former breakpoint
changed the root layout to horizontal and stacked the format selectors, so making
the window smaller moved the preview beside the controls. Compact mode now changes
only dimensions, margins and spacing; the root and format-row directions remain
unchanged. Long-action artwork keeps its aspect ratio and text remains readable.

Validation: the sizing suite's 25 applicable tests pass after rerunning the
corrected Forge matrix (three native-only tests skipped offscreen). This includes
216 Sound/Message/Forge cases across six themes, long labels, playlist mode,
three Forge preview shapes and sizes down to 960x516. Five additional native
Windows checks passed for live browser scaling/input/fullscreen, tab placement,
Sound Help alignment, Command immersion and the Sound preview height cap.
The real Main.py application was reopened and manually corner-shrunk to 1012x600;
Forge, Sound and Message kept their previews above reachable controls, with no
outer panel scrollbars after tab transitions settled. Compilation and diff checks
passed. The full repository suite was not run.

## 900x480 compact desktop support (2026-09-28)

The current minimum usable window is 900x480 logical pixels. Images now uses
the compact layout and reserves its measured heading, status, and card height
before the shared preview is sized. Sound, Message, and Forge also reserve their
control heights; the preview stays above the controls and can shrink to a
thumbnail. Help shrinks on short windows. Saved-letter and playlist scrolling
remains inside those lists, while the outer tab panels keep their actions visible.

Validation: the sizing suite passed 28 tests (three native-only skips), and all
14 support-information tests passed. Native Windows checks covered 100%, 125%,
150%, and 200% Qt scaling on the available display; final 125% resize repeats
and 200% Images checks showed separate, clickable controls with no outer scroll.
Screen-change geometry passed the mocked tests. A physical move between monitors
could not be checked because only one display was connected.
