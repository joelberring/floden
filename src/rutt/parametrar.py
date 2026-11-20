"""Parametrar för ruttanalys enligt kontrakt K7/v1 (`docs/DATAKONTRAKT.md`).

Gränsvärden för fordon, entréer, gångväg och insatstidsmål bestäms med
räddningstjänsten. Skarpa körningar vägrar köra när K7 innehåller `null`/`None`
eller när testvärden (`[rutt_test]`) används.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gemensamt.konfig import KonfigFel, las_konfig

KONTRAKT_K7 = "K7/v1"
OBLIGATORISKA_FORDON: tuple[str, ...] = (
    "personbil",
    "ambulans",
    "tung_raddningsbil",
)
OBLIGATORISKA_INSATSTIDSMAL: tuple[str, ...] = (
    "sjukhus",
    "vardboende",
    "skola",
    "forskola",
)


class RuttParametrarFel(Exception):
    """Fel vid läsning, validering eller användning av K7-ruttparametrar."""


@dataclass(frozen=True)
class FordonParametrar:
    """Fordonsspecifika framkomlighetsparametrar enligt K7/v1."""

    gransdjup_m: float | None = None
    hastighet_per_djup: tuple[tuple[float, float], ...] = ()

    def till_dict(self) -> dict[str, Any]:
        """Serialiserar fordonsparametrarna till K7/v1-lexikon."""
        return {
            "gransdjup_m": self.gransdjup_m,
            "hastighet_per_djup": [[float(d), float(f)] for d, f in self.hastighet_per_djup],
        }

    @classmethod
    def fran_dict(cls, data: Any, *, namn: str = "fordon") -> FordonParametrar:
        """Skapar `FordonParametrar` från deserialiserat JSON/TOML-objekt."""
        if not isinstance(data, Mapping):
            raise RuttParametrarFel(f"Fältet 'fordon.{namn}' måste vara ett objekt.")

        raw_grans = data.get("gransdjup_m")
        gransdjup_m: float | None
        if raw_grans is None:
            gransdjup_m = None
        elif isinstance(raw_grans, bool) or not isinstance(raw_grans, int | float):
            raise RuttParametrarFel(
                f"Fältet 'fordon.{namn}.gransdjup_m' måste vara ett tal eller null."
            )
        else:
            gransdjup_m = float(raw_grans)

        raw_kurva = data.get("hastighet_per_djup", [])
        if not isinstance(raw_kurva, Sequence) or isinstance(raw_kurva, str | bytes):
            raise RuttParametrarFel(
                f"Fältet 'fordon.{namn}.hastighet_per_djup' måste vara en lista av par."
            )
        punkter: list[tuple[float, float]] = []
        for idx, punkt in enumerate(raw_kurva):
            if (
                not isinstance(punkt, Sequence)
                or isinstance(punkt, str | bytes)
                or len(punkt) != 2
            ):
                raise RuttParametrarFel(
                    f"Ogiltig punkt i 'fordon.{namn}.hastighet_per_djup[{idx}]': "
                    "förväntade [djup_m, faktor]."
                )
            d_raw, f_raw = punkt[0], punkt[1]
            if (
                isinstance(d_raw, bool)
                or isinstance(f_raw, bool)
                or not isinstance(d_raw, int | float)
                or not isinstance(f_raw, int | float)
            ):
                raise RuttParametrarFel(
                    f"Ogiltiga tal i 'fordon.{namn}.hastighet_per_djup[{idx}]'."
                )
            punkter.append((float(d_raw), float(f_raw)))

        return cls(gransdjup_m=gransdjup_m, hastighet_per_djup=tuple(punkter))


def _standard_fordon() -> dict[str, FordonParametrar]:
    return {namn: FordonParametrar() for namn in OBLIGATORISKA_FORDON}


def _standard_insatstidsmal() -> dict[str, float | None]:
    return {namn: None for namn in OBLIGATORISKA_INSATSTIDSMAL}


@dataclass(frozen=True)
class RuttParametrarK7:
    """Ruttparametrar enligt kontrakt K7/v1 (`docs/DATAKONTRAKT.md`)."""

    kontrakt: str = KONTRAKT_K7
    fordon: dict[str, FordonParametrar] = field(default_factory=_standard_fordon)
    entre_gransdjup_m: float | None = None
    gangavstand_max_m: float | None = None
    gangvag_djup_max_m: float | None = None
    gangvag_fara_max_m2_s: float | None = None
    insatstidsmal_min: dict[str, float | None] = field(
        default_factory=_standard_insatstidsmal
    )
    ar_testvarde: bool = False

    def till_dict(self) -> dict[str, Any]:
        """Serialiserar ruttparametrarna till ett K7/v1-lexikon."""
        payload: dict[str, Any] = {
            "kontrakt": self.kontrakt,
            "fordon": {namn: f.till_dict() for namn, f in self.fordon.items()},
            "entre_gransdjup_m": self.entre_gransdjup_m,
            "gangavstand_max_m": self.gangavstand_max_m,
            "gangvag_djup_max_m": self.gangvag_djup_max_m,
            "gangvag_fara_max_m2_s": self.gangvag_fara_max_m2_s,
            "insatstidsmal_min": dict(self.insatstidsmal_min),
        }
        if self.ar_testvarde:
            payload["ar_testvarde"] = True
        return payload

    @classmethod
    def fran_dict(cls, data: Any, *, ar_testvarde: bool | None = None) -> RuttParametrarK7:
        """Skapar `RuttParametrarK7` från ett deserialiserat K7/v1-objekt."""
        if not isinstance(data, Mapping):
            raise RuttParametrarFel("K7-parametrar måste vara ett JSON-objekt.")

        kontrakt = str(data.get("kontrakt", KONTRAKT_K7))
        if kontrakt != KONTRAKT_K7:
            raise RuttParametrarFel(
                f"Ogiltigt K7-kontrakt '{kontrakt}', förväntade '{KONTRAKT_K7}'."
            )

        raw_fordon = data.get("fordon")
        if not isinstance(raw_fordon, Mapping):
            raise RuttParametrarFel("K7-parametrar saknar objektet 'fordon'.")
        fordon: dict[str, FordonParametrar] = {
            str(namn): FordonParametrar.fran_dict(f_data, namn=str(namn))
            for namn, f_data in raw_fordon.items()
        }

        raw_mal = data.get("insatstidsmal_min")
        if not isinstance(raw_mal, Mapping):
            raise RuttParametrarFel("K7-parametrar saknar objektet 'insatstidsmal_min'.")
        insatstidsmal: dict[str, float | None] = {}
        for namn, varde in raw_mal.items():
            if varde is None:
                insatstidsmal[str(namn)] = None
            elif isinstance(varde, bool) or not isinstance(varde, int | float):
                raise RuttParametrarFel(
                    f"Fältet 'insatstidsmal_min.{namn}' måste vara ett tal eller null."
                )
            else:
                insatstidsmal[str(namn)] = float(varde)

        test_flagga = (
            bool(ar_testvarde)
            if ar_testvarde is not None
            else bool(data.get("ar_testvarde", False))
        )

        return cls(
            kontrakt=kontrakt,
            fordon=fordon,
            entre_gransdjup_m=_las_valfritt_tal(
                data.get("entre_gransdjup_m"), "entre_gransdjup_m"
            ),
            gangavstand_max_m=_las_valfritt_tal(
                data.get("gangavstand_max_m"), "gangavstand_max_m"
            ),
            gangvag_djup_max_m=_las_valfritt_tal(
                data.get("gangvag_djup_max_m"), "gangvag_djup_max_m"
            ),
            gangvag_fara_max_m2_s=_las_valfritt_tal(
                data.get("gangvag_fara_max_m2_s"), "gangvag_fara_max_m2_s"
            ),
            insatstidsmal_min=insatstidsmal,
            ar_testvarde=test_flagga,
        )


def _las_valfritt_tal(varde: Any, faltnamn: str) -> float | None:
    if varde is None:
        return None
    if isinstance(varde, bool) or not isinstance(varde, int | float):
        raise RuttParametrarFel(f"Fältet '{faltnamn}' måste vara ett tal eller null.")
    return float(varde)


def las_ruttparametrar_k7(sokvag: str | Path) -> RuttParametrarK7:
    """Läser K7/v1-ruttparametrar från en JSON-fil."""
    fil = Path(sokvag)
    try:
        text = fil.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuttParametrarFel(f"Kunde inte läsa K7-parameterfilen {fil}: {exc}") from exc

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuttParametrarFel(f"Ogiltig JSON i K7-parameterfilen {fil}: {exc}") from exc

    return RuttParametrarK7.fran_dict(data)


def skriv_ruttparametrar_k7(param: RuttParametrarK7, sokvag: str | Path) -> Path:
    """Skriver K7/v1-ruttparametrar som formaterad JSON till `sokvag`."""
    mal = Path(sokvag)
    mal.parent.mkdir(parents=True, exist_ok=True)
    innehall = json.dumps(param.till_dict(), indent=2, ensure_ascii=False) + "\n"
    mal.write_text(innehall, encoding="utf-8")
    return mal


def las_testparametrar_fran_konfig(
    konfig_fil: str | Path = "config.toml",
) -> RuttParametrarK7:
    """Läser testparametrar från `[rutt_test]` i `config.toml` med `ar_testvarde=True`."""
    try:
        konfig = las_konfig(bas_fil=konfig_fil, lokal_fil=None)
    except KonfigFel as exc:
        raise RuttParametrarFel(
            f"Kunde inte läsa testparametrar från {konfig_fil}: {exc}"
        ) from exc

    raw_test = konfig.get("rutt_test")
    if not isinstance(raw_test, Mapping):
        raise RuttParametrarFel(
            f"Sektionen '[rutt_test]' saknas i konfigurationsfilen {konfig_fil}."
        )

    payload: dict[str, Any] = dict(raw_test)
    payload.setdefault("kontrakt", KONTRAKT_K7)
    return RuttParametrarK7.fran_dict(payload, ar_testvarde=True)


def validera_parametrar(
    param: RuttParametrarK7,
    *,
    skarp: bool = True,
    tillat_testvarden: bool = False,
) -> None:
    """Validerar K7-parametrar och listar exakt vilka fält som saknas.

    Args:
        param: Ruttparametrar enligt K7/v1.
        skarp: Om `True` (standard) vägras körning om `param.ar_testvarde` är `True`.
        tillat_testvarden: Alternativ flagga för att uttryckligen tillåta `[rutt_test]`
            i testkörningar även om `skarp` inte satts till `False`.

    Raises:
        RuttParametrarFel: Om fält är `None`/tomma, ogiltiga, eller om testvärden
            används vid skarp körning.
    """
    if param.kontrakt != KONTRAKT_K7:
        raise RuttParametrarFel(
            f"Ogiltigt K7-kontrakt '{param.kontrakt}', förväntade '{KONTRAKT_K7}'."
        )

    effektivt_skarp = skarp and not tillat_testvarden
    saknade: list[str] = []
    ogiltiga: list[str] = []

    for fordon_namn in OBLIGATORISKA_FORDON:
        if fordon_namn not in param.fordon:
            saknade.append(f"fordon.{fordon_namn}.gransdjup_m")
            saknade.append(f"fordon.{fordon_namn}.hastighet_per_djup")
            continue
        f_param = param.fordon[fordon_namn]
        if f_param.gransdjup_m is None:
            saknade.append(f"fordon.{fordon_namn}.gransdjup_m")
        elif not math.isfinite(f_param.gransdjup_m) or f_param.gransdjup_m <= 0.0:
            ogiltiga.append(
                f"fordon.{fordon_namn}.gransdjup_m ({f_param.gransdjup_m})"
            )

        if not f_param.hastighet_per_djup:
            saknade.append(f"fordon.{fordon_namn}.hastighet_per_djup")
        else:
            _validera_hastighetskurva(
                f_param.hastighet_per_djup,
                f"fordon.{fordon_namn}.hastighet_per_djup",
                ogiltiga,
            )

    skalara_falt: tuple[tuple[str, float | None, bool], ...] = (
        ("entre_gransdjup_m", param.entre_gransdjup_m, False),
        ("gangavstand_max_m", param.gangavstand_max_m, False),
        ("gangvag_djup_max_m", param.gangvag_djup_max_m, True),
        ("gangvag_fara_max_m2_s", param.gangvag_fara_max_m2_s, True),
    )
    for faltnamn, varde, tillat_noll in skalara_falt:
        if varde is None:
            saknade.append(faltnamn)
        elif not math.isfinite(varde) or (varde < 0.0 if tillat_noll else varde <= 0.0):
            ogiltiga.append(f"{faltnamn} ({varde})")

    for mal_namn in OBLIGATORISKA_INSATSTIDSMAL:
        mal_varde = param.insatstidsmal_min.get(mal_namn)
        if mal_varde is None:
            saknade.append(f"insatstidsmal_min.{mal_namn}")
        elif not math.isfinite(mal_varde) or mal_varde <= 0.0:
            ogiltiga.append(f"insatstidsmal_min.{mal_namn} ({mal_varde})")

    if saknade:
        saknade_str = ", ".join(saknade)
        raise RuttParametrarFel(
            "K7-parametrar saknar fastställda värden och kan inte köras: "
            f"[{saknade_str}]"
        )

    if ogiltiga:
        ogiltiga_str = ", ".join(ogiltiga)
        raise RuttParametrarFel(
            f"Ogiltiga värden i K7-parametrar: [{ogiltiga_str}]"
        )

    if effektivt_skarp and param.ar_testvarde:
        raise RuttParametrarFel(
            "Testvärden från [rutt_test] får inte användas för skarp körning."
        )


def _validera_hastighetskurva(
    kurva: tuple[tuple[float, float], ...],
    faltnamn: str,
    ogiltiga: list[str],
) -> None:
    tidigare_djup = -math.inf
    for djup_m, faktor in kurva:
        if not math.isfinite(djup_m) or djup_m < 0.0:
            ogiltiga.append(f"{faltnamn} (negativt/ogiltigt djup {djup_m})")
            return
        if djup_m < tidigare_djup:
            ogiltiga.append(f"{faltnamn} (djup måste vara sorterade)")
            return
        if not math.isfinite(faktor) or faktor < 0.0 or faktor > 1.0:
            ogiltiga.append(f"{faltnamn} (hastighetsfaktor {faktor} utanför [0, 1])")
            return
        tidigare_djup = djup_m


def berakna_hastighetsfaktor(fordon: FordonParametrar, djup_m: float) -> float:
    """Beräknar hastighetsfaktor `[0.0, 1.0]` vid vattendjup `djup_m`.

    Returnerar `0.0` om `djup_m >= gransdjup_m`, annars styckvis linjär
    interpolation i `fordon.hastighet_per_djup`.
    """
    if fordon.gransdjup_m is None:
        raise RuttParametrarFel("Fordonets 'gransdjup_m' saknas (None).")
    if not math.isfinite(djup_m):
        raise RuttParametrarFel(f"Ogiltigt vattendjup: {djup_m}")
    if djup_m >= fordon.gransdjup_m:
        return 0.0
    if not fordon.hastighet_per_djup:
        raise RuttParametrarFel("Fordonets 'hastighet_per_djup' är tom.")

    eff_djup = max(0.0, float(djup_m))
    punkter = fordon.hastighet_per_djup

    if eff_djup <= punkter[0][0]:
        return float(punkter[0][1])

    for (d0, f0), (d1, f1) in zip(punkter[:-1], punkter[1:], strict=True):
        if eff_djup <= d1:
            if d1 <= d0:
                return float(f1)
            andel = (eff_djup - d0) / (d1 - d0)
            return float(f0 + andel * (f1 - f0))

    return float(punkter[-1][1])
