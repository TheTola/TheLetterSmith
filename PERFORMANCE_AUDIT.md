# Letter Smith Performance Audit

Date: 2026-08-21  
Scope: investigation and measurement only; no production behavior was changed.

## Executive conclusion

Letter Smith's main performance problem is not one slow algorithm. Work that belongs to a specific feature or historical library is executed eagerly, repeatedly, and on the Qt GUI thread.

The highest-impact facts are:

1. A profiled `Nexus` construction took 4.01 s. `SoundTab` used 2.11 s, primarily because two 1024 x 1024 button images are cropped with 2,097,152 Python-level `pixelColor()` calls. `ForgeTab` used 0.94 s, including 0.69 s rebuilding Saved Letter cards.
2. Past letters are coupled to the active-letter path. Resolving one active Play folder recursively scans every Play bundle; Forge also enumerates every saved letter and rebuilds cover cards while the Saved Letters panel is closed.
3. Every eligible tab switch performs a complete synchronous workspace snapshot. It recopies unchanged pages, message assets, sound state, Prompt Writer state, and metadata on the GUI thread.
4. Forge change handling defeats its own debounce. `schedule_refresh()` performs a full source hash immediately; the timer callback hashes it again. One warm source fingerprint was 43.6 ms and one Forge state refresh was 62.1 ms.
5. Several image operations that take roughly 0.6-0.7 s run on the GUI thread: optimized PNG import and full-resolution message PNG encoding.

The Ultra pass should first remove historical-letter discovery from active work, eliminate the 2 s Sound artwork crop, add dirty/incremental tab autosave, and make Forge invalidation cheap. Those are narrow changes with substantially better return than a broad architecture rewrite.

## Measurement context

Measurements were taken on the current Windows workspace with Python 3.13, using `perf_counter`, `cProfile`, Qt's offscreen platform where a UI was required, and a warm filesystem cache.

Current data set:

| Data | Current size |
|---|---:|
| Source/application files inspected | 188 |
| Python files | 78 |
| Saved Play bundles found by canonical resolver | 19 |
| Saved Letter entries accepted by catalog | 18 |
| `output/Play` | 964 files, 1.87 GB |
| Active sound area | 78 files, 235 MB |
| Sound library records | 21 |
| Active page workspace | 1 file, 3.08 MB |
| Active message workspace | 1 file, 1.06 MB |

The active project is incomplete, so full Generate and an eligible production autosave were not executed. Those costs are estimated from their exact I/O paths. Warm-cache timings understate cold startup and slower disks. Offscreen timings do not include all real GPU/compositor costs.

### Representative timings

| Path | Result |
|---|---:|
| `Nexus` constructor | 4,009 ms |
| `SoundTab.__init__` within startup | 2,112 ms |
| two `SoundTab.ArtworkButton` crops | 2,006 ms |
| `ForgeTab.__init__` | 836-942 ms |
| initial `ForgeTab.refresh_saved_letters` | 694 ms profiled |
| repeated `refresh_saved_letters` | 619 ms median |
| `SavedLetterCatalog.list_entries` only | 39.8 ms median |
| `generate.play_bundle_directory` | 18.7 ms median |
| `config._iter_play_bundles` | 15.8 ms median |
| `generate.build_source_fingerprint` | 43.6 ms median |
| `generate.is_play_bundle_current` | 65.9 ms median |
| `ForgeTab.refresh_project_state` | 62.1 ms median |
| `validate_play_bundle` | 4.6 ms median |
| modeled full-resolution message render | 624 ms |
| message PNG encoding portion | 567 ms |
| optimized static PNG import | 718 ms |
| visible Sound entry, autosave disabled | 78 ms; two reloads |
| visible Sound exit, autosave disabled | 59 ms; two deactivations |
| `evaluate_readiness` | 2.6 ms median |
| `evaluate_project_save_eligibility` | 2.4 ms median |
| `SettingsStore.snapshot` | 0.19 ms median |
| `project_sync.image_fingerprint` | 0.58 ms median |

The message render modeled the live 2048 x 3072 `QImage` scale/blur/text/PNG path against a temporary output. Its breakdown was 30 ms decode, 9 ms scale, 12 ms blur, 6 ms composition, and 567 ms PNG encoding.

## Startup sequence

1. `Main.main()` resolves the application root, changes the working directory, and updates `sys.path`.
2. `Main.load_settings()` creates a `SettingsStore` and calls `snapshot()`. The constructor already reloads once, so this reads and normalizes `settings.json` twice.
3. Logging, Qt logging rules, Windows application identity, icon selection, and `QApplication` are initialized.
4. `run_startup_self_check()` validates packaged resources.
5. `Nexus` is imported. Importing `config.py` performs another import-time settings load.
6. `Nexus.__init__()` constructs the system tray, settings/state/path/save services, the full shell, a `QWebEngineView`, two help `QMovie` objects, preview widgets, overlays, timers, and recipient UI.
7. If the project is ready, `_initialize_project_tabs()` immediately imports and constructs `ImageTab`, `SoundTab`, `MessageTab`, `ForgeTab`, and `CommandTab`, connects all cross-tab signals, restores the previous tab, and applies tab state.
8. `window.show()` occurs only after all of the above. Event-loop work then starts both help movies and WebEngine processing.

### Immediate construction and safe lazy-loading boundary

| Component | Constructed now | Safe lazy strategy |
|---|---|---|
| Shell, title, recipient state, tab bar | Yes | Keep eager |
| `ImageTab` | Yes | Construct only if it is the restored/default tab; otherwise on first entry |
| `SoundTab`, players, visualizer, library migration | Yes | Construct on first Sound entry; retain its small persisted state contract |
| `MessageTab` | Yes | Construct on first Message entry; readiness can read canonical files without the widget |
| `ForgeTab`, readiness window, Saved catalog/cards | Yes | Construct on first Forge/Load Letters/Preview request |
| `CommandTab` | Yes | Construct on first Command entry |
| `QWebEngineView` | Yes | Construct on first embedded Forge preview, not at shell startup |
| Help idle and hover GIFs | Yes; both started | Load idle only; create/start hover movie on hover and stop it afterward |
| Prompt Writer | No | Already correctly lazy; retain this design |
| Music Archive dialog | No | Already lazy; retain this design |
| Target Browser | No; separate process | Already lazy; retain this design |

Lazy tabs need a lightweight domain-revision record so changes made before construction are reconciled once on first activation. They do not need proxy widgets or a second state system.

## Critical findings

| Bottleneck | Affected files/functions | Evidence | Estimated severity | Recommended solution | Difficulty | Risk | Expected user-visible benefit |
|---|---|---|---|---|---|---|---|
| Python per-pixel crop blocks startup | `sound_tab.py`: `ArtworkButton.__init__`, `_crop_transparent_edges`; `SoundTab._init_ui` | Two 1024 x 1024 assets caused 2,097,152 `pixelColor()` and alpha calls and consumed 2.01 s of a 4.01 s constructor | Critical; every ready-project startup | Remove runtime cropping by using pre-cropped canonical assets or cache a crop rectangle by path/mtime. If runtime detection remains necessary, scan native image bytes/alpha mask rather than Python `pixelColor()` calls. Reuse the shared artwork-button implementation. | Low | Low | About 2 s faster startup on this machine |
| Historical letters are on the active-letter path | `config.py`: `_iter_play_bundles`, `resolve_play_bundle_directory`; `generate.py`: `play_bundle_directory`; `saved_letters.py`: `SavedLetterCatalog.list_entries`; `Forge_Tab.py`: constructor, `refresh_saved_letters`, `activate_for_tab_change` | Active path resolution scans all 19 bundles in 18.7 ms. Catalog scan is 39.8 ms. Full hidden-panel card rebuild is 619 ms and Forge performs it at startup and activation. Cost grows linearly toward 1,000 letters. | Critical scalability and startup issue | Use the canonical active path/settings as the fast path and validate that one directory. Fall back to an indexed lookup only when the path is missing or identity mismatches. Construct/refresh the Saved catalog only when Load Letters opens or its watcher marks the cache dirty. Keep a catalog snapshot keyed by root directory revisions; update changed entries only. | Medium | Medium | Past-letter count stops affecting startup, editing, Forge entry, and Generate |
| Full synchronous autosave on every tab switch | `Nexus.py`: `_apply_tab_state`, `_autosave_project_on_tab_switch`; `project_save.py`: `save_workspace_snapshot`, `_copy_tree_to_context`; `readiness.py` | Every actual switch evaluates readiness and, when eligible, recursively rereads and atomically rewrites every workspace page/message file plus sound, Prompt Writer state, and metadata. It ignores `ProjectDirtyController` until after copying. All work is on the GUI thread. | Critical responsiveness issue for complete projects, especially GIFs/large images | Skip when the project/domain revisions are unchanged. Maintain per-domain dirty revisions and a saved manifest of size/mtime/content digest. Copy only changed files and remove only known deleted files. Run the file-copy transaction in a worker; apply final status/dirty state on the GUI thread. Keep manual Save as a forced reconciliation. | Medium | Medium | Tab switches become immediate; much less SSD write amplification |
| Forge debounce performs duplicate full hashes | `Forge_Tab.py`: `schedule_refresh`, `_refresh_source_fingerprint`, `refresh_project_state`, `_on_settings_changed`; `generate.py`: `build_source_fingerprint`; `Nexus.py` signal wiring | Warm fingerprint: 43.6 ms. `schedule_refresh()` hashes immediately, then the 120 ms callback hashes again. `refresh_project_state()` is 62.1 ms. Forge startup computed the fingerprint three times. Message slider changes and other signals invoke this on the GUI thread. | Critical interactive hitch/frequency | Make signals mark domain revisions only. Move fingerprint calculation inside the trailing debounce and compute at most once per revision. Filter settings changes to viewer-relevant keys. Cache per-file digests by `(resolved path, size, mtime_ns)` and cache immutable template/app SFX hashes for the process. Pass one computed fingerprint through currency check, build, completion, and metadata paths. | Medium | Low-Medium | Removes 50-100+ ms stalls from common changes and substantially reduces Forge CPU/disk reads |
| Forge preview release blocks the event loop | `Nexus.py`: `_release_forge_preview_files`; `Forge_Tab.py`: `deactivate_for_tab_change` | Leaving Forge emits a release that enters a nested `QEventLoop` and may wait up to 1.5 s for `about:blank`, directly in the tab-switch path. Restore also performs release before its worker starts. | Critical worst-case tab/load freeze | Keep the current page loaded and pause media on ordinary Forge exit. Release only before destructive bundle replacement, restore, or shutdown. At those boundaries use an asynchronous `loadFinished`/timeout state machine; do not run a nested event loop. Start the operation after the release callback. | Medium | Medium; Windows file-handle behavior must remain covered | Immediate Forge exit/return and responsive restore startup |

## High-value findings

| Bottleneck | Affected files/functions | Evidence | Estimated severity | Recommended solution | Difficulty | Risk | Expected user-visible benefit |
|---|---|---|---|---|---|---|---|
| Eager construction of all heavy features | `Nexus.py`: `__init__`, `_initialize_project_tabs`; constructors in all five tabs | `Nexus` took 4.01 s before `show()`. Sound and Forge alone accounted for about 3.05 s. WebEngine is created whether Forge is used or not. | High | Add one canonical `ensure_<tab>()` path per feature and create the restored tab first. Lazy-create WebEngine on first preview. Preserve current signal contracts through Nexus-owned domain revisions, not dummy tab instances. | Medium-High | Medium | Faster first window; unused Sound/Forge/WebEngine impose no startup cost |
| Saved Letter covers are decoded and widgets rebuilt repeatedly | `Forge_Tab.py`: `SavedLetterCard.__init__`, `refresh_saved_letters`, `_refresh_saved_archive` | Fifteen 2.2-8.3 MB cover PNGs are decoded and Smooth-scaled each refresh. Catalog-only cost was 40 ms; widget/cover rebuild raised it to 619 ms. Existing cards are destroyed even when entries are unchanged. | High | Retain cards by stable letter path and update only changed metadata. Generate/store a small cover thumbnail during Generate/save and load it via `QImageReader.setScaledSize`; fall back to source cover once for legacy entries. Populate archive rows/cards on demand and in viewport-sized batches. | Medium | Low | Saved Letters opens and refreshes several times faster with lower memory churn |
| Unchanged Image tab activation recreates every pixmap/movie | `Image_tab.py`: `sync_from_disk`, `sync_to_disk`, `refresh_cards`, `ImageAssetCard.set_asset_path`, `resizeEvent`, `preview_from_gallery`; `image_animation.py` manifest helpers | `sync_from_disk()` always refreshes cards and fingerprints twice. Exit also refreshes cards. Each refresh decodes all PNGs or destroys/recreates/starts `QMovie(CacheAll)`. GIFs continue while the tab is hidden. Hover decodes the PNG again. | High for completed/GIF-heavy projects | Refresh only slots whose manifest/path signature changed. Retain source pixmaps and movies. Stop/detach movies on deactivation and resume current slots on activation. Cache scaled thumbnails by source signature and target size. Reuse the card/source pixmap for hover. Avoid the second fingerprint when the resulting revision is already known. | Medium | Low-Medium | Faster Image entry/exit; lower hidden CPU and GIF memory use |
| Image import/optimization is synchronous | `Image_tab.py`: `set_image_path`; `image_animation.py`: `install_image_asset`, `inspect_gif`, `_write_static_png`, `_write_first_frame_png` | Optimizing the current 3.08 MB PNG took 718 ms. GIF inspection iterates every frame. File reads, Pillow decode/conversion, PNG optimization, GIF preview creation, manifest write, and project copies run on the GUI thread. | High | Use the existing worker pattern for decode/validate/convert/inspect/stage. Commit the prepared files transactionally on completion, then update only the changed card. Disable that card/action while busy and preserve error/status behavior. | Medium | Medium; cancellation and Windows replacement need care | Eliminates 0.7 s+ import freezes and makes large GIF imports responsive |
| Message preview rendering/encoding is synchronous | `Message_tab.py`: `_generate_image`, `_render_overlay_preview`, `sync_from_disk`; overlay controls | Representative 2048 x 3072 render was 624 ms, with 567 ms in PNG encoding. Slider changes write settings and trigger project/Forge signals per tick; only the render itself is debounced 180 ms. | High | Debounce persistence and preview invalidation as one operation. Render a low-resolution in-memory preview during interaction. Move the full-resolution render/PNG encode to a single latest-wins worker, atomically commit the result, and suppress stale completions by revision. Generate the full asset on settle/save/Generate. | Medium-High | Medium; ordering must prevent stale images | Overlay controls remain fluid instead of freezing for about 0.6 s after adjustment |
| Generate repeats unchanged work and validation | `generate.py`: `is_play_bundle_current`, `ensure_play_bundle`, `generate_play_bundle`, `build_play_bundle_to`, `build_source_fingerprint`, `validate_play_bundle`; `image_animation.py`: `build_runtime_image_assets`; `font_export.py`; `curtain_color.py` | Currency check scans Play folders, validates the bundle, and fully hashes sources. A stale build hashes again, copies all assets, re-extracts all GIF frames, retints curtains, rewrites templates/fonts, validates inside the builder, validates in the transaction validator, then validates final output. | High Generate latency and disk amplification | Preserve the transactional staging/final replace. Introduce a build-input manifest with per-domain/file signatures and derived-asset keys. Reuse unchanged GIF frames, tinted curtains, embedded fonts, static app assets, and audio copies in staging. Compute one fingerprint. Use a cheap state/signature current check; reserve full validation for a completed build, explicit repair, or suspicious state. Remove duplicate validation calls after one authoritative staged validation plus a minimal committed-state check. | High | Medium-High | Much faster repeat Generate/Preview, especially with 132 MB GIFs, while retaining rollback safety |
| Saved Letter restore has synchronous preflight and I/O amplification | `Forge_Tab.py`: `load_selected_letter`, `_release_project_files_for_restore`; `Nexus.py` release methods; `saved_letters.py`: `SavedLetterRestorer.restore`; `sound_model.py`: `import_runtime_track` | Restore itself correctly runs in a Forge worker, but GUI preflight may wait 1.5 s for WebEngine and 2-3.5 s for audio threads. The worker copies pages/message into one staging tree and then copies them again into transaction staging. Each new track rehashes, reloads the library, writes it, creates processed/original copies, and selected-current compatibility adds another copy. | High | Make handle release asynchronous and bounded. Copy validated sources directly into each `PathTransaction.prepare()` directory. Batch sound imports: one library load, one hash per source, one library write, and dedupe before copying. Keep rollback snapshots and final verification. | Medium-High | Medium-High; restore integrity is critical | Loading begins promptly and large letters use less time and temporary disk space |
| Sound activation is duplicated and rebuilds state/widgets | `Nexus.py`: `_apply_tab_state`; `sound_tab.py`: `showEvent`, `hideEvent`, `activate_for_tab_change`, `deactivate_for_tab_change`, `reload_project_from_disk`, `_refresh_playlist` | A visible offscreen switch produced two reloads on entry and two deactivations on exit. Entry was 78 ms and exit 59 ms with autosave disabled. Each reload rereads JSON, stats tracks, reconciles, resets players, and refreshes UI. Playlist refresh destroys/recreates all row widgets. | High frequency | Give Sound the same `_tab_active` idempotence guard used by Image/Message/Forge, and choose one lifecycle owner (Nexus or show/hide events). Reload only when sound directory/state revisions changed. Diff playlist rows by track ID and update active styling without reconstructing rows. | Low-Medium | Low | Roughly halves Sound switch work immediately; larger gains for long playlists |
| Decorative GIFs consume hidden work | `Nexus.py` help movies; `PromptWriterPanel.py` visionary timer; `command_bar.py` background/compact/scan movies | Both help movies start at startup although only one is attached. Sequential scaled `QMovie(CacheAll)` frame decoding measured 1.09 s for the 48 MB Help GIF and 136 ms for HHelp. Prompt Writer's 520 ms pulse continues while its window is hidden. The lazy Command Bar's 132 MB, 3000 x 4500, 73-frame `com.gif` took 4.45 s to step through once. | High for startup/Command handoff; moderate otherwise | Start only the visible movie; stop hidden movies and timers. Load hover movie on demand. Stop the Prompt Writer pulse in `popdown()` and restart in `popup()`. Replace oversized decorative source GIFs with display-sized optimized assets during a controlled asset pass, preserving appearance. Keep Command scan timers visible-gated. | Low-Medium | Low-Medium; visual comparison required | Less startup/background CPU and smoother Command Bar appearance |
| Shutdown does not explicitly stop every owned resource | `Nexus.py`: `shutdown`; `sound_tab.py`: `closeEvent`, `_stop_background_threads`; `PromptWriterPanel.py`: `shutdown`; help movies; WebEngine | Nexus releases Sound file handles but does not call Sound's thread/player/preview/analysis shutdown path. It does not shut down a live Prompt Writer or stop help movies. WebEngine is navigated blank but not explicitly disposed. Forge thread shutdown is checked only by `closeEvent`, while `aboutToQuit` can call `shutdown()` independently. | High reliability risk; intermittent performance impact | Add idempotent public `shutdown()` methods and have Nexus call them in ownership order: Forge worker, Prompt Writer, Sound workers/analysis/player/preview, Image movies, help movies/timers, WebEngine page/view, tray. Keep bounded waits only at application exit; log timeout ownership. | Medium | Medium | Clean exits, no lingering decode/work, fewer Windows file locks or `QThread destroyed while running` failures |

## Moderate findings

| Bottleneck | Affected files/functions | Evidence | Estimated severity | Recommended solution | Difficulty | Risk | Expected user-visible benefit |
|---|---|---|---|---|---|---|---|
| Settings JSON is reread by every accessor/instance | `settings_store.py`: constructor, `get`, `snapshot`, `reload`, `update_fields`; all `SettingsStore(...)` call sites | Constructor reload plus `snapshot()` reloads twice. Every `get()` reads/parses/normalizes disk. Many modules create independent stores. One warm snapshot is only 0.19 ms, so the isolated cost is small, but signal storms multiply it. | Moderate aggregate | Share a per-canonical-path snapshot/revision across instances. Check `(mtime_ns, size)` before disk reload to preserve external-edit support. Add batched/no-op updates and pass snapshots through one operation. Do not add a database or dependency. | Medium | Medium | Lower JSON I/O and cleaner signal behavior; modest direct latency win |
| Settings and message signals over-refresh Forge | `Message_tab.py`: `_persist_overlay_settings`, `_persist_settings`; `Forge_Tab.py`: settings and project listeners; `Nexus.py`: tab setting write | Each overlay slider tick atomically writes JSON and emits `project_changed`; the same update also emits `SettingsStore.changed`. `ui_last_tab` writes occur on each switch and Forge listens to all setting keys. | Moderate-High frequency | Emit domain-specific changed keys/revisions. Persist sliders on debounce/release. Ignore `ui_*`, publication-only, and other non-viewer keys in Forge. Do not emit project change for a no-op update. | Low-Medium | Low | Fewer writes and duplicate refreshes during common UI actions |
| Active Play lookup repeats full bundle discovery | `generate.py`: `play_bundle_directory`; `config.py`: `resolve_play_bundle_directory` | `_iter_play_bundles` alone is 15.8 ms for 19 bundles and runs inside a 18.7 ms active lookup. `is_play_bundle_current`, Generate, and Forge call it repeatedly. | Moderate now, critical at large libraries | Persist/validate the exact `active_play_dir` and derive the canonical recipient/title path first. Use project-ID catalog lookup only as recovery. Cache recovery index until Play watcher changes. | Medium | Medium | Removes library-size dependence from current preview/generation |
| Readiness repeats small disk/domain reads | `readiness.py`; `Forge_Tab.py`: `refresh_readiness`, `_required_gate`, `_set_busy`; `project_paths.py` | Readiness is 2.6 ms now, but it rereads settings, sound library/state, message HTML, and scans recipient title metadata. Several Forge paths call it more than once per action. | Moderate aggregate | Cache readiness by image/sound/message/settings/project-path domain revisions. Pass the evaluated result into preview/publish/build completion instead of re-evaluating. | Low-Medium | Low | Removes small repeated stalls and simplifies refresh accounting |
| Sound archive/table and playlist rebuild scale linearly | `sound_tab.py`: `ArchiveDialog.refresh`, `_refresh_playlist`, `SoundLibrary.available_track_ids`, `is_available` | Search refreshes the entire four-column table and calls `resizeRowsToContents()` on every keystroke. `is_available()` recomputes all available IDs. Playlist selection/playback paths rebuild custom row widgets. Current library has only 21 records, so measured impact is limited. | Moderate future scaling | Debounce archive search, use a table model or incremental rows, cache availability for the current library revision, and update playlist selection styling in place. | Medium | Low | Smooth browsing at hundreds/thousands of tracks |
| Cached audio analysis is decoded on the GUI thread | `sound_analyzer.py`: `load_cached`; `sound_preview.py` and `sound_visualizer.py`: `set_analysis_payload` | JSON read plus base64/zlib unpack of level and spectrum arrays occurs when selecting a track. Analysis generation itself correctly runs in a worker. | Moderate for long/high-resolution analyses | Load and unpack cached payload in the existing analysis worker/manager, then emit a decoded immutable payload. Keep only the current track plus a small bounded LRU if repeat switching warrants it. | Medium | Low-Medium | Less track-selection hitch |
| Repeated SmoothTransformation recreates pixmaps | `Nexus.py`: `_show_image`, `resizeEvent`; `image_button.py`: `paintEvent`; `sound_tab.py`: artwork paint; Image cards | Source images are rescaled on repeated resize/paint/hover events. Artwork buttons Smooth-scale on every repaint, including hover and style changes. | Moderate | Cache scaled pixmaps by source cache key/device-pixel-ratio/target size. Invalidate only when source or size changes; debounce resize-driven full-preview scaling. | Low-Medium | Low | Smoother resizing/hovering and lower paint CPU |
| Stale transaction folders inflate scans and disk use | `generate.py`: title-specific `cleanup_abandoned_staging`; `saved_letters.py` catalog scan; `transactional_io.py` cleanup helpers | A 132 MB `.build-staging.*` directory remains under `output/Play`. Saved catalog recursion still traverses staging names even if validation rejects them. Cleanup runs only for the current title when Generate starts. | Moderate | Exclude staging/backup path components from every catalog resolver. At Forge lazy initialization, safely clean only validated stale transaction names using the existing helper and bounded age/ownership rules. | Low-Medium | Low-Medium | Less disk use and catalog noise; prevents degradation after interrupted builds |
| Prompt Writer hidden repaint timer | `PromptWriterPanel.py`: `_start_visionary_pulse`, `popup`, `popdown`, `shutdown` | Prompt Writer is correctly lazy and state writes are debounced 350 ms, but its 520 ms styling timer continues after `popdown()` hides the persistent window. | Moderate-low | Stop pulse on popdown/hide and restart on popup. Keep existing file-signature module-list cache and debounced persistence. | Low | Low | Removes unnecessary hidden style polish/repaints |

## Low value / not worth optimizing yet

| Area | Evidence and decision |
|---|---|
| `project_sync.image_fingerprint` itself | 0.58 ms with the current single active page. It already samples large files. Avoid repeated calls, but do not replace the digest algorithm until a completed/GIF-heavy project is measured. |
| One settings snapshot | 0.19 ms warm. Fix batching/caching for correctness of the wider refresh graph, not as a standalone micro-optimization. |
| One readiness evaluation | 2.6 ms warm. Cache it as part of domain revisions; do not thread it independently. |
| Full bundle validation alone | 4.6 ms. Remove duplicate calls, but retain one authoritative validation; weakening validation is not justified. |
| Status, toast, Forge debounce, metadata, and overlay timers | These are single-shot and stop naturally. Their callbacks may be heavy, but the timers are not persistent repaint sources. |
| Sound visualizer/gate timers | The 16 ms visualizer and 120 ms gate are activated only while Sound owns the shared preview and are stopped on deactivation. Preserve this gating. |
| Particle effects | Timers are event-driven and stop when the effect queue is empty. No evidence makes them a priority. |
| Prompt Writer module lists | `_read_list_file_cached()` already validates cached content by `(mtime_ns, size)`. The list set is small and bounded. |
| `app.py` and `media_widgets.py` | `app.py` is an unreferenced CustomTkinter prototype and imports a missing `images_tab` module. The live entry is `Main.py` -> `Nexus`. Do not optimize the dead path; quarantine/removal is a separate cleanup decision. |
| `Editor.PreviewWidget` | The class has expensive paint behavior, but the current editor assigns `self.preview = self.editor`; the separate preview class is not on the live construction path. Do not optimize dead code during the Ultra pass. |
| Target Browser and publishing process work | Target launches separately/lazily; Forge publishing/build work uses a worker. Optimize only if targeted measurements later identify a user-facing delay outside network/process time. |

## Image, GIF, preview, and WebEngine trace

| Operation | Live path | Thread/lifecycle result |
|---|---|---|
| Image static card load | `ImageAssetCard.set_asset_path` -> `QPixmap(path)` -> Smooth thumbnail | GUI; repeated on every card refresh |
| Image GIF card load | `QMovie(path)`, `CacheAll`, scale, start | GUI; recreated on refresh; continues hidden |
| Image hover preview | `preview_from_gallery` -> new `QPixmap(path)` | GUI; duplicate decode |
| Image resize | card `_rescale` and Nexus `resizeEvent` | GUI; no target-size cache |
| Static image import | Pillow open/exif/convert/optimized PNG + atomic writes/copies | GUI; measured 718 ms |
| GIF image import | Pillow iterates all frames for metadata, copies GIF, encodes first-frame PNG | GUI |
| Generate image assets | copies all PNG/GIF files, reinspects GIFs, deletes/re-encodes every frame as PNG | Forge worker, but always full/nonincremental |
| Curtain color/tint | Pillow downsample/color analysis and full PNG tint | Forge worker; recomputed on every full build |
| Message preview | QImage decode/scale/optional blur, QTextDocument render, PNG encode | GUI; measured 624 ms |
| Message thumbnails | `_emit_best_preview` reloads full PNG/wall and Smooth-scales | GUI; repeated after render/sync |
| Saved Letter covers | up to 15 full `QPixmap` decodes and Smooth scales | GUI; repeated 619 ms refresh |
| Shared image preview | Nexus stores one source pixmap and Smooth-scales on display/resize | GUI; one-item retention, no unbounded cache |
| Sound/other artwork buttons | full pixmap retained; Sound first scans every alpha pixel; paint paths rescale | GUI; startup and repaint cost |
| Help GIFs | two QMovies created, CacheAll, scaled, and started | GUI event loop; hidden hover movie still advances |
| Command Bar GIFs | large background/compact/scan QMovies created when Command Bar launches | GUI; lazy but oversized compact asset is expensive |
| Forge WebEngine | QWebEngineView created at shell startup; local URL reload is guarded by path+mtime token | Eager process/resource cost; normal same-build reload guard is good |
| Forge unload | navigate to blank and nested wait | GUI-blocking; unnecessary on ordinary tab exit |

No unbounded application-level pixmap cache was found. The main memory risks are simultaneous `QMovie(CacheAll)` instances and transient full-resolution images/frames. Saved cards are `deleteLater()`'d, so rapid refreshes can temporarily retain old widget/pixmap graphs until the event loop drains, but this is bounded once refresh duplication is fixed.

## Signal and refresh map

| User/event source | Current refresh chain | Redundancy |
|---|---|---|
| Tab switch | tab deactivate -> eligibility -> full snapshot -> preview clear/layout -> `ui_last_tab` settings write -> tab activate | Full copy on every eligible switch; unrelated setting wakes Forge |
| Enter/leave Sound | Nexus explicit lifecycle plus child show/hide lifecycle | Two reloads and two deactivations in a visible test; Sound lacks guard |
| Image selection | install/copy -> `image_selected` -> Forge `schedule_refresh`; `images_changed` -> dirty | One Forge signal, but its schedule hashes twice across debounce |
| GIF settings | manifest copy -> `images_changed` + `animation_settings_changed` | Dirty is marked twice by related signals; Forge hashes through animation signal |
| Message overlay slider | settings atomic write -> shared settings signal; unconditional `project_changed`; render timer | Multiple timer starts, immediate Forge hash per tick, full PNG later |
| Message sync | settings/file fingerprints -> load/render/preview -> second fingerprint -> `project_changed` if changed | Repeated reads and potentially expensive render on activation |
| Project sound change | state save/reconcile -> queue/UI rebuild -> Forge refresh -> dirty | Appropriate domain change, but UI refresh is broader than needed |
| Forge activation | refresh project/fingerprint/readiness -> refresh Saved Letters -> ensure preview | Historical catalog and active preview work combined |
| Saved directory watcher | 180 ms catalog timer | Good debounce, but explicit operation refreshes can duplicate it |
| Settings change | Forge listens to every key | Must filter to viewer-relevant settings |

## Worker and GUI-thread assessment

Existing worker patterns to preserve:

- Forge `_TaskWorker` correctly runs Generate, restore, and publishing off the GUI thread.
- Sound import and repair use dedicated `QThread` workers.
- Audio analysis uses a one-at-a-time worker queue, preventing concurrent CPU spikes.
- Target Browser slow file inspection uses a background Python thread where appropriate.

Work that should adopt those patterns:

- Pillow image/GIF import and preprocessing.
- Full-resolution message rendering/PNG encoding, with latest-revision wins.
- Saved cover thumbnail generation for legacy entries; card creation itself remains on GUI.
- Autosave file copying and manifest reconciliation.
- Audio cached-payload read/decompression if measurement confirms selection hitch.

Work that should be eliminated/cached rather than merely threaded:

- Historical Play/catalog scans during active-letter work.
- Duplicate Forge fingerprints and validations.
- Unchanged Image card/movie reconstruction.
- Duplicate Sound lifecycle callbacks.

## Prioritized Ultra implementation plan

### Phase U0 - Baseline and guards

1. Add opt-in, dependency-free timing spans controlled by `LETTERSMITH_PERF=1` around `Main` bootstrap, `Nexus` construction, each lazy-tab constructor, tab switch stages, autosave, Forge fingerprint/current/build stages, Saved catalog/card phases, message render, image import, Sound reload, and shutdown waits.
2. Log elapsed milliseconds, item/file count, bytes, domain revision, and GUI-thread identity to the existing rotating log. Do not log user content or full external paths.
3. Add a repeatable local benchmark script using a temporary project and 1/100/1,000 synthetic metadata-only Saved entries plus configurable cover/audio sizes. Keep it outside normal startup.
4. Capture cold and warm baselines on a packaged Windows build before changing behavior.

Exit gate: measurements reproduce the current startup, Saved browsing, tab switch, message render, Forge refresh/current check, and repeat Generate paths.

### Phase U1 - Immediate low-risk wins

1. Remove/cache Sound artwork alpha scanning.
2. Make Sound activation/deactivation idempotent and single-owner.
3. Stop hidden Image/help/Prompt Writer movies and timers; resume only visible work.
4. Filter Forge settings keys and move fingerprint calculation behind the existing debounce.
5. Exclude staging/backup folders from all Saved/Play scans.
6. Cache scaled artwork/preview pixmaps by source and target size.

Expected result: roughly 2 s faster constructor and fewer common-tab/UI hitches with low implementation risk.

### Phase U2 - Separate active work from historical libraries

1. Make validated `active_play_dir`/canonical recipient-title path the active Play fast path.
2. Build a recovery-only project-ID index, invalidated by Play root watcher changes.
3. Lazy-create Forge Saved catalog UI only when Load Letters opens.
4. Cache catalog entries and diff cards by stable path/metadata signature.
5. Write display-sized cover thumbnails for new builds; lazily backfill legacy thumbnails without changing visible results.

Exit gate: startup, tab switching, Forge refresh, preview current check, and Generate perform zero Saved-catalog enumeration. Benchmarks with 1 and 1,000 saved letters are effectively equal outside Load Letters.

### Phase U3 - Incremental, nonblocking project persistence

1. Add per-domain revisions to existing dirty ownership: images, message, sound, Prompt Writer, relevant settings.
2. Store a project snapshot manifest and compare stat/digest signatures.
3. Skip tab autosave when no domain changed; copy only changed/deleted known files otherwise.
4. Run snapshot copying in one worker with coalesced latest-request semantics and transactional metadata completion.
5. Preserve a forced manual Save/reconcile path and Windows atomic-write retry behavior.

Exit gate: unchanged tab switches perform no project file writes; modified projects persist exactly once and restore tests remain green.

### Phase U4 - Image/message responsiveness

1. Move image/GIF import preprocessing to a worker and commit staged output atomically.
2. Make Image card reconciliation slot-diffed and stop hidden QMovies.
3. Split message interaction preview from final 2048 x 3072 output.
4. Use a latest-wins worker for full render; discard stale revision results.
5. Consolidate overlay setting persistence, project change, Forge invalidation, and render scheduling into one settled change.

Exit gate: large image/GIF import and overlay adjustment do not block GUI heartbeats; final assets remain byte-valid and visually equivalent.

### Phase U5 - Incremental Forge/Generate

1. Introduce one source-revision/fingerprint computation per operation, with per-file digest reuse.
2. Add a build-input manifest and reusable derived-asset cache for GIF frames, curtain images, fonts, static app assets, message assets, and audio.
3. Preserve full staging and atomic final-directory replacement; populate staging from reused or rebuilt assets.
4. Reduce validation to one authoritative staged validation and one minimal post-commit verification.
5. Make WebEngine release asynchronous and only destructive-boundary driven.

Exit gate: no-change Preview performs no rebuild; one-domain changes rebuild only that domain; generated output passes existing validation and packaged Windows preview tests.

### Phase U6 - Sound/archive and lifecycle closure

1. Cache Sound library availability by library revision and debounce/model-drive archive search.
2. Diff playlist rows and move cached analysis unpacking off GUI if measured.
3. Batch Saved restore audio imports and eliminate double page/message staging copies.
4. Add explicit idempotent shutdown across Forge, Sound, Prompt Writer, Image GIFs, help GIFs, WebEngine, and tray.
5. Validate repeated open/close, restore cancellation, active analysis/import shutdown, and Windows file replacement.

Exit gate: no running timers/threads/media sources remain after shutdown, no nested event loops remain in normal navigation, and lifecycle tests cover active-worker exit.

## Validation strategy for the Ultra pass

For each phase:

1. Compare the opt-in timing log against U0 baselines.
2. Run focused tests for the changed owner, then the full suite with `py -3.13 -m unittest discover -s test -p 'test_*.py'`.
3. Run an offscreen GUI lifecycle check and a real Windows packaged/manual check; offscreen results do not prove WebEngine, multimedia, or file-handle behavior.
4. Verify visible behavior: same assets, animation settings, saved-letter ordering, audio assignment, generated viewer, publishing inputs, and restore rollback.
5. Test at 1, 100, and 1,000 Saved entries and with large static images, multi-frame GIFs, long playlists, and active background workers.

## Recommended success targets

Targets should be finalized from packaged U0 measurements, but the current evidence supports:

- Ready-project constructor: under 1.5 s warm, with first window shown before unused tabs initialize.
- Unchanged ordinary tab switch: under 50 ms of synchronous GUI work and zero project writes.
- Forge refresh after a burst of changes: one fingerprint/current check per settled revision.
- Forge entry with Saved panel closed: no saved-letter enumeration or cover decode.
- Load Letters, 1,000 metadata-only entries: initial visible results under 250 ms, remainder incremental.
- Overlay interaction: no GUI-thread operation over 50 ms; final full render runs off-thread.
- Repeat no-change Generate/Preview: no asset regeneration and no full source rehash.
- Shutdown with active workers: bounded, clean, and free of `QThread`/media/WebEngine lifecycle warnings.
