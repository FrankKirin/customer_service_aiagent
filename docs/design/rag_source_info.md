### SourceInfo字段
    source_id: str
    source_type: Literal["policy", "manual"] = "policy"  # source类型
    section: Optional[str] = None  # 哪一章节
    page: Optional[int] = None  # 哪一页
    url: Optional[str] = None  # 哪个url
    relevance_score: float = Field(default=0.0, ge=0.0, le=1.0)  # 相关分