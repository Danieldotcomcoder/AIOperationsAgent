// Generate src/generated.ts from the OpenAPI schema the API exports.
//
//   pnpm contracts:generate     (root: exports openapi.json, then runs this)
//
// The output is committed so the SPA type-checks without a Python toolchain. A
// stale file is caught by `pnpm contracts:check`, which regenerates and diffs.
import { readFileSync, writeFileSync } from 'node:fs';
import openapiTS, { astToString } from 'openapi-typescript';

const schemaUrl = new URL('../openapi.json', import.meta.url);
const outputUrl = new URL('../src/generated.ts', import.meta.url);
const check = process.argv.includes('--check');

const ast = await openapiTS(JSON.parse(readFileSync(schemaUrl, 'utf8')));
const banner =
  '/* eslint-disable */\n' +
  '// GENERATED from packages/contracts/openapi.json by scripts/generate.mjs.\n' +
  '// Do not edit by hand: change the Pydantic models, then run `pnpm contracts:generate`.\n\n';
const contents = banner + astToString(ast);

if (check) {
  let current = '';
  try {
    current = readFileSync(outputUrl, 'utf8');
  } catch {
    // Missing counts as stale.
  }
  if (current.replace(/\r\n/g, '\n') !== contents.replace(/\r\n/g, '\n')) {
    console.error('src/generated.ts is stale: run `pnpm contracts:generate`.');
    process.exit(1);
  }
  console.log('src/generated.ts matches openapi.json');
} else {
  writeFileSync(outputUrl, contents);
  console.log('wrote src/generated.ts');
}
