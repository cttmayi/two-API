import pytest
from src.forwarder import _prepare_headers


class TestPrepareHeaders:
    def test_strips_hop_by_hop_headers(self):
        headers = {
            "host": "original.example.com",
            "transfer-encoding": "chunked",
            "connection": "keep-alive",
            "keep-alive": "timeout=5",
            "content-type": "application/json",
            "accept": "application/json",
        }
        result = _prepare_headers(headers, api_key=None)
        assert "host" not in result
        assert "transfer-encoding" not in result
        assert "connection" not in result
        assert "keep-alive" not in result
        assert result["content-type"] == "application/json"
        assert result["accept"] == "application/json"

    def test_injects_authorization_when_api_key_set(self):
        headers = {"content-type": "application/json"}
        result = _prepare_headers(headers, api_key="sk-test")
        assert result["authorization"] == "Bearer sk-test"

    def test_no_authorization_when_api_key_is_none(self):
        headers = {"content-type": "application/json"}
        result = _prepare_headers(headers, api_key=None)
        assert "authorization" not in result

    def test_no_authorization_when_api_key_is_empty_string(self):
        headers = {"content-type": "application/json"}
        result = _prepare_headers(headers, api_key="")
        assert "authorization" not in result