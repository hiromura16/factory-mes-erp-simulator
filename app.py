from factorylib import create_app
import database

app = create_app("sql", "汎用SQL データ投入", database)
