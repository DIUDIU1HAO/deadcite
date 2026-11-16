"""从真实运行结果生成 README 首屏的终端示意图。

用法：python docs/make_demo_svg.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import unicodedata
import xml.sax.saxutils as sx

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

BG = "#0d1117"
BAR = "#161b22"
FG = "#c9d1d9"
RED = "#f85149"
YELLOW = "#d29922"
GREEN = "#3fb950"
GRAY = "#6e7681"

CHAR_W = 8.4
LINE_H = 21
PAD_X = 22
PAD_Y = 18


def dwidth(text: str) -> float:
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def collect() -> list:
    proc = subprocess.run(
        [sys.executable, "-m", "deadcite", "examples/report.md", "--no-color"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    lines = [ln.rstrip() for ln in proc.stdout.splitlines()]
    # 去掉开头空行与末尾汇总后的空行，只保留最有信息量的部分
    while lines and not lines[0].strip():
        lines.pop(0)
    return lines


def colored_runs(line: str) -> list:
    """把一行拆成 (文本, 颜色) 片段。"""
    for mark, color in (("✗", RED), ("!", YELLOW), ("✓", GREEN)):
        idx = line.find(mark)
        if idx >= 0:
            return [
                (line[:idx], FG),
                (line[idx], color),
                (line[idx + 1 :], FG),
            ]
    if line.lstrip().startswith(("·", "─")):
        return [(line, GRAY)]
    if "条引用" in line:
        return [(line, FG)]
    return [(line, FG)]


def build(lines: list, out_path: str) -> None:
    cols = max(dwidth(ln) for ln in lines) if lines else 80
    width = int(cols * CHAR_W + PAD_X * 2)
    height = int(len(lines) * LINE_H + PAD_Y * 2 + 34)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="ui-monospace, SFMono-Regular, '
        f'Menlo, Consolas, monospace">',
        f'<rect width="{width}" height="{height}" rx="10" fill="{BG}"/>',
        f'<rect width="{width}" height="34" rx="10" fill="{BAR}"/>',
        f'<rect y="24" width="{width}" height="10" fill="{BAR}"/>',
        '<circle cx="20" cy="17" r="5.5" fill="#ff5f56"/>',
        '<circle cx="40" cy="17" r="5.5" fill="#ffbd2e"/>',
        '<circle cx="60" cy="17" r="5.5" fill="#27c93f"/>',
        f'<text x="{width / 2}" y="22" fill="{GRAY}" font-size="12" '
        f'text-anchor="middle">deadcite — 引用体检</text>',
    ]

    y = 34 + PAD_Y + 12
    for line in lines:
        if not line.strip():
            y += LINE_H
            continue
        x = float(PAD_X)
        for text, color in colored_runs(line):
            if not text:
                continue
            parts.append(
                f'<text x="{x:.1f}" y="{y}" fill="{color}" font-size="13" '
                f'xml:space="preserve">{sx.escape(text)}</text>'
            )
            x += dwidth(text) * CHAR_W
        y += LINE_H

    parts.append("</svg>")
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(parts))
    print(f"written {out_path}: {width}x{height}, {len(lines)} lines")


if __name__ == "__main__":
    build(collect(), os.path.join(HERE, "demo.svg"))
