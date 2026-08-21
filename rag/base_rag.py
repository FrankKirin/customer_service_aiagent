"""
RAG创建
1.利用PDF读取器载入PDF文件
2.根据设置好的chunk参数对PDF进行分片
3.利用本地模型/Open_API对分片进行向量化
4.存入Chroma数据库
5.根据query以及k值返回相似的向量
6.利用langchain的llm和rag一起返回对应rag

To-Do List
- 家电单型号，拓展到家电多型号
- 实现不同搜索策略的rag
"""
import fitz
from pathlib import Path


def pdf_loader():
    PROJECT_ROOT = Path(__file__).resolve().parent.parent
    file_location = PROJECT_ROOT/
    pass

if __name__ == "__main__":
    pdf_loader():