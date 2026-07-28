// DRVS demo-corpus query script — proves core/scoring.js works standalone,
// on a real (if tiny) corpus that has nothing to do with the project DRVS
// was distilled from. Runs entirely in plain Node: exact + lexical +
// dictionary + relation channels only (no semantic vectors — that channel
// needs a browser/WASM model, see client/vector-client.js, and is out of
// scope for a Node demo script by design, same as the reference deployment
// only ever verifies that channel in-browser, never in Node).
//
// Usage: node examples/demo-corpus/query_demo.mjs
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { scoreCorpus, prepareIndex, DEFAULT_CONFIG } from "../../core/scoring.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const DIST_DIR = join(__dirname, "dist");

const index = JSON.parse(readFileSync(join(DIST_DIR, "index.json"), "utf-8"));
const dictionary = JSON.parse(readFileSync(join(DIST_DIR, "dictionary.json"), "utf-8")).entries;
const documents = prepareIndex(index.documents);

const QUERIES = [
  { label: "exact title match", query: "陽台番茄種植入門" },
  { label: "dictionary alias (牛油果 -> 酪梨)", query: "牛油果" },
  { label: "lexical/summary overlap, no title match", query: "咖啡豆冰箱保存" },
  { label: "series relation (query only matches the basics doc; pests doc should ride in via same-series Tier D)", query: "支架與側芽" },
  { label: "cross-domain / zero-data (nothing in this corpus is about passports)", query: "護照申請流程" },
];

let allOk = true;
for (const { label, query } of QUERIES) {
  const result = scoreCorpus(documents, query, DEFAULT_CONFIG, dictionary);
  const top = result.results.slice(0, 4).map((r) => `${r.doc.t} [${r.tier}] ${r.score.toFixed(2)} — ${r.reasons[0]?.label || "?"}`);
  console.log(`\n=== ${label} ===`);
  console.log(`query: ${JSON.stringify(query)}  low_confidence=${result.low_confidence}  relaxed=${result.relaxed}`);
  top.forEach((line) => console.log(`  ${line}`));
}

// A few hard assertions so this doubles as a smoke test, not just printed
// output someone has to eyeball.
function check(label, cond) {
  console.log(`\n[${cond ? "PASS" : "FAIL"}] ${label}`);
  if (!cond) allOk = false;
}

const exact = scoreCorpus(documents, "陽台番茄種植入門", DEFAULT_CONFIG, dictionary);
check("exact title query's top hit is the right document", exact.results[0]?.id === "balcony-tomato-basics");

const alias = scoreCorpus(documents, "牛油果", DEFAULT_CONFIG, dictionary);
check("dictionary alias resolves to the avocado document", alias.results[0]?.id === "avocado-ripeness");

const seriesQuery = scoreCorpus(documents, "支架與側芽", DEFAULT_CONFIG, dictionary);
const pestsHit = seriesQuery.results.find((r) => r.id === "balcony-tomato-pests");
check(
  "same-series companion doc rides in via a relation, not a direct match",
  !!pestsHit && pestsHit.channels.includes("relation"),
);

const crossDomain = scoreCorpus(documents, "護照申請流程", DEFAULT_CONFIG, dictionary);
check("an unrelated query is honestly disclosed as low-confidence", crossDomain.low_confidence === true);

console.log(`\n${allOk ? "All demo checks passed." : "Some demo checks FAILED — see above."}`);
process.exit(allOk ? 0 : 1);
