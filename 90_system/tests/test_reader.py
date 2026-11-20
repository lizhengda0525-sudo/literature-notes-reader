"""Exercise the real local reader HTTP boundary using disposable paper fixtures."""
from http.client import HTTPConnection
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
from literature import Workspace
from reader import create_server, reader_identity, serve, zotero_links
from test_workflow import make_pdf


class LocalReaderTests(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / ".tmp"
        scratch.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="reader-tests-", dir=scratch)
        self.root = Path(self.temp.name)
        for folder in ("00_project", "10_inbox", "20_papers", "30_reviews",
                       "40_ideas", "90_system/templates"):
            (self.root / folder).mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / "90_system/workspace.json", self.root / "90_system/workspace.json")
        shutil.copyfile(ROOT / "90_system/templates/paper_note.md",
                        self.root / "90_system/templates/paper_note.md")
        (self.root / "00_project/questions.md").write_text("## RQ01 Test\n", encoding="utf-8")
        self.pid = Workspace(self.root).register(make_pdf(self.root / "10_inbox/test.pdf", "reader"))["paper_id"]
        (self.root / "private.txt").write_text("Must remain unavailable", encoding="utf-8")
        self.server = create_server(self.root, 0)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join()
        self.temp.cleanup()

    def request(self, path, method="GET", headers=None):
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        connection.request(method, path, headers=headers or {})
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def test_paper_note_and_pdf_load_without_copying_or_writing_notes(self):
        note = self.root / "20_papers" / self.pid / "close-reading.md"
        before = note.read_bytes()
        status, headers, body = self.request("/api/papers/" + self.pid)
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["versions"][0]["pages"], 1)
        self.assertEqual(data["versions"][0]["label"], "v1")
        self.assertEqual(data["note"], note.read_text(encoding="utf-8"))
        status, headers, body = self.request("/paper/" + self.pid + "/source.pdf")
        self.assertEqual(status, 200)
        self.assertTrue(body.startswith(b"%PDF"))
        self.assertEqual(note.read_bytes(), before)
        status, _, body = self.request("/api/papers")
        self.assertEqual(json.loads(body)[0]["id"], self.pid)
        self.assertEqual(self.server.server_address[0], "127.0.0.1")

    def test_private_files_traversal_and_untrusted_host_are_not_served(self):
        for path in ("/private.txt", "/assets/%2e%2e/scripts/literature.py",
                     "/paper/" + self.pid + "/../../private.txt",
                     "/paper/" + self.pid + "/paper.json",
                     "/assets/%2e%2e/%2e%2e/.git/config"):
            self.assertEqual(self.request(path)[0], 404, path)
        self.assertEqual(self.request("/", headers={"Host": "untrusted.example"})[0], 403)
        self.assertEqual(self.request("/api/papers", method="POST")[0], 403)

    def test_byte_ranges_and_head_allow_original_pdf_access(self):
        route = "/paper/" + self.pid + "/source.pdf"
        status, headers, body = self.request(route, headers={"Range": "bytes=0-4"})
        self.assertEqual(status, 206)
        self.assertEqual(body, b"%PDF-")
        self.assertTrue(headers["Content-Range"].startswith("bytes 0-4/"))
        self.assertEqual(self.request(route, headers={"Range": "bytes=99999999-"})[0], 416)
        self.assertEqual(self.request(route, "HEAD")[2], b"")

    def test_reader_assets_are_local_and_markdown_is_returned_as_data(self):
        status, headers, body = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn(b"/assets/reader.mjs", body)
        self.assertIn("default-src 'self'", headers["Content-Security-Policy"])
        self.assertEqual(self.request("/assets/mapping.mjs")[0], 200)
        self.assertIn("javascript", self.request("/assets/mapping.mjs")[1]["Content-Type"])
        self.assertEqual(self.request("/assets/vendor/katex/katex.min.js")[0], 200)
        self.assertNotIn(b"pdf-canvas", body)
        self.assertIn(b"library-search", body)

    def test_catalog_reads_full_notes_for_cross_paper_search_without_writing(self):
        note = self.root / "20_papers" / self.pid / "close-reading.md"
        before = note.read_bytes()
        entry = json.loads(self.request("/api/papers")[2])[0]
        self.assertEqual(entry["note"], note.read_text(encoding="utf-8"))
        self.assertIn("research_questions", entry)
        self.assertIn("tags", entry)
        self.assertEqual(note.read_bytes(), before)

    def test_detail_returns_evidence_and_fingerprint_from_the_same_md(self):
        note = self.root / "20_papers" / self.pid / "close-reading.md"
        original = note.read_text(encoding="utf-8")
        row = f"| {self.pid}:E001 | analysis | v1 | 1 | 图 1 | 测试判断 | fixture excerpt | ai |"
        note.write_text(original + "\n" + row + "\n", encoding="utf-8-sig")
        data = json.loads(self.request("/api/papers/" + self.pid)[2])
        self.assertEqual(data["note_hash"], hashlib.sha256(note.read_bytes()).hexdigest())
        self.assertEqual(data["note"], note.read_text(encoding="utf-8-sig"))
        self.assertEqual(data["evidence"], [
            {"id": self.pid + ":E001", "kind": "analysis", "version": "v1", "page": 1,
             "locator": "图 1", "claim": "测试判断", "excerpt": "fixture excerpt", "checked_by": "ai"}
        ])

    def test_editor_opens_only_registered_note_from_same_origin(self):
        route = "/api/papers/" + self.pid + "/open-note"
        host = f"127.0.0.1:{self.server.server_port}"
        with patch("reader.os.startfile", create=True) as launch:
            self.assertEqual(self.request(route, "POST")[0], 403)
            self.assertEqual(self.request(route, "POST", {"Origin":"https://other.example",
                "X-Reader-Action":"open-note"})[0], 403)
            launch.assert_not_called()
            headers = {"Origin": "http://" + host, "X-Reader-Action": "open-note"}
            self.assertEqual(self.request(route, "POST", headers)[0], 200)
            launch.assert_called_once_with(str(self.root / "20_papers" / self.pid / "close-reading.md"))
            self.assertEqual(self.request("/api/papers/P9999/open-note", "POST", headers)[0], 400)
            metadata = self.root / "20_papers" / self.pid / "paper.json"
            data = json.loads(metadata.read_text(encoding="utf-8"))
            data["note_path"] = "source.pdf"
            metadata.write_text(json.dumps(data), encoding="utf-8")
            self.assertEqual(self.request(route, "POST", headers)[0], 400)
            self.assertEqual(launch.call_count, 1)

    def test_zotero_links_require_explicit_library_and_valid_keys(self):
        self.assertIsNone(zotero_links({"zotero_item_key": "ABCDEFGH"})["item"])
        self.assertEqual(zotero_links({"zotero_item_key": "ABCDEFGH", "zotero_library_type": "user"})["item"],
                         "zotero://select/library/items/ABCDEFGH")
        self.assertEqual(zotero_links({"zotero_attachment_key": "1234ABCD", "zotero_library_type": "group",
                                     "zotero_library_id": 123})["pdf"],
                         "zotero://open-pdf/groups/123/items/1234ABCD")

    def test_launch_reuses_only_the_same_workspace(self):
        self.assertEqual(json.loads(self.request("/api/identity")[2]), reader_identity(self.root))
        self.assertTrue(serve(self.root, self.pid, self.server.server_port, False)["reused"])
        from literature import WorkspaceError
        with self.assertRaises(WorkspaceError):
            serve(ROOT, None, self.server.server_port, False)

    def test_author_manuscript_is_not_mislabeled_by_reference_to_published_version(self):
        metadata = self.root / "20_papers" / self.pid / "paper.json"
        data = json.loads(metadata.read_text(encoding="utf-8"))
        data["source"]["versions"][0]["description"] = (
            "Author accepted manuscript; not a replacement for the version of record")
        metadata.write_text(json.dumps(data), encoding="utf-8")
        response = json.loads(self.request("/api/papers/" + self.pid)[2])
        self.assertEqual(response["versions"][0]["label"], "作者接受稿")


if __name__ == "__main__":
    unittest.main()
