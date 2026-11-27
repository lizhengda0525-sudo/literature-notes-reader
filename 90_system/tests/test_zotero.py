"""Exercise Zotero import, selection and subsequent library edits using a disposable SQLite library."""
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "90_system/scripts"))
from literature import Workspace, WorkspaceError, dump, sha
from reader import zotero_links
from zotero_library import ZoteroLibrary, bind_item, link_revision, profile_database, resolve_paper


class ZoteroTests(unittest.TestCase):
    def setUp(self):
        (ROOT / ".tmp").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / ".tmp")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.db = root / "zotero.sqlite"
        self.writer = sqlite3.connect(self.db)
        self.addCleanup(self.writer.close)
        self.writer.executescript("""
            CREATE TABLE libraries(libraryID INTEGER PRIMARY KEY,type);
            CREATE TABLE groups(groupID,libraryID,name);
            CREATE TABLE itemTypes(itemTypeID INTEGER PRIMARY KEY,typeName);
            CREATE TABLE items(itemID INTEGER PRIMARY KEY,key,itemTypeID,libraryID);
            CREATE TABLE fields(fieldID INTEGER PRIMARY KEY,fieldName);
            CREATE TABLE itemData(itemID,fieldID,valueID);
            CREATE TABLE itemDataValues(valueID INTEGER PRIMARY KEY,value);
            CREATE TABLE deletedItems(itemID);
            CREATE TABLE itemAttachments(itemID,parentItemID,contentType,path);
            CREATE TABLE creators(creatorID INTEGER PRIMARY KEY,firstName,lastName);
            CREATE TABLE creatorTypes(creatorTypeID INTEGER PRIMARY KEY,creatorType);
            CREATE TABLE itemCreators(itemID,creatorID,creatorTypeID,orderIndex);
            INSERT INTO libraries VALUES(1,'user'),(2,'group');
            INSERT INTO groups VALUES(42,2,'Methods');
            INSERT INTO itemTypes VALUES(1,'journalArticle'),(2,'attachment'),(3,'note');
            INSERT INTO fields VALUES(1,'title'),(2,'DOI'),(3,'date');
            INSERT INTO creators VALUES(1,'Ari','Jones');
            INSERT INTO creatorTypes VALUES(1,'author');
        """)
        (root / "00_project").mkdir()
        dump(root / "90_system/workspace.json", {})
        self.ws = Workspace(root)
        self.pid = "P0001"
        self.path = self.ws.papers / self.pid / "paper.json"
        dump(self.path, {"paper_id": self.pid, "title": "A Longitudinal Study", "authors": ["Ari Jones"], "year": 2023})
        self.library = ZoteroLibrary(self.db)

    def paper(self, n=1, lib=1, title="A Longitudinal Study", doi="", year="2023"):
        c = self.writer
        c.execute("INSERT INTO items VALUES(?,?,1,?)", (n, "ARTICLE1", lib))
        for field, value in [(1, title), (2, doi), (3, year)]:
            v = c.execute("INSERT INTO itemDataValues(value) VALUES(?)", (value,)).lastrowid
            c.execute("INSERT INTO itemData VALUES(?,?,?)", (n, field, v))
        c.execute("INSERT INTO itemCreators VALUES(?,1,1,0)", (n,))
        c.commit()

    def pdf(self, n=5, parent=1, lib=1, name="paper.pdf"):
        key = f"FILE{n:04d}"
        self.writer.execute("INSERT INTO items VALUES(?,?,2,?)", (n, key, lib))
        self.writer.execute("INSERT INTO itemAttachments VALUES(?,?,?,?)", (n, parent, "application/pdf", "storage:" + name))
        self.writer.commit()
        return key

    def resolve(self):
        return resolve_paper(self.ws, self.pid, self.library)

    def test_import_after_first_read_then_attach_pdf_and_repeat(self):
        self.writer.execute("PRAGMA journal_mode=WAL")
        self.assertEqual(self.resolve()[1], "unmatched")
        self.paper(title="A LONGITUDINAL study")
        meta, status = self.resolve()
        self.assertEqual(status, "linked")
        self.assertIsNone(zotero_links(meta)["pdf"])
        key = self.pdf()
        self.library.loaded_at = 0
        meta, status = self.resolve()
        self.assertEqual(zotero_links(meta)["pdf"], "zotero://open-pdf/library/items/" + key)
        prior = self.path.stat().st_mtime_ns
        self.resolve()
        self.assertEqual(prior, self.path.stat().st_mtime_ns)
        self.assertEqual(meta["title"], "A Longitudinal Study")

    def test_duplicates_require_choice_and_pdf_must_belong_to_chosen_group(self):
        self.paper(); self.paper(2, 2)
        self.pdf(); key = self.pdf(6, 2, 2)
        original = self.ws.paper(self.pid)
        self.assertEqual(self.resolve(), (original, "choose"))
        options = self.library.search(original)
        self.assertEqual(options["total"], 2)
        group = "groups/42/ARTICLE1"
        with self.assertRaises(WorkspaceError):
            bind_item(self.ws, self.pid, self.library, group, "FILE0005", "manual", options["revision"])
        meta = bind_item(self.ws, self.pid, self.library, group, key, "manual", options["revision"])
        self.assertEqual(zotero_links(meta)["pdf"], "zotero://open-pdf/groups/42/items/" + key)
        with self.assertRaises(WorkspaceError):
            bind_item(self.ws, self.pid, self.library, "library/ARTICLE1", None, "auto", options["revision"])

    def test_multiple_pdfs_and_deliberate_item_only_choice(self):
        self.paper(); self.pdf(); self.pdf(6, name="appendix.pdf")
        original, status = self.resolve()
        self.assertEqual(status, "choose_pdf")
        choice = bind_item(self.ws, self.pid, self.library, "library/ARTICLE1", None, "item", link_revision(original))
        self.assertEqual(self.resolve(), (choice, "linked"))
        self.assertIsNone(zotero_links(choice)["pdf"])
        dump(self.ws.papers / "P0002/paper.json", {"paper_id": "P0002", "title": original["title"]})
        with self.assertRaisesRegex(WorkspaceError, "P0001"):
            bind_item(self.ws, "P0002", self.library, "library/ARTICLE1", None, "auto", link_revision(self.ws.paper("P0002")))

    def test_unscoped_legacy_link_preserves_selected_pdf_or_requests_review(self):
        self.paper(); self.pdf(); pdf = self.pdf(6)
        meta = self.ws.paper(self.pid)
        meta.update(zotero_item_key="ARTICLE1", zotero_attachment_key=pdf)
        dump(self.path, meta)
        bound, status = self.resolve()
        self.assertEqual(status, "linked")
        self.assertTrue(zotero_links(bound)["pdf"].endswith("/" + pdf))
        meta["zotero_item_key"] = "OLDITEM1"
        dump(self.path, meta)
        self.assertEqual(self.resolve(), (meta, "choose"))

    def test_removed_pdf_or_parent_requires_repair(self):
        self.paper(); self.pdf()
        bound, _ = self.resolve()
        self.writer.execute("INSERT INTO deletedItems VALUES(5)"); self.writer.commit()
        self.assertEqual(self.resolve(), (bound, "missing_pdf"))
        self.writer.execute("INSERT INTO deletedItems VALUES(1)"); self.writer.commit()
        self.assertEqual(self.resolve(), (bound, "missing"))
        self.assertEqual(self.library.search(bound, "longitudinal")["total"], 0)

    def test_doi_identity_and_conflicting_title_year_or_authors(self):
        meta = self.ws.paper(self.pid)
        meta["doi"] = "https://doi.org/10.1234/ABC"
        self.paper(title="Different title", doi="10.1234/abc")
        self.assertEqual(self.library.search(meta)["automatic"]["reason"], "DOI 一致")
        meta["title"] = "Different title"
        meta["doi"] = "10.1234/other"
        self.assertIsNone(self.library.search(meta)["automatic"])
        meta.update(doi=None, authors=["Someone Else"])
        self.assertIsNone(self.library.search(meta)["automatic"])
        meta.update(authors=["Ari Jones"], year=2010)
        self.assertIsNone(self.library.search(meta)["automatic"])

    def test_standalone_import_and_search_by_filename_are_read_only(self):
        key = self.pdf(parent=None, name="A Longitudinal Study.pdf")
        digest = sha(self.db)
        result = self.library.search(self.ws.paper(self.pid), "longitudinal")
        self.assertIsNone(result["automatic"])
        found = result["candidates"][0]
        self.assertTrue(found["standalone"])
        meta = bind_item(self.ws, self.pid, self.library, found["ref"], key, "manual", result["revision"])
        self.assertEqual(zotero_links(meta)["pdf"], "zotero://open-pdf/library/items/" + key)
        self.assertEqual(sha(self.db), digest)

    def test_profile_selects_active_custom_directory_and_missing_db_is_not_created(self):
        base = Path(self.temp.name)
        profiles = base / "Zotero/Zotero"
        folder = profiles / "Profiles/chosen"
        folder.mkdir(parents=True)
        (profiles / "profiles.ini").write_text("[Profile0]\nPath=old\n[Profile1]\nPath=Profiles/chosen\nDefault=1\n", "utf-8")
        custom = base / "导入论文"
        (folder / "prefs.js").write_text('user_pref("extensions.zotero.dataDir", ' + json.dumps(str(custom)) + ');', "utf-8")
        with patch.dict("os.environ", {"APPDATA": str(base)}):
            self.assertEqual(profile_database(), custom / "zotero.sqlite")
            with self.assertRaisesRegex(WorkspaceError, "找不到"):
                ZoteroLibrary().refresh()
        self.assertFalse(custom.exists())
