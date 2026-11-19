# モジュール読解: 非公開のプロファイルJSONとプリセットを別々に読み、明示指定した表の作成だけを実施する。既存CLI説明と値を出さないエラー表示は維持する。
"""Prepare a generic table using an explicit private connection JSON and preset.

Example: uv run python scripts/bootstrap.py --connection /private/profile.json --preset presets/01-quickstart.json
The caller must supply a DB user with CREATE TABLE permission. No database, account,
or existing schema is adopted, changed, or dropped.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import ConnectionProfile, RunSpec
from database import prepare_table

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connection", type=Path, required=True)
    parser.add_argument("--preset", type=Path, required=True)
    args = parser.parse_args()
    profile = ConnectionProfile.model_validate_json(args.connection.read_text())
    spec = RunSpec.model_validate_json(args.preset.read_text())
    try:
        print(json.dumps(prepare_table(profile, spec), ensure_ascii=False))
    except Exception as exc:
        raise SystemExit("Prepare failed: " + type(exc).__name__) from None
