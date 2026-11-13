已按方案 A 完成 **P0 恢复 + 任务 3.1 / 3.2 / 3.3** 的开发与验证。

## 已完成内容

### 1. P0：恢复可导入状态
- 重建 `/Users/frank/Documents/customer_service_aiagent/agent/state.py`
  - `SourceInfo`
  - `Citation`
  - `SharedState`
  - `PolicyAgentState`
  - `ManualAgentState`
  - `OrchestratorState`
  - `AgentResponse`
- 旧节点未恢复：`agent/graph.py`、`agent/router.py`、`faq/intent/quote` 节点、`memory/*`、`quote/*` 保持删除状态，当前代码不再引用。
- `agent/nodes/general_node.py` 已重写为新的通用问答节点。
- `main.py` 已恢复可导入，并接入新的 `POST /chat`。

### 2. 任务 3.1：主图 Orchestrator
新增：
- `/Users/frank/Documents/customer_service_aiagent/agent/main_graph.py`
- `/Users/frank/Documents/customer_service_aiagent/agent/session.py`
- `/Users/frank/Documents/customer_service_aiagent/agent/routing_rules.py`
- `/Users/frank/Documents/customer_service_aiagent/agent/nodes/prepare_node.py`
- `/Users/frank/Documents/customer_service_aiagent/agent/nodes/router_node.py`
- `/Users/frank/Documents/customer_service_aiagent/agent/nodes/clarification_node.py`
- `/Users/frank/Documents/customer_service_aiagent/agent/nodes/finalize_node.py`

主流程：
```text
START
  -> prepare_turn
  -> router
  -> clarify / policy_agent / manual_agent / general_agent
  -> finalize
  -> END
```

已实现：
- 规则优先、LLM 兜底路由
- `query_type = policy / manual / general`
- 指代/省略问题先澄清，例如“我这个能换吗？”
- 多轮澄清信息通过 `clarified_info` 保留
- `InMemorySaver` + `thread_id=session_id`
- 每轮重置临时状态，避免上一轮政策结论污染下一轮
- 澄清轮只返回澄清问题，不返回未形成结论的引用
- `processing_time_ms`、`should_escalate`、`source_citations` 汇总

### 3. 任务 3.2：政策问答子图
新增：
- `/Users/frank/Documents/customer_service_aiagent/agent/subgraphs/policy_agent.py`
- `/Users/frank/Documents/customer_service_aiagent/agent/prompts/policy_prompt.py`

实现：
- 政策检索 `top_k=5`
- 只基于真实政策原文生成结构化回答
- 输出 `PolicyAnswer` JSON 契约
- `used_sources` 只允许真实存在的 `S1/S2...` 编号
- 引用从真实 `Document.metadata` 构造
- 无检索结果、模型输出不合法、无引用时明确兜底
- 不使用当前不可靠的 `product_line / region` 元数据过滤

### 4. 任务 3.3：说明书问答子图
新增：
- `/Users/frank/Documents/customer_service_aiagent/agent/subgraphs/manual_agent.py`
- `/Users/frank/Documents/customer_service_aiagent/agent/prompts/manual_prompt.py`
- `/Users/frank/Documents/customer_service_aiagent/agent/utils/safety.py`

实现：
- 说明书检索 `top_k=8`
- 输出 `ManualAnswer` JSON 契约
- 生成操作步骤、所需工具、复杂度、是否需要专家
- `tools_needed` 只保留说明书原文中真实出现的工具
- 安全提示只从真实说明书原文抽取，不允许 LLM 生成
- 修复 PDF 软换行：只合并“以免 / 以及 / 、 / ：”等明确续行，避免整页文字粘成一条警告
- 输出真实引用，包含说明书文件名、页码和章节

### 5. API
新增：
- `/Users/frank/Documents/customer_service_aiagent/api/__init__.py`
- `/Users/frank/Documents/customer_service_aiagent/api/chat_router.py`

接口：
```http
POST /chat
Content-Type: application/json
```

请求示例：
```json
{
  "query": "收到货第5天发现性能故障，可以退货吗？",
  "session_id": "user-session-001",
  "user_id": "user-001"
}
```

响应：
```json
{
  "response": "...",
  "query_type": "policy",
  "source_citations": [],
  "should_escalate": false,
  "processing_time_ms": 1234.5
}
```

多轮对话必须复用同一个 `session_id`。

## 测试结果

已执行：

```text
python -c "import agent.tools"          -> ok
python -c "import main"                 -> ok
python -m pytest -q                     -> 92 passed, 5 skipped
```

默认跳过的 5 个测试是需要真实知识库/真实 LLM 的集成测试。

真实主图冒烟测试已单独执行：

```text
RUN_AGENT_EVAL=1 python -m pytest tests/integration/test_agent_graph.py -q
-> 1 passed
```

同时用真实知识库和真实 LLM 验证了：
- 政策问题返回政策条款引用
- 说明书问题返回操作步骤、真实页码引用和原文安全提示
- 多轮澄清后继续回答
- `POST /chat` 返回结构和引用序列化正常

新增测试文件均位于：
- `/Users/frank/Documents/customer_service_aiagent/tests/unit/`
- `/Users/frank/Documents/customer_service_aiagent/tests/integration/test_agent_graph.py`

## 当前已知限制

1. **会话存储是进程内内存**
   - 使用 `InMemorySaver`
   - 服务重启会丢失会话
   - 不适合多 worker 部署
   - 生产环境建议后续接入 Redis/SQLite checkpoint

2. **知识库元数据不足**
   - 当前真实 Chroma 元数据缺少 `product_model / issue_type / product_line / region`
   - 任务 3 只记录这些槽位，没有启用对应过滤条件
   - 等数据方补齐元数据后再开启精准过滤

3. **说明书缺失页面**
   - 说明书第 8–17、31–33 页存在图片/残片问题
   - 这些页面可能走低置信度兜底
   - 需要后续 OCR 或补充可解析文本

4. **API 形态变化**
   - 旧 `GET /chat` SSE 流式接口已被替换为 `POST /chat` JSON 接口
   - 如果已有前端依赖旧接口，需要同步调整

5. **未使用自造业务数据**
   - 生产路径只使用真实知识库、真实 embedding 和真实 LLM
   - 单元测试中的 LLM 响应用测试替身，不进入生产代码