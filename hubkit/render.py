"""hub 契约渲染纯函数（事实源: docs/architecture.md Part II「hub 契约」节）。

消费者：task/ingest format_entry（staged 产物渲染）与 apply_entry（落位拼接）。
全部为无副作用纯函数；venue 未登记等契约问题抛 ContractError 交由调用方转
needs_human，不静默降级。
"""

from __future__ import annotations

import csv
import io
import re

from hubkit.readers import BULLET_RE, TABLE_ROW_RE
from hubkit.schema import COUNT_BADGE, REPO_KIND_AUTHOR, VENUE_STYLES

FRAMEWORK_SHORT = {"PyTorch/Hugging Face": "PyTorch / HF"}


class ContractError(Exception):
    """契约级问题（如 venue 未登记），需人工处置。"""


def venue_style(venue: str) -> dict[str, str]:
    style = VENUE_STYLES.get(venue)
    if style is None:
        raise ContractError(
            f"venue 未在 hubkit/schema VENUE_STYLES 登记: {venue!r}（新 venue 需人工指定短名与色值）"
        )
    return style


def repo_kind(verdict: str, repo_url: str) -> str:
    """repo 行措辞由官方性结论决定；HF 条目强制 Author-maintained。"""
    if "huggingface.co" in repo_url:
        return REPO_KIND_AUTHOR
    if verdict == "author_maintained":
        return REPO_KIND_AUTHOR
    if verdict == "official":
        return "Official"
    raise ContractError(f"verdict 不允许直接入库: {verdict!r}（likely 须先经人工确认）")


def render_model_readme(name: str, row: dict, verdict: str, status_tail: str) -> str:
    """模型 README 六行 bullet。

    status_tail: Status 行 "PDF downloaded; " 之后的仓库获取方式说明。
    repo 快照无 .py 时调用方必须传入含 no runnable code 的说明（契约 wording）。
    commit_hash=unavailable 时 Framework/license 行用形态 b（commit 融入 Status）。
    """
    kind = repo_kind(verdict, row["repo_url"])
    commit = row["commit_hash"]
    flc = (f"Framework/license: {row['framework']} / {row['license']}."
           if commit == "unavailable"
           else f"Framework/license/commit: {row['framework']} / {row['license']} / `{commit}`")
    lines = [
        f"# {name}",
        "",
        f"- Paper: [{row['paper_title']}]({row['paper_url']}) ({row['venue']}, {row['year']})",
        f"- Paper PDF: `paper/{name}.pdf`",
        f"- {kind} repository: {row['repo_url']}",
        (f"- Verification: the repository is {kind.lower()} per the paper's code availability "
         f"statement and metadata cross-check; license is {row['license']}."),
        f"- {flc}",
        f"- Status: PDF downloaded; {status_tail}",
        "",
    ]
    return "\n".join(lines)


def render_csv_row(row: dict) -> str:
    """单行 CSV（QUOTE_MINIMAL，\\n 结尾）。"""
    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
    writer.writerow([row[c] for c in (
        "model_name", "paper_title", "year", "venue", "paper_url",
        "repo_url", "github_stars", "framework", "license", "commit_hash",
    )])
    return buf.getvalue()


def render_bullet(row: dict, local_prefix: str) -> str:
    """外层/内层 README 条目（标题行 + 缩进徽章行，顺序 📄 → code/🤗 → Stars → Local）。

    徽章图式与色值以 hub 存量格式为准（emoji URL 编码、venue 短名+年份、
    Stars 动态徽章仅 GitHub 条目且 stars 非空）。
    """
    name = row["model_name"]
    style = venue_style(row["venue"])
    short, color, year = style["short"], style["color"], row["year"]
    venue_badge = (f"[![{short} {year}]"
                   f"(https://img.shields.io/badge/%F0%9F%93%84-"
                   f"{short.replace(' ', '%20')}%20{year}-{color})]({row['paper_url']})")
    if "huggingface.co" in row["repo_url"]:
        repo_badge = (f"[![Model](https://img.shields.io/badge/"
                      f"%F0%9F%A4%97-Model-FFD21E)]({row['repo_url']})")
        stars_badge = ""
    else:
        repo_badge = (f"[![code](https://img.shields.io/badge/"
                      f"code-2ea44f?logo=github)]({row['repo_url']})")
        stars_badge = ""
        if row["github_stars"]:
            owner_repo = re.sub(r"^https?://github\.com/", "", row["repo_url"]).rstrip("/")
            stars_badge = (f"[![Stars](https://img.shields.io/github/stars/"
                           f"{owner_repo}?style=flat&label=Stars)]({row['repo_url']})")
    local_badge = (f"[![Local](https://img.shields.io/badge/"
                   f"folder-%F0%9F%93%81-f0f0f0)](./{local_prefix}{name}/)")
    badges = " ".join(b for b in (venue_badge, repo_badge, stars_badge, local_badge) if b)
    return f"* **({name}) {row['paper_title']}**\n  {badges}\n"


def render_table_row(row: dict) -> str:
    """对比表行：venue 短名 + framework 短名；GitHub 条目链接文本 owner/repo。"""
    name = row["model_name"]
    short = venue_style(row["venue"])["short"]
    fw = FRAMEWORK_SHORT.get(row["framework"], row["framework"])
    if "huggingface.co" in row["repo_url"]:
        link_text = re.sub(r"^https?://", "", row["repo_url"]).rstrip("/")
    else:
        link_text = re.sub(r"^https?://github\.com/", "", row["repo_url"]).rstrip("/")
    return f"| **{name}** | {row['year']} | {short} | {fw} | [{link_text}]({row['repo_url']}) |\n"


def splice_readme(text: str, bullet: str, table_row: str) -> str:
    """向 README 文本追加一个条目：bullet 块（标题行+徽章行）加在条目列表末尾，
    表行加在对比表末尾；条目数徽章 +1。纯文本操作，供 apply 落位与临时拼接预检共用。"""
    lines = text.splitlines(keepends=True)
    last_bullet = last_row = badge_at = badge_n = None
    for i, line in enumerate(lines):
        if BULLET_RE.match(line):
            last_bullet = i
        if TABLE_ROW_RE.match(line):
            last_row = i
        m = re.search(r"badge/Models-(\d+)-brightgreen", line)
        if m:
            badge_at, badge_n = i, int(m[1])
    if last_bullet is None or last_row is None or badge_at is None:
        raise ContractError("README 结构异常：缺少 bullet 列表 / 对比表 / 条目数徽章")
    # bullet 是两行块（标题 + 缩进徽章行），插入在最后一个条目的徽章行之后
    insert_at = last_bullet + 2
    lines.insert(insert_at, bullet)
    if last_row >= insert_at:
        last_row += 1
    lines.insert(last_row + 1, table_row)
    out = "".join(lines)
    return re.sub(r"badge/Models-(\d+)-brightgreen",
                  lambda m: f"badge/Models-{int(m[1]) + 1}-brightgreen", out)
