#!/usr/bin/env python3
"""Composeの既定名と接続契約を検証する。実起動はGitHub提供runner限定。"""

import argparse
import json
import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener


ROOT = Path(__file__).resolve().parents[1]
PROJECT = "factory-sql-data-loader"
CONTAINER = f"{PROJECT}-app-1"
VOLUME = f"{PROJECT}_app-data"
NETWORK = f"{PROJECT}_default"
IMAGE = f"{PROJECT}:local"
OVERRIDES = ("compose.lakehouse-network.yaml", "compose.admin-auth.yaml")
ORIGIN = "http://127.0.0.1:3560"
PRESET = "/api/presets/compose-ci-synthetic"
HTTP = build_opener(ProxyHandler({}))
# シェルの環境や既存の配備ファイルから名前・接続・Docker接続先を継承しない。
ENV = {key: os.environ[key] for key in ("PATH", "HOME") if key in os.environ}


def call(*args):
    """既定のローカルDocker接続だけを使い、各呼出しに期限を設ける。"""
    return subprocess.check_output(args, cwd=ROOT, env=ENV, text=True, timeout=60)


def compose(env_file, *files):
    """配備の.envやプロジェクト名の上書きを参照せず構成を組み立てる。"""
    return (
        "docker",
        "compose",
        "--env-file",
        str(env_file),
        "-f",
        "compose.yaml",
        *[value for path in files for value in ("-f", path)],
    )


def check_config(directory):
    """既定・各上書き・併用の四構成で名前、ポート、分離を確認する。"""
    env_file = directory / "site.env"
    # ここで使うパスはconfigの展開だけが目的。認証設定は実起動しない。
    env_file.write_text(
        "LAKEHOUSE_NETWORK=compose-ci-fictional-network\n"
        "ADMIN_USERNAME=compose-ci\n"
        f"ADMIN_PASSWORD_FILE={directory / 'config-only-password'}\n",
        encoding="utf-8",
    )
    for files in ((), (OVERRIDES[0],), (OVERRIDES[1],), OVERRIDES):
        config = json.loads(
            call(*compose(env_file, *files), "config", "--format", "json")
        )
        assert config["name"] == PROJECT, files
        assert config["volumes"]["app-data"]["name"] == VOLUME, files
        assert config["networks"]["default"]["name"] == NETWORK, files
        app = config["services"]["app"]
        assert app["image"] == IMAGE and "container_name" not in app, files
        assert app["read_only"] and "ALL" in app["cap_drop"], files
        (port,) = app["ports"]
        assert (port["host_ip"], str(port["published"]), port["target"]) == (
            "127.0.0.1",
            "3560",
            8080,
        ), files
        assert app["environment"]["APP_PORT"] == "3560", files
        if OVERRIDES[0] in files:
            shared = config["networks"]["lakehouse"]
            assert (
                shared["external"] and shared["name"] == "compose-ci-fictional-network"
            )
            assert PROJECT in app["networks"]["lakehouse"]["aliases"], files
        if OVERRIDES[1] in files:
            assert app["environment"]["ADMIN_PASSWORD_FILE"] == (
                "/run/secrets/factory-admin-password"
            ), files
            assert any(
                mount["target"] == "/run/secrets/factory-admin-password"
                and mount["read_only"]
                for mount in app["volumes"]
            ), files
        print("Compose config OK:", ", ".join(files) or "base")

    with env_file.open("a", encoding="utf-8") as config_env:
        config_env.write("APP_PORT=53560\n")
    custom = json.loads(call(*compose(env_file), "config", "--format", "json"))[
        "services"
    ]["app"]
    (port,) = custom["ports"]
    assert str(port["published"]) == "53560" and port["target"] == 8080
    assert custom["environment"]["APP_PORT"] == "53560"
    print("Compose config OK: custom APP_PORT with container port 8080")


def project_objects():
    """Composeラベルを基準に今回の候補リソースを列挙する。"""
    selector = f"label=com.docker.compose.project={PROJECT}"
    return (
        call("docker", "ps", "-aq", "--filter", selector).split(),
        call("docker", "volume", "ls", "-q", "--filter", selector).split(),
        call("docker", "network", "ls", "-q", "--filter", selector).split(),
    )


def require_empty():
    """名称またはラベルが競合するDocker環境では操作を中止する。"""
    if (
        any(project_objects())
        or CONTAINER in call("docker", "ps", "-a", "--format", "{{.Names}}").split()
        or VOLUME in call("docker", "volume", "ls", "-q").split()
        or NETWORK in call("docker", "network", "ls", "--format", "{{.Name}}").split()
    ):
        raise SystemExit("Existing Compose resources; refusing to operate.")


def read_http(path, payload=None):
    """localhostの合成専用インスタンスへJSONを送受信する。"""
    request = Request(
        ORIGIN + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json", "Origin": ORIGIN},
    )
    with HTTP.open(request, timeout=3) as response:
        return json.load(response)


def wait_ready():
    """起動待ちを有界にし、待機後に同じアプリ種別を確認する。"""
    deadline = time.monotonic() + 45
    while True:
        try:
            read_http("/healthz")
            assert read_http("/api/info")["kind"] == "sql"
            return
        except HTTPError:
            # HTTP応答まで到達したエラーは通信の起動競合と区別する。
            raise
        except (URLError, TimeoutError, ConnectionResetError):
            # Dockerのポート公開が先行すると、接続拒否・リセット・EOFがあり得る。
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.5)


def check_runtime_names():
    """コンテナの実名、マウント、ネットワーク、イメージを照合する。"""
    (app,) = json.loads(call("docker", "container", "inspect", CONTAINER))
    assert app["Name"] == f"/{CONTAINER}"
    assert app["Config"]["Image"] == IMAGE
    assert app["Config"]["Labels"]["com.docker.compose.project"] == PROJECT
    assert [m["Name"] for m in app["Mounts"] if m["Destination"] == "/data"] == [VOLUME]
    assert set(app["NetworkSettings"]["Networks"]) == {NETWORK}
    assert read_http("/api/runs")["runs"] == []


def stop_synthetic(base):
    """直前に空確認したプロジェクトのCIリソースだけを削除する。"""
    containers, volumes, networks = project_objects()
    for identity in containers:
        (app,) = json.loads(call("docker", "container", "inspect", identity))
        if app["Name"] != f"/{CONTAINER}":
            raise SystemExit("Unexpected Compose container; no cleanup performed.")
    if set(volumes) - {VOLUME}:
        raise SystemExit("Unexpected Compose volume; no cleanup performed.")
    for identity in networks:
        (network,) = json.loads(call("docker", "network", "inspect", identity))
        if network["Name"] != NETWORK:
            raise SystemExit("Unexpected Compose network; no cleanup performed.")
    call(*base, "down", "--volumes", "--timeout", "10")


def print_runtime_diagnostics(base):
    """削除前に合成専用サービスの状態と直近ログだけを表示する。"""
    for args in (
        ("ps", "--all"),
        ("logs", "--no-color", "--tail", "40", "app"),
    ):
        try:
            print(call(*base, *args), flush=True)
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"Compose diagnostics unavailable ({args[0]}): {exc}", flush=True)


def smoke(directory):
    """分離されたCIに限定し、空の起動・再起動・検証専用リセットを試す。"""
    if (
        os.getenv("GITHUB_ACTIONS") != "true"
        or os.getenv("RUNNER_ENVIRONMENT") != "github-hosted"
        or os.getenv("DOCKER_HOST")
        or os.getenv("DOCKER_CONTEXT")
        or call("docker", "context", "show").strip() != "default"
    ):
        raise SystemExit("Runtime smoke is limited to the GitHub-hosted local daemon.")
    require_empty()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 3560))
    env_file = directory / "smoke.env"
    env_file.write_text("SQL_HOST=\nSQL_PASSWORD_FILE=/dev/null\n", encoding="utf-8")
    base = compose(env_file)
    if call("docker", "image", "ls", "-q", IMAGE).strip():
        raise SystemExit("Existing canonical image; refusing to replace it.")
    call("docker", "image", "tag", "factory-source:test", IMAGE)
    try:
        call(*base, "up", "--detach", "--no-build", "--pull", "never")
        wait_ready()
        check_runtime_names()
        info = read_http("/api/info")
        assert read_http("/api/connections")["connections"]
        existing = read_http("/api/presets")["presets"]
        assert existing
        try:
            read_http(PRESET + "/export")
        except HTTPError as exc:
            assert exc.code == 404, exc
        else:
            raise AssertionError("Synthetic preset already present in fresh volume")
        spec = info["defaults"]
        first = read_http("/api/preview", spec)
        assert first["records"] and first["writes"] is False
        read_http(PRESET, spec)
        call(*base, "restart", "--timeout", "10", "app")
        wait_ready()
        check_runtime_names()
        assert read_http(PRESET + "/export") == spec, "Preset lost on restart"
        stop_synthetic(base)
        require_empty()
        call(*base, "up", "--detach", "--no-build", "--pull", "never")
        wait_ready()
        check_runtime_names()
        try:
            read_http(PRESET + "/export")
        except HTTPError as exc:
            assert exc.code == 404, exc
        else:
            raise AssertionError("Preset unexpectedly persisted after isolated reset")
        print("Compose runtime OK: exact names, health, preview, persistence, reset")
    except Exception:
        print_runtime_diagnostics(base)
        raise
    finally:
        stop_synthetic(base)
        require_empty()


def main():
    """通常は読み取り専用。CIで明示した場合だけ実起動する。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="factory-compose-check-") as temp:
        directory = Path(temp)
        check_config(directory)
        if args.smoke:
            smoke(directory)


if __name__ == "__main__":
    main()
