"""Local multi-user session workspaces and crash-released process leases.

Identity must be supplied by a trusted caller; this is not an OS sandbox.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID, uuid4


class SessionBusyError(RuntimeError):
    pass


@contextmanager
def workspace_lease(workspace: Path):
    """Fail fast when another thread/process owns this execution workspace."""
    root = workspace.resolve() / ".tacoagent"
    root.mkdir(parents=True, exist_ok=True)
    with (root / "execution.lock").open("a+b") as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise SessionBusyError("This workspace already has an active turn") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class SessionSpace:
    """One private code copy per user/session, never automatically merged."""

    def __init__(self, project: str | Path, user_id: str):
        if not user_id or not user_id.strip():
            raise ValueError("user_id must not be empty")
        self.project = Path(project).resolve()
        self.user_id = user_id
        user_key = hashlib.sha256(user_id.encode("utf-8")).hexdigest()
        self.root = self.project / ".tacoagent" / "sessions"
        self.legacy_root = self.project / ".tacoagent" / "users" / user_key / "sessions"

    def _check_storage(self, root: Path | None = None):
        current = self.project
        for part in (root or self.root).relative_to(self.project).parts:
            current = current / part
            if current.is_symlink() or getattr(current, "is_junction", lambda: False)():
                raise PermissionError("Session storage cannot contain links")

    def open(self, session_id: str) -> Path:
        self._check_storage()
        session_id = str(UUID(session_id))
        directory = self.root / session_id
        if not directory.exists() and not directory.is_symlink():
            self._check_storage(self.legacy_root)
            directory = self.legacy_root / session_id
        if directory.is_symlink() or getattr(directory, "is_junction", lambda: False)():
            raise PermissionError("Session directory cannot be a link")
        metadata = json.loads((directory / "owner.json").read_text(encoding="utf-8"))
        if metadata != {"user_id": self.user_id, "session_id": session_id}:
            raise PermissionError("Session ownership mismatch")
        workspace = directory / "workspace"
        if workspace.is_symlink() or getattr(workspace, "is_junction", lambda: False)() or not workspace.is_dir():
            raise PermissionError("Invalid session workspace")
        return workspace

    def create(self) -> tuple[str, Path]:
        from tacoagent.core.session import save_session, _new_session

        session_id = str(uuid4())
        self._check_storage()
        # Also coordinate with legacy runs while copying their source workspace.
        with workspace_lease(self.project):
            self.root.mkdir(parents=True, exist_ok=True)
            staging = Path(tempfile.mkdtemp(prefix=".creating-", dir=self.root))
            try:
                def ignore(directory, names):
                    return [name for name in names if name in {
                        ".git", ".tacoagent", ".venv", "venv", "node_modules", "__pycache__",
                        "TODO.md", "NOTEPAD.md", "HISTORY_SUMMARY.md",
                    } or (Path(directory) / name).is_symlink()
                       or (getattr(Path(directory) / name, "is_junction", lambda: False)())]

                workspace = staging / "workspace"
                shutil.copytree(self.project, workspace, ignore=ignore)
                session = _new_session()
                session.update(session_id=session_id, owner_id=self.user_id)
                save_session(workspace, session)
                (staging / "owner.json").write_text(json.dumps({
                    "user_id": self.user_id, "session_id": session_id,
                }), encoding="utf-8")
                staging.rename(self.root / session_id)
            except BaseException:
                shutil.rmtree(staging)
                raise
        return session_id, self.open(session_id)

    def list(self) -> list[str]:
        self._check_storage()
        self._check_storage(self.legacy_root)
        sessions = set()
        for root in (self.root, self.legacy_root):
            if not root.exists():
                continue
            for path in root.iterdir():
                if path.name.startswith("."):
                    continue
                try:
                    self.open(path.name)
                except (ValueError, OSError):
                    continue
                sessions.add(str(UUID(path.name)))
        return sorted(sessions)
