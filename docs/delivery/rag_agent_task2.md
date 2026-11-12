## 阶段2已完成

已按 `docs/design/development_plan.md` 中阶段2的 2.1–2.5 完成实现，并使用 `raw_data/` 下的真实政策/说明书文档完成构建与召回验证。

### 核心实现

- **文档解析器 `rag/document_parser.py`**
  - 新增 `ParsedBlock`、`PageQuality`、`ParsedDocument` 数据模型。
  - 支持 `.md/.txt/.pdf`。
  - Markdown 按标题生成 `section` 路径。
  - PDF 按页解析，保留 `page`、表格块。
  - 修复 front matter 行尾 `//` 注释容错，`https://` 不受影响。
  - 修复 `doc_type -> doc_type`，补充 `status`、`expiration_date`。
  - 增加 `effective_date_num` 派生字段，用于 Chroma 数值区间过滤。
  - 支持可选 `--ocr-fallback`（PaddleOCR）和 `--caption-images`（视觉模型 caption，默认关闭）。
  - 图片 caption 使用 MD5 缓存，失败显式报错。

- **质量检测 `rag/document_quality.py`**
  - 实现 `blank`、`page_num_only`、`garbled`、`too_short`、`image_only`、低 OCR 置信度等规则。
  - 修复目录页 `……`、`·`、`—` 被误判乱码的问题。
  - 页面质量、跳过原因写入质量报告。

- **章节感知 Chunker `rag/chunker.py`**
  - 保留原 `chunk_by_chars`。
  - 新增 `Chunk` 和 `chunk_by_sections()`。
  - 按 `(section, page)` 分组，不跨页、不跨章节。
  - 表格、安全提示块整块保留；超长表格按行拆分并重复表头。
  - 正文按 `。！？；` 切分，保留 overlap。
  - 短块同页同章节合并。
  - `chunk_index` 在最终输出序列上全局递增。

- **文档处理器 `rag/document_processor.py`**
  - 解析逻辑下沉到 `DocumentParser`，处理流程统一为：
    `发现文件 → 解析 → 质量过滤 → 切块 → 生成稳定 ID → 入库`
  - 稳定 ID 格式：
    `{document_id}::{page or 0}::{chunk_index}`
  - 自动去重文档内完全相同的块。
  - 异步路径使用原生 `aembed_documents()`，解析/Chroma 阻塞操作通过 `anyio.to_thread.run_sync` 卸载。

- **向量库 `rag/vector_store.py`**
  - 新增 Chroma 原生 `upsert`：先 embedding + upsert，再删除该 `document_id` 下旧 ID。
  - 新增 `get_ids_by_document_id`、`delete_ids`、`delete_stale_ids`。
  - 新增同步/异步 `upsert_policy_documents`、`upsert_manual_documents`。
  - 旧 `add_*` 接口在有稳定 `chunk_id` 时自动走 upsert。
  - 增加 `effective_date` 字符串区间到 Chroma 数值区间的转换。

- **构建脚本 `scripts/build_knowledge_base.py`**
  - 新增 `scripts/__init__.py`。
  - 支持：
    - `--source {all,policy,manual}`
    - `--policy-dir` / `--manual-dir`
    - `--reset`
    - `--dry-run`
    - `--max-chars` / `--overlap`
    - `--ocr-fallback`
    - `--caption-images`
    - `--vision-model`
    - `--report-dir`
  - `--dry-run` 不初始化 `VectorStoreManager`、不调用 embedding。
  - 任一文件解析失败时只生成报告，不写入向量库，退出码 1。
  - 生成：
    - `reports/knowledge_base/build_report.json`
    - `reports/knowledge_base/quality_report.json`

- **配置与忽略项**
  - `settings.py`：恢复 `LLM_MODEL` 默认值，满足现有测试契约；新增可选 `VISION_MODEL`。
  - `.gitignore`：忽略 `reports/` 和 `caption_cache.json`。

### 验收结果

- 单元测试：
  - `python -m pytest tests/unit -q` → **57 passed**
- 全量默认测试：
  - `python -m pytest -q` → **57 passed / 4 skipped**
  - 跳过的是需要已构建真实向量库的集成召回测试。
- 真实知识库构建：
  - 政策文档：**3 块**
  - 说明书：**614 块**
  - 总块数：**617**
  - 构建失败文件：**0**
  - 文档内重复内容块：**0**
  - 说明书 65 页全部进入质量记录，**51 页入库，14 页按规则跳过**
  - 跳过页：第 1 页 `too_short`，第 8–17、31–33 页 `page_num_only`
- 幂等性：
  - 连续执行两次，`policy_docs` 保持 **3**、`manual_docs` 保持 **614**，数量不变。
- 召回冒烟：
  - `RUN_KNOWLEDGE_BASE_EVAL=1 python -m pytest tests/integration/test_knowledge_base.py -q`
  - **4 passed**
  - 政策 golden set Recall@4：**19/19 = 100%**
  - `policy_type=refund` 和 `effective_date` 区间过滤均命中非空。
  - 说明书 3 条 `corpus_gap` 题目只记录 top-4，不计算召回。
- 500/768 对比：
  - 两者政策 Recall@4 均为 100%，但政策集只有 3 个内容块，指标已饱和。
  - 阶段2继续采用 **500 字符 / 50 overlap**，结论已回写 `docs/solutions/rag-pipeline/rag_keypoint.md`。

### 新增/修改文件

**新增**
- `rag/document_parser.py`
- `rag/document_quality.py`
- `scripts/__init__.py`
- `scripts/build_knowledge_base.py`
- `tests/unit/test_document_parser.py`
- `tests/unit/test_document_quality.py`
- `tests/unit/test_chunker.py`
- `tests/unit/test_build_knowledge_base.py`
- `tests/integration/test_knowledge_base.py`

**修改**
- `rag/chunker.py`
- `rag/document_processor.py`
- `rag/vector_store.py`
- `settings.py`
- `.gitignore`
- `tests/unit/test_document_processor.py`
- `tests/unit/test_settings.py`
- `tests/unit/test_tools_logic.py`
- `docs/design/development_plan.md`
- `docs/solutions/rag-pipeline/rag_keypoint.md`

### 使用命令

```bash
# 只解析并生成报告，不初始化向量库
python -m scripts.build_knowledge_base --source all --dry-run

# 全量重建
python -m scripts.build_knowledge_base --source all --reset

# 幂等增量更新
python -m scripts.build_knowledge_base --source all

# 执行召回验收
RUN_KNOWLEDGE_BASE_EVAL=1 python -m pytest tests/integration/test_knowledge_base.py -q
```

已知缺口保持与计划一致：默认未启用 OCR 时，说明书第 8–17 页安全正文和第 31–33 页零部件/清洁/停用正文不会入库，但已完整记录在质量报告中；未用 LLM 补写任何缺失内容。