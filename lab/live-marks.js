const marks = Array.from(document.querySelectorAll("[data-live-mark]"), node => ({
  node,
  ...JSON.parse(node.dataset.liveMark),
}));
const dollars = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
const time = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit", timeZoneName: "short" });

async function refreshMarks() {
  if (document.hidden) return;
  try {
    const response = await fetch("https://api.glassbox.gripe/prices", { signal: AbortSignal.timeout(10000) });
    if (!response.ok) throw new Error("Quote request failed");
    const { ok, asOf, prices } = await response.json();
    const timestamp = asOf * 1000;
    const age = Date.now() - timestamp;
    if (!ok || !prices || !Number.isFinite(age) || age < -60000 || age > 180000) {
      throw new Error("Quotes are stale or unavailable");
    }
    for (const { node, balance, day, holdings } of marks) {
      let change = 0;
      let covered = 0;
      for (const [symbol, weight, close] of holdings) {
        const ratio = prices[symbol] / close;
        // Reject implausible cross-exchange ratios, including different contract units.
        if (ratio < 0.5 || ratio > 2 || !Number.isFinite(ratio)) continue;
        change += weight * (ratio - 1);
        covered++;
      }
      if (covered === holdings.length && timestamp >= Date.parse(day) + 86400000) {
        node.textContent = `Live estimate ${dollars.format(balance * (1 + change))} · ${time.format(timestamp)}`;
      } else {
        node.textContent = covered < holdings.length
          ? `Live estimate unavailable · ${covered}/${holdings.length} quotes usable`
          : "Live estimate waiting for quotes after the close";
      }
    }
  } catch {
    for (const { node } of marks) node.textContent = "Live estimate unavailable";
  }
}

if (marks.length) {
  refreshMarks();
  setInterval(refreshMarks, 60000);
}
