"""Windows desktop entry for the existing local Markdown literature workspace."""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from functools import partial, wraps
import json
import os
from pathlib import Path
import sys
import threading
import time
from urllib.parse import urlsplit
import webbrowser

from desktop_store import NoteStore, StateStore, VERSION
from literature import WorkspaceError
from reader import ReaderHandler, ReaderServer, launch_markdown, launch_zotero, note_summary, paper_detail

APP_NAME = "精读笔记库"
RESOURCE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
ASSETS = RESOURCE / "reader"


def guarded(method):
    @wraps(method)
    def invoke(self, *args, **kwargs):
        try:
            with self._lock:
                return method(self, *args, **kwargs)
        except (OSError, ValueError, TypeError, KeyError, WorkspaceError) as error:
            return {"ok": False, "error": str(error) or "本地文件操作未完成。"}
    return invoke


class DesktopAPI:
    def __init__(self, state, root=None):
        self._state = state
        self._lock = threading.RLock()
        self._window = None
        self._notes = None
        self._error = ""
        self._draft_error = ""
        self._drafts = {}
        self._persisted_drafts = {}
        self._stop = threading.Event()
        self._close_pending = threading.Lock()
        self._close_approved = threading.Event()
        self._generation = 0
        candidate = root or state.data.get("last_project")
        if candidate:
            try:
                self._select(candidate)
            except (OSError, ValueError, WorkspaceError):
                self._error = "上次项目暂时无法访问。请重新连接项目盘，或选择已有项目。"

    @property
    def _workspace(self):
        return self._notes.workspace if self._notes else None

    def _select(self, root):
        notes = NoteStore(root, self._state)
        notes.signature()
        self._notes = notes
        self._generation += 1
        self._state.data["last_project"] = str(notes.root)
        recent = [str(notes.root)] + self._state.data.get("recent_projects", [])
        self._state.data["recent_projects"] = list(dict.fromkeys(recent))[:8]
        self._state.flush()
        self._error = ""

    def _project(self, expected):
        if not self._notes or expected != str(self._notes.root):
            raise WorkspaceError("项目已切换，请刷新后再操作。")
        return self._notes

    @guarded
    def get_status(self):
        notes = self._notes
        signature, available = "", False
        if notes:
            try:
                signature, available = notes.signature(), True
                self._error = self._draft_error
            except OSError:
                self._error = "项目暂时无法访问，请重新连接项目盘。未保存内容仍可保留为恢复草稿。"
        root = str(notes.root) if notes else None
        key = self._state.key(root, "ui") if root else "none"
        return {"ok": True, "version": VERSION, "project": root, "available": available,
                "name": notes.workspace.config.get("name", "文献阅读") if notes else "选择项目",
                "signature": signature, "generation": self._generation, "error": self._error,
                "ui": self._state.data.get("projects", {}).get(key, {}),
                "recent": self._state.data.get("recent_projects", [])}

    @guarded
    def choose_project(self):
        import webview
        paths = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        if not paths:
            return {"ok": True, "cancelled": True}
        self._select(paths[0])
        return self.get_status()

    @guarded
    def reopen_project(self, path):
        if path not in self._state.data.get("recent_projects", []):
            raise WorkspaceError("请选择最近使用的已有项目。")
        self._select(path)
        return self.get_status()

    @guarded
    def list_papers(self, project):
        if not project and not self._notes:
            return {"ok": True, "papers": []}
        ws = self._project(project).workspace
        return {"ok": True, "papers": [note_summary(ws, ws.paper(pid)) for pid in ws.ids()]}

    @guarded
    def read_note(self, project, pid):
        notes = self._project(project)
        notes.path(pid)
        return {"ok": True, **paper_detail(notes.workspace, pid),
                "recovery": self._state.recovery(notes.root, pid)}

    @guarded
    def save_note(self, project, pid, text, base_hash):
        notes = self._project(project)
        result = notes.save(pid, text, base_hash)
        if result.get("ok"):
            self._drafts.pop((project, pid), None)
            self._persisted_drafts.pop((project, pid), None)
        return result

    @guarded
    def update_draft(self, project, pid, text, base_hash, dirty):
        notes = self._project(project)
        key = (project, pid)
        if dirty:
            if not isinstance(text, str) or len(text.encode("utf-8")) > 2 * 1024 * 1024:
                raise WorkspaceError("草稿内容超过 2 MB。")
            self._drafts[key] = (notes, text, base_hash)
        else:
            self._drafts.pop(key, None)
            self._persisted_drafts.pop(key, None)
            self._state.recovery(notes.root, pid, clear=True)
        return {"ok": True}

    @guarded
    def discard_recovery(self, project, pid):
        notes = self._project(project)
        self._drafts.pop((project, pid), None)
        self._persisted_drafts.pop((project, pid), None)
        self._state.recovery(notes.root, pid, clear=True)
        return {"ok": True}

    @guarded
    def preserve_draft(self, project, pid, text, base_hash):
        notes = self._project(project)
        notes.draft(pid, text, base_hash)
        self._drafts[(project, pid)] = (notes, text, base_hash)
        return {"ok": True}

    @guarded
    def save_ui(self, project, value):
        notes = self._project(project)
        if not isinstance(value, dict) or len(json.dumps(value)) > 65536:
            raise WorkspaceError("界面设置无效。")
        # No arbitrary configuration or credentials accepted by this interface.
        filtered = {key: value[key] for key in ("last_paper", "positions", "font_size", "reading_width", "focus", "theme")
                    if key in value}
        key = self._state.key(notes.root, "ui")
        self._state.data.setdefault("projects", {})[key] = filtered
        self._state.flush()
        return {"ok": True}

    @guarded
    def add_image(self, project, pid, encoded):
        return self._project(project).add_image(pid, encoded)

    @guarded
    def open_note(self, project, pid):
        launch_markdown(self._project(project).path(pid))
        return {"ok": True}

    @guarded
    def open_project_folder(self, project):
        os.startfile(str(self._project(project).root))
        return {"ok": True}

    @guarded
    def open_zotero(self, project, pid):
        notes = self._project(project)
        launch_zotero(notes.workspace.paper(pid))
        return {"ok": True}

    @guarded
    def open_link(self, url):
        parsed = urlsplit(url)
        if parsed.scheme not in {"https", "http"} or not parsed.netloc or parsed.username or parsed.password:
            raise WorkspaceError("只能用浏览器打开普通网页链接。")
        webbrowser.open(url)
        return {"ok": True}

    def _flush_drafts(self):
        with self._lock:
            for key, (notes, text, base_hash) in list(self._drafts.items()):
                from desktop_store import fingerprint
                signature = fingerprint((base_hash + text).encode("utf-8"))
                if self._persisted_drafts.get(key) != signature:
                    notes.draft(key[1], text, base_hash)
                    self._persisted_drafts[key] = signature

    def _draft_loop(self):
        while not self._stop.wait(1):
            try:
                self._flush_drafts()
                self._draft_error = ""
            except OSError:
                self._draft_error = "本机恢复草稿写入失败，请尽快保存笔记。"

    def _closing(self):
        # WinForms calls this synchronously on its UI thread. WebView2 needs
        # that thread to deliver the result of any editor snapshot.
        if self._close_approved.is_set():
            return True
        if self._close_pending.acquire(blocking=False):
            threading.Thread(target=self._finish_close, name="desktop-close", daemon=True).start()
        return False

    def _finish_close(self):
        try:
            if self._prepare_close():
                self._close_approved.set()
                self._window.destroy()
        except Exception:
            self._close_approved.clear()
            self._window.create_confirmation_dialog("暂时无法退出", "请先保存笔记，再重试退出。")
        finally:
            try:
                if not self._close_approved.is_set() and self._window.events.loaded.is_set():
                    self._window.evaluate_js("document.body.inert = false")
            finally:
                self._close_pending.release()

    def _prepare_close(self):
        snapshot = None
        if self._window.events.loaded.is_set():
            snapshot = self._window.evaluate_js("window.readerDesktop?.snapshot() ?? null")
        if snapshot and snapshot.get("busy"):
            return False
        if snapshot and snapshot.get("dirty"):
            result = self.preserve_draft(snapshot["project"], snapshot["pid"], snapshot["text"], snapshot["base_hash"])
            if not result.get("ok"):
                self._window.create_confirmation_dialog("草稿保存失败", "请先保存或复制未保存内容。本次关闭已取消。")
                return False
        if snapshot and snapshot.get("ui") and self._notes:
            self.save_ui(str(self._notes.root), snapshot["ui"])
        if self._drafts:
            try:
                self._flush_drafts()
            except OSError:
                self._window.create_confirmation_dialog("草稿保存失败", "请先保存笔记或复制未保存内容。本次关闭已取消。")
                return False
            if not self._window.create_confirmation_dialog("仍有未保存修改", "已保留本机恢复草稿。确定关闭软件吗？"):
                return False
        self._state.data["window"] = {"width": self._window.width, "height": self._window.height}
        self._state.flush()
        return True


def single_instance():
    kernel = ctypes.windll.kernel32
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateMutexW(None, False, "Local\\LiteratureNotes.Desktop.v1")
    if kernel.GetLastError() == 183:
        user = ctypes.windll.user32
        user.FindWindowW.restype = wintypes.HWND
        for _ in range(20):
            window = user.FindWindowW(None, APP_NAME)
            if window:
                user.ShowWindow(window, 9)
                user.SetForegroundWindow(window)
                break
            time.sleep(.1)
        kernel.CloseHandle(handle)
        return None
    return handle


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project")
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--diagnostic-report", type=Path)
    args = parser.parse_args()
    state_dir = args.state_dir or Path(os.environ["LOCALAPPDATA"]) / "LiteratureNotes"
    state = StateStore(state_dir)
    handle = None
    if not args.diagnostic_report:
        handle = single_instance()
        if not handle:
            return 0
    api = DesktopAPI(state, args.project)
    if args.diagnostic_report:
        report = {"version": VERSION, "frozen": bool(getattr(sys, "frozen", False)),
                  "assets": (ASSETS / "reader.mjs").is_file(), "status": api.get_status()}
        try:
            import webview
            from webview.platforms.winforms import _is_chromium
            report["webview_import"] = True
            report["webview2_available"] = _is_chromium()
            from pypdf import PdfReader
            report["pdf_import"] = True
            report["required_assets"] = all((ASSETS / name).is_file() for name in (
                "index.html", "reader.mjs", "evidence.mjs", "mapping.mjs", "reader.css",
                "desktop-ui.mjs", "desktop.css", "theme.css", "vendor/blue-topaz/theme.css", "vendor/katex/katex.min.js",
                "vendor/dompurify/purify.min.js", "vendor/marked/marked.esm.js"))
        except Exception as error:
            report["error"] = type(error).__name__ + ": " + str(error)
        args.diagnostic_report.parent.mkdir(parents=True, exist_ok=True)
        args.diagnostic_report.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
        return 0
    server = ReaderServer(("127.0.0.1", 0), partial(ReaderHandler, assets=ASSETS))
    server.controller = api
    server.daemon_threads = True
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    drafts = threading.Thread(target=api._draft_loop, daemon=True)
    drafts.start()
    try:
        # Establish DPI awareness before the GUI framework creates any controls.
        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except (AttributeError, OSError):
            pass
        import webview
        from webview.menu import Menu, MenuAction
        window_state = state.data.get("window", {})
        window = webview.create_window(APP_NAME, f"http://127.0.0.1:{server.server_port}/",
            js_api=api, width=max(900, min(1600, int(window_state.get("width", 1280)))),
            height=max(620, min(1100, int(window_state.get("height", 820)))),
            min_size=(820, 560), text_select=True, background_color="#2e3440",
            menu=[Menu("项目", [MenuAction("打开已有项目…", lambda: window.evaluate_js(
                                    "document.getElementById('choose-project')?.click()")),
                                MenuAction("打开项目文件夹", lambda: api.open_project_folder(str(api._notes.root)) if api._notes else None),
                                MenuAction("退出", lambda: window.destroy())]),
                  Menu("帮助", [MenuAction("关于精读笔记库", lambda: window.create_confirmation_dialog(
                      "关于", f"精读笔记库 {VERSION}\n本地 Markdown · Zotero 原文阅读\n研究资料保留在所选项目目录。"))])])
        api._window = window
        window.events.closing += api._closing
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
        webview.start(gui="edgechromium", debug=False, private_mode=False,
                      storage_path=str(state_dir / "webview"), icon=str(RESOURCE / "desktop/app.ico"))
    except Exception as error:
        (state_dir / "last-error.txt").write_text(type(error).__name__ + ": " + str(error), "utf-8")
        ctypes.windll.user32.MessageBoxW(None,
            "软件未能启动。请确认已安装 Microsoft Edge WebView2 Runtime。\n"
            "下载地址：https://developer.microsoft.com/microsoft-edge/webview2/\n"
            "研究资料未被移动。", APP_NAME, 16)
        return 1
    finally:
        api._stop.set()
        drafts.join(timeout=3)
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)
        ctypes.windll.kernel32.CloseHandle(handle)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
