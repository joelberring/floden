"""Deterministiskt svenskt och stockholmskt regnbibliotek enligt kontrakt K4/v1."""

from __future__ import annotations

import csv
import math
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gemensamt.hash import sha256_fil

KONTRAKT_K4 = "K4/v1"
STANDARD_ATERKOMSTTIDER_AR: tuple[float, ...] = (
    2.0,
    5.0,
    10.0,
    20.0,
    50.0,
    100.0,
    200.0,
    500.0,
)
STANDARD_KLIMATFAKTORER: tuple[float, ...] = (1.0, 1.2, 1.24, 1.25, 1.3, 1.4)
STANDARD_CDS_TOPPFAKTOR: float = 0.37
STANDARD_VARAKTIGHET_MIN: float = 360.0
STANDARD_TIDSSTEG_MIN: float = 5.0

DAHLSTROM_MIN_VARAKTIGHET_MIN: float = 5.0
DAHLSTROM_MAX_VARAKTIGHET_MIN: float = 1440.0
L_PER_S_HA_TILL_MM_H: float = 0.36


class RegnFel(Exception):
    """Fel vid validering, beräkning eller I/O av regndata och IDF-tabeller."""


def _validera_positivt_tal(varde: float, namn: str) -> float:
    """Validerar att `varde` är ett ändligt tal strikt större än 0 (ej bool)."""
    if isinstance(varde, bool) or not isinstance(varde, int | float):
        raise RegnFel(f"{namn} måste vara ett tal, fick {varde!r}.")
    val = float(varde)
    if not math.isfinite(val) or val <= 0.0:
        raise RegnFel(f"{namn} måste vara ändligt och > 0, fick {val}.")
    return val


def _validera_icke_negativt_tal(varde: float, namn: str) -> float:
    """Validerar att `varde` är ett ändligt tal >= 0 (ej bool)."""
    if isinstance(varde, bool) or not isinstance(varde, int | float):
        raise RegnFel(f"{namn} måste vara ett tal, fick {varde!r}.")
    val = float(varde)
    if not math.isfinite(val) or val < 0.0:
        raise RegnFel(f"{namn} måste vara ändligt och >= 0, fick {val}.")
    return val


def dahlstrom_2010_intensitet_l_s_ha(
    varaktighet_min: float,
    aterkomsttid_ar: float,
    *,
    klimatfaktor: float = 1.0,
) -> float:
    """Beräknar regnintensitet i l/(s·ha) enligt Dahlström (2010) / Svenskt Vatten P104 & P110.

    Formel:
        T_man = 12.0 * aterkomsttid_ar
        i = 190.0 * T_man^(1/3) * ln(varaktighet_min) / varaktighet_min^0.98 + 2.0

    Giltighetsintervall: 5.0 <= varaktighet_min <= 1440.0 min, aterkomsttid_ar > 0,
    klimatfaktor > 0.
    """
    d_min = _validera_positivt_tal(varaktighet_min, "varaktighet_min")
    t_ar = _validera_positivt_tal(aterkomsttid_ar, "aterkomsttid_ar")
    kf = _validera_positivt_tal(klimatfaktor, "klimatfaktor")

    if d_min < DAHLSTROM_MIN_VARAKTIGHET_MIN or d_min > DAHLSTROM_MAX_VARAKTIGHET_MIN:
        raise RegnFel(
            f"varaktighet_min={d_min} ligger utanför giltighetsintervallet för "
            f"Dahlström (2010) [{DAHLSTROM_MIN_VARAKTIGHET_MIN}, "
            f"{DAHLSTROM_MAX_VARAKTIGHET_MIN}] min."
        )

    t_man = 12.0 * t_ar
    bas_l_s_ha = (
        190.0
        * math.pow(t_man, 1.0 / 3.0)
        * math.log(d_min)
        / math.pow(d_min, 0.98)
        + 2.0
    )
    return bas_l_s_ha * kf


def dahlstrom_2010_intensitet_mm_h(
    varaktighet_min: float,
    aterkomsttid_ar: float,
    *,
    klimatfaktor: float = 1.0,
) -> float:
    """Beräknar regnintensitet i mm/h enligt Dahlström (2010) / Svenskt Vatten P104 & P110."""
    return (
        dahlstrom_2010_intensitet_l_s_ha(
            varaktighet_min,
            aterkomsttid_ar,
            klimatfaktor=klimatfaktor,
        )
        * L_PER_S_HA_TILL_MM_H
    )


@dataclass(frozen=True)
class IdfTabell:
    """Lokal eller officiell IDF-tabell (Intensity–Duration–Frequency) i mm/h.

    Används för att läsa in officiella tabeller (t.ex. Stockholmsregn 2024 eller
    lokala SMHI-serier) med bilinjär interpolation i `(ln(D), ln(T)) -> ln(i)`.
    """

    namn: str
    kalla: str
    varaktigheter_min: tuple[float, ...]
    aterkomsttider_ar: tuple[float, ...]
    intensiteter_mm_h: tuple[tuple[float, ...], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.namn, str) or not self.namn.strip():
            raise RegnFel("IdfTabell.namn måste vara en icke-tom sträng.")
        if not isinstance(self.kalla, str) or not self.kalla.strip():
            raise RegnFel("IdfTabell.kalla måste vara en icke-tom sträng.")

        if not self.varaktigheter_min:
            raise RegnFel("IdfTabell.varaktigheter_min får inte vara tom.")
        if not self.aterkomsttider_ar:
            raise RegnFel("IdfTabell.aterkomsttider_ar får inte vara tom.")

        varakt = tuple(
            _validera_positivt_tal(v, f"varaktigheter_min[{idx}]")
            for idx, v in enumerate(self.varaktigheter_min)
        )
        for idx in range(1, len(varakt)):
            if varakt[idx] <= varakt[idx - 1]:
                raise RegnFel(
                    "IdfTabell.varaktigheter_min måste vara strikt stigande: "
                    f"{varakt[idx]} <= {varakt[idx - 1]}."
                )

        aterkomst = tuple(
            _validera_positivt_tal(t, f"aterkomsttider_ar[{idx}]")
            for idx, t in enumerate(self.aterkomsttider_ar)
        )
        for idx in range(1, len(aterkomst)):
            if aterkomst[idx] <= aterkomst[idx - 1]:
                raise RegnFel(
                    "IdfTabell.aterkomsttider_ar måste vara strikt stigande: "
                    f"{aterkomst[idx]} <= {aterkomst[idx - 1]}."
                )

        if len(self.intensiteter_mm_h) != len(varakt):
            raise RegnFel(
                f"Antal rader i intensiteter_mm_h ({len(self.intensiteter_mm_h)}) "
                f"matchar inte varaktigheter_min ({len(varakt)})."
            )

        norm_rader: list[tuple[float, ...]] = []
        for rad_idx, rad in enumerate(self.intensiteter_mm_h):
            if len(rad) != len(aterkomst):
                raise RegnFel(
                    f"Rad {rad_idx} i intensiteter_mm_h har längd {len(rad)}, "
                    f"förväntade {len(aterkomst)}."
                )
            norm_rad = tuple(
                _validera_positivt_tal(val, f"intensiteter_mm_h[{rad_idx}][{kol_idx}]")
                for kol_idx, val in enumerate(rad)
            )
            norm_rader.append(norm_rad)

        object.__setattr__(self, "varaktigheter_min", varakt)
        object.__setattr__(self, "aterkomsttider_ar", aterkomst)
        object.__setattr__(self, "intensiteter_mm_h", tuple(norm_rader))

    def intensitet_mm_h(
        self,
        varaktighet_min: float,
        aterkomsttid_ar: float,
        *,
        klimatfaktor: float = 1.0,
    ) -> float:
        """Interpolerar intensitet (mm/h) bilinjärt i `(ln(D), ln(T)) -> ln(i)`.

        Kastar `RegnFel` om `varaktighet_min` eller `aterkomsttid_ar` ligger utanför
        tabellens gränser (ingen tyst extrapolering).
        """
        d_min = _validera_positivt_tal(varaktighet_min, "varaktighet_min")
        t_ar = _validera_positivt_tal(aterkomsttid_ar, "aterkomsttid_ar")
        kf = _validera_positivt_tal(klimatfaktor, "klimatfaktor")

        d_min_lo = self.varaktigheter_min[0]
        d_min_hi = self.varaktigheter_min[-1]
        if d_min < d_min_lo or d_min > d_min_hi:
            raise RegnFel(
                f"varaktighet_min={d_min} ligger utanför IDF-tabellens intervall "
                f"[{d_min_lo}, {d_min_hi}] min."
            )

        t_lo = self.aterkomsttider_ar[0]
        t_hi = self.aterkomsttider_ar[-1]
        if t_ar < t_lo or t_ar > t_hi:
            raise RegnFel(
                f"aterkomsttid_ar={t_ar} ligger utanför IDF-tabellens intervall "
                f"[{t_lo}, {t_hi}] år."
            )

        i0, i1, w_d = _hitta_log_intervall(self.varaktigheter_min, d_min)
        j0, j1, w_t = _hitta_log_intervall(self.aterkomsttider_ar, t_ar)

        ln_q00 = math.log(self.intensiteter_mm_h[i0][j0])
        ln_q01 = math.log(self.intensiteter_mm_h[i0][j1])
        ln_q10 = math.log(self.intensiteter_mm_h[i1][j0])
        ln_q11 = math.log(self.intensiteter_mm_h[i1][j1])

        ln_d0 = (1.0 - w_t) * ln_q00 + w_t * ln_q01
        ln_d1 = (1.0 - w_t) * ln_q10 + w_t * ln_q11
        ln_val = (1.0 - w_d) * ln_d0 + w_d * ln_d1
        return math.exp(ln_val) * kf


def _hitta_log_intervall(axlar: tuple[float, ...], x: float) -> tuple[int, int, float]:
    """Returnerar `(idx0, idx1, vikt)` för logaritmisk interpolation av `x` inom `axlar`."""
    if len(axlar) == 1 or x <= axlar[0]:
        return 0, 0, 0.0
    if x >= axlar[-1]:
        sista = len(axlar) - 1
        return sista, sista, 0.0

    for idx in range(len(axlar) - 1):
        x0 = axlar[idx]
        x1 = axlar[idx + 1]
        if x == x0:
            return idx, idx, 0.0
        if x == x1:
            return idx + 1, idx + 1, 0.0
        if x0 < x < x1:
            ln_x0 = math.log(x0)
            ln_x1 = math.log(x1)
            vikt = (math.log(x) - ln_x0) / (ln_x1 - ln_x0)
            return idx, idx + 1, vikt

    sista = len(axlar) - 1
    return sista, sista, 0.0


def las_idf_tabell_csv(
    sokvag: str | Path,
    *,
    namn: str | None = None,
    kalla: str = "Lokal IDF-tabell",
) -> IdfTabell:
    """Läser en IDF-tabell från CSV med första kolumn `varaktighet_min` och återkomsttider i år."""
    fil = Path(sokvag)
    if not fil.is_file():
        raise RegnFel(f"IDF-tabellfil saknas: {fil}")

    try:
        with fil.open("r", encoding="utf-8", newline="") as fh:
            reader = csv.reader(fh)
            rader = list(reader)
    except (OSError, UnicodeDecodeError) as exc:
        raise RegnFel(f"Kunde inte läsa IDF-tabellen {fil}: {exc}") from exc

    if len(rader) < 2:
        raise RegnFel(f"IDF-tabellen {fil} måste ha en rubrikrad och minst en datarad.")

    rubrik = [kol.strip() for kol in rader[0]]
    if len(rubrik) < 2 or rubrik[0] != "varaktighet_min":
        raise RegnFel(
            f"IDF-tabellen {fil} måste ha första kolumn 'varaktighet_min' följt av "
            f"återkomsttider i år, fick {rubrik}."
        )

    try:
        aterkomsttider = tuple(float(kol) for kol in rubrik[1:])
    except ValueError as exc:
        raise RegnFel(f"Ogiltiga återkomsttidskolumner i {fil}: {rubrik[1:]}") from exc

    varaktigheter: list[float] = []
    matris: list[tuple[float, ...]] = []
    for rad_nr, rad in enumerate(rader[1:], start=2):
        if not rad or all(not cell.strip() for cell in rad):
            continue
        if len(rad) != len(rubrik):
            raise RegnFel(
                f"Rad {rad_nr} i {fil} har {len(rad)} kolumner, förväntade {len(rubrik)}."
            )
        try:
            d_val = float(rad[0].strip())
            i_vals = tuple(float(cell.strip()) for cell in rad[1:])
        except ValueError as exc:
            raise RegnFel(f"Ogiltigt tal på rad {rad_nr} i {fil}: {rad}") from exc
        varaktigheter.append(d_val)
        matris.append(i_vals)

    if not varaktigheter:
        raise RegnFel(f"IDF-tabellen {fil} saknar datarader.")

    eff_namn = namn.strip() if namn is not None and namn.strip() else fil.stem
    return IdfTabell(
        namn=eff_namn,
        kalla=kalla,
        varaktigheter_min=tuple(varaktigheter),
        aterkomsttider_ar=aterkomsttider,
        intensiteter_mm_h=tuple(matris),
    )


def skriv_idf_tabell_csv(sokvag: str | Path, tabell: IdfTabell) -> Path:
    """Skriver en `IdfTabell` som CSV med första kolumn `varaktighet_min`."""
    if not isinstance(tabell, IdfTabell):
        raise RegnFel("tabell måste vara ett IdfTabell-objekt.")
    mal = Path(sokvag)
    mal.parent.mkdir(parents=True, exist_ok=True)
    with mal.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        rubrik = ["varaktighet_min", *(_formatera_axelvarde(t) for t in tabell.aterkomsttider_ar)]
        writer.writerow(rubrik)
        for d_min, rad in zip(tabell.varaktigheter_min, tabell.intensiteter_mm_h, strict=True):
            writer.writerow([_formatera_axelvarde(d_min), *(f"{val:.6f}" for val in rad)])
    return mal


def _formatera_axelvarde(val: float) -> str:
    """Formaterar ett heltal utan decimaler och flyttal kompakt för CSV-rubrik/axel."""
    if val.is_integer():
        return str(int(val))
    return f"{val:g}"


@dataclass(frozen=True)
class RegnSerieK4:
    """Deterministisk punktregnserie enligt kontrakt K4/v1."""

    regn_id: str
    namn: str
    typ: str
    aterkomsttid_ar: float | None
    klimatfaktor: float
    varaktighet_min: float
    tidssteg_min: float
    toppfaktor: float | None
    statistikkalla: str
    tid_s: tuple[float, ...]
    intensitet_mm_h: tuple[float, ...]
    total_djup_mm: float
    max_intensitet_mm_h: float
    beskrivning: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.regn_id, str) or not self.regn_id.strip():
            raise RegnFel("RegnSerieK4.regn_id måste vara en icke-tom sträng.")
        if not isinstance(self.namn, str) or not self.namn.strip():
            raise RegnFel("RegnSerieK4.namn måste vara en icke-tom sträng.")
        if self.typ not in ("cds", "block", "csv"):
            raise RegnFel(
                f"Ogiltig regntyp '{self.typ}', måste vara 'cds', 'block' eller 'csv'."
            )
        if self.aterkomsttid_ar is not None:
            _validera_positivt_tal(self.aterkomsttid_ar, "aterkomsttid_ar")
        _validera_positivt_tal(self.klimatfaktor, "klimatfaktor")
        _validera_positivt_tal(self.varaktighet_min, "varaktighet_min")
        _validera_positivt_tal(self.tidssteg_min, "tidssteg_min")
        if self.toppfaktor is not None:
            tf = _validera_positivt_tal(self.toppfaktor, "toppfaktor")
            if tf >= 1.0:
                raise RegnFel(f"toppfaktor måste ligga i (0, 1), fick {tf}.")
        if not isinstance(self.statistikkalla, str) or not self.statistikkalla.strip():
            raise RegnFel("RegnSerieK4.statistikkalla måste vara en icke-tom sträng.")

        if len(self.tid_s) < 2:
            raise RegnFel("RegnSerieK4.tid_s måste innehålla minst två tidpunkter.")
        if len(self.tid_s) != len(self.intensitet_mm_h):
            raise RegnFel("tid_s och intensitet_mm_h måste ha samma längd.")

        tider = tuple(
            _validera_icke_negativt_tal(t, f"tid_s[{idx}]")
            for idx, t in enumerate(self.tid_s)
        )
        for idx in range(1, len(tider)):
            if tider[idx] < tider[idx - 1]:
                raise RegnFel(
                    f"tid_s får inte vara avtagande: {tider[idx]} < {tider[idx - 1]}."
                )
        intens = tuple(
            _validera_icke_negativt_tal(i, f"intensitet_mm_h[{idx}]")
            for idx, i in enumerate(self.intensitet_mm_h)
        )
        _validera_icke_negativt_tal(self.total_djup_mm, "total_djup_mm")
        _validera_icke_negativt_tal(self.max_intensitet_mm_h, "max_intensitet_mm_h")

        object.__setattr__(self, "tid_s", tider)
        object.__setattr__(self, "intensitet_mm_h", intens)

    def till_k4_rader(self) -> list[tuple[float, float]]:
        """Returnerar K4-rader `[(tid_s, intensitet_mm_h), ...]`."""
        return list(zip(self.tid_s, self.intensitet_mm_h, strict=True))

    def till_dict(self) -> dict[str, Any]:
        """Serialiserar serien inklusive K4-metadata till ett JSON-kompatibelt lexikon."""
        return {
            "kontrakt": KONTRAKT_K4,
            "regn_id": self.regn_id,
            "namn": self.namn,
            "typ": self.typ,
            "aterkomsttid_ar": self.aterkomsttid_ar,
            "klimatfaktor": self.klimatfaktor,
            "varaktighet_min": self.varaktighet_min,
            "tidssteg_min": self.tidssteg_min,
            "toppfaktor": self.toppfaktor,
            "statistikkalla": self.statistikkalla,
            "total_djup_mm": self.total_djup_mm,
            "max_intensitet_mm_h": self.max_intensitet_mm_h,
            "beskrivning": self.beskrivning,
            "tid_s": list(self.tid_s),
            "intensitet_mm_h": list(self.intensitet_mm_h),
        }


def _integrera_trapets_mm(
    tid_s: Sequence[float],
    intensitet_mm_h: Sequence[float],
) -> float:
    """Integrerar `(tid_s, intensitet_mm_h)` med trapetsregel och returnerar totalt djup (mm)."""
    total_mm = 0.0
    for idx in range(len(tid_s) - 1):
        dt_h = (tid_s[idx + 1] - tid_s[idx]) / 3600.0
        medel = 0.5 * (intensitet_mm_h[idx] + intensitet_mm_h[idx + 1])
        total_mm += medel * dt_h
    return total_mm


def _intensitet_fran_kalla(
    varaktighet_min: float,
    aterkomsttid_ar: float,
    *,
    klimatfaktor: float,
    idf_tabell: IdfTabell | None,
) -> float:
    """Hämtar medelintensitet (mm/h) från angiven `IdfTabell` eller Dahlström (2010)."""
    if idf_tabell is not None:
        return idf_tabell.intensitet_mm_h(
            varaktighet_min,
            aterkomsttid_ar,
            klimatfaktor=klimatfaktor,
        )
    return dahlstrom_2010_intensitet_mm_h(
        varaktighet_min,
        aterkomsttid_ar,
        klimatfaktor=klimatfaktor,
    )


def _ordna_cds_block(
    block_intensiteter: Sequence[float],
    toppfaktor: float,
) -> list[float]:
    """Ordnar fallande blockintensiteter i `n` tidssteg kring toppindex `round(r * (n - 1))`.

    Placerar toppblocket `I_1` vid `p = round(toppfaktor * (n - 1))` och fördelar
    efterföljande block växelvis till vänster och höger proportionellt mot `toppfaktor`
    respektive `1 - toppfaktor` så att både toppposition och asymmetri bevaras exakt.
    """
    n = len(block_intensiteter)
    if n == 1:
        return [float(block_intensiteter[0])]

    p = round(toppfaktor * (n - 1))
    p = max(0, min(n - 1, p))

    ut: list[float] = [0.0] * n
    ut[p] = float(block_intensiteter[0])

    vanster = p - 1
    hoger = p + 1
    # Ackumulerad brist mot ideal proportionell fördelning avgör nästa sida.
    n_v = 0
    n_h = 0

    for idx in range(1, n):
        val = float(block_intensiteter[idx])
        kan_v = vanster >= 0
        kan_h = hoger < n
        if not kan_v and not kan_h:
            break
        if kan_v and not kan_h:
            ut[vanster] = val
            vanster -= 1
            n_v += 1
            continue
        if kan_h and not kan_v:
            ut[hoger] = val
            hoger += 1
            n_h += 1
            continue

        # Jämför hur långt vänster resp. höger ligger efter målandel r resp. (1 - r)
        n_placerade = n_v + n_h + 1
        brist_v = toppfaktor * n_placerade - n_v
        brist_h = (1.0 - toppfaktor) * n_placerade - n_h
        ar_lika = math.isclose(brist_v, brist_h, abs_tol=1e-12)
        if brist_v > brist_h or (ar_lika and toppfaktor < 0.5):
            ut[vanster] = val
            vanster -= 1
            n_v += 1
        else:
            ut[hoger] = val
            hoger += 1
            n_h += 1

    return ut


def generera_cds_regn(
    aterkomsttid_ar: float,
    *,
    klimatfaktor: float = 1.0,
    varaktighet_min: float = STANDARD_VARAKTIGHET_MIN,
    tidssteg_min: float = STANDARD_TIDSSTEG_MIN,
    toppfaktor: float = STANDARD_CDS_TOPPFAKTOR,
    idf_tabell: IdfTabell | None = None,
    regn_id: str | None = None,
    namn: str | None = None,
    statistikkalla: str | None = None,
    beskrivning: str = "",
) -> RegnSerieK4:
    """Genererar ett exakt volymbevarande CDS-regn (Chicago Design Storm) i K4-format.

    Varje styckvis konstant block `[t_k, t_{k+1}]` med intensitet `I_k` representeras
    som ändpunktsparet `(t_k, I_k), (t_{k+1}, I_k)` så att trapetsintegration i
    `sankgraf.fyllning.berakna_total_regn_m` och `dynamik.nodmodell.skapa_regn_intervall`
    bevarar total regnvolym identiskt (`fel < 1e-12`).
    """
    t_ar = _validera_positivt_tal(aterkomsttid_ar, "aterkomsttid_ar")
    kf = _validera_positivt_tal(klimatfaktor, "klimatfaktor")
    d_tot = _validera_positivt_tal(varaktighet_min, "varaktighet_min")
    dt_min = _validera_positivt_tal(tidssteg_min, "tidssteg_min")
    tf = _validera_positivt_tal(toppfaktor, "toppfaktor")
    if tf >= 1.0:
        raise RegnFel(f"toppfaktor måste ligga strikt i (0, 1), fick {tf}.")

    kvot = d_tot / dt_min
    n_steg = round(kvot)
    if n_steg < 1 or abs(kvot - n_steg) > 1e-9:
        raise RegnFel(
            f"varaktighet_min ({d_tot}) måste vara en jämn multipel av "
            f"tidssteg_min ({dt_min})."
        )

    if idf_tabell is None and dt_min < DAHLSTROM_MIN_VARAKTIGHET_MIN:
        raise RegnFel(
            f"tidssteg_min ({dt_min}) måste vara >= {DAHLSTROM_MIN_VARAKTIGHET_MIN} min "
            "vid användning av Dahlström (2010)."
        )

    dt_h = dt_min / 60.0
    dt_s = dt_min * 60.0

    kum_djup_mm: list[float] = [0.0]
    for k in range(1, n_steg + 1):
        d_k = k * dt_min
        i_k = _intensitet_fran_kalla(
            d_k,
            t_ar,
            klimatfaktor=kf,
            idf_tabell=idf_tabell,
        )
        v_k = i_k * (d_k / 60.0)
        if v_k < kum_djup_mm[-1] - 1e-12:
            raise RegnFel(
                f"Icke-monoton kumulativ regnvolym vid {d_k} min: {v_k} < {kum_djup_mm[-1]}."
            )
        kum_djup_mm.append(max(kum_djup_mm[-1], v_k))

    block_intens: list[float] = [
        (kum_djup_mm[k] - kum_djup_mm[k - 1]) / dt_h for k in range(1, n_steg + 1)
    ]
    ordnade_intens = _ordna_cds_block(block_intens, tf)

    tid_s_lista: list[float] = []
    intens_lista: list[float] = []
    for k, i_val in enumerate(ordnade_intens):
        t0 = k * dt_s
        t1 = (k + 1) * dt_s
        tid_s_lista.extend((t0, t1))
        intens_lista.extend((i_val, i_val))

    total_djup_mm = _integrera_trapets_mm(tid_s_lista, intens_lista)
    max_i = max(ordnade_intens)

    eff_kalla = (
        statistikkalla
        if statistikkalla is not None
        else (
            idf_tabell.kalla
            if idf_tabell is not None
            else "Dahlström (2010) / Svenskt Vatten P104 & P110"
        )
    )
    eff_id = regn_id or (
        f"cds_T{_formatera_axelvarde(t_ar)}_D{_formatera_axelvarde(d_tot)}"
        f"_kf{_formatera_axelvarde(kf)}"
    )
    eff_namn = namn or (
        f"CDS {_formatera_axelvarde(t_ar)}-årsregn "
        f"({_formatera_axelvarde(d_tot)} min, kf={_formatera_axelvarde(kf)})"
    )

    return RegnSerieK4(
        regn_id=eff_id,
        namn=eff_namn,
        typ="cds",
        aterkomsttid_ar=t_ar,
        klimatfaktor=kf,
        varaktighet_min=d_tot,
        tidssteg_min=dt_min,
        toppfaktor=tf,
        statistikkalla=eff_kalla,
        tid_s=tuple(tid_s_lista),
        intensitet_mm_h=tuple(intens_lista),
        total_djup_mm=total_djup_mm,
        max_intensitet_mm_h=max_i,
        beskrivning=beskrivning,
    )


def generera_blockregn(
    aterkomsttid_ar: float,
    *,
    klimatfaktor: float = 1.0,
    varaktighet_min: float = STANDARD_VARAKTIGHET_MIN,
    idf_tabell: IdfTabell | None = None,
    regn_id: str | None = None,
    namn: str | None = None,
    statistikkalla: str | None = None,
    beskrivning: str = "",
) -> RegnSerieK4:
    """Genererar ett konstant blockregn över `[0, varaktighet_min * 60]` s i K4-format."""
    t_ar = _validera_positivt_tal(aterkomsttid_ar, "aterkomsttid_ar")
    kf = _validera_positivt_tal(klimatfaktor, "klimatfaktor")
    d_tot = _validera_positivt_tal(varaktighet_min, "varaktighet_min")

    i_mm_h = _intensitet_fran_kalla(
        d_tot,
        t_ar,
        klimatfaktor=kf,
        idf_tabell=idf_tabell,
    )
    slut_s = d_tot * 60.0
    tid_s = (0.0, slut_s)
    intens = (i_mm_h, i_mm_h)
    total_djup_mm = _integrera_trapets_mm(tid_s, intens)

    eff_kalla = (
        statistikkalla
        if statistikkalla is not None
        else (
            idf_tabell.kalla
            if idf_tabell is not None
            else "Dahlström (2010) / Svenskt Vatten P104 & P110"
        )
    )
    eff_id = regn_id or (
        f"block_T{_formatera_axelvarde(t_ar)}_D{_formatera_axelvarde(d_tot)}"
        f"_kf{_formatera_axelvarde(kf)}"
    )
    eff_namn = namn or (
        f"Blockregn {_formatera_axelvarde(t_ar)}-årsregn "
        f"({_formatera_axelvarde(d_tot)} min, kf={_formatera_axelvarde(kf)})"
    )

    return RegnSerieK4(
        regn_id=eff_id,
        namn=eff_namn,
        typ="block",
        aterkomsttid_ar=t_ar,
        klimatfaktor=kf,
        varaktighet_min=d_tot,
        tidssteg_min=d_tot,
        toppfaktor=None,
        statistikkalla=eff_kalla,
        tid_s=tid_s,
        intensitet_mm_h=intens,
        total_djup_mm=total_djup_mm,
        max_intensitet_mm_h=i_mm_h,
        beskrivning=beskrivning,
    )


def skriv_regn_k4_csv(sokvag: str | Path, serie: RegnSerieK4) -> Path:
    """Skriver en `RegnSerieK4` till K4-CSV (`tid_s,intensitet_mm_h`)."""
    if not isinstance(serie, RegnSerieK4):
        raise RegnFel("serie måste vara ett RegnSerieK4-objekt.")
    mal = Path(sokvag)
    mal.parent.mkdir(parents=True, exist_ok=True)
    with mal.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["tid_s", "intensitet_mm_h"])
        for t_s, i_mm_h in serie.till_k4_rader():
            writer.writerow([f"{t_s:.3f}", f"{i_mm_h:.6f}"])
    return mal


def las_regn_k4_csv(
    sokvag: str | Path,
    *,
    regn_id: str | None = None,
    namn: str | None = None,
    statistikkalla: str = "K4 CSV",
) -> RegnSerieK4:
    """Läser en punktregnfil i K4-CSV-format (`tid_s,intensitet_mm_h`) till `RegnSerieK4`."""
    fil = Path(sokvag)
    if not fil.is_file():
        raise RegnFel(f"K4-regnfil saknas: {fil}")

    try:
        with fil.open("r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            if reader.fieldnames != ["tid_s", "intensitet_mm_h"]:
                raise RegnFel(
                    f"Ogiltiga kolumner i {fil}: {reader.fieldnames}, "
                    "förväntade ['tid_s', 'intensitet_mm_h']."
                )
            tider: list[float] = []
            intensiteter: list[float] = []
            for rad_nr, row in enumerate(reader, start=2):
                try:
                    t_val = float(row["tid_s"])
                    i_val = float(row["intensitet_mm_h"])
                except (TypeError, ValueError) as exc:
                    raise RegnFel(f"Ogiltigt tal på rad {rad_nr} i {fil}: {row}") from exc
                tider.append(t_val)
                intensiteter.append(i_val)
    except (OSError, UnicodeDecodeError) as exc:
        raise RegnFel(f"Kunde inte läsa K4-regnfilen {fil}: {exc}") from exc

    if len(tider) < 2:
        raise RegnFel(f"K4-regnfilen {fil} måste innehålla minst två tidpunkter.")

    total_s = tider[-1] - tider[0]
    if total_s <= 0.0:
        raise RegnFel(f"K4-regnfilen {fil} måste ha positiv total varaktighet.")

    positiva_dt = [
        tider[idx] - tider[idx - 1]
        for idx in range(1, len(tider))
        if tider[idx] > tider[idx - 1]
    ]
    min_dt_s = min(positiva_dt) if positiva_dt else total_s
    total_djup_mm = _integrera_trapets_mm(tider, intensiteter)
    max_i = max(intensiteter)

    eff_id = regn_id.strip() if regn_id is not None and regn_id.strip() else fil.stem
    eff_namn = namn.strip() if namn is not None and namn.strip() else eff_id

    return RegnSerieK4(
        regn_id=eff_id,
        namn=eff_namn,
        typ="csv",
        aterkomsttid_ar=None,
        klimatfaktor=1.0,
        varaktighet_min=total_s / 60.0,
        tidssteg_min=min_dt_s / 60.0,
        toppfaktor=None,
        statistikkalla=statistikkalla,
        tid_s=tuple(tider),
        intensitet_mm_h=tuple(intensiteter),
        total_djup_mm=total_djup_mm,
        max_intensitet_mm_h=max_i,
        beskrivning=f"Inläst från K4-CSV ({fil.name}).",
    )


@dataclass(frozen=True)
class RegnForval:
    """Namngivet svenskt eller stockholmskt regnförval för skyfallsmodellering."""

    regn_id: str
    namn: str
    region: str
    typ: str
    aterkomsttid_ar: float
    klimatfaktor: float
    varaktighet_min: float
    tidssteg_min: float
    toppfaktor: float
    statistikkalla: str
    beskrivning: str

    def __post_init__(self) -> None:
        if not isinstance(self.regn_id, str) or not self.regn_id.strip():
            raise RegnFel("RegnForval.regn_id måste vara en icke-tom sträng.")
        if not isinstance(self.namn, str) or not self.namn.strip():
            raise RegnFel("RegnForval.namn måste vara en icke-tom sträng.")
        if self.region not in ("Stockholm", "Sverige"):
            raise RegnFel(
                f"RegnForval.region måste vara 'Stockholm' eller 'Sverige', fick {self.region!r}."
            )
        if self.typ not in ("cds", "block"):
            raise RegnFel(f"RegnForval.typ måste vara 'cds' eller 'block', fick {self.typ!r}.")
        _validera_positivt_tal(self.aterkomsttid_ar, "aterkomsttid_ar")
        _validera_positivt_tal(self.klimatfaktor, "klimatfaktor")
        _validera_positivt_tal(self.varaktighet_min, "varaktighet_min")
        _validera_positivt_tal(self.tidssteg_min, "tidssteg_min")
        tf = _validera_positivt_tal(self.toppfaktor, "toppfaktor")
        if tf >= 1.0:
            raise RegnFel(f"RegnForval.toppfaktor måste ligga i (0, 1), fick {tf}.")
        if not isinstance(self.statistikkalla, str) or not self.statistikkalla.strip():
            raise RegnFel("RegnForval.statistikkalla måste vara en icke-tom sträng.")

    def generera(self, *, idf_tabell: IdfTabell | None = None) -> RegnSerieK4:
        """Genererar en komplett K4-serie för detta förval."""
        eff_kalla = idf_tabell.kalla if idf_tabell is not None else self.statistikkalla
        if self.typ == "block":
            return generera_blockregn(
                self.aterkomsttid_ar,
                klimatfaktor=self.klimatfaktor,
                varaktighet_min=self.varaktighet_min,
                idf_tabell=idf_tabell,
                regn_id=self.regn_id,
                namn=self.namn,
                statistikkalla=eff_kalla,
                beskrivning=self.beskrivning,
            )
        return generera_cds_regn(
            self.aterkomsttid_ar,
            klimatfaktor=self.klimatfaktor,
            varaktighet_min=self.varaktighet_min,
            tidssteg_min=self.tidssteg_min,
            toppfaktor=self.toppfaktor,
            idf_tabell=idf_tabell,
            regn_id=self.regn_id,
            namn=self.namn,
            statistikkalla=eff_kalla,
            beskrivning=self.beskrivning,
        )

    def till_dict(self) -> dict[str, Any]:
        """Returnerar förvalets specifikation inklusive beräknat totaldjup och toppintensitet."""
        serie = self.generera()
        return {
            "regn_id": self.regn_id,
            "namn": self.namn,
            "region": self.region,
            "typ": self.typ,
            "aterkomsttid_ar": self.aterkomsttid_ar,
            "klimatfaktor": self.klimatfaktor,
            "varaktighet_min": self.varaktighet_min,
            "tidssteg_min": self.tidssteg_min,
            "toppfaktor": self.toppfaktor,
            "statistikkalla": self.statistikkalla,
            "beskrivning": self.beskrivning,
            "total_djup_mm": serie.total_djup_mm,
            "max_intensitet_mm_h": serie.max_intensitet_mm_h,
        }


def _bygg_inbyggda_forval() -> dict[str, RegnForval]:
    """Konstruerar deterministiska inbyggda regnförval för Stockholm och Sverige."""
    forval: dict[str, RegnForval] = {}

    stockholm_tider = (20.0, 50.0, 100.0, 200.0, 500.0)
    stockholm_kf = (
        ("kf124", 1.24, "Stockholms klimatanpassad faktor 1.24"),
        ("kf140", 1.4, "Stockholms högre klimatfaktor 1.40"),
    )
    for kf_tag, kf_val, kf_text in stockholm_kf:
        for t_ar in stockholm_tider:
            t_int = int(t_ar)
            rid = f"stockholm_cds_6h_{t_int}ar_{kf_tag}"
            forval[rid] = RegnForval(
                regn_id=rid,
                namn=f"Stockholm {t_int}-årsregn 6 h CDS (kf {kf_val:.2f})",
                region="Stockholm",
                typ="cds",
                aterkomsttid_ar=t_ar,
                klimatfaktor=kf_val,
                varaktighet_min=STANDARD_VARAKTIGHET_MIN,
                tidssteg_min=STANDARD_TIDSSTEG_MIN,
                toppfaktor=STANDARD_CDS_TOPPFAKTOR,
                statistikkalla=(
                    f"Dahlström (2010) / Svenskt Vatten P110 med klimatfaktor {kf_val:.2f} "
                    "(6 h CDS r=0.37; ersätts via IdfTabell när officiell "
                    "Stockholmsregn 2024-tabell laddas in)"
                ),
                beskrivning=(
                    f"6 h CDS-regn (r=0.37, dt=5 min) för {t_int} års återkomsttid med "
                    f"{kf_text}, baserat på Dahlström (2010) / Svenskt Vatten P110 tills "
                    "lokal officiell Stockholmsregn 2024-tabell laddas in via IdfTabell."
                ),
            )

    sverige_tider = (10.0, 20.0, 50.0, 100.0, 200.0, 500.0)
    sverige_kf = (
        ("kf100", 1.0, "utan klimatfaktor (1.00)"),
        ("kf120", 1.2, "klimatfaktor 1.20"),
        ("kf125", 1.25, "klimatfaktor 1.25"),
        ("kf130", 1.3, "klimatfaktor 1.30"),
        ("kf140", 1.4, "klimatfaktor 1.40"),
    )
    for kf_tag, kf_val, kf_text in sverige_kf:
        for t_ar in sverige_tider:
            t_int = int(t_ar)
            rid = f"sverige_cds_6h_{t_int}ar_{kf_tag}"
            forval[rid] = RegnForval(
                regn_id=rid,
                namn=f"Sverige P110 {t_int}-årsregn 6 h CDS (kf {kf_val:g})",
                region="Sverige",
                typ="cds",
                aterkomsttid_ar=t_ar,
                klimatfaktor=kf_val,
                varaktighet_min=STANDARD_VARAKTIGHET_MIN,
                tidssteg_min=STANDARD_TIDSSTEG_MIN,
                toppfaktor=STANDARD_CDS_TOPPFAKTOR,
                statistikkalla=(
                    f"Dahlström (2010) / Svenskt Vatten P104 & P110 (klimatfaktor {kf_val:g})"
                ),
                beskrivning=(
                    f"Nationellt 6 h CDS-regn (r=0.37, dt=5 min) för {t_int} års återkomsttid "
                    f"med {kf_text} enligt Dahlström (2010) / Svenskt Vatten P104 & P110."
                ),
            )

    return forval


REGN_FORVAL: dict[str, RegnForval] = _bygg_inbyggda_forval()


def lista_regnforval(*, region: str | None = None) -> list[RegnForval]:
    """Returnerar sorterad lista av inbyggda regnförval, valfritt filtrerad på region."""
    if region is not None and region not in ("Stockholm", "Sverige"):
        raise RegnFel(f"Okänd region '{region}'. Giltiga val: 'Stockholm', 'Sverige'.")
    forval = [
        item
        for item in REGN_FORVAL.values()
        if region is None or item.region == region
    ]
    return sorted(forval, key=lambda r: (r.region, r.klimatfaktor, r.aterkomsttid_ar, r.regn_id))


def hamta_regnforval(regn_id: str) -> RegnForval:
    """Hämtar ett inbyggt regnförval via `regn_id` eller kastar `RegnFel`."""
    if not isinstance(regn_id, str) or not regn_id.strip():
        raise RegnFel("regn_id måste vara en icke-tom sträng.")
    nyckel = regn_id.strip()
    if nyckel not in REGN_FORVAL:
        tillgangliga = ", ".join(sorted(REGN_FORVAL)[:6])
        raise RegnFel(
            f"Okänt regn_id '{regn_id}'. Exempel på giltiga id:n: {tillgangliga}."
        )
    return REGN_FORVAL[nyckel]


def skapa_regn_fran_spec(
    *,
    regn_id: str | None = None,
    aterkomsttid_ar: float | None = None,
    klimatfaktor: float = 1.0,
    varaktighet_min: float = STANDARD_VARAKTIGHET_MIN,
    typ: str = "cds",
    tidssteg_min: float = STANDARD_TIDSSTEG_MIN,
    toppfaktor: float = STANDARD_CDS_TOPPFAKTOR,
    idf_tabell: IdfTabell | None = None,
) -> RegnSerieK4:
    """Skapar en `RegnSerieK4` från antingen ett inbyggt `regn_id` eller fria parametrar.

    Om `regn_id` anges används förvalets värden som bas; om `aterkomsttid_ar` eller
    `klimatfaktor != 1.0` dessutom skickas in skrivs motsvarande fält över.
    """
    if regn_id is not None:
        forval = hamta_regnforval(regn_id)
        eff_t = aterkomsttid_ar if aterkomsttid_ar is not None else forval.aterkomsttid_ar
        eff_kf = klimatfaktor if klimatfaktor != 1.0 else forval.klimatfaktor
        eff_d = (
            varaktighet_min
            if varaktighet_min != STANDARD_VARAKTIGHET_MIN
            else forval.varaktighet_min
        )
        eff_dt = tidssteg_min if tidssteg_min != STANDARD_TIDSSTEG_MIN else forval.tidssteg_min
        eff_tf = toppfaktor if toppfaktor != STANDARD_CDS_TOPPFAKTOR else forval.toppfaktor
        eff_typ = typ if typ != "cds" else forval.typ
        eff_kalla = idf_tabell.kalla if idf_tabell is not None else forval.statistikkalla

        if eff_typ == "block":
            return generera_blockregn(
                eff_t,
                klimatfaktor=eff_kf,
                varaktighet_min=eff_d,
                idf_tabell=idf_tabell,
                regn_id=forval.regn_id,
                namn=forval.namn,
                statistikkalla=eff_kalla,
                beskrivning=forval.beskrivning,
            )
        if eff_typ == "cds":
            return generera_cds_regn(
                eff_t,
                klimatfaktor=eff_kf,
                varaktighet_min=eff_d,
                tidssteg_min=eff_dt,
                toppfaktor=eff_tf,
                idf_tabell=idf_tabell,
                regn_id=forval.regn_id,
                namn=forval.namn,
                statistikkalla=eff_kalla,
                beskrivning=forval.beskrivning,
            )
        raise RegnFel(f"Ogiltig regntyp '{eff_typ}', måste vara 'cds' eller 'block'.")

    if aterkomsttid_ar is None:
        raise RegnFel("Antingen regn_id eller aterkomsttid_ar måste anges.")

    if typ == "block":
        return generera_blockregn(
            aterkomsttid_ar,
            klimatfaktor=klimatfaktor,
            varaktighet_min=varaktighet_min,
            idf_tabell=idf_tabell,
        )
    if typ == "cds":
        return generera_cds_regn(
            aterkomsttid_ar,
            klimatfaktor=klimatfaktor,
            varaktighet_min=varaktighet_min,
            tidssteg_min=tidssteg_min,
            toppfaktor=toppfaktor,
            idf_tabell=idf_tabell,
        )
    raise RegnFel(f"Ogiltig regntyp '{typ}', måste vara 'cds' eller 'block'.")


# --- Registrerade IDF-källor (tabeller) --------------------------------------------------------

DATA_REGN_MAPP: Path = Path(__file__).resolve().parents[2] / "data" / "regn"
IDF_DAHLSTROM_2010 = "dahlstrom-2010"
IDF_STOCKHOLMSREGN_2024 = "stockholmsregn-2024"
STOCKHOLMSREGN_2024_STANDARD_KLIMATFAKTOR: float = 1.4
STOCKHOLMSREGN_2024_TIDIGARE_STADSFAKTOR: float = 1.25


@dataclass(frozen=True)
class IdfKalla:
    """En registrerad IDF-tabell med sidokort, kontrollsummor och standard-klimatfaktor.

    Tabellvärdena är utan klimatfaktor. `standard_klimatfaktor` används av
    `skapa_regn_fran_idf_kalla` och av CLI:t när ingen faktor anges; faktorn är alltid en
    uttrycklig parameter i anropet.
    """

    kalla_id: str
    namn: str
    sidokort: Path
    standard_klimatfaktor: float
    standard_klimatfaktor_motivering: str

    def las_sidokort(self) -> dict[str, Any]:
        """Läser sidokortet (TOML) och kontrollerar att tabellfilernas sha256 stämmer."""
        if not self.sidokort.is_file():
            raise RegnFel(f"Sidokort för IDF-källan '{self.kalla_id}' saknas: {self.sidokort}")
        try:
            kort = tomllib.loads(self.sidokort.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            raise RegnFel(f"Kunde inte läsa sidokortet {self.sidokort}: {exc}") from exc
        if kort.get("id") != self.kalla_id:
            raise RegnFel(
                f"Sidokortet {self.sidokort} har id {kort.get('id')!r}, "
                f"förväntade {self.kalla_id!r}."
            )
        for roll, post in kort.get("filer", {}).items():
            fil = self.sidokort.parent / post["fil"]
            if not fil.is_file():
                raise RegnFel(f"Tabellfil saknas för IDF-källan '{self.kalla_id}': {fil}")
            faktisk = sha256_fil(fil)
            if faktisk != post["sha256"]:
                raise RegnFel(
                    f"sha256 för {fil.name} ({roll}) stämmer inte med sidokortet "
                    f"({faktisk} != {post['sha256']}); tabellen har ändrats."
                )
        return kort

    def las(self) -> IdfTabell:
        """Läser tabellen och räknar om l/(s·ha) till mm/h (1 l/(s·ha) = 0,36 mm/h)."""
        kort = self.las_sidokort()
        fil = self.sidokort.parent / kort["filer"]["intensitet"]["fil"]
        rad = las_idf_tabell_csv(fil, namn=self.kalla_id)
        faktor = float(kort["omvandling_till_mm_h"])
        if abs(faktor - L_PER_S_HA_TILL_MM_H) > 1e-12:
            raise RegnFel(f"Sidokortets omvandling {faktor} avviker från {L_PER_S_HA_TILL_MM_H}.")
        return IdfTabell(
            namn=self.kalla_id,
            kalla=(
                f"{kort['namn']} ({kort['utgivare'].split(';')[0]}), {kort['tabell']} "
                f"s. {kort['sida']}, utan klimatfaktor"
            ),
            varaktigheter_min=rad.varaktigheter_min,
            aterkomsttider_ar=rad.aterkomsttider_ar,
            intensiteter_mm_h=tuple(
                tuple(v * L_PER_S_HA_TILL_MM_H for v in r) for r in rad.intensiteter_mm_h
            ),
        )

    def manifest_parametrar(self, klimatfaktor: float) -> dict[str, Any]:
        """Källa och faktor för K0-manifestets `parametrar` (inkl. sha256 för tabellfilerna)."""
        kort = self.las_sidokort()
        return {
            "idf_kalla": self.kalla_id,
            "idf_rapport": kort["rapport"],
            "idf_tabell": kort["tabell"],
            "idf_url": kort["url"],
            "idf_klimatfaktor": _validera_positivt_tal(klimatfaktor, "klimatfaktor"),
            "idf_klimatfaktor_ingar_i_tabell": bool(kort["klimatfaktor_ingar"]),
            "idf_sha256": {roll: post["sha256"] for roll, post in kort["filer"].items()},
        }

    def indatafiler(self) -> list[tuple[str, Path]]:
        """Roll och sökväg för filer som ska föras in som `FilPost` i manifestet."""
        kort = self.las_sidokort()
        mapp = self.sidokort.parent
        filer = [(f"idf_{roll}", mapp / post["fil"]) for roll, post in kort["filer"].items()]
        return [("idf_sidokort", self.sidokort), *filer]


IDF_KALLOR: dict[str, IdfKalla] = {
    IDF_STOCKHOLMSREGN_2024: IdfKalla(
        kalla_id=IDF_STOCKHOLMSREGN_2024,
        namn="Stockholmsregn 2024",
        sidokort=DATA_REGN_MAPP / "stockholmsregn_2024.toml",
        standard_klimatfaktor=STOCKHOLMSREGN_2024_STANDARD_KLIMATFAKTOR,
        standard_klimatfaktor_motivering=(
            "Stockholmsregn 2024 kap. 6.3 rekommenderar klimatfaktor 1,4 (multipliceras med hela "
            "regnkurvan, kap. 4.9); staden använde 1,25 när rapporten skrevs"
        ),
    ),
}


def lista_idf_kallor() -> list[str]:
    """Id:n för registrerade IDF-tabeller (Dahlström 2010 är en formel, inte en tabell)."""
    return sorted(IDF_KALLOR)


def hamta_idf_kalla(kalla_id: str) -> IdfKalla:
    """Hämtar en registrerad IDF-källa eller kastar `RegnFel`."""
    if kalla_id not in IDF_KALLOR:
        raise RegnFel(
            f"Okänd IDF-källa '{kalla_id}'. Giltiga: {IDF_DAHLSTROM_2010}, "
            f"{', '.join(lista_idf_kallor())}."
        )
    return IDF_KALLOR[kalla_id]


def las_stockholmsregn_2024() -> IdfTabell:
    """Stockholmsregn 2024, Tabell 7, i mm/h och UTAN klimatfaktor (ange den vid anropet)."""
    return IDF_KALLOR[IDF_STOCKHOLMSREGN_2024].las()


def skapa_regn_fran_idf_kalla(
    kalla_id: str,
    aterkomsttid_ar: float,
    *,
    klimatfaktor: float | None = None,
    typ: str = "cds",
    varaktighet_min: float = STANDARD_VARAKTIGHET_MIN,
    tidssteg_min: float = STANDARD_TIDSSTEG_MIN,
    toppfaktor: float = STANDARD_CDS_TOPPFAKTOR,
) -> RegnSerieK4:
    """Skapar block- eller CDS-regn ur en registrerad IDF-tabell.

    `klimatfaktor=None` ger källans standardfaktor (Stockholmsregn 2024: 1,4); värdet skrivs i
    serien (`klimatfaktor`) och i `regn_id`. Faktorn multipliceras med hela regnkurvan.
    """
    kalla = hamta_idf_kalla(kalla_id)
    kf = kalla.standard_klimatfaktor if klimatfaktor is None else klimatfaktor
    tabell = kalla.las()
    t_ar = _validera_positivt_tal(aterkomsttid_ar, "aterkomsttid_ar")
    d_tot = _validera_positivt_tal(varaktighet_min, "varaktighet_min")
    kf = _validera_positivt_tal(kf, "klimatfaktor")
    rid = (
        f"{kalla_id.replace('-', '')}_{typ}_{_formatera_axelvarde(d_tot)}min_"
        f"{_formatera_axelvarde(t_ar)}ar_kf{_formatera_axelvarde(kf)}"
    )
    if typ == "block":
        return generera_blockregn(
            t_ar, klimatfaktor=kf, varaktighet_min=d_tot, idf_tabell=tabell, regn_id=rid
        )
    if typ == "cds":
        return generera_cds_regn(
            t_ar,
            klimatfaktor=kf,
            varaktighet_min=d_tot,
            tidssteg_min=tidssteg_min,
            toppfaktor=toppfaktor,
            idf_tabell=tabell,
            regn_id=rid,
        )
    raise RegnFel(f"Ogiltig regntyp '{typ}', måste vara 'cds' eller 'block'.")
