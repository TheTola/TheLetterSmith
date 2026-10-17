# Final integration repair record

Baseline: `637da7f86475b243314579bf3242aa1ef8d12690`, clean integrated
`codex/ui-synchronization`; work continues on `codex/final-integration`.
No feature branch integration was necessary. All nine owner implementations are
already ancestors of this candidate. The separate primary checkout and its dirty
changes are excluded.

| ID | Reproduction / observed defect | Authoritative repair | Regression evidence |
|---|---|---|---|
| FIX-01 | Cold/repeated Messages preview release navigated to blank repeatedly, restarting pending unloads and sometimes refusing shutdown. | `message_shared_preview.py` distinguishes released, unloading and loaded views. | `test_message_shared_preview` cold/repeated release regression; seven focused tests passed. |
| FIX-02 | An old ThemeService styled another service's dialog; full single-process baseline entered repeated global restyling. | `ui_theme.py` limits ownership to its registered roots, makes queued callbacks inert on shutdown; Nexus disposes after preflight. | New foreign-host/disposal test failed before repair; 52 focused theme tests passed afterward. |
| FIX-03 | A deferred recipient focus lambda accessed a deleted QLineEdit during later window transitions. | Qt context-bound timer in `recipient_page.py`. | `test_window_sizing.test_recipient_focus_callback_is_canceled_with_its_widget`; Command lifecycle 16 tests passed. |
| FIX-04 | Diagnostics event filtering crashed during widget construction/GC while allocating Qt enum aliases on every event. | Cache typed event constants and ignore closed controllers in `beta_diagnostics.py`. | Two diagnostic native-abort reproductions; six focused privacy/diagnostic tests passed after repair. |
| FIX-05 | Opening the rich-text style submenu used an invalid QAction wrapper; Update Style failed after collection. | Explicit QAction construction/parent ownership in `Editor.py`. | Existing real mouse/submenu test now includes GC; all 25 editor style tests passed. |
| FIX-06 | Native Sound stop could stall; import completion intermittently aborted while releasing worker references. Repair callbacks also directly invoked UI consumers from worker lambdas. | Queue player notifications and repair results, use QObject slots for completion; retain native worker handles until nonblocking join confirms completion. | Native transport suite and real import callback reproduction; final combined results recorded separately. |

## Verification infrastructure and justified expectation updates

`release/verify_tests.py` runs all 55 discovered modules serially in isolated
processes/profiles, records counts, skips, crashes and timeouts, denies Python
writes outside the evidence root and external connections, and retains logs.
The release test gate now uses it. Early runner errors concerning Windows extended
paths and NUL were harness defects, corrected before candidate verification.

Existing tests referenced removed four-card Images controls, an automatic close
retry predating Save/Discard/Cancel, obsolete button dimensions/headings, collapsed
playlist duplicates and private prompts in display packages. They now exercise
the actual selected-page controls, explicit exit retry, content-sized labels,
repeated playlist occurrences and separate editable/display persistence. The
publication-control fixture supplies a real active Letter message before asserting
final-output readiness. No persistence/renderer implementation is mocked to obtain
these results. Native transport controls use the real application event loop,
instead of the nested QTest wait that stalled inside the installed backend.

The resource-input gate initially found a Git LFS pointer for `com.gif`. Local
`git lfs checkout` restored the existing object. Existing local FFmpeg binaries
were copied only after matching the repository's pinned hashes. No dependency
upgrade or external media download was needed. macOS documentation now matches
the manifest's 15.0 minimum; no macOS run is implied.

Final verification, graphical evidence, unresolved cases and delivery status are
recorded in the final verification report and requirements matrix.

## Additional combined-run findings

- FIX-07: importing configuration before Main configured paths normalized the
  verification checkout's `settings.json` despite an explicit profile override.
  `application_paths()` now resolves that override before bootstrap for runtime
  resource/workspace roots while retaining explicit synthetic project roots.
  `test_profile_applies_before_bootstrap_without_mutating_resource_settings`
  exercises real SettingsStore writes and checks master bytes. 25 path tests passed.
- FIX-08: the application-wide text-fit filter read button text during unrelated
  destruction events; frame/placement filters also allocated event collections
  and inspected unrelated objects. Gate by event type before accessing widgets and
  cache frame/launcher event sets. The new destruction regression passed; the
  repeated window-sizing module completed 35 tests with three native-only skips.
  Earlier native abort traces are retained; a bounded passing run is not a claim
  about every Qt backend or sustained stress load.
- Legacy curtain tests now create independent synthetic legacy packages without
  a canonical snapshot and explicitly refresh the derived catalog. Editing a
  presentation sidecar cannot override a current editable snapshot. Eleven tests
  passed. Identity/shutdown tests now use the Messages-owned combined preview and
  real Nexus shutdown; 31 tests passed. Resizing checks target the actual current
  Messages controls. The synthetic external folder-move scenario uses the existing
  bounded Windows sharing retry; its injected delete failure remains asserted.

The optional startup appearance popup could not be targeted by the native
inspection tool. Its event loop was confirmed waiting, not a startup crash.
Packaged smoke checks use an existing synthetic appearance preference. The
experimental window-title change was reverted; no appearance behavior changed.

- FIX-09: native packaged inspection found Cover-specific direction/copy help on
  Letter B. Refresh the existing single-card help and empty-prompt placeholder
  from its actual canonical page label. The four-model launcher test now checks
  those descriptions for every target. Prompt content is untouched.

- FIX-10: opening Prompt Writer after a canonical-only save refreshed the old
  page-controller projection, incrementing the project revision solely for its
  compatibility cache. Native inspection showed a false Unsaved changes title;
  snapshot comparison found no authored-content change. Remove that redundant
  launch-time reload: the panel already reads canonical pages and subscribes to
  their events. A new real-construction regression failed before the removal.
