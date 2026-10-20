# Context footer regression fixes — 2026-10-20

## Baseline and isolation

The task opened at `cf795130881b0be9b276f59321902d70b953b27a`.
The verification baseline is the subsequently committed
`43fa32fd75320e93a1403cf90c8072bd69cb7073` on `main`.
Windows, Python 3.13.13, PySide6 6.11.1.

All verification used managed worktrees and isolated profiles under
`C:/Temp/ls-footer-fix-20261020`. The repository runner redirects runtime data,
Qt settings and temporary files, audits writes and blocks external networking.
The user's running application and saved projects were left intact.
Concurrent Images, Prompt Writer and Saved Letters work is excluded from this
patch, including unrelated hunks in `Forge_Tab.py` and `test/test_saved_projects.py`.

## Changes

| File | Change |
|---|---|
| `Nexus.py` | Footer follows both the selected tab and active application workspace. Recipient/startup and full-page Prompt Writer hide it; returning restores the current tab's footer. Sound preview sizing measures the actual scroll viewport width, preventing wrapped labels from sustaining an unnecessary scrollbar. |
| `Forge_Tab.py` | Compact format/curtain controls share a row with enough width; Export participates in compact sizing. Empty status text reserves no row; messages and errors remain visible and timed clearing removes the row. Fonts and actions remain intact. |
| `test/test_saved_projects.py` | Save/reopen test uses the production inline Prompt Writer and compares the saved prompt against the final widget text, not a potentially older canonical value. |
| `test/test_window_sizing.py` | Exercises real tab clicks, cold initialization, Prompt Writer entry/return, real assigned audio, visible controls, compact containment and status clearing. Corrects stale assumptions described below. |
| `docs/foundation-contract.md` | Documents workspace-specific footer visibility. |

The three requested failures reproduced on the unchanged baseline:
Forge preview height (18 failing subcases), Sound/Messages layout (61),
and Saved Projects' obsolete `panel.isWindow()` assertion (1).
All three passed after the repairs. Subcases are not additional test cases.

The complete sizing suite also exposed four existing stale checks, reproduced
independently at the same baseline:

- Prompt Writer was expected to remain open after selecting Sound, despite its
  established inline-workspace lifecycle. The test now checks that Sound becomes
  active while the prompt draft and reusable writer instance survive.
- Protected-project Images expected the hidden legacy card; it now checks the
  actual page strip. New Project containment/non-overlap assertions remain.
- Sound compared a playback child label's local coordinates to the tab's
  coordinates. Both positions are now mapped into the same parent.
- Stale image import expected retired warning wording. The assertion now uses
  the current explicit destination warning; unchanged project data, target
  identity, exact prompts and GIF-byte checks remain.

The Sound/Messages fixture now assigns a real synthetic audio record instead of
manually changing labels on an empty-music UI. It checks the current Messages
selector/editor launcher rather than controls owned by the detached editor.
No fit thresholds, font checks, content comparisons or stale-write protections
were relaxed.

## Native inspection

An isolated synthetic five-page Nexus session was inspected through actual
Windows interaction at 1180×820:

- Images: `5 Pages`; Sound: assigned synthetic song.
- Messages: paper, opacity, contrast and zoom; actual artwork/text preview.
- Forge: GitHub Pages and the actual disconnected account status.
- Prompt Writer: authoring footer hidden; return restores Forge's hosting footer.

A fresh process after the final production change verified Forge at 900×480:
both format selectors fit, the preview renders, all actions remain reachable,
and the hosting footer is visible. Screenshots were inspected in-session; no
private screenshots or fixture content were committed. Native helper scripts
and logs remain in the evidence directory. The helpers use production Nexus
widgets/services with network authorization checks and UI sounds disabled.
This is not a new packaged executable, audible-playback or cross-platform test.

## Reproduction

Run from the tested checkout with the existing virtual environment:

```powershell
.venv/Scripts/python.exe release/verify_tests.py --output C:/Temp/ls-footer-fix-20261020/final-candidate --module test_messages_workspace --module test_readiness_editor_paths --module test_saved_projects --module test_saved_letter_recipient_restore --module test_window_sizing --module test_restored_workspaces.FullPageIntegrationTests.test_writer_occupies_main_workspace_and_single_service_button_opens_conversation
```

Use a fresh output directory for a rerun. The runner records the baseline,
working-tree state, per-module results and logs in `results.json`.
`baseline`, `broader-baseline` and `import-assertion-baseline` contain the
unmodified-baseline reproductions. `candidate-hashes.json` identifies the
tested production and test file contents.

## Final result

Final candidate: baseline `43fa32fd75320e93a1403cf90c8072bd69cb7073` plus
the changes in this commit. The final run followed the last production/test
edit; documentation was completed afterward.

| Suite | Passed | Skipped | Failed/errors |
|---|---:|---:|---:|
| Messages workspace | 30 | 0 | 0 |
| Readiness/editor paths | 59 | 0 | 0 |
| Saved projects | 18 | 0 | 0 |
| Recipient restoration | 1 | 0 | 0 |
| Complete window sizing | 35 | 3 | 0 |
| Inline Prompt Writer integration | 1 | 0 | 0 |
| Total distinct tests in the final run | **144** | **3** | **0** |

The three skips require native Windows destruction, maximized-window restore,
and multiple-monitor placement. The native footer/compact-layout inspection
above does not count as execution of those skipped tests. `git diff --check`
also passed. This is focused regression verification, not a full repository,
package or release certification.

Only the six files listed in this report (including the report itself) are
part of this commit. Staged implementation/test blobs were checked against the
isolated candidate before committing; concurrent working-tree hunks remain
outside the commit. The exact delivered revision is the commit containing
this document. Reopen the application to load the updated source; an already
running Python process does not reload these modules automatically.
