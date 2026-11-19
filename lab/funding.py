"""Repair late monthly archives and describe funding coverage for held positions."""

from concurrent.futures import ThreadPoolExecutor

import pandas as pd


def refresh(rates, closes, start, fetch_month, now=None):
    """Fetch completed months at the contract level, including holes in older months."""
    now = now if now is not None else pd.Timestamp.now(tz="UTC")
    current_month = now.normalize().replace(day=1)
    closes = closes.loc[(closes.index >= start) & (closes.index < current_month)]
    missing = closes.notna() & rates.reindex_like(closes).isna()
    jobs = [(symbol, month.strftime("%Y-%m"))
            for month, block in missing.groupby(pd.Grouper(freq="MS"))
            for symbol in block.columns[block.any()]]

    def fetch(job):
        symbol, month = job
        return fetch_month(symbol, month)

    patches = {}
    with ThreadPoolExecutor(8) as pool:
        for (symbol, month), result in zip(jobs, pool.map(fetch, jobs)):
            if result is not None and len(result):
                inside_month = result[result.index.strftime("%Y-%m") == month]
                if len(inside_month):
                    patches.setdefault(symbol, []).append(inside_month)
    if patches:
        incoming = pd.DataFrame({symbol: pd.concat(parts) for symbol, parts in patches.items()})
        rates = incoming.combine_first(rates)
    return rates.sort_index()


def coverage(held, rates):
    """Count missing contract-days and report the last fully covered close before any gap."""
    missing = held.fillna(0).abs().gt(1e-9) & rates.reindex_like(held).isna()
    gaps = missing.any(axis=1)
    latest_archive = rates.dropna(how="all").index.max() if len(rates) else pd.NaT
    covered = held.index[~gaps.cummax() & (held.index <= latest_archive)]
    return {
        "funding_through": f"{covered[-1]:%Y-%m-%d}" if len(covered) else "-",
        "funding_missing_contract_days": str(int(missing.to_numpy().sum())),
    }
