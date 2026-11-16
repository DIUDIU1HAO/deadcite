"""判定逻辑：一条引用是「确有问题」还是「可疑」，必须分清。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .parse import Claim, Reference
from .sources import Source, SourceIndex, fetch_url, is_distinctive, numbers_in

PROBLEM = "problem"  # 确定性错误：来源不存在、链接打不开、数字在原文里根本没有
SUSPECT = "suspect"  # 启发式怀疑：字面重合度低，不构成确证
OK = "ok"

KIND_LABEL = {
    "MISSING_REF": "参考文献里没有这一条",
    "NO_TARGET": "找不到对应信源",
    "UNREACHABLE": "信源打不开",
    "SOURCE_ERROR": "信源读取失败",
    "NUMBER_NOT_FOUND": "数字在原文中不存在",
    "LOW_OVERLAP": "原文中找不到相近表述",
}


@dataclass
class Issue:
    kind: str
    message: str
    severity: str
    detail: str = ""

    @property
    def label(self) -> str:
        return KIND_LABEL.get(self.kind, self.kind)


@dataclass
class Verdict:
    claim: Claim
    issues: List[Issue] = field(default_factory=list)
    source: Optional[str] = None
    score: float = 0.0

    @property
    def status(self) -> str:
        if any(i.severity == PROBLEM for i in self.issues):
            return PROBLEM
        if any(i.severity == SUSPECT for i in self.issues):
            return SUSPECT
        return OK


def _resolve(
    ref: Optional[Reference],
    url: Optional[str],
    index: SourceIndex,
    allow_fetch: bool,
    fetched: Dict[str, Source],
) -> Tuple[Optional[Source], Optional[Issue]]:
    """为一个引用目标找到信源；返回 (信源, 失败原因)。"""
    if url:
        local = index.find_local_by_url(url)
        if local is not None:
            return local, None
        if not allow_fetch:
            return None, Issue(
                "UNREACHABLE",
                f"链接未联网校验：{url}",
                SUSPECT,
                "加 --fetch 可实际抓取该链接",
            )
        if url not in fetched:
            fetched[url] = fetch_url(url)
        src = fetched[url]
        if src.error:
            return None, Issue("UNREACHABLE", f"链接打不开：{url}", PROBLEM, src.error)
        return src, None

    if ref is not None and ref.title:
        matched = index.match_title(ref.title)
        if matched is not None:
            return matched, None
        return None, Issue(
            "NO_TARGET",
            f"参考文献「{ref.title[:40]}」没有链接，本地也没有同名信源",
            PROBLEM,
        )

    return None, Issue("NO_TARGET", "该引用没有可定位的来源", PROBLEM)


def _fmt_num(number: str) -> str:
    """把 1842 显示成 1,842，便于在报告里对照原文。"""
    if "." in number:
        return number
    try:
        return f"{int(number):,}"
    except ValueError:
        return number


def check(
    claims: Sequence[Claim],
    refs: Dict[str, Reference],
    index: SourceIndex,
    threshold: float = 0.16,
    allow_fetch: bool = False,
) -> List[Verdict]:
    fetched: Dict[str, Source] = {}
    verdicts: List[Verdict] = []

    for claim in claims:
        issues: List[Issue] = []
        targets: List[Source] = []

        for key in claim.refs:
            ref = refs.get(key)
            if ref is None:
                issues.append(
                    Issue(
                        "MISSING_REF",
                        f"正文引用了 [{key}]，但参考文献表里没有第 {key} 条",
                        PROBLEM,
                    )
                )
                continue
            src, problem = _resolve(ref, ref.url, index, allow_fetch, fetched)
            if problem is not None:
                issues.append(problem)
            elif src is not None:
                targets.append(src)

        for url in claim.urls:
            src, problem = _resolve(None, url, index, allow_fetch, fetched)
            if problem is not None:
                issues.append(problem)
            elif src is not None:
                targets.append(src)

        best_score = 0.0
        best_source: Optional[str] = None
        all_numbers: set = set()

        for src in targets:
            if src.error:
                issues.append(
                    Issue("SOURCE_ERROR", f"信源读取失败：{src.name}", PROBLEM, src.error)
                )
                continue
            match = index.best(claim.plain)
            if match is not None:
                # 相似度按「这段文字 vs 全部信源的最佳块」评估，避免多信源时互相干扰
                if match.score > best_score:
                    best_score, best_source = match.score, match.source
            all_numbers |= index.numbers_of(src.name)

        if targets:
            claimed = {n for n in numbers_in(claim.plain) if is_distinctive(n)}
            missing = sorted(claimed - all_numbers)
            if missing:
                shown = "、".join(_fmt_num(n) for n in missing[:5])
                issues.append(
                    Issue(
                        "NUMBER_NOT_FOUND",
                        f"这些数字在信源里找不到：{shown}",
                        PROBLEM,
                    )
                )
            if best_score < threshold:
                issues.append(
                    Issue(
                        "LOW_OVERLAP",
                        f"与信源的字面重合度仅 {best_score:.2f}，原文中找不到相近表述",
                        SUSPECT,
                        f"阈值 {threshold:.2f}；转述幅度大时会误判，请人工确认",
                    )
                )

        verdicts.append(
            Verdict(claim=claim, issues=issues, source=best_source, score=best_score)
        )

    return verdicts


def summarize(verdicts: Sequence[Verdict]) -> Dict[str, int]:
    total = len(verdicts)
    problems = sum(1 for v in verdicts if v.status == PROBLEM)
    suspects = sum(1 for v in verdicts if v.status == SUSPECT)
    return {
        "total": total,
        "problems": problems,
        "suspects": suspects,
        "ok": total - problems - suspects,
    }
