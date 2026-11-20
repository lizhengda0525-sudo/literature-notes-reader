"""Local Markdown note library; PDF reading belongs to Zotero, with no per-paper HTML copies."""
from __future__ import annotations

import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import unquote, urlsplit

from literature import WorkspaceError, safe_path
from desktop_store import MAX_NOTE

ASSETS = Path(__file__).resolve().parents[1] / "reader"
PID = re.compile(r"P\d{4,}")


def note_summary(ws, meta):
    note = safe_path(ws.papers / meta["paper_id"], meta["note_path"])
    return {"id": meta["paper_id"], "title": meta["title"], "year": meta.get("year"),
            "authors": meta.get("authors", []), "tags": meta.get("tags", []),
            "research_questions": meta.get("research_questions", []),
            "reading_status": meta["reading_status"],
            "verification_status": meta["verification_status"],
            "updated_at": meta.get("updated_at"), "note": note.read_text(encoding="utf-8")}


def zotero_links(meta):
    # Only emit a library URI when the registered library identity is explicit.
    scope = meta.get("zotero_library_type")
    library = ("library" if scope == "user" else
               f"groups/{meta['zotero_library_id']}" if scope == "group" and
               str(meta.get("zotero_library_id", "")).isdigit() else None)
    item, attachment = meta.get("zotero_item_key"), meta.get("zotero_attachment_key")
    valid = lambda key: isinstance(key, str) and re.fullmatch(r"[A-Z0-9]{8}", key)
    return {"item": f"zotero://select/{library}/items/{item}" if library and valid(item) else None,
            "pdf": f"zotero://open-pdf/{library}/items/{attachment}"
            if library and valid(attachment) else None}


def launch_zotero(meta):
    links = zotero_links(meta)
    uri = links["pdf"] or links["item"]
    if not uri:
        raise WorkspaceError("这篇论文尚未关联 Zotero 条目。")
    try:
        os.startfile(uri)
    except OSError:
        for key in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            if not os.environ.get(key):
                continue
            program = Path(os.environ[key]) / "Zotero" / "zotero.exe"
            if program.is_file():
                subprocess.Popen([str(program), "-url", uri])
                return
        raise WorkspaceError("未找到可打开该条目的 Zotero，请检查本机 Zotero 安装。")


def launch_markdown(path):
    try:
        os.startfile(str(path))
    except OSError as error:
        if getattr(error, "winerror", None) != 1155:
            raise
        subprocess.Popen([str(Path(os.environ["SystemRoot"]) / "System32/notepad.exe"), str(path)])


def paper_detail(ws, pid):
    meta = ws.paper(pid)
    note = safe_path(ws.papers / pid, meta["note_path"])
    if note.stat().st_size > MAX_NOTE:
        raise WorkspaceError("笔记超过 2 MB，请使用外部编辑器。")
    note_bytes = note.read_bytes()
    markdown = note_bytes.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    evidence, errors = ws.evidence(pid, markdown)
    if errors:
        evidence = {}
    from pypdf import PdfReader
    versions = []
    for version in meta["source"]["versions"]:
        pdf = safe_path(ws.papers / pid, version["file"])
        description = version.get("description", "").casefold()
        label = ("作者接受稿" if "author accepted manuscript" in description else
                 "正式版" if "version of record" in description else
                 version["version"])
        versions.append({"id": version["version"], "file": version["file"],
                         "pages": len(PdfReader(pdf).pages),
                         "label": label})
    return {"id": pid, "title": meta["title"], "authors": meta["authors"],
            "journal": (meta.get("publication") or {}).get("journal", ""),
            "year": meta["year"], "versions": versions,
            "reading_status": meta["reading_status"],
            "verification_status": meta["verification_status"],
            "tags": meta.get("tags", []),
            "research_questions": meta.get("research_questions", []),
            "note_path": str(note), "zotero": zotero_links(meta),
            "note_hash": hashlib.sha256(note_bytes).hexdigest(),
            "validation_errors": errors,
            "current_version": meta["source"]["current_version"],
            "note": markdown,
            "evidence": [{"id": row[0], "kind": row[1], "version": row[2],
                                     "page": int(row[3]), "locator": row[4],
                                     "claim": row[5], "excerpt": row[6],
                                     "checked_by": row[7]} for row in evidence.values()]}


class ReaderHandler(BaseHTTPRequestHandler):
    def __init__(self, *args, assets=ASSETS, **kwargs):
        self.assets = Path(assets).resolve()
        super().__init__(*args, **kwargs)

    def log_message(self, *_):
        pass

    def response_headers(self, status, mime, size):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; script-src 'self'; "
                         "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
                         "font-src 'self'; connect-src 'self'; worker-src 'self' blob:; "
                         "object-src 'none'; base-uri 'none'; frame-ancestors 'none'")

    def json_response(self, value, status=200):
        data = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.response_headers(status, "application/json; charset=utf-8", len(data))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def send_file(self, path):
        if not path.is_file():
            raise FileNotFoundError
        size = path.stat().st_size
        start, end, status = 0, size - 1, 200
        byte_range = self.headers.get("Range")
        if byte_range:
            match = re.fullmatch(r"bytes=(\d+)-(\d*)", byte_range)
            if not match:
                self.json_response({"error": "Unsupported byte range"}, 416)
                return
            start = int(match[1])
            end = min(int(match[2]) if match[2] else size - 1, size - 1)
            if start > end or start >= size:
                self.json_response({"error": "Invalid byte range"}, 416)
                return
            status = 206
        mime = {".mjs": "text/javascript", ".js": "text/javascript",
                ".css": "text/css", ".html": "text/html; charset=utf-8",
                ".wasm": "application/wasm"}.get(path.suffix, mimetypes.guess_type(path.name)[0]
                                                or "application/octet-stream")
        self.response_headers(status, mime, max(0, end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command != "HEAD":
            with path.open("rb") as stream:
                stream.seek(start)
                remaining = end - start + 1
                while remaining > 0:
                    chunk = stream.read(min(remaining, 256 * 1024))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        try:
            host = self.headers.get("Host", "")
            if host not in {f"127.0.0.1:{self.server.server_port}",
                            f"localhost:{self.server.server_port}"}:
                self.json_response({"error": "Local reader only"}, 403)
                return
            path = unquote(urlsplit(self.path).path)
            parts = path.strip("/").split("/")
            ws = self.server.controller._workspace
            if (ws and len(parts) == 4 and parts[0] == "paper" and PID.fullmatch(parts[1])
                    and parts[2] == "images" and Path(parts[3]).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}):
                pid, relative = parts[1], "/".join(parts[2:])
                self.send_file(safe_path(ws.papers / pid, relative))
            elif path == "/":
                self.send_file(self.assets / "index.html")
            elif parts[0] == "assets":
                self.send_file(safe_path(self.assets, "/".join(parts[1:])))
            else:
                raise FileNotFoundError
        except (OSError, ValueError, KeyError, TypeError, WorkspaceError):
            self.json_response({"error": "Unable to load the requested local document."}, 404)

    def do_POST(self):
        self.json_response({"error": "Use the desktop window"}, 403)


class ReaderServer(ThreadingHTTPServer):
    # Windows must reject a second listener rather than sharing the same port.
    allow_reuse_address = False
