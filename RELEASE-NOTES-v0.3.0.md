# v0.3.0 发版说明

**发布日期**：2026-11-06
**对比版本**：v0.2.0 → v0.3.0（MINOR：新增桌面启动方式，界面与协议行为无破坏性改动）
**提交**：见本仓库 `chore(release): v0.3.0`

## 这一版做了什么

一句话：**从"浏览器里的网页"变成"桌面上的一个程序"**——有图标、双击即开、独立窗口、关窗即退。

## 新增

| 内容 | 说明 |
|---|---|
| 桌面版 exe | `build.bat` 一键打包出 `dist\PanTransfer\PanTransfer.exe`（约 10 MB，含 Python 运行时） |
| 内嵌图标 | 蓝色圆角方块 + 向下箭头，多尺寸 ICO 直接写进 exe；任务栏、窗口、快捷方式共用 |
| 应用窗口 | 用系统 Edge/Chrome 的 `--app` 模式打开，无地址栏、无标签页、独立 profile，不碰日常浏览器 |
| 桌面/开始菜单快捷方式 | 打包后自动创建，图标取自 exe |
| 端口自动避让 | 8765 被占时自动往下找，选中端口记在 `data\current_port`，二次双击复用同一实例 |
| 单实例复用 | 已有实例在跑时只开窗口，不会起第二个服务、不会重复跑任务 |
| 便携数据目录 | 数据库、日志、浏览器 profile 全在 `<程序目录>\data\`，整个文件夹可拷走 |

## 修复

| 问题 | 原因 | 现在 |
|---|---|---|
| 打包后启动即退，什么都不显示 | 数据目录写在 `%LOCALAPPDATA%`，被系统/安全软件拒绝，`Store()` 抛 `PermissionError`，又被当成"端口被占用"重试到失败 | 数据目录改到程序旁边 `<exe目录>\data\`，失败原因写进 `data\launcher.log` |
| 提示"本地端口都被占用了" | 端口检测用 `SO_REUSEADDR`，Windows 上会把"别人的端口"判成空闲 | 改用 `SO_EXCLUSIVEADDRUSE`，并对 `/api/health` 校验 JSON 字段，认错程序就换端口 |
| 窗口里一片空白 | 启动早期版本没等页面加载就截图；另外未登录的新 profile 会弹 Edge 同步确认页 | 等 `/api/health` 应答后才开窗口，并加 `--disable-sync`、`--disable-extensions`、`--disable-features=EdgeSyncConsent` |
| 打包后的 exe 找不到界面文件 | 单文件模式下静态资源解压在临时目录 | `STATIC_DIR` 兼容 `sys._MEIPASS`，`--onedir` 打包 |

## 优化

- 用 `--onedir` 而不是 `--onefile`：启动更快，不依赖临时目录解压权限。
- 启动失败不再静默：日志写 `data\launcher.log`，无控制台模式下才弹一次提示框。

## 已知限制

- **Windows 专用打包脚本**：`build.bat` 只处理 Windows；macOS/Linux 仍可用 `python run.py` 直接跑。
- 桌面版依赖系统已安装 Edge 或 Chrome（Windows 10/11 自带 Edge，正常不会缺）。
- 百度 / 夸克两个网盘仍未在真实账号上跑过完整批量（UC 已验证 24/24）。

## 升级方式

1. 关掉正在运行的 PanTransfer 窗口；
2. 用新版本覆盖旧文件夹（`data\` 目录保留即可，数据库自动兼容）；
3. 双击 `build.bat` 重新打包，或直接用新的 `dist\PanTransfer\PanTransfer.exe`。

## 校验

```bash
python -m unittest discover -s tests    # 52 tests OK
python tools\e2e_check.py              # ✅ 全链路自检通过（假网盘，不联网）
```

桌面版手测：

```bash
dist\PanTransfer\PanTransfer.exe       # 应弹出独立窗口，地址栏不存在，任务栏有图标
```
