# 项目命令

在项目根目录使用 PowerShell。日常入口 lit.cmd 自动选择已完成的 .venv，并禁用字节码写入。

```powershell
./lit.cmd register "10_inbox/paper.pdf" --title "论文题名" --rq RQ01
./lit.cmd extract P0001
./lit.cmd clean P0001
./lit.cmd add-version P0001 "10_inbox/revised.pdf" --label v2 --activate
./lit.cmd status P0001 --reading skimmed
./lit.cmd status P0001 --reading deep_read
./lit.cmd zotero search "关键词" --limit 5
./lit.cmd zotero ping
./lit.cmd sync P0001
./lit.cmd zotero read P0001 --start-page 1 --end-page 2
./lit.cmd review new --question RQ01 --title "方法比较" --papers P0001 P0002
./lit.cmd review stamp RQ01
./lit.cmd idea new --title "候选改进" --papers P0001 P0002 --reviews RQ01
./lit.cmd idea stamp I0001 --status needs_search
./lit.cmd check
./lit.cmd index
./lit.cmd doctor --probe-zotero
```

register 会校验归档副本后移出收件箱原件；外部路径原件保留，--keep-inbox 可保留收件箱副本。
使用 Windows 桌面软件“精读笔记库”打开文献项目。“编辑 MD”双栏编辑和预览，Ctrl+S 写回正式文件。插入、粘贴或拖入图片后以 images/ 相对路径引用。
extract 将辅助文本写入 .tmp/extract/Pxxxx/，仅用于定位原文；本轮使用后 clean Pxxxx 删除已知提取产物。clean 不删除 PDF、笔记和证据；遇到未知临时内容会停止，需逐项检查。其他任务的临时目录由创建者在使用后清理。
review new / idea new 只创建模板，须实际完成分析和证据引用后再 stamp。已有成果直接编辑，不能反复 new。
status --verification reviewed --user-confirmed 仅用于用户明确授权且实际完成的人工核对。
精细 Zotero 命令先查 .venv/Scripts/zotero-cli.exe --help；不调用 0.15.1 不支持的 library info，不直接修改书库数据库。

## 指定外部工作区
工具脚本支持 --root，且参数须放在子命令之前。PDF 相对路径以目标工作区根目录解析，也可使用绝对路径。使用用户明确指定的工作区，不修改调用所在项目的真实目录。

```powershell
& "<工具项目>/.venv/Scripts/python.exe" -B "<工具项目>/90_system/scripts/literature.py" --root "<目标项目>" check
```

外部工作区须包含实际配置、模板、研究问题与目录；脚本不自动替用户创建或切换研究项目。

## 脚本维护测试
```powershell
./.venv/Scripts/python.exe -B -m unittest discover -s 90_system/tests -v
```
