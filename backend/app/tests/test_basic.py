import os
import pytest
from fastapi.testclient import TestClient
from app.main import app, SessionLocal, hash_password
from app.models.user import User

@pytest.fixture(autouse=True)
def setup_admin():
    # Убедимся, что админ существует
    db = SessionLocal()
    try:
        if not db.query(User).filter_by(username="admin").first():
            db.add(User(username="admin", password_hash=hash_password(os.getenv("ADMIN_INITIAL_PASSWORD", "admin123")), role="admin", is_active=True))
            db.commit()
    finally:
        db.close()

client = TestClient(app)


def test_info():
    r = client.get("/challenge/info")
    assert r.status_code == 200
    assert "title" in r.json()


def test_register_and_auth_and_submit(monkeypatch):
    # Регистрация через скрытый бот-эндпоинт
    r = client.post("/bot/register", json={"username": "testuser", "password": "secret123"})
    assert r.status_code == 200

    # Получение API-ключа
    r = client.post("/challenge/auth", json={"username": "testuser", "password": "secret123"})
    assert r.status_code == 200
    api_key = r.json()["api_key"]
    assert api_key

    # Получение задания 1
    r = client.get("/challenge/endpoint/1", headers={"X-API-Key": api_key})
    assert r.status_code == 200

    # Неверный ответ
    r = client.post("/challenge/submit", json={"endpoint_id": 1, "value": "wrong"}, headers={"X-API-Key": api_key})
    assert r.status_code == 200
    assert r.json()["ok"] in (True, False)

