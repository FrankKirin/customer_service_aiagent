"""售后客服的规则路由和槽位抽取。

规则只处理“明显”的问题；规则无法判断时由 router_node 调用 LLM。
不在这里猜测用户意图，避免把“我这个能换吗”直接误判成政策问题。
"""

import re
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field

# 政策/交易类强关键词
POLICY_KEYWORDS = (
    "退货",
    "换货",
    "退换",
    "退款",
    "退掉",
    "退了",
    "换一台",
    "三包",
    "保修",
    "包修",
    "折旧",
    "折旧费",
    "无理由",
    "政策",
    "规定",
    "权益",
    "客户服务热线",
    "服务热线",
    "发票",
    "收费",
    "赔偿",
    "损失费",
    "安装",
    "安检",
    "修理",
    "维修",
    "售后",
    "购买凭证",
    "有效购机凭证",
)

# 说明书/产品使用类强关键词
MANUAL_KEYWORDS = (
    "说明书",
    "故障码",
    "故障代码",
    "e1",
    "e2",
    "e3",
    "显示屏",
    "冷藏室",
    "冷冻室",
    "档位",
    "几档",
    "温度设置",
    "温度调节",
    "除霜",
    "滤网",
    "清洁",
    "保养",
    "噪音",
    "嗡嗡",
    "异响",
    "报警",
    "不制冷",
    "不排水",
    "漏水",
    "不启动",
    "跳闸",
    "门封",
    "密封条",
    "操作步骤",
    "怎么用",
    "如何设置",
    "如何调节",
)

# 没有明显业务关键词，但存在指代/省略，应该追问而不是猜
AMBIGUOUS_PATTERNS = (
    "这个",
    "那个",
    "这种",
    "这样",
    "它",
    "还能",
    "能换",
    "能退",
    "可以换",
    "可以退",
    "行不行",
    "怎么办",
)


class RuleRoutingResult(BaseModel):
    """规则路由结果。"""

    query_type: Literal["policy", "manual", "general"] = Field(
        description="policy / manual / general"
    )
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str


def _matched_keywords(text: str, keywords: tuple[str, ...]) -> list[str]:
    return [keyword for keyword in keywords if keyword in text]


# 可能返回RuleRoutingResult对象，也有可能返回None
def classify_by_rules(query: str) -> Optional[RuleRoutingResult]:
    """只对明显单向命中的问题返回结果。"""

    text = query.strip().lower()
    if not text:
        return None

    policy_hits = _matched_keywords(text, POLICY_KEYWORDS)
    manual_hits = _matched_keywords(text, MANUAL_KEYWORDS)

    if policy_hits and not manual_hits:
        return RuleRoutingResult(
            query_type="policy",
            confidence=min(0.95, 0.75 + 0.05 * len(policy_hits)),
            reason=f"命中政策关键词: {', '.join(policy_hits)}",
        )
    if manual_hits and not policy_hits:
        return RuleRoutingResult(
            query_type="manual",
            confidence=min(0.95, 0.75 + 0.05 * len(manual_hits)),
            reason=f"命中说明书关键词: {', '.join(manual_hits)}",
        )
    # 同时命中或完全没命中时交给 LLM，避免规则互相覆盖
    return None


def looks_ambiguous(query: str) -> bool:
    """判断问题是否存在指代/省略，需要先澄清。"""

    text = query.strip().lower()
    if not text:
        return False
    if any(pattern in text for pattern in AMBIGUOUS_PATTERNS):
        return True
    # 很短的“换/退”类问题通常缺少对象和条件
    return len(text) <= 12 and ("换" in text or "退" in text)


def _extract_purchase_channel(text: str) -> Optional[str]:
    if "线下" in text or "门店" in text:
        return "线下门店"
    if "线上" in text or "商城" in text or "网购" in text:
        return "线上商城"
    if "电视" in text:
        return "电视购物"
    if "电话" in text:
        return "电话订购"
    if "邮购" in text:
        return "邮购"
    return None


def _extract_received_days(text: str) -> Optional[int]:
    match = re.search(r"(?:第)?(\d+)\s*(?:天|日)", text)
    if not match:
        return None
    days = int(match.group(1))
    return days if 0 <= days <= 3650 else None


def _extract_issue_type(text: str) -> Optional[str]:
    troubleshooting_words = (
        "故障",
        "不制冷",
        "不排水",
        "漏水",
        "不启动",
        "报警",
        "异响",
        "噪音",
        "跳闸",
    )
    if any(word in text for word in troubleshooting_words):
        return "troubleshooting"
    if any(word in text for word in ("清洁", "保养", "除霜", "滤网")):
        return "maintenance"
    if any(word in text for word in ("怎么用", "如何设置", "如何调节", "档位", "操作")):
        return "operation"
    return None


def extract_slots(query: str, clarified_info: Dict[str, Any]) -> Dict[str, Any]:
    """把当前问题中明确出现的槽位合并到 clarified_info。

    只写入用户原文明确表达的信息，不推断未知型号、地区和日期。
    """

    text = query.strip()
    merged = dict(clarified_info)

    purchase_channel = _extract_purchase_channel(text)
    if purchase_channel:
        merged["purchase_channel"] = purchase_channel

    received_days = _extract_received_days(text)
    if received_days is not None:
        merged["received_days"] = received_days

    issue_type = _extract_issue_type(text)
    if issue_type:
        merged["issue_type"] = issue_type

    return merged
