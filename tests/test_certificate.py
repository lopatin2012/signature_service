"""Интеграционные тесты работы с сертификатом.

Проверяют реального подписанта (`WindowsSigner` / `LinuxSigner`) через общий
интерфейс `BaseSigner`. Для выполнения требуется установленный криптопровайдер
(CAdESCOM на Windows / pycades + КриптоПро CSP на Linux) и хотя бы один валидный
сертификат с приватным ключом в хранилище.

Запуск (только с маркером `cert`, иначе весь модуль пропускается):
    pytest tests/test_certificate.py -m cert
"""

import base64

import pytest

from signers import get_signer

pytestmark = pytest.mark.cert


@pytest.fixture(scope="module")
def _cert_gate(pytestconfig):
    """Пропускает модуль, если маркер `cert` не был явно выбран через `-m cert`."""
    try:
        # Активные маркеры из командной строки доступны как атрибут конфигурации.
        markers = getattr(pytestconfig, "option", None)
        markexpr = markers.markexpr if markers else ""
        if markexpr and "cert" in markexpr:
            yield
            return
    except Exception:
        pass
    pytest.skip("Интеграционные тесты сертификата. Запуск: pytest tests/test_certificate.py -m cert")


@pytest.fixture(scope="module")
def signer(_cert_gate):
    """Реальный signer для текущей ОС (Windows или Linux)."""
    return get_signer()


@pytest.fixture(scope="module")
def serial_number(signer):
    """Первый валидный сертификат из хранилища, либо skip, если его нет."""
    certs = signer.get_list_certificates()
    if not certs:
        pytest.skip("В хранилище нет валидных сертификатов")
    return certs[0]["serial_number"]


@pytest.fixture(scope="module")
def sample_data():
    return "данные для проверки подписи 12345"


def test_certificate_listing_returns_expected_fields(signer):
    certs = signer.get_list_certificates()
    assert isinstance(certs, list)
    for c in certs:
        for field in ("serial_number", "fio", "inn", "valid_from", "valid_for", "valid_days"):
            assert field in c, f"Поле {field} отсутствует в описании сертификата"
        assert isinstance(c["serial_number"], str) and c["serial_number"]
        assert isinstance(c["valid_days"], int)


def test_attached_signature_is_valid_base64(signer, serial_number, sample_data):
    signed = signer.attached_signed_data(sample_data, serial_number)
    assert isinstance(signed, str) and signed
    assert "\r" not in signed and "\n" not in signed
    base64.b64decode(signed, validate=True)


def test_unpinned_signature_is_valid_base64(signer, serial_number, sample_data):
    signed = signer.unpinned_signed_data(sample_data, serial_number)
    assert isinstance(signed, str) and signed
    assert "\r" not in signed and "\n" not in signed
    base64.b64decode(signed, validate=True)


def test_signature_differs_between_inputs(signer, serial_number):
    s1 = signer.attached_signed_data("текст номер один", serial_number)
    s2 = signer.attached_signed_data("текст номер два", serial_number)
    assert s1 != s2


def test_unknown_serial_raises_value_error(signer, sample_data):
    with pytest.raises(ValueError):
        signer.attached_signed_data(sample_data, "NONEXISTENT_SERIAL_000")
