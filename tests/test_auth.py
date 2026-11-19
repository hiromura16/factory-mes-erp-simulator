# モジュール読解: DB認証引数の組み立てと、接続ファイルの非公開性を合成入力で確認する。
import base64
import struct

import pytest
from fastapi.testclient import TestClient
from types import SimpleNamespace
from pydantic import ValidationError

from config import ConnectionProfile
from db_connections import odbc_parameters, engine_for
from factorylib import create_app
from generation import GenerationError


# 読解 test_windows_and_kerberos_are_different_authentication_paths: 明示Windows資格情報とKerberos統合認証が別経路として扱われ、ODBC値の区切りが保持されることを確認する。
def test_windows_and_kerberos_are_different_authentication_paths():
    p = ConnectionProfile(
        id="sql",
        name="SQL",
        kind="sqlserver",
        host="sql.example",
        database="test",
        database_auth="kerberos",
        ssl_mode="verify",
    )
    text, kwargs = odbc_parameters(p)
    assert (
        "Trusted_Connection={yes}" in text and "PWD=" not in text and "UID=" not in text
    )
    assert "TrustServerCertificate={no}" in text
    p.database_auth = "password"
    p.username = "some;name"
    p.password = "not-a-secret"
    from pydantic import SecretStr

    p.password = SecretStr("safe};Trusted_Connection=yes")
    text, kwargs = odbc_parameters(p)
    assert "UID={some;name}" in text and "safe}};Trusted_Connection=yes}" in text


# 読解 test_sql_access_token_uses_attribute_not_password: SQL Server用アクセストークンを専用のバイナリ属性に渡し、通常の接続文字列に入れないことを確認する。
def test_sql_access_token_uses_attribute_not_password():
    p = ConnectionProfile(
        id="sql",
        name="SQL",
        kind="sqlserver",
        host="sql.example",
        database="test",
        database_auth="access_token",
        db_access_token="test-token",
    )
    text, kwargs = odbc_parameters(p)
    encoded = "test-token".encode("utf-16-le")
    assert kwargs["attrs_before"][1256] == struct.pack("<I", len(encoded)) + encoded
    assert (
        "PWD=" not in text and "UID=" not in text and "Trusted_Connection" not in text
    )


# 読解 test_database_auth_transport_combinations_are_checked: 認証と接続方式の不適合がモデルまたはドライバー初期化で拒否されることを確かめる。
def test_database_auth_transport_combinations_are_checked():
    with pytest.raises(ValidationError):
        ConnectionProfile(
            id="bad",
            name="bad",
            kind="oracle",
            host="db",
            service_name="service",
            database_auth="windows_password",
        )
    with pytest.raises(ValidationError):
        ConnectionProfile(
            id="bad", name="bad", kind="mysql", connection_method="dsn", dsn="test"
        )
    p = ConnectionProfile(
        id="ora",
        name="ora",
        kind="oracle",
        connection_method="dsn",
        dsn="WALLET_ALIAS",
        database_auth="external",
    )
    with pytest.raises(GenerationError, match="Instant Client"):
        engine_for(p, None)


# 読解 test_connection_file_upload_is_bounded_private_and_not_readable: 接続ファイルを制限付きの非公開ファイルとして保存し、APIで内容を取得できないことを確認する。
def test_connection_file_upload_is_bounded_private_and_not_readable(tmp_path):
    app = create_app(
        "sql",
        "Test",
        # コールバック: 任意の呼出引数を受ける無処理スタブ。実アダプターを起動せず、APIや履歴の振舞いだけを試験する。
        # コールバック: DBへの疎通を行わず空の結果を返すスタブ。補助ファイルAPIの試験を接続先から独立させる。
        SimpleNamespace(handle=lambda *a: None, test_connection=lambda *a: {}),
    )
    with TestClient(app) as c:
        secret = "private-file-value"
        r = c.post(
            "/api/connection-files",
            json={
                "name": "oracle/tnsnames.ora",
                "content_base64": base64.b64encode(secret.encode()).decode(),
            },
        )
        assert r.status_code == 200 and secret not in r.text
        target = tmp_path / "connection-files/oracle/tnsnames.ora"
        assert target.read_text() == secret and target.stat().st_mode & 0o777 == 0o600
        assert (
            c.post(
                "/api/connection-files",
                json={"name": "../../bad", "content_base64": ""},
            ).status_code
            == 422
        )
        assert "content" not in c.get("/api/connection-files").text
        assert c.get("/api/connection-files/oracle/tnsnames.ora").status_code == 404
