from dataclasses import dataclass

from tacoagent.core.file_journal import FileJournal, version
from tacoagent.core.workspace import WorkspaceSandbox


@dataclass(slots=True)
class FileDeleteTool:
    workspace: WorkspaceSandbox
    name: str = "file_delete"
    description: str = "Delete one workspace file with a durable undo record; directories are not supported."

    def invoke(self, *, file_path: str):
        journal = FileJournal(self.workspace)
        data, token = version(journal.path(file_path))
        if token is None:
            raise FileNotFoundError(file_path)
        change_id = journal.change(file_path, data, None, expected_version=token)
        return {"ok": True, "path": file_path, "deleted": True, "change_id": change_id}
