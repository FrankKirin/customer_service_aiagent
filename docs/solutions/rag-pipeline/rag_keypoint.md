---
title: 本项目 chunk 策略选择 512 tokens / 50 tokens overlap 的理由与验证
date: 2026-10-29
category: rag-pipeline
module: chunking
problem_type: design_pattern
applies_when:
  - 处理长度中等、结构较规整的内部文档
  - 需要在检索精度与上下文完整性之间取平衡
  - 使用中文 Embedding 模型
tags: [chunking, chunk-size, overlap, rag-pipeline]
---

## 决策

chunk_size = 512 tokens, chunk_overlap = 50 tokens（约 10%）。

## 理由

业界默认策略中，512 tokens 被认为是“上下文与精度之间的甜点区”：太小（128–256）会把完整概念切碎，太大（1024–2048）会稀释相关性、浪费 LLM token。50 token 的重叠是为了防止关键句子恰好落在边界上被切断。

本次语料是产品 FAQ 和内部流程文档，平均段落长度在 300–500 token 之间，512 基本能覆盖一个完整的问答对。

## 验证方式

取 20 条已知“问题 → 正确 chunk”的测试样本，用当前配置做检索，记录 top-4 命中率。如果命中率低于预期，下一步会尝试 384 或 768 做对比。

## 关联

- 如果后续切换到代码文件，应改用 AST-based 分块（按函数边界切，100–200 行），而不是固定 token 窗口。