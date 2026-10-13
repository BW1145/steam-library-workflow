import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import assert from "node:assert/strict";
import { writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { cdp } from "../dist/cdp/client.js";

const [mode, name, seedText, extraText, output] = process.argv.slice(2);
if (!["preview", "apply"].includes(mode) || !name || !seedText || !extraText || !output) {
  throw new Error("Usage: verify-collections.mjs preview|apply NAME SEED_IDS EXTRA_ID OUTPUT");
}
const seed = seedText.split(",").map(Number);
const extra = Number(extraText);
assert(seed.every((id) => Number.isSafeInteger(id) && id > 0));
assert(Number.isSafeInteger(extra) && extra > 0 && !seed.includes(extra));
const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const receipt = { started_at: new Date().toISOString(), mode, name, seed, extra, steps: [] };
const save = () => writeFileSync(output, JSON.stringify(receipt, null, 2) + "\n", "utf8");
const capture = async () => {
  try {
    return await cdp.evalInPage(
      () => {
        const cs = globalThis.collectionStore;
        return Array.from(cs.collectionsFromStorage.values())
          .map((c) => ({ id: c.id, name: c.displayName, data: c.ToStorageFormat() }))
          .sort((a, b) => a.id.localeCompare(b.id));
      },
      undefined,
      "capture-collection-storage",
    );
  } finally {
    cdp.close();
  }
};
receipt.before = await capture();
assert(!receipt.before.some((c) => c.name === name), "Choose an unused test name");
save();
const client = new Client({ name: "steam-collection-verification", version: "1" });
const transport = new StdioClientTransport({
  command: process.execPath,
  args: ["--env-file-if-exists=.env", "dist/index.js"],
  cwd: root,
  env: { ...process.env, STEAM_MCP_ALLOW_WRITES: mode === "apply" ? "1" : "0" },
});
let createdId;
const call = async (tool, args) => {
  const result = await client.callTool({ name: tool, arguments: args }, undefined, {
    timeout: 60000,
  });
  const payload = JSON.parse(result.content.find((c) => c.type === "text").text);
  receipt.steps.push({ tool, args, result: payload });
  save();
  assert(!result.isError && payload.ok !== false, JSON.stringify(payload));
  return payload;
};
const checkMembers = async (ids) => {
  const result = await call("steam_collection_get", { id: createdId, include_games: true });
  assert.equal(result.source, "live");
  assert.deepEqual(
    result.games.map((game) => game.appid).sort((a, b) => a - b),
    [...ids].sort((a, b) => a - b),
  );
};
try {
  await client.connect(transport);
  await call("steam_collection_create", { name, appids: seed, on_existing: "fail", dry_run: true });
  if (mode === "apply") {
    const made = await call("steam_collection_create", { name, appids: seed, on_existing: "fail" });
    createdId = made.collection.id;
    await checkMembers(seed);
    await call("steam_collection_add_games", { id: createdId, appids: [extra] });
    await checkMembers([...seed, extra]);
    await call("steam_collection_remove_games", { id: createdId, appids: [seed[0]] });
    await checkMembers([...seed.slice(1), extra]);
    await call("steam_collection_replace_games", { id: createdId, appids: seed, confirm: true });
    await checkMembers(seed);
    await call("steam_collection_replace_games", { id: createdId, appids: seed, confirm: true });
    await checkMembers(seed);
    await call("steam_collection_delete", { id: createdId, confirm: true, dry_run: true });
    await call("steam_collection_delete", { id: createdId, confirm: true });
    createdId = undefined;
  }
} catch (error) {
  receipt.error = String(error);
  throw error;
} finally {
  // Delete only the collection this run created, even when an assertion fails.
  try {
    if (createdId) await call("steam_collection_delete", { id: createdId, confirm: true });
  } finally {
    await client.close();
    receipt.after = await capture();
    receipt.original_collections_preserved =
      JSON.stringify(receipt.before) === JSON.stringify(receipt.after);
    receipt.completed_at = new Date().toISOString();
    save();
  }
}
assert(
  receipt.original_collections_preserved,
  "Original collection storage changed; inspect receipt",
);
console.log(JSON.stringify({ output, mode, steps: receipt.steps.length, preserved: true }));
