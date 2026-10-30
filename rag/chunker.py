def chunk_by_chars(text: str, chunk_size: int = 500, overlap: int = 50) -> list[str]:
    """
    将文本按字符数分割成块
    
    Args:
        text: 要分割的文本
        chunk_size: 每块的最大字符数，默认为500
        overlap: 块之间的重叠字符数，默认为50
        
    Returns:
        分割后的文本块列表
        
    Raises:
        ValueError: 当输入文本为空、chunk_size非正、overlap非正、或overlap >= chunk_size时
    """
    # 参数验证
    if not text:
        raise ValueError(f"输入文本不能为空")
    
    if chunk_size <= 0:
        raise ValueError(f"chunk_size 必须为正整数，当前值: {chunk_size}")
    
    if overlap < 0:
        raise ValueError(f"overlap 不能为负数，当前值: {overlap}")
    
    if overlap >= chunk_size:
        raise ValueError(f"overlap 必须小于 chunk_size，当前值: overlap={overlap}, chunk_size={chunk_size}")
    
    # 如果文本长度小于等于chunk_size，直接返回整个文本
    if len(text) <= chunk_size:
        return [text]
    
    chunks = []
    start = 0
    
    while start < len(text):
        # 计算块的结束位置
        end = min(start + chunk_size, len(text))
        
        # 提取当前块
        chunk = text[start:end]
        chunks.append(chunk)
        
        # 如果已经处理完所有文本，结束循环
        if end == len(text):
            break
        
        # 计算下一个块的开始位置，考虑重叠
        # 下一个块从当前块的结束位置减去重叠部分开始
        next_start = end - overlap
        
        # 确保下一个开始位置不超过当前结束位置
        # 并且不回到当前块开始位置之前
        if next_start <= start:
            next_start = start + 1
        
        # 确保不会无限循环
        if next_start >= end:
            next_start = end
        
        start = next_start
    
    return chunks