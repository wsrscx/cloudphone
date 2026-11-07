from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cloudphone.db import Database
from cloudphone.devices import AdbRunner, DeviceTracker, parse_adb_device_lines


class DeviceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temporary.name) / "test.db")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_adb_listing_parses_states_without_exposing_serial(self) -> None:
        parsed = parse_adb_device_lines([
            "List of devices attached",
            "ABC123 device product:foo model:Pixel_8 device:foo transport_id:1",
            "NETWORK unauthorized transport_id:2",
        ])
        self.assertEqual(parsed[0].model, "Pixel 8")
        self.assertEqual(parsed[1].state, "unauthorized")

    def test_device_id_is_keyed_and_public_shape_omits_serial(self) -> None:
        tracker = DeviceTracker(self.db, AdbRunner("adb"), b"k" * 32)
        first = tracker.device_id("ABC123")
        self.assertEqual(first, tracker.device_id("ABC123"))
        self.assertNotIn("ABC123", first)
        record = self.db.upsert_device(first, "ABC123", "device", "Pixel", "15")
        self.assertNotIn("serial", record.public_dict())

    def test_authorization_and_offline_state_persist(self) -> None:
        self.db.upsert_device("dev_one", "ABC", "device", "Pixel", "15")
        updated = self.db.update_device("dev_one", alias="Game phone", authorized=True)
        self.assertTrue(updated.authorized)  # type: ignore[union-attr]
        self.db.mark_missing_offline(set())
        offline = self.db.get_device("dev_one")
        self.assertEqual(offline.state, "offline")  # type: ignore[union-attr]
        self.assertTrue(offline.authorized)  # type: ignore[union-attr]
