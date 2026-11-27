"""Discover bibliographic items in the local Zotero profile and link them to workspace papers."""
from __future__ import annotations

import configparser
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
import unicodedata

from literature import WorkspaceError, dump, now, safe_path


def profile_database():
    base = Path(os.environ.get("APPDATA", Path.home())) / "Zotero/Zotero"
    profiles = configparser.ConfigParser(interpolation=None)
    profiles.read(base / "profiles.ini", encoding="utf-8-sig")
    sections = [profiles[s] for s in profiles.sections() if s.startswith("Profile") and "Path" in profiles[s]]
    default = next((profiles[s].get("Default") for s in profiles if s.startswith("Install") and profiles[s].get("Default")), None)
    profile = next((s for s in sections if s["Path"] == default), None)
    if profile is None:
        profile = next((s for s in sections if s.get("Default") == "1"), sections[0] if sections else None)
    data = Path.home() / "Zotero"
    if profile is not None:
        folder = base / profile["Path"] if profile.get("IsRelative", "1") == "1" else Path(profile["Path"])
        prefs = folder / "prefs.js"
        if prefs.is_file():
            # Consume only the data-directory preference. Never export profile settings.
            source = prefs.read_text("utf-8-sig")
            custom = re.search(r'^user_pref\("extensions\.zotero\.dataDir",\s*("(?:[^"\\]|\\.)*")\);', source, re.M)
            disabled = re.search(r'^user_pref\("extensions\.zotero\.useDataDir",\s*false\);', source, re.M)
            if custom and not disabled:
                data = Path(json.loads(custom[1]))
    return data / "zotero.sqlite"


def norm(value):
    return "".join(char for char in unicodedata.normalize("NFKC", value or "").casefold() if char.isalnum())


def doi(value):
    return re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", (value or "").strip(), flags=re.I).casefold()


def item_ref(meta):
    key = meta.get("zotero_item_key")
    if not isinstance(key, str) or not re.fullmatch(r"[A-Z0-9]{8}", key):
        return None
    if meta.get("zotero_library_type") == "user":
        return "library/" + key
    group = str(meta.get("zotero_library_id", ""))
    if meta.get("zotero_library_type") == "group" and group.isdecimal():
        return f"groups/{group}/{key}"
    return None


LINK_FIELDS = ("zotero_library_type", "zotero_library_id", "zotero_item_key", "zotero_attachment_key", "zotero_attachment_mode")


def link_revision(meta):
    fields = (*LINK_FIELDS, "title", "authors", "year", "doi")
    return hashlib.sha256(json.dumps([meta.get(k) for k in fields]).encode()).hexdigest()


def pdf_name(path):
    # Linked attachments can use an absolute path or Zotero's attachments: prefix.
    return (path or "").replace("\\", "/").rsplit("/", 1)[-1].rsplit(":", 1)[-1]


class ZoteroLibrary:
    def __init__(self, database=None):
        self.database = database
        self.stamp = None
        self.loaded_at = 0
        self.items = {}

    def refresh(self, force=False):
        try:
            db = self.database or profile_database()
            if not db.is_file():
                raise WorkspaceError("找不到本机 Zotero 书库。请先打开 Zotero 完成初始化，再重试。")
            wal = db.with_name(db.name + "-wal")
            paths = [db, wal] if wal.is_file() else [db]
            stamp = (db, *((s.st_mtime_ns, s.st_size) for s in (p.stat() for p in paths)))
            # Windows can defer timestamps on a WAL that Zotero keeps open.
            if not force and stamp == self.stamp and time.monotonic() - self.loaded_at < 15:
                return
            conn = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.7)
            try:
                conn.execute("PRAGMA query_only = ON")
                conn.execute("BEGIN")
                items = self._read(conn)
            finally:
                conn.close()
            self.stamp, self.items = stamp, items
            self.loaded_at = time.monotonic()
        except (OSError, sqlite3.Error, configparser.Error, ValueError) as error:
            raise WorkspaceError("暂时无法读取本机 Zotero 书库。请等待导入完成并重试。") from error

    @staticmethod
    def _read(conn):
        by_id, attachments = {}, []
        rows = conn.execute("""
            SELECT i.itemID,i.key,t.typeName,l.type,g.groupID,g.name,a.parentItemID,a.contentType,a.path,
                   MAX(CASE f.fieldName WHEN 'title' THEN v.value END),
                   MAX(CASE f.fieldName WHEN 'DOI' THEN v.value END),
                   MAX(CASE f.fieldName WHEN 'date' THEN v.value END)
              FROM items i JOIN itemTypes t USING(itemTypeID) JOIN libraries l USING(libraryID)
              LEFT JOIN groups g ON g.libraryID=i.libraryID LEFT JOIN itemAttachments a USING(itemID)
              LEFT JOIN itemData d ON d.itemID=i.itemID AND d.fieldID IN
                  (SELECT fieldID FROM fields WHERE fieldName IN ('title','DOI','date'))
              LEFT JOIN fields f USING(fieldID) LEFT JOIN itemDataValues v USING(valueID)
             WHERE l.type IN ('user','group') AND t.typeName NOT IN ('note','annotation')
               AND NOT EXISTS (SELECT 1 FROM deletedItems x WHERE x.itemID=i.itemID)
             GROUP BY i.itemID""")
        for ident, key, kind, lib, gid, group, parent, mime, path, title, article_doi, date in rows:
            if lib == "group" and not gid:
                continue
            scope = "library" if lib == "user" else f"groups/{gid}"
            if kind == "attachment":
                filename = pdf_name(path)
                if mime != "application/pdf" and not filename.casefold().endswith(".pdf"):
                    continue
                attachments.append((parent or ident, {"key": key, "title": filename or title or "PDF"}))
                if parent:
                    continue
                title = filename.removesuffix(".pdf") if filename and (not title or title.casefold() in {"pdf", "full text pdf"}) else title or filename
            year = re.search(r"\b(?:19|20)\d{2}\b", date or "")
            by_id[ident] = {"ref": scope + "/" + key, "title": title or key, "doi": doi(article_doi),
                            "year": year[0] if year else "", "authors": [], "pdfs": [],
                            "library": group if lib == "group" else "我的文库", "standalone": kind == "attachment"}
        for parent, pdf in attachments:
            if parent in by_id:
                by_id[parent]["pdfs"].append(pdf)
        for ident, first, last in conn.execute("""
                SELECT ic.itemID,c.firstName,c.lastName FROM itemCreators ic JOIN creators c USING(creatorID)
                JOIN creatorTypes ct USING(creatorTypeID) WHERE ct.creatorType='author' ORDER BY ic.itemID,ic.orderIndex"""):
            if ident in by_id:
                by_id[ident]["authors"].append(" ".join(filter(None, (first, last))))
        return {item["ref"]: item for item in by_id.values()}

    def search(self, meta, query=""):
        self.refresh()
        raw = query.strip()
        words = [norm(word) for word in raw.split() if norm(word)]
        title, article_doi = norm(meta["title"]), doi(meta.get("doi"))
        authors = {norm(a) for a in meta.get("authors", []) if a}
        current = item_ref(meta)
        legacy_key = meta.get("zotero_item_key") if not current else None
        candidates = []
        for item in self.items.values():
            equal_title = bool(title and title == norm(item["title"]))
            equal_doi = bool(article_doi and article_doi == item["doi"])
            conflict = bool(article_doi and item["doi"] and not equal_doi)
            same_author = any(norm(a) in authors for a in item["authors"])
            same_year = not meta.get("year") or not item["year"] or str(meta["year"]) == item["year"]
            if equal_doi:
                score, reason = 3, "DOI 一致"
            elif equal_title and same_author and same_year and not conflict:
                score, reason = 2, "题名与作者一致"
            else:
                score, reason = (1, "题名一致，请核对版本") if equal_title else (0, "")
            if raw:
                haystack = norm(" ".join([item["title"], item["doi"], item["ref"], *item["authors"], *(p["title"] for p in item["pdfs"])]))
                if not words or not all(word in haystack for word in words):
                    continue
            elif not (score or (len(title) > 10 and title in norm(item["title"])) or item["ref"] == current
                      or item["ref"].rsplit("/", 1)[-1] == legacy_key):
                continue
            candidates.append((score, {**item, "reason": reason}))
        candidates.sort(key=lambda pair: (-pair[0], pair[1]["title"].casefold(), pair[1]["ref"]))
        # Duplicate editions or libraries must remain distinguishable, even with an identical title.
        best = [entry for score, entry in candidates if score >= 1]
        automatic = best[0] if len(best) == 1 and candidates[0][0] >= 2 else None
        return {"candidates": [entry for _, entry in candidates[:40]], "total": len(candidates),
                "automatic": automatic, "revision": link_revision(meta), "current": current}


def bind_item(ws, pid, library, ref, attachment, mode, expected):
    library.refresh(force=True)
    item = library.items.get(ref)
    if not item or mode not in {"auto", "manual", "item"}:
        raise WorkspaceError("该条目已变更，请重新查找。")
    pdfs = [pdf["key"] for pdf in item["pdfs"]]
    if mode == "auto":
        attachment = pdfs[0] if len(pdfs) == 1 else None
    elif mode == "item":
        attachment = None
    elif attachment not in pdfs:
        raise WorkspaceError("所选 PDF 已移出该条目，请重新选择。")
    scope, key = ref.rsplit("/", 1)
    with ws.registration_lock():
        meta = ws.paper(pid)
        if link_revision(meta) != expected:
            raise WorkspaceError("关联已更新，请重新查找。")
        for other in ws.ids():
            if other != pid and item_ref(ws.paper(other)) == ref:
                raise WorkspaceError(f"这个 Zotero 条目已关联 {other}。")
        values = ("user" if scope == "library" else "group", None if scope == "library" else scope.split("/")[1],
                  key, attachment, mode)
        if tuple(meta.get(k) for k in LINK_FIELDS) != values:
            meta.update(zip(LINK_FIELDS, values))
            meta["zotero_synced_at"] = meta["updated_at"] = now()
            dump(safe_path(ws.papers, f"{pid}/paper.json"), meta)
    return meta


def resolve_paper(ws, pid, library):
    """Check the existing identity before discovering a new one. Never silently replace a saved choice."""
    meta = ws.paper(pid)
    library.refresh()
    current = item_ref(meta)
    if current:
        item = library.items.get(current)
        if not item:
            return meta, "missing"
        pdfs = [p["key"] for p in item["pdfs"]]
        selected = meta.get("zotero_attachment_key")
        if selected:
            return meta, "linked" if selected in pdfs else "missing_pdf"
        if meta.get("zotero_attachment_mode") == "item":
            return meta, "linked"
        if len(pdfs) > 1:
            return meta, "choose_pdf"
        if not pdfs:
            return meta, "linked"
    else:
        matches = library.search(meta)
        item = matches["automatic"]
        legacy_key = meta.get("zotero_item_key")
        if legacy_key and (not item or item["ref"].rsplit("/", 1)[-1] != legacy_key):
            return meta, "choose" if matches["total"] else "missing"
        if not item:
            return meta, "choose" if matches["total"] else "unmatched"
        legacy_pdf = meta.get("zotero_attachment_key")
        if legacy_pdf:
            if legacy_pdf not in [p["key"] for p in item["pdfs"]]:
                return meta, "missing_pdf"
            return bind_item(ws, pid, library, item["ref"], legacy_pdf, "manual", link_revision(meta)), "linked"
        if len(item["pdfs"]) > 1:
            return meta, "choose_pdf"
    return bind_item(ws, pid, library, item["ref"], None, "auto", link_revision(meta)), "linked"
