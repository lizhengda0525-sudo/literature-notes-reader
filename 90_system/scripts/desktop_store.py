"""Desktop state and conflict-safe note storage, independent of GUI and network."""
from __future__ import annotations

import ctypes
import base64
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading

from literature import Workspace, WorkspaceError, temporary_path

VERSION = "1.5.1"
MAX_NOTE = 2 * 1024 * 1024
MAX_IMAGE = 8 * 1024 * 1024


def fingerprint(data):
    return hashlib.sha256(data).hexdigest()


def atomic_bytes(path, data, scratch=None):
    path = Path(path)
    folder = Path(scratch) if scratch else path.parent
    folder.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(prefix="write-", suffix=".tmp", dir=folder, delete=False) as stream:
            name = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if name and name.exists():
            name.unlink()
        if scratch and folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()


def crypt(data, decrypt=False):
    """Windows current-user DPAPI; drafts never persist as plaintext."""
    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_byte))]
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    result = Blob()
    fn = ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    if decrypt:
        ok = fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result))
    else:
        ok = fn(ctypes.byref(source), "LiteratureNotes recovery", None, None, None, 1, ctypes.byref(result))
    if not ok:
        raise OSError("无法保护本机恢复草稿。")
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        ctypes.windll.kernel32.LocalFree(result.data)


class StateStore:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.file = self.folder / "preferences.json"
        try:
            self.data = json.loads(self.file.read_text("utf-8"))
        except (OSError, ValueError):
            self.data = {}
        if not isinstance(self.data, dict):
            self.data = {}

    def flush(self):
        with self.lock:
            atomic_bytes(self.file, json.dumps(self.data, ensure_ascii=False, indent=2).encode("utf-8"))

    def key(self, root, pid):
        # Avoid touching a disconnected project drive when preserving a draft.
        normalized = os.path.normcase(os.path.abspath(str(root)))
        return fingerprint((normalized.casefold() + "|" + pid).encode("utf-8"))

    def recovery(self, root, pid, value=None, clear=False):
        with self.lock:
            folder = self.folder / "recovery"
            path = folder / (self.key(root, pid) + ".bin")
            if clear:
                path.unlink(missing_ok=True)
                return None
            if value is not None:
                folder.mkdir(exist_ok=True)
                atomic_bytes(path, crypt(json.dumps(value, ensure_ascii=False).encode("utf-8")))
                return True
            if path.exists():
                return json.loads(crypt(path.read_bytes(), decrypt=True).decode("utf-8"))
            return None


class NoteStore:
    def __init__(self, root, state):
        self.root = Path(root).resolve()
        self.workspace = Workspace(self.root)
        for name in ("20_papers", "00_project"):
            if not (self.root / name).is_dir():
                raise WorkspaceError("请选择已有的文献阅读项目文件夹。")
        if not (self.root / "00_project/questions.md").is_file():
            raise WorkspaceError("该目录缺少研究问题文件。")
        self.state = state
        self.lock = threading.RLock()

    def path(self, pid):
        meta = self.workspace.paper(pid)
        if meta.get("note_path") != "close-reading.md":
            raise WorkspaceError("只能编辑已登记论文的 close-reading.md。")
        folder = self.root / "20_papers" / pid
        path = folder / "close-reading.md"
        if folder.is_symlink() or folder.is_junction() or path.is_symlink() or path.is_junction():
            raise WorkspaceError("笔记路径是链接，请使用真实项目目录。")
        if not path.resolve().is_relative_to(self.root / "20_papers"):
            raise WorkspaceError("笔记路径超出项目。")
        return path

    def read(self, pid):
        data = self.path(pid).read_bytes()
        if len(data) > MAX_NOTE:
            raise WorkspaceError("笔记超过当前版本支持的 2 MB，请使用外部编辑器。")
        return {"note": data.decode("utf-8-sig"), "hash": fingerprint(data)}

    def add_image(self, pid, encoded):
        if not isinstance(encoded, str) or len(encoded) > 4 * ((MAX_IMAGE + 2) // 3):
            raise WorkspaceError("每张图片最大为 8 MB。")
        data = base64.b64decode(encoded, validate=True)
        if len(data) > MAX_IMAGE:
            raise WorkspaceError("每张图片最大为 8 MB。")
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            ext = "png"
        elif data.startswith(b"\xff\xd8\xff"):
            ext = "jpg"
        elif data.startswith(b"RIFF") and data[8:12] == b"WEBP":
            ext = "webp"
        else:
            raise WorkspaceError("请选择 PNG、JPEG 或 WebP 图片。")
        folder = self.path(pid).parent / "images"
        if folder.is_symlink() or folder.is_junction():
            raise WorkspaceError("图片目录不能是链接。")
        name = fingerprint(data) + "." + ext
        path = folder / name
        with self.lock:
            folder.mkdir(exist_ok=True)
            if path.is_symlink() or path.is_junction():
                raise WorkspaceError("图片文件不能是链接。")
            if path.exists():
                if fingerprint(path.read_bytes()) != fingerprint(data):
                    raise WorkspaceError("同名图片内容不一致，请检查图片目录。")
            else:
                atomic_bytes(path, data)
        return {"ok": True, "path": "images/" + name}

    def save(self, pid, text, expected):
        if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_NOTE:
            raise WorkspaceError("笔记内容无效或超过 2 MB。")
        with self.lock:
            path = self.path(pid)
            original = path.read_bytes()
            if fingerprint(original) != expected:
                self.draft(pid, text, expected)
                return {"ok": False, "conflict": True, **self.read(pid)}
            # Exclusive cross-process writer lock; retained only during this transaction.
            lockfile = self.root / "00_project" / "_desktop-note.lock"
            if lockfile.is_symlink() or lockfile.is_junction():
                raise WorkspaceError("保存锁路径异常，请检查项目。")
            import msvcrt
            fd = os.open(lockfile, os.O_CREAT | os.O_RDWR)
            try:
                if os.fstat(fd).st_size == 0:
                    os.write(fd, b"1")
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            except OSError:
                os.close(fd)
                raise WorkspaceError("另一项保存正在进行，请稍后重试。")
            try:
                # Recheck after acquiring the lock and before replacing the original.
                if fingerprint(path.read_bytes()) != expected:
                    self.draft(pid, text, expected)
                    return {"ok": False, "conflict": True, **self.read(pid)}
                newline = "\r\n" if b"\r\n" in original else "\n"
                normalized = text.replace("\r\n", "\n").replace("\r", "\n")
                data = normalized.replace("\n", newline).encode("utf-8")
                if original.startswith(b"\xef\xbb\xbf"):
                    data = b"\xef\xbb\xbf" + data
                scratch = temporary_path(self.root, "desktop-save")
                atomic_bytes(path, data, scratch)
                self.state.recovery(self.root, pid, clear=True)
            except OSError:
                self.draft(pid, text, expected)
                raise
            finally:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                os.close(fd)
                try:
                    lockfile.unlink(missing_ok=True)
                except PermissionError:
                    pass  # Another process holds it; the OS lock still releases on exit.
            # Validation reports issues without rewriting research metadata or human status.
            validation = {"ok": False, "errors": ["项目检查暂未完成。"]}
            try:
                validation = self.workspace.check()
                self.workspace.index()
            except (OSError, ValueError, WorkspaceError) as error:
                validation = {"ok": False, "errors": [str(error)]}
            return {"ok": True, "hash": fingerprint(data), "validation": validation}

    def draft(self, pid, text, base_hash):
        if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_NOTE:
            raise WorkspaceError("恢复草稿超过 2 MB。")
        # Validate ID even when a removable project drive is offline.
        import re
        if not re.fullmatch(r"P\d{4,}", pid):
            raise WorkspaceError("论文编号无效。")
        self.state.recovery(self.root, pid, {"text": text, "base_hash": base_hash})

    def _signature_rows(self):
        if not self.root.is_dir():
            raise OSError("项目目录暂时无法访问，请重新连接项目盘。")
        questions = (self.workspace.project / "questions.md").stat()
        result = [["questions.md", questions.st_mtime_ns, questions.st_size]]
        for pid in self.workspace.ids():
            for name in ("paper.json", "close-reading.md", "resources.json"):
                path = self.root / "20_papers" / pid / name
                if name == "resources.json" and not path.exists():
                    continue
                stat = path.stat()
                result.append([pid, name, stat.st_mtime_ns, stat.st_size])
        return result

    def signature(self):
        return fingerprint(json.dumps(self._signature_rows()).encode("utf-8"))

    def link_signatures(self, pid, old_stat):
        # A link edit can be acknowledged locally only if all the other watched files
        # still match the UI's last snapshot, including an externally edited note.
        rows = self._signature_rows()
        row = next(r for r in rows if r[:2] == [pid, "paper.json"])
        old = [old_stat.st_mtime_ns, old_stat.st_size]
        if row[2:] == old:
            return {}
        current = fingerprint(json.dumps(rows).encode("utf-8"))
        row[2:] = old
        return {"previous_signature": fingerprint(json.dumps(rows).encode("utf-8")), "signature": current}
