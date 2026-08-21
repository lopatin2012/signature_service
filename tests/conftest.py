import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "cert: интеграционные тесты сертификата (требуют криптопровайдер и валидный сертификат в хранилище)",
    )
