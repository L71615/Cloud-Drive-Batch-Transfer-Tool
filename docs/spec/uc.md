# UC 网盘协议规格（v0.2）

> 状态：**已实现，待实机校验**。实现见 `app/providers/_clouddrive.py`（共享骨架）
> 与 `app/providers/uc.py`（常量/解析）。
>
> ⚠️ UC 与夸克接口形态相近，但域名、`pr` 参数、部分字段口径并不相同。
> 本文件里的常量以 UC 自己的抓包为准，不要因为"看着像"就复用夸克的值。

## §1 登录态

- 形式：浏览器 Cookie 字符串，放进 `Cookie` 头。
- 失效判定：HTTP 401/403，或 `status/code` 属于登录态类（见 §8）。

## §2 请求头

| 头 | 值 |
| --- | --- |
| User-Agent | 桌面 Chrome UA |
| Referer | `https://drive.uc.cn/` |
| Cookie | 用户自己的 Cookie |

公共参数：`pr=UCBrowser&fr=pc`。域名：`https://pc-api.uc.cn`。

## §3 分享 token

`POST /1/clouddrive/share/sharepage/token?pr=UCBrowser&fr=pc`
JSON：`{"pwd_id":"<分享id>","passcode":"<提取码或空>"}` → `{"status":0,"data":{"stoken":"..."}}`

分享链接形态：`https://drive.uc.cn/s/<id>`、`https://fast.uc.cn/s/<id>`，两者等价，
解析时去重。

## §4 列目录

`GET /1/clouddrive/share/sharepage/detail?pr=UCBrowser&fr=pc&pwd_id=&stoken=&pdir_fid=<fid>&force=0&_page=<n>&_size=50&_fetch_total=1`

字段映射（与夸克同名，但**以实测为准**）：

| 接口字段 | Entry | 说明 |
| --- | --- | --- |
| `file_name` | `path` | 拼父路径 |
| `size` | `size` | 目录为 0 |
| `fid` | `meta["fid"]`、`digest` | 转存必需 |
| `share_fid_token` | `meta["token"]` | 转存必需 |
| `dir` | — | true 递归 |
| `metadata._total` | — | 总条数 |

分页 50/页，深度上限 8，条目上限 5000。

## §5 转存

`POST /1/clouddrive/share/sharepage/save?pr=UCBrowser&fr=pc`
JSON：`fid_list` / `fid_token_list` / `to_pdir_fid` / `pwd_id` / `stoken` / `pdir_fid` / 分页与排序参数
→ `data.task_id`；再轮询 `GET /1/clouddrive/task?task_id=&retry_index=0`，`data.status==2` 为成功。

## §6 我的目录 / 容量

- 列：`GET /1/clouddrive/file/sort?pdir_fid=&_page=1&_size=200&_fetch_total=1`
- 建目录：`POST /1/clouddrive/file`，`{"pdir_fid","file_name","dir_path","dir_init_lock":false}` → `data.fid`
- 容量：`GET /1/clouddrive/member` → `data.total_capacity` / `data.use_capacity`

## §7 去重

同夸克：不做服务端去重，本地 `items(task_id, src)` 唯一约束保证一个任务内不重复转存。

## §8 状态码

| status | 含义 | 处理 |
| --- | --- | --- |
| 0 | 成功 | — |
| 41004 | 分享不存在 | 失败 |
| 41005 | 提取码错误 | 失败 |
| 41006 | stoken 无效 | 失败（可重取 token 重试） |
| 31001 / 31023 | 登录态问题 | 视为失效 |

## §9 待实机校验

- [ ] UC 的 `pr` 是否必须为 `UCBrowser`，`pc-api.uc.cn` 与 `drive.uc.cn` 前端用的域名是否一致。
- [ ] `detail` 返回的 `share_fid_token` 在 UC 是否同样必需。
- [ ] `save` 的 `to_pdir_fid` 是否接受与非根目录同层的新建 fid。
- [ ] `task` 状态全集（是否出现夸克没有的中间态）。
- [ ] UC 分享在未登录状态下是否仍可读 token（影响"只转存不登录"的边界）。
