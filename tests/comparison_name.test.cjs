"use strict";
const assert = require("node:assert/strict");
const {readFileSync} = require("node:fs");
const {join} = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

// 比較画面の実装を、最小DOMと読み取り専用のプレビュー応答で動かす。
class Element {
  constructor(tag) {
    this.tag = tag;
    this.children = [];
    this.events = new Map();
    this.attributes = new Map();
    this.value = "";
    this.checked = false;
  }
  querySelector(selector) {
    this.selectors ??= new Map();
    if (!this.selectors.has(selector)) this.selectors.set(selector, new Element(selector));
    return this.selectors.get(selector);
  }
  querySelectorAll(selector) {
    const current = selector === 'input[type="checkbox"]'
      && this.tag === "input" && this.type === "checkbox" ? [this] : [];
    return [...current, ...this.children.flatMap((node) => node.querySelectorAll(selector))];
  }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  addEventListener(event, callback) { this.events.set(event, callback); }
  setAttribute(name, value) { this.attributes.set(name, value); }
  emit(event) { return this.events.get(event)?.(); }
  click() { return this.emit("click"); }
}

test("比較名を統一してお気に入りと書き出しに使い、プレビューAPIは変えない", async () => {
  const expected = "factory-sql-data-loader";
  const key = "factory-comparison-favorites:" + expected;
  const root = new Element("#document");
  const selected = {
    id: "fixture-preset",
    spec: {name: "合成ひな型", fields: [], sql: {operation: "insert"}, rows: 2},
  };
  const calls = [];
  const saved = [];
  const downloads = [];
  let blob;
  const context = vm.createContext({
    window: {},
    document: {
      querySelector: (selector) => root.querySelector(selector),
      createElement: (tag) => {
        const result = new Element(tag);
        if (tag === "a") result.click = () => downloads.push(result.download);
        return result;
      },
    },
    localStorage: {
      getItem: (storageKey) => {
        assert.equal(storageKey, key);
        return JSON.stringify([selected.id]);
      },
      setItem: (storageKey, value) => saved.push([storageKey, value]),
    },
    state: {presets: [selected]},
    runSpec: () => ({...selected.spec, rows: 4}),
    api: async (path, spec) => {
      calls.push([path, spec.rows]);
      return {records: [{}], validated_spec: spec, writes: false};
    },
    Blob,
    URL: {
      createObjectURL: (value) => { blob = value; return "blob:synthetic"; },
      revokeObjectURL() {},
    },
    setTimeout() {},
  });
  const asset = (name) => readFileSync(join(__dirname, "../static", name), "utf8");
  vm.runInContext(asset("comparison.js"), context);
  const source = asset("app.js");
  const start = source.indexOf("window.FactoryComparison.mount({");
  const end = source.indexOf('\n  $("#sqlActions")', start);
  assert.ok(start > 0 && end > start);
  vm.runInContext(source.slice(start, end), context);

  const panel = root.querySelector("#comparison");
  const find = (selector) => panel.querySelector(selector);
  root.querySelector('.nav[data-tab="comparison"]').click();
  const item = find("#comparisonCatalog").children[0];
  const favorite = item.children[1].children[0];
  assert.equal(favorite.attributes.get("aria-pressed"), "true");
  favorite.click();
  assert.deepEqual(saved, [[key, "[]"]]);
  const checkbox = item.children[0].children[0];
  checkbox.checked = true;
  checkbox.emit("change");
  find("#comparisonCurrent").checked = true;
  await find("#comparisonRun").click();
  assert.deepEqual(calls, [["/api/preview", 4], ["/api/preview", 2]]);
  assert.equal(find("#comparisonResults").hidden, false);
  find("#comparisonJSON").click();
  assert.deepEqual(downloads, [expected + "-comparison.json"]);
  const report = JSON.parse(await blob.text());
  assert.equal(report.application, expected);
  assert.equal(report.cases.length, 2);
});
