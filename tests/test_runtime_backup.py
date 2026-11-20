import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from runtime_backup import (
    BackupError,
    create_backup,
    list_backups,
    restore_backup,
    verify_backup,
)


class RuntimeBackupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.database = self.base / "Data" / "users.db"
        self.database.parent.mkdir(parents=True)
        self._write_value("original")

    def _write_value(self, value: str):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS state (value TEXT)")
            connection.execute("DELETE FROM state")
            connection.execute("INSERT INTO state VALUES (?)", (value,))
            connection.commit()

    def _read_value(self) -> str:
        with closing(sqlite3.connect(self.database)) as connection:
            return connection.execute("SELECT value FROM state").fetchone()[0]

    def test_backup_is_consistent_and_verifiable(self):
        result = create_backup(base_dir=self.base)

        self.assertIsNotNone(result.path)
        self.assertEqual(result.database_count, 1)
        manifest = verify_backup(result.path, backup_root=self.base / "Backups")
        self.assertEqual(manifest["databases"][0]["path"], "Data/users.db")
        self.assertEqual(len(list_backups(base_dir=self.base)), 1)

    def test_restore_requires_force_and_preserves_recovery_snapshot(self):
        snapshot = create_backup(base_dir=self.base).path
        self._write_value("changed")

        with self.assertRaisesRegex(BackupError, "--force"):
            restore_backup(snapshot, base_dir=self.base)

        recovery = restore_backup(snapshot, base_dir=self.base, force=True)
        self.assertEqual(self._read_value(), "original")
        self.assertIsNotNone(recovery.path)
        self.assertGreaterEqual(len(list_backups(base_dir=self.base)), 2)

    def test_checksum_tampering_is_detected(self):
        snapshot = create_backup(base_dir=self.base).path
        manifest_path = snapshot / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["databases"][0]["sha256"] = "0" * 64
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        with self.assertRaisesRegex(BackupError, "checksum"):
            verify_backup(snapshot, backup_root=self.base / "Backups")

    def test_retention_removes_only_old_timestamped_snapshots(self):
        start = datetime(2026, 1, 1, tzinfo=UTC)
        for offset in range(3):
            self._write_value(str(offset))
            create_backup(
                base_dir=self.base,
                keep=2,
                now=start + timedelta(seconds=offset),
            )

        snapshots = list_backups(base_dir=self.base)
        self.assertEqual(len(snapshots), 2)
        self.assertTrue(all(path.name.startswith("20260101-") for path in snapshots))

    def test_missing_databases_are_a_safe_noop(self):
        self.database.unlink()
        result = create_backup(base_dir=self.base)
        self.assertIsNone(result.path)
        self.assertEqual(result.database_count, 0)


if __name__ == "__main__":
    unittest.main()
