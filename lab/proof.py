#!/usr/bin/env python3
"""
Tamper-evidence for the Momentum lab's weekly positions.

For each week in lab/positions.csv this writes lab/proofs/<week>.csv (that week's rows, written
once and never changed) and an OpenTimestamps proof, lab/proofs/<week>.csv.ots: the file's
SHA-256 is submitted to public OpenTimestamps calendars, which anchor it in a Bitcoin block
within a few hours. Later runs "upgrade" pending proofs to the confirmed Bitcoin attestation.

Anyone can check a week: download <week>.csv and <week>.csv.ots and drop both on
https://opentimestamps.org, or run `ots verify <week>.csv.ots`.

lab/proofs/index.csv (append-only) records each week's SHA-256, when it was stamped and the
GitHub Actions run that did it; lab/proofs/status.json holds the Bitcoin block once confirmed.

Usage: python lab/proof.py
"""

import csv
import hashlib
import io
import json
import os
import time

from opentimestamps.calendar import RemoteCalendar
from opentimestamps.core.notary import BitcoinBlockHeaderAttestation, PendingAttestation
from opentimestamps.core.op import OpAppend, OpSHA256
from opentimestamps.core.serialize import StreamDeserializationContext, StreamSerializationContext
from opentimestamps.core.timestamp import DetachedTimestampFile

HERE = os.path.dirname(os.path.abspath(__file__))
PROOFS = os.path.join(HERE, "proofs")
CALENDARS = ["https://a.pool.opentimestamps.org", "https://b.pool.opentimestamps.org",
             "https://a.pool.eternitywall.com"]


def week_files(src=os.path.join(HERE, "positions.csv"), dest=PROOFS):
    """Split a positions file into one file per week (first column), created once and never rewritten."""
    if not os.path.exists(src):
        return []
    text = open(src, encoding="utf-8").read().splitlines()
    header, rows = text[0], text[1:]
    weeks = {}
    for line in rows:
        weeks.setdefault(next(csv.reader(io.StringIO(line)))[0], []).append(line)
    os.makedirs(dest, exist_ok=True)
    out = []
    for week, lines in sorted(weeks.items()):
        p = os.path.join(dest, f"{week}.csv")
        if not os.path.exists(p):
            with open(p, "w", encoding="utf-8", newline="\n") as f:
                f.write("\n".join([header] + lines) + "\n")
        out.append((week, p))
    return out


def day_files(src_dir, dest):
    """Copy each finished day's positions file (byte for byte) into the proofs folder, once."""
    if not os.path.isdir(src_dir):
        return []
    os.makedirs(dest, exist_ok=True)
    out = []
    for name in sorted(os.listdir(src_dir)):
        if not name.endswith(".csv"):
            continue
        p = os.path.join(dest, name)
        if not os.path.exists(p):
            with open(os.path.join(src_dir, name), "rb") as a, open(p, "wb") as b:
                b.write(a.read())
        out.append((name[:-4], p))
    return out


def stamp(path):
    with open(path, "rb") as f:
        ft = DetachedTimestampFile.from_fd(OpSHA256(), f)
    # a random nonce keeps the file's hash private until the file itself is published
    tip = ft.timestamp.ops.add(OpAppend(os.urandom(16))).ops.add(OpSHA256())
    ok = 0
    for url in CALENDARS:
        try:
            tip.merge(RemoteCalendar(url).submit(tip.msg, timeout=20))
            ok += 1
        except Exception as e:
            print(f"  calendar {url} failed: {e}")
    if not ok:
        raise RuntimeError("no OpenTimestamps calendar accepted the stamp")
    with open(path + ".ots", "wb") as f:
        ft.serialize(StreamSerializationContext(f))
    return ok


def load(path):
    with open(path + ".ots", "rb") as f:
        return DetachedTimestampFile.deserialize(StreamDeserializationContext(f))


def upgrade(ft):
    """Ask calendars for the finished (Bitcoin-anchored) path of every pending attestation."""
    changed = False

    def walk(ts):
        nonlocal changed
        for att in list(ts.attestations):
            if isinstance(att, PendingAttestation):
                try:
                    ts.merge(RemoteCalendar(att.uri).get_timestamp(ts.msg, timeout=20))
                    changed = True
                except Exception:
                    pass          # not confirmed yet (normal for the first few hours)
        for child in ts.ops.values():
            walk(child)
    walk(ft.timestamp)
    return changed


def bitcoin_block(ft):
    heights = [a.height for _, a in ft.timestamp.all_attestations() if isinstance(a, BitcoinBlockHeaderAttestation)]
    return min(heights) if heights else None


def main():
    stamp_set(PROOFS, week_files())                                                    # M1, weekly
    stamp_set(os.path.join(PROOFS, "carry"),                                           # F1 carry, weekly
              week_files(os.path.join(HERE, "carry", "positions.csv"), os.path.join(PROOFS, "carry")))
    stamp_set(os.path.join(PROOFS, "seesaw"),                                          # F2 seesaw, daily
              day_files(os.path.join(HERE, "seesaw", "positions"), os.path.join(PROOFS, "seesaw")))


def stamp_set(folder, items):
    """Stamp new files, upgrade pending proofs, and keep folder/index.csv + folder/status.json."""
    if not items:
        return
    idx_path = os.path.join(folder, "index.csv")
    os.makedirs(folder, exist_ok=True)
    index = list(csv.DictReader(open(idx_path, encoding="utf-8"))) if os.path.exists(idx_path) else []
    known = {r["week"] for r in index}
    st_path = os.path.join(folder, "status.json")
    status = json.load(open(st_path)) if os.path.exists(st_path) else {}
    run = os.environ.get("GITHUB_RUN_ID")
    run_url = f"{os.environ.get('GITHUB_SERVER_URL')}/{os.environ.get('GITHUB_REPOSITORY')}/actions/runs/{run}" if run else ""

    for week, path in items:
        sha = hashlib.sha256(open(path, "rb").read()).hexdigest()
        if not os.path.exists(path + ".ots"):
            n = stamp(path)
            print(f"stamped {week} ({sha[:12]}…) with {n} calendar(s)")
        if week not in known:
            index.append({"week": week, "sha256": sha, "stamped_utc": time.strftime("%Y-%m-%d %H:%M", time.gmtime()),
                          "run_url": run_url})
        if (status.get(week) or {}).get("bitcoin_block") is None:
            ft = load(path)
            if upgrade(ft):
                with open(path + ".ots", "wb") as f:
                    ft.serialize(StreamSerializationContext(f))
            status[week] = {"bitcoin_block": bitcoin_block(ft)}
            print(f"{week}: " + (f"anchored in Bitcoin block {status[week]['bitcoin_block']}"
                                 if status[week]["bitcoin_block"] else "waiting for a Bitcoin block"))

    with open(idx_path, "w", encoding="utf-8", newline="\n") as f:
        w = csv.DictWriter(f, fieldnames=["week", "sha256", "stamped_utc", "run_url"], lineterminator="\n")
        w.writeheader()
        w.writerows(index)
    with open(st_path, "w", newline="\n") as f:          # LF everywhere, so Windows and Linux runs agree
        json.dump(status, f, indent=1, sort_keys=True)


if __name__ == "__main__":
    main()
