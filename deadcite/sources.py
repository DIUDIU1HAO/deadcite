"""信源加载、文本归一化、局部比对与数字抽取。"""

from __future__ import annotations

import html
import os
import re
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

TEXT_EXTS = {".md", ".markdown", ".txt", ".text", ".html", ".htm", ".rst", ".csv"}

TAG_RE = re.compile(r"<[^>]+>")
SCRIPT_RE = re.compile(r"<(script|style)\b.*?</\1>", re.S | re.I)

# 数字：允许千分位与小数
NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")

KEEP_RE = re.compile(r"[^0-9a-z\u4e00-\u9fff]+")


@dataclass
class Source:
    name: str
    text: str
    origin: str
    error: Optional[str] = None


def _read_html(raw: str) -> str:
    raw = SCRIPT_RE.sub(" ", raw)
    text = TAG_RE.sub(" ", raw)
    return html.unescape(text)


def load_sources(paths: Sequence[str]) -> List[Source]:
    """加载文件或目录下的文本信源。PDF/Word 不在本期范围内，会明确报错。"""
    found: List[Source] = []
    files: List[str] = []
    for p in paths:
        if os.path.isdir(p):
            for root, _dirs, names in os.walk(p):
                for name in sorted(names):
                    if os.path.splitext(name)[1].lower() in TEXT_EXTS:
                        files.append(os.path.join(root, name))
        elif os.path.isfile(p):
            files.append(p)
        else:
            found.append(Source(name=os.path.basename(p), text="", origin=p, error="路径不存在"))

    for path in files:
        try:
            with open(path, "rb") as fh:
                blob = fh.read()
        except OSError as exc:
            found.append(Source(os.path.basename(path), "", path, error=str(exc)))
            continue
        for enc in ("utf-8", "gb18030"):
            try:
                raw = blob.decode(enc)
                break
            except UnicodeDecodeError:
                raw = ""
        if not raw:
            found.append(Source(os.path.basename(path), "", path, error="无法解码"))
            continue
        text = _read_html(raw) if path.lower().endswith((".html", ".htm")) else raw
        found.append(Source(name=os.path.basename(path), text=text, origin=path))
    return found


def fetch_url(url: str, timeout: float = 10.0) -> Source:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "deadcite/0.1 (+https://github.com/DIUDIU1HAO/deadcite)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            raw = resp.read(2_000_000).decode(charset, errors="replace")
        return Source(name=url, text=_read_html(raw), origin=url)
    except (urllib.error.URLError, ValueError, OSError) as exc:
        return Source(name=url, text="", origin=url, error=str(exc))


def normalize(text: str) -> str:
    """NFKC 归一后只保留字母、数字与汉字；全角标点与空白在此被消掉。"""
    text = unicodedata.normalize("NFKC", text).lower()
    return KEEP_RE.sub("", text)


def shingles(text: str, n: int = 3) -> set:
    norm = normalize(text)
    if len(norm) < n:
        return {norm} if norm else set()
    return {norm[i : i + n] for i in range(len(norm) - n + 1)}


def similarity(a: str, b: str) -> float:
    sa, sb = shingles(a), shingles(b)
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    return inter / (len(sa) + len(sb) - inter)


def split_chunks(text: str, size: int = 420, overlap: int = 120) -> List[str]:
    """按空行切段；过长段落再滑窗切开，避免一段几千字拉低相似度。"""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    out: List[str] = []
    for para in paras:
        if len(para) <= size:
            out.append(para)
            continue
        step = max(1, size - overlap)
        for start in range(0, len(para), step):
            piece = para[start : start + size]
            if piece.strip():
                out.append(piece)
            if start + size >= len(para):
                break
    return out


def numbers_in(text: str) -> set:
    """抽取文本中的数字（去千分位、去尾随零），用于判断某个数字是否在源文中出现过。"""
    out = set()
    for m in NUMBER_RE.finditer(unicodedata.normalize("NFKC", text)):
        raw = m.group(0).replace(",", "")
        if "." in raw:
            raw = raw.rstrip("0").rstrip(".")
        if raw:
            out.add(raw)
    return out


def is_distinctive(number: str) -> bool:
    """一两位的整数在长文里几乎必然出现，检查它没有意义。"""
    digits = number.replace(".", "")
    return "." in number or len(digits) >= 2


@dataclass
class Match:
    score: float
    chunk: str
    source: str


class SourceIndex:
    def __init__(self, sources: Iterable[Source]):
        self.sources: List[Source] = [s for s in sources]
        self._chunks: List[Tuple[str, str]] = []  # (source_name, chunk)
        self._numbers: Dict[str, set] = {}
        for src in self.sources:
            if src.error or not src.text.strip():
                continue
            for chunk in split_chunks(src.text):
                self._chunks.append((src.name, chunk))
            self._numbers[src.name] = numbers_in(src.text)

    @property
    def usable(self) -> List[Source]:
        return [s for s in self.sources if not s.error and s.text.strip()]

    def by_name(self, name: str) -> Optional[Source]:
        for src in self.sources:
            if src.name == name or os.path.basename(src.origin) == name:
                return src
        return None

    def find_local_by_url(self, url: str) -> Optional[Source]:
        base = os.path.basename(url.split("?")[0].rstrip("/"))
        if not base:
            return None
        stem = os.path.splitext(base)[0].lower()
        for src in self.sources:
            name_stem = os.path.splitext(os.path.basename(src.origin))[0].lower()
            if name_stem and (name_stem == stem or stem in name_stem):
                return src
        return None

    def match_title(self, title: str) -> Optional[Source]:
        """参考文献没给链接时，按标题与文件名/首行做粗匹配。"""
        best: Optional[Tuple[float, Source]] = None
        for src in self.usable:
            head = src.text.strip().splitlines()[0] if src.text.strip() else ""
            score = max(similarity(title, src.name), similarity(title, head))
            if best is None or score > best[0]:
                best = (score, src)
        if best and best[0] >= 0.34:
            return best[1]
        return None

    def best(self, query: str) -> Optional[Match]:
        best: Optional[Match] = None
        for name, chunk in self._chunks:
            score = similarity(query, chunk)
            if best is None or score > best.score:
                best = Match(score=score, chunk=chunk, source=name)
        return best

    def numbers_of(self, source_name: str) -> set:
        return self._numbers.get(source_name, set())
