# v0.5.2 发版说明

**发布日期**：2026-11-07
**对比版本**：v0.5.1 → v0.5.2（PATCH：只修桌面启动，功能与协议不变）
**提交**：见同名 git tag `v0.5.2`

---

## 这一版做了什么

上一版虽然"能打开"，但用的是 `explorer.exe <url>` 这条路，在你机器上直接弹
`explorer.exe - Application Error（应用程序无法正常启动 0xc0000142）`。
这一版把启动链整个换掉，并且把无地址栏的应用窗口拿回来了。

---

## 原因

`0xc0000142` 是进程初始化失败。上一版给 `explorer.exe` 传了一份**自己拼的精简环境**
（只有 22 个变量，PATH 只留 system32），explorer 依赖的环境比这多，于是起不来。

同一个坑还有第二层：**由打包后的 exe 直接拉起浏览器，页面不渲染**（窗口开出来、
标题停在 `127.0.0.1/`、渲染进程不响应）。所以浏览器也不能由 exe 直接 `Popen`。

两条合起来，能用的组合只有一个：**浏览器要由 shell 一侧拉起，且不能带自定义环境**。

## 现在的启动链

```
桌面快捷方式 (PanTransfer.lnk)
   └─ wscript.exe  start_app.vbs
        ├─ cmd /c start "" PanTransfer.exe --serve     ← 只起服务，不开窗口
        ├─ 轮询 http://127.0.0.1:<port>/api/health     ← 等到真能应答
        └─ cmd /c start "" msedge.exe --app=http://127.0.0.1:<port>/ ...
```

- `PanTransfer.exe --serve`：新增的"只起服务"模式，自己不碰浏览器。
- `start_app.vbs`：负责等健康检查通过再开窗口；找不到 Edge 就退回默认浏览器。
- 快捷方式指向 `wscript.exe`，所以双击不会闪黑框。
- 端口从 `data\current_port` 读，跟之前一样支持"端口被占就换一个"。

## 验证过的事

| 检查 | 结果 |
|---|---|
| `wscript start_app.vbs` | 服务起来 → 应用窗口打开，标题「PanTransfer · 批量转存」，窗口 1280×880，无地址栏 |
| 窗口内容 | 完整浅色界面：左侧 6 项导航、网盘卡片、链接框 |
| 是否还会报 0xc0000142 | 不会：启动链里不再出现 `explorer.exe` + 精简环境 |
| `--serve` 模式 | 只起服务、日志 `serve-only mode, not opening a window`，页面由外部打开 |

顺带排除过的（都试了，都不行，别再试）：给浏览器传精简环境、清 `_PYI*`/`_MEI*`、
三路输出接空设备、`ShellExecuteW`、`.lnk` 走资源管理器（会被安全警告拦住）、
换 Chrome 同参数。

## 升级方式

1. 用新的 `build.bat` 重新打包（会一并把 `start_app.vbs` 拷进 `dist\PanTransfer\`）。
2. 双击 `make_shortcut.bat` 重建快捷方式（旧的指向 exe，必须换）。
3. `data\` 不用动。

```bash
build.bat            # 打包 + 拷 start_app.vbs + 重建快捷方式
```

## 校验

```bash
python -m unittest discover -s tests   # 期望 Ran 67 tests ... OK
python tools/e2e_check.py              # 期望 ✅ 全链路自检通过
```

双击桌面图标后应看到：独立的 PanTransfer 窗口（无地址栏），左侧导航 + 「新建转存」页。
`data\launcher.log` 里应有 `server ready on <port>` 与 `serve-only mode, not opening a window`。
