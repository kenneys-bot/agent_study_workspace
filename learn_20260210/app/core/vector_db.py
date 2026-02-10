"""
向量数据库客户端模块
提供与Chroma向量数据库的交互接口
"""

import logging
from typing import List, Dict, Optional, Any
from datetime import datetime
import uuid
import hashlib

from config.settings import settings
from config.constants import (
    VECTOR_DB_COLLECTION,
    DEFAULT_TOP_K,
    MAX_TOP_K,
    ErrorMessages
)

logger = logging.getLogger(__name__)


class VectorDBException(Exception):
    """向量数据库异常基类"""
    pass


class ConnectionError(VectorDBException):
    """连接异常"""
    pass


class QueryError(VectorDBException):
    """查询异常"""
    pass


class StorageError(VectorDBException):
    """存储异常"""
    pass


class Document:
    """
    文档数据模型
    """
    
    def __init__(
        self,
        id: str = None,
        content: str = None,
        metadata: Dict[str, Any] = None,
        embedding: List[float] = None
    ):
        """
        初始化文档
        
        Args:
            id (str): 文档唯一标识
            content (str): 文档内容
            metadata (Dict[str, Any]): 元数据
            embedding (List[float]): 向量表示
        """
        self.id = id or str(uuid.uuid4())
        self.content = content or ""
        self.metadata = metadata or {}
        self.embedding = embedding
        self.created_at = datetime.now()
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "id": self.id,
            "content": self.content,
            "metadata": self.metadata,
            "embedding": self.embedding,
            "created_at": self.created_at.isoformat()
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Document":
        """从字典创建文档"""
        return cls(
            id=data.get("id"),
            content=data.get("content"),
            metadata=data.get("metadata", {}),
            embedding=data.get("embedding")
        )


class VectorDBClient:
    """
    Chroma向量数据库客户端
    提供向量存储、检索和管理功能
    """
    
    def __init__(self, collection_name: str = VECTOR_DB_COLLECTION):
        """
        初始化向量数据库客户端
        
        Args:
            collection_name (str): 集合名称，默认为customer_service_knowledge
        """
        self.collection_name = collection_name
        self._client = None
        self._collection = None
        self._init_client()
    
    def _init_client(self):
        """初始化Chroma客户端"""
        try:
            import chromadb
            from chromadb.config import Settings
            
            # 初始化客户端
            self._client = chromadb.Client(Settings(
                chroma_server_host=settings.CHROMA_HOST,
                chroma_server_http_port=settings.CHROMA_PORT
            ))
            
            # 获取或创建集合
            self._collection = self._client.get_or_create_collection(
                name=self.collection_name,
                metadata={"description": "客服知识库向量数据"}
            )
            
            logger.info(f"向量数据库客户端初始化成功，集合: {self.collection_name}")
            
        except ImportError:
            logger.warning("Chroma库未安装，将使用内存模式")
            self._use_memory_mode()
        except Exception as e:
            logger.warning(f"Chroma连接失败，切换到内存模式: {str(e)}")
            self._use_memory_mode()
    
    def _use_memory_mode(self):
        """使用内存模式"""
        try:
            import chromadb
            from chromadb.config import Settings
            
            self._client = chromadb.Client(Settings(
                persist_directory="./chroma_data"
            ))
            self._collection = self._client.get_or_create_collection(
                name=self.collection_name,
                metadata={"description": "客服知识库向量数据"}
            )
        except ImportError:
            logger.error("Chroma库未安装，请先安装chromadb")
            raise StorageError("向量数据库客户端初始化失败")
    
    def add_documents(
        self,
        documents: List[str],
        metadatas: List[Dict[str, Any]] = None,
        ids: List[str] = None
    ) -> List[str]:
        """
        添加文档到向量数据库
        
        Args:
            documents (List[str]): 文档列表
            metadatas (List[Dict[str, Any]]): 元数据列表
            ids (List[str]): 文档ID列表
            
        Returns:
            List[str]: 添加的文档ID列表
        """
        try:
            # 生成ID
            if ids is None:
                ids = [str(uuid.uuid4()) for _ in documents]
            
            # 设置默认元数据
            if metadatas is None:
                metadatas = [{} for _ in documents]
            
            for i, meta in enumerate(metadatas):
                meta["created_at"] = datetime.now().isoformat()
            
            # 添加到集合
            self._collection.add(
                documents=documents,
                metadatas=metadatas,
                ids=ids
            )
            
            logger.info(f"成功添加 {len(documents)} 个文档")
            return ids
            
        except Exception as e:
            logger.error(f"添加文档失败: {str(e)}")
            raise StorageError(f"添加文档失败: {str(e)}")
    
    def query(
        self,
        query_texts: List[str],
        n_results: int = DEFAULT_TOP_K,
        where: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        查询相似文档
        
        Args:
            query_texts (List[str]): 查询文本列表
            n_results (int): 返回结果数量
            where (Dict[str, Any]): 过滤条件
            
        Returns:
            Dict[str, Any]: 查询结果
        """
        try:
            # 限制最大返回数量
            n_results = min(n_results, MAX_TOP_K)
            
            # 执行查询
            results = self._collection.query(
                query_texts=query_texts,
                n_results=n_results,
                where=where
            )
            
            logger.info(f"查询完成，返回 {len(results.get('documents', []))} 条结果")
            return results
            
        except Exception as e:
            logger.error(f"查询失败: {str(e)}")
            raise QueryError(f"查询失败: {str(e)}")
    
    def query_with_score(
        self,
        query_text: str,
        n_results: int = DEFAULT_TOP_K,
        where: Dict[str, Any] = None
    ) -> List[Dict[str, Any]]:
        """
        带分数的相似性搜索
        
        Args:
            query_text (str): 查询文本
            n_results (int): 返回结果数量
            where (Dict[str, Any]): 过滤条件
            
        Returns:
            List[Dict[str, Any]]: 带分数的查询结果
        """
        try:
            results = self.query(
                query_texts=[query_text],
                n_results=n_results,
                where=where
            )
            
            # 格式化结果
            formatted_results = []
            if results.get("documents") and results["documents"]:
                for i, doc in enumerate(results["documents"][0]):
                    result = {
                        "document": doc,
                        "metadata": results["metadatas"][0][i] if results.get("metadatas") else {},
                        "id": results["ids"][0][i] if results.get("ids") else None,
                        "distance": results.get("distances", [[]])[0][i] if results.get("distances") else None
                    }
                    formatted_results.append(result)
            
            return formatted_results
            
        except Exception as e:
            logger.error(f"带分数查询失败: {str(e)}")
            raise QueryError(f"查询失败: {str(e)}")
    
    def delete(
        self,
        ids: List[str] = None,
        where: Dict[str, Any] = None
    ) -> bool:
        """
        删除文档
        
        Args:
            ids (List[str]): 要删除的文档ID列表
            where (Dict[str, Any]): 过滤条件
            
        Returns:
            bool: 删除是否成功
        """
        try:
            self._collection.delete(
                ids=ids,
                where=where
            )
            logger.info(f"成功删除文档，ID: {ids}")
            return True
            
        except Exception as e:
            logger.error(f"删除文档失败: {str(e)}")
            raise StorageError(f"删除文档失败: {str(e)}")
    
    def update(
        self,
        ids: List[str],
        documents: List[str] = None,
        metadatas: List[Dict[str, Any]] = None
    ) -> bool:
        """
        更新文档
        
        Args:
            ids (List[str]): 要更新的文档ID列表
            documents (List[str]): 更新后的文档列表
            metadatas (List[Dict[str, Any]]): 更新后的元数据列表
            
        Returns:
            bool: 更新是否成功
        """
        try:
            self._collection.update(
                ids=ids,
                documents=documents,
                metadatas=metadatas
            )
            logger.info(f"成功更新文档，ID: {ids}")
            return True
            
        except Exception as e:
            logger.error(f"更新文档失败: {str(e)}")
            raise StorageError(f"更新文档失败: {str(e)}")
    
    def get_collection_info(self) -> Dict[str, Any]:
        """
        获取集合信息
        
        Returns:
            Dict[str, Any]: 集合信息
        """
        try:
            count = self._collection.count()
            return {
                "name": self.collection_name,
                "count": count,
                "metadata": self._collection.metadata
            }
        except Exception as e:
            logger.error(f"获取集合信息失败: {str(e)}")
            return {
                "name": self.collection_name,
                "count": 0,
                "error": str(e)
            }
    
    def list_collections(self) -> List[str]:
        """
        列出所有集合
        
        Returns:
            List[str]: 集合名称列表
        """
        try:
            collections = self._client.list_collections()
            return [col.name for col in collections]
        except Exception as e:
            logger.error(f"列出集合失败: {str(e)}")
            return []
    
    def create_collection(
        self,
        name: str,
        metadata: Dict[str, Any] = None
    ) -> bool:
        """
        创建集合
        
        Args:
            name (str): 集合名称
            metadata (Dict[str, Any]): 集合元数据
            
        Returns:
            bool: 创建是否成功
        """
        try:
            self._client.create_collection(
                name=name,
                metadata=metadata or {"description": "自定义集合"}
            )
            logger.info(f"成功创建集合: {name}")
            return True
        except Exception as e:
            logger.error(f"创建集合失败: {str(e)}")
            raise StorageError(f"创建集合失败: {str(e)}")
    
    def delete_collection(self, name: str) -> bool:
        """
        删除集合
        
        Args:
            name (str): 集合名称
            
        Returns:
            bool: 删除是否成功
        """
        try:
            self._client.delete_collection(name)
            logger.info(f"成功删除集合: {name}")
            return True
        except Exception as e:
            logger.error(f"删除集合失败: {str(e)}")
            raise StorageError(f"删除集合失败: {str(e)}")


# 便捷函数
def get_vector_db_client(
    collection_name: str = VECTOR_DB_COLLECTION
) -> VectorDBClient:
    """
    获取向量数据库客户端实例
    
    Args:
        collection_name (str): 集合名称
        
    Returns:
        VectorDBClient: 向量数据库客户端实例
    """
    return VectorDBClient(collection_name=collection_name)
