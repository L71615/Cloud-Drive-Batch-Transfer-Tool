# -*- coding: utf-8 -*-
"""文件名与目录路径的规范化。

两件事：
1. 用户输入的转存目录要规整（`/a//b/` → `/a/b`），否则建目录与落点判断会打架；
2. 分享里的文件名可能超长或带非法字符，这类文件在网盘那边必定失败，
   提前判掉比让整批 403 更好——一批 20 个文件里混进一个坏名字，
   整批会一起失败，用户只看到"转存失败"却不知道是哪个名字的问题。

只在本地做判断，不改动网盘上的任何东西：真名以服务端返回为准。
"""
from __future__ import annotations

# 网盘对单个文件名的长度上限（字符数）。夸克/UC 与百度都是 255，
# 留 5 个字符余量给服务端的 "(1)" 去重后缀。
DEFAULT_NAME_LIMIT = 250

ILLEGAL = set('<>:"/\\|?*')
RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {
    f"LPT{i}" for i in range(1, 10)
}


def clean_segment(name: str) -> str:
    """把一个文件名片段收拾成"能落在 Windows/网盘上"的样子。"""
    s = str(name or "")
    s = "".join(ch for ch in s if ch >= " " and ch != "\x7f")   # 控制字符
    s = s.replace("/", "_").replace("\\", "_")
    s = "".join("_" if ch in ILLEGAL else ch for ch in s)
    s = s.strip().rstrip(". ")                                  # 结尾的点和空格
    if s.split(".")[0].upper() in RESERVED:
        s = "_" + s
    return s or "_"


def split_ext(name: str) -> tuple[str, str]:
    """把扩展名单独切出来；"." 开头的隐藏文件名不当扩展名。"""
    s = str(name or "")
    i = s.rfind(".")
    if i <= 0 or i == len(s) - 1:
        return s, ""
    return s[:i], s[i:]


def limit_name(name: str, limit: int = DEFAULT_NAME_LIMIT) -> str:
    """超长就截断，但保留扩展名——不然 xxx.mp4 变成 xxx.m 就没法播放了。"""
    s = str(name or "")
    if len(s) <= limit:
        return s
    stem, ext = split_ext(s)
    keep = max(1, limit - len(ext))
    return stem[:keep] + ext


def normalize_dir(path: str) -> str:
    """规整转存目录：补前导 `/`、压掉重复 `/`、去掉结尾 `/`、拒绝 `..`。

    `/` 表示根目录；空串按 `/PanTransfer` 处理（与其他地方的默认值一致）。
    """
    s = str(path or "").strip().replace("\\", "/")
    if not s:
        return "/PanTransfer"
    parts = [p for p in s.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise ValueError("转存目录不能包含 ..")
    if not parts:
        return "/"
    return "/" + "/".join(clean_segment(p) for p in parts)


def name_problem(name: str, limit: int = DEFAULT_NAME_LIMIT) -> str:
    """检查分享里的文件名能不能落盘；没问题返回空串。

    只判"一定失败"的情况（空名、超长）。**不判非法字符**：网盘侧的规则比本地宽，
    夸克/UC 上存在带 `:` `?` 的名字，按本地 Windows 规则去拦会把能转的文件判死。
    """
    s = str(name or "")
    if not s.strip():
        return "文件名为空"
    if len(s) > limit:
        return f"文件名 {len(s)} 个字符，超过网盘上限 {limit}，无法转存"
    return ""
