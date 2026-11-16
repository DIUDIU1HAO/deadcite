import os

from deadcite.checks import PROBLEM, SUSPECT, check, summarize
from deadcite.parse import collect_claims, parse_references
from deadcite.sources import (
    Source,
    SourceIndex,
    is_distinctive,
    numbers_in,
    similarity,
    split_chunks,
)

HERE = os.path.dirname(os.path.abspath(__file__))
EXAMPLES = os.path.join(os.path.dirname(HERE), "examples")


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


# --------------------------------------------------------------------------- #
# 解析
# --------------------------------------------------------------------------- #
def test_parse_references_numeric_list():
    text = "正文 [1]。\n\n## 参考文献\n\n[1] 某报告. https://example.com/a\n2. 另一份. https://example.com/b\n"
    refs = parse_references(text)
    assert set(refs) == {"1", "2"}
    assert refs["1"].url == "https://example.com/a"
    assert refs["2"].title == "另一份"


def test_parse_references_without_section_returns_empty():
    assert parse_references("没有参考文献小节，只有正文 [1]。") == {}


def test_collect_claims_expands_ranges():
    claims = list(collect_claims("这句话有三个来源 [1-3]。\n"))
    assert claims[0].refs == ["1", "2", "3"]


def test_collect_claims_skips_code_fence():
    text = "```python\nx = 1  # 见 [1]\n```\n\n正文引用 [1]。\n"
    claims = list(collect_claims(text))
    assert len(claims) == 1
    assert claims[0].line == 5


def test_collect_claims_skips_reference_section():
    text = "正文 [1]。\n\n## 参考文献\n\n[1] 某报告. https://example.com/a\n[2] 另一份. https://example.com/b\n"
    claims = list(collect_claims(text))
    assert len(claims) == 1


def test_collect_claims_recognises_markdown_link():
    claims = list(collect_claims("据报告，增长很快（[来源](https://example.com/x)）。\n"))
    assert claims and claims[0].urls == ["https://example.com/x"]
    assert "来源" in claims[0].plain


# --------------------------------------------------------------------------- #
# 文本工具
# --------------------------------------------------------------------------- #
def test_numbers_in_normalises_thousands_and_trailing_zeros():
    assert numbers_in("规模 1,284 亿元，增长 28.50%") == {"1284", "28.5"}


def test_is_distinctive_filters_single_digits():
    assert not is_distinctive("9")
    assert is_distinctive("16")
    assert is_distinctive("4.9")


def test_similarity_ignores_punctuation_and_width():
    a = "2024 年中国咖啡市场规模达到 1,284 亿元"
    b = "2024年中国咖啡市场规模达到1,284亿元。"
    assert similarity(a, b) > 0.9


def test_split_chunks_breaks_long_paragraph():
    para = "咖啡" * 500
    chunks = split_chunks("标题\n\n" + para, size=100, overlap=20)
    assert len(chunks) > 1
    assert all(len(c) <= 100 for c in chunks)


# --------------------------------------------------------------------------- #
# 判定
# --------------------------------------------------------------------------- #
def _run(report_text, sources, threshold=0.16):
    refs = parse_references(report_text)
    claims = list(collect_claims(report_text))
    index = SourceIndex([Source(name=n, text=t, origin=n) for n, t in sources.items()])
    return check(claims, refs, index, threshold=threshold)


def test_number_absent_from_source_is_problem():
    report = "规模达到 1,842 亿元 [1]。\n\n## 参考文献\n\n[1] 报告. https://example.com/r\n"
    results = _run(report, {"r": "2024 年市场规模 1,284 亿元，同比增长 28.5%。"})
    kinds = {i.kind for v in results for i in v.issues}
    assert "NUMBER_NOT_FOUND" in kinds
    assert results[0].status == PROBLEM


def test_matching_number_passes():
    report = "规模达到 1,284 亿元 [1]。\n\n## 参考文献\n\n[1] 报告. https://example.com/r\n"
    results = _run(report, {"r": "2024 年市场规模 1,284 亿元，同比增长 28.5%。"})
    assert results[0].status != PROBLEM


def test_missing_reference_number_is_problem():
    report = "这一说法来自 [6]。\n\n## 参考文献\n\n[1] 报告. https://example.com/r\n"
    results = _run(report, {"r": "随便什么内容，足够长才能产生相似度。"})
    assert any(i.kind == "MISSING_REF" for i in results[0].issues)
    assert results[0].status == PROBLEM


def test_reference_without_link_and_no_local_file_is_problem():
    report = "据预测将突破 3,000 亿元 [4]。\n\n## 参考文献\n\n[4] 某行业白皮书 2025\n"
    results = _run(report, {"r": "完全不相干的信源内容，用来占位。"})
    assert results[0].status == PROBLEM


def test_unresolved_link_without_fetch_is_only_suspect():
    report = "某品牌日均出杯 1,200 杯 [2]。\n\n## 参考文献\n\n[2] 观察. https://example.invalid/x\n"
    results = _run(report, {"r": "占位内容。"})
    assert results[0].status == SUSPECT


def test_low_overlap_is_suspect_not_problem():
    report = "整个产业链正在经历一场由数字化驱动的结构性重塑 [1]。\n\n## 参考文献\n\n[1] 报告. https://example.com/r\n"
    results = _run(report, {"r": "2024 年市场规模 1,284 亿元，同比增长 28.5%。"})
    assert results[0].status == SUSPECT


# --------------------------------------------------------------------------- #
# 端到端：仓库自带的演示数据
# --------------------------------------------------------------------------- #
def test_example_report_counts():
    from deadcite import audit

    results = audit(read(os.path.join(EXAMPLES, "report.md")), [os.path.join(EXAMPLES, "sources")])
    stats = summarize(results)
    assert stats["total"] == 12
    assert stats["problems"] == 4
    assert stats["suspects"] == 2


def test_cli_exit_code_is_one_when_problems_found(capsys):
    from deadcite.cli import main

    code = main([os.path.join(EXAMPLES, "report.md"), "--no-color"])
    assert code == 1


def test_cli_json_output_is_valid(capsys):
    import json

    from deadcite.cli import main

    main([os.path.join(EXAMPLES, "report.md"), "--json"])
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["summary"]["total"] == 12
    assert len(payload["citations"]) == 12


# --------------------------------------------------------------------------- #
# 误报回归：这几类都不该被当成引用
# --------------------------------------------------------------------------- #
def test_inline_code_is_not_a_citation():
    """反引号里的 [7] 是示例，不是真的引用。"""
    text = "正文引用了 `[7]`，但参考文献表里只有 5 条。\n"
    assert list(collect_claims(text)) == []


def test_badge_links_are_not_citations():
    """徽章与图片链接不该被当成信源。"""
    text = (
        "[![Python](https://img.shields.io/badge/python-3.8-blue.svg)]"
        "(https://www.python.org)\n"
    )
    assert list(collect_claims(text)) == []


def test_inline_markdown_link_counts_as_citation():
    """报告里「据[统计局](url)数据」是真实引用形式，必须保留这个能力。"""
    text = "据[国家统计局](https://example.com/data)数据，产量增长 12%。\n"
    claims = list(collect_claims(text))
    assert len(claims) == 1 and claims[0].urls == ["https://example.com/data"]


def test_real_citation_still_detected_after_masking():
    text = "市场规模为 1,842 亿元 [1]。\n"
    claims = list(collect_claims(text))
    assert len(claims) == 1 and claims[0].refs == ["1"]
