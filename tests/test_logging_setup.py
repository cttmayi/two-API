import logging

from src.logging_setup import setup_logging


def test_setup_logging_attaches_handlers_to_uvicorn_loggers(tmp_path):
    log_path = setup_logging(str(tmp_path))

    uvicorn_error = logging.getLogger("uvicorn.error")
    uvicorn_access = logging.getLogger("uvicorn.access")

    assert uvicorn_error.handlers
    assert uvicorn_access.handlers
    assert not uvicorn_error.propagate
    assert not uvicorn_access.propagate

    uvicorn_error.warning("Invalid HTTP request received.")
    uvicorn_access.info('127.0.0.1:12345 - "GET / HTTP/1.1" 200 OK')

    with open(log_path) as f:
        content = f.read()

    assert "Invalid HTTP request received." in content
    assert "GET / HTTP/1.1" in content
