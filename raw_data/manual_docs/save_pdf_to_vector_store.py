import os, json, hashlib, logging
logger = logging.getLogger(__name__)
os.environ['PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK'] = 'True'
from pathlib import Path
from typing import cast

from chromadb import Collection, Documents, EmbeddingFunction, Embeddings
from settings import Settings, get_settings
from rag.vector_store import VectorStoreManager, get_vector_store_manager

import pymupdf, base64, re
from openai import OpenAI   # 复用现有依赖，硅基流动/DeepSeek 均为 OpenAI 兼容协议

settings: Settings = get_settings()
vector_manager: VectorStoreManager = get_vector_store_manager()

CURRENT_DIR: Path = Path(__file__).resolve().parent
PDF_FILE: str = "家用冰箱通用说明书_N.30-2-31.pdf"
PDF_FILE_PATH: Path = CURRENT_DIR / PDF_FILE

MAX_CHUNK_CHARS: int = 1000

IMAGE_DIR: str = "assets/images"
MD_PATH: str = "manual.md"
CAPTION_CACHE: str = "caption_cache.json"
COLLECTION_NAME: str = "manual_docs"

chat_client: OpenAI = OpenAI(
    api_key = settings.LLM_API_KEY,
    base_url = settings.LLM_BASE_URL,
)
embed_client: OpenAI = OpenAI(
    api_key = settings.EMBEDDING_API_KEY,
    base_url = settings.EMBEDDING_BASE_URL,
)

VISION_MODEL: str = settings.LLM_MODEL
EMBED_MODEL: str = settings.EMBEDDING_MODEL

class SiliconFlowEmbedding(EmbeddingFunction[Documents]):
    """硅基流动 embedding 适配器

    建库和查询必须使用同一个类、同一个模型，否则向量空间错位。
    """

    def __call__(self, input: Documents) -> Embeddings:
        """把一批文档文本转成向量，返回顺序与入参 input 一一对应"""
        texts: list[str] = [str(doc) for doc in input if doc and str(doc).strip()]
        if not texts:
            return cast(Embeddings, [])

        batch_size: int = 32
        all_embeddings: list[list[float]] = []

        for i in range(0, len(texts), batch_size):
            batch: list[str] = texts[i:i+batch_size]
            safe_batch: list[str] = [text[:8192] for text in batch]
            try:
                resp = embed_client.embeddings.create(
                    model=EMBED_MODEL,
                    input=batch,
                    extra_body = {"dimensions": None}
                )

                vecs = sorted(resp.data, key=lambda d: d.index)
                all_embeddings.extend([v.embedding for v in vecs])
            except Exception as e:
                logger.error(f"Embedding batch {i//batch_size} failed: {e}")
                raise
        return cast(Embeddings, all_embeddings)


collection: Collection = vector_manager.client.get_or_create_collection(
    name=COLLECTION_NAME,
    embedding_function=SiliconFlowEmbedding(),
    metadata={"hnsw:space": "cosine"},
)

# ============ 第1步：PDF 转 Markdown + 提取图片（新 API） ============
doc: pymupdf.Document = pymupdf.open(PDF_FILE_PATH)  # 先打开 Document，再调方法
md_text: str = doc.to_markdown(
    write_images=True,              # 图片落盘
    image_path=IMAGE_DIR,           # 保存目录
    image_format="png",
    image_size_limit=0.05,          # 过滤小于页面5%的图
    # 版面分析 2.0 默认开启：多栏顺序、页眉页脚剔除、表格识别自动生效
)
doc.close()
Path(MD_PATH).write_text(md_text)
print(f"Markdown 已生成: {len(md_text)} 字符")

# ============ 第2步：文本切块 ============
def chunk_markdown(md: str, chunk_size: int = 500) -> list[str]:
    """按 markdown 标题分段、再按中文句末标点切块（不做长度硬切，交给 hard_split 兜底）"""
    sections: list[str] = re.split(r'\n(?=#{1,3} )', md)
    chunks: list[str] = []
    for sec in sections:
        sec = sec.strip()
        if not sec:
            continue
        if len(sec) <= chunk_size:
            chunks.append(sec)
        else:
            title: str = sec.split('\n')[0]
            sentences: list[str] = re.split(r'(?<=[。！？])\s*', sec[len(title):])  # 只在中文句末标点切分，表格/无标点长段落不会做切分
            buf: str = title + '\n'
            for s in sentences:
                if len(buf) + len(s) > chunk_size and buf.strip() != title:
                    chunks.append(buf.strip())
                    buf = title + '\n' + s
                else:
                    buf += s
            if buf.strip():
                chunks.append(buf.strip())
    return [c for c in chunks if len(c) > 20]

def hard_split(chunks: list[str], max_chars: int = MAX_CHUNK_CHARS, min_keep_chars: int = 20) -> list[str]:
    """把超过 max_chars 的超长块按行硬切，避免单条文本超出 embedding 模型的上下文上限"""
    result: list[str] = []

    for chunk in chunks:
        if len(chunk) <= max_chars:
            result.append(chunk)
            continue

        split_pieces: list[str] = _split_long_chunk(chunk, max_chars)
        result.extend(split_pieces)

    return [c for c in result if len(c.strip()) > min_keep_chars]

# 贪心拼接，对于有些很短的行，拼接合并到一个chunk里
def _split_long_chunk(text: str, max_chars: int) -> list[str]:
    """按行贪心拼接切分单个超长块，单行自身超长时再按 max_chars 硬切"""
    pieces: list[str] = []
    buffer: str = ""

    for line in text.split("\n"):
        # 1.单行超长：强制按max_chars切片，剩余部分继续参与后续拼接
        while len(line) > max_chars:
            pieces.append(line[:max_chars])
            line = line[max_chars:]
        # 2.拼接后超限，输出当前buffer，用本行开启新buffer
        if buffer and len(buffer) + 1 + len(line) > max_chars:
            pieces.append(buffer)
            buffer = line # 超了line不加到pieces中，作为新的buffer
        else:
            # 3.未超限：追加到buffer(首行不加换行符)
            buffer = f"{buffer}\n{line}" if buffer else line

    # 4.输出尾部残留
    if buffer:
        pieces.append(buffer)

    return pieces

text_chunks: list[str] = hard_split(chunk_markdown(md_text))
print(f"文本切出 {len(text_chunks)} 个 chunk，最长 {max((len(c) for c in text_chunks), default=0)} 字符")

# ============ 第3步：图片 caption（带缓存，不变） ============
CAPTION_PROMPT: str = """这是产品说明书中的示意图。请描述：
1. 图的主题（什么产品/什么场景）
2. 所有标注文字（部件名称），逐个列出
3. 各部件的位置关系（上下、内外、左右）
输出简洁的中文描述，用于检索索引，不要客套话。"""

def caption_image(img_bytes: bytes) -> str:
    """调用视觉模型为说明书图片生成中文描述，作为图片的检索文本"""
    b64: str = base64.b64encode(img_bytes).decode()
    resp = chat_client.chat.completions.create(
        model=VISION_MODEL,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": CAPTION_PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ],
        }],
        temperature=0.1,
    )
    content: str | None = resp.choices[0].message.content
    if content is None:
        # 空描述会让 documents 与 embedding 条数对不上，必须显式报错而不是写空串
        raise ValueError("视觉模型未返回图片描述")
    return content

cache: dict[str, str] = json.loads(Path(CAPTION_CACHE).read_text()) if Path(CAPTION_CACHE).exists() else {}

# 版面分析模式下图片引用仍是标准 markdown 图片语法
image_paths: list[str] = re.findall(r'!\[\]\((assets/images/[^)]+)\)', md_text)
print(f"提取到 {len(image_paths)} 张图")

image_records: list[dict[str, str]] = []
for img_path in image_paths:
    img_bytes: bytes = Path(img_path).read_bytes()
    key: str = hashlib.md5(img_bytes).hexdigest()
    caption: str
    if key in cache:
        caption = cache[key]
        print(f"[缓存] {img_path}")
    else:
        caption = caption_image(img_bytes)
        cache[key] = caption
        Path(CAPTION_CACHE).write_text(json.dumps(cache, ensure_ascii=False, indent=1))
        print(f"[新图] {img_path} -> {caption[:40]}...")
    image_records.append({"path": img_path, "caption": caption})

# ============ 第4步：写入 chromadb（不变） ============
collection.add(
    ids=[f"text_{i}" for i in range(len(text_chunks))],
    documents=text_chunks,
    metadatas=[{"type": "text", "doc": PDF_FILE} for _ in text_chunks],
)
collection.add(
    ids=[f"img_{Path(r['path']).stem}" for r in image_records],
    documents=[r["caption"] for r in image_records],
    metadatas=[{"type": "image", "image_path": r["path"], "doc": PDF_FILE}
               for r in image_records],
)
print(f"建库完成: {collection.count()} 条记录")