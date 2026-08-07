"""Logging setup, applied once at application startup."""

import logging

# ASCII only: the Windows console defaults to cp1252 and mangles anything else.
_FORMAT = "%(asctime)s  %(levelname)-8s  %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=level.upper(),
        format=_FORMAT,
        datefmt=_DATE_FORMAT,
        force=True,
    )
