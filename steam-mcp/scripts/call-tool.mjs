import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { writeFileSync } from "node:fs";

const [name, rawArgs = "{}", output] = process.argv.slice(2);
if (!name) throw new Error("Usage: node scripts/call-tool.mjs TOOL [JSON_ARGUMENTS] [OUTPUT_FILE]");
const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const client = new Client({ name: "steam-workflow", version: "1" });
const transport = new StdioClientTransport({
  command: process.execPath,
  args: ["--env-file-if-exists=.env", "dist/index.js"],
  cwd: root,
  env: Object.fromEntries(Object.entries(process.env).filter(([, value]) => value !== undefined)),
});
try {
  await client.connect(transport);
  const result = await client.callTool({ name, arguments: JSON.parse(rawArgs) }, undefined, {
    timeout: 240_000,
  });
  const payload = result.content.find((item) => item.type === "text");
  const text = payload?.text ?? JSON.stringify(result);
  if (output) {
    writeFileSync(output, text + "\n", "utf8");
    console.log(JSON.stringify({ output, isError: result.isError ?? false }));
  } else console.log(text);
  if (result.isError) process.exitCode = 1;
} finally {
  await client.close();
}
