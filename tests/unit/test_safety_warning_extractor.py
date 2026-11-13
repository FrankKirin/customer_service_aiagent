"""安全提示抽取测试。

测试文本取自真实说明书片段，不构造虚假的产品安全要求。
"""

from langchain_core.documents import Document

from agent.utils.safety import extract_safety_warnings


def test_extracts_heading_and_following_sentence_from_real_manual_excerpt():
    document = Document(
        page_content="注意：\n接通电源前，请仔细阅读说明书，正确使用冰箱，并妥善保管。"
    )

    warnings = extract_safety_warnings([document])

    assert warnings == ["注意：接通电源前，请仔细阅读说明书，正确使用冰箱，并妥善保管。"]


def test_merges_soft_line_break_inside_safety_sentence():
    document = Document(
        page_content=(
            "2.插头一定要保持清洁，安全插入到底，不易脱落。"
            "插拔电源时要手持插头。切勿使冰箱挤压插头；"
            "切勿损伤电源线，也不要让电源线贴近压缩机以免\n"
            "绝缘失效；切勿使用损伤的电源线、插头和插座。"
        )
    )

    warnings = extract_safety_warnings([document])

    assert any("以免绝缘失效" in warning for warning in warnings)


def test_returns_empty_when_no_safety_keyword_exists():
    document = Document(page_content="冰箱外观、颜色或图案见实物。")

    assert extract_safety_warnings([document]) == []


def test_same_warning_is_deduplicated():
    content = "注意：\n接通电源前，请仔细阅读说明书，正确使用冰箱，并妥善保管。"
    documents = [
        Document(page_content=content),
        Document(page_content=content),
    ]

    assert extract_safety_warnings(documents) == [
        "注意：接通电源前，请仔细阅读说明书，正确使用冰箱，并妥善保管。"
    ]
