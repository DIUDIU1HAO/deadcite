"""终端呈现层：颜色探测、按显示宽度折行、图标降级。

本模块只用标准库：为一个几百行的 CLI 引入 Markdown 解析器不划算。
颜色降级与 ruff / clap 一致，四者任一命中即输出纯文本：

* 显式传入 ``--no-color``
* 环境变量 ``NO_COLOR`` 非空（见 https://no-color.org）
* ``TERM=dumb`` 或 ``CLICOLOR=0``
* 输出流不是 TTY（管道、重定向、CI）

这样 ``deadcite report.md | tee out.txt`` 不会把 ANSI 转义写进文件。
"""

from __future__ import annotations

import os
import sys
import unicodedata
from typing import Dict, IO, List, Optional, Sequence, Tuple

RESET = "\033[0m"

CODES: Dict[str, str] = {
    "reset": RESET,
    "bold": "\033[1m",
    "dim": "\033[2m",
    "red": "\033[31m",
    "yellow": "\033[33m",
    "green": "\033[32m",
    "cyan": "\033[36m",
}

# 状态图标：Unicode 与 ASCII 两套，GBK 终端下不会输出方块
ICONS: Dict[str, str] = {"problem": "✗", "suspect": "!", "ok": "✓"}
ASCII_ICONS: Dict[str, str] = {"problem": "x", "suspect": "!", "ok": "+"}

ELLIPSIS = "…"
ELLIPSIS_ASCII = "~"

# 优先在这些字符之后断开。只收全角标点、空格与 "/"：
# ASCII 的 , ; : 出现在 URL、版本号里，拿它当断点会把链接切碎。
_BREAK_AFTER = set(" ，。、；：！？）」』】》…·/")


def char_width(ch: str) -> int:
    """单个字符的终端显示宽度：东亚全角字符占两列。"""
    return 2 if unicodedata.east_asian_width(ch) in "WF" else 1


def display_width(text: str) -> int:
    """整串字符的显示宽度，用于对齐而不是 ``len()``。"""
    return sum(char_width(ch) for ch in text)


def pad(text: str, target: int) -> str:
    """右侧补空格到目标显示宽度。"""
    return text + " " * max(0, target - display_width(text))


def truncate(text: str, limit: int, ellipsis: str = ELLIPSIS) -> str:
    """按显示宽度截断，超长时补省略号；不会把一个汉字切成两半。"""
    if limit <= 0:
        return ""
    if display_width(text) <= limit:
        return text
    budget = limit - display_width(ellipsis)
    out: List[str] = []
    used = 0
    for ch in text:
        w = char_width(ch)
        if used + w > budget:
            break
        out.append(ch)
        used += w
    return "".join(out).rstrip() + ellipsis


def _soft_cut(text: str, hard: int) -> int:
    """在硬切点之前找一个更自然的断点。

    规则：优先断在最近的标点/空格之后；若最近的断点已经很远，说明正踩在一个
    不可分割的长 token 上（URL、长英文词），此时把整个 token 挪到下一行，
    避免出现 ``sto`` / ``re-efficiency`` 这种残片。找不到合适位置才硬切。
    """
    stop = 0
    for i in range(hard, 1, -1):
        if text[i - 1] in _BREAK_AFTER or text[i - 1] == " ":
            stop = i
            break
    if stop < 8:  # 断点太靠前，切出来几乎是空行，不如硬切
        return hard
    window = max(8, hard // 3)
    if hard - stop <= window:
        return stop
    return stop if hard - stop >= 12 else hard


def wrap(text: str, width: int, indent: int = 0) -> List[str]:
    """按显示宽度折行，返回**不含缩进**的内容行。

    ``width`` 是含缩进的总宽度，``indent`` 是调用方会补上的前缀宽度，
    因此每行内容都严格不超过 ``width - indent``，补上前缀后正好不溢出。
    """
    text = " ".join(text.split())
    budget = max(8, width - indent)
    if not text:
        return []
    lines: List[str] = []
    remaining = text
    while remaining:
        if display_width(remaining) <= budget:
            lines.append(remaining)
            break
        used = 0
        hard = 0
        for i, ch in enumerate(remaining):
            w = char_width(ch)
            if used + w > budget:
                break
            used += w
            hard = i + 1
        cut = max(1, _soft_cut(remaining, hard)) if hard else 1
        head = remaining[:cut].rstrip()
        if head:
            lines.append(head)
        remaining = remaining[cut:].lstrip()
    return lines


def two_columns(rows: Sequence[Tuple[str, str]], gap: int = 2) -> List[str]:
    """把 (左, 右) 渲染成按显示宽度对齐的两列文本。"""
    if not rows:
        return []
    left_width = max(display_width(left) for left, _ in rows)
    return [pad(left, left_width + gap) + right for left, right in rows]


def supports_unicode(stream: Optional[IO[str]] = None) -> bool:
    """输出流能否编码状态图标；Windows 的 GBK 控制台不能。"""
    stream = sys.stdout if stream is None else stream
    encoding = getattr(stream, "encoding", None) or "utf-8"
    try:
        "✗✓─·…".encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return False
    return True


def _is_tty(stream: IO[str]) -> bool:
    try:
        return bool(stream.isatty())
    except (AttributeError, OSError, ValueError):  # pytest 的捕获对象等
        return False


def _enable_windows_vt() -> bool:
    """Windows 10+ 打开 VT 处理；老控制台返回 False，调用方降级为无色。"""
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        enable_vt = 0x0004
        if mode.value & enable_vt:
            return True
        return bool(kernel32.SetConsoleMode(handle, mode.value | enable_vt))
    except (AttributeError, OSError, ValueError):
        return False


def supports_color(stream: Optional[IO[str]] = None) -> bool:
    """判断该输出流是否可以安全地写 ANSI 颜色。"""
    stream = sys.stdout if stream is None else stream
    if not _is_tty(stream):
        return False
    if os.environ.get("NO_COLOR", "") != "":
        return False
    if os.environ.get("TERM", "") == "dumb":
        return False
    if os.environ.get("CLICOLOR", "") == "0":
        return False
    if os.name == "nt":
        return _enable_windows_vt()
    return True


class Theme:
    """一组已决定好是否着色的样式工具，渲染函数只依赖它，不碰全局状态。"""

    def __init__(self, color: bool = False, unicode: bool = True) -> None:
        self.color = color
        self.unicode = unicode

    @classmethod
    def plain(cls) -> "Theme":
        """纯文本主题：无颜色、ASCII 图标，适合测试与日志。"""
        return cls(color=False, unicode=False)

    def paint(self, text: str, *styles: str) -> str:
        """按顺序套用样式；未启用颜色时原样返回。"""
        if not self.color or not text:
            return text
        prefix = "".join(CODES[s] for s in styles if s in CODES)
        return f"{prefix}{text}{RESET}" if prefix else text

    def icon(self, status: str) -> str:
        table = ICONS if self.unicode else ASCII_ICONS
        return table.get(status, "?")

    @property
    def ellipsis(self) -> str:
        return ELLIPSIS if self.unicode else ELLIPSIS_ASCII

    @property
    def bullet(self) -> str:
        return "·" if self.unicode else "-"

    def rule(self, limit: int) -> str:
        return self.paint("─" * limit if self.unicode else "-" * limit, "dim")
