# 百度网盘协议规格（v0.2）

> 状态：**已实现，待实机校验**。下列字段来自公开接口行为的取证整理，实现代码在
> `app/providers/baidu.py`。凡是还没用真实账号跑过的行，都在 §9 里列着。

## §1 登录态

- 形式：浏览器 Cookie 字符串，最少包含 `BDUSS=`；带 `STOKEN=` 时可做更多操作。
- 客户端原样放进 `Cookie` 请求头，不做任何改写（`app/providers/_http.py`）。
- 失效判定：HTTP 401/403，或 JSON `errno` 属于登录态类错误（见 §8）。

## §2 请求头

| 头 | 值 |
| --- | --- |
| User-Agent | 桌面 Chrome UA（`_http.UA`） |
| Referer | `https://pan.baidu.com/` |
| Cookie | 用户自己的 Cookie |

公共查询参数：`channel=chunlei&web=1&app_id=250528&clienttype=0`。

## §3 分享元信息与提取码

1. 分享短链 `https://pan.baidu.com/s/<surl>`，`surl` 即分享 id。
2. `GET /api/shorturlinfo?shorturl=<surl>` → `{errno, shareid, uk}`。
   - `shareid`、`uk` 缺一即视为分享失效。
3. 有提取码时：`POST /share/verify?surl=<surl>&channel=chunlei&web=1&app_id=250528&clienttype=0`
   （表单 `pwd=<提取码>&vcode=&vcode_str=`）→ `{errno, randsk}`。
4. `sekey` = `randsk` 的 URL 解码值，后续转存必须带上。

提取码来源：链接里的 `?pwd=xxxx`、文本里的 `提取码: xxxx` / `密码：xxxx`，四位字母数字。

## §4 列目录

`GET /share/list?uk=&shareid=&order=other&desc=1&showempty=0&page=<n>&num=100&dir=<路径>&t=0&bdstoken=`

响应 `list[]` 字段映射到 `Entry`：

| 接口字段 | Entry | 说明 |
| --- | --- | --- |
| `server_filename` | `path` | 与父路径拼成完整路径 |
| `size` | `size` | 目录为 0 |
| `md5` | `digest` | 仅文件 |
| `fs_id` | `meta["fs_id"]` | 转存必需 |
| `isdir` | — | 1 时递归展开 |
| `path` | — | 子目录绝对路径，用于继续翻页 |

- 分页：返回条数 `< 100` 即最后一页。
- 递归：BFS，深度上限 8，总条目上限 5000。

## §5 转存

`POST /share/transfer?shareid=&from=<uk>&sekey=<sekey>&ondup=newcopy&async=1&channel=chunlei&web=1&app_id=250528&clienttype=0`

- 表单：`fsidlist=[<fs_id>,...]`（JSON 数组字符串）、`path=<目标目录绝对路径>`。
- **异步**：带 `async=1` 时成功只回 `{errno:0, taskid:"<id>", extra:{list:[]}}`，
  落点必须再查 §5.1 的任务；`extra.list` 为空且没有 taskid 时不能按路径猜（会把失败当成功）。
- 目标目录不存在时先建目录（§6）。

## §5.1 转存任务查询

`GET /share/taskquery?taskid=<id>&channel=chunlei&web=1&app_id=250528&clienttype=0`

- `{errno:0, status:2, extra:{list:[{from,to}]}}` → 完成，`extra.list[].to` 即落点。
- `status`：`0/1` 处理中（继续轮询，间隔 0.6s、上限 300s）、`2/3` 完成、`4/5` 失败。
- `errno` 非 0 时按 §8 表翻译（如 `31066` 任务不存在）。
- 实测 2026-11-06：不带 taskid 的同步回执在部分账号下始终是空 `extra.list`，
  因此一律走「拿 taskid → 轮询」这条路。

## §6 目录与容量

- 列目录：`GET /api/list?dir=<路径>&order=name&num=100&page=1`（同上公有参数）
- 建目录：`POST /api/create?a=commit`，表单 `path=<绝对路径>&isdir=1&block_list=[]&size=0`
- 容量：`GET /api/quota?checkfree=1&checkexpire=1`

## §7 去重

首版不做服务端去重：`ondup=newcopy` 表示重名自动生成副本，避免覆盖用户已有文件。
本地去重键为 `items(task_id, src)`，同一任务内同一源路径只转一次（断点续跑依赖它）。

## §8 错误码

| errno | 含义 | 处理 |
| --- | --- | --- |
| 0 | 成功 | — |
| 2 | 参数错误 | 直接失败，提示查规格 |
| -6 | 需要验证（风控） | 失败，提示降速后重试 |
| -7 | 文件名非法 | 失败 |
| -9 | 提取码错误 | 失败 |
| -62 | 分享已失效 | 失败 |
| 4 | 请求过于频繁 | 失败，建议调低 QPS |
| 12 | 文件已存在 | 视为成功（建目录时） |
| 111 | 空间不足 | 失败 |
| 112 | 转存数量超限 | 失败 |
| 31066 | 文件不存在 | 失败 |

未收录的码：原样带 `errno` 抛出，便于继续补表。

## §9 待实机校验

- [ ] `shorturlinfo` 在带提取码分享上是否直接返回 `randsk`（当前实现总是走 verify）。
- [ ] `share/list` 的 `dir` 是否需要与 `uk` 同源（跨账号转存场景）。
- [ ] 转存返回 `extra.list` 是否逐文件一一对应（已按一一对应实现）。
- [ ] `api/create` 对已存在目录返回 12 还是 0。
- [ ] 大目录（>1000 项）分页稳定性与 `t` 参数是否需要逐页变化。
- [ ] 风控触发条件与最小安全 QPS（当前默认 0.8）。
