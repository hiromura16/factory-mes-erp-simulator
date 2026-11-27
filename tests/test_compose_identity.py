"""Compose名の変更がUIの契約と隔離SQLiteの操作を壊さないことを確認する。"""

import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path

from fastapi.testclient import TestClient

import database
from factorylib import create_app


ROOT = Path(__file__).resolve().parents[1]
PROJECT = "factory-sql-data-loader"


def test_compose_uses_canonical_default_and_no_operational_legacy_names():
    """ファイル名から決まる既定名や、旧名を指定する起動例に戻さない。"""
    info = json.loads((ROOT / "compose.yaml").read_text(encoding="utf-8"))
    assert info["name"] == PROJECT
    assert info["services"]["app"]["image"] == f"{PROJECT}:local"
    assert "container_name" not in info["services"]["app"]
    assert info["services"]["app"]["ports"] == [
        "${BIND_IP:-127.0.0.1}:${APP_PORT:-3560}:8080"
    ]
    old_root = "mes" + "-erp" + "-simulator"
    old_name = re.compile(
        r"factory[\-_]mes[\-_]erp[\-_]simulator|" + re.escape(old_root)
    )
    for filename in (
        "README.md",
        "compose.yaml",
        "compose.admin-auth.yaml",
        "compose.lakehouse-network.yaml",
        "static/app.js",
    ):
        text = (ROOT / filename).read_text(encoding="utf-8")
        assert not old_name.search(text), filename
    for filename in ("docs/vm-compose.md", "docs/help-site/install.html"):
        text = (ROOT / filename).read_text(encoding="utf-8")
        # 旧配備の識別に必要な説明だけを残し、起動例からは除く。
        assert len(old_name.findall(text)) == 1, filename
        (line,) = [line for line in text.splitlines() if old_name.search(line)]
        assert "旧配備で使われていた" in line and "docker compose" not in line
        assert f"{PROJECT}-app-1" in text
        assert f"{PROJECT}_app-data" in text
        assert f"{PROJECT}_default" in text


def test_sql_api_metadata_preview_and_preset_persist_in_disposable_data(tmp_path):
    """ローカルの合成DBと実APIで、接続・見本・表情報・履歴を再確認する。"""
    databases = tmp_path / "databases"
    databases.mkdir()
    db_path = databases / "compose-contract.sqlite3"
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            "CREATE TABLE synthetic_parts (id INTEGER PRIMARY KEY, label TEXT)"
        )
        conn.execute("INSERT INTO synthetic_parts VALUES (1, 'fixture')")
        conn.commit()
    profile = {
        "id": "compose-contract",
        "name": "隔離SQL検証",
        "kind": "sqlite",
        "sqlite_path": db_path.name,
    }
    with TestClient(create_app("sql", "合成Compose契約", database)) as client:
        assert client.get("/healthz").status_code == 200
        info = client.get("/api/info").json()
        assert info["kind"] == "sql"
        assert client.post("/api/connections", json=profile).status_code == 200
        spec = info["defaults"]
        spec["connection_id"] = profile["id"]
        spec["sql"]["table"] = "synthetic_parts"
        checked = client.post("/api/preview", json=spec)
        assert checked.status_code == 200, checked.text
        preview = checked.json()
        assert preview["records"] and preview["writes"] is False
        columns = client.post("/api/table/inspect", json=spec)
        assert columns.status_code == 200, columns.text
        assert [
            (entry["name"], entry["primary_key"]) for entry in columns.json()["columns"]
        ] == [("id", True), ("label", False)]
        assert (
            client.post("/api/presets/compose-contract", json=spec).status_code == 200
        )
        assert client.get("/api/runs").json()["runs"] == []
    with TestClient(create_app("sql", "合成Compose契約", database)) as client:
        profiles = client.get("/api/connections").json()["connections"]
        assert any(item["id"] == profile["id"] for item in profiles)
        spec_reloaded = client.get("/api/presets/compose-contract/export").json()
        assert spec_reloaded == spec
        assert (
            client.post("/api/preview", json=spec_reloaded).json()["records"]
            == preview["records"]
        )
        assert client.get("/api/runs").json()["runs"] == []
    with closing(sqlite3.connect(db_path)) as conn:
        assert conn.execute("SELECT id, label FROM synthetic_parts").fetchall() == [
            (1, "fixture")
        ]
