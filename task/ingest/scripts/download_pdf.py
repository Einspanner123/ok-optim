#!/usr/bin/env python3
"""download_pdf: PDF 多源链（五级降级，契约「PDF 获取多源链模板」）。

    S2 openAccessPdf → arXiv /pdf/ → citation_pdf_url → Unpaywall → pdf_needs_manual

落地校验：%PDF magic + ≥50KB；写入 <AGENT_RUN_DIR>/paper/paper.pdf。
可选线索经 --arxiv-id / --doi / --s2-pdf / --citation-pdf-url 传入（无状态）。
exit: 0 下载成功 / 2 needs_human（全链失败，status=pdf_needs_manual）/ 3 fatal。
"""

from __future__ import annotations

import argparse
import signal
import sys
from pathlib import Path

from _state import _stdio_json, workdir
from _net import http_get, http_get_stream, load_dotenv
from _net import extract_metadata
from hubkit.schema import PDF_MAGIC, PDF_MIN_BYTES


class DownloadDeadlineExceeded(BaseException):
    """Stop the whole PDF chain, including a stream that keeps making progress."""


def _deadline_expired(signum: int, frame: object) -> None:
    raise DownloadDeadlineExceeded


def _ok_pdf(data: bytes) -> bool:
    return data.startswith(PDF_MAGIC) and len(data) >= PDF_MIN_BYTES


def chain_urls(cand: dict) -> list[tuple[str, str]]:
    """按优先级展开候选 URL（source 标签, url）。"""
    urls: list[tuple[str, str]] = []
    if cand.get("openaccesspdf_url"):
        urls.append(("s2_openaccesspdf", cand["openaccesspdf_url"]))
    if cand.get("arxiv_id"):
        urls.append(("arxiv_pdf", f"https://arxiv.org/pdf/{cand['arxiv_id']}"))
    if cand.get("citation_pdf_url"):
        urls.append(("citation_pdf_url", cand["citation_pdf_url"]))
    if cand.get("doi"):
        email = __import__("os").environ.get("EMAIL", "")
        if email:
            urls.append(("unpaywall",
                         f"https://api.unpaywall.org/v2/{cand['doi']}?email={email}"))
    return urls


def _unpaywall_pdf(url: str) -> str | None:
    import json as _json
    resp = http_get(url)
    if resp.status_code != 200:
        return None
    for loc in resp.json().get("oa_locations") or []:
        u = loc.get("url_for_pdf") or loc.get("url_for_landing_page")
        if u:
            return u
    return None


def try_chain(cand: dict) -> tuple[bytes, str] | None:
    for source, url in chain_urls(cand):
        try:
            if source == "unpaywall":
                real = _unpaywall_pdf(url)
                if not real:
                    continue
                url, source = real, "unpaywall→" + real.split("/")[2]
            data = http_get_stream(url, timeout=30.0)
            if _ok_pdf(data):
                return data, source
        except Exception:
            continue
    # citation_pdf_url 现取（未预登记时）
    paper_url = cand.get("paper_url")
    if paper_url and not cand.get("citation_pdf_url"):
        try:
            resp = http_get(paper_url)
            meta = extract_metadata(resp.text)
            cpdf = meta.get("pdf_url")
            if cpdf:
                data = http_get_stream(cpdf, timeout=30.0)
                if _ok_pdf(data):
                    return data, "citation_pdf_url"
        except Exception:
            pass
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="download_pdf: PDF 多源链")
    parser.add_argument("paper_url", help="论文落地页 URL（供 citation_pdf_url 兜底）")
    parser.add_argument("--arxiv-id", default=None, help="arXiv ID（/pdf/ 通道）")
    parser.add_argument("--doi", default=None, help="DOI（Unpaywall 通道）")
    parser.add_argument("--s2-pdf", default=None, help="S2 openAccessPdf URL（首选通道）")
    parser.add_argument("--citation-pdf-url", default=None, help="出版方 citation_pdf_url")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    load_dotenv()

    cand = {"paper_url": args.paper_url, "arxiv_id": args.arxiv_id, "doi": args.doi,
            "openaccesspdf_url": args.s2_pdf, "citation_pdf_url": args.citation_pdf_url}
    paper_dir = workdir() / "paper"
    paper_dir.mkdir(parents=True, exist_ok=True)

    previous_handler = signal.signal(signal.SIGALRM, _deadline_expired)
    signal.setitimer(signal.ITIMER_REAL, 90.0)
    try:
        got = try_chain(cand)
    except DownloadDeadlineExceeded:
        got = None
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
    if not got:
        payload = {"status": "pdf_needs_manual",
                   "tried": [s for s, _ in chain_urls(cand)]}
        _stdio_json(payload, args.json)
        print("needs_human: 多源链全部失败（pdf_needs_manual）", file=sys.stderr)
        return 2
    data, source = got
    pdf_path = paper_dir / "paper.pdf"
    pdf_path.write_bytes(data)
    payload = {"status": "downloaded", "source": source,
               "path": str(pdf_path), "bytes": len(data)}
    _stdio_json(payload, args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
