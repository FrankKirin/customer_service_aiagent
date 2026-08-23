"""
PDF
 ↓
Document Loader
 ↓
Chunk
 ↓
Embedding
 ↓
Vector DB

"""
from langchain_community.document_loaders import PyMuPDFLoader
from langchain_core.documents import Document
from pathlib import Path

def load_pdf(file_path: str) -> list[Document]


def load_document(file_path: str) -> list[Document]:
    suffix = Path(file_path).suffix.lower()

    if suffix == "pdf":
        return load_pdf(file_path)

    if suffix == "txt":
        return load_txt(file_path)

    if suffix == "pptx":
        return load_pdf(file_path)