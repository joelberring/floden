"""Paket- och lagergränser för Flodens källkod (`src/`).

Säkerställer genom AST-analys att:
1. `src/luft/` (MIT-licens, se `docs/BESLUT.md`) aldrig importerar GPL-kod
   utanför `gemensamt`.
2. Baslagret `gemensamt` aldrig drar in domän- eller applikationspaket
   (bortsett från det uttryckligt listade arvet `gemensamt/cog.py -> testsvit.k1_io`).
3. De fem beräkningsdomänerna (`buller`, `dagsljus`, `ekologi`, `luft`,
   vattenklustret) inte korsimporterar varandra eller presentationslagren
   (`webb`, `floden_mcp`, `rapport`, `floden`).
4. CLI-ingången `floden` förblir toppnod utan inkommande importer från `src/`.
"""

from __future__ import annotations

import ast
from functools import cache
from pathlib import Path

_ROT = Path(__file__).resolve().parents[2]
_SRC = _ROT / "src"

_VATTEN_PAKET = frozenset({"vatten", "tvad", "sankgraf", "dynamik", "lapp", "mark_forluster"})
_PRESENTATION = frozenset({"webb", "floden_mcp", "rapport", "floden"})

# Kända, tillfälliga arvsimporter som ska avvecklas (se paket-och-ci-arkitektur.md).
# Nya undantag får inte läggas till utan arkitekturbeslut.
_KANDA_GEMENSAMT_UNDANTAG: frozenset[tuple[str, str]] = frozenset(
    {
        ("gemensamt/cog.py", "testsvit"),
    }
)


@cache
def _toppnivapaket() -> frozenset[str]:
    return frozenset(
        p.name
        for p in _SRC.iterdir()
        if p.is_dir() and not p.name.startswith((".", "__"))
    )


@cache
def _externa_importer(paket: str, alla_paket: frozenset[str]) -> tuple[tuple[str, int, str], ...]:
    """Returnerar (relativ_fil, rad, målpaket) för alla interna importer ut ur `paket`."""
    traffar: list[tuple[str, int, str]] = []
    paket_rot = _SRC / paket
    for pyfil in sorted(paket_rot.rglob("*.py")):
        rel = pyfil.relative_to(_SRC).as_posix()
        trad = ast.parse(pyfil.read_text(encoding="utf-8"), filename=rel)
        for nod in ast.walk(trad):
            if isinstance(nod, ast.Import):
                for alias in nod.names:
                    topp = alias.name.split(".")[0]
                    if topp in alla_paket and topp != paket:
                        traffar.append((rel, nod.lineno, topp))
            elif isinstance(nod, ast.ImportFrom) and nod.level == 0 and nod.module:
                topp = nod.module.split(".")[0]
                if topp in alla_paket and topp != paket:
                    traffar.append((rel, nod.lineno, topp))
    return tuple(traffar)


def _formatera_brott(brott: list[tuple[str, int, str]] | tuple[tuple[str, int, str], ...]) -> str:
    return "\n".join(f"  {fil}:{rad} -> {mal}" for fil, rad, mal in brott)


def test_luft_mit_importerar_bara_gemensamt() -> None:
    """`src/luft/` är MIT-licensierat och får aldrig importera GPL-paket."""
    alla = _toppnivapaket()
    tillatna = {"gemensamt"}
    brott = [
        (fil, rad, mal)
        for fil, rad, mal in _externa_importer("luft", alla)
        if mal not in tillatna
    ]
    assert not brott, (
        "MIT-paketet `luft` får bara importera `gemensamt`, hittade:\n"
        + _formatera_brott(brott)
    )


def test_gemensamt_importerar_inga_domaner_eller_appar() -> None:
    """Baslagret `gemensamt` får inte vara beroende av domän- eller applikationspaket."""
    alla = _toppnivapaket()
    brott = [
        (fil, rad, mal)
        for fil, rad, mal in _externa_importer("gemensamt", alla)
        if (fil, mal) not in _KANDA_GEMENSAMT_UNDANTAG
    ]
    assert not brott, (
        "Baslagret `gemensamt` har otillåtna uppåtimporter:\n"
        + _formatera_brott(brott)
    )


def test_mark_forluster_forblir_lovpaket() -> None:
    """`mark_forluster` är en fristående beräkningsmodul utan interna beroenden."""
    alla = _toppnivapaket()
    brott = _externa_importer("mark_forluster", alla)
    assert not brott, (
        "`mark_forluster` ska inte ha interna beroenden till andra paket:\n"
        + _formatera_brott(brott)
    )


def test_domaner_korsimporterar_inte_varandra_eller_presentation() -> None:
    """De fem beräkningsdomänerna får inte importera varandra eller webb/MCP/rapport/CLI."""
    alla = _toppnivapaket()
    forbjudet_per_paket: dict[str, frozenset[str]] = {
        "buller": frozenset({"dagsljus", "ekologi", "luft"}) | _VATTEN_PAKET | _PRESENTATION,
        "dagsljus": frozenset({"buller", "ekologi", "luft"}) | _VATTEN_PAKET | _PRESENTATION,
        "ekologi": frozenset({"buller", "dagsljus", "luft", "karna"})
        | _VATTEN_PAKET
        | _PRESENTATION,
    }
    for vatten_pkg in _VATTEN_PAKET:
        forbjudet_per_paket[vatten_pkg] = (
            frozenset({"buller", "dagsljus", "ekologi", "luft"}) | _PRESENTATION
        )

    brott: list[tuple[str, int, str]] = []
    for paket, forbjudna in sorted(forbjudet_per_paket.items()):
        for fil, rad, mal in _externa_importer(paket, alla):
            if mal in forbjudna:
                brott.append((fil, rad, mal))

    assert not brott, (
        "Otillåtna korsdomän- eller presentationsimporter upptäckta:\n"
        + _formatera_brott(brott)
    )


def test_floden_cli_har_inga_inkommande_importer() -> None:
    """CLI-ingången `floden` ska vara toppnod och aldrig importeras av andra paket."""
    alla = _toppnivapaket()
    brott: list[tuple[str, int, str]] = []
    for paket in sorted(alla - {"floden"}):
        for fil, rad, mal in _externa_importer(paket, alla):
            if mal == "floden":
                brott.append((fil, rad, mal))
    assert not brott, (
        "Andra paket får inte importera CLI-paketet `floden`:\n"
        + _formatera_brott(brott)
    )
