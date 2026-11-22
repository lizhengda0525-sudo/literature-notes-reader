"""Build portable and installer packages without including research data."""
from __future__ import annotations
import argparse
import hashlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

from desktop_store import VERSION

ROOT = Path(__file__).resolve().parents[2]
DESKTOP = ROOT / "90_system/desktop"

SCRATCH = ROOT / ".tmp" / f"desktop-build-{VERSION}"


def licenses():
    rows = ["# Third-party notices\n", "Python runtime: Python Software Foundation License.\n"]
    output = SCRATCH / "licenses"
    for name in ("pywebview", "pythonnet", "clr_loader", "cffi", "pycparser", "bottle", "proxy_tools", "typing_extensions", "pypdf"):
        dist = metadata.distribution(name)
        license_name = dist.metadata.get("License-Expression") or dist.metadata.get("License") or "See bundled license files"
        rows.append(f"- {name} {dist.version}: {license_name}\n")
        for file in dist.files or []:
            if any(word in Path(file).name.lower() for word in ("license", "notice", "copying")):
                source = Path(dist.locate_file(file))
                if source.is_file():
                    dest = output / name / Path(file).name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, dest)
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if python_license.is_file():
        (output / "Python").mkdir(parents=True, exist_ok=True)
        shutil.copyfile(python_license, output / "Python/LICENSE.txt")
    rows.append("\nMarkdown, formula and HTML sanitation libraries: see _internal/reader/vendor/manifest.json and each bundled license.\n")
    rows.append("\nBlue Topaz: bundled local Typora Nord/Blue Topaz stylesheet, with original attribution retained.\n")
    rows.append("\nBuild tools: PyInstaller (GPL with bootloader exception), Pillow (HPND), Inno Setup (see compiler license). Build tools are not required on the user's computer.\n")
    (DESKTOP / "THIRD_PARTY.md").write_text("\n".join(rows), "utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--compiler", type=Path, required=True)
    args = parser.parse_args()
    SCRATCH.mkdir(parents=True, exist_ok=True)
    licenses()
    environment = dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1",
                       TEMP=str(SCRATCH / "tmp"), TMP=str(SCRATCH / "tmp"),
                       PYINSTALLER_CONFIG_DIR=str(SCRATCH / "pyinstaller-cache"))
    (SCRATCH / "tmp").mkdir(exist_ok=True)
    subprocess.run([sys.executable, "-B", "-m", "PyInstaller", "--noconfirm", "--clean",
        "--workpath", str(SCRATCH / "work"), "--distpath", str(SCRATCH / "dist"),
        str(DESKTOP / "desktop.spec")], check=True, cwd=ROOT, env=environment)
    portable = SCRATCH / "dist/精读笔记库"
    shutil.copytree(SCRATCH / "licenses", portable / "licenses", dirs_exist_ok=True)
    (portable / "使用说明.md").write_text(f"""# 精读笔记库 {VERSION}

Windows 64 位本地软件。免安装版请完整解压文件夹，双击“精读笔记库.exe”；请勿只移动其中的 EXE。
首次打开时选择已有文献项目文件夹（包含 90_system/workspace.json），以后恢复上次项目。
PDF 用 Zotero 打开；正式笔记仍是论文目录内的 close-reading.md。

- 阅读：检索正文、按标签/研究问题/状态筛选、章节目录、字号与正文宽度、专注阅读。
- 论文信息、摘要中译、作者框架图和结论中译排在精读前面。点击原图可放大。缺图时由你决定如何处理。末节讨论与动态脑网络建模的联系。
- 编辑：点击“编辑 MD”，左侧输入、右侧实时预览。粘贴、拖入或选择图片，原文件自动存入本篇 images/。Ctrl+S 保存相对引用，Ctrl+Z 撤销。阅读设置可以切换 Blue Topaz 暗色与浅色。
- 修改冲突：外部编辑或 Codex 同时改动时，不自动覆盖；比较磁盘与未保存版本后继续手动合并。
- 恢复：未保存内容使用 Windows 当前用户加密，保存在本机应用数据目录的恢复区。
- 关闭：有未保存修改时提示；程序升级和卸载不删除研究项目。
- Zotero 打开已关联的条目或附件。精读采用的论文版本见项目档案。

运行需要 Microsoft Edge WebView2 Runtime；不需要安装 Python。若启动提示缺少组件，请从微软官网下载 WebView2 Runtime。
下载：https://developer.microsoft.com/microsoft-edge/webview2/

窗口与项目记录位于 %LOCALAPPDATA%\\LiteratureNotes；恢复草稿仅能由原 Windows 用户解密。
资料保留在用户选择的研究项目目录，软件不迁移原文、不上传研究材料，不自动写 Zotero。
已有研究项目由 Codex/lit.cmd 管理登记与编号；软件维护笔记，保存不代表用户已核对。

交付自动检查覆盖文件保存、冲突、加密草稿、路径、项目恢复、索引与打包资源。
按用户要求，最终版本的真实窗口、操作体验和安装/卸载界面由用户验收。
""", "utf-8")
    files = []
    for file in sorted(portable.rglob("*")):
        if not file.is_file():
            continue
        relative = file.relative_to(portable).as_posix()
        if file.suffix.lower() in {".pdf", ".bin"} or any(part in {"20_papers", "private", ".venv", ".git", "preferences.json"} for part in file.parts):
            raise RuntimeError("Unexpected research/private file in package: " + relative)
        files.append({"path": relative, "sha256": hashlib.sha256(file.read_bytes()).hexdigest()})
    (portable / "package-manifest.json").write_text(json.dumps({"version": VERSION, "files": files}, ensure_ascii=False, indent=2), "utf-8")
    release = DESKTOP / "releases" / VERSION
    release.mkdir(parents=True, exist_ok=True)
    archive = release / f"精读笔记库-{VERSION}-免安装.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for file in sorted(portable.rglob("*")):
            if file.is_file():
                bundle.write(file, "精读笔记库/" + file.relative_to(portable).as_posix())
    subprocess.run([str(args.compiler.resolve()), "/Qp", "/DAppVersion=" + VERSION, "/DSourceDir=" + str(portable),
                    "/DOutputDir=" + str(release), str(DESKTOP / "installer.iss")],
                   check=True, cwd=DESKTOP, env=environment)
    artifacts = [archive, release / f"精读笔记库-{VERSION}-安装.exe"]
    (release / "SHA256SUMS.txt").write_text("\n".join(hashlib.sha256(p.read_bytes()).hexdigest() + "  " + p.name for p in artifacts) + "\n", "utf-8")
    print(json.dumps({"ok": True, "release": str(release), "artifacts": [{"name": p.name, "bytes": p.stat().st_size} for p in artifacts]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
