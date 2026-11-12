# 售后退货客服AI Agent开发任务拆解

基于技术方案分析和项目现状，任务拆分为7个阶段：

## 项目现状分析
1. ✅ **已有基础设施**：
   - FastAPI Web服务层
   - LangGraph/LangChain基础框架（已有agent目录结构）
   - 阶段1已完成：`agent/state.py` 的 State 模型、`agent/tools/` 的 4 个工具、`rag/vector_store.py` 的双集合（`policy_docs` / `manual_docs`）+ 同步/真异步检索与写入
   - RAG基础链路：`rag/document_processor.py` 已支持 `.md/.txt/.pdf` 解析、YAML front matter、PDF 页码、500/50 字符切块、`python -m rag.document_processor --reset`
   - 真实数据：`raw_data/policy_docs/售后服务政策.md`、`raw_data/manual_docs/家用冰箱通用说明书_N.30-2-31.pdf`（65 页）
   - 评测集：`rag/eval/golden_set.jsonl`（26 条，19 条可回答、7 条应澄清/拒答）

2. ⚠️ **需要重构/增强**：
   - 当前 agent 框架是基础版，需要扩展为多 Agent 架构
   - 阶段2的知识库链路不完整：`rag/document_parser.py` 是空文件、`rag/chunker.py` 只有未被引用的 `chunk_by_chars`、无 `scripts/build_knowledge_base.py`
   - 真实 PDF 为图文混排，部分页面只解析出残片；政策 md 的 front matter 不是合法 YAML，当前入库会失败
   - 入库未使用稳定 ID，重复执行会产生重复块；缺少构建报告与知识库验收标准
   - 需要在此基础上补齐解析、章节切块、质量检测、幂等构建与召回评测

## 阶段1: 基础架构和配置（预计：2-3天）

### 任务1.1: 创建Pydantic State模型
- **文件**: `/Users/frank/Documents/customer_service_aiagent/agent/state.py`（重构现有文件）
- **任务**：
  - 创建共享State基类：`SharedState`
  - 创建政策问答State：`PolicyAgentState`
  - 创建说明书问答State：`ManualAgentState`
  - 集成到现有`AgentState`或替换

### 任务1.2: 更新工具定义
- **文件**: `/Users/frank/Documents/customer_service_aiagent/agent/tools/`（新目录）
- **任务**：
  - 创建policy_retriever.py：政策检索工具
  - 创建manual_retriever.py：说明书检索工具
  - 创建clarification_tool.py：信息澄清工具
  - 创建confidence_tool.py：置信度评估工具

### 任务1.3: 配置向量数据库
- **文件**: `/Users/frank/Documents/customer_service_aiagent/rag/vector_store.py`
- **任务**：
  - 初始化ChromaDB客户端
  - 创建两个集合：`policy_docs`和`manual_docs`
  - 配置元数据过滤功能

## 阶段2: 知识库构建和数据处理（预计：3-4天）

> **阶段目标**：把 `raw_data/` 下的真实政策/说明书文档，变成“可追溯、可重建、可评测”的向量知识库。
> **阶段边界**：不生成示例数据、不补造业务元数据、无原文时不让 LLM 编内容；解析失败必须显式报错并进入构建报告，不能静默跳过。

### 现状基线（2026-11-11 按真实代码与数据核对）

| 能力 | 当前实现 | 结论 |
| --- | --- | --- |
| 向量库 | `rag/vector_store.py`：Chroma 双集合 `policy_docs` / `manual_docs`、同步+真异步检索/写入、`where` 元数据过滤、距离→相关度归一化 | ✅ 阶段1已完成，阶段2直接复用 |
| 文档入库 | `rag/document_processor.py`：支持 `.md/.txt/.pdf`、YAML front matter、PDF 页码、500/50 字符切块、`python -m rag.document_processor --reset` | ⚠️ 能用，但解析/切块/幂等/质量处理不完整 |
| 政策文档 | `raw_data/policy_docs/售后服务政策.md`（858 字符，5 个标题） | ⚠️ front matter 含 `status: "active"  // ...`，不是合法 YAML，当前解析直接抛 `ValueError`；按 500/50 硬切约 2 块，条款边界丢失 |
| 说明书 | `raw_data/manual_docs/家用冰箱通用说明书_N.30-2-31.pdf`（65 页） | ⚠️ 当前 pypdf 解析出 176 块，有文本的块 100% 带 `page`；但最短块仅 5 字符，共 14 页有效文本 < 30 字符（第 1、8-17、31-33 页），安全注意事项等正文在图片里；`section` 元数据 0 条 |
| 解析器/切块器 | `rag/document_parser.py` 为空文件；`rag/chunker.py` 只有 `chunk_by_chars`，未被任何流程引用 | ❌ 阶段2需落地并被 `document_processor` 复用 |
| 构建脚本 | 无 `scripts/` 目录、无 `build_knowledge_base.py`；`raw_data/manual_docs/` 下两个原型脚本存在硬编码路径、裸 `except`、直接写库 | ❌ 需收敛为正式脚本，原型逻辑迁出 `raw_data` |
| 入库幂等 | `vector_store._add/_aadd` 调用 `add_documents` 时未传稳定 `ids` | ❌ 不加 `--reset` 重复执行会追加重复块，阶段2必须修复 |
| 评测集 | `rag/eval/golden_set.jsonl` 共 26 条（19 条可回答、7 条应澄清/拒答；23 条指向政策文档，3 条要求说明书且均不可回答） | ✅ 可做政策召回冒烟；但 `source_doc=after_sale.md` 与实际文件名不一致，需按 `source_section/retrieval_keywords` 匹配或确认别名 |
| 单测基线 | `python -m pytest tests/unit -q` → **27 passed / 5 failed** | ⚠️ 1 条为政策 front matter YAML 失败（任务2.1修复）；4 条为 `tests/unit/test_settings.py` 期望 `LLM_MODEL` 有默认值，但 `settings.py` 当前声明为必填（阶段2前置修复，不属知识库逻辑） |

### 任务2.1: 落地文档解析器（预计：1天）

- **文件**：
  - `rag/document_parser.py`（新增，替换空壳）
  - `rag/document_processor.py`（改造为“发现文件 + 编排入库”，解析逻辑下沉）
- **数据模型**（`dataclass(frozen=True)`，不直接依赖 LangChain）：
  - `ParsedBlock`: `text`、`page: int | None`、`section: str | None`、`block_type: Literal["text", "table", "image_caption"]`、`metadata: dict[str, str | int | float | bool]`
  - `PageQuality`: `page`、`text_length`、`source: Literal["native", "ocr", "image_caption"]`、`ocr_confidence: float | None`、`issues: tuple[str, ...]`
  - `ParsedDocument`: `source_path`、`file_name`、`source_type: Literal["policy", "manual"]`、`blocks`、`page_qualities`、`doc_metadata`
- **解析策略**：
  - 统一入口：`parse_document(path, source_type)`；分别实现 `parse_policy_document()` / `parse_manual_document()`
  - 政策：`.md/.txt/.pdf`；Markdown 按 `#` 标题产出 `section` 路径（如 `服务政策/如何退、换货？/退换货规定`）；front matter 只映射真实字段
  - 说明书：`.pdf` 优先按页解析（保留 `page`、表格、图片块），简单文本 PDF 可回退 pypdf；图片页在 `--caption-images` 开启时产出 `image_caption` 块（见任务2.3）
  - front matter 容错：只剥离“前面是空白、不在引号内且不属于 `://`”的行尾 `//` 注释并 `logger.warning`（源文件是真实数据，不在 `raw_data` 里改数据）；`https://` 等 URL 值不受影响，YAML 结构本身损坏仍抛 `ValueError`
  - 映射修复：现有映射把 `doc_type` 映射成 `policy_type`，会覆盖真实的 `policy_type: refund`；改为 `doc_type -> doc_type`；同时补上遗漏的 `status`、`expiration_date`。当前政策文件只有 `doc_id/doc_type/policy_type/effective_date/expiration_date/status`，`region/product_line/source_url` 不存在，对应过滤命中为空属预期，需数据方补齐后再启用。`effective_date` 保留 ISO 字符串用于展示，另从真实值派生 `effective_date_num`（YYYYMMDD 整数），因为 Chroma 1.x 的 `$gte/$lte` 只接受数值
  - 元数据必填：所有块都写入 `document_id`（front matter `doc_id` 或文件 stem）、`source_type`、`file_name`；`None`/空串不写入，不补 `region/product_line/effective_date/product_model/issue_type` 等业务默认值
  - 元数据白名单：仅允许 Chroma 支持的标量类型（str/int/float/bool）；出现 list/dict 直接报错，不静默转换（`None`/空串按上一条跳过）
  - 格式范围：本阶段只做 `.md/.txt/.pdf`；Word 解析需要新增 `python-docx` 依赖且当前无真实样本，暂不实现
- **验收**：
  - 原 CLI 保持可用，现有 `tests/unit/test_document_processor.py` 全绿
  - 政策 md 能解析出 5 个章节；说明书 65 页都进入解析/质量记录，有文本或 caption 的块 100% 带 `page`，图片页不产生“安全注意事项 - 6 -”式垃圾块
  - 空文件、加密 PDF、未知后缀、损坏 front matter 都有明确异常与文件路径

### 任务2.2: 章节感知 Chunker（预计：1天）

- **文件**：`rag/chunker.py`
- **保留兼容**：`chunk_by_chars` 原样保留（已有测试/旧调用不受影响），但新链路统一走章节感知接口
- **新增接口**：
  - `Chunk` 数据模型：`content`、`section`、`page`、`block_type`、`chunk_index`、`char_count`
  - `chunk_by_sections(blocks, max_chars=500, overlap=50, min_chars=120) -> list[Chunk]`
- **切块规则**：
  1. 按 `(section, page)` 分组，标题跟随正文，不把标题单独成块
  2. 不跨页合并（保证 PDF 页码引用准确），不跨 `section` 合并
  3. 表格与安全警告块整块保留（Markdown 表格行或含“警告/注意/切勿/必须/禁止”的块）；超长时按行切并重复表头，不按句子切
  4. 超长 `section` 先按中文句子边界 `。！？；` 切分；单句仍超长才硬切
  5. 过短 `section`（< `min_chars`）与同页、同 `section` 的相邻块合并；合并后超长则回退拆分
  6. 稳定 `chunk_id`：`{document_id}::{page or 0}::{chunk_index}`；`document_id` 优先取 front matter `doc_id`，缺失时用文件名 stem（不用绝对路径，保证跨机器可重建）
  7. `chunk_index` 在过滤/合并后的最终输出序列上按文档全局递增（不按 section 重置），避免政策文档 `page=0` 时不同 section 产生相同 ID
  8. `overlap` 只用于同页同章节的正文块；表格/安全警告块不重叠，避免重复表头或安全提示
- **参数决策**：默认保持 500 字符 / 50 overlap（与 `docs/solutions/rag-pipeline/rag_keypoint.md` 的 512 tokens / 50 tokens 结论对齐），CLI 暴露参数；阶段2验收时用 golden set 对比 500/768 的 Recall@4，结论回写 `rag_keypoint.md`
- **验收**：
  - 所有文本块 `<= max_chars`（单行或单个不可拆分表格行本身超长时除外，但必须在构建报告中标注），不出现无意义的 < 10 字符块（短标题/短 caption 与正文合并，合并后仍无正文则丢弃）
  - 说明书有文本/图片 caption 的块 100% 带 `page`；当前政策 md 100% 块带 `section`（政策 PDF 若无标题结构，`section` 允许为空但必须保留 `page` 并在报告中记录）
  - 同一输入重复执行，`chunk_id` 与块内容完全一致

### 任务2.3: 文档质量检测与图文混排处理（预计：0.5-1天）

- **文件**：
  - `rag/document_quality.py`（新增，迁移 `raw_data/manual_docs/pdf_quanlity_check.py` 的判定规则）
  - `rag/document_parser.py`（调用质量判定，产出 page 级质量信息）
- **质量规则**（基于真实页面文本与 OCR 置信度，不猜测内容）：
  - `blank`：去空白后有效字符 < 5
  - `page_num_only`：去掉页码/“第 N 页”/“N / M”后剩余有效字符 < 10（覆盖“标题 + 页码”残片）
  - `garbled`：`\ufffd`/`(cid:N)` 占比 > 10%，或异常字符占比 > 30%（异常字符统计排除中英文标点及 `…`/`·`/`—` 等排版符号，避免目录页被误判）
  - `too_short`：有效字符 < 30
  - `low_ocr_confidence` / `suspect_ocr`：OCR 置信度 < 0.50 / < 0.60
  - `image_only`：无有效文本但页面存在图片
- **默认策略（不启用 OCR）**：保留原生文本；判定 `blank` / `page_num_only` / `garbled` 的页面，以及 `too_short` / `image_only` 且没有表格/图片 caption 的页面，不进入索引，但必须在质量报告中记录并计数，不能静默丢弃
- **已知缺口**：默认不开启 OCR 时，说明书第 8-17 页的安全正文、第 31-33 页的零部件/清洁/停用正文不入库；阶段2只记录并暴露该缺口，阶段4说明书问答前由数据方决定是否开启 OCR/caption，不能用 LLM 补写
- **`--ocr-fallback`（显式开启）**：200dpi 渲染 + PaddleOCR（依赖已在 `pyproject.toml`），记录 `ocr_confidence`；`< 0.60` 的页面标记 `needs_review`，默认不入库；绝不写空串或猜测内容
- **`--caption-images`（显式开启，可选）**：按页提取图片，按 MD5 复用 `reports/knowledge_base/caption_cache.json`；缓存缺失才调用视觉模型，调用失败显式报错（不写空 caption）；caption 作为 `image_caption` 块保留 `page` / `image_path`。当前 `settings` 没有视觉模型字段，阶段2默认关闭；启用前需确认 `LLM_MODEL` 支持图像或由数据方提供 `VISION_MODEL`，否则直接报错，不静默跳过
- **质量报告**：`reports/knowledge_base/quality_report.json`（运行时生成，不提交版本库），字段 `page/source/text_length/ocr_confidence/issues`
- **验收**：
  - 关闭 OCR 时：65 页 PDF 的质量报告页数 = 65，第 1 页标注 `too_short`，第 8-17、31-33 页标注 `page_num_only`（第 8-17 页 pypdf 仅剩“安全注意事项/页码”或纯页码残片）
  - 开启 OCR 时：第 8-17 页恢复正文并记录 `ocr_confidence`，第 1、31-33 页按实际识别结果标注，不强行判为合格
  - 关闭 OCR / caption 时不产生任何模型调用；开启后失败会中断并打印具体页码与原因

### 任务2.4: 知识库构建脚本与幂等入库（预计：1天）

- **文件**：
  - `scripts/__init__.py`
  - `scripts/build_knowledge_base.py`（新增）
  - `rag/vector_store.py`（小改：写入接口支持传入稳定 `ids`，优先走 Chroma `upsert`）
  - `.gitignore`（忽略运行时生成的 `reports/` 和遗留 `caption_cache.json`）
- **CLI（argparse，与现有 `document_processor` 风格一致）**：
  ```bash
  python -m scripts.build_knowledge_base \
    --source all --reset \
    --report-dir reports/knowledge_base
  ```
  参数：`--source {all,policy,manual}`、`--policy-dir`、`--manual-dir`、`--reset`、`--dry-run`、`--max-chars`、`--overlap`、`--ocr-fallback`、`--caption-images`
- **流程**：`discover → parse → quality filter → chunk →（dry-run 仅统计）/ 计算 embedding + upsert → 生成报告`
- **幂等**：解析阶段产出稳定 `chunk_id`；每个文件先完成 embedding 并 upsert（同 id 覆盖而不是追加），成功后再查询该 `document_id` 的旧 ID，删除不在新 ID 集合中的残留块；embedding/upsert 失败时不删除旧数据。`--reset` 只重置本次 `--source` 选中的集合，用于全量重建
- **写入实现**：使用 `chromadb` 原生 `client.get_or_create_collection(...).upsert(...)`；检索继续复用现有 LangChain `Chroma` 包装，两者指向同一持久化集合，避免依赖 LangChain 未暴露的 upsert 私有接口
- **异步约束**：不得在事件循环里直接调用同步写库/重置/计数；异步路径用 `await embeddings.aembed_documents()` 计算向量，再把 Chroma 同步 `upsert`、`get`、`delete`、`reset_*`、`get_collection_count` 通过 `anyio.to_thread.run_sync` 卸载到线程池（与现有 `document_processor` 的文件读取方式一致）
- **构建报告**：`reports/knowledge_base/build_report.json`（无论成功/失败都生成）
  - 每文件：状态、页数、入库页数、跳过页数及原因、块数、失败原因
  - 汇总：切块 `min/avg/max`、重复块数、入库前后集合数量、耗时
- **失败策略**：任一文件解析失败 → 不进入写入阶段，汇总失败清单并返回退出码 1（避免半成品知识库）；目录为空 → `FileNotFoundError`；不得回退示例数据
- **Embedding**：复用 `rag.vector_store.SiliconFlowEmbeddings`，模型/地址/密钥全部来自 `settings` 的 `EMBEDDING_*`，禁止硬编码或换用其他模型
- **`--dry-run` 约束**：不初始化 ChromaDB / embedding、不实例化 `VectorStoreManager`（其构造函数会初始化 embedding），不产生 API 费用，只做解析与切块校验
- **验收**：
  - `--dry-run` 在无向量库、无网络时可跑通并输出统计
  - `--source all --reset` 后 `policy_docs` / `manual_docs` 均 > 0
  - 不加 `--reset` 连续执行两次，集合数量不变且无重复内容

### 任务2.5: 阶段2验收与回归（预计：0.5天）

- **单元测试**（`tests/unit/`）
  - `test_document_parser.py`：front matter `//` 容错、章节路径、页码、加密/空 PDF、未知后缀
  - `test_chunker.py`：章节边界、不跨页、表格/警告不拆、短块合并、`chunk_id` 稳定；使用 `raw_data` 真实文档，缺失自动 skip
  - `test_document_quality.py`：乱码/仅页码/空白判定（使用真实页面样本）
  - 异常输入只构造空文件/损坏字节并断言具体异常，不伪造业务文档内容；真实文档缺失时自动 skip
- **召回冒烟**（`tests/integration/test_knowledge_base.py`）
  - 用 `golden_set.jsonl` 中 19 条 `answerable=true` 的题目计算 Recall@4；政策题基线目标 `>= 80%`
  - 说明书类 3 条题目均为 `corpus_gap`（不可回答），不计算召回，只记录 top-4 结果与相关度分布，供阶段4决定澄清/拒答策略
  - 另有 4 条政策不可回答样本（三包有效期/折旧费/压缩机保修/信息不足需澄清）同样是 `corpus_gap`，不计算召回，记录 top-4 供阶段4拒答与澄清策略使用
  - 命中判定：top-4 中存在 `section` 末级等于 `source_section`（或路径包含该标题）、且正文至少命中 `min(2, len(retrieval_keywords))` 个关键词的块；含数字/型号的关键词（如 `7日`、`400-8866-191`）必须命中，`source_doc` 只做展示不参与判定
- **回归与报告**：
  - `python -m pytest tests/unit -q` 全绿：当前基线 27 passed / 5 failed，其中政策 YAML 1 条由任务2.1修复；`settings` 4 条在阶段2开始前确认修复（二选一：给 `LLM_MODEL` 补默认值，或修正测试断言，以配置契约为准）
  - 构建报告无失败文件；真实存在的元数据过滤（`policy_type=refund` 或 `effective_date` 区间）命中非空
- **不做**：LLM 答案准确率评估（阶段6）、全量 OCR（按需开启）

### 阶段2执行顺序与依赖

1. **2.1 解析器 + front matter 容错**：先解决政策文档无法入库的阻塞
2. **2.2 章节感知 Chunker**：接入 `document_processor`
3. **2.3 质量检测/图片处理**：可与 2.2 并行
4. **2.4 构建脚本 + 幂等入库**：依赖 2.1-2.3
5. **2.5 验收与报告**：依赖 2.1-2.4

> 人力估算：任务合计 4-4.5 人天；单人串行时按 4-4.5 天排，2.3 与 2.2 并行时关键路径约 3.5 天（2.1 → 2.2 → 2.4 → 2.5）。

### 阶段2完成定义（DoD）

- [ ] 政策/说明书都能通过一条命令重建，构建报告可追溯
- [ ] 每个 chunk 都带文件来源；PDF 块带 `page`，Markdown 块带 `section`；无法定位的块不入库
- [ ] 重复执行不产生重复数据；失败文件不会被静默跳过
- [ ] 政策 golden set Recall@4 `>= 80%`，说明书知识缺口样本已记录（不计算召回）
- [ ] 现有 27 条通过用例不回归，5 条失败用例修复后 `tests/unit` 全绿

### 阶段2边界（避免与后续阶段混淆）

- `raw_data/` 下的真实文档只读：不修改/新增/删除；需要补充文档时创建空目录，由你放入；原型脚本只复用逻辑，不修改/删除原文件
- 不新增依赖：`pypdf`、`PyMuPDF`、`PaddleOCR`、`Chroma`、`LangChain` 已在 `pyproject.toml`
- 不实现检索工具（阶段4）/ Agent 子图（阶段3）：阶段2只保证知识库质量与可评测

## 阶段3: Agent子图实现（预计：4-5天）

### 任务3.1: 重构主图Orchestrator
- **文件**: `/Users/frank/Documents/customer_service_aiagent/agent/main_graph.py`（新文件）
- **任务**：
  - 基于现有的graph.py重构
  - 实现路由节点改进
  - 集成会话管理

### 任务3.2: 政策问答子图实现
- **文件**: `/Users/frank/Documents/customer_service_aiagent/agent/subgraphs/policy_agent.py`
- **任务**：
  - 实现政策检索节点
  - 实现政策解析节点
  - 实现来源引用生成

### 任务3.3: 说明书问答子图实现
- **文件**: `/Users/frank/Documents/customer_service_aiagent/agent/subgraphs/manual_agent.py`
- **任务**：
  - 实现说明书检索节点
  - 实现故障排查步骤生成
  - 实现安全警告生成

## 阶段4: 工具实现（预计：3-4天）

### 任务4.1: 检索工具实现
- **文件**：
  - `/Users/frank/Documents/customer_service_aiagent/rag/retriever.py`（重构）
  - `/Users/frank/Documents/customer_service_aiagent/rag/policy_retriever.py`
  - `/Users/frank/Documents/customer_service_aiagent/rag/manual_retriever.py`
- **任务**：
  - 向量相似度检索
  - 元数据过滤（产品型号、地区等）
  - 多阶段检索策略

### 任务4.2: 澄清和验证工具
- **文件**: `/Users/frank/Documents/customer_service_aiagent/agent/tools/clarification_tool.py`
- **任务**：
  - 缺失信息检测
  - 生成澄清问题
  - 处理用户澄清回复

### 任务4.3: 置信度评估工具
- **文件**: `/Users/frank/Documents/customer_service_aiagent/agent/tools/confidence_tool.py`
- **任务**：
  - 检索相关性评分
  - LLM生成置信度评估
  - 兜底回复决策

## 阶段5: API和会话管理（预计：2-3天）

### 任务5.1: API接口扩展
- **文件**: `/Users/frank/Documents/customer_service_aiagent/api/`（新增）
  - `customer_service_router.py`
  - `session_manager.py`
  - `webhook_manager.py`
- **任务**：
  - 创建RESTful API接口
  - 支持多轮对话
  - 支持澄清流程

### 任务5.2: 会话存储
- **文件**: `/Users/frank/Documents/customer_service_aiagent/memory/session_store.py`
- **任务**：
  - Redis会话存储实现
  - 会话状态持久化
  - 历史记录管理

## 阶段6: 测试和监控（预计：3-4天）

### 任务6.1: 单元测试
- **目录**: `/Users/frank/Documents/customer_service_aiagent/tests/`
- **任务**：
  - 工具类测试
  - 节点逻辑测试
  - State模型测试

### 任务6.2: 集成测试
- **文件**: 
  - `/Users/frank/Documents/customer_service_aiagent/tests/integration/`
  - `/Users/frank/Documents/customer_service_aiagent/tests/e2e/`
- **任务**：
  - 端到端流程测试
  - RAG准确性测试
  - 多轮对话测试

### 任务6.3: 监控和日志
- **文件**: `/Users/frank/Documents/customer_service_aiagent/core/monitoring.py`
- **任务**：
  - 结构化日志配置
  - Prometheus指标定义
  - 性能监控工具

## 阶段7: 部署和文档（预计：2-3天）

### 任务7.1: 配置管理
- **文件**：
  - `/Users/frank/Documents/customer_service_aiagent/config`目录
  - Dockerfile和docker-compose.yml
- **任务**：
  - 环境变量配置
  - Docker容器化
  - 部署脚本

### 任务7.2: 文档编写
- **文件**：
  - `/Users/frank/Documents/customer_service_aiagent/docs/`目录
  - API文档
  - 开发指南
- **任务**：
  - Swagger/OpenAPI文档
  - 架构说明文档
  - 使用指南

---

## 任务优先级和依赖关系

### P0优先级（核心功能）：
> 状态：✅ 已完成 / ⏳ 待开始
1. ✅ **P0**: State模型定义（基础）
2. ✅ **P0**: 工具定义和接口
3. ✅ **P0**: 向量数据库配置
4. ⏳ **P0**: 文档解析和知识库构建（阶段2：解析器、章节切块、质量检测、幂等构建脚本、召回验收）
5. ⏳ **P0**: 主图Orchestrator重构

### P1优先级（业务功能）：
6. ⏳ **P1**: 政策问答子图实现
7. ⏳ **P1**: 说明书问答子图实现
8. ⏳ **P1**: 检索工具实现
9. ⏳ **P1**: API接口扩展

### P2优先级（增强功能）：
10. ⏳ **P2**: 澄清工具实现
11. ⏳ **P2**: 置信度评估工具
12. ⏳ **P2**: 会话管理
13. ⏳ **P2**: 监控和日志

---

## 开发顺序建议

### 第1周（快速原型）：
1. **Day 1-2**：完成阶段1（State模型、工具定义、向量DB配置）
2. **Day 3-5**：完成阶段2（解析器、章节感知切块、质量检测、幂等构建脚本、Recall@4 验收）

### 第2周（核心实现）：
3. **Day 6-7**：完成政策问答子图（阶段3.1-3.2）
4. **Day 8-9**：完成说明书问答子图（阶段3.3）
5. **Day 10**：完成检索工具（阶段4.1）

### 第3周（功能完善）：
6. **Day 11-12**：完成澄清和置信度工具（阶段4.2-4.3）
7. **Day 13-14**：完成API和会话管理（阶段5）

### 第4周（测试优化）：
8. **Day 15-17**：完成测试（阶段6）
9. **Day 18-19**：完成部署和文档（阶段7）
10. **Day 20**：最终测试和优化

---

## 具体任务执行建议

### 立即开始的任务（并行进行）：

#### 1. **数据准备团队**：
- 收集和整理售后政策文档
- 收集和整理产品说明书
- 准备测试查询和预期答案

#### 2. **后端架构团队**：
- 设计State模型和工具接口
- 配置向量数据库
- 搭建基础LangGraph框架

#### 3. **前端API团队**：
- 设计API接口规范
- 准备会话管理框架
- 设计错误处理和兜底策略

---

## 风险和缓解措施

### 技术风险：
1. **向量检索质量不佳**
   - 缓解：多种分块策略测试，混合检索（关键词+向量）

2. **LLM生成答案不准确**
   - 缓解：严格的来源引用，置信度评估，人工标注优化

3. **多轮对话状态管理复杂**
   - 缓解：清晰的State设计，会话边界标记，状态恢复机制

4. **PDF 图文混排导致召回缺失**
   - 缓解：页面级质量检测，坏页不入库并进入报告；按需开启 OCR / 图片 caption，不写猜测内容

5. **front matter / 文档格式不规范**
   - 缓解：解析器只做可解释的最小容错（如剥离 `//` 行尾注释）并告警；结构损坏直接报错，由数据方修正源文件

6. **重复构建产生重复向量**
   - 缓解：稳定 `chunk_id` + 写入接口传 `ids`，构建后校验集合数量与重复块数

### 业务风险：
1. **政策文档格式多样**
   - 缓解：灵活的文档解析器，人工预处理关键文档

2. **产品型号复杂**
   - 缓解：产品型号标准化映射，模糊匹配逻辑

### 时间风险：
1. **集成测试时间不足**
   - 缓解：开发早期开始测试，自动化测试优先

---

## 成功标准

### 技术标准：
- ✅ 支持RAG的问答准确性 > 85%
- ✅ 知识库：政策 golden set Recall@4 >= 80%；chunk 页码/章节可追溯率 100%；重复构建重复块数 = 0
- ✅ 多轮对话成功率 > 90%
- ✅ API响应时间 < 3秒
- ✅ 支持100+并发查询

### 业务标准：
- ✅ 政策查询准确回答率 > 80%
- ✅ 使用说明查询准确回答率 > 85%
- ✅ 澄清问题的用户完成率 > 60%
- ✅ 用户满意度评分 > 4.0/5.0

---

这个任务拆解基于您的现有项目结构，保持了一致的技术栈，并按照优先级和依赖关系进行了合理规划。建议按照推荐的开发顺序逐步实施，每周进行进度评估和调整。