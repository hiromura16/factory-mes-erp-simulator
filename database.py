"""SQLAlchemy Core adapters for SQL Server, Oracle, PostgreSQL, MySQL and SQLite."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.dialects import oracle, mssql

from generation import GenerationError, batch_limit, generate_record, inject_fault


def password(profile):
    return profile.password.get_secret_value() if profile.password else ""


def sqlite_path(profile):
    root = Path(
        os.environ.get(
            "SQLITE_ROOT", str(Path(os.environ.get("DATA_DIR", "./data")) / "databases")
        )
    ).resolve()
    root.mkdir(parents=True, exist_ok=True)
    candidate = Path(profile.sqlite_path)
    path = (candidate if candidate.is_absolute() else root / candidate).resolve()
    if not path.is_relative_to(root) or path == root:
        raise GenerationError("SQLiteの保存先は許可されたdatabases領域内にしてください")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def engine_for(profile):
    from db_connections import engine_for as connect_engine

    return connect_engine(profile, sqlite_path)


def column_type(field):
    if field.data_type == "string":
        return sa.Unicode(field.length)
    if field.data_type == "integer":
        return sa.BigInteger()
    if field.data_type == "number":
        return sa.Float().with_variant(oracle.BINARY_DOUBLE(), "oracle")
    if field.data_type == "decimal":
        return sa.Numeric(field.precision, field.scale)
    if field.data_type == "boolean":
        return sa.Boolean()
    if field.data_type == "datetime":
        return sa.DateTime().with_variant(mssql.DATETIME2(), "mssql")
    return sa.UnicodeText()


def table_definition(spec):
    return sa.Table(
        spec.sql.table,
        sa.MetaData(),
        *[
            sa.Column(
                f.name,
                column_type(f),
                primary_key=f.primary_key,
                nullable=not f.primary_key,
                autoincrement=False,
            )
            for f in spec.fields
        ],
        schema=spec.sql.schema_name or None,
        implicit_returning=False,
    )


def test_connection(profile):
    engine = engine_for(profile)
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql(
                "SELECT 1 FROM DUAL" if profile.kind == "oracle" else "SELECT 1"
            )
            inspector = sa.inspect(conn)
            tables = inspector.get_table_names()[:200]
            return {"status": "connected", "kind": profile.kind, "tables": tables}
    finally:
        engine.dispose()


def prepare_table(profile, spec):
    engine = engine_for(profile)
    try:
        with engine.begin() as conn:
            exists = sa.inspect(conn).has_table(
                spec.sql.table, schema=spec.sql.schema_name or None
            )
            if not exists:
                table_definition(spec).create(conn, checkfirst=True)
            table = sa.Table(
                spec.sql.table,
                sa.MetaData(),
                schema=spec.sql.schema_name or None,
                autoload_with=conn,
            )
            return {
                "status": "exists" if exists else "created",
                "table": spec.sql.table,
                "columns": [
                    {"name": c.name, "type": str(c.type), "primary_key": c.primary_key}
                    for c in table.columns
                ],
            }
    finally:
        engine.dispose()


def inspect_table(profile, spec):
    engine = engine_for(profile)
    try:
        with engine.connect() as conn:
            table = sa.Table(
                spec.sql.table,
                sa.MetaData(),
                schema=spec.sql.schema_name or None,
                autoload_with=conn,
            )
            return {
                "table": spec.sql.table,
                "columns": [
                    {
                        "name": c.name,
                        "type": str(c.type),
                        "nullable": c.nullable,
                        "primary_key": c.primary_key,
                    }
                    for c in table.columns
                ],
            }
    finally:
        engine.dispose()


def ddl(profile, spec):
    engine = engine_for(profile)
    try:
        return {
            "ddl": str(
                sa.schema.CreateTable(table_definition(spec)).compile(
                    dialect=engine.dialect
                )
            ),
            "executed": False,
        }
    finally:
        engine.dispose()


def convert_rows(spec, rows, mapping, table=None, dialect=None):
    output = []
    for row in rows:
        result = {}
        for field in spec.fields:
            value = row[field.name]
            if value is not None:
                if field.data_type == "datetime":
                    at = datetime.fromisoformat(value).astimezone(timezone.utc)
                    value = at.replace(tzinfo=None)
                    if dialect == "sqlserver" and table is not None:
                        target = table.c[mapping[field.name]].type
                        if isinstance(target, mssql.DATETIMEOFFSET):
                            value = at.isoformat()
                        elif isinstance(target, sa.DateTime) and not isinstance(
                            target, mssql.DATETIME2
                        ):
                            value = value.isoformat(timespec="milliseconds")
                elif field.data_type == "decimal":
                    value = Decimal(value)
                elif field.data_type == "json":
                    if table is None or not isinstance(
                        table.c[mapping[field.name]].type, sa.JSON
                    ):
                        value = json.dumps(value, ensure_ascii=False, allow_nan=False)
            result[mapping[field.name]] = value
        output.append(result)
    return output


def existing_keys(conn, table, keys, rows):
    found = set()
    chunk_size = max(1, min(500, 1500 // len(keys)))
    for offset in range(0, len(rows), chunk_size):
        group = rows[offset : offset + chunk_size]
        condition = (
            table.c[keys[0]].in_([r[keys[0]] for r in group])
            if len(keys) == 1
            else sa.or_(*[sa.and_(*[table.c[k] == r[k] for k in keys]) for r in group])
        )
        found.update(
            tuple(r)
            for r in conn.execute(
                sa.select(*[table.c[k] for k in keys]).where(condition)
            )
        )
    return found


def handle(spec, ctx, profile, store):
    engine = engine_for(profile)
    try:
        with engine.connect() as conn:
            table = sa.Table(
                spec.sql.table,
                sa.MetaData(),
                schema=spec.sql.schema_name or None,
                autoload_with=conn,
            )
            conn.rollback()
            mapping = {}
            for field in spec.fields:
                exact = [c.name for c in table.columns if c.name == field.name]
                candidates = exact or [
                    c.name
                    for c in table.columns
                    if c.name.casefold() == field.name.casefold()
                ]
                if len(candidates) != 1:
                    raise GenerationError("生成項目と接続先の列名を一致させてください")
                mapping[field.name] = candidates[0]
            keys = [mapping[f.name] for f in spec.fields if f.primary_key]
            if spec.sql.operation != "insert" and (
                not keys or set(keys) != {c.name for c in table.primary_key.columns}
            ):
                raise GenerationError(
                    "更新・取消・upsertには接続先と一致する主キー定義が必要です"
                )
            step = batch_limit(spec, spec.batch_size)
            for offset in range(0, spec.rows, step):
                ctx.check()
                inject_fault(spec, ctx, "before_batch", offset // step + 1)
                count = min(step, spec.rows - offset)
                raw = [
                    generate_record(spec, spec.start_sequence + offset + i)
                    for i in range(count)
                ]
                rows = convert_rows(spec, raw, mapping, table, profile.kind)
                inserted, updated, deleted = 0, 0, 0
                with conn.begin():
                    if spec.sql.operation == "insert":
                        new = rows
                        old = []
                    else:
                        present = existing_keys(conn, table, keys, rows)
                        old = [r for r in rows if tuple(r[k] for k in keys) in present]
                        new = [
                            r for r in rows if tuple(r[k] for k in keys) not in present
                        ]
                        if new and spec.sql.operation == "update":
                            raise GenerationError("更新対象の主キーが見つかりません")
                    if spec.sql.operation in {"insert", "upsert"} and new:
                        if profile.kind == "sqlserver":
                            page = max(1, min(1000, 2000 // len(mapping)))
                            for i in range(0, len(new), page):
                                conn.execute(sa.insert(table).values(new[i : i + page]))
                        else:
                            conn.execute(sa.insert(table), new)
                        inserted = len(new)
                    if spec.sql.operation in {"update", "upsert"} and old:
                        values = {
                            name: sa.bindparam("v_" + name)
                            for name in mapping.values()
                            if name not in keys
                        }
                        if values:
                            stmt = (
                                sa.update(table)
                                .where(
                                    sa.and_(
                                        *[
                                            table.c[k] == sa.bindparam("k_" + k)
                                            for k in keys
                                        ]
                                    )
                                )
                                .values(values)
                            )
                            conn.execute(
                                stmt,
                                [
                                    {
                                        **{"k_" + k: r[k] for k in keys},
                                        **{"v_" + k: r[k] for k in values},
                                    }
                                    for r in old
                                ],
                            )
                        updated = len(old)
                    if spec.sql.operation == "delete" and old:
                        stmt = sa.delete(table).where(
                            sa.and_(
                                *[table.c[k] == sa.bindparam("k_" + k) for k in keys]
                            )
                        )
                        conn.execute(
                            stmt, [{"k_" + k: r[k] for k in keys} for r in old]
                        )
                        deleted = len(old)
                    inject_fault(spec, ctx, "after_write", offset // step + 1)
                ctx.progress(
                    processed=count,
                    inserted=inserted,
                    updated=updated,
                    deleted=deleted,
                    commits=1,
                )
                if offset + count < spec.rows:
                    ctx.pace(offset + count)
    finally:
        engine.dispose()
