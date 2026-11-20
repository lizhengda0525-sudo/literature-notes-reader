# 精读笔记库

Windows 本地论文笔记阅读器。打开已有文献项目，使用 Zotero 阅读原文，使用同目录的 Markdown 文件记录精读笔记。

在 Releases 下载 Windows 安装包或免安装包。源代码包含共用阅读界面、本机服务、桌面客户端、测试与构建脚本。

已有文献工作区的网页版可用 lit.cmd reader 打开。在其他位置运行源码时，使用 Python 3.12 或更高版本安装 pypdf，执行 python 90_system/scripts/literature.py --root "<文献工作区>" reader。
