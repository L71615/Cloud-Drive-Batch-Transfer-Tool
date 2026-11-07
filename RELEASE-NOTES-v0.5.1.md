# v0.5.1 发版说明

**发布日期**：2026-11-07
**对比版本**：v0.5.0 → v0.5.1（PATCH：只修桌面启动，功能与协议不变）
**提交**：见同名 git tag `v0.5.1`

---

## 这一版做了什么

修"双击桌面图标后没法用"：现在双击会真的把界面打开，而且是能点的界面。

---

## 问题与排查

现象：双击图标后，任务管理器里有 `PanTransfer.exe`、服务也在跑（`/api/health` 正常 200），
但屏幕上要么是白窗口，要么什么都没有。

判定依据：白窗口的标题停在 `127.0.0.1/`（页面标题 `PanTransfer · 批量转存` 从未出现），
CDP 里 `Runtime.evaluate` 直接超时 —— 不是服务端慢，是浏览器的渲染进程没起来。

逐一排除过的方向：

| 怀疑 | 做法 | 结果 |
|---|---|---|
| `--app` 应用窗口模式有问题 | 改成普通标签页 | 一样白屏 |
| PyInstaller 环境变量污染子进程 | 清 `_PYI*`/`_MEI*` | 无效 |
| `_internal` 混进 PATH 导致加载错 DLL | 从零搭一份最小环境（只留 SystemRoot 等） | 无效 |
| 标准句柄无效 | 三路接 `DEVNULL` | 无效 |
| 我们自己当父进程有问题 | 改 `ShellExecuteW` | 无效 |
| 浏览器选错 | 换 Chrome 同样参数 | 一样 |

**结论**：由**打包后的 exe** 直接拉起浏览器时，浏览器渲染进程起不来；同样的参数由
python 或桌面快捷方式拉起就正常。这条差异在 Windows 上没有可复现的官方解释，
继续深挖不如换个可靠的路子。

## 修复

把打开界面的动作交给**资源管理器**：

```python
subprocess.Popen(["explorer.exe", url])
```

浏览器由 shell 启动，不再是本进程的子进程 —— 实测页面立刻正常渲染。

## 代价（说清楚）

- 界面现在开在**默认浏览器**里，会多一条地址栏。
- 原来的无地址栏应用窗口（`--app`）不再默认使用：那条路在打包后不可靠。
- 生命周期：服务常驻，页面 30 分钟没访问就自动退出；想立刻关，任务管理器结束 `PanTransfer.exe`。

## 另外两个顺手的修复

| 问题 | 原因 | 现在 |
|---|---|---|
| 每次 `build.bat` 后登录态和任务全没了 | PyInstaller 会清空 `dist\PanTransfer\`，连 `data\` 一起删 | 先打到 `build_dist`，再拷进 `dist`，保留 `data\` |
| `make_shortcut.bat` 报 `'exe' is not recognized` | cmd 里嵌套 PowerShell 的引号转义崩了 | 改成 `tools/make_shortcut.ps1`（纯 ASCII，避开 PowerShell 5.1 把 UTF-8 当 ANSI 的坑） |

## 升级方式

1. 覆盖源码与 `dist\PanTransfer\`（`data\` 不要动）。
2. 桌面快捷方式不用重建（路径没变）；重建就跑 `make_shortcut.bat`。
3. 重新打包用 `build.bat`。

## 校验

```bash
python -m unittest discover -s tests   # 期望 Ran 67 tests ... OK
python tools/e2e_check.py              # 期望 ✅ 全链路自检通过
```

打包后双击图标：浏览器应打开 `http://127.0.0.1:<端口>/`，标题为「PanTransfer · 批量转存」。
`data\launcher.log` 里应看到 `server ready on 8767` 与 `open in browser via explorer: ...`。
