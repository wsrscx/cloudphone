# Third-party notices

The runtime launches **scrcpy-server v4.1**, distributed by Genymobile under
the Apache License 2.0. The reproducible build pins upstream tag `v4.1` at
commit `2926c06c5dc3064ae6d8db706f1a98a37cfcf3f0`.

Project: https://github.com/Genymobile/scrcpy

License: https://www.apache.org/licenses/LICENSE-2.0

`direct-build-v4.1.patch` contains Android SDK and build-tool compatibility
changes only. It does not add control messages, keyframe requests, reset
messages, quality commands, media transports, or capture behavior. Runtime
quality changes restart the upstream server with new standard command-line
parameters.

Distributed binaries retain scrcpy's copyright and Apache 2.0 notice. The
corresponding pinned source information and compatibility patch are included
under `third_party/scrcpy`.
