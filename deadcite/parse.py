"""把报告拆成「带引用的论断」，并把参考文献表解析成可查询的结构。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional

# 参考文献小节的标题；中英文都认
REF_HEADING = re.compile(
    r"^\s{0,3}(?:#{1,6}\s*)?"
    r"(参考文献|参考资料|引用来源|信息来源|References?|Sources?|Bibliography|Works\s+Cited)"
    r"\s*[:：]?\s*$",
    re.I,
)

# 正文里的编号引用：[1] [1,2] [1-3] [1，2]
CITE_RE = re.compile(r"\[(\d{1,3}(?:\s*[,，、\-–—]\s*\d{1,3})*)\]")

MD_LINK_RE = re.compile(r"\[([^\]\n]{0,200})\]\((https?://[^)\s]+)\)")
BARE_URL_RE = re.compile(r"(?<![\](])(https?://[^\s)>\]]+)")

# 反引号内的行内代码：里面的 [7] 是示例，不是引用
INLINE_CODE_RE = re.compile(r"(`+)(?:(?!\1).)+?\1")

# 图片与徽章：Markdown 图片语法，或 shields.io 这类纯展示资源
IMAGE_MD_RE = re.compile(r"!\[[^\]\n]*\]\([^)\n]*\)")
ASSET_URL_RE = re.compile(
    r"\.(?:svg|png|jpe?g|gif|webp|ico)(?:\?|$)|shields\.io|badge|img\.shields",
    re.I,
)

FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")

# 编号列表：`[1] 标题` / `1. 标题` / `1) 标题` / `[1]: 标题`
REF_ENTRY_RE = re.compile(r"^\s*(?:\[(\d{1,3})\]|(\d{1,3})\s*[.)、])\s*[:：]?\s*(.+)$")


@dataclass
class Reference:
    key: str
    title: str
    url: Optional[str]
    raw: str
    line: int


@dataclass
class Claim:
    """一句带引用的话。"""

    text: str
    plain: str  # 去掉引用标记后的纯文本，用于比对
    line: int
    refs: List[str] = field(default_factory=list)
    urls: List[str] = field(default_factory=list)

    @property
    def has_source(self) -> bool:
        return bool(self.refs or self.urls)


def strip_fences(text: str) -> str:
    """把围栏代码块替换成等量空行，保持行号不变。"""
    out: List[str] = []
    inside = False
    for line in text.splitlines():
        if FENCE_RE.match(line):
            inside = not inside
            out.append("")
            continue
        out.append("" if inside else line)
    return "\n".join(out)


def _expand_keys(group: str) -> List[str]:
    keys: List[str] = []
    for part in re.split(r"[,，、]", group):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^(\d{1,3})\s*[\-–—]\s*(\d{1,3})$", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if 0 < b - a <= 30:  # 防止 [1-999] 这种写出几百条
                keys.extend(str(i) for i in range(a, b + 1))
            else:
                keys.extend([m.group(1), m.group(2)])
            continue
        if part.isdigit():
            keys.append(str(int(part)))
    return keys


def parse_references(text: str) -> Dict[str, Reference]:
    """解析参考文献小节。找不到小节时返回空字典。"""
    lines = strip_fences(text).splitlines()
    start = None
    for i, line in enumerate(lines):
        if REF_HEADING.match(line):
            start = i + 1
            break
    if start is None:
        return {}

    refs: Dict[str, Reference] = {}
    for i in range(start, len(lines)):
        line = lines[i]
        if re.match(r"^\s{0,3}#{1,6}\s", line) or REF_HEADING.match(line):
            break  # 进入下一节
        m = REF_ENTRY_RE.match(line)
        if not m:
            continue
        key = m.group(1) or m.group(2)
        body = m.group(3).strip()
        url_m = BARE_URL_RE.search(body)
        url = url_m.group(1).rstrip("。.,；;）)") if url_m else None
        title = BARE_URL_RE.sub("", body).strip(" -—:：\t")
        # 链接被摘掉后常在标题尾部留下一个句点，去掉它（但不碰 "No.1" 这类）
        title = re.sub(r"(?<=[^\d])[.。]\s*$", "", title).strip()
        refs[str(int(key))] = Reference(
            key=str(int(key)), title=title, url=url, raw=body, line=i + 1
        )
    return refs


def split_sentences(line: str) -> List[str]:
    """按中文句末标点切句；不切英文句点，避免把 3.5、URL 切断。"""
    parts = re.split(r"(?<=[。！？!?])\s*", line)
    return [p.strip() for p in parts if p.strip()]


def _reference_start(clean: str) -> Optional[int]:
    """参考文献小节的起始行号（1 基）。这一节之后的内容不再当作正文论断。"""
    for lineno, line in enumerate(clean.splitlines(), start=1):
        if REF_HEADING.match(line):
            return lineno
    return None


def _blank(text: str, pattern: "re.Pattern[str]") -> str:
    """把匹配内容替换成等长空格：屏蔽干扰项，同时不改变字符位置。"""
    return pattern.sub(lambda m: " " * len(m.group(0)), text)


def collect_claims(text: str) -> Iterator[Claim]:
    """逐句产出带引用的论断。代码块、行内代码、图片、参考文献表都不参与。"""
    clean = strip_fences(text)
    ref_start = _reference_start(clean)
    for lineno, line in enumerate(clean.splitlines(), start=1):
        if ref_start is not None and lineno >= ref_start:
            break
        if not line.strip() or line.lstrip().startswith(">"):
            continue
        # 行内代码里的 [7] 只是示例；徽章/图片不是论断
        line = _blank(line, INLINE_CODE_RE)
        line = _blank(line, IMAGE_MD_RE)

        for sentence in split_sentences(line):
            keys: List[str] = []
            for m in CITE_RE.finditer(sentence):
                keys.extend(_expand_keys(m.group(1)))
            urls = [
                m.group(2)
                for m in MD_LINK_RE.finditer(sentence)
                # 标签被清空的链接是徽章/图片的外壳，不是引用
                if m.group(1).strip() and not ASSET_URL_RE.search(m.group(2))
            ]
            for m in BARE_URL_RE.finditer(sentence):
                url = m.group(1)
                if url not in urls and not ASSET_URL_RE.search(url):
                    urls.append(url)

            plain = CITE_RE.sub("", sentence)
            plain = MD_LINK_RE.sub(lambda m: m.group(1), plain)
            plain = BARE_URL_RE.sub("", plain).strip()
            if not keys and not urls:
                continue
            yield Claim(text=sentence.strip(), plain=plain, line=lineno, refs=keys, urls=urls)
