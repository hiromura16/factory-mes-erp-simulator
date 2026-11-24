"""確認済みの接続版で表準備が呼ばれることを、外部DBなしで確かめる。"""

from types import SimpleNamespace

from fastapi.testclient import TestClient

from config import RunSpec
from factorylib import create_app


def test_prepare_honors_confirmed_database_revision():
    """別の画面が接続先を更新しても、版指定の表準備は新しい宛先へ移らない。"""
    calls = []

    def prepare_stub(profile, spec):
        """登録された接続・対象で受け付けたことだけを記録する。"""
        calls.append((profile.database, spec.sql.table))
        return {"status": "created"}

    app = create_app("sql", "Fixture", SimpleNamespace(prepare_table=prepare_stub))
    profile = {
        "id": "fixture",
        "name": "合成DB",
        "kind": "sqlserver",
        "host": "db.example.invalid",
        "database": "reviewed_example",
    }
    with TestClient(app) as client:
        initial = client.post("/api/connections", json=profile)
        assert initial.status_code == 200
        revision = initial.json()["revision"]
        spec = RunSpec(
            connection_id="fixture",
            connection_revision=revision,
            sql={"schema_name": "synthetic", "table": "sample_table"},
        ).model_dump(mode="json")
        updated = client.post(
            "/api/connections", json={**profile, "database": "new_example"}
        )
        assert updated.status_code == 200
        assert updated.json()["revision"] == revision + 1
        result = client.post("/api/table/prepare", json=spec)
        assert result.status_code == 200
        assert calls == [("reviewed_example", "sample_table")]
