(() => {
  "use strict";

  const operations = [
    { value: "insert", label: "追加のみ" },
    { value: "upsert", label: "追加または更新" },
    { value: "update", label: "既存行のみ更新" },
    { value: "delete", label: "主キーが一致する行を削除" },
  ];
  const dataTypes = [
    { value: "string", label: "文字列" }, { value: "integer", label: "整数" },
    { value: "number", label: "浮動小数点" }, { value: "decimal", label: "固定精度の小数" },
    { value: "boolean", label: "真偽値" }, { value: "datetime", label: "日時" },
    { value: "json", label: "JSON" },
  ];
  const generators = [
    { value: "record_id", label: "再現可能なレコードID" }, { value: "uuid", label: "UUID" },
    { value: "sequence", label: "連番" }, { value: "integer", label: "整数範囲" },
    { value: "uniform", label: "一様分布" }, { value: "normal", label: "正規分布" },
    { value: "choice", label: "候補から選択" }, { value: "boolean", label: "確率で真偽値" },
    { value: "constant", label: "固定値" }, { value: "template", label: "文字列の書式" },
    { value: "timestamp", label: "時刻系列" }, { value: "reference", label: "別項目を参照" },
    { value: "sum", label: "加算" }, { value: "difference", label: "減算" },
    { value: "product", label: "乗算" }, { value: "ratio", label: "除算" },
  ];
  const identifier = /^[A-Za-z_][A-Za-z0-9_]{0,62}$/;
  const faultEnabled = { path: "faults.enabled", equals: true };

  function summarize(spec, preview) {
    const sql = spec.sql ?? {};
    const operation = sql.operation ?? "upsert";
    const fields = Array.isArray(spec.fields) ? spec.fields.filter(field => identifier.test(field?.name ?? "")) : [];
    const columns = fields.map(field => field.name);
    const keys = fields.filter(field => field.primary_key === true).map(field => field.name);
    const values = columns.filter(name => !keys.includes(name));
    const table = sql.table ?? "sample_records";
    const schema = sql.schema_name ?? "";
    const target = [schema, table].filter(Boolean).join(".");
    const rows = [
      { label: "接続プロファイル", value: spec.connection_id ?? "default" },
      { label: "対象テーブル", value: target },
      { label: "実行時の操作", value: operations.find(item => item.value === operation)?.label ?? "未定義" },
      { label: "主キー", value: keys.join("、") || (operation === "insert" ? "指定なし" : "要指定") },
    ];
    if (["insert", "upsert"].includes(operation)) {
      rows.push({ label: "追加する列", value: columns.join("、") || "項目を追加してください" });
    }
    if (["update", "upsert"].includes(operation)) {
      rows.push({ label: "更新する列", value: values.join("、") || "主キー以外の列はありません" });
    }
    if (identifier.test(table) && (!schema || identifier.test(schema)) && columns.length) {
      const statements = [];
      const predicate = keys.map(key => key + " = :k_" + key).join(" AND ");
      if (["insert", "upsert"].includes(operation)) {
        statements.push("INSERT INTO " + target + " (" + columns.join(", ") + ") VALUES ("
          + columns.map(name => ":v_" + name).join(", ") + ")");
      }
      if (["update", "upsert"].includes(operation) && predicate && values.length) {
        statements.push("UPDATE " + target + " SET "
          + values.map(name => name + " = :v_" + name).join(", ") + " WHERE " + predicate);
      }
      if (operation === "delete" && predicate) statements.push("DELETE FROM " + target + " WHERE " + predicate);
      if (statements.length) rows.push({ label: "SQLの概略（方言・引用符省略）", value: statements.join(" / ") });
    }
    if (operation === "upsert") {
      rows.push({ label: "追加／更新の分岐", value: "バッチごとに既存の主キーを確認し、存在すれば更新、存在しなければ追加します。" });
    }
    if (fields.length) {
      const columnType = field => {
        const kind = field.data_type ?? "string";
        if (kind === "string") return "Unicode(" + (field.length ?? 255) + ")";
        if (kind === "decimal") return "NUMERIC(" + (field.precision ?? 18) + "," + (field.scale ?? 3) + ")";
        return { integer: "BIGINT", number: "浮動小数点", boolean: "真偽値", datetime: "日時（SQL ServerはDATETIME2）", json: "Unicodeテキスト" }[kind] ?? "未定義";
      };
      rows.push({ label: "生成値に対応する列型の目安", value: fields.map(field => field.name + ": " + columnType(field)).join("\n") });
    }
    const count = spec.rows ?? 30;
    const batchSize = preview?.effective_batch_limit ?? spec.batch_size ?? 1000;
    if (Number.isSafeInteger(count) && count > 0 && Number.isSafeInteger(batchSize) && batchSize > 0) {
      rows.push({ label: "コミット回数の目安", value: Math.ceil(count / batchSize).toLocaleString("ja-JP") + "回（1バッチ最大" + batchSize + "行）" });
    }
    const rate = spec.target_rate ?? 10;
    if (Number.isFinite(rate) && rate >= 0) {
      const estimate = rate ? ((count / rate) + (spec.initial_delay_seconds ?? 0)) : null;
      rows.push({ label: "所要時間の目安", value: Number.isFinite(estimate)
        ? estimate.toLocaleString("ja-JP", { maximumFractionDigits: 2 }) + "秒（接続・処理・バーストの時間を除く）"
        : "速度制限なし。実行先の性能に依存します。" });
    }
    if (keys.length && Array.isArray(preview?.records)) {
      const sample = preview.records.map(record => keys.map(key => record[key]));
      const valid = sample.filter(tuple => tuple.every(value => value != null));
      const repeated = valid.length - new Set(valid.map(tuple => JSON.stringify(tuple))).size;
      rows.push({ label: "サンプルの主キー", value: "確認 " + sample.length + "行、重複 " + repeated
        + "行、欠落またはNULL " + (sample.length - valid.length) + "行" });
    }
    return rows;
  }

  window.FactoryPresetConfig = {
    title: "SQL投入の設定",
    description: "書き込み先、列と主キー、バッチの条件を編集します。保存・見本ではDBへ接続しません。",
    sections: [
      {
        title: "接続先・操作",
        description: "外部DBのホストや認証情報は接続設定で管理します。プリセットには接続IDだけを保存できます。",
        fields: [
          { path: "connection_id", label: "接続プロファイル", type: "connection", default: "default" },
          { path: "sql.schema_name", label: "スキーマ", type: "text", placeholder: "例: public", help: "空欄は接続先の既定。ここではスキーマを作成しません。" },
          { path: "sql.table", label: "テーブル", type: "text", default: "sample_records", placeholder: "例: sample_records", help: "英数字・下線。列名を既存テーブルと合わせます。" },
          { path: "sql.operation", label: "実行時の操作", type: "select", options: operations, default: "upsert" },
          { path: "connection_revision", label: "接続設定の版を固定", type: "integer", min: 1, help: "空欄は実行開始時の最新版。" },
        ],
      },
      {
        title: "列定義・主キー",
        description: "生成順に並べます。追加以外の操作では、実際のテーブルと同じ主キーを指定します。組み合わせ主キーは複数の列を指定します。",
        fields: [
          {
            path: "fields", label: "テーブル列", type: "table",
            default: { name: "new_column", data_type: "string", generator: "constant", value: "sample" },
            columns: [
              { path: "name", label: "列名", type: "text" },
              { path: "data_type", label: "列のデータ型", type: "select", options: dataTypes, default: "string" },
              { path: "generator", label: "生成方式", type: "select", options: generators, default: "constant" },
              { path: "primary_key", label: "主キー", type: "boolean", default: false },
              { path: "value", label: "固定値 JSON", type: "json", when: { path: "generator", equals: "constant" } },
              { path: "template", label: "文字列の書式", type: "text", when: { path: "generator", equals: "template" } },
              { path: "minimum", label: "最小値または $参照 JSON", type: "json", when: { path: "generator", equals: ["integer", "uniform"] } },
              { path: "maximum", label: "最大値または $参照 JSON", type: "json", when: { path: "generator", equals: ["integer", "uniform"] } },
              { path: "mean", label: "平均または $参照 JSON", type: "json", when: { path: "generator", equals: "normal" } },
              { path: "stddev", label: "標準偏差または $参照 JSON", type: "json", when: { path: "generator", equals: "normal" } },
              { path: "start", label: "開始値または開始時刻 JSON", type: "json", when: { path: "generator", equals: ["sequence", "timestamp"] }, help: "時刻・共有値の参照は引用符で囲みます。" },
              { path: "step", label: "刻みまたは $参照 JSON", type: "json", when: { path: "generator", equals: ["sequence", "timestamp"] }, help: "時刻系列では秒単位。" },
              { path: "choices", label: "候補の配列または $参照 JSON", type: "json", when: { path: "generator", equals: "choice" } },
              { path: "weights", label: "候補の重み JSON", type: "json", when: { path: "generator", equals: "choice" } },
              { path: "probability", label: "true の確率または $参照 JSON", type: "json", when: { path: "generator", equals: "boolean" } },
              { path: "sources", label: "参照する先行項目", type: "string-list", when: { path: "generator", equals: ["reference", "sum", "difference", "product", "ratio"] } },
              { path: "null_probability", label: "欠損率", type: "number", min: 0, max: 1, step: 0.01, default: 0, help: "主キーには欠損率を指定できません。" },
              { path: "length", label: "文字列の最大長", type: "integer", min: 1, max: 4000, when: { path: "data_type", equals: "string" } },
              { path: "precision", label: "合計桁数", type: "integer", min: 1, max: 38, when: { path: "data_type", equals: "decimal" } },
              { path: "scale", label: "小数桁数", type: "integer", min: 0, max: 18, when: { path: "data_type", equals: "decimal" } },
            ],
          },
          { path: "parameters", label: "共有パラメーター", type: "json", help: "生成条件から $名前 で参照できます。機密情報は入力しないでください。" },
        ],
      },
      {
        title: "件数・トランザクション",
        fields: [
          { path: "rows", label: "総行数", type: "integer", min: 1, max: 100000000, default: 30 },
          { path: "batch_size", label: "コミットあたりの上限行数", type: "integer", min: 1, max: 10000, default: 1000 },
          { path: "target_rate", label: "目標行数 / 秒", type: "integer", min: 0, max: 1000000, default: 10, help: "0は制限なし。" },
          { path: "max_duration_seconds", label: "実行時間上限（秒）", type: "integer", min: 1, max: 86400 },
          { path: "run_key", label: "実行キー", type: "text", default: "trial-001", help: "同じキー・シード・連番は同じ生成キーになります。" },
          { path: "seed", label: "乱数シード", type: "integer", min: 0, max: 2147483647, default: 42 },
          { path: "start_sequence", label: "開始シーケンス", type: "integer", min: 0, max: 1000000000 },
          { path: "initial_delay_seconds", label: "開始前の待機（秒）", type: "number", min: 0, max: 3600, step: 0.1 },
          { path: "burst_rows", label: "バーストあたりの行数", type: "integer", min: 0, max: 1000000, help: "0は通常送信。指定値より大きなバッチを抑えます。" },
          { path: "burst_pause_ms", label: "バーストの間隔（ms）", type: "integer", min: 0, max: 3600000 },
        ],
      },
      {
        title: "更新・ロールバック試験",
        description: "書き込み後の障害は、そのバッチのロールバックを試す設定です。以前に確定したバッチは戻りません。",
        fields: [
          { path: "faults.enabled", label: "処理障害を有効化", type: "boolean", default: false },
          { path: "faults.stage", label: "発生位置", type: "select", options: [{ value: "before_batch", label: "バッチ開始前" }, { value: "after_write", label: "書き込み後、コミット前" }], when: faultEnabled },
          { path: "faults.action", label: "処理", type: "select", options: [{ value: "none", label: "なし" }, { value: "fail", label: "バッチを失敗させる" }, { value: "delay", label: "遅延させる" }], when: faultEnabled },
          { path: "faults.from_batch", label: "最初に適用するバッチ番号", type: "integer", min: 1, max: 100000000, when: faultEnabled },
          { path: "faults.every_batches", label: "適用間隔（バッチ）", type: "integer", min: 1, max: 100000000, when: faultEnabled },
          { path: "faults.max_occurrences", label: "最大発生回数", type: "integer", min: 0, max: 1000000, when: faultEnabled },
          { path: "faults.delay_ms", label: "遅延（ms）", type: "integer", min: 0, max: 60000, when: { path: "faults.action", equals: "delay" } },
          {
            path: "anomalies", label: "データ異常ルール", type: "table",
            default: { name: "異常注入", field: "", operation: "null", from_row: 0, probability: 1 },
            columns: [
              { path: "name", label: "ルール名", type: "text", default: "異常注入" },
              { path: "enabled", label: "有効", type: "boolean", default: true },
              { path: "field", label: "対象項目名", type: "text" },
              { path: "operation", label: "操作", type: "select", default: "null", options: [
                { value: "null", label: "欠測（NULL）" }, { value: "omit", label: "項目を省く" }, { value: "freeze", label: "値を固定" },
                { value: "set", label: "指定値に置換" }, { value: "add", label: "加算" }, { value: "multiply", label: "倍率" },
                { value: "ramp", label: "徐々に増減" }, { value: "copy_from", label: "別の行をコピー" }, { value: "timestamp_shift", label: "時刻をずらす" },
              ] },
              { path: "value", label: "異常値 JSON", type: "json", when: { path: "operation", equals: ["set", "add", "multiply", "ramp", "copy_from", "timestamp_shift"] } },
              { path: "from_row", label: "開始位置（0始まり）", type: "integer", min: 0, max: 100000000, default: 0 },
              { path: "to_row", label: "終了位置（空欄は最後まで）", type: "integer", min: 0, max: 100000000 },
              { path: "every", label: "何行ごとに適用するか", type: "integer", min: 1, max: 100000000, default: 1 },
              { path: "probability", label: "適用確率", type: "number", min: 0, max: 1, step: 0.01, default: 1 },
              { path: "bypass_validation", label: "意図的に型や制約を外す", type: "boolean", default: false },
              { path: "when", label: "適用条件 JSON", type: "json", help: "空欄なら条件を指定しません。" },
            ],
          },
        ],
      },
    ],
    summarize,
  };
})();
