"use strict";
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const app = readFileSync(join(__dirname, "../static/app.js"), "utf8");
const apiClient = readFileSync(join(__dirname, "../static/api-client.js"), "utf8");

// ブラウザー側の実装を使い、HTTPの応答だけ合成する。
function fixture() {
  const requests = [];
  const prompts = [];
  const options = {
    revision: 3,
    confirm: () => true,
    connections: () => Promise.resolve([{id: "fixture", revision: options.revision}]),
  };
  const context = vm.createContext({
    Headers,
    URL,
    structuredClone,
    document: {
      querySelector: () => ({querySelectorAll: () => []}),
      addEventListener() {},
    },
    window: {
      location: {origin: "http://localhost"},
      addEventListener() {},
      confirm(message) {
        prompts.push(message);
        return options.confirm(message);
      },
    },
    async fetch(path, settings) {
      const body = settings.body ? JSON.parse(settings.body) : undefined;
      const token = new Headers(settings.headers).get("X-Operation-Confirmation");
      requests.push({path, body, token});
      if (path === "/api/connections") {
        return Response.json({connections: await options.connections()});
      }
      if (path === "/api/operations/plan") {
        return Response.json({
          confirmation_token: "fixture-token",
          summary: {
            operation: body.path === "/api/table/prepare" ? "create_table" : body.payload.sql.operation,
            destination_id: "fixture",
            destination_name: "合成DB",
            revision: 3,
            destination_kind: "sqlite",
            table: body.payload.sql.schema_name + "." + body.payload.sql.table,
            rows: body.payload.rows,
          },
        });
      }
      if (path === "/api/table/inspect" || path === "/api/table/ddl") {
        return Response.json({status: "read"});
      }
      if (!token) return Response.json({detail: "確認が必要です"}, {status: 428});
      assert.equal(token, "fixture-token");
      return Response.json(path === "/api/runs" ? {id: "synthetic"} : {status: "created"});
    },
  });
  const run = (source) => vm.runInContext(source, context);
  run(apiClient);
  run(app.slice(0, app.lastIndexOf("\ninit().catch(")));
  run([
    'state.connections = [{id: "fixture", name: "合成DB", kind: "sqlite", revision: 3}];',
    'state.spec = {connection_id: "fixture", connection_revision: null,',
    '  sql: {schema_name: "demo", table: "items"}};',
  ].join("\n"));
  return {run, context, requests, prompts, options};
}

test("表作成の確認を取り消すと、実行用のトークン付きPOSTを送らない", async () => {
  const {run, requests, prompts, options} = fixture();
  options.confirm = () => false;
  await assert.rejects(run('sqlDatabaseAction("/api/table/prepare")'), /操作を取り消しました/);
  assert.deepEqual(requests.map(({path}) => path),
    ["/api/connections", "/api/table/prepare", "/api/operations/plan"]);
  assert.ok(requests.every(({token}) => token === null));
  assert.equal(prompts.length, 1);
  assert.match(prompts[0], /合成DB \/ fixture（版 3）/);
  assert.match(prompts[0], /接続の種類：sqlite/);
  assert.match(prompts[0], /対象テーブル：demo\.items/);
  assert.match(prompts[0], /既存テーブルは変更しません/);
});

test("確認後も表示された接続版と対象表だけを送る", async () => {
  const {run, requests, prompts} = fixture();
  const result = await run('sqlDatabaseAction("/api/table/prepare")');
  assert.equal(result.status, "created");
  assert.equal(prompts.length, 1);
  const [, initial, plan, confirmed] = requests;
  assert.equal(confirmed.path, "/api/table/prepare");
  assert.equal(confirmed.token, "fixture-token");
  assert.deepEqual(plan.body, {path: initial.path, payload: initial.body});
  assert.deepEqual(confirmed.body, initial.body);
  assert.equal(confirmed.body.connection_id, "fixture");
  assert.equal(confirmed.body.connection_revision, 3);
  assert.equal(confirmed.body.sql.table, "items");
  assert.equal(run("state.spec.connection_revision"), null);
  for (const suffix of ["inspect", "ddl"]) {
    await run('sqlDatabaseAction("/api/table/' + suffix + '")');
    assert.equal(requests.at(-1).path, "/api/table/" + suffix);
    assert.equal(requests.at(-1).body.connection_revision, 3);
  }
  assert.equal(prompts.length, 1);
});

test("別画面で同じIDの版を更新したときは書き込まず再取得する", async () => {
  for (const path of ["/api/table/prepare", "/api/runs"]) {
    const {run, context, options, requests, prompts} = fixture();
    options.revision = 4;
    let refresh = 0;
    context.refresh = async () => { refresh++; };
    await assert.rejects(
      run('sqlDatabaseAction(' + JSON.stringify(path) + ', api, refresh)'), /接続先が更新/);
    assert.equal(refresh, 1);
    assert.deepEqual(requests.map(({path}) => path), ["/api/connections"]);
    assert.equal(prompts.length, 0);
  }
});

test("照合の応答を待つ間に対象表が変わったら確認もPOSTもしない", async () => {
  const {run, options, requests, prompts} = fixture();
  let complete;
  options.connections = () => new Promise((resolve) => { complete = resolve; });
  const pending = run('sqlDatabaseAction("/api/table/prepare")');
  run('state.spec.sql.table = "changed"');
  complete([{id: "fixture", revision: 3}]);
  await assert.rejects(pending, /実行条件が変更/);
  assert.deepEqual(requests.map(({path}) => path), ["/api/connections"]);
  assert.equal(prompts.length, 0);
});

test("更新・削除・upsertの取消は、実行用のトークン付きPOSTを送らない", async () => {
  const labels = {
    update: "既存行を更新",
    delete: "既存行を削除",
    upsert: "未登録行を追加し、該当する既存行を更新",
  };
  for (const [operation, label] of Object.entries(labels)) {
    const {run, options, requests, prompts} = fixture();
    run('state.spec.rows = 15; state.spec.sql.operation = ' + JSON.stringify(operation));
    options.confirm = () => false;
    await assert.rejects(run('sqlDatabaseAction("/api/runs")'), /操作を取り消しました/);
    assert.deepEqual(requests.map(({path}) => path),
      ["/api/connections", "/api/runs", "/api/operations/plan"]);
    assert.ok(requests.every(({token}) => token === null));
    assert.equal(prompts.length, 1);
    assert.ok(prompts[0].includes("操作：" + label));
    assert.match(prompts[0], /合成DB \/ fixture（版 3）/);
    assert.match(prompts[0], /対象テーブル：demo\.items/);
    assert.match(prompts[0], /設定した生成行数：15件/);
    assert.match(prompts[0], /実際の更新・削除件数や連鎖処理/);
  }
});

test("確認時にフォームが変わっても、実行する更新は表示された対象のまま", async () => {
  const {run, options, requests, prompts} = fixture();
  run('state.spec.rows = 15; state.spec.sql.operation = "update";');
  options.confirm = () => {
    run('state.spec.sql.table = "other_items"');
    return true;
  };
  assert.equal((await run('sqlDatabaseAction("/api/runs")')).id, "synthetic");
  assert.equal(prompts.length, 1);
  assert.deepEqual(requests.map(({path}) => path),
    ["/api/connections", "/api/runs", "/api/operations/plan", "/api/runs"]);
  const [, initial, plan, confirmed] = requests;
  assert.deepEqual(plan.body.payload, initial.body);
  assert.deepEqual(confirmed.body, initial.body);
  assert.equal(confirmed.token, "fixture-token");
  assert.equal(confirmed.body.connection_revision, 3);
  assert.equal(confirmed.body.sql.table, "items");
  assert.equal(confirmed.body.sql.operation, "update");
  assert.equal(confirmed.body.rows, 15);
});
