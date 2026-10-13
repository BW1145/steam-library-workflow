import { writeFileSync } from "node:fs";
import { cdp } from "../dist/cdp/client.js";
import { snapshotLibrary } from "../dist/cdp/programs/read.js";
import { account } from "../dist/model.js";

const output = process.argv[2];
if (!output) throw new Error("Usage: export-library.mjs OUTPUT_FILE");
try {
  const snapshot = await cdp.evalInPage(snapshotLibrary, { includeTags: true }, "export-library");
  const details = await cdp.evalInPage(
    () => {
      const cs = globalThis.collectionStore;
      return {
        app_types: Object.fromEntries(
          globalThis.appStore.allApps.map((a) => [a.appid, a.app_type]),
        ),
        collections: Array.from(cs.collectionsFromStorage.values()).map((c) => ({
          id: c.id,
          name: c.displayName,
          dynamic: !!c.bIsDynamic,
          appids: (c.allApps || []).map((a) => a.appid),
          storage: c.ToStorageFormat(),
        })),
      };
    },
    undefined,
    "export-library-details",
  );
  const data = {
    generated_at_utc: new Date().toISOString(),
    account_id: Number(account().accountId),
    source: "steam-client-live",
    snapshot,
    ...details,
  };
  writeFileSync(output, JSON.stringify(data, null, 2) + "\n", "utf8");
  console.log(
    JSON.stringify({ output, apps: snapshot.total, collections: details.collections.length }),
  );
} finally {
  cdp.close();
}
