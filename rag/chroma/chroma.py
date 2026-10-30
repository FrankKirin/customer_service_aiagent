import chromadb

def get_chroma():
    client = chromadb.PersistentClient(path="./chroma_db")
    return client
