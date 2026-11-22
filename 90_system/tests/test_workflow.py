"""Behavioral tests use disposable synthetic documents, never the real paper catalog."""
from pathlib import Path
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "90_system/scripts"))
from literature import Workspace, WorkspaceError, dump, load, sha, safe_path, skill_package_errors, temporary_path
import setup_project

def make_pdf(path, label):
    stream = ("BT /F1 12 Tf 50 750 Td (TEST FIXTURE " + label + ") Tj ET").encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(str(i).encode() + b" 0 obj\n" + obj + b"\nendobj\n")
    xref = len(output)
    output.extend(b"xref\n0 6\n0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n" + str(xref).encode() + b"\n%%EOF\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(output)
    return path

class WorkspaceBehaviorTests(unittest.TestCase):
    def setUp(self):
        self.cache_root = (ROOT / ".tmp").resolve()
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="literature-tests-", dir=self.cache_root)
        self.root = Path(self.temp.name).resolve()
        self.assertTrue(self.root.is_relative_to(self.cache_root) and self.root.name.startswith("literature-tests-"))
        for relative in ("00_project", "10_inbox", "20_papers", "30_reviews", "40_ideas",
                         "90_system/templates", "90_system/index"):
            (self.root / relative).mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / "90_system/workspace.json", self.root / "90_system/workspace.json")
        for file in (ROOT / "90_system/templates").glob("*"):
            if file.is_file():
                shutil.copyfile(file, self.root / "90_system/templates" / file.name)
        (self.root / "00_project/sources.md").write_text("# Test sources\n", encoding="utf-8")
        (self.root / "00_project/questions.md").write_text("## RQ01 Test\n## RQ02 Test\n", encoding="utf-8")
        (self.root / "00_project/registrations.jsonl").write_text("", encoding="utf-8")
        self.ws = Workspace(self.root)

    def tearDown(self):
        self.assertTrue(self.root.is_relative_to(self.cache_root) and self.root.name.startswith("literature-tests-"))
        self.temp.cleanup()

    def add(self, name="one", **kwargs):
        return self.ws.register(make_pdf(self.root / "10_inbox" / f"{name}.pdf", name), **kwargs)["paper_id"]

    def finish_note(self, pid, figure="未找到作者提供的主方法框架图，待你决定如何处理。"):
        meta = self.ws.paper(pid)
        chunks = [f"# {pid} TEST NOTE"]
        for heading in self.ws.config["required_note_sections"]:
            chunks.extend(["", f"## {heading}"])
            chunks.append(figure if heading == "主方法框架图" else "Synthetic analysis.")
            if heading == "精读笔记":
                chunks.extend(["### 我的想法与疑问", "<!-- USER_NOTES_START -->",
                               "## TODO Keep my own heading and words.", "<!-- USER_NOTES_END -->"])
        path = self.root / "20_papers" / pid / meta["note_path"]
        path.write_text("\n".join(chunks) + "\n", encoding="utf-8")
        return path

    def ready_review(self, pids):
        result = self.ws.new_artifact("review", "Test comparison", pids, "RQ01")
        path = self.ws.artifact_dir("review", result["id"]) / "review.md"
        path.write_text("# TEST review\nThe studies used different sampling intervals.", encoding="utf-8")
        self.ws.stamp("review", "RQ01")
        return path

    def test_registration_deduplicates_and_only_consumes_inbox(self):
        original = make_pdf(self.root / "10_inbox/one.pdf", "one")
        digest = sha(original)
        result = self.ws.register(original, title="Signal | noise", doi="10.1234/test", rqs=["RQ01"])
        self.assertEqual(result["paper_id"], "P0001")
        self.assertFalse(original.exists())
        target = self.root / "20_papers/P0001/source.pdf"
        self.assertEqual(sha(target), digest)
        meta = self.ws.paper("P0001")
        note = target.parent / meta["note_path"]
        self.assertEqual(note.name, "close-reading.md")
        self.assertEqual(note.parent, target.parent)
        self.assertTrue(note.is_file())
        self.assertIn(r"| 原文题名 | Signal \| noise |", note.read_text("utf-8"))
        self.assertIn("| DOI | 10.1234/test |", note.read_text("utf-8"))
        self.assertFalse((target.parent / "notes").exists())
        catalog = load(self.root / "90_system/index/catalog.json")
        self.assertEqual(catalog["papers"][0]["note_path"], "20_papers/P0001/close-reading.md")
        external = self.root / "external.pdf"
        shutil.copyfile(target, external)
        self.assertFalse(self.ws.register(external)["created"])
        self.assertTrue(external.exists())
        self.assertEqual(self.add("two"), "P0002")
        self.assertTrue(self.ws.check()["ok"])

    def test_valid_files_in_separate_subfolders_are_rejected(self):
        for kind in ("note", "pdf"):
            with self.subTest(kind=kind):
                pid = self.add(kind)
                meta = self.ws.paper(pid)
                folder = self.root / "20_papers" / pid
                relative = "misplaced/" + (meta["note_path"] if kind == "note" else "source.pdf")
                nested = safe_path(folder, relative)
                nested.parent.mkdir()
                original = folder / (meta["note_path"] if kind == "note" else "source.pdf")
                shutil.copyfile(original, nested)
                if kind == "note":
                    meta["note_path"] = relative
                    expected = "reading note must be close-reading.md beside its PDF"
                else:
                    meta["source"]["versions"][0]["file"] = relative
                    expected = "PDF and reading note must share the paper directory"
                dump(folder / "paper.json", meta)
                result = self.ws.check()
                self.assertFalse(result["ok"])
                self.assertIn(f"{pid}: {expected}", result["errors"])
                self.assertFalse(any("missing" in e for e in result["errors"] if e.startswith(pid + ":")))


    def test_relative_pdf_input_uses_the_selected_workspace_root(self):
        original = make_pdf(self.root / "10_inbox/relative.pdf", "relative")
        digest = sha(original)
        result = self.ws.register("10_inbox/relative.pdf")
        target = self.root / "20_papers" / result["paper_id"] / "source.pdf"
        self.assertEqual(sha(target), digest)
        self.assertFalse(original.exists())
        self.assertTrue(self.ws.check()["ok"])


    def test_doi_duplicate_is_version_not_second_work(self):
        pid = self.add(doi="https://doi.org/10.1234/EXAMPLE")
        other = make_pdf(self.root / "10_inbox/new.pdf", "new")
        with self.assertRaises(WorkspaceError):
            self.ws.register(other, doi="10.1234/example")
        self.assertEqual(self.ws.ids(), [pid])
        self.assertTrue(other.exists())
        self.finish_note(pid)
        self.ws.status(pid, reading="skimmed", verification="reviewed", user_confirmed=True)
        old = self.root / "20_papers/P0001/source.pdf"
        old_hash = sha(old)
        self.ws.add_version(pid, other, "v2", activate=True)
        self.assertEqual(sha(old), old_hash)
        active = self.ws.source(self.ws.paper(pid))
        self.assertEqual(active["file"], "source-v2.pdf")
        self.assertTrue((old.parent / active["file"]).is_file())
        self.assertEqual(self.ws.paper(pid)["verification_status"], "unreviewed")
        self.assertTrue(any("previous PDF" in w for w in self.ws.check()["warnings"]))

    def test_incomplete_reading_and_unconfirmed_human_review_are_rejected(self):
        pid = self.add()
        with self.assertRaises(WorkspaceError):
            self.ws.status(pid, reading="deep_read")
        with self.assertRaises(WorkspaceError):
            self.ws.status(pid, verification="reviewed")
        self.finish_note(pid)
        self.ws.status(pid, reading="deep_read")
        self.assertEqual(self.ws.paper(pid)["verification_status"], "unreviewed")
        self.assertTrue(self.ws.check()["ok"])

    def test_framework_figure_needs_a_local_image_or_a_reader_decision(self):
        pid = self.add()
        for figure in ("", "Generic text", "![remote](https://example.org/figure.png)",
                       "![broken](images/missing.png)", "~~~md\n![example](images/sample.png)\n~~~"):
            with self.subTest(figure=figure):
                self.finish_note(pid, figure)
                with self.assertRaises(WorkspaceError):
                    self.ws.status(pid, reading="deep_read")
        folder = self.ws.papers / pid / "images"
        folder.mkdir()
        (folder / "author figure.png").write_bytes(b"fixture")
        for figure in ("![作者原图](<images/author figure.png>)", "未找到作者框架图，待你决定。",
                       "按你的决定略过框架图。"):
            with self.subTest(figure=figure):
                self.finish_note(pid, figure)
                self.assertEqual(self.ws.note_complete(pid), [])

    def test_overview_order_and_last_section_are_checked(self):
        pid = self.add()
        note = self.finish_note(pid)
        good = note.read_text("utf-8")
        for bad in (good.replace("## 摘要中译", "## temp").replace("## 结论中译", "## 摘要中译").replace("## temp", "## 结论中译"),
                    good + "\n## extra\nExtra\n"):
            note.write_text(bad, "utf-8")
            self.assertTrue(self.ws.note_complete(pid))
        note.write_text(good, "utf-8")
        self.assertEqual(self.ws.note_complete(pid), [])

    def test_broken_note_image_is_detected(self):
        pid = self.add()
        note = self.finish_note(pid)
        with note.open("a", encoding="utf-8") as stream:
            stream.write("\n![Test](images/missing.png)\n")
        result = self.ws.check()
        self.assertFalse(result["ok"])
        self.assertTrue(any("Broken local link" in e for e in result["errors"]))

    def test_note_changes_propagate_to_review_and_idea(self):
        pids = [self.add("one"), self.add("two")]
        for pid in pids:
            self.finish_note(pid)
            self.ws.status(pid, reading="deep_read")
        self.ready_review(pids)
        idea = self.ws.new_artifact("idea", "TEST hypothesis", pids, review_ids=["RQ01"])["id"]
        idea_dir = self.ws.artifact_dir("idea", idea)
        (idea_dir / "idea.md").write_text("# TEST idea\nCompare changes across time intervals.", encoding="utf-8")
        self.ws.stamp("idea", idea)
        review_meta = load(self.ws.artifact_dir("review", "RQ01") / "review.json")
        self.assertFalse(self.ws.stale(review_meta))
        note = self.root / "20_papers/P0001/close-reading.md"
        with note.open("a", encoding="utf-8") as stream:
            stream.write("\nChanged interpretation.\n")
        self.assertTrue(self.ws.stale(review_meta))
        self.assertTrue(self.ws.stale(load(idea_dir / "idea.json")))
        with self.assertRaises(WorkspaceError):
            self.ws.stamp("idea", idea)
        self.ws.stamp("review", "RQ01")
        self.ws.stamp("idea", idea)
        self.assertFalse(self.ws.stale(load(idea_dir / "idea.json")))

    def test_unknown_input_paper_cannot_be_stamped(self):
        pid = self.add()
        self.ws.new_artifact("review", "TEST", [pid], "RQ01")
        folder = self.ws.artifact_dir("review", "RQ01")
        (folder / "review.md").write_text("A finished comparison.", encoding="utf-8")
        meta = load(folder / "review.json")
        meta["paper_ids"] = ["P0999"]
        dump(folder / "review.json", meta)
        with self.assertRaises(WorkspaceError):
            self.ws.stamp("review", "RQ01")
        self.assertFalse(self.ws.check()["ok"])

    def test_original_pdf_tampering_is_detected(self):
        pid = self.add()
        path = self.root / "20_papers" / pid / "source.pdf"
        with path.open("ab") as stream:
            stream.write(b"\nmodified")
        self.assertFalse(self.ws.check()["ok"])

    def test_zotero_sync_preserves_human_notes_and_research_state(self):
        pid = self.add(zotero_key="ABCD1234", rqs=["RQ01"])
        note = self.finish_note(pid)
        before = note.read_bytes()
        self.ws.status(pid, reading="skimmed")
        response = {"ok": True, "data": {"data": {
            "title": "TEST updated title", "date": "2025",
            "DOI": "10.1234/test", "creators": [{"creatorType": "author", "firstName": "A", "lastName": "Test"}]}}}
        with patch.object(self.ws, "zotero", return_value=response):
            self.ws.sync_zotero(pid)
        self.assertEqual(note.read_bytes(), before)
        meta = self.ws.paper(pid)
        self.assertEqual(meta["title"], "TEST updated title")
        self.assertEqual(meta["reading_status"], "skimmed")
        self.assertEqual(meta["research_questions"], ["RQ01"])
        self.assertEqual(meta["metadata_origin"], "zotero")
        self.assertFalse((self.root / "90_system/cache").exists())

    def test_cli_errors_and_prose_are_not_treated_as_metadata(self):
        pid = self.add(zotero_key="ABCD1234")
        before = (self.root / "20_papers" / pid / "paper.json").read_bytes()
        with patch.object(self.ws, "zotero", return_value={"ok": True, "data": {"text": "A prose summary"}}):
            with self.assertRaises(WorkspaceError):
                self.ws.sync_zotero(pid)
        self.assertEqual((self.root / "20_papers" / pid / "paper.json").read_bytes(), before)
        failure = subprocess.CompletedProcess([], 1, '{"ok":false,"error":{"message":"not connected"}}', "")
        with patch.object(self.ws, "cli_path", return_value="zotero-cli"), patch("literature.subprocess.run", return_value=failure):
            with self.assertRaisesRegex(WorkspaceError, "not connected"):
                self.ws.zotero(["get", "collections", "--limit", "1"])

    def test_ids_are_not_reused_and_relative_paths_cannot_escape(self):
        self.add()
        with (self.root / "00_project/registrations.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"paper_id": "P0099", "registered_at": "TEST"}) + "\n")
        self.assertEqual(self.add("next"), "P0100")
        with self.assertRaises(WorkspaceError):
            safe_path(self.root / "20_papers", "../outside")

    def test_partial_environment_is_not_selected(self):
        cli = self.root / ".venv/Scripts/zotero-cli.exe"
        ready = self.root / ".venv/.workspace-ready"
        original = Path.is_file
        state = {"ready": False}
        def exists(path):
            if path == cli:
                return True
            if path == ready:
                return state["ready"]
            return original(path)
        with patch.object(Path, "is_file", exists), patch("literature.shutil.which", return_value=None):
            self.assertIsNone(self.ws.cli_path())
            state["ready"] = True
            self.assertEqual(self.ws.cli_path(), str(cli))

    def test_extracted_text_has_physical_page_and_source_version(self):
        pid = self.add()
        result = self.ws.extract(pid)
        self.assertEqual(result["pages"], 1)
        text = Path(result["output"]).read_text(encoding="utf-8")
        self.assertIn("PDF_PAGE:1", text)
        self.assertIn("TEST FIXTURE", text)
        manifest = load(self.root / ".tmp/extract" / pid / "manifest.json")
        self.assertEqual(manifest["source_sha256"], self.ws.source(self.ws.paper(pid))["sha256"])
        self.assertFalse((self.root / "20_papers" / pid / "extracted").exists())

    def test_cleanup_preserves_archives_other_papers_and_allows_resume(self):
        first, second = self.add("first"), self.add("second")
        note = self.finish_note(first)
        archive = self.root / "20_papers" / first
        before = {file.name: file.read_bytes() for file in archive.iterdir() if file.is_file()}
        self.ws.extract(first)
        other_output = Path(self.ws.extract(second)["output"])
        self.assertEqual(set(self.ws.clean(first)["removed"]), {"fulltext.md", "manifest.json"})
        self.assertEqual(before, {file.name: file.read_bytes() for file in archive.iterdir() if file.is_file()})
        self.assertTrue(other_output.is_file())
        self.assertEqual(self.ws.clean(first)["removed"], [])
        self.assertTrue(Path(self.ws.extract(first)["output"]).is_file())
        self.assertTrue(note.is_file())
        self.ws.clean(first)
        self.ws.clean(second)
        self.assertFalse((self.root / ".tmp").exists())

    def test_cleanup_retains_unknown_content_without_partial_deletion(self):
        pid = self.add()
        output = Path(self.ws.extract(pid)["output"])
        personal = output.parent / "personal.md"
        personal.write_text("Keep this note", encoding="utf-8")
        with self.assertRaisesRegex(WorkspaceError, "Unknown extraction content"):
            self.ws.clean(pid)
        self.assertTrue(output.is_file())
        self.assertEqual(personal.read_text(encoding="utf-8"), "Keep this note")
        with self.assertRaises(WorkspaceError):
            self.ws.clean("../20_papers")

    def test_temporary_paths_reject_junctions_even_within_workspace(self):
        original = Path.is_junction
        linked = self.root / ".tmp"
        with patch.object(Path, "is_junction", lambda path: path == linked or original(path)):
            with self.assertRaisesRegex(WorkspaceError, "links or junctions"):
                temporary_path(self.root, "extract/P0001/fulltext.md")
        with self.assertRaises(WorkspaceError):
            temporary_path(self.root, "../20_papers/P0001")

    def test_check_detects_missing_sources_and_temporary_document_links(self):
        pid = self.add()
        output = Path(self.ws.extract(pid)["output"])
        note = self.finish_note(pid)
        with note.open("a", encoding="utf-8") as stream:
            stream.write(f"\n[Bad temporary link](../../.tmp/extract/{pid}/fulltext.md)\n")
        errors = self.ws.check()["errors"]
        self.assertTrue(any("must not reference temporary" in error for error in errors))
        self.assertTrue(output.is_file())
        (self.root / "00_project/sources.md").unlink()
        self.assertTrue(any("source list is missing" in error for error in self.ws.check()["errors"]))


    def copy_primary_skill(self):
        primary = load(ROOT / "90_system/workspace.json")["primary_skill"]
        source = safe_path(ROOT, primary["path"])
        target = self.root / "90_system/skills/literature-workflow"
        shutil.copytree(source, target)
        config = load(self.root / "90_system/workspace.json")
        config["primary_skill"]["path"] = target.relative_to(self.root).as_posix()
        config["primary_skill"]["native_path"] = "installed-skills/literature-workflow"
        dump(self.root / "90_system/workspace.json", config)
        return target

    def test_primary_skill_integrity_detects_tampering(self):
        bundle = self.copy_primary_skill()
        self.assertEqual(skill_package_errors(bundle), [])
        with (bundle / "references/reading.md").open("a", encoding="utf-8") as stream:
            stream.write("\nChanged method.\n")
        self.assertTrue(any("Missing/changed" in e for e in skill_package_errors(bundle)))

    def test_primary_skill_rejects_unlisted_files_and_manifest_escape(self):
        bundle = self.copy_primary_skill()
        (bundle / "extra-report.md").write_text("Unlisted process report", encoding="utf-8")
        self.assertTrue(skill_package_errors(bundle))
        manifest = load(bundle / "manifest.json")
        manifest["files"][0]["path"] = "../outside.txt"
        dump(bundle / "manifest.json", manifest)
        self.assertTrue(any("escapes" in e for e in skill_package_errors(bundle)))

    def test_primary_skill_install_moves_bundle_and_is_repeatable(self):
        source = self.copy_primary_skill()
        with patch.object(setup_project, "ROOT", self.root):
            first = setup_project.install_primary_skill()
            second = setup_project.install_primary_skill()
        target = self.root / "installed-skills/literature-workflow"
        self.assertFalse(source.exists())
        self.assertEqual(skill_package_errors(target), [])
        self.assertEqual(load(self.root / "90_system/workspace.json")["primary_skill"]["path"],
                         "installed-skills/literature-workflow")
        self.assertEqual(first["native_skill"], second["native_skill"])
        self.assertTrue(first["updated"])
        self.assertFalse(second["updated"])

    def change_skill(self, bundle, suffix="\nUpdated project instructions.\n"):
        document = bundle / "SKILL.md"
        document.write_text(document.read_text(encoding="utf-8") + suffix, encoding="utf-8")
        manifest = load(bundle / "manifest.json")
        for item in manifest["files"]:
            item["sha256"] = sha(bundle / item["path"])
        dump(bundle / "manifest.json", manifest)

    def test_primary_skill_update_replaces_verified_native_and_removes_fallback(self):
        source = self.copy_primary_skill()
        target = self.root / "installed-skills/literature-workflow"
        shutil.copytree(source, target)
        self.change_skill(source)
        expected = sha(source / "SKILL.md")
        with patch.object(setup_project, "ROOT", self.root):
            result = setup_project.install_primary_skill()
        self.assertTrue(result["updated"])
        self.assertEqual(sha(target / "SKILL.md"), expected)
        self.assertFalse(source.exists())
        self.assertEqual(skill_package_errors(target), [])
        self.assertEqual(load(self.root / "90_system/workspace.json")["primary_skill"]["path"],
                         "installed-skills/literature-workflow")
        self.assertEqual(list((self.root / ".tmp").iterdir()), [])

    def test_primary_skill_update_preserves_unverified_native_content(self):
        source = self.copy_primary_skill()
        target = self.root / "installed-skills/literature-workflow"
        shutil.copytree(source, target)
        personal = target / "personal-note.md"
        personal.write_text("Personal research record", encoding="utf-8")
        config_before = (self.root / "90_system/workspace.json").read_bytes()
        with patch.object(setup_project, "ROOT", self.root):
            with self.assertRaisesRegex(RuntimeError, "unverified"):
                setup_project.install_primary_skill()
        self.assertTrue(source.is_dir())
        self.assertEqual(personal.read_text(encoding="utf-8"), "Personal research record")
        self.assertEqual((self.root / "90_system/workspace.json").read_bytes(), config_before)

    def test_primary_skill_update_rolls_back_when_config_save_fails(self):
        source = self.copy_primary_skill()
        target = self.root / "installed-skills/literature-workflow"
        shutil.copytree(source, target)
        old_hash = sha(target / "SKILL.md")
        self.change_skill(source)
        new_hash = sha(source / "SKILL.md")
        config_before = (self.root / "90_system/workspace.json").read_bytes()
        with patch.object(setup_project, "ROOT", self.root), \
             patch("setup_project.dump", side_effect=OSError("fixture write failure")):
            with self.assertRaisesRegex(OSError, "fixture write failure"):
                setup_project.install_primary_skill()
        self.assertEqual(sha(source / "SKILL.md"), new_hash)
        self.assertEqual(sha(target / "SKILL.md"), old_hash)
        self.assertEqual((self.root / "90_system/workspace.json").read_bytes(), config_before)
        self.assertEqual(list((self.root / ".tmp").iterdir()), [])

    def test_primary_skill_links_are_checked_after_manifest_refresh(self):
        bundle = self.copy_primary_skill()
        self.change_skill(bundle, "\n[Missing resource](references/missing.md)\n")
        errors = skill_package_errors(bundle)
        self.assertTrue(any("Broken/outside skill link" in error for error in errors))
        self.assertFalse(any("Missing/changed skill resource" in error for error in errors))

    def test_primary_skill_retains_recovery_copy_when_rollback_is_blocked(self):
        source = self.copy_primary_skill()
        target = self.root / "installed-skills/literature-workflow"
        shutil.copytree(source, target)
        old_hash = sha(target / "SKILL.md")
        self.change_skill(source)
        new_hash = sha(source / "SKILL.md")
        original_replace = Path.replace
        def move(path, destination):
            if path == target and destination == source:
                raise OSError("fixture rollback blocked")
            return original_replace(path, destination)
        with patch.object(setup_project, "ROOT", self.root), \
             patch.object(Path, "replace", move), \
             patch("setup_project.dump", side_effect=OSError("fixture write failure")):
            with self.assertRaisesRegex(RuntimeError, "files retained"):
                setup_project.install_primary_skill()
        copies = list((self.root / ".tmp").glob("skill-update-*/previous/SKILL.md"))
        self.assertEqual(len(copies), 1)
        self.assertEqual(sha(copies[0]), old_hash)
        self.assertEqual(sha(target / "SKILL.md"), new_hash)

    def test_environment_failure_cleans_build_files_and_restores_temp(self):
        import os
        requirements = self.root / "90_system/environment/requirements.in"
        requirements.parent.mkdir(parents=True)
        shutil.copyfile(ROOT / "90_system/environment/requirements.in", requirements)
        executable = self.root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        executable.parent.mkdir(parents=True)
        executable.touch()
        original = {key: os.environ.get(key) for key in ("TEMP", "TMP")}
        with patch.object(setup_project, "ROOT", self.root), \
             patch("setup_project.subprocess.run", return_value=subprocess.CompletedProcess([], 0)), \
             patch("setup_project.run", side_effect=RuntimeError("fixture install failure")):
            with self.assertRaisesRegex(RuntimeError, "fixture install failure"):
                setup_project.environment_setup()
        self.assertEqual({key: os.environ.get(key) for key in original}, original)
        self.assertEqual(list((self.root / ".tmp").iterdir()), [])

    def test_doctor_reports_native_skill_installation_pending(self):
        self.copy_primary_skill()
        health = Workspace(self.root).doctor()
        checks = {c["name"]: c for c in health["checks"]}
        self.assertTrue(checks["primary_reading_skill"]["ok"])
        self.assertFalse(checks["native_skill_installation"]["ok"])
        self.assertFalse(checks["single_native_skill_entry"]["ok"])
        self.assertFalse(health["ok"])


    def test_doctor_reports_pending_update_when_older_native_exists(self):
        source = self.copy_primary_skill()
        native = self.root / "installed-skills/literature-workflow"
        shutil.copytree(source, native)
        self.change_skill(source)
        health = Workspace(self.root).doctor()
        checks = {check["name"]: check for check in health["checks"]}
        self.assertTrue(checks["primary_reading_skill"]["ok"])
        self.assertTrue(checks["single_native_skill_entry"]["ok"])
        self.assertFalse(checks["native_skill_installation"]["ok"])
        self.assertTrue(any("pending" in message for message in checks["native_skill_installation"]["details"]))

if __name__ == "__main__":
    unittest.main()

