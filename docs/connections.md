# データベースの接続と認証

| DB | 認証・接続方式 |
| --- | --- |
| SQL Server | SQL Server認証、Windowsドメイン資格情報（NTLM）、Kerberos統合認証、Entraサービスプリンシパル / Managed Identity / 取得済みアクセストークン。ホスト、ODBC DSN、ODBC接続文字列 |
| Oracle | DBユーザー、Wallet TLS、Thickの外部認証、OAuthトークン。サービス名 / SID、TNSエイリアス、接続記述子 |
| PostgreSQL | パスワード、クライアント証明書、Kerberos/GSS、OS/libpq外部設定、取得済みトークン。ホスト、service、Unixソケット、libpq接続文字列 |
| MySQL / MariaDB | パスワード、TLSクライアント証明書、OS/ソケットの外部認証。ホスト、Unixソケット、接続URI |
| SQLite | アプリ内のDBファイル。追加サーバー・DB認証不要 |

SQL ServerのSQL認証とWindows認証は明示的に選択します。WindowsパスワードはpymssqlへDOMAIN\usernameとして渡します。KerberosはMicrosoft ODBC Driver 18でTrusted_Connectionを指定し、有効なチケット・DNS・SPNを利用します。ブラウザー利用者のWindowsログイン情報を自動使用しません。SQL用DockerイメージにはMicrosoft ODBC Driver 18とKerberosライブラリを同梱します。

Kerberos設定はconnection-filesのkrb5.conf / krb5cc、libpq serviceはpg_service.conf、ODBC DSNはodbc.iniへ配置できます。チケットやトークンの取得・更新とドメイン側の構成は外部で行います。Entraの対話式MFAフローは実装していません。

Oracle Thinが既定。Thick外部認証を使う場合はOracle Instant Clientを事前配置し、ORACLE_CLIENT_MODE=thickと必要ならORACLE_CLIENT_LIB_DIRで起動します。Thin/Thickはプロセス全体の設定なので、切替にはアプリの再起動が必要です。Kerberos/GSSや各外部認証は接続先の設定も必要で、実際のドメイン環境での検証は別途行ってください。
