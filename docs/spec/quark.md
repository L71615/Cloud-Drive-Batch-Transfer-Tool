# 夸克网盘协议规格（v0.2）

> 状态：**已实现，待实机校验**。实现见 `app/providers/_clouddrive.py`（共享）
> 与 `app/providers/quark.py`（常量/解析）。UC 与夸克同构但常量独立，见 `uc.md`。

## §1 登录态

- 形式：浏览器 Cookie 字符串（至少包含登录态字段，如 `__pus=`）。
- 直接放入 `Cookie` 头；HTTP 401/403 → 判定失效。

## §2 请求头

| 头 | 值 |
| --- | --- |
| User-Agent | 桌面 Chrome UA |
| Referer | `https://pan.quark.cn/` |
| Cookie | 用户自己的 Cookie |

公共查询参数：`pr=ucpro&fr=pc`。域名：`https://drive-pc.quark.cn`。

## §3 分享 token

`POST /1/clouddrive/share/sharepage/token?pr=ucpro&fr=pc`
JSON：`{"pwd_id": "<分享id>", "passcode": "<提取码或空>"}`
→ `{"status":0,"data":{"stoken":"..."}}`

- 分享 id 来自 `https://pan.quark.cn/s/<id>`（≥6 位字母数字）。
- 无 `stoken` ⇒ 提取码错误或分享失效。

## §4 列目录

`GET /1/clouddrive/share/sharepage/detail?pr=ucpro&fr=pc&pwd_id=&stoken=&pdir_fid=<目录fid>&force=0&_page=<n>&_size=50&_fetch_banner=0&_fetch_share=0&_fetch_total=1&_sort=file_type:asc,file_name:asc`

字段映射：

| 接口字段 | Entry | 说明 |
| --- | --- | --- |
| `file_name` | `path` | 拼父路径 |
| `size` | `size` | 目录为 0/缺省 |
| `fid` | `digest`、`meta["fid"]` | 转存必需 |
| `share_fid_token` | `meta["token"]` | 转存必需 |
| `dir` | — | true 时递归 |
| `metadata._total` | — | 分页总数 |

- 根目录 `pdir_fid=0`；每页 50，`page*50 >= _total` 结束。
- 递归深度上限 8，条目上限 5000。

## §5 转存

`POST /1/clouddrive/share/sharepage/save?pr=ucpro&fr=pc`

```json
{
  "fid_list": ["<fid>"],
  "fid_token_list": ["<share_fid_token>"],
  "to_pdir_fid": "<我的目标目录 fid>",
  "pwd_id": "<分享id>",
  "stoken": "<stoken>",
  "pdir_fid": "<这批文件在分享里的父目录 fid>",
  "scene": "link"
}
```

**关键约束（实测 2026-11-04）**：`pdir_fid` 必须与 `fid_list` 同源，即这批文件必须
都是 `pdir_fid` 这个目录的直接子项。全传 `"0"`（分享根）时，根目录的文件能成功，
子目录里的文件一律返回 `HTTP 403 {"message":"转存文件token校验异常[<token> token校验异常]"}`。
因此批量转存要**按父目录分组**，每组一次 save；`fid_token_list` 与 `fid_list` 必须一一对应。

→ `{"status":0,"data":{"task_id":"..."}}`；随后轮询任务：

`GET /1/clouddrive/task?pr=ucpro&fr=pc&task_id=<id>&retry_index=0`
→ `data.status`：`2` 成功、`3/4` 失败，其余视为进行中；轮询间隔 0.5s，上限 120s。

### §5.1 删除（"覆盖同名"用）

`POST /1/clouddrive/file/delete?pr=ucpro&fr=pc`

```json
{"action_type": 2, "file_list": ["<fid>"], "exclude_fids": []}
```

- `action_type=2` 进回收站（可恢复），`1` 是彻底删除——只用 2。
- 返回 `data.task_id`，非空时按上面的任务接口等它完成。
- 要先按名字 + 父目录找到 fid，所以删除的粒度是"单个文件"。
- **百度没有等价实现**：`/api/filemanager` 需要 bdstoken，当前未采集，界面上「覆盖」
  对百度会明确报错，请用「跳过」或「保留副本」。

## §6 我的目录 / 容量

- 列目录：`GET /1/clouddrive/file/sort?pdir_fid=<fid>&_page=1&_size=200&_fetch_total=1&_sort=file_type:asc,file_name:asc`
- 建目录：`POST /1/clouddrive/file`，JSON `{"pdir_fid":"<父fid>","file_name":"<名字>","dir_path":"","dir_init_lock":false}` → `data.fid`
- 容量：`GET /1/clouddrive/member` → `data.total_capacity`、`data.use_capacity`

目标路径 `/a/b/c` 逐级 `ensure_dir`：先在本层按名字找目录，找不到才创建；同进程内缓存 fid。

## §7 去重

同百度：不做服务端去重，依赖本地 `items(task_id, src)` 唯一约束 + 断点续跑。

## §8 状态码

| status | 含义 | 处理 |
| --- | --- | --- |
| 0 | 成功 | — |
| 41004 | 分享不存在 | 失败 |
| 41005 | 提取码错误 | 失败 |
| 41006 | stoken 无效/过期 | 失败，重取 token 后可重试 |
| 31001 / 31023 | 登录态问题 | 判为失效（AuthExpired） |

`status` 非 0 一律抛 `ProviderError`，消息里带 `status` 与 `code`。

## §9 待实机校验

- [ ] `save` 是否支持一次提交多个 fid（当前实现按文件逐个提交，最稳但最慢）。
- [ ] `task` 的 `status` 全集取值（是否还有 `5`/`6` 等中间态）。
- [ ] `file/sort` 的 `_size` 上限（当前取 200）。
- [ ] 目录创建接口是否对同名目录返回错误码而非复用。
- [ ] 转存后的落盘文件名规则（重名是否自动加 `(1)`）。
