"""Gemensamt fel för webb-API:t."""

from __future__ import annotations


class FlodenWebbFel(Exception):
    """Fel i webb-API:ts indata, routing eller exekvering."""

    def __init__(self, meddelande: str, *, status_kod: int = 400) -> None:
        super().__init__(meddelande)
        self.status_kod = status_kod
