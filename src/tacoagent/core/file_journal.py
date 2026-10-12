"""Durable, conservative undo for attributable file-tool writes.

All participating writers share a process lock. External writers are checked
again after capturing the destination; installation never overwrites a path.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
import tempfile
from pathlib import Path
from uuid import UUID, uuid4

from tacoagent.core.session_space import workspace_lease
from tacoagent.core.workspace import WorkspaceSandbox


class FileConflictError(RuntimeError):
    pass


def version(path: Path):
    try:
        before = path.lstat()
    except FileNotFoundError:
        return None, None
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise FileConflictError("Only ordinary files without hard links are supported")
    data = path.read_bytes()
    after = path.lstat()
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
        raise FileConflictError("File changed while reading")
    return data, {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data),
                  "mtime_ns": after.st_mtime_ns, "inode": after.st_ino,
                  "mode": stat.S_IMODE(after.st_mode)}


class FileJournal:
    def __init__(self, workspace):
        self.sandbox = workspace if isinstance(workspace, WorkspaceSandbox) else WorkspaceSandbox(workspace)
        self.root = self.sandbox.root / ".tacoagent" / "undo"
        self._no_links(self.root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _no_links(self, path):
        current = self.sandbox.root
        for part in path.relative_to(current).parts:
            current /= part
            if current.is_symlink() or getattr(current, "is_junction", lambda: False)():
                raise FileConflictError("Linked paths cannot be journaled or restored")

    def path(self, relative):
        requested = Path(relative)
        if requested.is_absolute() or ".." in requested.parts or not requested.parts:
            raise ValueError("Expected a workspace-relative file path")
        if requested.parts[0].casefold() in {".git", ".tacoagent"}:
            raise ValueError("Internal metadata cannot be edited through file tools")
        path = self.sandbox.root / requested
        self._no_links(path)
        self.sandbox.resolve(relative)
        return path

    def _save(self, record):
        target = self.root / (record["id"] + ".json")
        fd, temporary = tempfile.mkstemp(dir=self.root, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(record, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def _install(self, path, data, mode):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._no_links(path)
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".taco-write-")
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if mode is not None:
                os.chmod(temporary, mode)
            # Exclusive publication: never replace a concurrently created file.
            os.link(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def _exchange(self, relative, expected, data, mode, record):
        path = self.path(relative)
        if version(path)[1] != expected:
            raise FileConflictError("Current file version differs from the recorded version")
        captured = self.root / (record["id"] + "." + str(uuid4()) + ".captured")
        if expected is not None:
            record.setdefault("captured_paths", []).append(captured.name)
            self._save(record)
        moved = False
        try:
            if expected is not None:
                os.rename(path, captured)
                moved = True
                if version(captured)[1] != expected:
                    raise FileConflictError("File changed during the operation")
            if data is not None:
                self._install(path, data, mode)
            elif path.exists() or path.is_symlink():
                raise FileConflictError("File was recreated during deletion")
        except BaseException:
            if moved:
                try:
                    os.link(captured, path)
                except OSError:
                    # Keep the displaced content when a concurrent writer owns path.
                    pass
                else:
                    captured.unlink()
            raise
        # Retain the displaced inode: an external editor may still have an open
        # handle to it. Never discard those late writes; manual recovery can
        # inspect captured_paths even after a successful operation.
        actual, result = version(path)
        if actual != data:
            raise FileConflictError("File changed after publication; operation is uncertain")
        return result

    def change(self, relative, before, after, *, expected_version=None):
        with workspace_lease(self.root):
            path = self.path(relative)
            current, token = version(path)
            if current != before or (expected_version is not None and token != expected_version):
                raise FileConflictError("File changed since it was read; read it again")
            record = {"id": str(uuid4()), "path": path.relative_to(self.sandbox.root).as_posix(),
                      "status": "prepared", "before": None if before is None else base64.b64encode(before).decode(),
                      "before_version": token, "after_version": None,
                      "operation": "create" if before is None else "delete" if after is None else "edit"}
            self._save(record)  # Durable before image precedes disk mutation.
            try:
                record["after_version"] = self._exchange(relative, token, after,
                    token["mode"] if token else None, record)
            except BaseException:
                record["status"] = "uncertain"
                self._save(record)
                raise
            record["status"] = "applied"
            self._save(record)
            return record["id"]

    def entries(self):
        self._no_links(self.root)
        records = []
        for path in sorted(self.root.glob("*.json"), key=lambda p: p.stat().st_mtime_ns):
            self._no_links(path)
            records.append(json.loads(path.read_text(encoding="utf-8")))
        return records

    def rollback(self, change_id):
        change_id = str(UUID(change_id))
        with workspace_lease(self.root):
            entry = self.root / (change_id + ".json")
            self._no_links(entry)
            record = json.loads(entry.read_text(encoding="utf-8"))
            if record["id"] != change_id:
                raise ValueError("Journal identity mismatch")
            if record["status"] == "rolled_back":
                return {"ok": True, "id": change_id, "status": "already_rolled_back"}
            if record["status"] != "applied":
                return {"ok": False, "id": change_id, "status": "conflict", "error": "Incomplete operation requires manual recovery"}
            before = None if record["before"] is None else base64.b64decode(record["before"], validate=True)
            token = record["before_version"]
            if before is not None and hashlib.sha256(before).hexdigest() != token["sha256"]:
                raise ValueError("Before image checksum mismatch")
            try:
                restored_token = self._exchange(record["path"], record["after_version"], before,
                                               token["mode"] if token else None, record)
            except (OSError, ValueError, FileConflictError) as exc:
                return {"ok": False, "id": change_id, "path": record["path"], "status": "conflict", "error": str(exc)}
            record["status"] = "rolled_back"
            self._save(record)
            # Permit reverse-order undo of an uninterrupted chain of agent edits.
            # Equality includes the original inode and timestamp, not just bytes.
            if token is not None:
                for prior in self.entries():
                    if (prior["status"] == "applied" and prior["path"] == record["path"]
                            and prior["after_version"] == token):
                        prior["after_version"] = restored_token
                        self._save(prior)
            return {"ok": True, "id": change_id, "path": record["path"], "status": "rolled_back"}
