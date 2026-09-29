"""ingest 网络脚本共享底座：UA / 代理 / 限流 / 缓存 / gh api / 磁盘下载。

仅 task/ingest/scripts/ 内部使用。契约（architecture.md「检索接口抽象」）：
- 各通道限流（S2 1req/s、arXiv ≥3s、gh api 滑动窗口 30req/min、网页 1req/2s/域名）
- 响应按查询键磁盘缓存（runs/.cache/ingest/，纯内容寻址），重复查询零网络
- httpx + Mozilla UA；代理经 envguard 注入（ALL_PROXY / HTTPS_PROXY）
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

import httpx

from _state import REPO_ROOT

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# 纯内容寻址缓存：无业务语义，跨 run 复用安全
CACHE_ROOT = REPO_ROOT / "runs" / ".cache" / "ingest"

# 每域名最小请求间隔（秒）；未列出的域名走默认
DOMAIN_INTERVALS = {
    "api.semanticscholar.org": 1.0,   # S2 匿名档 1req/s
    "export.arxiv.org": 3.0,          # arXiv 官方政策
    "api.github.com": 0.25,           # 配合 30req/min 滑动窗口
    "api.unpaywall.org": 1.0,
}
DEFAULT_INTERVAL = 2.0
GH_WINDOW, GH_MAX_PER_WINDOW = 60.0, 30

_last_hit: dict[str, float] = {}
_gh_hits: list[float] = []


def proxy() -> str | None:
    """代理取自 envguard 注入的环境（ALL_PROXY 优先，回退 HTTPS_PROXY）。"""
    import os
    return os.environ.get("ALL_PROXY") or os.environ.get("HTTPS_PROXY") or None


def client(timeout: float = 30.0, use_proxy: bool = True) -> httpx.Client:
    # trust_env=False：代理只经 proxy() 显式传入，否则 use_proxy=False 会仍被
    # httpx 从环境读回 ALL_PROXY（回退直连失效的根因）
    px = proxy() if use_proxy else None
    return httpx.Client(timeout=timeout, follow_redirects=True, trust_env=False,
                        headers={"User-Agent": UA}, proxy=px)


def _throttle(url: str) -> None:
    host = url.split("/")[2] if "://" in url else url
    interval = DOMAIN_INTERVALS.get(host, DEFAULT_INTERVAL)
    now = time.monotonic()
    wait = interval - (now - _last_hit.get(host, 0.0))
    if wait > 0:
        time.sleep(wait)
    _last_hit[host] = time.monotonic()


def cache_key(url: str, params: dict | None = None,
              headers: dict | None = None) -> Path:
    # 键含 params 与 headers：Accept 差异（raw README vs JSON）不得串缓存
    basis = url
    if params:
        basis += "?" + json.dumps(params, sort_keys=True)
    if headers:
        basis += "#" + json.dumps(headers, sort_keys=True)
    name = hashlib.sha256(basis.encode()).hexdigest()[:24]
    return CACHE_ROOT / f"{name}.cache"


def _get(url: str, params: dict | None, headers: dict | None,
         timeout: float) -> httpx.Response:
    """GET：代理可用走代理，代理连不上自动直连回退（S2/arXiv 等直连本可达）。"""
    try:
        with client(timeout=timeout) as c:
            return c.get(url, params=params, headers=headers)
    except (httpx.ProxyError, httpx.ConnectError):
        if not proxy():
            raise
        with client(timeout=timeout, use_proxy=False) as c:
            return c.get(url, params=params, headers=headers)


def http_get(url: str, params: dict | None = None, headers: dict | None = None,
             timeout: float = 30.0, cache: bool = True) -> httpx.Response:
    """限流 GET + 磁盘缓存。二进制安全（bytes 可经 resp.content 取）。"""
    ck = cache_key(url, params, headers)
    if cache and ck.is_file():
        return httpx.Response(200, content=ck.read_bytes(),
                              headers={"X-Cache": "hit"},
                              request=httpx.Request("GET", url))
    _throttle(url)
    resp = _get(url, params, headers, timeout)
    if cache and resp.status_code == 200:
        CACHE_ROOT.mkdir(parents=True, exist_ok=True)
        ck.write_bytes(resp.content)
    return resp


def http_get_stream(url: str, headers: dict | None = None,
                    timeout: float = 120.0, max_bytes: int = 50 * 1024 * 1024) -> bytes:
    """大文件流式下载（不走缓存），返回 bytes；超过 max_bytes 抛错。
    代理连不上自动直连回退（递归一次，use_proxy=False 终止）。"""
    _throttle(url)
    chunks: list[bytes] = []
    total = 0
    try:
        with client(timeout=timeout) as c:
            with c.stream("GET", url, headers=headers) as resp:
                resp.raise_for_status()
                for chunk in resp.iter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError(f"download exceeds {max_bytes} bytes: {url}")
                    chunks.append(chunk)
    except (httpx.ProxyError, httpx.ConnectError):
        if not proxy():
            raise
        return _stream_direct(url, headers, timeout, max_bytes)
    return b"".join(chunks)


def _stream_direct(url: str, headers: dict | None,
                   timeout: float, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    with client(timeout=timeout, use_proxy=False) as c:
        with c.stream("GET", url, headers=headers) as resp:
            resp.raise_for_status()
            for chunk in resp.iter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError(f"download exceeds {max_bytes} bytes: {url}")
                chunks.append(chunk)
    return b"".join(chunks)


def gh_api(path: str, params: dict | None = None,
           headers: dict | None = None) -> httpx.Response:
    """gh api：GITHUB_TOKEN 认证 + 30req/min 滑动窗口 + 缓存。"""
    import os
    now = time.monotonic()
    _gh_hits[:] = [t for t in _gh_hits if now - t < GH_WINDOW]
    if len(_gh_hits) >= GH_MAX_PER_WINDOW:
        time.sleep(GH_WINDOW - (now - _gh_hits[0]) + 0.1)
    _gh_hits.append(time.monotonic())
    tok = os.environ.get("GITHUB_TOKEN", "")
    h = {"Accept": "application/vnd.github+json"}
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    if headers:
        h.update(headers)
    url = path if path.startswith("http") else f"https://api.github.com{path}"
    return http_get(url, params=params, headers=h)


def gh_raw(path: str, ref: str | None = None) -> str:
    """仓库原始文件（README 等）：Accept raw，返回文本。"""
    h = {"Accept": "application/vnd.github.raw"}
    resp = gh_api(path, headers=h)
    resp.raise_for_status()
    return resp.text


AVAIL_RE = re.compile(
    r"(?:code\s+(?:and\s+data\s+)?availability|data\s+and\s+code\s+availability)"
    r"[^\n]{0,80}?[:：]?\s*(.{100,1500}?)(?=\n\s*\n|</p>|$)", re.I | re.S)
META_RE = re.compile(
    r'<meta\s+name="([^"]+)"\s+content="([^"]*)"', re.I)
STRIP = re.compile(r"<[^>]+>")


def strip_html(html: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.I | re.S)
    return STRIP.sub(" ", text)


def extract_availability(html: str) -> str:
    m = AVAIL_RE.search(html) or AVAIL_RE.search(strip_html(html))
    if not m:
        return ""
    return " ".join(m.group(1).split())[:1200]


def extract_metadata(html: str) -> dict:
    out: dict[str, str] = {}
    for name, content in META_RE.findall(html):
        name = name.lower()
        if name.startswith("citation_"):
            out[name[len("citation_"):]] = content.strip()
    return out



def load_dotenv() -> None:
    """脚本独立执行（不经 launcher）时从项目 .env 补 env——已设置的键不覆盖。"""
    import os
    env_path = REPO_ROOT / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip("\"'"))
