"""Logging helpers for edge_agent.

All loggers live under the ``edge_agent`` namespace.  By default no handlers
are attached, so library users only see output if they explicitly configure
the ``edge_agent`` logger (standard Python library practice).
"""

from __future__ import annotations

import logging

_ROOT_LOGGER_NAME = "edge_agent"


def get_logger(name: str) -> logging.Logger:
    """Return a child logger under the ``edge_agent`` namespace.

    >>> get_logger("agent.weather-bot")
    <Logger edge_agent.agent.weather-bot (WARNING)>
    """
    return logging.getLogger(f"{_ROOT_LOGGER_NAME}.{name}")
