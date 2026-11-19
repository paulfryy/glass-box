"""Replay saved paper accounts and check that the published positions are append-only."""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

import m1_exposure as exposure

LAB = Path(__file__).resolve().parent
SCRIPTS = ("m1", "carry", "seesaw")
WEEKLY_POSITIONS = ("positions.csv", "carry/positions.csv", "controls/basket/positions.csv",
                    "controls/longonly/positions.csv", exposure.LOG)
EQUITY = ("equity.csv", "carry/equity.csv", "seesaw/equity.csv", "controls/basket/equity.csv",
          "controls/longonly/equity.csv")


def position_files(lab):
    paths = [lab / name for name in WEEKLY_POSITIONS]
    paths += sorted((lab / "seesaw/positions").glob("*.csv"))
    return {p.relative_to(lab).as_posix(): p.read_bytes() for p in paths if p.exists()}


def check_history(base, current):
    repo = LAB.parent
    names = subprocess.check_output(["git", "ls-tree", "-r", "--name-only", base, "lab"], cwd=repo).decode().splitlines()
    for name in names:
        relative = name.removeprefix("lab/")
        weekly = relative in WEEKLY_POSITIONS
        if not weekly and not (relative.startswith("seesaw/positions/") and relative.endswith(".csv")):
            continue
        previous = subprocess.check_output(["git", "show", f"{base}:{name}"], cwd=repo).splitlines()
        latest = current.get(relative, b"").splitlines()
        if (latest[:len(previous)] if weekly else latest) != previous:
            raise ValueError(f"Changed a previously published position in {name}")


def check_equity(replayed):
    files = list(EQUITY)
    files.extend(f"{exposure.REPLAY}/{name}/equity.csv" for name in exposure.ACCOUNTS)
    if (LAB / exposure.LOG).exists():
        files.extend(f"{exposure.FOLDER}/{name}/equity.csv" for name in exposure.ACCOUNTS)
    for name in files:
        actual = pd.read_csv(replayed / name).set_index("date")
        expected = pd.read_csv(LAB / name).set_index("date")
        for fields, tolerance in ((["gross", "funding", "cost", "net"], 0.00000101),
                                  (["equity", "btc_equity"], 0.0101)):
            try:
                pd.testing.assert_frame_equal(actual[fields], expected[fields], check_exact=False,
                                              rtol=0, atol=tolerance)
            except AssertionError as exc:
                raise ValueError(f"Paper equity did not replay from saved inputs in {name}") from exc
    return len(files)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", help="Git revision containing the previous published positions")
    args = parser.parse_args()
    published = position_files(LAB)
    if args.base:
        check_history(args.base, published)
    with TemporaryDirectory(prefix="strategy-lab-replay-") as directory:
        replayed = Path(directory) / "lab"
        shutil.copytree(LAB, replayed, ignore=shutil.ignore_patterns("__pycache__", "proofs"))
        for name in SCRIPTS:
            subprocess.run([sys.executable, replayed / f"{name}.py", "--offline"], check=True)
        if position_files(replayed) != published:
            raise ValueError("Offline replay changed the published position record")
        count = check_equity(replayed)
    print(f"Verified {count} paper accounts and preserved the published position records.")


if __name__ == "__main__":
    main()
