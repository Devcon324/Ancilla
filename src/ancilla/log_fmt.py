"""Pipe-aligned log lines: timestamp | label | message."""
import logging

LABEL_WIDTH = 10
DATE_FMT = "%Y-%m-%d %H:%M:%S"


def line(label: str, message: str) -> str:
    """Format 'Label      | message' with a fixed-width label column."""
    label = label.rstrip(":").strip()
    return f"{label:<{LABEL_WIDTH}} | {message}"


def _is_structured(message: str) -> bool:
    return (
        len(message) >= LABEL_WIDTH + 3
        and message[LABEL_WIDTH : LABEL_WIDTH + 3] == " | "
    )


class PipeFormatter(logging.Formatter):
    """Emit: YYYY-MM-DD HH:MM:SS | Label      | message"""

    def format(self, record: logging.LogRecord) -> str:
        ts = self.formatTime(record, self.datefmt)
        msg = record.getMessage()
        body = msg if _is_structured(msg) else line("Log", msg)
        return f"{ts} | {body}"


def setup_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(PipeFormatter(datefmt=DATE_FMT))
    logging.basicConfig(level=level, handlers=[handler], force=True)


def info(logger: logging.Logger, label: str, message: str) -> None:
    logger.info(line(label, message))


def warning(logger: logging.Logger, label: str, message: str) -> None:
    logger.warning(line(label, message))
