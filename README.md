# 精读笔记库

用于 Windows 的本地论文 Markdown 阅读和编辑软件。在 [Releases](https://github.com/lizhengda0525-sudo/literature-notes-reader/releases/latest) 获取安装版或免安装版。首次打开时选择含有 `90_system/workspace.json` 的已有文献工作区。

- 在“编辑 MD”左侧写 Markdown，右侧查看实时排版，按 Ctrl+S 保存。阅读设置可在本地 Blue Topaz 暗色主题和浅色之间切换。
- 选择、粘贴或拖入 PNG、JPEG、WebP。每张图片最多 8 MB，存入当前论文的 `images/`，相同内容在本篇内复用。正式笔记 `20_papers/Pxxxx/close-reading.md` 通过相对路径引用图片。
- 工具栏可在系统编辑器打开 Markdown，在 Zotero 打开已关联的原文。桌面软件支持跨论文检索、章节导航、修改冲突比较和 Windows 当前用户加密的恢复草稿。

顶栏提供阅读、资料库与工作台，启动时恢复上次的论文与阅读位置。从正文点图、表或公式的编号即可打开侧栏对照，亦可在资料库按论文、类型或正文与补充来源检索。工作台汇总项目中的阅读状态、研究问题、方法标签和资料数量。

新笔记依次展示论文信息、摘要、关键词、作者主方法框架图、结论和精读，最后联系动态脑网络建模。摘要、结论默认显示中文，可展开英文原文，关键词照录原论文。原文缺少框架图时会提示读者决定如何处理。

## 从源代码运行

使用 Windows x64、Python 3.12 或更新版本和 Edge WebView2 Runtime。在仓库根目录创建虚拟环境后执行：

```powershell
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -r 90_system/desktop/requirements.freeze.txt
./.venv/Scripts/python.exe -B 90_system/scripts/desktop.py
```

构建安装包还需要 Inno Setup。使用同一环境执行 `90_system/scripts/build_desktop.py --compiler <ISCC.exe路径>`。构建产物在 `90_system/desktop/releases/` 下。测试使用临时合成论文，执行 `python -B -m unittest discover -s 90_system/tests`。
