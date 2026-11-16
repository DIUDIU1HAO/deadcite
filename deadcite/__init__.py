"""deadcite：找出 AI 生成报告里的假引用。"""

from typing import List, Sequence

from .checks import OK, PROBLEM, SUSPECT, Issue, Verdict, check, summarize
from .parse import Claim, Reference, collect_claims, parse_references
from .sources import Source, SourceIndex, load_sources

__version__ = "0.2.0"

__all__ = [
    "__version__",
    "Claim",
    "Reference",
    "Source",
    "SourceIndex",
    "Issue",
    "Verdict",
    "check",
    "collect_claims",
    "parse_references",
    "load_sources",
    "summarize",
    "OK",
    "PROBLEM",
    "SUSPECT",
    "audit",
]


def audit(
    report_text: str,
    sources: Sequence[str],
    threshold: float = 0.16,
    fetch: bool = False,
) -> List[Verdict]:
    """便捷入口：给一段报告文本和一批信源路径，返回逐条判定。

    Args:
        report_text: 报告全文（Markdown 或纯文本）。
        sources: 信源文件或目录路径。
        threshold: 字面重合度下限，低于它记一条可疑。
        fetch: 是否对引用里的链接发起真实请求。

    Returns:
        与正文论断顺序一致的判定列表。
    """
    refs = parse_references(report_text)
    claims = list(collect_claims(report_text))
    index = SourceIndex(load_sources(sources))
    return check(claims, refs, index, threshold=threshold, allow_fetch=fetch)
