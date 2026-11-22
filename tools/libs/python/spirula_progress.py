#!/usr/bin/env python3
"""Relay Spirula output: pair matching as a progress bar, detail lines only with --verbose."""

import argparse
import re
import sys

from tqdm import tqdm

PAIR_PROGRESS = re.compile(r"^\[paires\]\s+(\d+)/(\d+)\b")


def relay(stream, verbose=False):
    bar = None
    try:
        for raw in stream:
            for line in raw.replace("\r", "\n").split("\n"):
                if not line.strip():
                    continue
                match = PAIR_PROGRESS.match(line)
                if match:
                    done, total = int(match.group(1)), int(match.group(2))
                    if bar is None or bar.total != total:
                        if bar is not None:
                            bar.close()
                        bar = tqdm(total=total, desc="Paires", unit="paire")
                    bar.n = done
                    bar.refresh()
                    if not verbose:
                        continue
                tqdm.write(line)
    finally:
        if bar is not None:
            bar.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    relay(sys.stdin, args.verbose)
    return 0


if __name__ == "__main__":
    sys.exit(main())
