"""
文档向量化处理器
处理raw_data中的政策文档和说明书文档，将其向量化并存储到ChromaDB
"""

import os
import glob
from typing import List, Dict, Any, Optional
from pathlib import Path
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from core.logger import logger

from rag.vector_store import get_vector_store_manager
# from rag.chunker import TextChunker  # 如果存在的话


class DocumentProcessor:
    """文档向量化处理器"""
    
    def __init__(self):
        self.vector_store = get_vector_store_manager()
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=500,
            chunk_overlap=50,
            length_function=len,
            separators=["\n\n", "\n", "。", "！", "？", "；", "，", " ", ""]
        )
        
    def process_policy_documents(self, directory: str = "raw_data/policy_docs") -> List[str]:
        """处理政策文档目录"""
        try:
            # 查找所有文档文件
            patterns = ["*.txt", "*.md", "*.pdf", "*.docx"]
            all_files = []
            for pattern in patterns:
                all_files.extend(glob.glob(os.path.join(directory, pattern)))
            
            logger.info("找到政策文档文件", count=len(all_files), files=all_files[:5])
            
            all_documents = []
            
            for file_path in all_files:
                try:
                    logger.info("处理政策文档", file=file_path)
                    docs = self._process_policy_file(file_path)
                    all_documents.extend(docs)
                    
                except ValueError as e:
                    logger.error("解析政策文件失败", file=file_path, error=str(e))
                except UnicodeDecodeError as e:
                    logger.error("文本编码错误", file=file_path, error=str(e))
            
            # 添加到向量存储
            if all_documents:
                ids = self.vector_store.add_policy_documents(all_documents)
                logger.info("政策文档向量化完成", total_docs=len(all_documents), total_chunks=len(ids))
                return ids
            else:
                logger.warning("未找到可处理的政策文档")
                return []
                
        except FileNotFoundError as e:
            logger.error("政策文档目录不存在", directory=directory, error=str(e))
            return []
        except PermissionError as e:
            logger.error("无权访问政策文档目录", directory=directory, error=str(e))
            return []
    
    def process_manual_documents(self, directory: str = "raw_data/manual_docs") -> List[str]:
        """处理说明书文档目录"""
        try:
            # 查找所有文档文件
            patterns = ["*.txt", "*.md", "*.pdf", "*.docx"]
            all_files = []
            for pattern in patterns:
                all_files.extend(glob.glob(os.path.join(directory, pattern)))
            
            logger.info("找到说明书文档文件", count=len(all_files), files=all_files[:5])
            
            all_documents = []
            
            for file_path in all_files:
                try:
                    logger.info("处理说明书文档", file=file_path)
                    docs = self._process_manual_file(file_path)
                    all_documents.extend(docs)
                    
                except ValueError as e:
                    logger.error("解析说明书文件失败", file=file_path, error=str(e))
                except UnicodeDecodeError as e:
                    logger.error("文本编码错误", file=file_path, error=str(e))
            
            # 添加到向量存储
            if all_documents:
                ids = self.vector_store.add_manual_documents(all_documents)
                logger.info("说明书文档向量化完成", total_docs=len(all_documents), total_chunks=len(ids))
                return ids
            else:
                logger.warning("未找到可处理的说明书文档")
                return []
                
        except FileNotFoundError as e:
            logger.error("说明书文档目录不存在", directory=directory, error=str(e))
            return []
        except PermissionError as e:
            logger.error("无权访问说明书文档目录", directory=directory, error=str(e))
            return []
    
    def _process_policy_file(self, file_path: str) -> List[Document]:
        """处理单个政策文件"""
        text_content = self._read_file_content(file_path)
        
        # 从文件名提取元数据
        filename = Path(file_path).stem
        metadata = self._parse_policy_metadata(filename)
        metadata["source"] = filename
        metadata["file_path"] = file_path
        
        # 文本分块
        chunks = self.text_splitter.create_documents([text_content], [metadata])
        
        # 为每个块添加单独的元数据
        for i, chunk in enumerate(chunks):
            chunk.metadata["doc_id"] = f"{filename}_{i}"
            chunk.metadata["chunk_index"] = i
        
        return chunks
    
    def _process_manual_file(self, file_path: str) -> List[Document]:
        """处理单个说明书文件"""
        text_content = self._read_file_content(file_path)
        
        # 从文件名提取元数据
        filename = Path(file_path).stem
        metadata = self._parse_manual_metadata(filename)
        metadata["source"] = filename
        metadata["file_path"] = file_path
        
        # 文本分块
        chunks = self.text_splitter.create_documents([text_content], [metadata])
        
        # 为每个块添加单独的元数据
        for i, chunk in enumerate(chunks):
            chunk.metadata["doc_id"] = f"{filename}_{i}"
            chunk.metadata["chunk_index"] = i
        
        return chunks
    
    def _read_file_content(self, file_path: str) -> str:
        """读取文件内容（支持常见文本格式）"""
        # 目前只支持txt和md，后续可以扩展PDF和DOCX支持
        ext = Path(file_path).suffix.lower()
        
        if ext in ['.txt', '.md']:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    return f.read()
            except UnicodeDecodeError:
                with open(file_path, 'r', encoding='gbk') as f:
                    return f.read()
        
        # TODO: 添加PDF和DOCX支持
        elif ext == '.pdf':
            raise ValueError("PDF文件处理功能尚未实现")
        elif ext == '.docx':
            raise ValueError("DOCX文件处理功能尚未实现")
        else:
            raise ValueError(f"不支持的文件格式: {ext}")
    
    def _parse_policy_metadata(self, filename: str) -> Dict[str, Any]:
        """从政策文件名称解析元数据"""
        metadata = {
            "source_type": "policy",
            "region": "全国",  # 默认值
            "effective_date": "2024-01-01",
            "product_line": "通用"
        }
        
        # 简单的文件名解析逻辑
        parts = filename.lower().split('_')
        
        if len(parts) >= 3:
            if 'warranty' in filename:
                metadata["policy_type"] = "warranty"
            elif 'refund' in filename:
                metadata["policy_type"] = "refund"
            elif 'service' in filename:
                metadata["policy_type"] = "service"
            
            for part in parts:
                if part in ['北京', '上海', '广州', '深圳']:
                    metadata["region"] = part
                    break
            
            if '家用电器' in filename:
                metadata["product_line"] = "家用电器"
            elif '消费电子' in filename:
                metadata["product_line"] = "消费电子"
        
        return metadata
    
    def _parse_manual_metadata(self, filename: str) -> Dict[str, Any]:
        """从说明书文件名称解析元数据"""
        metadata = {
            "source_type": "manual",
            "product_model": "通用",
            "issue_type": "general"
        }
        
        # 简单的文件名解析逻辑
        parts = filename.lower().split('_')
        
        if len(parts) >= 2:
            # 假设第一个部分是产品型号
            metadata["product_model"] = parts[0].upper()
            
            if 'operation' in filename or '使用' in filename:
                metadata["issue_type"] = "operation"
            elif 'troubleshooting' in filename or '故障' in filename:
                metadata["issue_type"] = "troubleshooting"
            elif 'maintenance' in filename or '维护' in filename:
                metadata["issue_type"] = "maintenance"
        
        return metadata
def create_sample_policy_documents() -> List[Document]:
    """创建示例政策文档（在没有实际文件时使用）"""
    logger.info("创建示例政策文档")
    
    sample_policies = [
        Document(
            page_content="产品保修政策：家用电器产品自购买之日起12个月内享受免费保修服务，人为损坏除外。需要携带正规购买凭证和产品序列号到指定维修点进行维修服务。",
            metadata={
                "source": "warranty_policy_home_appliances_v1.2",
                "source_type": "policy",
                "policy_type": "warranty",
                "product_line": "家用电器",
                "region": "全国",
                "effective_date": "2024-01-01",
                "section": "3.2.1"
            }
        ),
        Document(
            page_content="退货退款政策：产品在未使用、包装和附件完好的情况下，自收货之日起7天内可无理由退货。退货时需提供购买凭证，退款将在审核通过后7个工作日内原路返还。",
            metadata={
                "source": "refund_policy_general_v1.3", 
                "source_type": "policy",
                "policy_type": "refund",
                "product_line": "通用",
                "region": "全国",
                "effective_date": "2024-03-01",
                "section": "5.1.1"
            }
        ),
    ]
    
    return sample_policies


def create_sample_manual_documents() -> List[Document]:
    """创建示例说明书文档（在没有实际文件时使用）"""
    logger.info("创建示例说明书文档")
    
    sample_manuals = [
        Document(
            page_content="洗衣机WX-1000使用说明：首次使用前请先运行一次空桶清洗程序，去除内桶中的杂质。洗涤程序选择：1.标准洗(60分钟)，2.快速洗(30分钟)，3.强力洗(90分钟)。使用注意事项：请勿超过最大洗涤量8kg，避免使用过多洗衣粉。",
            metadata={
                "source": "WX-1000_operation_manual_v2.1",
                "source_type": "manual",
                "product_model": "WX-1000",
                "issue_type": "operation",
                "section": "3.2",
                "page": 15
            }
        ),
        Document(
            page_content="洗衣机常见故障排查：问题1：洗衣机不启动。解决方法：检查电源插座是否通电，电源线是否连接牢固。问题2：洗衣机漏水。解决方法：检查排水管是否连接正确，检查门密封圈是否损坏。问题3：洗涤效果不佳。解决方法：检查是否放入过多衣物，确认洗涤程序选择正确。",
            metadata={
                "source": "WX-1000_troubleshooting_v2.1",
                "source_type": "manual",
                "product_model": "WX-1000",
                "issue_type": "troubleshooting",
                "section": "5.3",
                "page": 27
            }
        ),
    ]
    
    return sample_manuals


def main():
    """文档向量化主流程"""
    try:
        processor = DocumentProcessor()
        
        print("=== 开始文档向量化处理 ===")
        
        # 1. 检查是否有实际文件
        import glob
        policy_files = glob.glob("raw_data/policy_docs/*.*")
        manual_files = glob.glob("raw_data/manual_docs/*.*")
        
        # 2. 处理政策文档
        print(f"\n1. 处理政策文档...")
        if policy_files:
            print(f"   找到 {len(policy_files)} 个政策文件")
            policy_ids = processor.process_policy_documents()
            count = len(policy_ids) if policy_ids else 0
            print(f"   成功向量化 {count} 个政策文档块")
        else:
            print("   警告：未找到政策文档文件，使用示例数据")
            sample_policies = create_sample_policy_documents()
            if sample_policies:
                ids = processor.vector_store.add_policy_documents(sample_policies)
                print(f"   成功添加 {len(ids)} 个示例政策文档")
            else:
                print("   错误：无法创建示例政策文档")
        
        # 3. 处理说明书文档
        print(f"\n2. 处理说明书文档...")
        if manual_files:
            print(f"   找到 {len(manual_files)} 个说明书文件")
            manual_ids = processor.process_manual_documents()
            count = len(manual_ids) if manual_ids else 0
            print(f"   成功向量化 {count} 个说明书文档块")
        else:
            print("   警告：未找到说明书文档文件，使用示例数据")
            sample_manuals = create_sample_manual_documents()
            if sample_manuals:
                ids = processor.vector_store.add_manual_documents(sample_manuals)
                print(f"   成功添加 {len(ids)} 个示例说明书文档")
            else:
                print("   错误：无法创建示例说明书文档")
        
        print(f"\n✅ 文档向量化处理完成")
        
    except ImportError as e:
        print(f"错误：缺少依赖库 - {e}")
    except ConnectionError as e:
        print(f"错误：无法连接到向量数据库 - {e}")
    except Exception as e:
        print(f"错误：处理过程异常 - {e}")


if __name__ == "__main__":
    main()