"""Desktop saves, conflicts, recovery and lifecycle use disposable synthetic papers."""
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "90_system/scripts"))
from desktop_store import NoteStore, StateStore, fingerprint
from desktop import DesktopAPI
from literature import Workspace, WorkspaceError
from test_workflow import make_pdf


class DesktopTests(unittest.TestCase):
    def setUp(self):
        (ROOT / ".tmp").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="desktop-tests-", dir=ROOT / ".tmp")
        self.root = Path(self.temp.name) / "中文 项目"
        for folder in ("00_project", "10_inbox", "20_papers", "30_reviews", "40_ideas", "90_system/templates"):
            (self.root / folder).mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / "90_system/workspace.json", self.root / "90_system/workspace.json")
        shutil.copy(ROOT / "90_system/templates/paper_note.md", self.root / "90_system/templates/paper_note.md")
        (self.root / "00_project/questions.md").write_text("## RQ01 Test\n", encoding="utf-8")
        (self.root / "00_project/sources.md").write_text("# Test sources\n", encoding="utf-8")
        self.pid = Workspace(self.root).register(make_pdf(self.root / "10_inbox/test.pdf", "desktop"))["paper_id"]
        self.state = StateStore(Path(self.temp.name) / "state")
        self.notes = NoteStore(self.root, self.state)
        self.path = self.notes.path(self.pid)

    def tearDown(self):
        self.temp.cleanup()

    def test_save_preserves_metadata_original_pdf_and_line_endings(self):
        meta = self.path.with_name("paper.json").read_bytes()
        pdf = self.path.with_name("source.pdf").read_bytes()
        self.path.write_bytes(b"# TEST\r\nPersonal fixture words\r\n")
        old = self.notes.read(self.pid)
        result = self.notes.save(self.pid, "# TEST\nPersonal fixture words\nNew paragraph\n", old["hash"])
        self.assertTrue(result["ok"])
        self.assertEqual(self.path.read_bytes(), b"# TEST\r\nPersonal fixture words\r\nNew paragraph\r\n")
        self.assertEqual(meta, self.path.with_name("paper.json").read_bytes())
        self.assertEqual(pdf, self.path.with_name("source.pdf").read_bytes())
        self.assertFalse((self.root / "00_project/_desktop-note.lock").exists())

    def test_external_edit_conflicts_without_overwrite_and_encrypts_recovery(self):
        before = self.notes.read(self.pid)
        self.path.write_text("# EXTERNAL VERSION\n", encoding="utf-8")
        result = self.notes.save(self.pid, "# UNSAVED PRIVATE FIXTURE\n", before["hash"])
        self.assertTrue(result["conflict"])
        self.assertEqual(self.path.read_text("utf-8"), "# EXTERNAL VERSION\n")
        restored = self.state.recovery(self.root, self.pid)
        self.assertEqual(restored["text"], "# UNSAVED PRIVATE FIXTURE\n")
        recovery = next((self.state.folder / "recovery").glob("*.bin"))
        self.assertNotIn(b"UNSAVED PRIVATE FIXTURE", recovery.read_bytes())

    def test_replace_failure_retains_original_and_latest_draft(self):
        before = self.notes.read(self.pid)
        real = os.replace
        def fail_note(source, destination):
            if Path(destination) == self.path:
                raise OSError("simulated full disk")
            return real(source, destination)
        with patch("desktop_store.os.replace", side_effect=fail_note):
            with self.assertRaises(OSError):
                self.notes.save(self.pid, "# DRAFT AFTER FAILURE\n", before["hash"])
        self.assertEqual(self.notes.read(self.pid)["hash"], before["hash"])
        self.assertEqual(self.state.recovery(self.root, self.pid)["text"], "# DRAFT AFTER FAILURE\n")

    def test_recovery_can_survive_project_drive_unavailable(self):
        moved = self.root.with_name("offline")
        self.root.rename(moved)
        self.notes.draft(self.pid, "unsaved while offline", "base")
        self.assertEqual(self.state.recovery(self.root, self.pid)["text"], "unsaved while offline")
        moved.rename(self.root)

    def test_controller_rejects_stale_project_and_arbitrary_paths(self):
        api = DesktopAPI(self.state, self.root)
        result = api.save_note(str(self.root / "wrong"), self.pid, "bad", "hash")
        self.assertFalse(result["ok"])
        self.assertFalse(api.read_note(str(self.root), "../../secret")["ok"])
        self.assertFalse(api.open_link("file:///C:/Windows/notepad.exe")["ok"])
        self.assertFalse(api.open_link("javascript:alert(1)")["ok"])

    def test_project_missing_is_not_recreated_and_recent_project_is_retained(self):
        self.state.data["last_project"] = str(self.root / "missing")
        self.state.flush()
        api = DesktopAPI(self.state)
        self.assertFalse(api.get_status()["available"])
        self.assertFalse((self.root / "missing").exists())
        self.assertEqual(self.state.data["last_project"], str(self.root / "missing"))

    def test_preferences_and_last_project_restore(self):
        api = DesktopAPI(self.state, self.root)
        ui = {"last_paper": self.pid, "positions": {self.pid: 333}, "font_size": 19, "private_key": "must not persist"}
        self.assertTrue(api.save_ui(str(self.root), ui)["ok"])
        reopened = DesktopAPI(StateStore(self.state.folder))
        value = reopened.get_status()
        self.assertEqual(value["ui"]["positions"][self.pid], 333)
        self.assertNotIn("private_key", value["ui"])

    def test_signature_detects_external_note_change(self):
        first = self.notes.signature()
        self.path.write_text("# External changes\n", encoding="utf-8")
        self.assertNotEqual(first, self.notes.signature())

    def test_close_keeps_latest_draft_and_cancel_does_not_close(self):
        api = DesktopAPI(self.state, self.root)
        snapshot = {"dirty": True, "project": str(self.root), "pid": self.pid,
                    "text": "last unsaved keystroke", "base_hash": "old", "ui": {}}
        api._window = SimpleNamespace(evaluate_js=lambda _: snapshot,
            create_confirmation_dialog=lambda *_: False, width=1200, height=800)
        self.assertFalse(api._closing())
        self.assertEqual(self.state.recovery(self.root, self.pid)["text"], snapshot["text"])

    def test_close_is_cancelled_when_recovery_cannot_be_written(self):
        api = DesktopAPI(self.state, self.root)
        snapshot = {"dirty": True, "project": str(self.root), "pid": self.pid,
                    "text": "last edit", "base_hash": "old", "ui": {}}
        api._window = SimpleNamespace(evaluate_js=lambda _: snapshot,
            create_confirmation_dialog=lambda *_: True, width=1200, height=800)
        with patch.object(self.state, "recovery", side_effect=OSError("full disk")):
            self.assertFalse(api._closing())

    def test_drafts_for_two_papers_are_not_confused(self):
        second = Workspace(self.root).register(make_pdf(self.root / "10_inbox/two.pdf", "two"))["paper_id"]
        api = DesktopAPI(self.state, self.root)
        for pid in (self.pid, second):
            self.assertTrue(api.update_draft(str(self.root), pid, "identical text", "base", True)["ok"])
        api._flush_drafts()
        self.assertEqual(self.state.recovery(self.root, self.pid)["text"], "identical text")
        self.assertEqual(self.state.recovery(self.root, second)["text"], "identical text")


if __name__ == "__main__":
    unittest.main()
