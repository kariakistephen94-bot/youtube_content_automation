from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_upload_pdf(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.files.UPLOAD_DIR", tmp_path)
    response = client.post(
        "/uploads", files={"file": ("doc.pdf", b"%PDF-1.4 test", "application/pdf")}
    )
    assert response.status_code == 201
    body = response.json()
    assert body["content_type"] == "application/pdf"
    assert (tmp_path / body["stored_as"]).exists()


def test_rejects_unsupported_type():
    response = client.post("/uploads", files={"file": ("notes.txt", b"hello", "text/plain")})
    assert response.status_code == 400
