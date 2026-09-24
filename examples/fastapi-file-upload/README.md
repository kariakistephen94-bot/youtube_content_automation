# FastAPI File Upload API

A small API that accepts PDF and image uploads, validates them, and stores them on disk.

## Run

```bash
uv sync
uv run fastapi dev app/main.py
```

Open http://127.0.0.1:8000/docs to try the upload endpoint.

## Test

```bash
uv run pytest
```

## Configuration

| Variable | Default | Description |
| --- | --- | --- |
| `UPLOAD_DIR` | `uploads` | Where uploaded files are stored |
| `MAX_UPLOAD_MB` | `5` | Maximum upload size in megabytes |
