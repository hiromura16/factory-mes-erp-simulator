"use strict";
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

// 同一オリジンAPIとブラウザーの確認を代替し、外部データベースへ接続しない。
function fixture() {
  const js = readFileSync(join(__dirname, "../static/app.js"), "utf8");
  const context = vm.createContext({
    structuredClone,
    document: {
      querySelector: () => ({querySelectorAll: () => []}),
      addEventListener() {},
    },
    window: {addEventListener() {}},
  });
  const run = (text) => vm.runInContext(text, context);
  run(js.slice(0, js.lastIndexOf("\ninit().catch(")));
  run(`
    state.connections = [{id: "fixture", name: "合成DB", kind: "sqlite", revision: 3}];
    state.spec = {connection_id: "fixture", connection_revision: null,
      sql: {schema_name: "demo", table: "items"}};
  `);
  return {run, context};
}

test("確認を取り消すと対象表を作成するPOSTを送らない", async () => {
  const {run, context} = fixture();
  const paths = [];
  context.request = async (path) => {
    paths.push(path);
    return {connections: [{id: "fixture", revision: 3}]};
  };
  context.confirm = (message) => {
    assert.match(message, /合成DB \/ fixture · sqlite · 版 3/);
    assert.match(message, /対象テーブル: demo\.items/);
    return false;
  };
  const result = await run('sqlDatabaseAction("/api/table/prepare", request, confirm)');
  assert.equal(result, null);
  assert.deepEqual(paths, ["/api/connections"]);
});

test("確認後も表示された接続版と対象表だけを送る", async () => {
  const {run, context} = fixture();
  const requests = [];
  context.request = async (path, spec) => {
    requests.push([path, spec]);
    if (path === "/api/connections")
      return {connections: [{id: "fixture", revision: 3}]};
    return {status: "created"};
  };
  context.confirm = () => true;
  await run('sqlDatabaseAction("/api/table/prepare", request, confirm)');
  assert.equal(requests[1][0], "/api/table/prepare");
  assert.equal(requests[1][1].connection_id, "fixture");
  assert.equal(requests[1][1].connection_revision, 3);
  assert.equal(requests[1][1].sql.table, "items");
  assert.equal(run("state.spec.connection_revision"), null);
  for (const suffix of ["inspect", "ddl"]) {
    await run(`sqlDatabaseAction("/api/table/${suffix}", request, confirm)`);
    assert.equal(requests.at(-1)[0], "/api/table/" + suffix);
    assert.equal(requests.at(-1)[1].connection_revision, 3);
  }
});

test("別画面で同じIDの版を更新したときは書き込まず再取得する", async () => {
  for (const path of ["/api/table/prepare", "/api/runs"]) {
    const {run, context} = fixture();
    let refresh = 0;
    context.request = async (name) => {
      assert.equal(name, "/api/connections");
      return {connections: [{id: "fixture", revision: 4}]};
    };
    context.confirm = () => assert.fail("表示中の版を再確認していない");
    context.refresh = async () => { refresh++; };
    await assert.rejects(
      run(`sqlDatabaseAction("${path}", request, confirm, refresh)`), /接続先が更新/);
    assert.equal(refresh, 1);
  }
});

test("照合の応答を待つ間に対象表が変わったら確認もPOSTもしない", async () => {
  const {run, context} = fixture();
  let complete;
  context.request = (path) => {
    assert.equal(path, "/api/connections");
    return new Promise((resolve) => { complete = resolve; });
  };
  context.confirm = () => assert.fail("変更した表は確認していない");
  const pending = run('sqlDatabaseAction("/api/table/prepare", request, confirm)');
  run('state.spec.sql.table = "changed"');
  complete({connections: [{id: "fixture", revision: 3}]});
  await assert.rejects(pending, /実行条件が変更/);
});

test("更新・削除・既存行を更新できるupsertの取消は実行POSTを送らない", async () => {
  for (const operation of ["update", "delete", "upsert"]) {
    const {run, context} = fixture();
    run(`state.spec.rows = 15; state.spec.sql.operation = "${operation}";`);
    const paths = [];
    context.request = async (path) => {
      paths.push(path);
      return {connections: [{id: "fixture", revision: 3}]};
    };
    context.confirm = (message) => {
      assert.match(message, /合成DB \/ fixture · sqlite · 版 3/);
      assert.match(message, /対象テーブル: demo\.items/);
      assert.match(message, new RegExp("操作: " + operation));
      assert.match(message, /設定した生成行数: 15件/);
      return false;
    };
    assert.equal(await run('sqlDatabaseAction("/api/runs", request, confirm)'), null);
    assert.deepEqual(paths, ["/api/connections"]);
  }
});

test("確認済み更新は対象を固定し、設定待ちで対象が変わると確認も送信もしない", async () => {
  for (const move of [false, true]) {
    const {run, context} = fixture();
    run('state.spec.rows = 15; state.spec.sql.operation = "update";');
    let resolve;
    const requests = [];
    let prompts = 0;
    context.request = (path, spec) => {
      requests.push([path, spec]);
      return path === "/api/connections"
        ? new Promise((done) => { resolve = done; })
        : Promise.resolve({id: "synthetic"});
    };
    context.confirm = () => { prompts++; return true; };
    const pending = run('sqlDatabaseAction("/api/runs", request, confirm)');
    if (move) run('state.spec.sql.table = "other_items"');
    resolve({connections: [{id: "fixture", revision: 3}]});
    if (move) {
      await assert.rejects(pending, /実行条件が変更/);
      assert.equal(prompts, 0);
      assert.equal(requests.length, 1);
    } else {
      assert.equal((await pending).id, "synthetic");
      assert.equal(prompts, 1);
      assert.equal(requests.length, 2);
      assert.equal(requests[1][0], "/api/runs");
      assert.equal(requests[1][1].connection_revision, 3);
      assert.equal(requests[1][1].sql.table, "items");
      assert.equal(requests[1][1].sql.operation, "update");
      assert.equal(requests[1][1].rows, 15);
    }
  }
});
