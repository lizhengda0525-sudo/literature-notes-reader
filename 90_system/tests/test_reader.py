"""Exercise the desktop bridge and the window's static asset boundary using disposable papers."""
from http.client import HTTPConnection
import base64
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "90_system/scripts"))
from desktop import DesktopAPI
from desktop_store import StateStore
from literature import Workspace
from reader import ReaderHandler, ReaderServer, zotero_links
from test_workflow import make_pdf

PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jNp0AAAAASUVORK5CYII="


class DesktopReaderTests(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / ".tmp"
        scratch.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="reader-tests-", dir=scratch)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for folder in ("00_project", "10_inbox", "20_papers", "30_reviews", "40_ideas", "90_system/templates"):
            (self.root / folder).mkdir(parents=True)
        for relative in ("90_system/workspace.json", "90_system/templates/paper_note.md"):
            shutil.copyfile(ROOT / relative, self.root / relative)
        (self.root / "00_project/questions.md").write_text("## RQ01 Test\n", "utf-8")
        (self.root / "00_project/sources.md").write_text("# Test\n", "utf-8")
        ws = Workspace(self.root)
        self.pid = ws.register(make_pdf(self.root / "10_inbox/test.pdf", "reader"))["paper_id"]
        self.project = str(self.root)
        self.folder = ws.papers / self.pid
        self.api = DesktopAPI(StateStore(self.root / "test-state"), self.project)
        self.server = ReaderServer(("127.0.0.1", 0), ReaderHandler)
        self.server.controller = self.api
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join()

    def request(self, path, method="GET", headers=None):
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            connection.request(method, path, headers=headers or {})
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def test_assets_only_no_browser_note_api_or_pdf(self):
        self.assertEqual(self.request("/assets/vendor/blue-topaz/theme.css")[0], 200)
        for path in ("/api/papers", "/api/papers/" + self.pid, "/api/identity", "/api/desktop/status",
                     "/paper/" + self.pid + "/source.pdf", "/assets/%2e%2e/scripts/literature.py"):
            self.assertEqual(self.request(path)[0], 404)
        self.assertEqual(self.request(f"/api/papers/{self.pid}/open-note", "POST")[0], 403)
        self.assertEqual(self.request("/", headers={"Host":"untrusted.example"})[0], 403)

    def test_detail_matches_disk_and_catalog_and_evidence(self):
        path = self.folder / "close-reading.md"
        row = f"| {self.pid}:E001 | analysis | v1 | 1 | 图 1 | 测试判断 | fixture excerpt | ai |"
        path.write_text(path.read_text("utf-8") + "\n" + row + "\n", "utf-8-sig")
        data = self.api.read_note(self.project, self.pid)
        self.assertEqual(data["note_hash"], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(data["note"], path.read_text("utf-8-sig"))
        self.assertEqual(data["versions"][0]["pages"], 1)
        self.assertEqual(data["evidence"][0]["id"], self.pid + ":E001")
        self.assertEqual(self.api.list_papers(self.project)["papers"][0]["id"], self.pid)

    def test_image_saved_once_per_paper_and_relative_link_survives_reopen(self):
        original = self.api.read_note(self.project, self.pid)
        saved = self.api.add_image(self.project, self.pid, PNG)
        data = base64.b64decode(PNG)
        rel = saved["path"]
        self.assertEqual(rel, "images/" + hashlib.sha256(data).hexdigest() + ".png")
        self.assertEqual(self.api.add_image(self.project, self.pid, PNG), saved)
        self.assertEqual(len(list((self.folder / "images").iterdir())), 1)
        self.assertEqual(self.request(f"/paper/{self.pid}/{rel}"), (200, data))
        note = original["note"] + f"\n![示例]({rel})\n"
        self.assertTrue(self.api.save_note(self.project, self.pid, note, original["note_hash"])["ok"])
        reopened = self.api.read_note(self.project, self.pid)
        self.assertEqual(reopened["note"], note)
        self.assertTrue(self.api.save_note(self.project, self.pid, "stale", original["note_hash"])["conflict"])

    def test_invalid_image_and_wrong_project_cannot_write(self):
        for project, pid, data in ((self.project, self.pid, "YWJj"), (self.project, self.pid, "bad!"),
                                   (self.project, "../P0001", PNG), ("other", self.pid, PNG)):
            self.assertFalse(self.api.add_image(project, pid, data)["ok"])
        self.assertFalse((self.folder / "images").exists())

    def test_theme_is_saved_for_the_selected_project(self):
        self.assertTrue(self.api.save_ui(self.project, {"theme": "light"})["ok"])
        reopened = DesktopAPI(StateStore(self.root / "test-state"), self.project)
        self.assertEqual(reopened.get_status()["ui"]["theme"], "light")

    def test_external_launches_use_registered_note_and_zotero_uri(self):
        with patch("reader.os.startfile", create=True) as launch:
            self.assertTrue(self.api.open_note(self.project, self.pid)["ok"])
            launch.assert_called_once_with(str(self.folder / "close-reading.md"))
            self.assertFalse(self.api.open_zotero(self.project, self.pid)["ok"])
            path = self.folder / "paper.json"
            meta = json.loads(path.read_bytes())
            meta.update(zotero_library_type="user", zotero_attachment_key="ABCD1234")
            path.write_text(json.dumps(meta), "utf-8")
            self.assertTrue(self.api.open_zotero(self.project, self.pid)["ok"])
            launch.assert_called_with("zotero://open-pdf/library/items/ABCD1234")
            meta["note_path"] = "source.pdf"
            path.write_text(json.dumps(meta), "utf-8")
            self.assertFalse(self.api.open_note(self.project, self.pid)["ok"])

    def test_zotero_links_require_explicit_library_and_valid_keys(self):
        self.assertIsNone(zotero_links({"zotero_item_key":"ABCDEFGH"})["item"])
        self.assertEqual(zotero_links({"zotero_attachment_key":"1234ABCD", "zotero_library_type":"group",
                                     "zotero_library_id":123})["pdf"], "zotero://open-pdf/groups/123/items/1234ABCD")
