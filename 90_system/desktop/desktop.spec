# Build with --workpath and --distpath pointing into the project .tmp task folder.
from pathlib import Path
base = Path(SPECPATH).resolve()
system = base.parent
a = Analysis([str(system / 'scripts/desktop.py')], pathex=[str(system / 'scripts')],
    binaries=[], datas=[(str(system / 'reader'), 'reader'), (str(base / 'app.ico'), 'desktop'),
                        (str(base / 'THIRD_PARTY.md'), '.')],
    hiddenimports=['webview.platforms.winforms', 'webview.platforms.edgechromium', 'pypdf'],
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=['PyQt5','PyQt6','PySide2','PySide6','tkinter','IPython','notebook','matplotlib','numpy','PIL'],
    noarchive=False, optimize=1)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='精读笔记库', debug=False,
    bootloader_ignore_signals=False, strip=False, upx=False, console=False,
    icon=str(base / 'app.ico'), manifest=str(base / 'app.manifest'))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='精读笔记库')
