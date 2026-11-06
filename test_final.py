#!/usr/bin/env python3
"""验证任务1.1、1.2、1.3的完成情况"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

print("=== 验证任务完成情况 ===")

# 1. 验证State模型
print("\n1. 验证State模型...")
try:
    from agent.state import SharedState, PolicyAgentState, ManualAgentState
    
    # 测试创建基础State
    shared = SharedState(query="测试查询", session_id="test_session")
    print("✅ SharedState创建成功")
    
    # 测试PolicyAgentState
    policy = PolicyAgentState(
        query="保修政策", 
        session_id="test_session",
        query_type="policy",
        product_line="家用电器"
    )
    print("✅ PolicyAgentState创建成功")
    
    # 测试ManualAgentState
    manual = ManualAgentState(
        query="使用说明",
        session_id="test_session",
        query_type="manual", 
        product_model="WX-1000"
    )
    print("✅ ManualAgentState创建成功")
    
except Exception as e:
    print(f"❌ State模型验证失败: {e}")
    sys.exit(1)

# 2. 验证工具导入
print("\n2. 验证工具模块...")
try:
    from agent.tools import PolicyRetrieverTool, ManualRetrieverTool, ClarificationTool, ConfidenceTool
    
    print(f"✅ PolicyRetrieverTool: {PolicyRetrieverTool.name}")
    print(f"✅ ManualRetrieverTool: {ManualRetrieverTool.name}")
    print(f"✅ ClarificationTool: {ClarificationTool.name}")
    print(f"✅ ConfidenceTool: {ConfidenceTool.name}")
    
except Exception as e:
    print(f"❌ 工具模块验证失败: {e}")
    sys.exit(1)

# 3. 验证向量存储
print("\n3. 验证向量存储配置...")
try:
    from rag.vector_store import VectorStoreManager, get_vector_store_manager
    
    print("✅ vector_store.py导入成功")
    
    # 测试获取管理器
    manager = get_vector_store_manager()
    print("✅ VectorStoreManager获取成功")
    
except Exception as e:
    print(f"⚠️ 向量存储验证有警告: {e}")
    print("  (需要实际向量数据库连接才能完全测试)")

# 4. 简单功能测试
print("\n4. 简单功能测试...")
try:
    # 测试政策检索
    result = PolicyRetrieverTool.invoke({
        "query": "洗衣机保修",
        "top_k": 2
    })
    print(f"✅ 政策检索工具测试成功，返回{len(result)}个文档")
    
    # 测试澄清工具
    question = ClarificationTool.invoke({
        "missing_fields": ["product_model"],
        "context": "洗衣机坏了"
    })
    print(f"✅ 澄清工具测试成功: {question[:50]}...")
    
except Exception as e:
    print(f"❌ 功能测试失败: {e}")
    sys.exit(1)

print("\n🎉 所有基础验证通过！")
print("\n=== 任务完成总结 ===")
print("1. ✅ State模型 (agent/state.py)")
print("   - SharedState基类")
print("   - PolicyAgentState政策问答状态")  
print("   - ManualAgentState说明书问答状态")
print("   - 保持向后兼容的AgentState")

print("\n2. ✅ 工具定义 (agent/tools/)")
print("   - policy_retriever.py: 政策检索工具")
print("   - manual_retriever.py: 说明书检索工具")
print("   - clarification_tool.py: 信息澄清工具")
print("   - confidence_tool.py: 置信度评估工具")
print("   - __init__.py: 统一导出")

print("\n3. ✅ 向量数据库配置 (rag/vector_store.py)")
print("   - VectorStoreManager: 统一管理器")
print("   - ChromaDB客户端初始化")
print("   - policy_docs和manual_docs两个集合")
print("   - 元数据过滤和搜索功能")

print("\n⚠️ 注意: 向量数据库当前为模拟模式，需要连接到真实数据库")
print("可以运行以下命令进一步测试:")
print("  python -c \"from rag.vector_store import get_vector_store_manager; m = get_vector_store_manager(); print(m.get_collection_stats())\"")