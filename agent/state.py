"""Agent 共享状态定义。

约定：
- 节点接收的是 Pydantic State 对象，字段通过属性访问；
- 主图使用 OrchestratorState，政策子图使用 PolicyAgentState，
  说明书子图使用 ManualAgentState；
- 所有子图字段都必须在 OrchestratorState 中存在，否则父图会丢弃。
"""

import operator
from datetime import datetime
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


class SharedState(BaseModel):
    """主图和两个子图共享的基础状态。"""

    # 用户输入
    query: str = Field(description="用户原始查询")
    user_id: Optional[str] = Field(default=None, description="用户ID")
    session_id: str = Field(description="会话ID")

    # 路由信息
    query_type: Literal["policy", "manual", "general"] = Field(
        default="general", description="查询类型"
    )
    missing_info: List[str] = Field(default_factory=list, description="缺失的关键信息列表")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="路由置信度")

    # 澄清管理
    clarification_needed: bool = Field(default=False, description="是否需要澄清")
    clarification_question: str = Field(default="", description="澄清问题")
    clarified_info: Dict[str, Any] = Field(default_factory=dict, description="已澄清的信息")

    # 检索结果
    retrieved_docs: List[Document] = Field(default_factory=list, description="检索到的文档片段")
    source_info: List[SourceInfo] = Field(default_factory=list, description="来源信息")
    retrieval_confidence: float = Field(
        default=0.0, ge=0.0, le=1.0, description="检索置信度"
    )

    # 响应生成
    response: str = Field(default="", description="最终响应")
    source_citations: List[Citation] = Field(default_factory=list, description="引用的来源")
    should_escalate: bool = Field(default=False, description="是否需要转人工")

    # 元数据
    timestamp: datetime = Field(default_factory=datetime.now, description="时间戳")
    processing_time_ms: float = Field(default=0.0, ge=0.0, description="处理时间(ms)")

    # LangGraph 需要的历史消息
    messages: Annotated[List[AnyMessage], operator.add] = Field(
        default_factory=list, description="消息历史"
    )

    model_config = ConfigDict(arbitrary_types_allowed=True)


class PolicyAgentState(SharedState):
    """政策问答子图状态。"""

    product_line: Optional[str] = Field(default=None, description="产品线")
    region: Optional[str] = Field(default=None, description="地区")
    policy_applicability: Literal[
        "applicable", "not_applicable", "conditional", "unknown"
    ] = Field(default="unknown", description="政策适用性")
    policy_conditions: Dict[str, Any] = Field(default_factory=dict, description="适用条件")
    policy_summary: str = Field(default="", description="政策摘要")
    policy_violations: List[str] = Field(default_factory=list, description="不符合政策的地方")


class ManualAgentState(SharedState):
    """说明书问答子图状态。"""

    product_model: Optional[str] = Field(default=None, description="产品型号")
    product_version: Optional[str] = Field(default=None, description="产品版本")
    issue_type: Optional[
        Literal["operation", "maintenance", "troubleshooting"]
    ] = Field(default=None, description="问题类型")
    step_by_step_guide: bool = Field(default=False, description="是否包含步骤指导")
    safety_warnings: List[str] = Field(default_factory=list, description="安全警告")
    tools_needed: List[str] = Field(default_factory=list, description="所需工具")

    # 技术指标
    requires_expert: bool = Field(default=False, description="是否需要专家协助")
    complexity_level: Literal["low", "medium", "high"] = Field(
        default="low", description="操作复杂度"
    )


class OrchestratorState(PolicyAgentState, ManualAgentState):
    """主图编排状态。

    同时包含政策和说明书子图的字段，保证子图返回的领域字段不会被父图丢弃。
    """


class AgentResponse(BaseModel):
    """对外返回的稳定响应结构。"""

    response: str
    query_type: Literal["policy", "manual", "general"]
    source_citations: List[Citation] = Field(default_factory=list)
    should_escalate: bool = False
    processing_time_ms: float = 0.0
