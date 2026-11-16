"""deadcite：找出 AI 生成报告里的假引用。"""

from .checks import OK, PROBLEM, SUSPECT, Issue, Verdict, check, summarize
from .parse import Claim, Reference, collect_claims, parse_references
from .sources import Source, SourceIndex, load_sources

__version__ = "0.1.0"

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


def audit(report_text: str, sources, threshold: float = 0.16, fetch: bool = False):
    """便捷入口：给一段报告文本和一批信源路径，返回逐条判定。"""
    refs = parse_references(report_text)
    claims = list(collect_claims(report_text))
    index = SourceIndex(load_sources(sources))
    return check(claims, refs, index, threshold=threshold, allow_fetch=fetch)
