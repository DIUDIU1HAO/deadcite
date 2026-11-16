"""命令行入口。"""

from __future__ import annotations

import argparse
import os
import sys
from typing import List, Optional, Sequence

from . import __version__
from .checks import check, summarize
from .parse import collect_claims, parse_references
from .report import render_json, render_terminal
from .sources import SourceIndex, load_sources

EPILOG = """示例:
  deadcite report.md                      体检报告，信源自动取同目录 sources/
  deadcite report.md -s sources/          指定信源目录
  deadcite report.md -s a.md -s b.pdf     多个信源（本期只支持文本类文件）
  deadcite report.md --fetch              对引用里的链接实际发起请求
  deadcite report.md --json > out.json    机器可读结果

退出码: 0 未发现问题 / 1 发现问题 / 2 参数或读取错误
"""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="deadcite",
        description="找出 AI 生成报告里的假引用：不存在的来源、打不开的链接、原文里根本没有的数字。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=EPILOG,
    )
    p.add_argument("reports", nargs="+", help="要体检的报告文件（Markdown 或纯文本）")
    p.add_argument(
        "-s",
        "--sources",
        action="append",
        default=[],
        metavar="PATH",
        help="信源文件或目录，可重复；默认尝试报告同目录下的 sources/",
    )
    p.add_argument(
        "--fetch",
        action="store_true",
        help="对引用中的链接实际发起请求（默认只看本地信源）",
    )
    p.add_argument(
        "--threshold",
        type=float,
        default=0.16,
        help="字面重合度低于此值记为可疑，默认 0.16",
    )
    p.add_argument(
        "--fail-on",
        default="problem",
        choices=["problem", "suspect", "none"],
        help="退出码触发阈值，默认 problem",
    )
    p.add_argument("--json", action="store_true", help="输出 JSON")
    p.add_argument("--no-color", action="store_true", help="禁用彩色")
    p.add_argument("-v", "--verbose", action="store_true", help="同时列出通过的引用")
    p.add_argument("--version", action="version", version=f"deadcite {__version__}")
    return p


def _sync_stdout() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def _read(path: str) -> str:
    with open(path, "rb") as fh:
        blob = fh.read()
    for enc in ("utf-8", "gb18030"):
        try:
            return blob.decode(enc)
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("utf-8", blob, 0, 1, "无法解码")


def _default_sources(report: str) -> List[str]:
    guess = os.path.join(os.path.dirname(os.path.abspath(report)), "sources")
    return [guess] if os.path.isdir(guess) else []


def main(argv: Optional[Sequence[str]] = None) -> int:
    _sync_stdout()
    args = build_parser().parse_args(argv)

    if not 0 < args.threshold <= 1:
        print("deadcite: --threshold 需要落在 (0, 1] 区间", file=sys.stderr)
        return 2

    sources = list(args.sources) or _default_sources(args.reports[0])
    index = SourceIndex(load_sources(sources)) if sources else SourceIndex([])

    if not index.usable and not args.fetch:
        print(
            "deadcite: 没有可用信源，只能检查引用是否悬空。"
            "用 -s 指定信源目录，或加 --fetch 联网校验。",
            file=sys.stderr,
        )

    exit_code = 0
    json_parts: List[str] = []

    for report in args.reports:
        try:
            text = _read(report)
        except (OSError, UnicodeDecodeError) as exc:
            print(f"deadcite: 读不了 {report}：{exc}", file=sys.stderr)
            return 2

        refs = parse_references(text)
        claims = list(collect_claims(text))
        results = check(
            claims, refs, index, threshold=args.threshold, allow_fetch=args.fetch
        )

        if args.json:
            json_parts.append(render_json(results, report))
        else:
            if not claims:
                print(f"\n{report}\n  没有发现带引用的句子。")
            else:
                print(render_terminal(results, report, color=not args.no_color, verbose=args.verbose))

        stats = summarize(results)
        if args.fail_on == "problem" and stats["problems"]:
            exit_code = 1
        elif args.fail_on == "suspect" and (stats["problems"] or stats["suspects"]):
            exit_code = 1

    if args.json:
        # 始终输出数组：单文件与多文件的类型必须一致，否则下游解析会分裂
        print("[" + ",\n".join(json_parts) + "]")

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
