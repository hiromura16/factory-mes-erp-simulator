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
curl -fsS --retry 12 --retry-connrefused --retry-delay 1 --max-time 5 http://127.0.0.1:3560/healthz
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

管理画面と操作APIは既定では認証を要求しません。認証付きで使う手順は[管理画面の認証](#管理画面の認証)にあります。ホスト側で `127.0.0.1` にバインドしても、共有ネットワークの参加コンテナは管理画面と操作APIに到達できます。信頼済みの収集・試験サービス専用のネットワークを使います。

既定のプロジェクト内ネットワークも残ります。共有ネットワーク上では専用の別名 `factory-sql-data-loader` とコンテナ側のポートを使います。別ComposeのSQLサーバーに書く場合、UIの「接続先」には共有ネットワーク上のSQLサービス名とコンテナ側ポートを設定します。SQLiteを使う場合は外部ネットワークは不要です。

## 管理画面の認証

管理HTTPをホスト外から使う場合は、TLSを終端するリバースプロキシのSSO・認証機能、またはアプリのBasic認証を設定します。Basic認証を別の端末から使う場合は、HTTPSかSSHトンネルで接続します。

1. 管理パスワードを1行で保存したファイルを用意します。コンテナUID 1000で読み取れるように所有者と権限を設定します。
2. VMの `.env` に `ADMIN_USERNAME` と、パスワードファイルの絶対パスを `ADMIN_PASSWORD_FILE` に設定します。
3. 通常設定に認証用Composeを加えて起動します。

```sh
docker compose -p factory-mes-erp-simulator -f compose.yaml -f compose.admin-auth.yaml up -d --build
```

共有ネットワークやNFSを使用する場合は、そのオーバーライドも同じコマンドに加えます。管理画面・ヘルプ・操作API・接続ファイルのアップロードは同じ認証で保護されます。`/healthz` は稼働確認用です。公開データ取得APIを持つアプリの取得用認証は、公開プロファイルで設定します。

## リソースの設定

`APP_MEMORY` はコンテナが使用するメモリの上限（既定512MiB）です。`MIN_AVAILABLE_GIB` はホストに確保する空きメモリ量（新規既定1GiB）で、しきい値を下回ると生成を停止します。以前の `.env` に `MIN_AVAILABLE_GIB=6` を記載している場合は、その値が優先されます。

`TZ` でコンテナの設置先のタイムゾーンを指定できます（既定Asia/Tokyo）。合成した日時は入力のタイムゾーンを解釈しUTCに変換します。

## 停止・更新

画面で発生器またはジョブを停止し、停止・完了を確認してからコンテナを止めます。

```sh
docker compose -p factory-mes-erp-simulator stop
docker compose -p factory-mes-erp-simulator up -d --build
```

共有ネットワーク、NFS、管理Basic認証を設定した場合は、更新時も起動時と同じ `-f` と環境変数を指定します。設定DB、暗号鍵、永続データをバックアップし、ボリューム `app-data` とホストマウントを引き継ぎます。更新後も停止状態または実行履歴を確認します。

HTTP(S)プロキシが必要なビルドでは `.env.example` の大文字・小文字の対を同じ値に設定します。実行時の通信はネットワーク、DNS、ポート、認証を個別に確認します。
