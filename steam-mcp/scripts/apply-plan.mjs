import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { readFileSync, writeFileSync, mkdirSync, copyFileSync, existsSync } from "node:fs";
import { createHash } from "node:crypto";
import { dirname, resolve, basename } from "node:path";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";
import { cdp } from "../dist/cdp/client.js";
import { backupCollections } from "../dist/tools/shared.js";
import { namespaceSidecarFiles } from "../dist/local/cloudstorage.js";

const [mode, planFile, receiptFile] = process.argv.slice(2);
assert(
  ["preview", "apply"].includes(mode) && planFile && receiptFile,
  "Usage: apply-plan.mjs preview|apply PLAN RECEIPT",
);
const mcpRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const root = resolve(mcpRoot, "..");
const plan = JSON.parse(readFileSync(planFile, "utf8"));
assert.equal(plan.account_id, Number(process.env.STEAM_ACCOUNT_ID));
assert.equal(plan.status, "awaiting_review");
for (const [file, expected] of Object.entries(plan.input_sha256)) {
  assert.equal(
    createHash("sha256")
      .update(readFileSync(resolve(root, file)))
      .digest("hex"),
    expected,
    file,
  );
}
const baseline = JSON.parse(readFileSync(resolve(root, "output/live_library.json"), "utf8"));
const capture = () =>
  cdp.evalInPage(
    () =>
      Array.from(globalThis.collectionStore.collectionsFromStorage.values())
        .map((c) => {
          const storage = c.ToStorageFormat();
          const resolved = (c.allApps || []).map((a) => a.appid);
          const members = new Set(resolved);
          if (!c.bIsDynamic) {
            for (const part of [storage.roamingData, storage.localData]) {
              for (const appid of part?.added || []) members.add(appid);
              for (const appid of part?.removed || []) members.delete(appid);
            }
          }
          return {
            id: c.id,
            name: c.displayName,
            dynamic: !!c.bIsDynamic,
            appids: [...members].sort((a, b) => a - b),
            storage,
          };
        })
        .sort((a, b) => a.id.localeCompare(b.id)),
    undefined,
    "classification-storage",
  );
const storageOnly = (rows) =>
  rows.map((c) => ({ id: c.id, storage: c.storage })).sort((a, b) => a.id.localeCompare(b.id));
const receipt = {
  mode,
  rule_version: plan.rule_version,
  account_id: plan.account_id,
  started_at: new Date().toISOString(),
  status: "running",
  steps: [],
  collections: [],
};
const save = () => writeFileSync(receiptFile, JSON.stringify(receipt, null, 2) + "\n", "utf8");
const client = new Client({ name: "steam-classification-batch", version: "1" });
const transport = new StdioClientTransport({
  command: process.execPath,
  args: ["--env-file-if-exists=.env", "dist/index.js"],
  cwd: mcpRoot,
  env: { ...process.env, STEAM_MCP_ALLOW_WRITES: mode === "apply" ? "1" : "0" },
});
const call = async (name, args) => {
  const raw = await client.callTool({ name, arguments: args }, undefined, { timeout: 60000 });
  const result = JSON.parse(raw.content.find((c) => c.type === "text").text);
  receipt.steps.push({ tool: name, args, result });
  save();
  assert(!raw.isError && result.ok !== false, JSON.stringify(result));
  assert(!result.skipped?.length, JSON.stringify(result.skipped));
  assert(result.verified !== false, JSON.stringify(result));
  return result;
};
const chunks = (ids) =>
  Array.from({ length: Math.ceil(ids.length / 500) }, (_, i) => ids.slice(i * 500, (i + 1) * 500));
try {
  receipt.before = await capture();
  assert.deepEqual(
    storageOnly(receipt.before),
    storageOnly(baseline.collections),
    "Collection baseline changed",
  );
  save();
  await client.connect(transport);
  const status = await call("steam_status", {});
  assert(status.client.stores_ready && status.client.debug_port);
  if (mode === "apply") {
    process.env.STEAM_MCP_ALLOW_WRITES = "1";
    receipt.backup_path = backupCollections("classification-start", true);
    assert(receipt.backup_path, "Steam storage backup missing");
    const dir = resolve(
      root,
      "data/snapshots",
      `steam-storage-${receipt.started_at.replace(/[:.]/g, "-")}`,
    );
    receipt.storage_snapshot_path = dir;
    mkdirSync(dir, { recursive: true });
    for (const file of namespaceSidecarFiles(String(plan.account_id)))
      if (existsSync(file)) copyFileSync(file, resolve(dir, basename(file)));
    save();
  }
  // Create and update destinations before retiring old collections.
  const actions = [
    ...plan.actions.filter((a) => a.action !== "delete"),
    ...plan.actions.filter((a) => a.action === "delete"),
  ];
  for (const action of actions) {
    if (action.action === "preserve") continue;
    assert(action.status !== "category_pending", action.name);
    let id = action.id;
    const dry = mode !== "apply";
    if (action.action === "create") {
      const ids = action.desired_appids || [];
      const made = await call("steam_collection_create", {
        name: action.name,
        appids: ids.slice(0, 500),
        on_existing: "fail",
        dry_run: dry,
      });
      id = made.collection.id;
      if (!dry)
        for (const batch of chunks(ids.slice(500)))
          await call("steam_collection_add_games", { id, appids: batch });
    } else if (action.action === "delete") {
      await call("steam_collection_delete", { id, confirm: true, dry_run: dry });
      if (!dry) assert(!(await capture()).some((c) => c.id === id));
      continue;
    } else {
      let desired = action.desired_appids;
      if (action.action === "review_dynamic_migration") {
        const original = receipt.before.find((c) => c.id === id);
        const scoped = new Set(action.scope_appids);
        const inventory = JSON.parse(readFileSync(resolve(root, "output/inventory.json"), "utf8"));
        const scope = new Set(
          inventory.entries.filter((g) => g.decision === "game").map((g) => g.appid),
        );
        for (const part of [original.storage.roamingData, original.storage.localData])
          for (const appid of part?.added || []) if (!scope.has(appid)) scoped.add(appid);
        desired = [...scoped].sort((a, b) => a - b);
        const info = await cdp.evalInPage(
          async (arg) => {
            const c = globalThis.collectionStore.GetCollection(arg.id);
            if (!c.bIsDynamic || typeof c.FreezeToStatic !== "function")
              throw Error("FREEZE_UNAVAILABLE");
            if (!arg.dry) {
              c.FreezeToStatic();
              await c.Save();
            }
            return { id: c.id, dynamic: !!c.bIsDynamic };
          },
          { id, dry },
          "classification-freeze",
        );
        receipt.steps.push({
          tool: "Steam.FreezeToStatic",
          args: { id, dry_run: dry },
          result: info,
        });
        save();
        if (!dry) assert.equal(info.dynamic, false);
        if (dry) {
          receipt.collections.push({ id, name: action.name, expected_appids: desired });
          continue;
        }
      }
      if (action.action === "rename_and_update")
        await call("steam_collection_rename", { id, new_name: action.name, dry_run: dry });
      const current = (await capture()).find((c) => c.id === id);
      const wanted = new Set(desired);
      const actual = new Set(current.appids);
      for (const batch of chunks(current.appids.filter((a) => !wanted.has(a))))
        await call("steam_collection_remove_games", { id, appids: batch, dry_run: dry });
      for (const batch of chunks(desired.filter((a) => !actual.has(a))))
        await call("steam_collection_add_games", { id, appids: batch, dry_run: dry });
      action.desired_appids = desired;
    }
    const expected = action.desired_appids || [];
    if (!dry) {
      const actual = (await capture()).find((c) => c.id === id);
      assert(actual && !actual.dynamic);
      assert.equal(actual.name, action.name);
      assert.deepEqual(
        actual.appids,
        [...expected].sort((a, b) => a - b),
        action.name,
      );
    }
    receipt.collections.push({ id, name: action.name, expected_appids: expected });
    save();
    if (receipt.collections.length % 10 === 0)
      console.log(JSON.stringify({ mode, collections: receipt.collections.length }));
  }
  receipt.after = await capture();
  if (mode === "preview") assert.deepEqual(storageOnly(receipt.after), storageOnly(receipt.before));
  const untouched = new Set(plan.actions.filter((a) => a.action === "preserve").map((a) => a.id));
  assert.deepEqual(
    storageOnly(receipt.after.filter((c) => untouched.has(c.id))),
    storageOnly(receipt.before.filter((c) => untouched.has(c.id))),
  );
  receipt.status = "complete";
  receipt.completed_at = new Date().toISOString();
  save();
  console.log(
    JSON.stringify({
      mode,
      status: receipt.status,
      collections: receipt.collections.length,
      calls: receipt.steps.length,
      backup: receipt.backup_path,
      receipt: receiptFile,
    }),
  );
} catch (error) {
  receipt.status = "failed";
  receipt.error = String(error);
  save();
  throw error;
} finally {
  await client.close();
  cdp.close();
}
