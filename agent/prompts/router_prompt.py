"""路由提示词。"""

import json
from typing import Any, Dict


def build_router_prompt(
    query: str,
    history: str,
    clarification_pending: bool,
    clarified_info: Dict[str, Any],
) -> str:
    clarified_text = json.dumps(clarified_info, ensure_ascii=False)
    return f"""你是韩电售后客服系统的意图路由器，只判断问题类型，不回答用户问题。

可选意图：
- policy：退换货、退款、三包、保修、折旧、收费、赔偿、发票、售后政策、服务权益、退换货流程。
- manual：产品说明书、使用操作、功能设置、清洁保养、故障码、异常现象、故障排查。
- general：与售后政策、产品使用都无关的普通问题。

判断规则：
1. 用户明确问政策/退换/保修/收费/流程，归为 policy。
2. 用户明确问说明书/操作/设置/故障现象/故障码，归为 manual。
3. 不能因为出现“冰箱”“产品”等词就把问题归为 policy 或 manual。
4. 如果问题存在“这个/那个/它/还能换吗”等指代或省略，缺少判断对象，应设置 needs_clarification=true，并把 missing_info 填为 ["query_scope"]。
5. 如果上一轮正在等待用户澄清，必须结合历史消息理解当前回答；用户已经补充的信息放入 clarified_info。
6. 不要编造用户没有提供的产品型号、地区、购买渠道、日期或故障信息。

会话历史：
{history or "无"}

上一轮是否正在等待澄清：{"是" if clarification_pending else "否"}

已知澄清信息：
{clarified_text}

当前问题：
{query}

只输出一个 JSON 对象，不要输出 Markdown。格式必须是：
{{
  "query_type": "policy",
  "confidence": 0.0,
  "missing_info": [],
  "needs_clarification": false,
  "clarification_question": "",
  "clarified_info": {{}},
  "product_line": null,
  "region": null,
  "product_model": null,
  "issue_type": null
}}
"""
