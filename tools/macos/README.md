# macOS FFmpeg release inputs

`release/provision_ffmpeg.py` stages the checksum-pinned providers and versions
defined in `release/ffmpeg_manifest.json`. The macOS validator/build invokes it
automatically, writes `ffmpeg` and `ffprobe` here, and records the exact source,
license declaration, archive hashes, staged hashes, and version banners in
`tools/FFmpeg-PROVENANCE.txt`.

The current arm64 and x86_64 inputs are separate Martin Riedl 9.0.1 release
builds with committed archive and binary SHA-256 values. Redirect or `latest`
URLs are not used.

The provisioner creates thin arm64 or x86_64 tools and can combine both verified
inputs into universal2 tools. It removes quarantine metadata, sets executable
permissions, verifies required FFmpeg capabilities and architecture slices, and
ad-hoc signs the staged files before PyInstaller applies the final app signature.
The release validator also rejects non-system dynamic-library dependencies.

Use `--ffmpeg-source-dir PATH` only with reviewed local inputs named `ffmpeg` and
`ffprobe`; otherwise the pinned HTTPS artifacts are downloaded. Homebrew paths
are never used at runtime. The staged tools, cache, and generated provenance are
ignored by Git.

Before public redistribution, retain the providers' signature/checksum evidence,
the applicable license text, and matching Corresponding Source/build materials
with the release. The currently pinned macOS builds declare GPL-3.0-or-later;
the binary URLs alone do not replace the distributor's source-code obligations.

Verify on the native build host:

```bash
chmod 755 tools/macos/ffmpeg tools/macos/ffprobe
tools/macos/ffmpeg -version
tools/macos/ffprobe -version
/usr/bin/lipo -archs tools/macos/ffmpeg
/usr/bin/lipo -archs tools/macos/ffprobe
/usr/bin/otool -L tools/macos/ffmpeg
/usr/bin/otool -L tools/macos/ffprobe
shasum -a 256 tools/macos/ffmpeg tools/macos/ffprobe
```
