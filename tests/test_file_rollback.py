import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tacoagent.core.file_journal import FileJournal, FileConflictError
from tacoagent.core.workspace import WorkspaceSandbox
from tacoagent.tools import FileCreateTool, FileEditTool, FileDeleteTool


class FileRollbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sandbox = WorkspaceSandbox(self.root)
        self.journal = FileJournal(self.sandbox)

    def test_edit_restores_exact_bytes_and_is_idempotent(self):
        path = self.root / 'a'
        original = b'old\r\nsecond\r\n'
        path.write_bytes(original)
        result = FileEditTool(self.sandbox).invoke(file_path='a', old_text='old', new_text='new')
        self.assertTrue(FileJournal(self.root).rollback(result['change_id'])['ok'])
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(self.journal.rollback(result['change_id'])['status'], 'already_rolled_back')

    def test_create_undo_deletes_and_delete_undo_restores_binary(self):
        result = FileCreateTool(self.sandbox).invoke(file_path='sub/a', content='agent')
        self.assertTrue(self.journal.rollback(result['change_id'])['ok'])
        self.assertFalse((self.root / 'sub/a').exists())
        path = self.root / 'binary'
        path.write_bytes(b'\x00\xff')
        result = FileDeleteTool(self.sandbox).invoke(file_path='binary')
        self.assertFalse(path.exists())
        self.assertTrue(self.journal.rollback(result['change_id'])['ok'])
        self.assertEqual(path.read_bytes(), b'\x00\xff')

    def test_user_changes_are_preserved_for_edit_create_and_delete(self):
        for operation in ('edit', 'create', 'delete'):
            with self.subTest(operation=operation):
                path = self.root / operation
                if operation == 'create':
                    result = FileCreateTool(self.sandbox).invoke(file_path=operation, content='agent')
                else:
                    path.write_text('old')
                    if operation == 'edit':
                        result = FileEditTool(self.sandbox).invoke(file_path=operation, old_text='old', new_text='agent')
                    else:
                        result = FileDeleteTool(self.sandbox).invoke(file_path=operation)
                path.write_text('user')
                self.assertEqual(self.journal.rollback(result['change_id'])['status'], 'conflict')
                self.assertEqual(path.read_text(), 'user')

    def test_reverse_order_edits_and_out_of_order_conflict(self):
        (self.root / 'a').write_text('A')
        tool = FileEditTool(self.sandbox)
        first = tool.invoke(file_path='a', old_text='A', new_text='B')['change_id']
        second = tool.invoke(file_path='a', old_text='B', new_text='C')['change_id']
        self.assertFalse(self.journal.rollback(first)['ok'])
        self.assertTrue(self.journal.rollback(second)['ok'])
        self.assertTrue(self.journal.rollback(first)['ok'])
        self.assertEqual((self.root / 'a').read_text(), 'A')

    def test_same_content_with_new_version_is_conflict(self):
        result = FileCreateTool(self.sandbox).invoke(file_path='a', content='agent')
        path = self.root / 'a'
        timestamp = path.stat().st_mtime_ns
        os.utime(path, ns=(timestamp + 1_000_000, timestamp + 1_000_000))
        self.assertFalse(self.journal.rollback(result['change_id'])['ok'])

    def test_failed_journal_save_prevents_modification(self):
        path = self.root / 'a'
        path.write_bytes(b'old')
        with patch.object(FileJournal, '_save', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.journal.change('a', b'old', b'new')
        self.assertEqual(path.read_bytes(), b'old')

    def test_concurrent_recreation_during_restore_is_not_overwritten(self):
        (self.root / 'a').write_text('old')
        result = FileDeleteTool(self.sandbox).invoke(file_path='a')
        install = self.journal._install
        def race(path, data, mode):
            path.write_text('user')
            return install(path, data, mode)
        with patch.object(self.journal, '_install', side_effect=race):
            self.assertFalse(self.journal.rollback(result['change_id'])['ok'])
        self.assertEqual((self.root / 'a').read_text(), 'user')

    def test_no_internal_paths_or_traversal(self):
        for name in ('.tacoagent/undo/a', '.git/config', '../outside'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.journal.change(name, None, b'bad')

    def test_cli_session_ownership_and_active_workspace_lock(self):
        from typer.testing import CliRunner
        from tacoagent.cli import app
        from tacoagent.core.session_space import SessionSpace, workspace_lease
        session_id, workspace = SessionSpace(self.root, 'alice').create()
        change = FileCreateTool(WorkspaceSandbox(workspace)).invoke(file_path='a', content='agent')['change_id']
        args = ['rollback', change, '-w', str(self.root), '--session', session_id]
        runner = CliRunner()
        self.assertNotEqual(runner.invoke(app, args + ['--user', 'bob']).exit_code, 0)
        self.assertTrue((workspace / 'a').exists())
        with workspace_lease(workspace):
            self.assertNotEqual(runner.invoke(app, args + ['--user', 'alice']).exit_code, 0)
        result = runner.invoke(app, args + ['--user', 'alice'])
        self.assertEqual(result.exit_code, 0, result.stdout)
        self.assertFalse((workspace / 'a').exists())

    def test_edit_between_version_check_and_capture_preserves_user_content(self):
        path = self.root / 'a'
        result = FileCreateTool(self.sandbox).invoke(file_path='a', content='agent')
        rename = os.rename
        def race(source, destination):
            Path(source).write_text('user')
            return rename(source, destination)
        with patch('tacoagent.core.file_journal.os.rename', side_effect=race):
            self.assertFalse(self.journal.rollback(result['change_id'])['ok'])
        self.assertEqual(path.read_text(), 'user')

    def test_legacy_snapshot_cannot_force_overwrite(self):
        from tacoagent.core.checkpoint import restore_workspace_git
        path = self.root / 'a'
        path.write_text('user')
        self.assertFalse(restore_workspace_git(self.root, self.root, 'old-commit'))
        self.assertEqual(path.read_text(), 'user')
