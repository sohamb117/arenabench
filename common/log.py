import logging
import sys
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import structlog
from structlog.stdlib import BoundLogger


def _ts_processor(
    _: structlog.types.WrappedLogger,
    __: str,
    event_dict: structlog.types.EventDict,
) -> structlog.types.EventDict:
    event_dict["ts"] = datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    return event_dict


def configure(level: str = "INFO") -> None:
    logging.basicConfig(stream=sys.stderr, format="%(message)s", level=level.upper(), force=True)
    structlog.configure(
        processors=[
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            _ts_processor,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> BoundLogger:
    return structlog.stdlib.get_logger(name)


@contextmanager
def jsonl_sink(path: Path) -> Generator[None, None, None]:
    stream = path.open("a", encoding="utf-8", buffering=1)
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        yield
    finally:
        handler.flush()
        root.removeHandler(handler)
        handler.close()
