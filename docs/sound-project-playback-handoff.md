# Sound interface and project-wide playback handoff

Date: 2026-10-13. Branch: `codex/sound-project-playback`.

This is a partial feature implementation, not merged release acceptance.

## Provenance and isolation

The primary checkout started at
`6d015d2e1d93d4773dc99df6ac2a2eca31bf52c1` with uncommitted foundation and
unrelated work. That revision alone did not contain the completed foundation.
The isolated foundation-only snapshot used here is
`5daa5e3b29bf1b6ca3019f56f1bb9c0d2aca6954`. The feature worktree is
`C:\Users\Oluwatola Ayedun\.codex\worktrees\sound-project-playback\SmithLetter`.
This task did not modify the primary checkout or merge sibling feature work.

The pre-existing dirty work recorded in [foundation-verification.md](foundation-verification.md)
includes publishing authentication/Pages files, release scripts and metadata,
`requirements.txt`, `settings.json`, application-path and publishing tests,
`fault_diagnostics.py`, `https_transport.py`, their tests, `Prompt Writer/`, and
`.test-output/`. Those changes were not part of this feature snapshot.

Tests used the original checkout's installed `.venv` interpreter with a new short
absolute `LETTER_SMITH_PROFILE_ROOT` for each process. Fixture workspaces were
temporary directories. Inputs were synthetic WAV files, the existing
`five_page_project` builder, and copies of bundled stock audio/manifest entries.
No private projects, external providers, publishing, remote pushes, or downloads
were used. Stock resources were read-only inputs.

A legacy foundation fixture changed this worktree's `settings.json` by adding
an empty `prompt_writer_provider`. Its exact diff was verified and restored to
the baseline bytes after tests. It is not included in the feature commit.
The task changes are isolated in the commit containing this handoff, directly
after the foundation snapshot above; no remote operation was performed.

## Modified paths and scope

| Path | Change |
| --- | --- |
| `sound_tab.py` | Three workspace areas, explicit assignment/preview actions, ordered playlist and keyboard controls, advanced audio section, missing-track presentation, volume restoration, validated import staging, context guards, and backend-driven transport lifecycle. |
| `sound_model.py` | Compatibility synchronization retains authored IDs when a library record is missing. |
| `test/test_sound_project_playback.py` | Feature/model/UI/import tests using production Sound and foundation APIs. |
| `test/test_sound_playback_lifecycle.py` | Transport state, stale callbacks, repeat/crossfade, shutdown and missing-resource tests. |
| `test/test_sound_archive_preview.py` | Layout assertion updated for intentional vertical playlist rows; retains scrolling and adds action visibility, focus and bounds checks. |
| `test/test_window_sizing.py` | Exercise playlist modes through production model operations; retain long-title, all-theme, control-bounds, no-scroll and minimum-visualizer assertions. |
| `docs/sound-project-playback-handoff.md` | This handoff. |

No foundation schema, migration, persistence, page registry, shared shell, Forge,
Template, or rendering files were changed. The existing audio engine and library
remain in use. There is no page-by-page soundtrack system.

## Implemented behavior and ownership

The Sound workspace groups Available Music, Current Playlist, and Playback and
Settings. Import names Music Archive and the current project as its destinations.
Playlist selection highlights a row without changing the selected playback
track. Preview Track, double-click, or Return explicitly auditions that assigned
track. Ctrl+Up/Down reorders; Delete removes the assignment. Space controls the
Sound transport while the playlist has focus; focused buttons retain their normal
Space activation. Advanced Audio exposes conversion-to-single and archive repair
without removing their existing functionality. Empty states explicitly permit a
silent project. Playlist rows show source/duration and distinguish selection from
the actually playing track.

`ProjectSoundState` retains its existing fields: `version`, `mode`,
`single_track_id`, `playlist`, `playlist_expanded`, and `selected_track_id`.
`ProjectSound` owns assignment, selection, ordering and compatibility persistence.
The existing application prohibits duplicate track IDs; no occurrence schema was
introduced. Reordering requires the existing membership, including missing IDs.
Removing an assignment does not delete source bytes or the archive entry. Stock
records remain immutable through existing library mutation rules.

Missing files/records remain assigned and visible as unavailable. The transport
does not substitute a different track for an unavailable selected track. The
library availability cache is retained, with explicit refresh of referenced IDs
for external removal/repair; this avoids a full scan on every transport update.

`_ImportWorker` stages and validates media before installing new library files.
Bundled FFprobe validates when present; direct MP3 import can use the existing Qt
media backend for metadata validation when FFprobe is absent. Metadata validation
does not play audio. Invalid media, conversion failure and cancellation do not
commit a successful import. Normal errors are readable and omit raw private paths;
detailed diagnostics use the established logger.

The Sound `PlaylistPlayer` owns its two existing media slots for transitions.
Archive/stock dialogs own temporary audition players. `_exec_music_popup` pauses
the Sound owner when audition starts, releases the popup source before resuming,
and resumes only when the tab/session remains eligible. The existing repeat,
playlist-end and crossfade behavior is retained.

`activate_for_tab_change`, `deactivate_for_tab_change`,
`prepare_for_project_restore`, `release_current_file_handle`,
`reload_project_from_disk`, `reset_project_sound`, and `shutdown` remain the
integration lifecycle hooks. Deactivation/restore/shutdown stop playback and
invalidate old playback callbacks. Ordinary tab deactivation leaves imports
running. Per-source generation guards reject old media
callbacks; scheduled restart/crossfade work is canceled on stop.

Import completion accepted by the background-generation guard retains intentional
library registration but skips assignment when the initiating project context
differs. Project restore increments that generation and discards older completion
callbacks entirely. Popup completion similarly skips
assignment/resume after context replacement. Context uses
`peek_project_session(root).session_id` when available and the existing background
generation. This is defensive protection; it does not replace the missing
foundation-level atomic project-target contract described below.

## Settings and consumer hooks

The existing saved volume scale remains integer 0–100, persisted as
`music_volume` and `starting_volume` in the compatibility settings. Keyboard and
wheel edits now persist as well as slider release. Reload restores volume under
`QSignalBlocker`, resets temporary preview mute, does not emit volume editing
callbacks, and does not autoplay. Mute is labeled as temporary preview mute;
canonical project mute persistence is not implemented.

New `PlaylistPlayer.transport_state` and `transportStateChanged(str)` expose
`loading`, `playing`, `paused`, `stopped`, `error`, and `unavailable` from actual
backend state. `playbackChanged(bool)` also follows backend state, not an assumed
successful play request. Sound uses these signals for its transport presentation.

Existing `SoundTab.volume_changed(int)` and `sound_state_changed(str)` remain
connected by `Nexus.py`. Existing state strings include `muted`, `unmuted`,
`project-sound-changed`, `project-sound-cleared`, and `sound-state-reloaded`.
Forge currently consumes the
existing compatibility settings/audio and shell connections; it does not yet
consume a newly implemented canonical sound snapshot. No new canonical snapshot
format is claimed by this branch.

## Foundation and Forge integration blockers

1. **Project-scoped delayed-work guard:** `ProjectSession.capture_target` and
   `apply_result` target a page. Valid empty drafts have no page to capture. The
   foundation owner must add a project-target capture/current/apply contract
   covering session identity and relevant sound revisions, with an atomic guard
   and mutation. Sound imports/assignments must then consume it instead of a
   feature-local context comparison. Do not fabricate a page or duplicate the
   session system.
2. **Canonical Sound projection:** authoritative model sound changes have no
   supported incremental Sound/Forge compatibility projection. The existing
   `project_consumers.consumer_issue(root, "output")` blocks canonical sound
   changes until the complete save/load activation path restores supported views.
   The foundation owner must expose sound-only projection/activation that preserves
   unrelated pages, messages and prompts. Sound must then read/write authoritative
   sound fields through that contract and Forge must consume its effective
   settings/events. Current model preservation tests do not establish this missing
   consumer integration.
3. **Mute contract:** foundation and Forge owners must define whether and where
   project soundtrack mute is canonical, persist it, expose effective volume/mute,
   and distinguish it from temporary audition mute. This branch preserves the
   existing runtime-only behavior instead of inventing an unsupported field.
4. **Forge delayed playback:** the Forge/shared-shell owner must cancel pending
   `Template.py` `advanceMusicSequence` timers when
   `Nexus._set_forge_preview_visible(False)` hides Forge. Otherwise a delayed
   sequence callback can restart hidden viewer audio. Ordinary `Template.go`
   page navigation was statically inspected and does not restart the soundtrack;
   full-presentation Restart is separate. No live Forge lifecycle fix or
   cross-owner playback acceptance is claimed here.

## Commands and verification

Runs used PowerShell from the feature worktree and
`C:\Users\Oluwatola Ayedun\Desktop\SmithLetter\.venv\Scripts\python.exe`.
Each Sound suite ran in a fresh process/profile with these environment settings:

```powershell
$env:LETTER_SMITH_PROFILE_ROOT = Join-Path $env:TEMP ('lss-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
$env:QT_QPA_PLATFORM = 'offscreen'
$env:QTWEBENGINE_CHROMIUM_FLAGS = '--disable-gpu'
& 'C:\Users\Oluwatola Ayedun\Desktop\SmithLetter\.venv\Scripts\python.exe' -c "import sys,unittest; sys.path.insert(0,'test'); result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromName('test_sound_project_playback')); sys.exit(not result.wasSuccessful())"
```

The same runner was invoked separately with each module in this table:

| Suite | Before feature edits | Final recorded result |
| --- | --- | --- |
| `test_sound_restore_lock` | 10 passed | 10 passed |
| `test_sound_archive_preview` | 24 passed | 24 passed |
| `test_sound_visualizer_fallback` | 12 passed | 12 passed |
| `test_saved_letter_restore_sound` | 28 passed | 28 passed |
| `test_sound_project_playback` | New | 19 passed |
| `test_sound_playback_lifecycle` | New | 12 passed |
| `test_window_sizing.WindowSizingTests.test_sound_and_message_fit_without_panel_scrolling` | Not separately run before edits | 1 passed, across all existing themes/modes/sizes |

Baseline Sound selection: **74 passed**, no pre-existing failures in these four
suites. Final Sound discovery (`-m unittest discover -s test -p 'test_sound*.py' -q`):
**77 passed**; saved-sound restore separately: **28 passed**. The sizing check
separately passed its existing theme/mode/size matrix. The vertical-list assertion initially failed against the old
wrapping expectation; it was intentionally revised for the chosen ordered layout,
not classified as a pre-existing failure. `git diff --check` passed for the complete task diff.
The sizing check initially exposed lost visualizer height and hidden-mode height
reservation. The compact controls and active-card layout were corrected; its
original no-scroll, control-bounds and minimum-preview assertions now pass.

The root agent also reported **62 passing foundation tests** across
`test_project_model`, `test_project_repository`, `test_project_session`, and
`test_foundation_consumers`, using the repository unittest loader with `test`
first on `sys.path`. These counts are separate from the Sound selection.

Coverage includes minimum/five/maximum supported page counts (2/5/10), repeated
Letter pages, add/reorder/retype/delete/restore preserving project-wide sound,
canonical repository round-trip, legacy Sound state round-trip, duplicate
prohibition, stock/imported mixed IDs, missing/relinked resources, import failure
and cancellation, real malformed/valid MP3 validation, keyboard list/volume
actions, preview-versus-selection, project replacement guards, silent state, and
volume restoration without edit callbacks/autoplay.

Important evidence limits: canonical page tests seed the current Sound state into
the model and verify the model/repository and production Sound assignment remain
unchanged independently. Project-switch tests explicitly clear the legacy Sound
assignment after session replacement to simulate the existing shell restore.
Neither test bypass is evidence of completed canonical Sound activation.
Conversion failure is injected into the real worker cleanup path. QMediaPlayer
tests use real local media/backend with muted output; no audible listening is
claimed.

The real Nexus shell was rendered offscreen and then exercised with the native
Windows Qt platform (`QT_QPA_PLATFORM=windows`) in a temporary synthetic workspace.
The native check used two generated 44.1 kHz stereo WAV tracks: Return preview,
actual muted Playing/Paused/Stopped states, Ctrl+Down reordering, leaving Sound,
900x480 and 1400x900 resizing, and normal shutdown passed. Both sizes had zero
horizontal/vertical panel overflow. No authenticated account or private media
was used. GPU fallback diagnostics occurred without preventing these checks.

Native screenshots, visually inspected:

- `C:\Users\Oluwatola Ayedun\.codex\visualizations\2026\10\13\01a13763-c742-7670-891a-2d6ebcf71ae0\sound-smoke\sound-native-900.png`
- `C:\Users\Oluwatola Ayedun\.codex\visualizations\2026\10\13\01a13763-c742-7670-891a-2d6ebcf71ae0\sound-smoke\sound-native-1400.png`

An additional natural automatic-crossfade experiment hung inside native Qt's
event loop after loading the second short WAV. The same hang was reproduced
with the untouched baseline PlaylistPlayer implementation; 8 kHz mono and
44.1 kHz stereo fixtures were tried. Owned hung processes were terminated and
no hanging test was retained. Existing controlled crossfade/order/handoff tests
pass, but natural-end/crossfade acceptance and audible listening remain open.
Successful real non-MP3 conversion was not exercised; cancellation/failure used
the production worker with injected conversion failure. No macOS, packaged
application, full release, external generation or publishing acceptance ran.
The broader pre-existing failures in the foundation report remain outside this
branch's claims.

## Integration handoff

Integrate the feature commit only after reviewing the foundation snapshot against
the shared foundation baseline; do not apply its parent over unrelated local work.
Coordinate the four foundation/Forge changes above.
After those contracts land, replace compatibility-only sound ownership with the
canonical contract and verify real New Project, save/reload, A→B→A replacement,
late imports, and Sound↔Forge transitions in the merged shell. Repeat graphical
keyboard/resizing checks and listen to local audio on supported native platforms.
Do not merge this report as a claim that sibling features or the release passed.

- **Feature implementation: Partial.** Sound UI, local playback/import lifecycle,
  missing-resource retention and focused tests are implemented; authoritative
  project Sound/Forge integration and the media acceptance limits above remain.
- **Ready for sequential integration: No.** Foundation sound projection,
  project-scoped delayed-work guards, canonical mute meaning, and Forge timer
  cancellation require coordinated owner changes.
- **Cross-feature verification:** actual foundation model/repository checks,
  existing saved-sound restore tests, offscreen Sound checks, and the scripted
  native Windows shell/media check ran. Canonical
  Sound/Forge consumption, audible/natural-end media, sibling feature
  integration, packaged artifacts, and publishing remain unverified.
