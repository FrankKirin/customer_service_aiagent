"""主图与子图状态定义。

约定：
- 父图只保存需要跨轮持久化、对外输出或参与决策的字段；
- 主图与子图不共享 State，子图只通过输入/输出契约与父图交换数据；
- 子图 State 必须显式声明输出契约字段，否则 LangGraph 会丢弃节点返回值；
- `documents` 保存真实检索到的 Document（文本 + metadata），不是 embedding 向量。
"""

import operator
from typing import Annotated, Any, Dict, List, Literal, Optional

from langchain_core.documents import Document
from langchain_core.messages import AnyMessage
from pydantic import BaseModel, ConfigDict, Field


class SourceInfo(BaseModel):
    """可展示给用户的来源信息。"""

    source_id: str
    source_type: Literal["policy", "manual"] = "policy"
    section: Optional[str] = None
    page: Optional[int] = None
    url: Optional[str] = None
    relevance_score: float = Field(default=0.0, ge=0.0, le=1.0)

    def __str__(self) -> str:
        parts: List[str] = []
        if self.source_type == "policy":
            if self.section:
                parts.append(f"政策条款: {self.section}")
        elif self.source_type == "manual":
            parts.append(f"说明书: {self.source_id}")
            if self.section:
                parts.append(f"说明章节: {self.section}")
            if self.page is not None:
                parts.append(f"页码: {self.page}")

        if self.relevance_score > 0:
            parts.append(f"(相关度:{self.relevance_score:.2f})")

        return " | ".join(parts) if parts else self.source_id


class Citation(BaseModel):
    """回答中实际引用的来源片段。"""

    text: str
    source: SourceInfo
    start_char: int = 0
    end_char: int = 0

    def __str__(self) -> str:
        return f"「{self.text[:50]}...」来源: {self.source}"


class PolicyAgentState(BaseModel):
    """政策子图状态：输入契约 + 子图内部字段 + 输出契约。"""

    # 输入契约（由父图 wrapper 提供）
    user_query: str = Field(description="用户当前问题")
    context_query: str = Field(description="父图构建的检索/生成上下文")

    # 子图内部字段
    documents: List[Document] = Field(
        default_factory=list, description="真实检索到的政策文档片段"
    )
    policy_applicability: Literal[
        "applicable", "not_applicable", "conditional", "unknown"
    ] = Field(default="unknown", description="政策适用性")
    policy_conditions: List[str] = Field(
        default_factory=list, description="政策适用需要满足的条件"
    )
    policy_violations: List[str] = Field(
        default_factory=list, description="用户情况与政策不符的地方"
    )

    # 输出契约（必须由子图返回给父图 wrapper）
    final_answer: str = Field(default="", description="政策子图生成的回答")
    source_citations: List[Citation] = Field(
        default_factory=list, description="回答实际引用的来源"
    )
    should_escalate: bool = Field(default=False, description="是否需要转人工")
    needs_clarification: bool = Field(default=False, description="是否需要用户补充信息")
    clarification_question: str = Field(default="", description="向用户提出的澄清问题")

    model_config = ConfigDict(arbitrary_types_allowed=True)


class ManualAgentState(BaseModel):
    """说明书子图状态：输入契约 + 子图内部字段 + 输出契约。"""

    # 输入契约（由父图 wrapper 提供）
    user_query: str = Field(description="用户当前问题")
    context_query: str = Field(description="父图构建的检索/生成上下文")

    # 子图内部字段
    documents: List[Document] = Field(
        default_factory=list, description="真实检索到的说明书文档片段"
    )
    issue_type: Optional[
        Literal["operation", "maintenance", "troubleshooting"]
    ] = Field(default=None, description="从用户问题中确定的说明书问题类型")
    steps: List[str] = Field(default_factory=list, description="说明书操作/排查步骤")
    safety_warnings: List[str] = Field(
        default_factory=list, description="从说明书原文抽取的安全提示"
    )
    requires_expert: bool = Field(default=False, description="是否需要专家协助")
    complexity_level: Literal["low", "medium", "high"] = Field(
        default="low", description="操作复杂度"
    )

    # 输出契约（必须由子图返回给父图 wrapper）
    final_answer: str = Field(default="", description="说明书子图生成的回答")
    source_citations: List[Citation] = Field(
        default_factory=list, description="回答实际引用的来源"
    )
    should_escalate: bool = Field(default=False, description="是否需要转人工")
    needs_clarification: bool = Field(default=False, description="是否需要用户补充信息")
    clarification_question: str = Field(default="", description="向用户提出的澄清问题")

    model_config = ConfigDict(arbitrary_types_allowed=True)


class OrchestratorState(BaseModel):
    """主图编排状态（薄状态）。

    子图领域字段不会进入父图 checkpoint，父图只接收输出契约字段。
    """

    # 本轮输入与跨轮历史
    user_query: str = Field(description="用户当前原始问题")
    messages: Annotated[List[AnyMessage], operator.add] = Field(
        default_factory=list, description="消息历史"
    )

    # 路由与澄清
    query_type: Literal["policy", "manual", "general"] = Field(
        default="general", description="路由预测的目标领域"
    )
    needs_clarification: bool = Field(default=False, description="本轮是否返回澄清问题")
    clarification_question: str = Field(default="", description="向用户提出的澄清问题")
    clarified_info: Dict[str, Any] = Field(
        default_factory=dict, description="多轮对话中已经确认的槽位"
    )
    missing_info: List[str] = Field(default_factory=list, description="缺失的信息字段")

    # 对外结果
    final_answer: str = Field(default="", description="最终回答")
    source_citations: List[Citation] = Field(
        default_factory=list, description="最终回答引用的来源"
    )
    should_escalate: bool = Field(default=False, description="是否需要转人工")

    model_config = ConfigDict(arbitrary_types_allowed=True)


class AgentResponse(BaseModel):
    """对外返回的稳定响应结构。"""

    response: str
    query_type: Literal["policy", "manual", "general"]
    source_citations: List[Citation] = Field(default_factory=list)  # 引用来源
    should_escalate: bool = False   # 是否转人工
    needs_clarification: bool = False  # 是否需要澄清
    clarification_question: str = ""
    processing_time_ms: float = 0.0   # 处理时间，偏运维观测
