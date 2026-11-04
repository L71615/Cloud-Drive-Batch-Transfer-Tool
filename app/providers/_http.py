# -*- coding: utf-8 -*-
"""共享 HTTP 客户端：只做四件事——带 Cookie、超时、重试、限速。

刻意不复用任何第三方 SDK，也不携带从其它软件里提取的密钥。
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from ..ratelimit import bucket
from .base import AuthExpired, ProviderError

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# 诊断用：只记录"当前线程"的请求，避免并发任务的流量混进来
_TRACE = threading.local()


def trace_on() -> list:
    """开始记录本线程发出的请求，返回可追加的列表。"""
    _TRACE.sink = []
    return _TRACE.sink


def trace_off() -> None:
    _TRACE.sink = None


def _record(method: str, url: str, response) -> None:
    sink = getattr(_TRACE, "sink", None)
    if sink is None:
        return
    text = response if isinstance(response, str) else json.dumps(response, ensure_ascii=False)
    sink.append({
        "method": method,
        "url": url,
        "response": text[:4000],
    })


class Client:
    def __init__(self, provider: str, credential: str, qps: float = 1.0,
                 referer: str = "", timeout: float = 20.0):
        self.provider = provider
        self.credential = (credential or "").strip()
        self.timeout = timeout
        self.referer = referer
        self._bucket = bucket(f"{provider}:default", qps)

    def _headers(self, extra: dict | None = None) -> dict:
        h = {
            "User-Agent": UA,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        if self.referer:
            h["Referer"] = self.referer
        if self.credential:
            h["Cookie"] = self.credential
        if extra:
            h.update({k: v for k, v in extra.items() if v})
        return h

    def request(self, method: str, url: str, *, params: dict | None = None,
                body: dict | None = None, headers: dict | None = None,
                form: bool = False, tries: int = 3) -> dict | str:
        if params:
            sep = "&" if urllib.parse.urlparse(url).query else "?"
            url = f"{url}{sep}{urllib.parse.urlencode(params)}"
        data = None
        hdrs = self._headers(headers)
        if body is not None:
            if form:
                data = urllib.parse.urlencode(body).encode()
                hdrs["Content-Type"] = "application/x-www-form-urlencoded"
            else:
                data = json.dumps(body, ensure_ascii=False).encode("utf-8")
                hdrs["Content-Type"] = "application/json;charset=UTF-8"

        last: Exception | None = None
        for attempt in range(tries):
            if not self._bucket.acquire(1.0, timeout=self.timeout * 2):
                raise ProviderError(f"{self.provider}: rate limited locally")
            req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read()
                text = raw.decode("utf-8", "replace")
                try:
                    obj = json.loads(text)
                except json.JSONDecodeError:
                    obj = text
                _record(method, url, obj)
                return obj
            except urllib.error.HTTPError as e:
                if e.code in (401, 403):
                    _record(method, url, f"HTTP {e.code}")
                    raise AuthExpired(f"{self.provider}: http {e.code}") from e
                last = e
            except (urllib.error.URLError, TimeoutError) as e:
                last = e
            time.sleep(min(2 ** attempt, 8) * 0.5)
        raise ProviderError(f"{self.provider}: request failed after {tries} tries: {last}")
