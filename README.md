# PanTransfer · 本地网盘批量转存

把别人分享的 **百度网盘 / 夸克网盘 / UC 网盘** 链接，批量转存到你自己的网盘目录。
跑在本机 `127.0.0.1` 上的单页应用：侧边导航、任务进度、逐文件结果、账号管理、引擎设置。

- 无账号体系、无登录墙、无会员限制、无遥测。
- 只用 **Python 标准库**，零第三方依赖，`python run.py` 即跑。
- 所有请求都用**你自己的** Cookie / 登录态，不内置任何 AppKey、密钥或代登录服务。
- 非官方作品，与百度、夸克、UC 及其关联公司无任何关系。

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
python -m unittest discover -s tests     # 17 个用例，含 8 个适配器链路用例
python tools/e2e_check.py                # 假网盘 + 真 HTTP 服务 + 真任务引擎，全链路
```

也可以手工联调：

```bash
python tools/mock_netdisk.py --port 8899
python run.py --port 8765 --gateway http://127.0.0.1:8899
# 百度链接 https://pan.baidu.com/s/1AbCdEf  提取码 x9y8   Cookie 随便给个含 BDUSS= 的串
```

`--gateway` 只把三个 provider 的 BASE 指到本地假网盘，真实使用不要加这个参数。

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

## 关于"独立实现"

本项目是**洁净室重写**：协议事实由 [`docs/spec/`](docs/spec/) 用自然语言描述，
代码侧只看规格、不照抄任何既有软件的实现。仓库内不含任何第三方二进制、补丁、
反汇编产物、私钥、商标或内部接口密钥。规则见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## License

MIT，见 [LICENSE](LICENSE)。使用前请自行确认你的行为符合各网盘的服务条款。
