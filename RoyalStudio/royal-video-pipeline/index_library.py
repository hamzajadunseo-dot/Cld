"""Stage 1: scan the Royal Media Library and build/update data/index.db.

    python index_library.py                 # full incremental run
    python index_library.py --limit 20      # only process 20 new/changed files (quick test)
    python index_library.py --no-captions   # skip the Claude API step
"""
import argparse

from royal.config import load_config
from royal.indexer import Indexer


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="path to config.yaml (default: next to this script)")
    ap.add_argument("--limit", type=int, help="process at most N new/changed files this run")
    ap.add_argument("--no-captions", action="store_true", help="skip Claude captions")
    args = ap.parse_args()
    Indexer(load_config(args.config)).run(limit=args.limit, captions=not args.no_captions)


if __name__ == "__main__":
    main()
