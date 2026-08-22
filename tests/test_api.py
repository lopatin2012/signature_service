"""Тесты HTTP-эндпоинтов FastAPI.

В фикстуре `client` реальный singleton `main.signer` подменяется на фейк, чтобы
проверять маршрутизацию, маппинг ошибок и формат ответов без криптопровайдера.
"""

import base64
import pytest
from fastapi.testclient import TestClient

import main


@pytest.fixture()
def client(monkeypatch):
    """TestClient с подменённым (фейковым) signer'ом."""
    from signers.base import SigningResult

    class FakeSigner:
        def __init__(self):
            self.attached_calls = 0
            self.unpinned_calls = 0

        def get_list_certificates(self):
            return [
                {
                    "serial_number": "AB12",
                    "fio": "Иванов Иван",
                    "inn": "123456789012",
                    "valid_from": "01-01-2025",
                    "valid_for": "01-01-2030",
                    "valid_days": 365,
                }
            ]

        def attached_signed_data(self, data, serial_number):
            self.attached_calls += 1
            self.last_result = SigningResult(success=True)
            return ("c2lnbmVkLWF0dGFjaGVk", self.last_result)

        def unpinned_signed_data(self, data, serial_number):
            self.unpinned_calls += 1
            self.last_result = SigningResult(success=True)
            return ("c2lnbmVkLXVucGlubmVk", self.last_result)

    fake = FakeSigner()
    monkeypatch.setattr(main, "signer", fake)
    with TestClient(main.app) as c:
        c._fake = fake
        yield c


def _sign_body(data="данные для подписания", serial_number="AB12CD34"):
    return {"data": data, "serial_number": serial_number}


def test_root_returns_service_info(client):
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["service"] == "Signature Service"
    assert set(body["endpoints"]) == {
        "sign_attached",
        "sign_unpinned",
        "list_certificates",
        "docs",
        "redoc",
        "openapi_schema",
    }


def test_list_certificates(client):
    resp = client.get("/api/certificates/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert body["certificates"][0]["serial_number"] == "AB12"


def test_sign_attached_success(client):
    resp = client.post("/api/sign/attached/", json=_sign_body())
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert base64.b64decode(body["signed_data"], validate=True)
    assert client._fake.attached_calls == 1


def test_sign_unpinned_success(client):
    resp = client.post("/api/sign/unpinned/", json=_sign_body())
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert base64.b64decode(body["signed_data"], validate=True)
    assert client._fake.unpinned_calls == 1


def test_sign_attached_invalid_serial_returns_404(client):
    def _boom(data, serial_number):
        raise ValueError(f"Сертификат с серийным номером {serial_number} не найден")

    client._fake.attached_signed_data = _boom
    resp = client.post("/api/sign/attached/", json=_sign_body(serial_number="ZZZZ1234"))
    assert resp.status_code == 404
    assert "не найден" in resp.json()["detail"]


def test_sign_internal_error_returns_500(client):
    def _boom(data, serial_number):
        raise RuntimeError("сбой криптопровайдера")

    client._fake.attached_signed_data = _boom
    resp = client.post("/api/sign/attached/", json=_sign_body())
    assert resp.status_code == 500


@pytest.mark.parametrize("path", ["/api/sign/attached/", "/api/sign/unpinned/"])
def test_sign_requires_serial_number(client, path):
    resp = client.post(path, json={"data": "текст"})
    assert resp.status_code == 422


def test_sign_form_data_returns_422_with_json_hint(client):
    """form-data (не JSON) отклоняется с подсказкой про JSON-тело."""
    resp = client.post(
        "/api/sign/attached/",
        data={"data": "текст", "serial_number": "AB12CD34"},
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert any(e.get("type") == "json_body_expected" for e in detail)
