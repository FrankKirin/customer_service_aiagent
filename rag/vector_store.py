"""
向量数据库配置
配置ChromaDB客户端，创建和管理知识库集合
使用硅基流动的embedding API
"""

import os
from typing import List, Dict, Any, Optional
from pathlib import Path
import chromadb
from chromadb.config import Settings
from langchain_community.vectorstores import Chroma
from langchain_openai import OpenAIEmbeddings
from langchain_core.documents import Document

from core.logger import logger
from settings import get_settings


class SiliconFlowEmbeddings(OpenAIEmbeddings):
    """硅基流动的embedding API适配器"""
    
    def __init__(self, **kwargs):
        from settings import get_settings
        settings = get_settings()
        
        super().__init__(
            openai_api_key=settings.EMBEDDING_API_KEY,
            openai_api_base=settings.EMBEDDING_BASE_URL,
            model=settings.EMBEDDING_MODEL,
            **kwargs
        )


class VectorStoreManager:
    """向量存储管理器, 支持police和manual的检索"""
    
    def __init__(self):
        self.settings = get_settings()
        project_root = Path(__file__).parent.parent
        self.persist_directory = str(project_root / ".chroma_db")
        os.makedirs(self.persist_directory, exist_ok=True)
        
        # 初始化客户端
        try:
            self.client = chromadb.PersistentClient(
                path=self.persist_directory,
                settings=Settings(anonymized_telemetry=False)
            )
        except chromadb.errors.ChromaError as e:
            logger.error("ChromaDB客户端初始化失败", error=str(e))
            raise
        
        # 使用硅基流动的embedding API
        try:
            self.embeddings = SiliconFlowEmbeddings()
        except ValueError as e:
            logger.error("Embedding模型初始化失败", error=str(e))
            raise
        except ConnectionError as e:
            logger.error("无法连接到硅基流动API", error=str(e))
            raise
        
        # 初始化两个集合
        try:
            self.policy_collection = Chroma(
                collection_name="policy_docs",
                embedding_function=self.embeddings,
                client=self.client,
                persist_directory=self.persist_directory
            )
            
            self.manual_collection = Chroma(
                collection_name="manual_docs", 
                embedding_function=self.embeddings,
                client=self.client,
                persist_directory=self.persist_directory
            )
        except chromadb.errors.ChromaError as e:
            logger.error("向量集合初始化失败", error=str(e))
            raise
        
        logger.info("向量存储初始化完成")
    
    def search_policy_documents(
        self, 
        query: str, 
        filter_conditions: Optional[Dict[str, Any]] = None,
        top_k: int = 5
    ) -> List[Document]:
        """搜索政策文档"""
        try:
            results = self.policy_collection.similarity_search_with_score(
                query=query,
                k=top_k,
                filter=filter_conditions
            )
            
            docs = []
            for doc, score in results:
                metadata = doc.metadata.copy()
                metadata["similarity_score"] = float(score)
                metadata["relevance_score"] = 1.0 - min(max(score, 0), 1)
                docs.append(Document(page_content=doc.page_content, metadata=metadata))
            
            return docs
        except chromadb.errors.ChromaError as e:
            logger.error("搜索政策文档失败", error=str(e))
            return []
        except ValueError as e:
            logger.error("查询参数无效", error=str(e))
            return []
    
    def search_manual_documents(
        self, 
        query: str, 
        filter_conditions: Optional[Dict[str, Any]] = None,
        top_k: int = 5
    ) -> List[Document]:
        """搜索说明书文档"""
        try:
            results = self.manual_collection.similarity_search_with_score(
                query=query,
                k=top_k,
                filter=filter_conditions
            )
            
            docs = []
            for doc, score in results:
                metadata = doc.metadata.copy()
                metadata["similarity_score"] = float(score)
                metadata["relevance_score"] = 1.0 - min(max(score, 0), 1)
                docs.append(Document(page_content=doc.page_content, metadata=metadata))
            
            return docs
        except chromadb.errors.ChromaError as e:
            logger.error("搜索说明书文档失败", error=str(e))
            return []
        except ValueError as e:
            logger.error("查询参数无效", error=str(e))
            return []
    
    def add_policy_documents(self, documents: List[Document]) -> List[str]:
        """添加政策文档到向量存储"""
        try:
            ids = self.policy_collection.add_documents(documents)
            logger.info("政策文档添加成功", count=len(documents))
            return ids
        except chromadb.errors.ChromaError as e:
            logger.error("添加政策文档失败", error=str(e))
            raise
        except ValueError as e:
            logger.error("文档数据格式错误", error=str(e))
            raise
    
    def add_manual_documents(self, documents: List[Document]) -> List[str]:
        """添加说明书文档到向量存储"""
        try:
            ids = self.manual_collection.add_documents(documents)
            logger.info("说明书文档添加成功", count=len(documents))
            return ids
        except chromadb.errors.ChromaError as e:
            logger.error("添加说明书文档失败", error=str(e))
            raise
        except ValueError as e:
            logger.error("文档数据格式错误", error=str(e))
            raise


# 全局实例, 懒加载单例, 直接创建影响import，import变慢
_vector_store_manager: Optional[VectorStoreManager] = None

def get_vector_store_manager() -> VectorStoreManager:
    """获取全局向量存储管理器"""
    global _vector_store_manager
    
    if _vector_store_manager is None:
        _vector_store_manager = VectorStoreManager()
    
    return _vector_store_manager