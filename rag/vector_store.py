"""
向量数据库配置
配置ChromaDB客户端，创建和管理知识库集合

约定（见 .clinerules）:
- embedding 使用配置文件中的硅基流动 API（EMBEDDING_BASE_URL / EMBEDDING_API_KEY / EMBEDDING_MODEL）
- 异常优先精确捕获，不使用裸 Exception
- 同时提供同步接口和真异步接口（异步接口直接调用底层异步 API，不在异步方法里套同步方法）
"""

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

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
        """同步写入的公共实现, 分批入库"""
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
        """真异步写入: 直接 await 底层异步写入接口(embedding 走异步 HTTP)"""
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


# 全局实例, 懒加载单例, 直接在import时创建会拖慢启动
_vector_store_manager: Optional[VectorStoreManager] = None


def get_vector_store_manager() -> VectorStoreManager:
    """获取全局向量存储管理器"""
    global _vector_store_manager

    if _vector_store_manager is None:
        _vector_store_manager = VectorStoreManager()

    return _vector_store_manager
