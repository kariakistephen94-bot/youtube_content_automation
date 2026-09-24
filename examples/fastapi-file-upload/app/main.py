from fastapi import FastAPI

from app.routes import uploads

app = FastAPI(title="File Upload API")
app.include_router(uploads.router)


@app.get("/")
def health() -> dict[str, str]:
    return {"status": "ok"}
