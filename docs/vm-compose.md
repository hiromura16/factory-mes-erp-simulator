# Linux VMでDocker Composeから起動する

Docker Engine、Compose v2以降、Python 3を利用します。VMにこのリポジトリを取得し、そのディレクトリで操作します。

新規導入では、VMで次を実行します。

```sh
git clone https://github.com/hiromura16/factory-sql-data-loader.git
cd factory-sql-data-loader
```


## 1. 起動設定を作る

新規のチェックアウトで以下を実行します。既存配備を更新する場合は現在の `.env` とマウントを引き継ぎます。

```sh
python3 scripts/init_local.py --private-dir ../factory-private/factory-sql-data-loader --data-dir ./data --env-file .env
```

- Webの既定は `127.0.0.1:3560` です。重複する場合は `.env` の `APP_PORT` を空きポートへ変更します。
- 別端末から管理する場合はVMへSSHポート転送します。信頼済みネットワークに公開する場合は `BIND_IP` とアクセス元の制限をVMで設定します。

手元のPCからSSHポート転送するときの例です。`user@vm-address` をVMの接続先に置き換え、SSHセッションを開いたまま `http://127.0.0.1:3560` を開きます。

```sh
ssh -N -L 3560:127.0.0.1:3560 user@vm-address
```

- 初回はSQLiteで試せます。`SQL_HOST` でSQL Serverを初期登録する場合は、`SQL_PASSWORD_FILE` にUID 1000が読み取れる非公開ファイルを指定します。

`docker compose ls` で既存のプロジェクト名を確認し、引き継ぐ配備では同じ名前を `-p` で指定します。ボリュームはプロジェクト名で識別されます。 この手順では従来の既定名 `factory-mes-erp-simulator` を明示して、リポジトリ名変更後も既存のデータを参照します。配備で別の名前を使っている場合はそちらに置き換えます。

## 2. ビルド・起動する

```sh
docker compose -p factory-mes-erp-simulator config --quiet
docker compose -p factory-mes-erp-simulator up -d --build
docker compose -p factory-mes-erp-simulator ps
curl -fsS http://127.0.0.1:3560/healthz
curl -fsS http://127.0.0.1:3560/api/runs
```

`/healthz` はWebの応答確認です。実行履歴に新しいジョブが追加されないことを確認します。 Web画面を開き、[アプリ固有のマニュアル](user-manual.md)の手順で明示的に開始します。カスタムポートの場合はURLも合わせます。

## 3. 別のComposeプロジェクトとつなぐ

VMの到達可能なインターフェースに公開したホストポートで接続できます。相手のコンテナから見た `127.0.0.1` は相手自身です。

同じVMで非公開のDockerネットワークを共有する場合は、管理者が用意したネットワークに必要なLakehouse側サービスも参加させます。以下の名前は例です。

```sh
docker network inspect lakehouse-input
export LAKEHOUSE_NETWORK=lakehouse-input
docker compose -p factory-mes-erp-simulator -f compose.yaml -f compose.lakehouse-network.yaml config --quiet
docker compose -p factory-mes-erp-simulator -f compose.yaml -f compose.lakehouse-network.yaml up -d --build
```

既定のプロジェクト内ネットワークも残ります。共有ネットワーク上では専用の別名 `factory-sql-data-loader` とコンテナ側のポートを使います。別ComposeのSQLサーバーに書く場合、UIの「接続先」には共有ネットワーク上のSQLサービス名とコンテナ側ポートを設定します。SQLiteを使う場合は外部ネットワークは不要です。

## 停止・更新

画面で発生器またはジョブを停止し、停止・完了を確認してからコンテナを止めます。

```sh
docker compose -p factory-mes-erp-simulator stop
docker compose -p factory-mes-erp-simulator up -d --build
```

共有ネットワークを使う場合は更新にも同じ `-f` と環境変数を使います。設定DB、暗号鍵、永続データをバックアップし、ボリューム `app-data` とホストマウントを引き継ぎます。更新後も停止状態または実行履歴を確認します。

HTTP(S)プロキシが必要なビルドでは `.env.example` の大文字・小文字の対を同じ値に設定します。実行時の通信はネットワーク、DNS、ポート、認証を個別に確認します。
