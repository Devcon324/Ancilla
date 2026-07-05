"""Aligned label: value log lines for the voice assistant."""
import logging

LABEL_WIDTH = 12


def line(label: str, message: str) -> str:
    """Format 'Label:     message' with a fixed-width label column."""
    if not label.endswith(":"):
        label = f"{label}:"
    return f"{label:<{LABEL_WIDTH}}{message}"


def info(logger: logging.Logger, label: str, message: str) -> None:
    logger.info(line(label, message))


def warning(logger: logging.Logger, label: str, message: str) -> None:
    logger.warning(line(label, message))
