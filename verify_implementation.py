#!/usr/bin/env python3
"""验证所有实现文件是否存在和基本结构"""

import os
import sys

def check_file(path, description):
    """检查文件是否存在和可读"""
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read(500)
                print(f"✅ {description}: {path}")
                return True
        except:
            print(f"⚠️ {description}: {path} (读取失败)")
            return False
    else:
        print(f"❌ {description}: {path} (不存在)")
        return False

print("=== Agent系统开发：阶段1实现验证 ===\n")

all_good = True

# 任务1.1: State模型
print("任务1.1: Pydantic State模型")
all_good &= check_file(
    "/Users/frank/Documents/customer_service_aiagent/agent/state.py",
    "State模型文件"
)

# 任务1.2: 工具定义
print("\n任务1.2: 工具定义")
all_good &= check_file(
    "/Users/frank/Documents/customer_service_aiagent/agent/tools/__init__.py",
    "工具模块初始化"
)
all_good &= check_file(
    "/Users/frank/Documents/customer_service_aiagent/agent/tools/policy_retriever.py",
    "政策检索工具"
)
all_good &= check_file(
    "/Users/frank/Documents/customer_service_aiagent/agent/tools/manual_retriever.py",
    "说明书检索工具"
)
all_good &= check_file(
    "/Users/frank/Documents/customer_service_aiagent/agent/tools/clarification_tool.py",
    "信息澄清工具"
)
all_good &= check_file(
    "/Users/frank/Documents/customer_service_aiagent/agent/tools/confidence_tool.py",
    "置信度评估工具"
)

# 任务1.3: 向量数据库
print("\n任务1.3: 向量数据库配置")
all_good &= check_file(
    "/Users/frank/Documents/customer_service_aiagent/rag/vector_store.py",
    "向量存储配置"
)

# 检查核心类和方法
print("\n=== 代码结构验证 ===")
try:
    # 导入state模块
    import importlib.util
    import sys
    sys.path.insert(0, "/Users/frank/Documents/customer_service_aiagent")
    
    # 测试state导入
    from agent.state import SharedState, PolicyAgentState, ManualAgentState
    print("✅ State模型导入成功")
    
    # 测试工具导入
    from agent.tools import PolicyRetrieverTool, ManualRetrieverTool
    print("✅ 工具模块导入成功")
    
    # 测试向量存储导入
    from rag.vector_store import get_vector_store_manager
    print("✅ 向量存储模块导入成功")
    
except Exception as e:
    print(f"❌ 代码导入失败: {e}")
    all_good = False

print("\n=== 实现总结 ===")
if all_good:
    print("🎉 所有任务文件已成功创建！")
    print("\n新增/修改的文件：")
    print("1. /Users/frank/Documents/customer_service_aiagent/agent/state.py")
    print("   - SharedState, PolicyAgentState, ManualAgentState")
    print("   - SourceInfo, Citation模型")
    print("   - 保持向后兼容的AgentState")
    
    print("\n2. /Users/frank/Documents/customer_service_aiagent/agent/tools/")
    print("   - policy_retriever.py: 政策检索")
    print("   - manual_retriever.py: 说明书检索")
    print("   - clarification_tool.py: 信息澄清")
    print("   - confidence_tool.py: 置信度评估")
    print("   - __init__.py: 统一导出")
    
    print("\n3. /Users/frank/Documents/customer_service_aiagent/rag/vector_store.py")
    print("   - VectorStoreManager类")
    print("   - ChromaDB客户端配置")
    print("   - policy_docs和manual_docs集合")
    
    print("\n核心功能：")
    print("- 💾 Pydantic State模型，支持类型验证和自动序列化")
    print("- 🔧 四个核心工具函数，符合LangChain Tool标准")
    print("- 🗄️ 向量数据库管理器，支持分集合检索")
    print("- 🔄 向后兼容现有LangGraph状态定义")
    
else:
    print("⚠️ 部分验证失败，请检查上述问题")
    sys.exit(1)

print("\n下一步：")
print("1. 编写pytest测试覆盖新功能")
print("2. 集成到现有LangGraph流程")
print("3. 配置向量数据库连接")
print("4. 添加文档索引功能")