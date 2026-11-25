# Agent 代码简洁性指南

## 🚫 避免这些过度封装模式

### 1. 简单的属性访问包装器
```python
# ❌ 不要这样写
def get_user_query(state):
    return state.user_query

def _subgraph_query(state):
    return state.context_query.strip() or state.user_query.strip()

# ✅ 应该直接使用
answer = state.user_query
query = state.context_query.strip() or state.user_query.strip()
```

### 2. 单行工具函数
```python
# ❌ 避免
def truncate_text(text, length=100):
    return text[:length] + "..." if len(text) > length else text

def safe_get(dict_obj, key, default=None):
    return dict_obj.get(key, default)

# ✅ 除非在代码中多次调用（≥3次），否则内联
preview = text[:100] + "..." if len(text) > 100 else text
value = data.get('key', 'default')
```

### 3. 过度抽象的参数处理
```python
# ❌ 过于抽象
def prepare_input_data(state, config):
    return {
        'query': state.user_query,
        'context': state.context,
        **config
    }

# ✅ 明确表达
query_data = {
    'query': state.user_query,
    'context': state.context,
    'temperature': config.get('temp', 0.7)
}
```

## ✅ 应该封装的情况

### 1. 复杂业务逻辑（≥5行）
```python
# ✅ 好的封装
def build_citations(source_ids, documents, doc_type, fallback_count=3):
    """根据来源ID构建引用列表，提供兜底逻辑"""
    citations = []
    # 复杂的映射和验证逻辑
    for source_id in source_ids:
        # ... 多个步骤的逻辑
        pass
    return citations
```

### 2. 异常处理包装
```python
# ✅ 合理的包装
async def safely_retrieve_documents(query, retriever, fallback=None):
    """安全检索文档，处理常见异常"""
    try:
        return await retriever.ainvoke({"query": query})
    except (APIError, TimeoutError) as e:
        logger.warning(f"检索失败: {e}")
        return fallback or []
```

### 3. 数据转换/格式化
```python
# ✅ 有意义的转换函数
def documents_to_prompt_context(documents):
    """将文档列表转换为prompt格式"""
    if not documents:
        return "无相关文档"
    
    sections = []
    for i, doc in enumerate(documents, 1):
        meta = doc.metadata
        sections.append(f"[{meta.get('source', f'文档{i}')}]\n{doc.page_content}")
    
    return "\n\n".join(sections)
```

## 📋 简单决策树

```
是否要为一个逻辑创建函数？
    ↓
逻辑是否简单（≤3行，无分支）？
    ├─ 是 → 被调用次数 ≥ 3？
    │   ├─ 是 → 可以考虑封装（但仍需判断语义价值）
    │   └─ 否 → ❌ 不要封装，直接内联
    │
    └─ 否 → 有明确的业务含义？
        ├─ 是 → ✅ 封装，取个好名字
        └─ 否 → 逻辑是否独立可测试？
            ├─ 是 → ✅ 封装
            └─ 否 → 考虑是否真的需要函数
```

## 💡 实际应用示例

### policy_agent.py 中的改进
```python
# ❌ 原代码（过度封装）
def _subgraph_query(state: PolicyAgentState) -> str:
    return state.context_query.strip() or state.user_query.strip()

# ✅ 改进后（直接内联）
# policy_retrieve_node 中：
query = state.context_query.strip() or state.user_query.strip()

# policy_analyze_node 中：
question = state.context_query.strip() or state.user_query.strip()
```

## 🎯 核心原则

1. **DRY vs KISS 平衡**：不要为了"不要重复自己"而牺牲"保持简单"
2. **语义优先**：函数应该表达清晰的业务意图，不只是避免重复
3. **三处准则**：逻辑被调用3次以上才考虑封装
4. **可读性至上**：内联简单逻辑通常比跳转到另一个函数更易读
5. **注释替代**：对于简单但需要说明的逻辑，优先使用注释而不是函数

## 🔍 代码审查提示

发现过度封装时，可以使用以下标签：
+ `over-engineering` - 过度工程化
+ `inline-candidate` - 建议内联的简单函数
+ `semantic-wrapping` - 缺乏语义价值的包装函数

---

*保持代码简洁，让复杂的事情简单，简单的事情直接。*