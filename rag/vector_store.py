"""
向量数据库配置
配置ChromaDB客户端，创建和管理知识库集合

约定（见 .clinerules）:
- embedding 使用配置文件中的硅基流动 API（EMBEDDING_BASE_URL / EMBEDDING_API_KEY / EMBEDDING_MODEL）
- 异常优先精确捕获，不使用裸 Exception
- 同时提供同步接口和真异步接口（异步接口直接调用底层异步 API，不在异步方法里套同步方法）
"""

import os
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import anyio.to_thread
import chromadb
import openai
from chromadb.config import Settings
from langchain_community.vectorstores import Chroma
from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings

from core.logger import logger
from settings import get_settings

POLICY_COLLECTION_NAME = "policy_docs"
MANUAL_COLLECTION_NAME = "manual_docs"

# 单次写入向量库的文档块数量, 避免单次 embedding 请求体过大
ADD_BATCH_SIZE = 32


class SiliconFlowEmbeddings(OpenAIEmbeddings):
    """硅基流动的 embedding API 适配器

    硅基流动兼容 OpenAI 协议, 因此复用 OpenAIEmbeddings,
    只把 base_url / api_key / model 替换成配置文件中的硅基流动参数。
    """

    def __init__(self, **kwargs: Any) -> None:
        settings = get_settings()
        super().__init__(
            openai_api_key=settings.EMBEDDING_API_KEY,
            openai_api_base=settings.EMBEDDING_BASE_URL,
            model=settings.EMBEDDING_MODEL,
            **kwargs,
        )


def _distance_to_relevance(distance: float) -> float:
    """把 Chroma 返回的距离转换成 (0, 1] 的相关度

    similarity_search_with_score 返回的是距离(越小越相关), 不能直接用 1 - distance。
    这里用 1 / (1 + distance) 做单调归一化: 距离 0 -> 1.0, 距离越大越趋近 0。
    """
    return round(1.0 / (1.0 + max(distance, 0.0)), 4)


def _to_documents(results: Sequence[Tuple[Document, float]]) -> List[Document]:
    """把 (文档, 距离) 结果转换成带相关度元数据的文档列表"""
    documents: List[Document] = []
    for doc, distance in results:
        metadata = doc.metadata.copy()
        metadata["distance"] = float(distance)
        metadata["relevance_score"] = _distance_to_relevance(float(distance))
        documents.append(Document(page_content=doc.page_content, metadata=metadata))
    return documents


def _chunked(documents: List[Document], batch_size: int = ADD_BATCH_SIZE) -> List[List[Document]]:
    """按批切分文档, 供写入向量库时使用"""
    return [documents[i:i + batch_size] for i in range(0, len(documents), batch_size)]


def _date_to_number(value: Any) -> int:
    """把 ISO 日期转换成 YYYYMMDD 整数，供 Chroma 的数值区间过滤使用。"""
    if isinstance(value, datetime):
        return int(value.date().strftime("%Y%m%d"))
    if isinstance(value, date):
        return int(value.strftime("%Y%m%d"))
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(datetime.fromisoformat(value).date().strftime("%Y%m%d"))
        except ValueError as e:
            raise ValueError(f"effective_date 不是合法 ISO 日期: {value}") from e
    raise ValueError(f"effective_date 过滤值类型不支持: {type(value).__name__}")


def _normalize_filter_conditions(conditions: Dict[str, Any]) -> Dict[str, Any]:
    """把 effective_date 字符串区间转换成 Chroma 可执行的数值区间。

    Chroma 1.x 的 ``$gte/$lte`` 只接受 int/float，而 policy_retriever 用
    ISO 字符串构造更易读的过滤条件；这里在进入 Chroma 前统一转换，并确保
    单个字段的多个操作符用 ``$and`` 拆开。
    """
    if "$and" in conditions:
        normalized: List[Dict[str, Any]] = []
        for item in conditions["$and"]:
            child = _normalize_filter_conditions(item)
            if set(child) == {"$and"}:
                normalized.extend(child["$and"])
            else:
                normalized.append(child)
        return {"$and": normalized}

    if "$or" in conditions:
        return {"$or": [_normalize_filter_conditions(item) for item in conditions["$or"]]}

    if "effective_date" in conditions and isinstance(conditions["effective_date"], dict):
        date_conditions: List[Dict[str, Any]] = []
        for operator, value in conditions["effective_date"].items():
            date_conditions.append(
                {"effective_date_num": {operator: _date_to_number(value)}}
            )

        other_conditions = [
            {key: value}
            for key, value in conditions.items()
            if key != "effective_date"
        ]
        date_conditions = other_conditions + date_conditions
        if len(date_conditions) == 1:
            return date_conditions[0]
        return {"$and": date_conditions}

    return conditions


class VectorStoreManager:
    """向量存储管理器, 支持政策(policy)和说明书(manual)两个集合的读写"""

    def __init__(self) -> None:
        self.settings = get_settings()
        project_root = Path(__file__).parent.parent
        self.persist_directory = str(project_root / ".chroma_db")
        os.makedirs(self.persist_directory, exist_ok=True)

        try:
            self.client = chromadb.PersistentClient(
                path=self.persist_directory,
                settings=Settings(anonymized_telemetry=False),
            )
        except chromadb.errors.ChromaError as e:
            logger.error("ChromaDB客户端初始化失败", error=str(e))
            raise

        try:
            self.embeddings = SiliconFlowEmbeddings()
        except ValueError as e:
            # 配置项缺失或模型名非法
            logger.error("Embedding模型初始化失败", error=str(e))
            raise

        self.policy_collection = self._build_collection(POLICY_COLLECTION_NAME)
        self.manual_collection = self._build_collection(MANUAL_COLLECTION_NAME)

        logger.info("向量存储初始化完成", persist_directory=self.persist_directory)

    def _build_collection(self, collection_name: str) -> Chroma:
        """创建(或复用)一个向量集合"""
        try:
            return Chroma(
                collection_name=collection_name,
                embedding_function=self.embeddings,
                client=self.client,
                persist_directory=self.persist_directory,
            )
        except chromadb.errors.ChromaError as e:
            logger.error("向量集合初始化失败", collection_name=collection_name, error=str(e))
            raise

    def _reset_collection(self, collection_name: str) -> Chroma:
        """删除并重建集合, 保证重复向量化时不会产生重复数据"""
        # 因为Chroma的add_documents是追加式的，不查重
        try:
            self.client.delete_collection(name=collection_name)
            logger.info("已删除向量集合", collection_name=collection_name)
        except chromadb.errors.NotFoundError as e:
            # 首次运行集合还不存在, 属于正常情况
            logger.info("向量集合不存在, 直接创建", collection_name=collection_name, error=str(e))
        except chromadb.errors.ChromaError as e:
            logger.error("删除向量集合失败", collection_name=collection_name, error=str(e))
            raise
        return self._build_collection(collection_name)

    def reset_policy_collection(self) -> None:
        """清空政策集合, 用于重新向量化"""
        self.policy_collection = self._reset_collection(POLICY_COLLECTION_NAME)

    def reset_manual_collection(self) -> None:
        """清空说明书集合, 用于重新向量化"""
        self.manual_collection = self._reset_collection(MANUAL_COLLECTION_NAME)

    def get_collection_count(self, collection_name: str) -> int:
        """获取集合中文档块数量, 用于验证向量化结果"""
        try:
            return self.client.get_collection(name=collection_name).count()
        except chromadb.errors.NotFoundError:
            return 0
        except chromadb.errors.ChromaError as e:
            logger.error("获取集合数量失败", collection_name=collection_name, error=str(e))
            raise

    # ------------------------------------------------------------------ 检索
    def _search(
        self,
        collection: Chroma,
        collection_name: str,
        query: str,
        filter_conditions: Optional[Dict[str, Any]],
        top_k: int,
    ) -> List[Document]:
        """同步检索的公共实现"""
        # [(document1, score1), (document2, score2)], 分数越低越相似
        if filter_conditions:
            filter_conditions = _normalize_filter_conditions(filter_conditions)
        try:
            results = collection.similarity_search_with_score(
                query=query,
                k=top_k,
                filter=filter_conditions,
            )
        except chromadb.errors.ChromaError as e:
            logger.error("向量检索失败", collection=collection_name, error=str(e))
            raise
        except ValueError as e:
            # 过滤条件不合法(例如字段类型不匹配)
            logger.error("检索参数无效", collection=collection_name, error=str(e))
            raise
        except openai.APIError as e:
            logger.error("硅基流动embedding接口调用失败", collection=collection_name, error=str(e))
            raise
        return _to_documents(results)

    async def _asearch(
        self,
        collection: Chroma,
        collection_name: str,
        query: str,
        filter_conditions: Optional[Dict[str, Any]],
        top_k: int,
    ) -> List[Document]:
        """真异步检索: 直接 await 底层异步检索接口(embedding 走异步 HTTP)"""
        if filter_conditions:
            filter_conditions = _normalize_filter_conditions(filter_conditions)
        try:
            results = await collection.asimilarity_search_with_score(
                query=query,
                k=top_k,
                filter=filter_conditions,
            )
        except chromadb.errors.ChromaError as e:
            logger.error("向量检索失败(异步)", collection=collection_name, error=str(e))
            raise
        except ValueError as e:
            logger.error("检索参数无效(异步)", collection=collection_name, error=str(e))
            raise
        except openai.APIError as e:
            logger.error("硅基流动embedding接口调用失败(异步)", collection=collection_name, error=str(e))
            raise
        return _to_documents(results)

    def search_policy_documents(
        self,
        query: str,
        filter_conditions: Optional[Dict[str, Any]] = None,
        top_k: int = 5,
    ) -> List[Document]:
        """同步搜索政策文档"""
        return self._search(self.policy_collection, POLICY_COLLECTION_NAME, query, filter_conditions, top_k)

    async def asearch_policy_documents(
        self,
        query: str,
        filter_conditions: Optional[Dict[str, Any]] = None,
        top_k: int = 5,
    ) -> List[Document]:
        """异步搜索政策文档"""
        return await self._asearch(self.policy_collection, POLICY_COLLECTION_NAME, query, filter_conditions, top_k)

    def search_manual_documents(
        self,
        query: str,
        filter_conditions: Optional[Dict[str, Any]] = None,
        top_k: int = 5,
    ) -> List[Document]:
        """同步搜索说明书文档"""
        return self._search(self.manual_collection, MANUAL_COLLECTION_NAME, query, filter_conditions, top_k)

    async def asearch_manual_documents(
        self,
        query: str,
        filter_conditions: Optional[Dict[str, Any]] = None,
        top_k: int = 5,
    ) -> List[Document]:
        """异步搜索说明书文档"""
        return await self._asearch(self.manual_collection, MANUAL_COLLECTION_NAME, query, filter_conditions, top_k)

    # ------------------------------------------------------------------ 写入
    def _add(self, collection: Chroma, collection_name: str, documents: List[Document]) -> List[str]:
        """同步写入的公共实现。

        新版文档块自带稳定 chunk_id 时优先走 upsert；旧调用方没有 ID 时仍走
        add_documents，保持兼容。
        """
        stable_ids = [document.metadata.get("chunk_id") for document in documents]
        if documents and all(isinstance(chunk_id, str) and chunk_id for chunk_id in stable_ids):
            return self._upsert(collection_name, documents, [str(chunk_id) for chunk_id in stable_ids])

        ids: List[str] = []
        for batch in _chunked(documents):
            try:
                ids.extend(collection.add_documents(batch))
            except chromadb.errors.ChromaError as e:
                logger.error("写入向量库失败", collection=collection_name, error=str(e))
                raise
            except ValueError as e:
                # 文档元数据不合法(Chroma 只接受基础类型)
                logger.error("文档数据格式错误", collection=collection_name, error=str(e))
                raise
            except openai.APIError as e:
                logger.error("硅基流动embedding接口调用失败", collection=collection_name, error=str(e))
                raise
        logger.info("文档写入向量库完成", collection=collection_name, chunks=len(ids))
        return ids

    async def _aadd(self, collection: Chroma, collection_name: str, documents: List[Document]) -> List[str]:
        """真异步写入：有稳定 chunk_id 时走 upsert，旧文档走异步 add。"""
        stable_ids = [document.metadata.get("chunk_id") for document in documents]
        if documents and all(isinstance(chunk_id, str) and chunk_id for chunk_id in stable_ids):
            return await self._aupsert(
                collection_name, documents, [str(chunk_id) for chunk_id in stable_ids]
            )

        ids: List[str] = []
        for batch in _chunked(documents):
            try:
                ids.extend(await collection.aadd_documents(batch))
            except chromadb.errors.ChromaError as e:
                logger.error("写入向量库失败(异步)", collection=collection_name, error=str(e))
                raise
            except ValueError as e:
                logger.error("文档数据格式错误(异步)", collection=collection_name, error=str(e))
                raise
            except openai.APIError as e:
                logger.error("硅基流动embedding接口调用失败(异步)", collection=collection_name, error=str(e))
                raise
        logger.info("文档写入向量库完成(异步)", collection=collection_name, chunks=len(ids))
        return ids

    def add_policy_documents(self, documents: List[Document]) -> List[str]:
        """同步添加政策文档"""
        return self._add(self.policy_collection, POLICY_COLLECTION_NAME, documents)

    async def aadd_policy_documents(self, documents: List[Document]) -> List[str]:
        """异步添加政策文档"""
        return await self._aadd(self.policy_collection, POLICY_COLLECTION_NAME, documents)

    def add_manual_documents(self, documents: List[Document]) -> List[str]:
        """同步添加说明书文档"""
        return self._add(self.manual_collection, MANUAL_COLLECTION_NAME, documents)

    async def aadd_manual_documents(self, documents: List[Document]) -> List[str]:
        """异步添加说明书文档"""
        return await self._aadd(self.manual_collection, MANUAL_COLLECTION_NAME, documents)


    # ------------------------------------------------------- upsert / 增量清理
    def _get_native_collection(self, collection_name: str) -> Any:
        """获取 Chroma 原生集合，用于按稳定 ID upsert。"""
        try:
            return self.client.get_or_create_collection(name=collection_name)
        except chromadb.errors.ChromaError as e:
            logger.error("获取原生向量集合失败", collection=collection_name, error=str(e))
            raise

    @staticmethod
    def _validate_upsert_input(documents: List[Document], ids: List[str]) -> None:
        if len(documents) != len(ids):
            raise ValueError(
                f"documents 与 ids 数量不一致: documents={len(documents)}, ids={len(ids)}"
            )
        if len(ids) != len(set(ids)):
            raise ValueError("写入 ID 存在重复，无法保证幂等写入")

        for document in documents:
            for key, value in document.metadata.items():
                if value is None or isinstance(value, (list, dict, tuple, set)):
                    raise ValueError(
                        f"向量元数据 {key} 必须是 str/int/float/bool 标量, "
                        f"当前为 {type(value).__name__}"
                    )

    def _upsert_sync_batch(
        self,
        collection_name: str,
        ids: List[str],
        documents: List[str],
        metadatas: List[Dict[str, Any]],
        embeddings: List[List[float]],
    ) -> None:
        """在线程池中执行 Chroma 原生 upsert（阻塞调用）。"""
        collection = self._get_native_collection(collection_name)
        try:
            collection.upsert(
                ids=ids,
                embeddings=embeddings,
                documents=documents,
                metadatas=metadatas,
            )
        except chromadb.errors.ChromaError as e:
            logger.error("向量 upsert 失败", collection=collection_name, error=str(e))
            raise
        except ValueError as e:
            logger.error("向量 upsert 元数据/参数错误", collection=collection_name, error=str(e))
            raise

    def _upsert(
        self,
        collection_name: str,
        documents: List[Document],
        ids: List[str],
    ) -> List[str]:
        """同步 upsert：先做 embedding，再按稳定 ID 覆盖写入。"""
        self._validate_upsert_input(documents, ids)
        if not documents:
            return []

        for start in range(0, len(documents), ADD_BATCH_SIZE):
            batch_documents = documents[start:start + ADD_BATCH_SIZE]
            batch_ids = ids[start:start + ADD_BATCH_SIZE]
            texts = [document.page_content for document in batch_documents]
            metadatas = [dict(document.metadata) for document in batch_documents]
            try:
                embeddings = self.embeddings.embed_documents(texts)
                self._upsert_sync_batch(collection_name, batch_ids, texts, metadatas, embeddings)
            except chromadb.errors.ChromaError:
                raise
            except ValueError:
                raise
            except openai.APIError as e:
                logger.error(
                    "硅基流动 embedding 接口调用失败",
                    collection=collection_name,
                    error=str(e),
                )
                raise

        logger.info("文档 upsert 完成", collection=collection_name, chunks=len(ids))
        return ids

    async def _aupsert(
        self,
        collection_name: str,
        documents: List[Document],
        ids: List[str],
    ) -> List[str]:
        """真异步 upsert：await 异步 embedding，Chroma 阻塞调用放线程池。"""
        self._validate_upsert_input(documents, ids)
        if not documents:
            return []

        for start in range(0, len(documents), ADD_BATCH_SIZE):
            batch_documents = documents[start:start + ADD_BATCH_SIZE]
            batch_ids = ids[start:start + ADD_BATCH_SIZE]
            texts = [document.page_content for document in batch_documents]
            metadatas = [dict(document.metadata) for document in batch_documents]
            try:
                embeddings = await self.embeddings.aembed_documents(texts)
                await anyio.to_thread.run_sync(
                    self._upsert_sync_batch,
                    collection_name,
                    batch_ids,
                    texts,
                    metadatas,
                    embeddings,
                )
            except chromadb.errors.ChromaError:
                raise
            except ValueError:
                raise
            except openai.APIError as e:
                logger.error(
                    "硅基流动 embedding 接口调用失败(异步)",
                    collection=collection_name,
                    error=str(e),
                )
                raise

        logger.info("文档 upsert 完成(异步)", collection=collection_name, chunks=len(ids))
        return ids

    def upsert_policy_documents(self, documents: List[Document], ids: List[str]) -> List[str]:
        return self._upsert(POLICY_COLLECTION_NAME, documents, ids)

    async def aupsert_policy_documents(
        self, documents: List[Document], ids: List[str]
    ) -> List[str]:
        return await self._aupsert(POLICY_COLLECTION_NAME, documents, ids)

    def upsert_manual_documents(self, documents: List[Document], ids: List[str]) -> List[str]:
        return self._upsert(MANUAL_COLLECTION_NAME, documents, ids)

    async def aupsert_manual_documents(
        self, documents: List[Document], ids: List[str]
    ) -> List[str]:
        return await self._aupsert(MANUAL_COLLECTION_NAME, documents, ids)



    # --------------------------------------------------------- 增量清理接口
    def get_ids_by_document_id(self, collection_name: str, document_id: str) -> set[str]:
        """查询某个源文件在集合中的旧块 ID。"""
        collection = self._get_native_collection(collection_name)
        try:
            result = collection.get(where={"document_id": document_id}, include=[])
        except chromadb.errors.ChromaError as e:
            logger.error(
                "查询文档旧块失败",
                collection=collection_name,
                document_id=document_id,
                error=str(e),
            )
            raise
        return set(result.get("ids") or [])

    def delete_ids(self, collection_name: str, ids: List[str]) -> int:
        """删除指定 ID，返回实际请求删除的数量。"""
        if not ids:
            return 0
        collection = self._get_native_collection(collection_name)
        try:
            collection.delete(ids=ids)
        except chromadb.errors.ChromaError as e:
            logger.error("删除旧块失败", collection=collection_name, error=str(e))
            raise
        logger.info("删除旧文档块", collection=collection_name, chunks=len(ids))
        return len(ids)

    def delete_stale_ids(
        self, collection_name: str, document_id: str, keep_ids: List[str]
    ) -> List[str]:
        """删除该文档中不属于本次构建的旧 ID（先 upsert、后清理）。"""
        existing_ids = self.get_ids_by_document_id(collection_name, document_id)
        stale_ids = sorted(existing_ids - set(keep_ids))
        if stale_ids:
            self.delete_ids(collection_name, stale_ids)
        return stale_ids



# 全局实例, 懒加载单例, 直接在import时创建会拖慢启动
_vector_store_manager: Optional[VectorStoreManager] = None


def get_vector_store_manager() -> VectorStoreManager:
    """获取全局向量存储管理器"""
    global _vector_store_manager

    if _vector_store_manager is None:
        _vector_store_manager = VectorStoreManager()

    return _vector_store_manager
