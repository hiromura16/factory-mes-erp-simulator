# モジュール読解: 接続プロファイルをDB方言固有のドライバー設定へ変換する。ここでは表の変更を行わない。
"""Database-specific authentication/transport registry. Secrets never enter engine URLs."""

from __future__ import annotations

import os
import struct

import sqlalchemy as sa
from sqlalchemy.engine import URL, make_url
from sqlalchemy.pool import NullPool

from generation import GenerationError
from connection_security import connection_file, tls_context

AUTH_METHODS = {
    "sqlserver": [
        "password",
        "windows_password",
        "kerberos",
        "entra_service_principal",
        "entra_managed_identity",
        "access_token",
    ],
    "oracle": ["password", "external", "access_token"],
    "postgresql": [
        "password",
        "kerberos",
        "client_certificate",
        "external",
        "access_token",
    ],
    "mysql": ["password", "client_certificate", "external"],
    "mariadb": ["password", "client_certificate", "external"],
    "sqlite": ["password"],
}
CONNECTION_METHODS = {
    "sqlserver": ["host", "dsn", "raw"],
    "oracle": ["host", "dsn", "raw"],
    "postgresql": ["host", "service", "unix_socket", "raw"],
    "mysql": ["host", "unix_socket", "raw"],
    "mariadb": ["host", "unix_socket", "raw"],
    "sqlite": ["host"],
}
DRIVERS = {
    "sqlserver": ["auto", "pymssql", "pyodbc"],
    "oracle": ["auto", "oracledb_thin", "oracledb_thick"],
    "postgresql": ["auto", "psycopg"],
    "mysql": ["auto", "pymysql"],
    "mariadb": ["auto", "pymysql"],
    "sqlite": ["auto"],
}
OPTIONS = {
    "sqlserver": {"appname", "tds_version"},
    "oracle": {
        "retry_count",
        "retry_delay",
        "expire_time",
        "stmtcachesize",
        "disable_oob",
    },
    "postgresql": {
        "application_name",
        "options",
        "client_encoding",
        "target_session_attrs",
        "gssencmode",
        "krbsrvname",
        "passfile",
    },
    "mysql": {"charset", "sql_mode", "init_command", "program_name"},
    "mariadb": {"charset", "sql_mode", "init_command", "program_name"},
}


# 読解 secret: ドライバーへ渡すため指定フィールドの秘密値を取り出す。未設定は空文字で表す。
def secret(p, name):
    value = getattr(p, name)
    return value.get_secret_value() if value else ""


# 読解 odbc_value: ODBCの値を波括弧で囲み、閉じ括弧を二重化して設定項目の区切りと混同させない。
def odbc_value(value):
    return "{" + str(value).replace("}", "}}") + "}"


# 読解 odbc_parameters: 認証と接続方式に応じたODBC文字列・接続引数を作る。アクセストークンはパスワード欄でなく専用属性へ渡す。
def odbc_parameters(p):
    if p.connection_method == "raw":
        if not p.connection_string:
            raise GenerationError("暗号化保存する接続文字列を指定してください")
        text = secret(p, "connection_string")
    else:
        values = (
            {"DSN": p.dsn}
            if p.connection_method == "dsn"
            else {
                "DRIVER": p.odbc_driver,
                "SERVER": p.host + ("," + str(p.port) if p.port else ""),
                "DATABASE": p.database,
            }
        )
        values.update(
            Encrypt="no" if p.ssl_mode == "off" else "yes",
            TrustServerCertificate="no" if p.ssl_mode == "verify" else "yes",
        )
        if p.database_auth == "kerberos":
            values["Trusted_Connection"] = "yes"
        elif p.database_auth == "entra_managed_identity":
            values["Authentication"] = "ActiveDirectoryMsi"
            if p.username:
                values["UID"] = p.username
        elif p.database_auth != "access_token":
            values.update(UID=p.username, PWD=secret(p, "password"))
            if p.database_auth == "entra_service_principal":
                values["Authentication"] = "ActiveDirectoryServicePrincipal"
            elif p.database_auth == "password":
                values["Authentication"] = "SqlPassword"
            else:
                raise GenerationError(
                    "明示的なWindows資格情報はpymssqlドライバー、統合認証はKerberosを選んでください"
                )
        if "appname" in p.driver_options:
            values["APP"] = p.driver_options["appname"]
        text = ";".join(k + "=" + odbc_value(v) for k, v in values.items())
    kwargs = {"timeout": p.timeout_seconds}
    if p.database_auth == "access_token":
        if not p.db_access_token:
            raise GenerationError("アクセストークンが必要です")
        if p.connection_method == "raw":
            raise GenerationError("トークン認証ではhostまたはDSN方式を使用してください")
        token = secret(p, "db_access_token").encode("utf-16-le")
        kwargs["attrs_before"] = {1256: struct.pack("<I", len(token)) + token}
    return text, kwargs


# 読解 engine_for: DBごとの許可ドライバーと追加設定を検証し、接続プールを再利用しないSQLAlchemyエンジンを作る。Oracle Thick初期化はプロセス全体に影響する。
def engine_for(p, sqlite_path):
    if p.driver not in DRIVERS[p.kind]:
        raise GenerationError("選択したDBに対応するドライバーを選んでください")
    if set(p.driver_options) - OPTIONS.get(p.kind, set()):
        raise GenerationError(
            "未対応の追加ドライバー設定です。接続方式にrawを選ぶか、対応項目を確認してください"
        )
    common = {"poolclass": NullPool, "hide_parameters": True}
    if p.kind == "sqlite":
        return sa.create_engine(
            URL.create("sqlite", database=str(sqlite_path(p))),
            connect_args={"timeout": p.timeout_seconds},
            **common,
        )
    if p.kind == "sqlserver":
        odbc = p.driver == "pyodbc" or (
            p.driver == "auto"
            and (
                p.database_auth not in {"password", "windows_password"}
                or p.connection_method != "host"
                or p.ssl_mode == "verify"
            )
        )
        if odbc:
            import pyodbc

            text, kwargs = odbc_parameters(p)
            if (
                p.connection_method != "raw"
                and p.connection_method != "dsn"
                and p.odbc_driver not in pyodbc.drivers()
            ):
                raise GenerationError(
                    "指定したODBCドライバーが未導入です。SQL用DockerイメージにはMicrosoft ODBC Driver 18を同梱しています"
                )

            # 読解 engine_for.connect: SQLAlchemyから要求された時にODBC接続を作り、作成後のクエリタイムアウトも設定する。
            def connect():
                c = pyodbc.connect(text, **kwargs)
                c.timeout = p.timeout_seconds
                return c

            return sa.create_engine("mssql+pyodbc://", creator=connect, **common)
        if (
            p.database_auth not in {"password", "windows_password"}
            or p.connection_method != "host"
        ):
            raise GenerationError("この認証・接続方式にはpyodbcを選択してください")
        if p.ssl_mode == "verify":
            raise GenerationError(
                "証明書を検証する場合はpyodbcドライバーを選択してください"
            )
        import pymssql

        user = (
            (p.domain + "\\" + p.username)
            if p.database_auth == "windows_password" and p.domain
            else p.username
        )
        args = {
            "server": p.host,
            "port": p.port or 1433,
            "user": user,
            "password": secret(p, "password"),
            "database": p.database,
            "login_timeout": p.timeout_seconds,
            "timeout": p.timeout_seconds,
            "charset": "UTF-8",
            "encryption": "off" if p.ssl_mode == "off" else "require",
            "use_datetime2": True,
            **p.driver_options,
        }
        return sa.create_engine(
            # コールバック: SQLAlchemyが接続を必要とした時だけ、構築済み引数でSQL Server接続を作る。
            "mssql+pymssql://", creator=lambda: pymssql.connect(**args), **common
        )
    if p.kind == "oracle":
        import oracledb

        mode = os.environ.get("ORACLE_CLIENT_MODE", "thin")
        if mode == "thick" and oracledb.is_thin_mode():
            oracledb.init_oracle_client(
                lib_dir=os.environ.get("ORACLE_CLIENT_LIB_DIR") or None
            )
        if (
            p.driver == "oracledb_thick" or p.database_auth == "external"
        ) and mode != "thick":
            raise GenerationError(
                "Oracle外部認証・Thick接続にはInstant Clientを用意し、ORACLE_CLIENT_MODE=thickでアプリを起動してください"
            )
        if p.driver == "oracledb_thin" and mode == "thick":
            raise GenerationError(
                "このプロセスはOracle Thickモードです。autoまたはoracledb_thickを選んでください"
            )
        if p.connection_method == "raw":
            if not p.connection_string:
                raise GenerationError("Oracle DSN接続記述子が必要です")
            dsn = secret(p, "connection_string")
        elif p.connection_method == "dsn":
            dsn = p.dsn
        else:
            dsn = oracledb.makedsn(
                p.host,
                p.port or 1521,
                sid=p.sid or None,
                service_name=p.service_name or None,
            )
            if p.ssl_mode != "off":
                dsn = dsn.replace("(PROTOCOL=TCP)", "(PROTOCOL=TCPS)")
        args = {
            "dsn": dsn,
            "tcp_connect_timeout": p.timeout_seconds,
            "ssl_server_dn_match": p.ssl_mode == "verify",
            **p.driver_options,
        }
        if p.database_auth == "external":
            args["externalauth"] = True
        elif p.database_auth == "access_token":
            if not p.db_access_token:
                raise GenerationError("OracleのOAuthアクセストークンが必要です")
            args["access_token"] = secret(p, "db_access_token")
        else:
            args.update(user=p.username, password=secret(p, "password"))
        if p.config_dir:
            args["config_dir"] = connection_file(p.config_dir, directory=True)
        if p.wallet_location:
            args.update(
                wallet_location=connection_file(p.wallet_location, directory=True),
                wallet_password=secret(p, "wallet_password") or None,
            )
        if p.tls_ca_file:
            args["ssl_context"] = tls_context(p)
        return sa.create_engine(
            # コールバック: SQLAlchemyの接続要求時に構築済み引数でOracle接続を作る。
            "oracle+oracledb://", creator=lambda: oracledb.connect(**args), **common
        )
    if p.kind == "postgresql":
        import psycopg

        args = {
            "connect_timeout": p.timeout_seconds,
            "sslmode": {
                "off": "disable",
                "require": "require",
                "verify": "verify-full",
            }[p.ssl_mode],
            **p.driver_options,
        }
        if "passfile" in args:
            args["passfile"] = connection_file(str(args["passfile"]))
        conninfo = ""
        if p.connection_method == "raw":
            if not p.connection_string:
                raise GenerationError("libpq接続文字列が必要です")
            conninfo = secret(p, "connection_string")
        elif p.connection_method == "service":
            args["service"] = p.dsn
        else:
            args.update(
                host=connection_file(p.unix_socket, directory=True)
                if p.connection_method == "unix_socket"
                else p.host,
                port=p.port or 5432,
                dbname=p.database,
            )
        if p.username:
            args["user"] = p.username
        if p.database_auth == "password" and (
            p.connection_method != "raw" or p.password
        ):
            args["password"] = secret(p, "password")
        elif p.database_auth == "access_token":
            if not p.db_access_token:
                raise GenerationError("DBアクセストークンが必要です")
            args["password"] = secret(p, "db_access_token")
        elif p.database_auth == "kerberos":
            args.setdefault("gssencmode", "prefer")
            args.setdefault("krbsrvname", "postgres")
        for key, name in [
            ("sslrootcert", "tls_ca_file"),
            ("sslcert", "tls_cert_file"),
            ("sslkey", "tls_key_file"),
        ]:
            if getattr(p, name):
                args[key] = connection_file(getattr(p, name))
        if p.tls_key_password:
            args["sslpassword"] = secret(p, "tls_key_password")
        if p.database_auth == "client_certificate" and not (
            p.tls_cert_file and p.tls_key_file and p.ssl_mode != "off"
        ):
            raise GenerationError(
                "証明書認証にはTLSとクライアント証明書・秘密鍵が必要です"
            )
        return sa.create_engine(
            "postgresql+psycopg://",
            # コールバック: SQLAlchemyの接続要求時に検査済みconninfoと引数でPostgreSQLへ接続する。
            creator=lambda: psycopg.connect(conninfo, **args),
            **common,
        )
    import pymysql

    args = {
        "host": p.host,
        "port": p.port or 3306,
        "user": p.username,
        "database": p.database,
        "connect_timeout": p.timeout_seconds,
        "read_timeout": p.timeout_seconds,
        "write_timeout": p.timeout_seconds,
        "charset": "utf8mb4",
        **p.driver_options,
    }
    if p.connection_method == "raw":
        if not p.connection_string:
            raise GenerationError("MySQL/MariaDB接続URIが必要です")
        uri = make_url(secret(p, "connection_string"))
        if uri.get_backend_name() not in {"mysql", "mariadb"}:
            raise GenerationError("mysql://またはmariadb:// URIを指定してください")
        args.update(
            host=uri.host or "localhost",
            port=uri.port or 3306,
            user=uri.username or "",
            password=uri.password or "",
            database=uri.database or "",
        )
        if set(uri.query) - {"charset"}:
            raise GenerationError(
                "URIの追加設定はcharsetのみ対応。その他は専用欄を使用してください"
            )
        args.update(uri.query)
    elif p.database_auth == "password" and (p.connection_method != "raw" or p.password):
        args["password"] = secret(p, "password")
    if p.connection_method == "unix_socket":
        args["unix_socket"] = connection_file(p.unix_socket)
    if p.ssl_mode != "off":
        args["ssl"] = tls_context(p)
    if p.database_auth == "client_certificate" and not (
        p.tls_cert_file and p.tls_key_file and p.ssl_mode != "off"
    ):
        raise GenerationError("証明書認証にはTLSとクライアント証明書・秘密鍵が必要です")
    return sa.create_engine(
        # コールバック: SQLAlchemyの接続要求時に構築済み引数でMySQL互換接続を作る。
        "mysql+pymysql://", creator=lambda: pymysql.connect(**args), **common
    )
