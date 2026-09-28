// Local test of checkCoin() against real coins (uses HELIUS_API_KEY from the environment).
import { checkCoin } from "./src/index.js";
const env = { HELIUS_API_KEY: process.env.HELIUS_API_KEY };
for (const m of process.argv.slice(2)) {
  const r = await checkCoin(env, m);
  if (!r.ok) { console.log(m.slice(0, 6), "ERROR", r.error); continue; }
  console.log(`\n${r.name} ($${r.symbol}) verdict=${r.verdict} fails=${r.failCount} progress=${(r.curveProgress * 100).toFixed(1)}% graduated=${r.graduated}`);
  for (const f of r.flags) console.log(`  ${f.ok ? "PASS" : "FAIL"} [${f.severity}] ${f.check} -- ${f.detail}`);
}
