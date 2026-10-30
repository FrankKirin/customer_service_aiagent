import chromadb

# 持久化到硬盘，重启后数据还在
client = chromadb.PersistentClient(path="/.chroma_db")

