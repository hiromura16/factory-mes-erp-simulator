"""Compose起動待機の通信競合と診断の回帰テスト。"""

from http.client import RemoteDisconnected
from unittest.mock import Mock, call
from urllib.error import HTTPError, URLError

import pytest

from scripts import check_compose


def test_readiness_retries_transient_disconnects(monkeypatch):
    """Dockerのポート公開直後のリセットと応答前EOFは再試行する。"""
    read = Mock(
        side_effect=[
            ConnectionResetError("port published before web app"),
            RemoteDisconnected("no response yet"),
            URLError(ConnectionRefusedError()),
            TimeoutError(),
            {"status": "ok"},
            {"kind": "sql"},
        ]
    )
    sleep = Mock()
    monkeypatch.setattr(check_compose, "read_http", read)
    monkeypatch.setattr(check_compose.time, "sleep", sleep)

    check_compose.wait_ready()

    assert read.call_args_list == [call("/healthz")] * 5 + [call("/api/info")]
    assert sleep.call_args_list == [call(0.5)] * 4


def test_readiness_propagates_http_failure(monkeypatch):
    """HTTP 500が返るのは起動競合ではなく、待たずに通知する。"""
    error = HTTPError("http://localhost/healthz", 500, "failure", None, None)
    monkeypatch.setattr(check_compose, "read_http", Mock(side_effect=error))
    sleep = Mock()
    monkeypatch.setattr(check_compose.time, "sleep", sleep)

    with pytest.raises(HTTPError) as exc:
        check_compose.wait_ready()

    assert exc.value is error
    sleep.assert_not_called()


def test_readiness_stops_after_deadline(monkeypatch):
    """通信が戻らない場合は期限後に元のエラーを返す。"""
    error = ConnectionResetError("did not start")
    monkeypatch.setattr(check_compose, "read_http", Mock(side_effect=error))
    monkeypatch.setattr(check_compose.time, "monotonic", Mock(side_effect=[0, 46]))
    sleep = Mock()
    monkeypatch.setattr(check_compose.time, "sleep", sleep)

    with pytest.raises(ConnectionResetError) as exc:
        check_compose.wait_ready()

    assert exc.value is error
    sleep.assert_not_called()
