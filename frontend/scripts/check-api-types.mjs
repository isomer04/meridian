import { readFile } from "node:fs/promises";

const contract = await readFile(
  new URL("../src/lib/api/generated.ts", import.meta.url),
  "utf8",
);
if (
  !contract.includes('Schema["RunResponse"]') ||
  !contract.includes('Schema["LoanRunResult"]')
) {
  throw new Error("OpenAPI aliases are incomplete.");
}
console.log("Checked committed MERIDIAN OpenAPI aliases.");
