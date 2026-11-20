"""Consistent, recoverable backups for Project L's runtime SQLite databases."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from config_loader import BASE_DIR


BACKUP_DIRECTORY_NAME = "Backups"
BACKUP_NAME_PATTERN = re.compile(r"^\d{8}-\d{6}-\d{6}$")
MANIFEST_NAME = "manifest.json"
KNOWN_DATABASES = (
    Path("Data/users.db"),
    Path("Data/user.db"),
    Path("sticky.db"),
)


class BackupError(RuntimeError):
    """Raised when a backup cannot be created, verified, or restored safely."""


@dataclass(frozen=True)
class BackupResult:
    path: Path | None
    database_count: int
    removed_count: int = 0


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _runtime_path(base_dir: Path, relative_path: Path) -> Path:
    resolved_base = base_dir.resolve()
    resolved = (resolved_base / relative_path).resolve()
    if not _is_within(resolved, resolved_base):
        raise BackupError(
            f"Runtime database path escaped the project directory: {relative_path}"
        )
    return resolved


def _backup_root(base_dir: Path, backup_root: Path | None = None) -> Path:
    root = (
        backup_root.resolve()
        if backup_root is not None
        else (base_dir.resolve() / BACKUP_DIRECTORY_NAME).resolve()
    )
    if backup_root is None and not _is_within(root, base_dir.resolve()):
        raise BackupError(
            "The backup directory must stay inside the project directory."
        )
    return root


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sqlite_integrity_check(path: Path) -> None:
    """Raise BackupError unless SQLite reports a clean database."""
    try:
        uri = f"{path.resolve().as_uri()}?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            result = connection.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.Error as exc:
        raise BackupError(f"Could not inspect SQLite database {path}: {exc}") from exc
    if not result or str(result[0]).casefold() != "ok":
        detail = result[0] if result else "no result"
        raise BackupError(f"SQLite integrity check failed for {path}: {detail}")


def _copy_sqlite_consistently(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        source_uri = f"{source.resolve().as_uri()}?mode=ro"
        with (
            closing(sqlite3.connect(source_uri, uri=True)) as source_connection,
            closing(sqlite3.connect(destination)) as destination_connection,
        ):
            source_connection.backup(destination_connection)
    except sqlite3.Error as exc:
        raise BackupError(f"Could not back up {source}: {exc}") from exc
    sqlite_integrity_check(destination)


def _manifest_entry(relative_path: Path, backup_file: Path) -> dict[str, Any]:
    return {
        "path": relative_path.as_posix(),
        "sha256": _sha256(backup_file),
        "size": backup_file.stat().st_size,
    }


def _safe_backup_directories(root: Path) -> list[Path]:
    if not root.exists():
        return []
    directories = []
    for candidate in root.iterdir():
        if (
            candidate.is_dir()
            and BACKUP_NAME_PATTERN.fullmatch(candidate.name)
            and _is_within(candidate, root)
        ):
            directories.append(candidate.resolve())
    return sorted(directories, key=lambda item: item.name, reverse=True)


def prune_backups(root: Path, keep: int) -> int:
    """Remove only timestamped backup directories beyond the retention limit."""
    if keep < 1:
        raise BackupError("Backup retention must keep at least one snapshot.")
    resolved_root = root.resolve()
    removed = 0
    for candidate in _safe_backup_directories(resolved_root)[keep:]:
        if candidate.parent != resolved_root or not _is_within(
            candidate, resolved_root
        ):
            raise BackupError(f"Refusing to remove unsafe backup path: {candidate}")
        shutil.rmtree(candidate)
        removed += 1
    return removed


def create_backup(
    *,
    base_dir: Path = BASE_DIR,
    backup_root: Path | None = None,
    keep: int = 14,
    now: datetime | None = None,
) -> BackupResult:
    """Create one consistent snapshot of every existing Project L database."""
    resolved_base = base_dir.resolve()
    root = _backup_root(resolved_base, backup_root)
    existing = [
        (relative, _runtime_path(resolved_base, relative))
        for relative in KNOWN_DATABASES
        if _runtime_path(resolved_base, relative).is_file()
    ]
    if not existing:
        removed = prune_backups(root, keep) if root.exists() else 0
        return BackupResult(path=None, database_count=0, removed_count=removed)

    root.mkdir(parents=True, exist_ok=True)
    timestamp = (now or datetime.now(UTC)).astimezone(UTC)
    name = timestamp.strftime("%Y%m%d-%H%M%S-%f")
    final_directory = (root / name).resolve()
    if final_directory.exists():
        raise BackupError(f"Backup snapshot already exists: {final_directory}")
    temporary_directory = (root / f".{name}-{uuid.uuid4().hex}.tmp").resolve()
    if not _is_within(temporary_directory, root):
        raise BackupError("Refusing to create a backup outside the backup directory.")

    manifest_entries: list[dict[str, Any]] = []
    try:
        temporary_directory.mkdir(parents=False)
        for relative, source in existing:
            destination = temporary_directory / relative
            _copy_sqlite_consistently(source, destination)
            manifest_entries.append(_manifest_entry(relative, destination))

        manifest = {
            "format": 1,
            "created_at": timestamp.isoformat(),
            "databases": manifest_entries,
        }
        manifest_path = temporary_directory / MANIFEST_NAME
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary_directory, final_directory)
    except Exception:
        if temporary_directory.exists() and _is_within(temporary_directory, root):
            shutil.rmtree(temporary_directory)
        raise

    verify_backup(final_directory, backup_root=root)
    removed = prune_backups(root, keep)
    return BackupResult(
        path=final_directory,
        database_count=len(manifest_entries),
        removed_count=removed,
    )


def _validated_backup_directory(path: Path, backup_root: Path) -> Path:
    resolved_root = backup_root.resolve()
    resolved_path = path.resolve()
    if not _is_within(resolved_path, resolved_root) or resolved_path == resolved_root:
        raise BackupError(
            "The selected backup must be inside the Project L backup directory."
        )
    if not resolved_path.is_dir():
        raise BackupError(f"Backup directory not found: {resolved_path}")
    return resolved_path


def _load_manifest(backup_directory: Path) -> dict[str, Any]:
    manifest_path = backup_directory / MANIFEST_NAME
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BackupError(
            f"Could not read backup manifest {manifest_path}: {exc}"
        ) from exc
    if not isinstance(manifest, dict) or manifest.get("format") != 1:
        raise BackupError(f"Unsupported or invalid backup manifest: {manifest_path}")
    databases = manifest.get("databases")
    if not isinstance(databases, list) or not databases:
        raise BackupError(f"Backup manifest contains no databases: {manifest_path}")
    return manifest


def verify_backup(path: Path, *, backup_root: Path | None = None) -> dict[str, Any]:
    """Verify paths, hashes, sizes, and SQLite integrity for one snapshot."""
    root = (backup_root or path.resolve().parent).resolve()
    backup_directory = _validated_backup_directory(path, root)
    manifest = _load_manifest(backup_directory)
    known = {item.as_posix() for item in KNOWN_DATABASES}
    for entry in manifest["databases"]:
        if not isinstance(entry, dict):
            raise BackupError("Backup manifest contains an invalid database entry.")
        relative_text = str(entry.get("path", ""))
        relative = Path(relative_text)
        if (
            relative.as_posix() not in known
            or relative.is_absolute()
            or ".." in relative.parts
        ):
            raise BackupError(
                f"Backup manifest contains an unsafe path: {relative_text}"
            )
        backup_file = (backup_directory / relative).resolve()
        if not _is_within(backup_file, backup_directory) or not backup_file.is_file():
            raise BackupError(f"Backup database is missing: {relative_text}")
        if backup_file.stat().st_size != int(entry.get("size", -1)):
            raise BackupError(f"Backup size mismatch: {relative_text}")
        if _sha256(backup_file) != str(entry.get("sha256", "")):
            raise BackupError(f"Backup checksum mismatch: {relative_text}")
        sqlite_integrity_check(backup_file)
    return manifest


def restore_backup(
    path: Path,
    *,
    base_dir: Path = BASE_DIR,
    backup_root: Path | None = None,
    force: bool = False,
    keep: int = 14,
) -> BackupResult:
    """Restore a verified snapshot after first taking a recovery backup."""
    if not force:
        raise BackupError(
            "Restore requires --force because it replaces live database files."
        )
    resolved_base = base_dir.resolve()
    root = _backup_root(resolved_base, backup_root)
    backup_directory = _validated_backup_directory(path, root)
    manifest = verify_backup(backup_directory, backup_root=root)
    # Keep every existing snapshot during restore so an older selected backup
    # cannot be pruned before its files are copied. Normal startup retention
    # resumes after the restore has completed.
    snapshot_count = len(_safe_backup_directories(root))
    recovery = create_backup(
        base_dir=resolved_base,
        backup_root=root,
        keep=max(keep, snapshot_count + 1),
    )

    for entry in manifest["databases"]:
        relative = Path(entry["path"])
        source = (backup_directory / relative).resolve()
        target = _runtime_path(resolved_base, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.restore.tmp")
        try:
            shutil.copy2(source, temporary)
            sqlite_integrity_check(temporary)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    return recovery


def list_backups(
    *, base_dir: Path = BASE_DIR, backup_root: Path | None = None
) -> list[Path]:
    return _safe_backup_directories(_backup_root(base_dir.resolve(), backup_root))


def _print_backup_result(result: BackupResult, quiet: bool) -> None:
    if quiet:
        return
    if result.path is None:
        print("No runtime databases exist yet; no backup was needed.")
    else:
        print(f"Backed up {result.database_count} database(s) to {result.path}")
    if result.removed_count:
        print(f"Removed {result.removed_count} expired backup snapshot(s).")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Back up and restore Project L databases"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    backup_parser = subparsers.add_parser("backup", help="create a verified backup")
    backup_parser.add_argument("--keep", type=int, default=14)
    backup_parser.add_argument("--quiet", action="store_true")
    subparsers.add_parser("list", help="list available backups")
    verify_parser = subparsers.add_parser("verify", help="verify a backup")
    verify_parser.add_argument("path", type=Path)
    restore_parser = subparsers.add_parser("restore", help="restore a verified backup")
    restore_parser.add_argument("path", type=Path)
    restore_parser.add_argument("--force", action="store_true")
    restore_parser.add_argument("--keep", type=int, default=14)

    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "backup":
            _print_backup_result(
                create_backup(keep=arguments.keep),
                arguments.quiet,
            )
        elif arguments.command == "list":
            for backup in list_backups():
                print(backup)
        elif arguments.command == "verify":
            verify_backup(arguments.path, backup_root=BASE_DIR / BACKUP_DIRECTORY_NAME)
            print(f"Backup verified: {arguments.path}")
        elif arguments.command == "restore":
            recovery = restore_backup(
                arguments.path,
                force=arguments.force,
                keep=arguments.keep,
            )
            print(f"Restored backup: {arguments.path}")
            if recovery.path is not None:
                print(f"Pre-restore recovery snapshot: {recovery.path}")
    except BackupError as exc:
        print(f"Backup error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
