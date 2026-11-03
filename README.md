# PanTransfer

本地单机批量转存工具：把别人分享的 **百度网盘 / 夸克 / UC** 分享链接，批量转存到你自己的网盘目录。

- 无账号体系、无登录墙、无会员限制、无遥测 —— 只是个跑在 `127.0.0.1` 的本地服务。
- 只用 **Python 标准库**，零第三方依赖，`python run.py` 即跑。
- 所有网络请求都用**你自己的** Cookie / 登录态，不内置任何 AppKey、密钥或代登录服务。
- 非官方作品，与百度、夸克、UC 及其关联公司无任何关系。

## 快速开始

```bash
python run.py --open          # 打开 http://127.0.0.1:8765/
```

1. 在「账号」里保存某个网盘的 Cookie（或直接在建任务时填）。
2. 粘贴分享链接（每行一条，可带 `提取码`），选目标目录。
3. 点「开始转存」，任务页看进度；支持暂停 / 继续 / 取消，进程重启后自动续跑。

没有真实账号也能先跑通全链路：`provider` 选 `mock`。

```bash
python run.py --port 8765 --db data/pantransfer.db --workers 2
python -m unittest discover -s tests
```

## 当前能力

| provider | 链接解析 | 列目录 | 转存 | 状态 |
|---|---|---|---|---|
| `mock` | ✅ | ✅ | ✅ | 完整可用，用于开发与 CI |
| `baidu` | ✅ | 待填规格 | 待填规格 | 骨架 |
| `quark` | ✅ | 待填规格 | 待填规格 | 骨架 |
| `uc` | ✅ | 待填规格 | 待填规格 | 骨架 |

三个真实适配器的端点集中写在各自模块的 `API` 字典里，为空即抛
`ProviderNotReady` 并指出缺哪一节规格。填法见 [docs/spec/](docs/spec/)。

## 架构

```
run.py                 启动入口（argparse + serve_forever）
app/http_api.py        JSON API + 内置单页 UI，绑定 127.0.0.1
app/tasks.py           任务状态机 + 线程池 + 重试 + 断点续跑
app/db.py              SQLite 仓储（accounts / tasks / items）
app/ratelimit.py       令牌桶，按 provider 限速
app/providers/base.py  Provider 契约（parse_links / list_share / transfer）
app/providers/        baidu · quark · uc · mock
app/static/index.html  单页界面，无构建步骤
docs/spec/             协议规格（自然语言，实现只照这里写）
```

任务状态：`pending → resolving → listing → transferring → verifying → done`，
失败为 `failed`，用户中断为 `paused / canceled`；`paused` 不会被子线程覆盖。

## HTTP API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 版本与 provider 能力 |
| GET/POST | `/api/accounts` | 列出 / 保存登录态 |
| DELETE | `/api/accounts/{id}` | 删除登录态 |
| POST | `/api/parse` | 只解析链接，不联网 |
| GET/POST | `/api/tasks` | 任务列表 / 新建任务 |
| GET | `/api/tasks/{id}` | 任务详情（含逐文件结果） |
| POST | `/api/tasks/{id}/pause\|resume\|cancel` | 控制任务 |

## 明确不做

挂载盘、WebDAV、微信/QQ 机器人、上传下载、分享外链生成、分销/邀请、
强更、云端同步、任何形式的会员绕过。

## 关于"独立实现"

本项目是**洁净室重写**：协议事实由 `docs/spec/` 用自然语言描述，代码侧只看规格、
不照抄任何既有软件的实现。仓库内不含任何第三方二进制、补丁、反汇编产物、
私钥、商标或内部接口密钥。详见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## License

MIT，见 [LICENSE](LICENSE)。使用前请自行确认你的行为符合各网盘的服务条款。
