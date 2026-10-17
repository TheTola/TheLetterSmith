# Sound interface and project-wide playback handoff

Updated: 2026-10-16. Branch: `codex/sound-project-playback`.

The Sound and authorized shared foundation/Forge integration are complete on
this branch. Combined tests and native Windows checks passed. This is not merged
or packaged release acceptance.

## Provenance and isolation

The primary checkout began at `6d015d2e1d93d4773dc99df6ac2a2eca31bf52c1`
with uncommitted foundation and unrelated work. The initial isolated foundation
snapshot was `5daa5e3b29bf1b6ca3019f56f1bb9c0d2aca6954`. The feature was rebased
onto the completed foundation `1612235`, including its resource-alias fix.
The superseded feature commit `708855a` remains on
`codex/sound-before-foundation-update`; the current feature history includes
`b6d7ced` and `684f2fe`. The subsequent shared integration is authorized and was
completed in
`C:\Users\Oluwatola Ayedun\.codex\worktrees\sound-project-playback\SmithLetter`.
Do not apply the superseded foundation snapshot alongside this branch.

The primary checkout and its unrelated dirty work were not modified. No sibling
feature branch was merged. Tests use the primary checkout's installed `.venv`
with a fresh absolute `LETTER_SMITH_PROFILE_ROOT` per process, temporary
synthetic projects, and bundled stock media. No private projects, providers,
publishing, remote pushes, downloads or new dependencies were used. A legacy
test fixture added only an empty `prompt_writer_provider` to this worktree's
`settings.json`; its exact contents were verified and restored to HEAD before
committing. The live checkout's settings were not changed.

## Changed file groups

| Files | Scope |
| --- | --- |
| `sound_tab.py`, `sound_model.py` | Sound workspace, existing library/player lifecycle, canonical assignment and saved audio controls, guarded imports/popups, missing-resource retention and portable aliases. |
| `project_model.py`, `project_session.py` | Independent project-field mutation counters and atomic project-target guards, including empty drafts. |
| `project_consumers.py`, `project_repository.py`, `saved_letters.py` | Incremental Sound projection, portable assets, canonical/legacy restore, settings mirrors and rollback. |
| `Template.py`, `generate.py`, `Nexus.py` | Generated audio defaults, preview fingerprinting, owned viewer playback cancellation and shell visibility/shutdown hooks. |
| `test/test_sound_project_playback.py`, `test/test_sound_playback_lifecycle.py`, `test/test_sound_archive_preview.py`, `test/test_sound_restore_lock.py`, `test/test_window_sizing.py` | Sound, native-media lifecycle, fixture and layout regressions. |
| `test/test_project_model.py`, `test/test_project_session.py`, `test/test_foundation_consumers.py`, `test/test_saved_letter_restore_sound.py` | Project guards, projection, portable resource isolation and rollback. |
| `test/test_page_viewer.py`, `test/test_viewer_start.py`, `test/test_curtain_cache.py` | Viewer ownership, generated defaults and cache invalidation. |
| `docs/foundation-contract.md`, `docs/foundation-handoff.md`, this document | Shared API and integration handoff updates. |

The existing library, two-slot audio engine, project session and schema remain
in use. There is no per-page soundtrack system or sibling Messages/Forge page
rendering implementation in this work.

## Sound behavior and canonical contract

The workspace groups Available Music, Current Playlist, and Playback and
Settings. Selection highlights a row; Preview Track, double-click or Return
auditions it. Ctrl+Up/Down reorders, Delete removes the assignment, and Space
controls transport while the playlist has focus. Focused buttons retain their
normal Space activation. Advanced Audio retains conversion-to-single and archive
repair. Empty playlists support silent projects. Removing assignments preserves
source bytes and archive records; missing files/records retain their authored IDs
and remain visible as unavailable. Duplicate track IDs remain prohibited.

Canonical project `sound` owns the existing assignment/order fields plus
`volume` (integer 0–100) and `muted` (boolean). `project_sound_snapshot(session)`
normalizes the effective state. Older projects without `sound.volume` use their
shared `music_volume`, then `starting_volume`, then `STARTING_VOLUME`; absent
canonical mute defaults to false. Sound's mute button now edits the saved project
setting. Reload uses blocked signals, restores both fields, and does not autoplay.

`update_project_sound(session, patch)` commits a narrow authored edit with its
playback views. `project_sound_views(session)` refreshes those disposable views
without replacing or saving the project. They mirror `music_volume`,
`starting_volume`, and `music_muted` in `SettingsStore`, plus the existing
`current_project_sound`/current audio compatibility files. `prepare_consumer`
projects supported canonical Sound before checking output compatibility.
Unprepared or unsupported unrelated content remains protected by existing guards.

Projection preserves pages, messages, prompts, shared settings and session
identity. Sound/provenance edits advance project revision without invalidating
page work or marking prompts stale. Volume/mute-only projection reuses unchanged
audio assets; file-change tokens detect resource replacement. Portable resources
are compared by content hash before a cached record is reused, including when
two projects use the same authored track ID. Replaced cached bytes retain an
archive identity. Missing metadata and aliases survive projection/save/reload.
Projection failures restore affected compatibility files/settings/archive state;
canonical restore also rolls audio settings back if its final settings write
fails. Canonical saves collect assigned portable audio even before visiting Sound.

For delayed work, use `ProjectSession.capture_project_target(fields=('sound',))`,
`project_target_is_current(target)`, and `apply_project_result(target, callback)`.
The token covers session/project identity, restoration epoch, field signatures
and mutation counters; the guard and callback share one mutation scope. It needs
no page or selection, so empty drafts are supported. Relevant edits, including
edit-then-revert, replacement, restoration and close invalidate it. Unrelated
page edits and selection/reordering do not. Sound imports and popup assignment
consume this contract alongside existing cancellation/context guards. A completed
import may remain registered in Music Archive while stale assignment is rejected;
restore/shutdown cancellation still discards older callbacks.

`_ImportWorker` stages and validates media before installing library files.
FFprobe validates when present; direct MP3 import can use existing Qt metadata
validation when it is absent. Validation does not play audio. Invalid media,
conversion failure and cancellation do not commit a successful import.

`PlaylistPlayer.transport_state`/`transportStateChanged(str)` report actual
backend `loading`, `playing`, `paused`, `stopped`, `error`, or `unavailable` state.
Deactivation/restore/shutdown invalidate playback callbacks and cancel scheduled
restart/crossfade work. Ordinary tab deactivation leaves imports running. Popup
audition releases its source before eligible Sound playback resumes. Existing
`volume_changed(int)` and `sound_state_changed(str)` shell signals remain in use.

## Forge lifecycle

`generate.py` reads the effective settings mirrors into `INITIAL_VOLUME` and
`INITIAL_MUTED`; mute participates in the preview fingerprint. Viewer mute is
local to the loaded presentation and survives next/back navigation. Full Restart
reloads the authored defaults; old global session-storage mute is not applied.
Page navigation does not restart the soundtrack.

`window.letterSmithPlayback.setVisible(false)` stops all owned audio, including
transient gliss/flip sounds, cancels playlist delays and crossfade/intro animation
frames, and rejects stale callbacks. `shutdown()` additionally disposes owned
sources. Showing a stopped viewer does not automatically resume playback.
Document hiding/page exit use these hooks. Nexus invokes them on pending/hidden
previews, late WebEngine completion and shutdown, and restores visibility for a
reused preview. Existing unrelated rendering/output restrictions remain in force.

## Verification

Run from the feature worktree with a fresh profile. The repository uses unittest;
put `test` first on `sys.path` and run Qt/WebEngine suites in separate processes.

```powershell
$env:LETTER_SMITH_PROFILE_ROOT = Join-Path $env:TEMP ('lss-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
$env:QT_QPA_PLATFORM = 'offscreen'
$env:QTWEBENGINE_CHROMIUM_FLAGS = '--disable-gpu'
& 'C:\Users\Oluwatola Ayedun\Desktop\SmithLetter\.venv\Scripts\python.exe' -c "import sys,unittest; sys.path.insert(0,'test'); result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromName('test_sound_project_playback')); sys.exit(not result.wasSuccessful())"
```

Final checks for the shared integration:

| Selection | Result |
| --- | --- |
| `python -m unittest discover -s test -p 'test_sound*.py' -q` | 84 passed in 32.658 seconds |
| `test_project_model`, `test_project_session`, `test_project_repository`, `test_foundation_consumers`, `test_saved_letter_restore_sound` | 118 passed in 20.454 seconds (56 model/session, 13 repository, 17 consumer, 32 restore) |
| `test_page_viewer` plus `test_curtain_cache.CurtainCacheTests.test_project_audio_settings_reach_generated_viewer_and_invalidate_cache` | 7 passed in 12.331 seconds |
| `test_viewer_start` | 1 passed in 64.633 seconds, covering 7 mouse/touch/keyboard opening scenarios and hidden native-media cancellation |
| `test_window_sizing.WindowSizingTests.test_sound_and_message_fit_without_panel_scrolling` | 1 passed in 29.074 seconds, across the existing theme/mode/size matrix |
| Native Windows Nexus smoke, fresh profile | Passed keyboard preview/reordering, actual muted playback, canonical volume/mute, New Project, canonical restore without autoplay, leaving Sound, source preservation and shutdown |
| `git diff --check` | Passed |

The six page-viewer tests use real QWebEngine/production viewer JavaScript with
instrumented media calls to verify timers, mute, navigation and stale callbacks.
The existing opening test separately uses actual bundled media and native
`playing` events. Audio output was muted; neither establishes audible quality.
The native shell check uses synthetic 44.1 kHz stereo WAV files, the real
`start_new_project` and `SavedLetterRestorer.restore_canonical` paths, and the
same ready-state transition and shell refresh used by restore completion. It
does not exercise catalog selection through the mouse. Both 900×480 and
1400×900 renders had zero panel overflow and were visually inspected. The first
harness omitted the ready-state transition after New Project, so tab changes
were ignored; the corrected harness passed without a production workaround.

Final logs, smoke script and screenshots are retained in
`C:\Users\Oluwatola Ayedun\.codex\visualizations\2026\10\16\sound-project-playback`.
Reproduce the native check from this worktree after setting the fresh profile
above (the script selects the Windows Qt platform):

```powershell
$evidence = 'C:\Users\Oluwatola Ayedun\.codex\visualizations\2026\10\16\sound-project-playback'
& 'C:\Users\Oluwatola Ayedun\Desktop\SmithLetter\.venv\Scripts\python.exe' (Join-Path $evidence 'lettersmith-canonical-smoke.py') (Get-Location).Path $evidence
```

Final regressions also cover a queued external volume edit followed by mute,
edit-then-revert during import, resource replacement behind a stale hash alias,
different portable resources with the same authored ID, and rollback after
projection failure. Original/replaced archive bytes remain preserved.

The following earlier results establish baseline and media-conversion history.

Before these shared changes, Sound discovery passed 79 tests, saved-sound restore
passed 28, and the window-sizing check passed its existing theme/mode/size matrix.
The foundation selection passed 63 after rebasing onto `1612235`. Earlier native
Windows Nexus checks exercised Return preview, real muted Playing/Paused/Stopped
states, Ctrl+Down ordering, leaving Sound, shutdown, and 900×480/1400×900 sizing
with zero panel overflow. Visually inspected screenshots remain under:
`C:\Users\Oluwatola Ayedun\.codex\visualizations\2026\10\13\01a13763-c742-7670-891a-2d6ebcf71ae0\sound-smoke`.

Two bounded native-media regressions use `QApplication.exec()` to verify repeated
two-slot crossfades, single-track natural-end repeat and quiet shutdown with
muted 2-second WAV fixtures. They catch the unchanged-source duration-cache
regression fixed in `684f2fe`. An earlier `QTest.qWait` natural-end harness hung
on both baseline and feature; production-event-loop checks passed instead.

Real WAV-to-MP3 conversion passed through `_ImportWorker.run()` without converter
mocks. FFprobe measured the synthetic 44.1 kHz stereo fixture at 2.0 seconds.
Registration, assignment/save/reload and removal preserved input bytes, archived
original, processed MP3 and catalog record. Only the primary checkout's existing
ignored FFmpeg/FFprobe executables were copied to the worktree; their SHA-256
hashes matched. Reproduction script and logs:

```powershell
& 'C:\Users\Oluwatola Ayedun\Desktop\SmithLetter\.venv\Scripts\python.exe' "$env:TEMP\lss-accept-3a0ab45b\real_wav_import.py" 'C:\Users\Oluwatola Ayedun\.codex\worktrees\sound-project-playback\SmithLetter'
```

`%TEMP%\lss-accept-3a0ab45b` also contains `conversion.log` and `foundation.log`.
The script creates/removes isolated synthetic data. Failure/cancellation checks
separately exercise the production worker with injected conversion failure.

## Integration handoff and limits

The former four Sound/Forge blockers are resolved. Integrate this branch after
foundation `1612235`; do not apply the superseded snapshot. Keep the shared
project-target, projection and viewer changes together with their Sound consumers.
Integration into the primary checkout and sibling branches has not occurred. Audible
listening, macOS, packaged application, complete release and publishing acceptance
remain unverified. This branch does not establish independent multi-message
rendering or other sibling feature completion.

- **Feature implementation: Complete.** Canonical Sound and Forge integration,
  regression checks and native Windows acceptance passed on this feature branch.
- **Ready for sequential integration: Yes.** Apply the branch after `1612235`
  and resolve any later shared-file changes using the documented contracts.
- **Cross-feature verification:** foundation persistence and guards, Sound UI and
  native media, generated Forge defaults and real viewer lifecycle were verified.
  Merged sibling features, audible listening, packaged releases and publishing
  remain outside this acceptance.
