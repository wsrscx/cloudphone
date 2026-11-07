from __future__ import annotations

import asyncio
import hashlib
import tempfile
import unittest
from pathlib import Path

from cloudphone.devices import AdbResult, AdbRunner


class _ArtifactAdb(AdbRunner):
    def __init__(self, installed_digest: str | None, pm_output: str | None = None):
        super().__init__("adb")
        self.installed_digest = installed_digest
        self.pm_output = pm_output or "package:/data/app/example/base.apk"
        self.shell_calls: list[tuple[str, ...]] = []
        self.install_calls: list[tuple[str, Path]] = []

    async def shell(self, serial: str, *args: str, **_kwargs: object) -> AdbResult:
        self.shell_calls.append((serial, *args))
        if args[:2] == ("pm", "path"):
            if self.installed_digest is None:
                return AdbResult(b"", b"package not found", 1)
            return AdbResult(self.pm_output.encode(), b"", 0)
        if args[0] == "sha256sum":
            assert self.installed_digest is not None
            return AdbResult(f"{self.installed_digest}  {args[1]}\n".encode(), b"", 0)
        raise AssertionError(f"unexpected shell command: {args!r}")

    async def install(self, serial: str, apk: Path) -> None:
        await asyncio.sleep(0)
        self.install_calls.append((serial, apk))
        self.installed_digest = self._file_sha256(apk)


class _PushAdb(AdbRunner):
    def __init__(self) -> None:
        super().__init__("adb")
        self.remote_digest: str | None = None
        self.push_calls: list[tuple[str, Path, str]] = []

    async def shell(self, serial: str, *args: str, **_kwargs: object) -> AdbResult:
        if args[0] != "sha256sum" or self.remote_digest is None:
            return AdbResult(b"", b"not found", 1)
        return AdbResult(f"{self.remote_digest}  {args[1]}\n".encode(), b"", 0)

    async def push(self, serial: str, local: Path, remote: str) -> None:
        await asyncio.sleep(0)
        self.push_calls.append((serial, local, remote))
        self.remote_digest = self._file_sha256(local)


class ArtifactCacheTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.apk = Path(self.temporary.name) / "ime.apk"
        self.apk.write_bytes(b"signed apk bytes")
        self.digest = hashlib.sha256(self.apk.read_bytes()).hexdigest()

    async def asyncTearDown(self) -> None:
        self.temporary.cleanup()

    async def test_fresh_runner_skips_matching_installed_base_apk(self) -> None:
        adb = _ArtifactAdb(
            self.digest,
            "\n".join([
                "package:/data/app/example/split_config.en.apk",
                " package:/data/app/example/base.apk ",
                "package:/data/app/example/split_config.arm64_v8a.apk",
            ]),
        )

        changed = await adb.install_if_changed("serial", self.apk, "example.package")

        self.assertFalse(changed)
        self.assertEqual(adb.install_calls, [])
        self.assertEqual(
            adb.shell_calls,
            [
                ("serial", "pm", "path", "example.package"),
                ("serial", "sha256sum", "/data/app/example/base.apk"),
            ],
        )

        self.assertFalse(await adb.install_if_changed("serial", self.apk, "example.package"))
        self.assertEqual(adb.shell_calls[-1], ("serial", "pm", "path", "example.package"))
        self.assertEqual(len(adb.shell_calls), 3)

    async def test_mismatched_apk_is_installed(self) -> None:
        adb = _ArtifactAdb(hashlib.sha256(b"old apk").hexdigest())

        changed = await adb.install_if_changed("serial", self.apk, "example.package")

        self.assertTrue(changed)
        self.assertEqual(adb.install_calls, [("serial", self.apk)])

    async def test_concurrent_calls_install_only_once(self) -> None:
        adb = _ArtifactAdb(None)

        results = await asyncio.gather(*(
            adb.install_if_changed("serial", self.apk, "example.package") for _ in range(4)
        ))

        self.assertEqual(results.count(True), 1)
        self.assertEqual(results.count(False), 3)
        self.assertEqual(adb.install_calls, [("serial", self.apk)])

    async def test_ambiguous_pm_path_output_falls_back_to_install(self) -> None:
        adb = _ArtifactAdb(
            self.digest,
            "\n".join([
                "package:/data/app/example/split_config.en.apk",
                "package:/data/app/example/split_config.arm64_v8a.apk",
            ]),
        )

        self.assertTrue(await adb.install_if_changed("serial", self.apk, "example.package"))
        self.assertEqual(adb.install_calls, [("serial", self.apk)])
        self.assertNotIn(("serial", "sha256sum", "/data/app/example/split_config.en.apk"), adb.shell_calls)

    async def test_concurrent_pushes_transfer_artifact_only_once(self) -> None:
        adb = _PushAdb()

        results = await asyncio.gather(*(
            adb.push_if_changed("serial", self.apk, "/data/local/tmp/server.jar") for _ in range(4)
        ))

        self.assertEqual(results.count(True), 1)
        self.assertEqual(results.count(False), 3)
        self.assertEqual(adb.push_calls, [("serial", self.apk, "/data/local/tmp/server.jar")])

    async def test_remote_verification_rejects_mismatch_and_caches_success(self) -> None:
        adb = _PushAdb()
        remote = "/data/local/cloudphone/server.jar"
        adb.remote_digest = hashlib.sha256(b"old server").hexdigest()

        self.assertFalse(await adb.remote_artifact_matches("serial", self.apk, remote))
        adb.remote_digest = self.digest
        self.assertTrue(await adb.remote_artifact_matches("serial", self.apk, remote))
        adb.remote_digest = None
        self.assertTrue(await adb.remote_artifact_matches("serial", self.apk, remote))


if __name__ == "__main__":
    unittest.main()
