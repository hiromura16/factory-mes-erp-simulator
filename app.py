"""ASGI起動用のモジュール。import時に保存領域と実行履歴を初期化する。"""

from factorylib import create_app
import database

app = create_app("sql", "汎用SQL データ投入", database)
