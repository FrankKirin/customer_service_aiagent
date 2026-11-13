State字段只有满足以下条件材质的保留
    - __需要跨节点传递__：例如检索节点产生的 `documents` 要给生成节点使用。

    - __需要跨轮持久化__：例如 `messages`、上一轮澄清状态。

    - __需要对外输出__：例如 `source_citations`、`should_escalate`。

    - __需要参与路由、澄清或降级决策__：例如 `needs_clarification`。

父图State：
    -user_query:用户的原始提问
    -final_answer:最终的agent回答答案
    -query_type：路由预测出的目标领域
    -needs_clarification: 本轮是否先返回澄清问题，True/False
    -clarification_question: 反问用户，让用户澄清的问题
    -messages: 历史消息
    -source_citations: 来源引用list[Citation]
    -should_escalate: 是否需要人工介入，True/False
policy子图State:
    -documents:List[Document]:rag查询返回的文档向量
    -policy_applicability:applicable, not_applicable, conditional, unknow: 政策适用性判断
    -policy_conditions:list[str]: 政策适用需要满足的条件
    -policy_violations: 用户当前情况与政策不符的地方
    -retrieval_confidence: 根据检索文档真实relevance_score计算出的平均检索置信度
manual子图State：
    -documents: rag检索返回的向量文档
    -issue_type: operation/ maintenance / troubleshooting：说明书问题类型
    -steps: 说明书使用步骤
    -safety_warnings: 说明书中提取的安全提示
    -requires_expert True/False: 判断问题是否需要专家处理(转人工)
    -complexity_level: 操作复杂度

policy子图输入契约：
    -user_query
    -context_query: 父图传给子图的检索/生成上下文查询(只有最近几轮用户消息, 合并确认槽位,做长度限制)

manual子图输入契约：
    -user_query
    -context_query

policy子图输出契约：
    -final_answer
    -source_citations
    -should_escalate
    -needs_clarification
    -clarification_question

manual子图输出契约：
    -final_answer
    -source_citations
    -should_escalate
    -needs_clarification
    -clarification_question
