"""確認済みの接続版で表準備が呼ばれることを、外部DBなしで確かめる。"""

import time
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

import database
from config import ConnectionProfile, RunSpec
from factorylib import create_app
from test_operation_gate import confirmed_post


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
        result = confirmed_post(client, "/api/table/prepare", json=spec)
        assert result.status_code == 200
        assert calls == [("reviewed_example", "sample_table")]


def test_sql_api_confirmation_protects_real_local_rows():
    """Verify that unconfirmed schema edits and mutations cannot reach SQLite."""
    app = create_app("sql", "Fixture", database)
    profile = ConnectionProfile(id="confirmation", name="Test SQLite", kind="sqlite", sqlite_path="confirmation.db")
    spec = {
        "connection_id": profile.id,
        "run_key": "same-four-keys",
        "rows": 4,
        "target_rate": 0,
        "fields": [
            {"name": "id", "data_type": "string", "generator": "record_id", "primary_key": True},
            {"name": "v", "data_type": "integer", "generator": "sequence"},
        ],
        "sql": {"table": "confirmed_records", "operation": "insert"},
    }

    def wait_for_run(client, response):
        assert response.status_code == 202, response.text
        run_id = response.json()["id"]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status = client.get(f"/api/runs/{run_id}").json()
            if status["status"] not in {"queued", "running"}:
                assert status["status"] == "completed", status
                return
            time.sleep(0.02)
        raise AssertionError(f"Run {run_id} did not complete")

    with patch("factorylib.shutil.disk_usage", return_value=SimpleNamespace(free=3 * 1024**3)), TestClient(app) as client:
        assert client.post("/api/connections", json=profile.model_dump(mode="json")).status_code == 200
        assert client.post("/api/table/prepare", json=spec).status_code == 428
        assert confirmed_post(client, "/api/table/prepare", json=spec).status_code == 200
        wait_for_run(client, client.post("/api/runs", json=spec))

        engine = database.engine_for(profile)
        try:
            with engine.connect() as connection:
                original_rows = connection.exec_driver_sql("SELECT * FROM confirmed_records ORDER BY id").all()
            assert len(original_rows) == 4
            for action in ("update", "upsert", "delete"):
                payload = {**spec, "sql": {**spec["sql"], "operation": action}}
                assert client.post("/api/runs", json=payload).status_code == 428
                with engine.connect() as connection:
                    rows = connection.exec_driver_sql("SELECT * FROM confirmed_records ORDER BY id").all()
                assert rows == original_rows
            wait_for_run(client, confirmed_post(client, "/api/runs", json=payload))
            with engine.connect() as connection:
                assert connection.exec_driver_sql("SELECT count(*) FROM confirmed_records").scalar_one() == 0
        finally:
            engine.dispose()
