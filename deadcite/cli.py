"""命令行入口。

职责边界：解析参数、读文件、决定退出码。所有排版都交给 ``report``，
所有颜色与图标决策都交给 ``theme``，方便单独测试。
"""

from __future__ import annotations

import argparse
import os
import sys
import textwrap
from typing import List, Optional, Sequence, Tuple

from . import __version__
from .checks import check, summarize
from .parse import collect_claims, parse_references
from .report import render_json, render_terminal
from .sources import SourceIndex, load_sources
from .theme import Theme, display_width, pad, supports_color, supports_unicode

DOCS_URL = "https://github.com/DIUDIU1HAO/deadcite#readme"

EXAMPLES = [
    ("deadcite report.md", "体检报告，信源自动取同目录 sources/"),
    ("deadcite report.md -s sources/", "指定信源目录"),
    ("deadcite report.md --fetch", "对引用里的链接实际发起请求"),
    ("deadcite report.md --json | jq .summary", "机器可读结果，接管道处理"),
    ("deadcite report.md -q --fail-on suspect", "只留汇总，可疑也算失败"),
]

EXIT_CODES = [
    ("0", "未发现问题"),
    ("1", "达到 --fail-on 阈值"),
    ("2", "参数错误或文件读不了"),
]


def _two_column(rows: Sequence[Tuple[str, str]]) -> str:
    """按显示宽度对齐的两列文本，宽度变化不会像手打空格那样错位。"""
    left_width = max(display_width(left) for left, _ in rows) if rows else 0
    return "\n".join(pad(left, left_width + 2) + right for left, right in rows)


def _epilog(color: bool) -> str:
    theme = Theme(color=color)

    def head(text: str) -> str:
        return theme.paint(text, "bold")

    body = "\n".join(
        [
            head("示例:"),
            _two_column(EXAMPLES),
            "",
            head("退出码:"),
            _two_column(EXIT_CODES),
            "",
            f"文档: {DOCS_URL}",
        ]
    )
    return textwrap.indent(body, "  ")


class _Formatter(argparse.RawDescriptionHelpFormatter):
    """保留 epilog 的原始排版（示例区自己算对齐），其余交给 argparse。"""


class _Parser(argparse.ArgumentParser):
    """给分组标题加粗。

    只在整行标题上加样式，不碰参数名列：参数名的显示宽度参与列对齐计算，
    插入 ANSI 转义会让 ``--help`` 错位。
    ``_format_heading`` 在 3.13 的 ``_Section`` 里已不再被调用，
    因此选择在 ``format_help`` 出口做后处理，跨版本稳定。
    """

    def __init__(self, *args, color: bool = False, **kwargs) -> None:
        self._color = color
        super().__init__(*args, **kwargs)

    def format_help(self) -> str:
        text = super().format_help()
        if not self._color:
            return text
        theme = Theme(color=True)
        titles = {g.title for g in self._action_groups if g.title}
        lines = []
        for line in text.split("\n"):
            label = line.strip()
            if label.endswith(":") and label[:-1] in titles:
                line = line[: len(line) - len(label)] + theme.paint(label, "bold")
            lines.append(line)
        return "\n".join(lines)


def build_parser(color: bool = False) -> argparse.ArgumentParser:
    """构造参数解析器；``color`` 只影响 ``--help`` 的标题样式。"""
    p = _Parser(
        prog="deadcite",
        add_help=False,
        color=color,
        description=(
            "找出 AI 生成报告里的假引用：不存在的来源、打不开的链接、"
            "原文里根本没有的数字。"
            "\n\n确定性错误（problem）与启发式怀疑（suspect）严格分开，"
            "只有前者默认影响退出码。"
        ),
        formatter_class=_Formatter,
        epilog=_epilog(color),
    )
    p.add_argument(
        "reports",
        nargs="+",
        metavar="REPORT",
        help="要体检的报告文件（Markdown 或纯文本）",
    )

    sources = p.add_argument_group("信源")
    sources.add_argument(
        "-s",
        "--sources",
        action="append",
        default=[],
        metavar="PATH",
        help="信源文件或目录，可重复（默认：报告同目录下的 sources/）",
    )
    sources.add_argument(
        "--fetch",
        action="store_true",
        help="对引用中的链接实际发起请求（默认只查本地信源）",
    )

    judge = p.add_argument_group("判定")
    judge.add_argument(
        "--threshold",
        type=float,
        default=0.16,
        metavar="FLOAT",
        help="字面重合度低于此值记为可疑，取值 (0, 1]，默认 0.16",
    )
    judge.add_argument(
        "--fail-on",
        default="problem",
        choices=["problem", "suspect", "none"],
        help="退出码触发阈值，默认 problem",
    )

    output = p.add_argument_group("输出")
    output.add_argument(
        "--json",
        action="store_true",
        help="输出 JSON 数组到 stdout，诊断信息仍走 stderr",
    )
    output.add_argument(
        "--no-color",
        action="store_true",
        help="禁用彩色（管道与重定向时本就自动禁用）",
    )
    output.add_argument("-q", "--quiet", action="store_true", help="只输出汇总，不列逐条结果")
    output.add_argument("-v", "--verbose", action="store_true", help="同时列出通过的引用")

    other = p.add_argument_group("其他")
    other.add_argument("-h", "--help", action="help", help="显示本帮助并退出")
    other.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"deadcite {__version__}",
        help="显示版本号",
    )
    p._positionals.title = "报告"  # noqa: SLF001  (argparse 没有公开的改标题入口)
    return p


def _sync_stdout() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def _theme(no_color: bool) -> Theme:
    """stdout 的颜色与图标能力一起探测：两者都得为真才好排版。"""
    enabled = not no_color and supports_color(sys.stdout)
    return Theme(color=enabled, unicode=supports_unicode(sys.stdout))


def _read(path: str) -> str:
    """按 utf-8 → gb18030 依次尝试解码，都失败才报错。"""
    with open(path, "rb") as fh:
        blob = fh.read()
    for enc in ("utf-8", "gb18030"):
        try:
            return blob.decode(enc)
        except UnicodeDecodeError:
            continue
    raise UnicodeError(f"无法按 utf-8/gb18030 解码：{path}")


def _default_sources(report: str) -> List[str]:
    guess = os.path.join(os.path.dirname(os.path.abspath(report)), "sources")
    return [guess] if os.path.isdir(guess) else []


def _fail(message: str, no_color: bool = False) -> int:
    """统一的错误出口：stderr 一行、退出码 2。"""
    theme = Theme(color=not no_color and supports_color(sys.stderr))
    print(f"{theme.paint('deadcite:', 'bold', 'red')} {message}", file=sys.stderr)
    return 2


def _warn(message: str, no_color: bool = False) -> None:
    theme = Theme(color=not no_color and supports_color(sys.stderr))
    print(f"{theme.paint('deadcite:', 'bold', 'yellow')} {message}", file=sys.stderr)


def _exit_code(stats: dict, fail_on: str) -> int:
    if fail_on == "none":
        return 0
    if fail_on == "suspect":
        return 1 if (stats["problems"] or stats["suspects"]) else 0
    return 1 if stats["problems"] else 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    """命令行主入口；返回进程退出码。"""
    _sync_stdout()
    raw = list(sys.argv[1:] if argv is None else argv)
    help_color = supports_color(sys.stdout) and not _no_color_requested(raw)
    args = build_parser(color=help_color).parse_args(raw)

    if not 0 < args.threshold <= 1:
        return _fail("--threshold 需要落在 (0, 1] 区间", args.no_color)

    theme = _theme(args.no_color)
    sources = list(args.sources) or _default_sources(args.reports[0])
    index = SourceIndex(load_sources(sources)) if sources else SourceIndex([])

    if not index.usable and not args.fetch:
        _warn(
            "没有可用信源，只能检查引用是否悬空。"
            "用 -s 指定信源目录，或加 --fetch 联网校验。",
            args.no_color,
        )

    exit_code = 0
    json_parts: List[str] = []

    for report in args.reports:
        try:
            text = _read(report)
        except (OSError, UnicodeError) as exc:
            return _fail(f"读不了 {report}：{exc}", args.no_color)

        refs = parse_references(text)
        claims = list(collect_claims(text))
        results = check(
            claims, refs, index, threshold=args.threshold, allow_fetch=args.fetch
        )

        if args.json:
            json_parts.append(
                render_json(
                    results,
                    report,
                    threshold=args.threshold,
                    fail_on=args.fail_on,
                    version=__version__,
                )
            )
        elif not claims:
            print(f"\n{theme.paint(report, 'bold')}\n  没有发现带引用的句子。")
        else:
            print(
                render_terminal(
                    results,
                    report,
                    color=theme.color,
                    verbose=args.verbose,
                    quiet=args.quiet,
                    theme=theme,
                )
            )

        exit_code = max(exit_code, _exit_code(summarize(results), args.fail_on))

    if args.json:
        # 始终输出数组：单文件与多文件的类型必须一致，否则下游解析会分裂
        print("[" + ",\n".join(json_parts) + "]")

    return exit_code


def _no_color_requested(argv: Sequence[str]) -> bool:
    """``--help`` 在参数解析完之前就要输出，先从原始 argv 与环境变量看一眼。"""
    return "--no-color" in argv or os.environ.get("NO_COLOR", "") != ""


if __name__ == "__main__":
    raise SystemExit(main())
