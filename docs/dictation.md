# Offline dictation

Prompt Writer's **For All Pages** box, each page's directions, and editable generated
prompts have microphone controls. The letter editor has a microphone in its text
toolbar. Click to start, speak, then
click the stop symbol to finish the remaining speech. Final phrases enter the normal
text editor with its existing undo and save behavior. A selection is preserved;
dictation starts after it.

Right-click a microphone and choose **Dictation settings…** to
select a microphone, download the compact English model (191 MB), or choose an
existing verified English model pack. Supported packs contain exactly one of
`ggml-base.en.bin`, `ggml-small.en-q5_1.bin`, or `ggml-small.en.bin`, alongside
`ggml-silero-v6.2.0.bin`. Existing supported Holo Voice packs can be reused.
Downloads are explicit, cancellable, resumable, and checked against pinned hashes.
Recognition never downloads assets automatically.

English recognition runs locally on the CPU using the same Whisper/Silero pipeline
as Holo Voice. No API key, account, or paid speech service is needed. Audio stays in
bounded memory buffers; the application does not save recordings or copy dictated
text to the clipboard. Only finalized text follows normal project saving/export.
Model paths and microphone choices are device-local preferences, outside letters.

One field records at a time. Typing, changing the caret, switching pages or fields,
hiding/closing the editor, and Windows lock/suspend cancel unfinished words while
keeping committed phrases. Clicking Stop finishes queued audio. Temporary previews
are not document text and are excluded from autosave. Late results cannot retarget
another field or a replaced document. Windows session monitoring must be available
before capture starts.

Accuracy and latency depend on microphone quality, background noise, speech, and
CPU speed. The capture queue holds eight seconds; overflowing it stops recording
with a visible error and retains finalized text. A phrase ends after approximately
half a second of detected silence, or splits around 20 seconds with quiet-gap or
overlap handling. Recording sessions are limited to 100,000 finalized characters.

## Source and release builds

Run from the repository root on Windows:

```powershell
py -3.13 tools/build_dictation.py
py -3.13 tools/build_dictation.py --check
```

The developer build uses pinned whisper.cpp v1.9.5/b5454 libraries and a pinned
compiler. It writes the helper and libraries to `build/dictation-runtime` and their
hash manifest to `dictation_runtime_hashes.json`. Windows distributions include the
verified native runtime and upstream license; model weights remain separate local
assets. Installed users do not need a compiler. This dictation runtime currently
supports Windows.

`dictation.py` owns editor bindings and setup UI, `dictation_session.py` owns native
lock/suspend monitoring, `dictation_speech.py` owns capture/streaming/process
supervision, and `dictation_models.py` owns verified model setup. The implementation
is self-contained in LetterSmith and does not import Holo Desk at runtime.
