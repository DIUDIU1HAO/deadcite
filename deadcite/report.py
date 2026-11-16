"""终端与 JSON 输出。

终端输出只依赖 :class:`deadcite.theme.Theme`：是否着色、图标用 Unicode 还是
ASCII 都在这里之外决定，渲染函数本身是无状态且可测的。
JSON 是给机器读的契约：``schema_version`` 保证字段演进可控，其余字段只增不改。
"""

from __future__ import annotations

import json
import shutil
from typing import Dict, List, Optional, Sequence

from .checks import KIND_LABEL, PROBLEM, SUSPECT, Verdict, summarize
from .theme import Theme, display_width, pad, truncate, wrap

__all__ = [
    "SCHEMA_VERSION",
    "render_json",
    "render_terminal",
    "width",
    "pad",  # 0.1 时这两个函数在 report 里，保留导出位置，避免下游 import 断掉
]

SCHEMA_VERSION = 1

# 0.1 里 report.width() 的旧名字
width = display_width

# 终端再宽也不铺满：一行超过 100 列反而难扫读
MAX_CONTENT_WIDTH = 96
RULE_WIDTH = 54


def _content_width() -> int:
    try:
        cols = shutil.get_terminal_size((100, 24)).columns
    except OSError:
        cols = 100
    return max(48, min(cols - 2, MAX_CONTENT_WIDTH))


def _cite_hint(verdict: Verdict, theme: Theme) -> str:
    keys = verdict.claim.refs
    urls = verdict.claim.urls
    bits: List[str] = []
    if keys:
        bits.append("[" + ",".join(keys) + "]")
    if urls:
        bits.append(urls[0] if len(urls) == 1 else f"{urls[0]} 等 {len(urls)} 个链接")
    if verdict.source:
        bits.append(f"信源：{verdict.source}")
    return theme.paint(f" {theme.bullet} ".join(bits), "dim")


def _status_icon(verdict: Verdict, theme: Theme) -> str:
    style = {PROBLEM: "red", SUSPECT: "yellow"}.get(verdict.status, "green")
    return theme.paint(theme.icon(verdict.status), style)


def render_terminal(
    results: Sequence[Verdict],
    file_label: str,
    color: bool = True,
    verbose: bool = True,
    quiet: bool = False,
    theme: Optional[Theme] = None,
) -> str:
    """把逐条判定渲染成人类可读的报告。

    Args:
        results: ``check()`` 返回的判定列表。
        file_label: 显示在标题行的文件名。
        color: 未传 ``theme`` 时用来构造主题；传了 ``theme`` 则以它为准。
        verbose: 是否连通过的引用一起列出。
        quiet: 只保留汇总，不列逐条结果。
        theme: 外部构造好的主题（命令行入口按 TTY 探测后传入）。

    Returns:
        完整的报告文本，末尾带换行。
    """
    theme = theme if theme is not None else Theme(color=color)
    body: List[str] = []
    stats = summarize(results)
    width = _content_width()

    shown = [v for v in results if verbose or v.status != "ok"]

    if shown and not quiet:
        num_width = max(len(str(v.claim.line)) for v in shown)
        body.append(theme.paint(file_label, "bold"))
        for v in shown:
            body.append("")
            prefix = f"  {v.claim.line:>{num_width}}  {_status_icon(v, theme)} "
            body_indent = " " * display_width(prefix)
            body_width = max(24, width - display_width(body_indent))

            if not v.issues:
                text = truncate(v.claim.text.strip(), body_width, theme.ellipsis)
                body.append(prefix + theme.paint(text, "dim"))
                continue

            headline = theme.paint(v.issues[0].label, "bold")
            body.append(prefix + headline)
            quote = truncate(v.claim.text.strip(), body_width, theme.ellipsis)
            body.append(body_indent + theme.paint(quote, "dim"))

            for issue in v.issues:
                bullet = body_indent + theme.bullet + " "
                bullet_width = display_width(bullet)
                hang = " " * bullet_width
                pieces = wrap(issue.message, width, indent=bullet_width)
                for i, piece in enumerate(pieces):
                    body.append((bullet if i == 0 else hang) + piece)
                if issue.detail:
                    detail_indent = body_indent + "  "
                    for piece in wrap(
                        issue.detail, width, indent=display_width(detail_indent)
                    ):
                        body.append(detail_indent + theme.paint(piece, "dim"))

            hint = _cite_hint(v, theme)
            if hint:
                body.append(body_indent + hint)

    if results and not body and not quiet:
        # 全部通过：仍然标出文件名，多文件跑的时候才分得清是哪一份
        body.append(theme.paint(file_label, "bold"))

    lines: List[str] = [""] + body if body else []
    if lines:
        lines.append("")
    lines.append(theme.rule(min(width, RULE_WIDTH)))
    lines.append("  " + _summary_line(stats, theme))
    if stats["total"]:
        lines.append("  " + _ratio_line(stats, theme))
    lines.append("")
    return "\n".join(lines)


def _summary_line(stats: Dict[str, int], theme: Theme) -> str:
    parts = [f"{stats['total']} 条引用"]
    problems = stats["problems"]
    suspects = stats["suspects"]
    parts.append(
        theme.paint(f"{problems} 条有问题", "red") if problems else "0 条有问题"
    )
    parts.append(
        theme.paint(f"{suspects} 条可疑", "yellow") if suspects else "0 条可疑"
    )
    parts.append(f"{stats['ok']} 条通过")
    return f" {theme.bullet} ".join(parts)


def _ratio_line(stats: Dict[str, int], theme: Theme) -> str:
    ratio = stats["problems"] / stats["total"] if stats["total"] else 0.0
    value = f"{ratio:.1%}"
    if ratio:
        value = theme.paint(value, "red")
    return f"伪造率 {value}（{stats['problems']}/{stats['total']}，只算确定性错误）"


def render_json(
    results: Sequence[Verdict],
    file_label: str,
    threshold: float = 0.16,
    fail_on: str = "problem",
    version: str = "",
) -> str:
    """渲染单个报告文件的 JSON 对象（不含外层数组）。

    顶层结构由调用方拼成数组，因此单文件与多文件类型一致。
    新增字段只追加、不改名，``schema_version`` 用于标识契约版本。
    """
    stats = summarize(results)
    payload: Dict = {
        "schema_version": SCHEMA_VERSION,
        "tool": "deadcite",
        "version": version,
        "file": file_label,
        "threshold": threshold,
        "fail_on": fail_on,
        "summary": {
            "total": stats["total"],
            "problems": stats["problems"],
            "suspects": stats["suspects"],
            "ok": stats["ok"],
            "problem_ratio": round(
                stats["problems"] / stats["total"] if stats["total"] else 0.0, 4
            ),
        },
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
