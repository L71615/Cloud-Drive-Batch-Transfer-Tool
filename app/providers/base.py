# -*- coding: utf-8 -*-
"""Provider 契约。三个网盘各写各的适配器，通过这个接口被任务引擎调用。"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field

# 用户从聊天软件/网页里复制出来的链接常夹带零宽字符和全角空格，
# 这些看不见的字符会让正则匹配失败，先在这里统一清掉。
_INVISIBLE = dict.fromkeys(map(ord, "\u200b\u200c\u200d\ufeff\u00a0\u3000"), None)
_DASHES = str.maketrans({"\uff0f": "/", "\uff1a": ":", "\uff1f": "?", "\uff1d": "="})


def clean_text(text: str) -> str:
    """去掉零宽字符、NBSP、全角空格，并把全角 URL 分隔符转成半角。"""
    out = str(text or "").translate(_INVISIBLE)
    return out.translate(_DASHES)


class ProviderNotReady(RuntimeError):
    """适配器尚未按 docs/spec 配置好端点或凭据。"""


class ProviderError(RuntimeError):
    """可重试的协议错误。"""


class AuthExpired(ProviderError):
    """登录态失效，需要用户重新扫码。"""


@dataclass
class ShareRef:
    provider: str
    share_id: str
    url: str = ""
    passcode: str = ""
    title: str = ""


@dataclass
class Entry:
    path: str
    size: int = 0
    digest: str = ""
    meta: dict = field(default_factory=dict)   # 适配器私有：fid / token / fs_id 等


@dataclass
class TransferResult:
    ok: int = 0
    failed: int = 0
    messages: list[str] = field(default_factory=list)


class Provider(abc.ABC):
    name: str = "base"
    label: str = "通用"          # 界面显示名（中文）
    color: str = "#666666"       # 品牌色
    short: str = "??"            # 图标里的两个字
    qps: float = 1.0

    def __init__(self, credential: str = ""):
        self.credential = credential

    # --- 能力声明：UI 与任务引擎据此裁剪 ---
    @property
    def caps(self) -> dict:
        return {"batch": True, "dedup": False, "max_batch": 50}

    @property
    def ready(self) -> bool:
        """端点是否已按规格配置完成。"""
        return True

    def describe(self) -> dict:
        return {
            "name": self.name,
            "label": self.label,
            "color": self.color,
            "short": self.short,
            "ready": self.ready,
            "caps": self.caps,
        }

    def check_auth(self) -> dict:
        """用一次最轻的请求验证登录态。返回 {valid, message}。"""
        raise ProviderNotReady(f"{self.name}: 尚未配置端点，无法检测登录态")

    @abc.abstractmethod
    def parse_links(self, text: str) -> list[ShareRef]:
        """从用户粘贴的文本里提取分享引用。纯本地解析。"""

    @abc.abstractmethod
    def list_share(self, ref: ShareRef) -> list[Entry]:
        """列出分享内文件（含目录递归由适配器自己决定）。"""

    @abc.abstractmethod
    def transfer(self, ref: ShareRef, entry: Entry, target: str) -> str:
        """转存单个条目，返回目标路径。失败抛 ProviderError。"""

    # --- 可选能力 ---
    def list_dir(self, path: str) -> list[Entry]:
        raise ProviderNotReady(f"{self.name}: list_dir 未实现")

    def browse(self, path: str = "/") -> list[dict]:
        """浏览"我的网盘"某一层，供界面选目录用。不创建目录。

        返回 [{name, path, is_dir, size, meta}]，path 为绝对路径。
        """
        raise ProviderNotReady(f"{self.name}: browse 未实现")

    def quota(self) -> dict:
        raise ProviderNotReady(f"{self.name}: quota 未实现")

    def diagnose(self, ref: ShareRef) -> list[dict]:
        """分段跑一次最小链路，供界面"协议诊断"用。不改动任何数据。"""
        steps: list[dict] = []
        self._run_step(steps, "链接解析", lambda: f"share_id={ref.share_id}")
        return steps

    def _run_step(self, steps: list[dict], name: str, fn) -> None:
        import time as _t
        t0 = _t.time()
        try:
            detail = fn()
            steps.append({"name": name, "ok": True,
                          "ms": int((_t.time() - t0) * 1000), "detail": str(detail or "")})
        except Exception as e:  # noqa: BLE001 - 诊断就是要看到原始错误
            steps.append({"name": name, "ok": False,
                          "ms": int((_t.time() - t0) * 1000),
                          "error": f"{type(e).__name__}: {e}"})
