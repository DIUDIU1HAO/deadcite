"""终端与 JSON 输出。"""

from __future__ import annotations

import json
import unicodedata
from typing import Dict, List, Sequence

from .checks import KIND_LABEL, PROBLEM, SUSPECT, Verdict, summarize

RESET = "\033[0m"
DIM = "\033[2m"
RED = "\033[31m"
YELLOW = "\033[33m"
GREEN = "\033[32m"
BOLD = "\033[1m"


def width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def pad(text: str, target: int) -> str:
    return text + " " * max(0, target - width(text))


def _cite_hint(verdict: Verdict) -> str:
    keys = verdict.claim.refs
    urls = verdict.claim.urls
    bits: List[str] = []
    if keys:
        bits.append("[" + ",".join(keys) + "]")
    if urls:
        bits.append(urls[0] if len(urls) == 1 else f"{urls[0]} 等 {len(urls)} 个链接")
    if verdict.source:
        bits.append(f"信源：{verdict.source}")
    return " · ".join(bits)


def render_terminal(
    results: Sequence[Verdict], file_label: str, color: bool = True, verbose: bool = True
) -> str:
    def c(code: str, text: str) -> str:
        return f"{code}{text}{RESET}" if color else text

    lines: List[str] = []
    stats = summarize(results)

    shown = [v for v in results if v.status != "ok"] if not verbose else list(results)
    if not verbose:
        shown = [v for v in results if v.status != "ok"]

    if shown:
        lines.append("")
        lines.append(c(BOLD, file_label))
        for v in shown:
            mark = {
                PROBLEM: c(RED, "✗"),
                SUSPECT: c(YELLOW, "!"),
                "ok": c(GREEN, "✓"),
            }[v.status]
            if not v.issues:
                lines.append(f"  {v.claim.line}  {mark} {c(DIM, v.claim.text.strip()[:90])}")
                continue
            first = v.issues[0]
            lines.append("")
            lines.append(f"  {v.claim.line}  {mark} {c(BOLD, first.label)}")
            lines.append(f"      {c(DIM, v.claim.text.strip()[:120])}")
            for issue in v.issues:
                extra = f"  {c(DIM, issue.detail)}" if issue.detail else ""
                lines.append(f"      · {issue.message}{extra}")
            hint = _cite_hint(v)
            if hint:
                lines.append(f"      {c(DIM, hint)}")

    lines.append("")
    lines.append("─" * 54)
    ratio = stats["problems"] / stats["total"] if stats["total"] else 0.0
    parts = [
        f"{stats['total']} 条引用",
        c(RED, f"{stats['problems']} 条有问题") if stats["problems"] else "0 条有问题",
        c(YELLOW, f"{stats['suspects']} 条可疑") if stats["suspects"] else "0 条可疑",
        f"{stats['ok']} 条通过",
    ]
    lines.append("  " + " · ".join(parts))
    if stats["total"]:
        label = "伪造率" if stats["problems"] else "伪造率"
        value = f"{ratio:.1%}"
        lines.append("  " + label + " " + (c(RED, value) if ratio else value))
    lines.append("")
    return "\n".join(lines)


def render_json(results: Sequence[Verdict], file_label: str) -> str:
    payload: Dict = {
        "file": file_label,
        "summary": summarize(results),
        "citations": [
            {
                "line": v.claim.line,
                "text": v.claim.text.strip(),
                "refs": v.claim.refs,
                "urls": v.claim.urls,
                "status": v.status,
                "source": v.source,
                "score": round(v.score, 4),
                "issues": [
                    {
                        "kind": i.kind,
                        "label": KIND_LABEL.get(i.kind, i.kind),
                        "severity": i.severity,
                        "message": i.message,
                        "detail": i.detail,
                    }
                    for i in v.issues
                ],
            }
            for v in results
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
