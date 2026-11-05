# 提交与发版约定

这份文档回答两个问题：**每次提交到 GitHub 要写什么**，**版本号什么时候改、怎么改**。

---

## 一、提交信息怎么写

格式固定为一行标题 + 可选正文：

```
<类型>(<范围>): <一句话说明做了什么>

<为什么这么做 / 有什么影响，可省略>
```

### 类型（只用这几个）

| 类型 | 什么时候用 | 例子 |
|---|---|---|
| `feat` | 用户能感知的新能力 | `feat(ui): 新增网盘管理页` |
| `fix` | 修掉一个错误 | `fix(uc): 转存时 pdir_fid 用条目自己的父目录` |
| `docs` | 只改文档 | `docs: 补登录态获取说明` |
| `refactor` | 改结构但不改行为 | `refactor(tasks): 拆出 flush_batch` |
| `perf` | 明显更快 | `perf(tasks): 判重改为启动时扫一次目标目录` |
| `ui` | 只改样式/布局 | `ui: 主题改为浅色` |
| `test` | 只加/改测试 | `test: 补夸克混目录用例` |
| `chore` | 杂事（依赖、构建、发版） | `chore(release): v0.2.0` |

### 范围（括号里）

用受影响的模块名，和目录对得上：`baidu` `quark` `uc` `tasks` `ui` `api` `db` `rate` `docs`。不确定就省略括号。

### 三条硬规则

1. **一句话说清"改了什么"，不是"改了代码"**
   - 好：`fix(uc): 批量转存时按父目录分批，修子目录 403`
   - 差：`update`、`修改代码`、`fix bug`、`优化`、`1`
2. **标题不超过 50 个字符**，详情放正文（正文每行不超过 72 字符）
3. **不要在标题里用中文全角引号《》“”‘’**：PowerShell 会把它当路径分隔符，直接报
   `error: pathspec '...' did not match any file(s)`。要用引号就写单引号 `'...'` 或干脆不用。

### 一次提交只做一件事

- 改协议 → 规格（`docs/spec/`）和代码**放同一个提交**；
- 顺手改的无关格式、重命名，单独一个 `refactor` / `style` 提交；
- 发版提交只改版本号和 CHANGELOG，不夹带代码。

### 正文什么时候写

改动不明显、或需要解释"为什么"时写正文，例如：

```
fix(uc): 目标目录同名文件不再重复转存

之前按"文件名+大小"判重，把不同分享里的同名文件当成已存在，
导致 34 个文件记成 done 但网盘里没有。改为按完整路径判重。
```

---

## 二、版本号怎么定

用语义化版本 `MAJOR.MINOR.PATCH`，当前处于 `0.x` 阶段：

| 位 | 什么时候 +1 | 例子 |
|---|---|---|
| `PATCH`（第三位） | 只修 bug、改文档、调整样式，**不加新东西** | `0.2.0 → 0.2.1` |
| `MINOR`（第二位） | 加了功能 / 改了界面结构 / 新增网盘支持 | `0.2.0 → 0.3.0` |
| `MAJOR`（第一位） | 协议或数据格式不兼容（老库需要迁移、API 破坏性变更） | `0.x → 1.0.0` |

正式 `1.0.0` 的判定：**三个网盘都用真实账号跑通一次完整批量转存**。在那之前一直是 `0.x`。

### 版本号存在两个地方，必须同时改

```
app/__init__.py     __version__ = "0.2.0"
pyproject.toml      version = "0.2.0"
```

界面左下角、`/api/health`、`/api/meta` 显示的版本都来自 `app/__init__.py`，不需要别处再改。
两处不一致时以 `app/__init__.py` 为准（它才是运行时读的）。

---

## 三、发一个版本的完整步骤

以 `0.2.0 → 0.2.1` 为例：

```bash
cd E:\设备转存软件\pantransfer

# 1. 确认工作区干净、测试全过
git status
python -m unittest discover -s tests

# 2. 改两处版本号
#    app/__init__.py      __version__ = "0.2.1"
#    pyproject.toml       version = "0.2.1"

# 3. 写 RELEASE-NOTES-v0.2.1.md，在 CHANGELOG.md 顶部加一节，并更新两者的「当前版本」行

# 4. 提交（只含版本号、CHANGELOG、发版说明）
git add -A
git commit -m "chore(release): v0.2.1"
git tag v0.2.1

# 5. 推上去，标签一起推
git push origin main
git push origin v0.2.1
```

### README 版头格式（每个版本都要更新）

README 最顶部保持这个结构，三行状态块 + 五个徽章 + 一行链接：

```markdown
# 项目名

### 一句话定位 · A / B / C

> ✅ **当前版本**：`v0.2.0` (2026-11-05) — 这一版主要做了什么
> **上一版本**：`v0.1.0` (2026-11-04) — 上一版做了什么
> 详见 [RELEASE-NOTES-v0.2.0.md](RELEASE-NOTES-v0.2.0.md) + [CHANGELOG.md](CHANGELOG.md)

**一句话能力罗列 · 动作 → 动作 → 结果**

![version](https://img.shields.io/badge/version-0.2.0-2b7cff)
![python](https://img.shields.io/badge/python-3.10%2B-3776ab)
![license](https://img.shields.io/badge/license-MIT-18a058)

[中文](#项目名) · [文档导航](#文档) · [更新日志](CHANGELOG.md) · [发版说明](docs/release.md)
```

要点：

- 「当前版本」那行**必须带日期**和一句“这版做了什么”，不能只写版本号；
- 上一版本也要写，方便回退时知道前一版是什么；
- 徽章里 `version` 必须和 `app/__init__.py` 一致，发版时一起改；
- 徽章用 shields.io 静态徽章，不引入 CI 构建状态（避免显示失败），颜色沿用：版本 `2b7cff`、Python `3776ab`、通过类 `18a058`、警告类 `e5484d`。

### RELEASE-NOTES 写法

每个版本一个文件 `RELEASE-NOTES-vX.Y.Z.md`，固定小节：

```markdown
# v0.2.1 发版说明

**发布日期**：2026-11-06
**对比版本**：`v0.2.0` → `v0.2.1`（PATCH：只修问题）
**提交**：`chore(release): v0.2.1`

## 这一版做了什么        ← 一句话 + 说明这版的主题
## 新增 / 修复 / 优化     ← 表格：问题 | 原因 | 现在怎么样
## 已知限制               ← 还没验证到的部分，写清楚
## 升级方式               ← 用户要做什么（通常只是重启）
## 校验                   ← 跑过的命令与结果
```

「修复」用三列表格（问题 / 原因 / 现在），比只写“修了 xxx”有用：以后再看能知道当时为什么错。

### CHANGELOG 写法

`CHANGELOG.md` 只放条目，最新版本在最上面，开头三行写当前版本与上一版本：

```markdown
> ✅ **当前版本**：`v0.2.1` (2026-11-06) — 修 UC 子目录偶发 403
> 上一版本：`v0.2.0` (2026-11-05) — 浅色界面改版 + 网盘管理页

## v0.2.1 — 2026-11-06

### 修复
- UC 转存子目录文件时偶发 403
```

分类只用：`新增` / `修复` / `优化` / `不兼容` / `其它`。没有内容的小节直接不写。

---

## 四、推不上去时按这个顺序查

| 现象 | 原因 | 解决 |
|---|---|---|
| `Everything up-to-date` | 本地没有新提交，或已经推过了 | 先 `git status` 看有没有未提交改动 |
| `rejected ... non-fast-forward` | 远端有你本地没有的提交（网页上改过东西） | `git pull --rebase origin main` 再推 |
| `Authentication failed` / `could not read Username` | 凭据过期或被清 | `git config --global credential.helper manager`，推一次会弹窗登录 GitHub |
| `fatal: not a git repository` | 命令跑在父目录了 | 必须 `cd E:\设备转存软件\pantransfer`，仓库根在这里 |
| `error: pathspec '...' did not match` | 提交信息里带了全角引号 | 改写提交信息，用单引号或不带引号 |
| `LF will be replaced by CRLF` | Windows 换行提示 | 忽略，不是错误 |
| 命令显示 `[exit code: 1]` 但输出是 `main -> main` | PowerShell 把 git 的 stderr 当错误 | 忽略，看输出最后一行有没有 `main -> main` |

**判断推送成功只认一行**：

```
xxxxxxx..yyyyyyy  main -> main
```

或执行 `git ls-remote origin main`，对比它和 `git rev-parse HEAD` 是不是同一个哈希。

---

## 五、每次提交前的最小检查

```bash
cd E:\设备转存软件\pantransfer
python -m unittest discover -s tests     # 必须全过
git status                               # 确认没有 data/、*.db、work/ 被加进来
git diff --cached --stat                 # 看清这次到底改了什么
```

`.gitignore` 已经挡住 `data/`、`*.db`、`work/`、`orig/`、`*.exe`、`*.log`。
如果 `git status` 里出现了这些，先 `git rm --cached <文件>` 再从 `.gitignore` 补规则，别直接提交。
