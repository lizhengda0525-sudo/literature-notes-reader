"""Literature workspace CLI. Core commands use the Python standard library."""
from __future__ import annotations
import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import unquote
import re
import shutil
import subprocess
import sys
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from paper_resources import validate_resource_catalog

READING = ("queued", "skimmed", "deep_read", "skipped")
VERIFICATION = ("unreviewed", "partial", "reviewed")
IDEA_STAGES = ("candidate", "needs_search", "needs_experiment", "supported", "paused", "dropped")
FIGURE_SECTION = "主方法框架图"
ORIGINAL_DISCLOSURE = re.compile(
    r"(?P<translation>.*?)\n<details>\s*<summary>英文原文</summary>\s*(?P<original>.*?)\s*</details>",
    re.DOTALL)
NOTE_LINK = re.compile(r'(!?)\[[^\]\n]*\]\(\s*(?:<([^>\n]+)>|([^\s)]+))(?:\s+"[^"\n]*")?\s*\)')
PLACEHOLDER = re.compile(r"待填写|待补充|TODO|\{\{[^}]+\}\}", re.I)

class WorkspaceError(Exception):
    pass

def now():
    try:
        tz = ZoneInfo("Asia/Shanghai")
    except ZoneInfoNotFoundError:
        tz = timezone(timedelta(hours=8))
    return datetime.now(tz).isoformat(timespec="seconds")

def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()

def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))

def dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)

def safe_path(base, relative):
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise WorkspaceError("Invalid relative path.")
    part = Path(relative)
    if part.is_absolute() or ".." in part.parts or ":" in relative:
        raise WorkspaceError("Path escapes its directory.")
    result = (base / part).resolve()
    if not result.is_relative_to(base.resolve()):
        raise WorkspaceError("Path escapes its directory.")
    return result

def table_cell(value):
    return " ".join(value.splitlines()).replace("\\", "\\\\").replace("|", r"\|")

def normalize_doi(value):
    value = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:\s*)", "", (value or "").strip(), flags=re.I)
    if value and not re.fullmatch(r"10\.\d{4,9}/\S+", value, re.I):
        raise WorkspaceError("DOI must have the form 10.xxxx/xxxx.")
    return value.lower()


def temporary_path(root, relative=""):
    """Resolve scratch space without following symlinks or Windows junctions."""
    root = Path(root).resolve()
    resolved = safe_path(root, ".tmp/" + relative)
    candidate = root / ".tmp" / relative
    for part in (candidate, *candidate.parents):
        if part == root:
            break
        if part.is_symlink() or part.is_junction():
            raise WorkspaceError("Temporary paths must not contain links or junctions.")
    return resolved


def skill_package_errors(folder):
    """Check the primary skill bundle before using or moving it."""
    folder = Path(folder).resolve()
    errors = []
    try:
        manifest = load(folder / "manifest.json")
        if manifest.get("schema_version") != 1 or manifest.get("skill") != "literature-workflow":
            return ["Invalid primary skill manifest."]
        entries = manifest["files"]
        names = [item["path"] for item in entries]
        required = {"SKILL.md", "agents/openai.yaml", "sources.json",
                    "licenses/Delores-MIT.txt", "licenses/K-Dense-MIT.txt",
                    "references/reading.md", "references/appraisal.md",
                    "references/search-and-citations.md", "references/synthesis-and-ideas.md",
                    "references/maintenance.md", "references/commands.md"}
        if len(names) != len(set(names)) or not required.issubset(names):
            errors.append("Incomplete or duplicate skill manifest entries.")
        for item in entries:
            path = safe_path(folder, item["path"])
            if (folder / item["path"]).is_symlink() or not path.is_file() or sha(path) != item["sha256"]:
                errors.append(f"Missing/changed skill resource: {item['path']}")
        actual = {p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()}
        if actual != set(names) | {"manifest.json"}:
            errors.append("Unexpected or missing skill bundle files.")
        text = (folder / "SKILL.md").read_text(encoding="utf-8")
        if not re.match(r"\A---\r?\nname: literature-workflow\r?\ndescription: .+?\r?\n---", text):
            errors.append("Invalid primary skill metadata.")
        provenance = load(folder / "sources.json")
        if len(provenance.get("derived_from", [])) != 5:
            errors.append("Missing method provenance.")
        for source in provenance.get("derived_from", []):
            if not safe_path(folder, source["license"]).is_file():
                errors.append(f"Missing source license: {source['name']}")
        for document in folder.rglob("*.md"):
            for target in re.findall(r"\[[^\]]*\]\(([^\s)]+)\)", document.read_text(encoding="utf-8")):
                if target.startswith(("#", "http://", "https://", "mailto:")):
                    continue
                relative = target.split("#", 1)[0]
                resource = (document.parent / relative).resolve()
                if not resource.is_relative_to(folder) or not resource.is_file():
                    errors.append(f"Broken/outside skill link: {document.name}: {target}")
    except (OSError, ValueError, KeyError, TypeError, WorkspaceError) as error:
        errors.append(str(error))
    return errors

class Workspace:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.config = load(self.root / "90_system/workspace.json")
        self.papers = self.root / "20_papers"
        self.project = self.root / "00_project"
        self.templates = self.root / "90_system/templates"

    def ids(self):
        return sorted(p.name for p in self.papers.glob("P*")
                      if p.is_dir() and re.fullmatch(r"P\d{4,}", p.name))

    def paper(self, pid):
        if not re.fullmatch(r"P\d{4,}", pid):
            raise WorkspaceError("Invalid paper ID.")
        path = self.papers / pid / "paper.json"
        if not path.is_file():
            raise WorkspaceError(f"Paper not found: {pid}")
        return load(path)

    def source(self, meta):
        versions = [v for v in meta["source"]["versions"]
                    if v["version"] == meta["source"]["current_version"]]
        if len(versions) != 1:
            raise WorkspaceError("Exactly one active PDF version is required.")
        return versions[0]

    def question_titles(self):
        text = (self.project / "questions.md").read_text(encoding="utf-8-sig")
        return {key: title.strip() for key, title in re.findall(r"^## (RQ\d+)\b([^\n]*)", text, re.M)}

    def questions(self):
        return set(self.question_titles())

    def render(self, name, values):
        text = (self.templates / name).read_text(encoding="utf-8")
        return re.sub(r"\{\{(\w+)\}\}", lambda m: str(values.get(m[1], m[0])), text)

    def ledger(self):
        path = self.project / "registrations.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()] if path.exists() else []

    @contextmanager
    def registration_lock(self):
        path = self.project / "_registration.lock"
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        except FileExistsError as error:
            raise WorkspaceError("A registration is active, or its lock needs inspection.") from error
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(json.dumps({"pid": os.getpid(), "at": now()}))
            yield
        finally:
            if path.resolve().parent == self.project.resolve():
                path.unlink(missing_ok=True)

    def pdf(self, filename):
        path = Path(filename).expanduser()
        if not path.is_absolute():
            path = self.root / path
        path = path.resolve()
        if not path.is_file():
            raise WorkspaceError(f"PDF not found: {path}")
        with path.open("rb") as stream:
            if b"%PDF-" not in stream.read(1024):
                raise WorkspaceError("File has no PDF header.")
        return path

    def consume_inbox(self, original, target, digest, keep):
        if not keep and original.is_relative_to((self.root / "10_inbox").resolve()) and original != target:
            if not target.resolve().is_relative_to(self.papers.resolve()):
                raise WorkspaceError("Destination is outside the paper directory.")
            if sha(original) != digest or sha(target) != digest:
                raise WorkspaceError("PDF changed during registration; inbox file retained.")
            original.unlink()

    def register(self, filename, title=None, doi=None, zotero_key=None, rqs=(), keep=False):
        original = self.pdf(filename)
        digest, doi = sha(original), normalize_doi(doi)
        rqs = list(dict.fromkeys(rqs))
        if set(rqs) - self.questions():
            raise WorkspaceError("Unknown research question.")
        if zotero_key and not re.fullmatch(r"[A-Z0-9]{8}", zotero_key):
            raise WorkspaceError("Zotero item key must have 8 uppercase letters/digits.")
        with self.registration_lock():
            for pid in self.ids():
                meta = self.paper(pid)
                if any(v["sha256"] == digest for v in meta["source"]["versions"]):
                    return {"paper_id": pid, "created": False, "reason": "same_pdf"}
                if (doi and meta.get("doi") == doi) or (zotero_key and meta.get("zotero_item_key") == zotero_key):
                    raise WorkspaceError(f"Work is already {pid}; use add-version for a different PDF.")
            used = self.ids() + [r["paper_id"] for r in self.ledger()]
            pid = f"P{max([int(p[1:]) for p in used] + [0]) + 1:04d}"
            folder = self.papers / pid
            folder.mkdir(exist_ok=False)
            target = folder / "source.pdf"
            shutil.copyfile(original, target)
            if sha(target) != digest:
                raise WorkspaceError("Copied PDF checksum mismatch.")
            date = now()
            meta = {
                "schema_version": 1, "paper_id": pid, "title": title or original.stem,
                "authors": [], "year": None, "doi": doi or None, "arxiv_id": None, "source_url": None,
                "metadata_origin": "manual", "zotero_item_key": zotero_key,
                "zotero_attachment_key": None, "zotero_synced_at": None,
                "reading_status": "queued", "verification_status": "unreviewed", "priority": "medium",
                "research_questions": rqs, "tags": [], "note_path": "close-reading.md",
                "note_source_sha256": None,
                "source": {"current_version": "v1", "versions": [
                    {"version": "v1", "file": "source.pdf", "sha256": digest, "added_at": date}]},
                "added_at": date, "updated_at": date, "history": []
            }
            if "关键词" in self.config["required_note_sections"]:
                meta["note_format_version"] = 2
            note = safe_path(folder, meta["note_path"])
            note.parent.mkdir(parents=True, exist_ok=True)
            note.write_text(self.render("paper_note.md", {
                "paper_id": pid, "title": table_cell(meta["title"]), "doi": table_cell(doi) or "待填写",
            }), encoding="utf-8")
            dump(folder / "paper.json", meta)
            record = {"paper_id": pid, "registered_at": date, "initial_pdf_sha256": digest,
                      "directory": f"20_papers/{pid}"}
            with (self.project / "registrations.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            self.consume_inbox(original, target, digest, keep)
        self.index()
        return {"paper_id": pid, "created": True, "directory": str(folder)}

    def add_version(self, pid, filename, label, activate=False, keep=False):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,31}", label):
            raise WorkspaceError("Invalid version label.")
        original = self.pdf(filename)
        digest = sha(original)
        with self.registration_lock():
            meta = self.paper(pid)
            versions = meta["source"]["versions"]
            if any(v["version"] == label or v["sha256"] == digest for v in versions):
                raise WorkspaceError("Version label or PDF already exists.")
            folder = self.papers / pid
            target = safe_path(folder, f"source-{label}.pdf")
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                raise WorkspaceError("Target already exists.")
            shutil.copyfile(original, target)
            if sha(target) != digest:
                raise WorkspaceError("Copied PDF checksum mismatch.")
            versions.append({"version": label, "file": target.relative_to(folder).as_posix(),
                             "sha256": digest, "added_at": now()})
            if activate:
                meta["source"]["current_version"] = label
                meta["verification_status"] = "unreviewed"
            meta["updated_at"] = now()
            meta["history"].append({"at": now(), "action": "add_version", "version": label,
                                    "activated": activate})
            dump(folder / "paper.json", meta)
            self.consume_inbox(original, target, digest, keep)
        self.index()
        return {"paper_id": pid, "version": label, "activated": activate}

    def fingerprint(self, pid):
        meta = self.paper(pid)
        folder, active = self.papers / pid, self.source(meta)
        return {"metadata_sha256": sha(folder / "paper.json"),
                "note_sha256": sha(safe_path(folder, meta["note_path"])),
                "source_version": active["version"], "source_sha256": active["sha256"]}

    def note_complete(self, pid):
        folder = self.papers / pid
        meta = self.paper(pid)
        path = safe_path(folder, meta["note_path"])
        text = path.read_text(encoding="utf-8-sig")
        errors = []
        # Personal notes are free-form, including headings and unfinished questions.
        text = re.sub(r"<!-- USER_NOTES_START -->.*?<!-- USER_NOTES_END -->", "", text, flags=re.S)
        sections, headings = {}, []
        fence = ""
        for line in text.splitlines():
            marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
            if marker:
                if not fence:
                    fence = marker[1]
                    if headings:
                        sections[headings[-1]].append("[代码]")
                elif marker[1].startswith(fence) and not marker[2].strip():
                    fence = ""
                continue
            if fence:
                continue
            if line.startswith("## "):
                name = line[3:].strip()
                headings.append(name)
                sections[name] = []
            elif headings:
                sections[headings[-1]].append(line)
        required = [s for s in self.config["required_note_sections"] if s != "原文证据"]
        # An existing note keeps its original layout until it is actually migrated.
        old_titles = {"摘要": "摘要中译", "结论": "结论中译"}
        if ("关键词" in required and not meta.get("note_format_version")
                and set(old_titles.values()).issubset(headings)
                and {"摘要", "结论", "关键词"}.isdisjoint(headings)):
            required = [old_titles.get(s, s) for s in required if s != "关键词"]
        bilingual = "关键词" in required and "摘要" in required and "结论" in required
        for name in required:
            body = "\n".join(sections.get(name, [])).strip()
            if not body or PLACEHOLDER.search(body):
                errors.append(f"未完成章节「{name}」")
            elif bilingual and name in old_titles:
                absent = ("原文未提供摘要。" if name == "摘要" else "原文未提供结论或讨论总结段。")
                if body == absent or f"原文未提供英文{name}。" in body:
                    continue
                pair = ORIGINAL_DISCLOSURE.fullmatch(body)
                if (not pair or not re.search(r"[\u3400-\u9fff]", pair["translation"])
                        or not re.search(r"[A-Za-z]", pair["original"])):
                    errors.append(f"「{name}」请先写中文，再用“英文原文”折叠区保留对应原文。")
            elif bilingual and name == "关键词":
                if not re.search(r"[A-Za-z]", body) and "原文未提供英文关键词。" not in body:
                    errors.append("关键词请照录作者英文，缺少时写“原文未提供英文关键词。”")
        if FIGURE_SECTION in required:
            ordered = headings[:len(required) - 1] == required[:-1] and headings[-1:] == required[-1:]
            if not ordered or any(headings.count(name) > 1 for name in required):
                errors.append("笔记章节顺序应为" + " → ".join(required))
            figure = "\n".join(sections.get(FIGURE_SECTION, []))
            paths = [unquote(quoted or plain) for image, quoted, plain in NOTE_LINK.findall(figure) if image]
            has_figure = any(re.fullmatch(r"images/[^/\\:?#]+\.(?:png|jpe?g|webp)", p, re.I)
                             and safe_path(folder, p).is_file() for p in paths)
            decision = re.search(r"(?:你|您|用户|读者)(?:的|已|来|自行|另行)?(?:决定|确认|选择)", figure)
            if not has_figure and not decision:
                errors.append("主方法框架图需嵌入本篇作者原图，缺图时请说明情况并标记“待你决定”。")
        errors.extend(self.link_errors(path, require_inventory=bilingual))
        return errors

    def status(self, pid, reading=None, verification=None, user_confirmed=False):
        meta = self.paper(pid)
        if reading == "deep_read":
            errors = self.note_complete(pid)
            if errors:
                raise WorkspaceError("; ".join(errors))
        if verification == "reviewed" and not user_confirmed:
            raise WorkspaceError("Human review requires explicit user confirmation and --user-confirmed.")
        before = {"reading": meta["reading_status"], "verification": meta["verification_status"]}
        if reading:
            meta["reading_status"] = reading
            if reading in ("skimmed", "deep_read"):
                meta["note_source_sha256"] = self.source(meta)["sha256"]
        if verification:
            meta["verification_status"] = verification
        meta["updated_at"] = now()
        meta["history"].append({"at": now(), "action": "status", "before": before,
                               "reading": meta["reading_status"], "verification": meta["verification_status"]})
        dump(self.papers / pid / "paper.json", meta)
        self.index()
        return {"paper_id": pid, "reading": meta["reading_status"], "verification": meta["verification_status"]}


    def artifact_dir(self, kind, identifier):
        pattern = r"RQ\d+" if kind == "review" else r"I\d{4,}"
        if not re.fullmatch(pattern, identifier):
            raise WorkspaceError("Invalid artifact ID.")
        return self.root / ("30_reviews" if kind == "review" else "40_ideas") / identifier

    def artifacts(self):
        result = []
        for kind, directory in (("review", "30_reviews"), ("idea", "40_ideas")):
            for path in sorted((self.root / directory).glob(f"*/{kind}.json")):
                result.append((kind, path.parent, load(path)))
        return result

    def snapshot(self, paper_ids, review_ids=()):
        papers = {pid: self.fingerprint(pid) for pid in sorted(set(paper_ids))}
        reviews = {}
        for rid in sorted(set(review_ids)):
            folder = self.artifact_dir("review", rid)
            reviews[rid] = {"manifest_sha256": sha(folder / "review.json"),
                            "content_sha256": sha(folder / "review.md")}
        return {"papers": papers, "reviews": reviews}

    def stale(self, meta):
        old = meta.get("input_snapshot")
        if old is None:
            return ["not_stamped"]
        try:
            current = self.snapshot(meta["paper_ids"], meta.get("review_ids", []))
        except (WorkspaceError, OSError, KeyError) as error:
            return [str(error)]
        reasons = []
        for kind in ("papers", "reviews"):
            for key in sorted(set(old.get(kind, {})) | set(current[kind])):
                if old.get(kind, {}).get(key) != current[kind].get(key):
                    reasons.append(f"{kind}:{key}")
        if meta["kind"] == "idea":
            for rid in meta.get("review_ids", []):
                review = load(self.artifact_dir("review", rid) / "review.json")
                if self.stale(review):
                    reasons.append(f"review_inputs:{rid}")
        return reasons

    def new_artifact(self, kind, title, paper_ids, question=None, review_ids=()):
        self.snapshot(paper_ids, review_ids)
        if kind == "review":
            if question not in self.questions():
                raise WorkspaceError("A review requires an existing research question.")
            identifier = question
        else:
            used = [int(p.name[1:]) for p in (self.root / "40_ideas").glob("I*")
                    if re.fullmatch(r"I\d{4,}", p.name)]
            identifier = f"I{max(used + [0]) + 1:04d}"
        folder = self.artifact_dir(kind, identifier)
        if folder.exists():
            raise WorkspaceError("Artifact already exists; edit its existing files.")
        folder.mkdir()
        meta = {"schema_version": 1, "id": identifier, "kind": kind, "title": title,
                "status": "draft" if kind == "review" else "candidate",
                "paper_ids": sorted(set(paper_ids)), "review_ids": sorted(set(review_ids)),
                "input_snapshot": None, "created_at": now(), "updated_at": now()}
        dump(folder / f"{kind}.json", meta)
        (folder / f"{kind}.md").write_text(self.render(f"{kind}.md", {
            "artifact_id": identifier, "title": title}), encoding="utf-8")
        self.index()
        return {"kind": kind, "id": identifier, "directory": str(folder)}

    def stamp(self, kind, identifier, status=None):
        folder = self.artifact_dir(kind, identifier)
        meta = load(folder / f"{kind}.json")
        text = (folder / f"{kind}.md").read_text(encoding="utf-8")
        if not text.strip() or PLACEHOLDER.search(text):
            raise WorkspaceError("Artifact still contains unfinished template fields.")
        if not meta["paper_ids"] and not meta.get("review_ids"):
            raise WorkspaceError("Declare the inputs used for this artifact.")
        for rid in meta.get("review_ids", []):
            if self.stale(load(self.artifact_dir("review", rid) / "review.json")):
                raise WorkspaceError(f"Update and stamp review {rid} first.")
        allowed = ("draft", "ready", "archived") if kind == "review" else IDEA_STAGES
        if status and status not in allowed:
            raise WorkspaceError("Unsupported artifact status.")
        meta["input_snapshot"] = self.snapshot(meta["paper_ids"], meta.get("review_ids", []))
        meta["status"] = status or ("ready" if kind == "review" else "needs_search")
        meta["updated_at"] = now()
        dump(folder / f"{kind}.json", meta)
        self.index()
        return {"id": identifier, "status": meta["status"], "stale": False}

    def index(self):
        papers, artifacts = [], []
        for pid in self.ids():
            meta = self.paper(pid)
            active = self.source(meta)
            papers.append({"paper_id": pid, "title": meta["title"], "doi": meta.get("doi"),
                           "reading_status": meta["reading_status"],
                           "verification_status": meta["verification_status"],
                           "research_questions": meta["research_questions"],
                           "note_path": f"20_papers/{pid}/{meta['note_path']}",
                           "source_path": f"20_papers/{pid}/{active['file']}",
                           "needs_reading_update": meta.get("note_source_sha256") not in (None, active["sha256"])})
        for kind, folder, meta in self.artifacts():
            artifacts.append({"kind": kind, "id": meta["id"], "title": meta["title"],
                              "status": meta["status"], "stale_reasons": self.stale(meta),
                              "path": (folder / f"{kind}.md").relative_to(self.root).as_posix()})
        dump(self.root / "90_system/index/catalog.json",
             {"schema_version": 1, "generated_at": now(), "papers": papers, "artifacts": artifacts})
        lines = ["# 文献与成果目录", "", "自动生成；编辑 paper.json、阅读记录或专题后运行 index。", "",
                 f"论文：{len(papers)}；专题：{sum(a['kind']=='review' for a in artifacts)}；想法：{sum(a['kind']=='idea' for a in artifacts)}。",
                 "", "| 编号 | 题名 | 阅读状态 | 人工核对 | 研究问题 |", "|---|---|---|---|---|"]
        for row in papers:
            title = table_cell(row["title"])
            lines.append(f"| [{row['paper_id']}](../{row['note_path']}) | {title} | "
                         f"{row['reading_status']} | {row['verification_status']} | {', '.join(row['research_questions'])} |")
        if not papers:
            lines += ["", "尚未登记论文。把 PDF 放入 10_inbox 后进行登记。"]
        lines += ["", "| 成果 | 标题 | 状态 | 更新检查 |", "|---|---|---|---|"]
        for row in artifacts:
            title = table_cell(row["title"])
            freshness = "需更新" if row["stale_reasons"] else "输入未变化"
            lines.append(f"| [{row['id']}](../{row['path']}) | {title} | {row['status']} | {freshness} |")
        (self.project / "catalog.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        return {"papers": len(papers), "artifacts": len(artifacts)}

    def link_errors(self, path, require_inventory=False):
        errors = []
        resource_ids = set()
        for _, quoted, plain in NOTE_LINK.findall(path.read_text(encoding="utf-8-sig")):
            target = quoted or plain
            if target.startswith("#res-"):
                resource_ids.add(target[5:])
            if target.startswith(("#", "http://", "https://", "mailto:", "data:")):
                continue
            target = unquote(target.split("#", 1)[0])
            if not target:
                continue
            resolved = (path.parent / target).resolve()
            if not resolved.is_relative_to(self.root):
                errors.append(f"Local link leaves the workspace: {target}")
            elif resolved.is_relative_to(self.root / ".tmp"):
                errors.append(f"Permanent records must not reference temporary files: {target}")
            elif not resolved.exists():
                errors.append(f"Broken local link: {target}")
        if path.name == "close-reading.md" and path.parent.parent == self.papers:
            errors.extend(validate_resource_catalog(path.parent, resource_ids, require_inventory))
        return errors

    def check(self):
        errors, warnings, seen_hashes, seen_dois = [], [], {}, {}
        try:
            source_list = safe_path(self.root, self.config.get("source_list", "00_project/sources.md"))
            if not source_list.is_file():
                errors.append("The configured reading source list is missing.")
            for document in [self.root / "README.md", self.root / "AGENTS.md",
                             *self.project.glob("*.md")]:
                if document.is_file():
                    errors.extend(f"{document.name}: {message}" for message in self.link_errors(document))
        except (OSError, ValueError, TypeError, WorkspaceError) as error:
            errors.append(f"Project document check: {error}")
        registered = [r["paper_id"] for r in self.ledger()]
        if len(set(registered)) != len(registered):
            errors.append("Duplicate registration IDs.")
        for pid in self.ids():
            try:
                meta = self.paper(pid)
                if meta["paper_id"] != pid or meta["schema_version"] != 1:
                    raise WorkspaceError("Metadata identity/schema mismatch.")
                if meta["reading_status"] not in READING or meta["verification_status"] not in VERIFICATION:
                    raise WorkspaceError("Invalid reading/verification status.")
                if meta["metadata_origin"] not in ("manual", "zotero"):
                    raise WorkspaceError("Invalid metadata origin.")
                if not isinstance(meta["title"], str) or not meta["title"].strip() or not isinstance(meta["authors"], list):
                    raise WorkspaceError("Invalid title/authors.")
                if meta.get("priority") not in ("low", "medium", "high"):
                    raise WorkspaceError("Invalid priority.")
                if not all(isinstance(a, str) for a in meta["authors"]):
                    raise WorkspaceError("Authors must be strings.")
                if meta.get("year") is not None and type(meta["year"]) is not int:
                    raise WorkspaceError("Year must be an integer or null.")
                if meta.get("doi") is not None and not isinstance(meta["doi"], str):
                    raise WorkspaceError("DOI must be a string or null.")
                for field in ("zotero_item_key", "zotero_attachment_key"):
                    if meta.get(field) is not None and not re.fullmatch(r"[A-Z0-9]{8}", str(meta[field])):
                        raise WorkspaceError("Invalid Zotero key.")
                if set(meta["research_questions"]) - self.questions():
                    raise WorkspaceError("Unknown research question.")
                if pid not in registered:
                    errors.append(f"{pid}: missing registration event")
                folder, active = self.papers / pid, self.source(meta)
                if meta["note_path"] != "close-reading.md":
                    errors.append(f"{pid}: reading note must be close-reading.md beside its PDF")
                versions = meta["source"]["versions"]
                if len({v["version"] for v in versions}) != len(versions):
                    errors.append(f"{pid}: duplicate version labels")
                for version in versions:
                    path = safe_path(folder, version["file"])
                    if path.parent != folder.resolve():
                        errors.append(f"{pid}: PDF and reading note must share the paper directory")
                    if not path.is_file() or sha(path) != version["sha256"]:
                        errors.append(f"{pid}: missing/changed PDF {version['version']}")
                    digest = version["sha256"]
                    if digest in seen_hashes and seen_hashes[digest] != pid:
                        errors.append(f"{pid}: PDF also registered as {seen_hashes[digest]}")
                    seen_hashes[digest] = pid
                note = safe_path(folder, meta["note_path"])
                if not note.is_file():
                    errors.append(f"{pid}: missing note")
                elif meta["reading_status"] == "deep_read":
                    errors.extend(f"{pid}: {m}" for m in self.note_complete(pid))
                else:
                    errors.extend(f"{pid}: {m}" for m in self.link_errors(note))
                if meta.get("note_source_sha256") not in (None, active["sha256"]):
                    warnings.append(f"{pid}: note belongs to a previous PDF version")
                doi = normalize_doi(meta.get("doi"))
                if doi and doi in seen_dois:
                    errors.append(f"{pid}: DOI also registered as {seen_dois[doi]}")
                if doi:
                    seen_dois[doi] = pid
                extraction = temporary_path(self.root, f"extract/{pid}/manifest.json")
                if extraction.exists() and load(extraction).get("source_sha256") != active["sha256"]:
                    warnings.append(f"{pid}: extracted text is stale")
            except (OSError, ValueError, KeyError, TypeError, WorkspaceError) as error:
                errors.append(f"{pid}: {error}")
        for pid in set(registered) - set(self.ids()):
            errors.append(f"{pid}: registered directory is missing")
        try:
            for kind, folder, meta in self.artifacts():
                if self.artifact_dir(kind, meta["id"]) != folder:
                    errors.append(f"{folder.name}: ID/directory mismatch")
                if set(meta["paper_ids"]) - set(self.ids()):
                    errors.append(f"{meta['id']}: unknown input papers")
                errors.extend(f"{meta['id']}: {m}" for m in self.link_errors(folder / f"{kind}.md"))
                if meta["kind"] != kind or meta["schema_version"] != 1:
                    errors.append(f"{meta['id']}: invalid artifact kind/schema")
                if kind == "review" and meta.get("review_ids"):
                    errors.append(f"{meta['id']}: reviews may depend on papers only")
                if self.stale(meta):
                    warnings.append(f"{meta['id']}: needs update")
        except (OSError, ValueError, KeyError, TypeError, WorkspaceError) as error:
            errors.append(f"Artifact check: {error}")
        return {"ok": not errors, "papers": len(self.ids()),
                "errors": list(dict.fromkeys(errors)), "warnings": list(dict.fromkeys(warnings))}


    def cli_path(self):
        ready = (self.root / ".venv/.workspace-ready").is_file()
        for path in (self.root / ".venv/Scripts/zotero-cli.exe", self.root / ".venv/bin/zotero-cli"):
            if ready and path.is_file():
                return str(path)
        return shutil.which("zotero-cli")

    def zotero(self, arguments, timeout=45):
        cli = self.cli_path()
        if not cli:
            raise WorkspaceError("Project zotero-cli is missing. Run finish-setup.cmd.")
        env = dict(os.environ)
        env.update({"ZOTERO_LOCAL": "true", "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"})
        for key in ("ZOTERO_API_KEY", "ZOTERO_LIBRARY_ID", "ZOTERO_LIBRARY_TYPE"):
            env.pop(key, None)
        result = subprocess.run([cli, "--json", *arguments], cwd=self.root, env=env,
                                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        try:
            response = json.loads(result.stdout)
        except ValueError as error:
            detail = result.stderr.strip()[-500:]
            raise WorkspaceError(f"Zotero returned no structured JSON (exit {result.returncode}). {detail}") from error
        if not isinstance(response, dict):
            raise WorkspaceError("Unexpected Zotero response.")
        if result.returncode or response.get("ok") is False:
            detail = response.get("error", {})
            message = detail.get("message", "CLI request failed") if isinstance(detail, dict) else str(detail)
            raise WorkspaceError(message[:500])
        return response

    def sync_zotero(self, pid):
        meta = self.paper(pid)
        key = meta.get("zotero_item_key")
        if not key or not re.fullmatch(r"[A-Z0-9]{8}", key):
            raise WorkspaceError("Set a valid zotero_item_key in paper.json first.")
        response = self.zotero(["get", "metadata", key])
        data = response.get("data")
        if isinstance(data, dict) and isinstance(data.get("data"), dict):
            data = data["data"]
        if isinstance(data, dict) and isinstance(data.get("item"), dict):
            data = data["item"].get("data", data["item"])
        if not isinstance(data, dict) or not isinstance(data.get("title"), str):
            raise WorkspaceError("Unsupported metadata response; inspect the CLI JSON contract before syncing.")
        meta["title"] = data["title"]
        meta["authors"] = [c.get("name") or " ".join(filter(None, [c.get("firstName"), c.get("lastName")]))
                           for c in data.get("creators", []) if c.get("creatorType", "author") == "author"]
        match = re.search(r"\b(?:19|20)\d{2}\b", str(data.get("date", "")))
        meta["year"] = int(match.group()) if match else None
        meta["doi"] = normalize_doi(data.get("DOI")) or None
        meta["source_url"] = data.get("url") or None
        meta["metadata_origin"] = "zotero"
        meta["zotero_synced_at"] = now()
        meta["updated_at"] = now()
        dump(self.papers / pid / "paper.json", meta)
        self.index()
        return {"paper_id": pid, "metadata_origin": "zotero", "synced_at": meta["zotero_synced_at"]}

    def extract(self, pid):
        try:
            from pypdf import PdfReader
        except ImportError as error:
            raise WorkspaceError("Extraction needs pypdf; run finish-setup.cmd or use the PDF skill.") from error
        meta = self.paper(pid)
        folder, source = self.papers / pid, self.source(meta)
        path = safe_path(folder, source["file"])
        if sha(path) != source["sha256"]:
            raise WorkspaceError("PDF changed; register a new version instead of overwriting.")
        reader = PdfReader(str(path))
        lines = ["# 提取全文", "", f"论文：{pid}；PDF 版本：{source['version']}。",
                 "辅助检索缓存；公式、图表和重要数字需回到 PDF 页面核对。", ""]
        blank = []
        for number, page in enumerate(reader.pages, 1):
            text = page.extract_text() or ""
            if not text.strip():
                blank.append(number)
            lines.extend([f"<!-- PDF_PAGE:{number} -->", f"## PDF 物理页 {number}", "", text, ""])
        target = temporary_path(self.root, f"extract/{pid}/fulltext.md")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(lines), encoding="utf-8")
        dump(target.parent / "manifest.json", {
            "paper_id": pid, "source_version": source["version"], "source_sha256": source["sha256"],
            "parser": "pypdf", "pages": len(reader.pages), "blank_pages": blank, "generated_at": now()})
        return {"paper_id": pid, "pages": len(reader.pages), "blank_pages": blank, "output": str(target)}

    def clean(self, pid):
        """Delete only this paper's known extraction files, never paper archives."""
        self.paper(pid)
        target = temporary_path(self.root, f"extract/{pid}")
        removed = []
        if target.exists():
            entries = list(target.iterdir())
            for entry in entries:
                temporary_path(self.root, f"extract/{pid}/{entry.name}")
                if entry.name not in {"fulltext.md", "manifest.json"} or not entry.is_file():
                    raise WorkspaceError("Unknown extraction content retained; inspect before cleanup.")
            for entry in entries:
                entry.unlink()
                removed.append(entry.name)
            target.rmdir()
            for parent in (target.parent, target.parent.parent):
                if parent.is_dir() and not any(parent.iterdir()):
                    parent.rmdir()
        return {"paper_id": pid, "removed": removed}

    def doctor(self, probe=False):
        checks = [{"name": p, "ok": (self.root / p).is_file()}
                  for p in ("README.md", "AGENTS.md", "00_project/state.md", "00_project/questions.md")]

        primary = self.config.get("primary_skill", {})
        try:
            active_path = safe_path(self.root, primary["path"])
            skill_errors = skill_package_errors(active_path)
            checks.append({"name": "primary_reading_skill", "ok": not skill_errors,
                           "path": str(active_path), "details": skill_errors})
            native_path = safe_path(self.root, primary["native_path"])
            native_errors = skill_package_errors(native_path)
            if active_path != native_path:
                native_errors.append("The configured primary skill is a project fallback; native update is pending.")
            checks.append({"name": "native_skill_installation", "ok": not native_errors,
                           "path": str(native_path), "details": native_errors})
            entries = sorted(p.parent.name for p in native_path.parent.glob("*/SKILL.md"))
            checks.append({"name": "single_native_skill_entry", "ok": entries == ["literature-workflow"],
                           "entries": entries})
        except (OSError, ValueError, KeyError, TypeError, WorkspaceError) as error:
            checks.append({"name": "primary_reading_skill", "ok": False, "details": [str(error)]})
        cli = self.cli_path()
        checks.append({"name": "zotero_cli", "ok": bool(cli), "path": cli})
        git_ok = False
        if shutil.which("git"):
            result = subprocess.run(["git", "-C", str(self.root), "rev-parse", "--show-toplevel"],
                                    capture_output=True, text=True, encoding="utf-8", errors="replace")
            git_ok = result.returncode == 0 and Path(result.stdout.strip()).resolve() == self.root
        checks.append({"name": "project_git", "ok": git_ok})
        try:
            import pypdf
            parser_version = pypdf.__version__
        except ImportError:
            parser_version = None
        checks.append({"name": "pdf_text_parser", "ok": bool(parser_version), "version": parser_version})
        if probe:
            try:
                self.zotero(["get", "collections", "--limit", "1"], timeout=20)
                checks.append({"name": "zotero_local_connection", "ok": True})
            except (WorkspaceError, subprocess.TimeoutExpired, OSError) as error:
                checks.append({"name": "zotero_local_connection", "ok": False, "detail": str(error)[:500]})
        return {"checked_at": now(), "ok": all(c["ok"] for c in checks), "checks": checks}

def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, help="Workspace root; defaults to this script's project.")
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("register")
    s.add_argument("pdf"); s.add_argument("--title"); s.add_argument("--doi"); s.add_argument("--zotero-key")
    s.add_argument("--rq", nargs="*", default=[]); s.add_argument("--keep-inbox", action="store_true")
    s = sub.add_parser("add-version")
    s.add_argument("paper_id"); s.add_argument("pdf"); s.add_argument("--label", required=True)
    s.add_argument("--activate", action="store_true"); s.add_argument("--keep-inbox", action="store_true")
    s = sub.add_parser("status")
    s.add_argument("paper_id"); s.add_argument("--reading", choices=READING)
    s.add_argument("--verification", choices=VERIFICATION); s.add_argument("--user-confirmed", action="store_true")
    for name in ("index", "check"):
        sub.add_parser(name)
    s = sub.add_parser("doctor"); s.add_argument("--probe-zotero", action="store_true")
    for kind in ("review", "idea"):
        s = sub.add_parser(kind)
        actions = s.add_subparsers(dest="action", required=True)
        new = actions.add_parser("new")
        new.add_argument("--title", required=True); new.add_argument("--papers", nargs="*", default=[])
        if kind == "review":
            new.add_argument("--question", required=True)
        else:
            new.add_argument("--reviews", nargs="*", default=[])
        stamp = actions.add_parser("stamp")
        stamp.add_argument("identifier"); stamp.add_argument("--status")
    for name in ("extract", "sync", "clean"):
        s = sub.add_parser(name); s.add_argument("paper_id")
    s = sub.add_parser("zotero")
    actions = s.add_subparsers(dest="action", required=True)
    actions.add_parser("ping")
    q = actions.add_parser("search"); q.add_argument("query"); q.add_argument("--limit", type=int, default=5)
    q = actions.add_parser("read"); q.add_argument("paper_id")
    q.add_argument("--start-page", type=int, required=True); q.add_argument("--end-page", type=int, required=True)
    return p

def main(argv=None):
    args = parser().parse_args(argv)
    try:
        ws = Workspace(args.root or Path(__file__).resolve().parents[2])
        command = args.command
        if command == "register":
            result = ws.register(args.pdf, args.title, args.doi, args.zotero_key, args.rq, args.keep_inbox)
        elif command == "add-version":
            result = ws.add_version(args.paper_id, args.pdf, args.label, args.activate, args.keep_inbox)
        elif command == "status":
            if not args.reading and not args.verification:
                raise WorkspaceError("Provide --reading or --verification.")
            result = ws.status(args.paper_id, args.reading, args.verification, args.user_confirmed)
        elif command == "index":
            result = ws.index()
        elif command == "check":
            result = ws.check()
        elif command == "doctor":
            result = ws.doctor(args.probe_zotero)
        elif command in ("review", "idea"):
            result = (ws.new_artifact(command, args.title, args.papers,
                      getattr(args, "question", None), getattr(args, "reviews", []))
                      if args.action == "new" else ws.stamp(command, args.identifier, args.status))
        elif command == "extract":
            result = ws.extract(args.paper_id)
        elif command == "clean":
            result = ws.clean(args.paper_id)
        elif command == "sync":
            result = ws.sync_zotero(args.paper_id)
        elif command == "zotero":
            if args.action == "ping":
                result = ws.zotero(["get", "collections", "--limit", "1"], timeout=20)
            elif args.action == "search":
                if not 1 <= args.limit <= 100:
                    raise WorkspaceError("Limit must be 1..100.")
                result = ws.zotero(["search", args.query, "--limit", str(args.limit), "--detail", "keys_only"])
            else:
                if args.start_page < 1 or args.end_page < args.start_page:
                    raise WorkspaceError("Invalid PDF page range.")
                meta = ws.paper(args.paper_id)
                key = meta.get("zotero_attachment_key") or meta.get("zotero_item_key")
                if not key:
                    raise WorkspaceError("Paper has no Zotero item/attachment key.")
                result = ws.zotero(["read", key, "--start-page", str(args.start_page),
                                    "--end-page", str(args.end_page)])
        else:
            raise WorkspaceError("Unsupported command.")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok", True) else 1
    except (WorkspaceError, OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        return 1

if __name__ == "__main__":
    raise SystemExit(main())

