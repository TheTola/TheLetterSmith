# macOS FFmpeg tools

Place native, redistributable `ffmpeg` and `ffprobe` executables in this
directory on the macOS build host. Both files must match the selected target
architecture (`arm64`, `x86_64`, or `universal2`), be executable, and pass
`-version` before packaging begins.

The release builder deliberately does not download third-party binaries or
accept Homebrew paths. This keeps the signed app self-contained and makes the
binary provenance and license review explicit.
