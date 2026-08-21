from fastapi import FastAPI
from api.chat_router import router as chat_router 

app = FastAPI(title="海运AI客服")
app.include_router(chat_router)

@app.get("/")
def root():
    return {"message": "海运客服API已启动，请访问/docs查看文档"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)