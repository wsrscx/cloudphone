# Upstream scrcpy server v4.1 for direct streaming

This directory pins upstream scrcpy v4.1 at commit
`2926c06c5dc3064ae6d8db706f1a98a37cfcf3f0`. `SOURCE.lock` records both the
source archive and official unmodified server hashes. The Apache-2.0 license is preserved in
`LICENSE`.

`direct-build-v4.1.patch` contains build compatibility changes only:

- compile SDK 34 and Android 15 constant compatibility;
- AGP 8.7.3, Gradle 8.9, and Build Tools 34.0.0 compatibility.

It does not change capture, encoder, keyframe, reset, quality, or control behavior. The runtime
artifact therefore uses the upstream scrcpy 4.1 protocol without project-specific message types.
Quality changes are performed by stopping the current process and starting a fresh upstream server
with new command-line constraints.

The build workspace name includes the patch SHA-256 prefix, so rebuilding after a patch change
cannot silently reuse a source tree containing an older patch revision.

`build-direct-server.ps1` downloads the pinned source archive when it is absent, verifies the
recorded hash, applies the patch with `git apply`, runs the upstream unit tests, release build, and
release lint, then emits the direct server artifact. It
does not install tools; JDK and Android SDK must already be available to Gradle.

Use `scripts/build.ps1` for normal project builds. It temporarily maps the repository to an ASCII
drive path on Windows, which avoids Gradle and JDK file-lock/path failures when the checkout path
contains non-ASCII characters. The mapping is always removed when the build finishes.

The default output is `third_party/scrcpy/out/scrcpy-server-direct-v4.1`; the project build writes
`dist/scrcpy-server-direct-v4.1`. Distribute the adjacent Apache-2.0 license and retain the upstream
scrcpy attribution. The legacy cloud-phone patch and build script remain only as archived sources
and are not part of the runtime build.
