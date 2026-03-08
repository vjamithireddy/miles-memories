from fastapi import FastAPI

from app.settings import get_app_port

app = FastAPI(title="MilesMemories API", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=get_app_port(), reload=True)
