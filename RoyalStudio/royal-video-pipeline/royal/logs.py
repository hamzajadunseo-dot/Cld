"""Console + file logging. Every run appends to data/logs/<name>.log."""
import logging
import sys
from pathlib import Path


def get_logger(name: str, data_dir: Path) -> logging.Logger:
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    logger.propagate = False  # some libraries configure the root logger; avoid double lines
    fmt = logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s", "%H:%M:%S")
    try:
        sys.stdout.reconfigure(errors="replace")   # names with non-Latin letters must never break a run
    except Exception:
        pass
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    logger.addHandler(console)
    fh = logging.FileHandler(log_dir / f"{name}.log", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s"))
    logger.addHandler(fh)
    return logger
