from __future__ import annotations

import hashlib
from collections.abc import Iterator

import pytest

from lantmateriet.klient import LmKlient
from lantmateriet.nycklar import Nycklar
from tests.lantmateriet.falsk_server import HEMLIGHET, KLIENT_ID, FalskLm


@pytest.fixture
def lm() -> Iterator[FalskLm]:
    s = FalskLm().start()
    try:
        yield s
    finally:
        s.stop()


class Klocka:
    """Falsk klocka och sömn: sömn flyttar tiden framåt och noteras."""

    def __init__(self) -> None:
        self.t = 1000.0
        self.somn: list[float] = []

    def nu(self) -> float:
        return self.t

    def sov(self, s: float) -> None:
        self.somn.append(s)
        self.t += s


@pytest.fixture
def klocka() -> Klocka:
    return Klocka()


@pytest.fixture
def klient(lm: FalskLm, klocka: Klocka) -> LmKlient:
    return LmKlient(
        Nycklar(id=KLIENT_ID, hemlighet=HEMLIGHET),
        lm.tjanster(),
        sov=klocka.sov,
        nu=klocka.nu,
        max_forsok=3,
        timeout_s=10.0,
    )


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()
