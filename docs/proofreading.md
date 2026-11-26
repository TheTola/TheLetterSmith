# Spelling and grammar

The letter editor, Prompt Writer's **For All Pages**, every page's **Directions**,
and editable **Generated Prompt** use the same offline writing tools.

The dictionary automatically fixes established, completed typos, such as `teh`
to `the`. Uncertain spelling is underlined. Local AI checks changed paragraphs
after a pause and offers grammar corrections; accepting a suggestion applies it
as an ordinary, undoable edit. Grammar is not silently rewritten when saving.
Right-click an underlined passage for Writing Suggestions, Ignore, or Add to
Dictionary. Saved custom words and protected prompt terms are shared with the AI.

Automatic edits wait until dictation finishes and ignore temporary speech previews.
Corrections preserve rich text formatting and selection. Undo does not immediately
reapply the same correction. Loading or closing a project preserves saved wording,
and a delayed suggestion cannot apply to a changed document or another page.

## Local grammar setup

AI grammar runs on the computer without an account or API key. A verified existing
Holo Desk model pack can be reused; Holo Desk does not need to be installed or
running once a supported pack is available. **AI proofreading settings…** in the
writing menu can install a separate local pack or select an existing one. Downloads
are explicit, cancellable, and verified against pinned checksums. Dictionary
checking remains available when the model is unavailable or a check fails.

## Release resources

The release includes `language_ai`, `language_runtime`, `language_model_setup`, and
`proofreading` as application modules. `language_runtime_hashes.json` is bundled at
`resources/language-runtime/language_runtime_hashes.json`; source launches read
the manifest beside `language_runtime.py`. Model weights and model-runtime
binaries stay in the selected device-local pack, outside saved letters and exports.

## Changed files

- UI integration: `Editor.py`, `PromptWriterPanel.py`, `prompt_writer_workspace.py`,
  `dictation.py`.
- Shared engine: `language_service.py`, `proofreading.py`, `language_ai.py`,
  `language_runtime.py`, `language_runtime_hashes.json`, `language_model_setup.py`,
  `dictation_models.py`, `Main.py`.
- Release and documentation: `release/release_manifest.json`, `release/README.md`,
  `docs/proofreading.md`.
- Tests: `test/test_language_service.py`, `test/test_proofreading.py`,
  `test/test_dictation.py`, `test/test_readiness_editor_paths.py`,
  `test/test_restored_workspaces.py`.

## Validation

The focused integration run passed 163 tests. Real local inference passed through
the shared service, plain and rich editor bindings, and a frozen windowed worker.
Checks included manual acceptance, Undo, paragraph separators, supersession,
verified model reuse, and worker shutdown. Offscreen renders confirmed the review
and dictation controls remain separate and visible. The full LetterSmith installer
was not rebuilt as part of this change.
