from pathlib import Path
import json
import os

import pytest
import sqlalchemy as sa

import database as db
from config import ConnectionProfile, FieldRule, RunSpec
from generation import GenerationError
from conftest import Context


def test_sqlite_custom_schema_crud_and_decimal_json_roundtrip():
    profile = ConnectionProfile(
        id="local",
        name="local",
        kind="sqlite",
        sqlite_path="tests.sqlite3",
        ssl_mode="off",
    )
    spec = RunSpec(
        rows=11,
        batch_size=4,
        target_rate=0,
        fields=[
            FieldRule(name="id", generator="record_id", primary_key=True, length=64),
            FieldRule(name="text_value", generator="constant", value="日本語の品目"),
            FieldRule(
                name="amount",
                data_type="decimal",
                generator="constant",
                value="12.345",
                scale=3,
            ),
            FieldRule(
                name="valid", data_type="boolean", generator="boolean", probability=1
            ),
            FieldRule(
                name="details",
                data_type="json",
                generator="constant",
                value={"nested": [1, 2]},
            ),
        ],
    )
    assert "CREATE TABLE" in db.ddl(profile, spec)["ddl"]
    assert db.prepare_table(profile, spec)["status"] == "created"
    assert db.prepare_table(profile, spec)["status"] == "exists"
    ctx = Context()
    db.handle(spec, ctx, profile, None)
    assert ctx.stats["inserted"] == 11
    ctx = Context()
    db.handle(spec, ctx, profile, None)
    assert ctx.stats["updated"] == 11 and ctx.stats["inserted"] == 0
    engine = db.engine_for(profile)
    with engine.connect() as conn:
        row = (
            conn.exec_driver_sql("SELECT * FROM sample_records LIMIT 1")
            .mappings()
            .one()
        )
        assert row["text_value"] == "日本語の品目" and json.loads(row["details"]) == {
            "nested": [1, 2]
        }
    engine.dispose()
    spec.sql.operation = "delete"
    ctx = Context()
    db.handle(spec, ctx, profile, None)
    assert ctx.stats["deleted"] == 11
    spec.sql.operation = "update"
    with pytest.raises(GenerationError):
        db.handle(spec, Context(), profile, None)
    assert len(db.inspect_table(profile, spec)["columns"]) == 5


def test_path_is_constrained():
    p = ConnectionProfile(
        id="bad", name="bad", kind="sqlite", sqlite_path="../outside.db"
    )
    with pytest.raises(GenerationError):
        db.engine_for(p)


@pytest.mark.skipif(
    not os.environ.get("SQL_TEST_PROFILES_FILE"),
    reason="External driver fixtures opt-in",
)
def test_external_database_driver_contracts():
    profiles = json.loads(Path(os.environ["SQL_TEST_PROFILES_FILE"]).read_text())
    for values in profiles:
        profile = ConnectionProfile.model_validate(values)
        spec = RunSpec(
            run_key="adapter-check-" + profile.kind,
            rows=17,
            batch_size=7,
            target_rate=0,
        )
        spec.sql.table = "adapter_validation_v2"
        try:
            assert db.test_connection(profile)["status"] == "connected"
            db.prepare_table(profile, spec)
            ctx = Context()
            db.handle(spec, ctx, profile, None)
            assert ctx.stats["inserted"] == 17, (profile.kind, ctx.stats)
            spec.fields[5].mean = 75
            ctx = Context()
            db.handle(spec, ctx, profile, None)
            assert ctx.stats["updated"] == 17, (profile.kind, ctx.stats)
            engine = db.engine_for(profile)
            with engine.connect() as conn:
                table = sa.Table(spec.sql.table, sa.MetaData(), autoload_with=conn)
                assert conn.scalar(sa.select(sa.func.count()).select_from(table)) == 17
            engine.dispose()
            spec.sql.operation = "delete"
            ctx = Context()
            db.handle(spec, ctx, profile, None)
            assert ctx.stats["deleted"] == 17
        finally:
            engine = db.engine_for(profile)
            try:
                with engine.begin() as conn:
                    db.table_definition(spec).drop(conn, checkfirst=True)
            finally:
                engine.dispose()


def test_existing_sql_datetime_and_native_json_keep_expected_types():
    from sqlalchemy.dialects import mssql, postgresql
    from generation import generate_record

    fields = [
        FieldRule(
            name="at",
            data_type="datetime",
            generator="timestamp",
            start="2026-11-13T00:00:00+00:00",
        ),
        FieldRule(
            name="details",
            data_type="json",
            generator="constant",
            value={"nested": [1, 2]},
        ),
    ]
    spec = RunSpec(fields=fields)
    table = sa.Table(
        "records",
        sa.MetaData(),
        sa.Column("at", mssql.DATETIME()),
        sa.Column("details", postgresql.JSONB()),
    )
    result = db.convert_rows(
        spec,
        [generate_record(spec, 0)],
        {"at": "at", "details": "details"},
        table,
        "sqlserver",
    )[0]
    assert result["at"] == "2026-11-13T00:00:00.000" and result["details"] == {
        "nested": [1, 2]
    }


def test_injected_failure_before_commit_rolls_back_whole_batch():
    from config import FaultOptions
    from factorylib import Runtime

    profile = ConnectionProfile(
        id="local", name="local", kind="sqlite", sqlite_path="rollback.sqlite3"
    )
    spec = RunSpec(
        rows=12,
        batch_size=4,
        target_rate=0,
        faults=FaultOptions(enabled=True, stage="after_write", action="fail"),
    )
    db.prepare_table(profile, spec)
    runtime = Runtime("sql", lambda s, c: db.handle(s, c, profile, None), 100000000)
    job = runtime.start(spec)
    runtime.thread.join(timeout=5)
    result = runtime.get(job["id"])
    assert result["status"] == "failed" and result["stats"]["faults_injected"] == 1
    engine = db.engine_for(profile)
    with engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT count(*) FROM sample_records").scalar() == 0
    engine.dispose()
