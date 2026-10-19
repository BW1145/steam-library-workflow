import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { compileFunction } from "node:vm";

// Execute the real action loop with in-memory Steam/CDP doubles. No client,
// transport, account data, disk writes, or compiled MCP modules are loaded.
const script = readFileSync(new URL("../../scripts/apply-plan.mjs", import.meta.url), "utf8");
const start = script.indexOf("  for (const action of actions) {");
const end = script.indexOf("  receipt.after = await capture();", start);
assert.ok(start >= 0 && end > start, "apply-plan action loop must be present");
const actionLoop = script.slice(start, end);
const executeLoop = (context) =>
  compileFunction(
    `return (async () => { ${actionLoop} })()`,
    Object.keys(context),
  )(...Object.values(context));

async function runAction({
  dynamic = true,
  rename = true,
  mode = "apply",
  failRename = false,
} = {}) {
  const item = {
    id: "fixture-id",
    displayName: "Old Name",
    bIsDynamic: dynamic,
    appids: [1, 99],
    FreezeToStatic() {
      this.bIsDynamic = false;
    },
    async Save() {},
  };
  const calls = [];
  const receipt = {
    before: [{ id: item.id, name: item.displayName, storage: { roamingData: { added: [99] } } }],
    steps: [],
    collections: [],
  };
  const action = {
    id: item.id,
    name: rename ? "New Name" : "Old Name",
    action: dynamic ? "review_dynamic_migration" : "rename_and_update",
    status: "proposed",
    scope_appids: [2],
    desired_appids: [2, 99],
  };
  const capture = async () => [
    {
      id: item.id,
      name: item.displayName,
      dynamic: item.bIsDynamic,
      appids: [...item.appids].sort((a, b) => a - b),
    },
  ];
  const context = {
    actions: [action],
    mode,
    receipt,
    assert,
    capture,
    globalThis: { collectionStore: { GetCollection: () => item } },
    cdp: { evalInPage: async (fn, arg) => fn(arg) },
    call: async (name, args) => {
      calls.push({ name, args });
      if (name === "steam_collection_rename" && failRename) throw new Error("rename failed");
      if (args.dry_run) return {};
      if (name === "steam_collection_rename") item.displayName = args.new_name;
      else if (name === "steam_collection_remove_games")
        item.appids = item.appids.filter((id) => !args.appids.includes(id));
      else if (name === "steam_collection_add_games") item.appids.push(...args.appids);
      else throw new Error(`Unexpected tool: ${name}`);
      return {};
    },
    chunks: (ids) => (ids.length ? [ids] : []),
    save: () => {},
    readFileSync: () =>
      JSON.stringify({ entries: [1, 2].map((appid) => ({ appid, decision: "game" })) }),
    resolve: () => "fixture-inventory",
    root: "fixture-root",
  };
  let error;
  try {
    await executeLoop(context);
  } catch (caught) {
    error = caught;
  }
  return { item, calls, receipt, action, context, error };
}

for (const dynamic of [true, false]) {
  test(`${dynamic ? "dynamic conversion plus" : "static"} rename preserves ID and outside-scope members`, async () => {
    const result = await runAction({ dynamic });
    assert.ifError(result.error);
    assert.equal(result.item.id, "fixture-id");
    assert.equal(result.item.displayName, "New Name");
    assert.equal(result.item.bIsDynamic, false);
    assert.deepEqual(
      result.item.appids.sort((a, b) => a - b),
      [2, 99],
    );
    assert.equal(result.calls.filter((c) => c.name === "steam_collection_rename").length, 1);
    assert.equal(result.receipt.collections.length, 1);
    // A regenerated no-change plan must not freeze, rename, or change members again.
    result.context.actions = [{ ...result.action, action: "preserve" }];
    const before = result.calls.length;
    await executeLoop(result.context);
    assert.equal(result.calls.length, before);
  });
}

test("same-name dynamic conversion does not rename", async () => {
  const result = await runAction({ rename: false });
  assert.ifError(result.error);
  assert.equal(result.item.displayName, "Old Name");
  assert.equal(result.item.bIsDynamic, false);
  assert.equal(
    result.calls.some((c) => c.name === "steam_collection_rename"),
    false,
  );
});

test("combined preview validates rename as dry run without freezing or changing members", async () => {
  const result = await runAction({ mode: "preview" });
  assert.ifError(result.error);
  assert.equal(result.item.bIsDynamic, true);
  assert.equal(result.item.displayName, "Old Name");
  assert.deepEqual(result.item.appids, [1, 99]);
  assert.equal(result.calls.length, 1);
  assert.equal(result.calls[0].name, "steam_collection_rename");
  assert.equal(result.calls[0].args.dry_run, true);
});

for (const mode of ["preview", "apply"]) {
  test(`rename failure stops ${mode} before member changes or success receipt`, async () => {
    const result = await runAction({ mode, failRename: true });
    assert.match(result.error.message, /rename failed/);
    assert.equal(result.item.bIsDynamic, mode === "preview");
    assert.deepEqual(result.item.appids, [1, 99]);
    assert.equal(result.item.displayName, "Old Name");
    assert.equal(result.calls.length, 1);
    assert.equal(result.receipt.collections.length, 0);
  });
}
