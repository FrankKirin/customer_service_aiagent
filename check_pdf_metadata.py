#!/usr/bin/env python3
"""
查看PDF文件的元数据信息
"""

from pypdf import PdfReader
import sys
from pathlib import Path

def show_pdf_metadata(pdf_path: str):
    """显示PDF文件的元数据信息"""
    try:
        reader = PdfReader(pdf_path)
    except Exception as e:
        print(f"错误: 无法读取PDF文件 '{pdf_path}': {e}")
        return
    
    pdf_file = Path(pdf_path)
    print(f"=== PDF文件: {pdf_file.name} ===")
    print(f"文件大小: {pdf_file.stat().st_size:,} 字节")
    print(f"页数: {len(reader.pages)}")
    print(f"是否加密: {reader.is_encrypted}")
    
    print("\n--- PDF元数据 ---")
    if reader.metadata:
        for key, value in reader.metadata.items():
            # 移除前导斜杠
            key_name = key.lstrip('/')
            print(f"  {key_name}: {value}")
        
        # 解析常见字段
        print("\n--- 元数据解析 ---")
        metadata = reader.metadata
        
        # 标题
        title = metadata.get('/Title')
        if title:
            print(f"○ 标题: {title}")
        
        # 作者
        author = metadata.get('/Author')
        if author:
            print(f"○ 作者: {author}")
        
        # 创建者
        creator = metadata.get('/Creator')
        if creator:
            print(f"○ 创建者: {creator}")
        
        # 生成者
        producer = metadata.get('/Producer')
        if producer:
            print(f"○ 生成软件: {producer}")
        
        # 创建日期和修改日期
        for date_key, label in [('/CreationDate', '创建日期'), ('/ModDate', '修改日期')]:
            date_value = metadata.get(date_key)
            if date_value:
                print(f"○ {label}: {date_value}")
                
    else:
        print("  无元数据")
    
    print("\n--- 文档处理器如何读取 ---")
    print("在 document_processor.py 中, 代码读取PDF元数据的方式:")
    print("""
    def _read_pdf(file_path: str) -> Tuple[Dict[str, Any], List[Tuple[int, str]]]:
        reader = PdfReader(file_path)
        title = (reader.metadata or {}).get("/Title")
        info: Dict[str, Any] = {"pdf_title": str(title)} if title else {}
        # ... 其余代码
    """)
    
    if reader.metadata and reader.metadata.get('/Title'):
        print(f"\n当前项目提取的元数据: {{'pdf_title': '{reader.metadata.get('/Title')}'}}")
    else:
        print("\n当前项目提取的元数据: {} (空字典, 因为没有标题)")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        pdf_path = sys.argv[1]
    else:
        # 使用项目中的示例PDF
        pdf_path = "./raw_data/manual_docs/家用冰箱通用说明书_N.30-2-31.pdf"
    
    if not Path(pdf_path).exists():
        print(f"错误: 文件 '{pdf_path}' 不存在")
        sys.exit(1)
    
    show_pdf_metadata(pdf_path)