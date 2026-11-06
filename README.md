# PanTransfer

### 本地网盘批量转存 · 百度 / 夸克 / UC

> ✅ **当前版本**：`v0.5.0` (2026-11-06) — 文件名与目录规范化：坏名字只影响它自己
> **上一版本**：`v0.4.0` (2026-11-06) — 百度异步转存落点回填 + 同名文件三档策略
> 详见 [RELEASE-NOTES-v0.5.0.md](RELEASE-NOTES-v0.5.0.md) + [CHANGELOG.md](CHANGELOG.md)

**粘贴分享链接 → 自动读全目录 → 批量转存进自己的网盘 · 任务进度逐条可见 · 断点续跑 · 同名跳过/留副本/覆盖**

![version](https://img.shields.io/badge/version-0.5.0-2b7cff)
![python](https://img.shields.io/badge/python-3.10%2B-3776ab)
![deps](https://img.shields.io/badge/dependencies-0-18a058)
![license](https://img.shields.io/badge/license-MIT-18a058)
![platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-555)

[中文](#pantransfer) · [文档导航](#文档) · [更新日志](CHANGELOG.md) · [发版说明](docs/release.md)

---

把别人分享的链接，批量转存到你自己的网盘目录。
跑在本机 `127.0.0.1` 上的单页应用：侧边导航、任务进度、逐文件结果、账号管理、引擎设置。

- 无账号体系、无登录墙、无会员限制、无遥测。
- 只用 **Python 标准库**，零第三方依赖，`python run.py` 即跑。
- 所有请求都用**你自己的** Cookie / 登录态，不内置任何 AppKey、密钥或代登录服务。
- 非官方作品，与百度、夸克、UC 及其关联公司无任何关系。

## 桌面版（双击即开）

不想看到浏览器地址栏，就把程序打成桌面软件：

```bash
build.bat          # 双击：打包成 dist\PanTransfer\PanTransfer.exe，并创建桌面快捷方式
```

之后桌面上就有 **PanTransfer 图标**，双击直接弹出独立窗口（无地址栏、无标签页、任务栏有图标），关掉窗口服务自动退出。

细节：

- 图标是内嵌在 exe 里的，窗口标题栏/任务栏用的同一张；图标由 [tools/make_icon.py](tools/make_icon.py) 用标准库现画（不依赖任何图片素材）。
- 窗口是用系统 Edge/Chrome 的 `--app` 模式打开的，**独立 profile**，不碰你日常浏览器的书签和登录态。
- 数据和日志都在 `<程序目录>\data\`（整个文件夹可以拷到 U 盘带走）；想换地方就挪文件夹。
- 端口默认 8765，被别的程序占了会自动往下找，并且把选中的端口记在 `data\current_port`；第二次双击会复用已开的实例，不会起两个。
- 已经打包好但快捷方式丢了：双击 [make_shortcut.bat](make_shortcut.bat) 重建。
- 极少数机器上应用窗口会打不开（浏览器渲染异常）：程序会在 25 秒后自动关掉那个空窗口，改用默认浏览器打开同一界面，功能完全一样，只是多了地址栏；这种模式下 30 分钟没人操作会自动退出，不会留后台进程。

## 快速开始

```bash
python run.py --open          # 启动并打开 http://127.0.0.1:8765/
```

Windows 也可以双击 [start.bat](start.bat)。

界面上四步：

1. **账号管理** → 保存某个网盘的 Cookie（或建任务时临时填）。不会取 Cookie？看 **[docs/login.md](docs/login.md)**，账号页里也内置了对应步骤。
2. **新建转存** → 选网盘卡片，粘贴分享链接（每行一条，可带 `提取码`），点「浏览…」选目标目录。
3. 点「开始转存」，到 **转存任务** 看进度；支持暂停 / 继续 / 取消 / 重试 / 删除。
4. 点任务行展开详情，可看每个文件的转存结果与失败原因。

没有真实账号也能先跑通全链路：网盘选 **演示模式（mock）**，随便粘贴几行字即可。

## 扫码登录（推荐）

「账号管理 → 扫码登录 → 打开登录窗口并扫码」：

1. 工具用系统自带 Edge 开一个**独立 profile 的窗口**，直接打开官方登录页；
2. 你在那个窗口里扫码（或输账号密码、过验证码），全部由官方页面完成；
3. 登录成功后，工具通过 Chrome DevTools Protocol 读取该域名下的 Cookie，校验后自动存进账号列表并关掉窗口。

实现上不碰任何登录协议、不需要 AppKey、不需要开放平台资质：二维码是官方页面自己画的，我们只做"读 Cookie"这一步（`app/browser_login.py`，纯标准库手写的 WebSocket 客户端）。因此官方改版也不会让我们失效——除非它整个改掉网页登录。

也可以走手动路线（没有 Edge / 想用别的浏览器）：按 **[docs/login.md](docs/login.md)** 复制 Cookie 粘贴进来，同样是自动清洗 + 校验。

## 能力矩阵

| 网盘 | 链接解析 | 提取码 | 递归列目录 | 转存 | 容量查询 | 状态 |
|---|---|---|---|---|---|---|
| 百度网盘 | ✅ | ✅ | ✅ | ✅ | ✅ | 已实现，待实机校验 |
| 夸克网盘 | ✅ | ✅ | ✅ | ✅ | ✅ | 已实现，待实机校验 |
| UC 网盘 | ✅ | ✅ | ✅ | ✅ | ✅ | 已实现，待实机校验 |
| 演示模式 | ✅ | — | ✅ | ✅ | ✅ | 完整可用，用于开发与 CI |

"待实机校验"的意思是：代码路径已被本地假网盘完整验证过（解析、翻页、目录创建、
异步任务轮询、错误码翻译），但还没用真实账号跑过；`docs/spec/*.md` 的 §9 列出了
每条待确认项。真实使用时若报错，先看规格 §8 的错误码表。

## 离线自检（不碰真实网盘）

```bash
python -m unittest discover -s tests     # 52 个用例，含适配器链路、链接解析、登录态、限速
python tools/e2e_check.py                # 假网盘 + 真 HTTP 服务 + 真任务引擎，全链路
```

也可以手工联调：

```bash
python tools/mock_netdisk.py --port 8899
python run.py --port 8765 --gateway http://127.0.0.1:8899
# 百度链接 https://pan.baidu.com/s/1AbCdEf  提取码 x9y8   Cookie 随便给个含 BDUSS= 的串
```

`--gateway` 只把三个 provider 的 BASE 指到本地假网盘，真实使用不要加这个参数。

## 进度语义（`done/total` 是什么）

- `total` 是**分享内已发现的文件数**，不是链接条数。一条 uc/夸克 分享链接指向的往往是一整个目录，
  里面有几百上千个文件，所以 `total` 会随着目录被展开而不断增长——这是正常的。
- 任务是**两段式**：先只读不写地把所有分享的目录展开完（状态 `读取目录`，进度 `已发现 N 个文件`），
  再逐条转存（状态 `转存中`，每成功一个界面 `+1`）。这样进度条的分母一开始就是确定的，
  而且任务详情里能立刻看到完整清单。
- 一批里的文件必须来自**同一条分享**（`pwd_id`/`stoken`/`fid_token` 都是分享级的，混批会整批 403），
  所以流水线遇到分享切换就切批；每批 `BATCH_SIZE=20`，一次 save 提交多个 `fid`。
- 硬边界：单条分享最多 5000 个文件 / 8 层目录深度 / 30 分钟处理时间；目标目录判重最多看 4 层、20000 个文件。
  超过会明确报错，不会静默少转。
- 速度**自动调速**：设置里的 QPS 只是上限（默认 2.5，最高 10）。被限速（429 或"请求过于频繁"）会自动减半，
  连续顺利会自动回升，页面实时显示每个网盘当前速率。

## 文档

| 文档 | 内容 |
|---|---|
| [docs/login.md](docs/login.md) | 三个网盘分别怎么拿到 Cookie、有效期与失效表现、三种录入方式的区别、安全说明 |
| [docs/spec/README.md](docs/spec/README.md) | 协议规格的写法纪律（实现只照规格写）与总索引 |
| [docs/spec/baidu.md](docs/spec/baidu.md) | 百度网盘：登录态、请求头、`shorturlinfo`/`verify`+`sekey`、`share/list`、`share/transfer`、`api/list`·`api/create`·`api/quota`、errno 表 |
| [docs/spec/quark.md](docs/spec/quark.md) | 夸克网盘：`sharepage/token`·`detail`·`save`、`task` 轮询、`file/sort`、`member`、状态码 |
| [docs/spec/uc.md](docs/spec/uc.md) | UC 网盘：与夸克同构但端点独立；`pdir_fid` 必须与 `fid_list` 同源等实测约束 |
| [docs/release.md](docs/release.md) | 提交信息怎么写、一次提交的范围、版本号怎么定、发版五步、推送失败排查 |
| [CHANGELOG.md](CHANGELOG.md) | 每个版本改了什么（按 `新增`/`修复`/`优化`/`不兼容` 分类） |
| [CONTRIBUTING.md](CONTRIBUTING.md) | 洁净室规则、只用标准库、QPS 纪律、提交前检查 |
| [LICENSE](LICENSE) | MIT |

每份规格的 §9 列出"还没用真实账号确认"的项，§8 是错误码表——真机上遇到报错先查这两节。

## 架构

```
run.py                  启动入口（argparse + serve_forever + --gateway）
app/http_api.py         JSON API + 静态资源，绑定 127.0.0.1
app/tasks.py            任务状态机 + 并发闸门 + 重试 + 断点续跑 + 引擎设置
app/db.py               SQLite 仓储（accounts / tasks / items / settings）
app/ratelimit.py        令牌桶，按 provider 限速
app/providers/base.py   Provider 契约（parse_links / list_share / transfer / quota）
app/providers/_http.py  共享 HTTP 客户端（Cookie / 超时 / 重试 / 限速）
app/providers/_clouddrive.py  夸克与 UC 共享的云盘流程
app/providers/          baidu · quark · uc · mock
app/static/             单页界面（index.html / app.js / app.css），无构建步骤
docs/spec/              协议规格（自然语言；实现只照这里写）
tools/                  假网盘、端到端自检
```

任务状态机：`pending → resolving → listing → transferring → verifying → done`，
失败为 `failed`，用户中断为 `paused / canceled`；`paused` 不会被子线程覆盖，
恢复后按 `items` 表已完成的记录续跑，进程重启也一样。

## HTTP API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 版本、provider 能力 |
| GET | `/api/meta` | 网盘元信息（中文名、颜色、就绪状态） |
| GET | `/api/stats` | 运行中/完成/失败任务数、文件总数 |
| GET/POST | `/api/accounts` | 列出 / 保存登录态 |
| POST | `/api/accounts/{id}/check` | 检测登录态是否有效 |
| POST | `/api/accounts/validate` | 只校验 Cookie 格式（不联网、不落库） |
| POST | `/api/login/browser` | 打开代登录窗口，返回会话 id |
| GET | `/api/login/browser/{sid}` | 轮询登录状态，`ready` 时带捕获到的 Cookie |
| POST | `/api/login/browser/save` | 把该会话的登录态存进账号列表并关窗 |
| DELETE | `/api/login/browser/{sid}` | 放弃登录，关闭窗口 |
| DELETE | `/api/accounts/{id}` | 删除登录态 |
| GET/POST | `/api/settings` | 引擎设置（并发 / QPS / 重试 / 默认目录） |
| POST | `/api/parse` | 只解析链接，不联网 |
| GET | `/api/browse?provider=&path=` | 浏览自己网盘的目录（界面「浏览…」用） |
| POST | `/api/diag` | 协议诊断：按步骤跑最小链路，返回每步结果 + 原始响应 |
| GET/POST | `/api/tasks` | 任务列表 / 新建任务 |
| GET | `/api/tasks/{id}` | 任务详情（含逐文件结果） |
| POST | `/api/tasks/{id}/pause\|resume\|cancel\|retry` | 控制任务 |
| DELETE | `/api/tasks/{id}` | 删除任务及其记录 |

## 协议诊断（验证真实账号用）

左侧「协议诊断」页把一次完整链路拆成可观察的步骤，逐步列出结果、耗时与错误，
并把每一步的原始 HTTP 响应原样贴在下方（`/api/diag`）：

| 网盘 | 诊断步骤 |
|---|---|
| 百度网盘 | 解析链接 → 分享信息 / 提取码校验 → 列分享根目录（第一页）→ 查询我的容量 |
| 夸克 / UC | 解析链接 → 分享 token → 列分享根目录（第一页）→ 查询我的容量 |

它是**只读**的：不会建目录、不会转存、不改任何数据——失败某一步也不会中断后面的步骤，
所以一次点击就能看清"到底是登录态、提取码、还是接口字段的问题"。

另外「新建转存」里的**浏览…**按钮走 `/api/browse`，直接列出你自己网盘的目录树，
选中的路径就是转存目标，不用再手打路径。

## 明确不做

挂载盘、WebDAV、微信/QQ 机器人、上传下载、分享外链生成、分销/邀请、强更、
云端同步、任何形式的会员或授权绕过。

## License

MIT，见 [LICENSE](LICENSE)。使用前请自行确认你的行为符合各网盘的服务条款。
