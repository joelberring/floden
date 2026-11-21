"""Import av Referencelaboratoriets testfall (RL 18-18, Excel-filer daterade 2018-11-01).

Läser de fyra arbetsböckerna direkt (`.xls`) och skriver ett fall per blad under
`testsvit/N_nord2000/`. Körs om lokalt med

    python -m buller.testfall.xlsimport --kallmapp <mapp> --ut testsvit/N_nord2000

Utan `--kallmapp` används `[buller] kallmapp` i `config.local.toml` om den finns. Källmappen
ska ha filerna under `testfall/` med de namn som står i `ARBETSBOCKER`. Filerna kopieras
aldrig in i repot.

`xlrd` behövs bara för importen och är inget körberoende för bullermodulen.

Varje värde kontrolleras på ett andra, oberoende sätt innan fallen skrivs (se
`kontrollera`): A-vägd summa av spektrum mot bladets totalnivå, L_E − L_eq mot trafiken,
bladet `ToBeTested` mot fallbladen, bladet `OverAll` mot årsmedelbladen och, om den finns,
JSON-utskriften av arbetsboken för rak väg.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from buller.testfall.excelimport import Celler, _las_terrang, _som_tal, importera_rak_vag_blad
from buller.testfall.format import (
    Facit,
    Facitserie,
    N2kFall,
    Skalar,
    Trafikpost,
    skriv_fall,
)
from buller.typer import ANTAL_BAND, TERSBAND_HZ, BullerFel, Flyttal, Vader

REGELVERK = "rl18_18"
DOKUMENT = (
    "Referencelaboratoriet RL 18-18, Test Cases for Road Traffic Noise – Nord2000, "
    "version december 2018"
)
METODVERSION = "Nord2000 Road, testfall version december 2018 (Excel-filer daterade 20181101)"
FACITSKAL = "Gällande officiella testfall (RL 18-18); värdena är lästa direkt ur Excel-filen."
ARBETSBOCKER: Mapping[str, str] = {
    "rak_vag": "testfall/teststraightroad_20181101.xls",
    "kurvig_vag": "testfall/testcurvedroad_20181101.xls",
    "stadsgata": "testfall/testcitystreet_20181101.xls",
    "arsmedel": "testfall/testyearlyaverage_20181101.xls",
}
JSON_RAK_VAG = "testfall/json/teststraightroad_20181101.json"
SKOGSFALL = (121, 122, 123, 124)
TUNGA_FALL = {17: 2, 18: 3}  # RL 18-18 kapitel 2
_GUL = (255, 255, 0)
_VINDKOLUMNER = {"u=0m/s": 0.0, "u=3m/s": 3.0, "u=-3m/s": -3.0}

# Väder för kurvig väg och stadsgata, RL 18-18 kapitel 3 (s. 12); stadsgata har u = 0.
_VADER_KAP3 = Vader(
    temperatur_c=15.0, relativ_fuktighet_procent=70.0, vind_m_s=0.0, zu_m=10.0, z0_m=0.05,
    dt_dz_k_m=0.0, std_vind_m_s=0.5, std_dt_dz_k_m=0.0, cv2=0.12, ct2=0.008,
)  # fmt: skip


@dataclass(frozen=True)
class Arbetsbok:
    """En inläst arbetsbok: celltext per blad och vilka celler som är gulmarkerade."""

    fil: Path
    blad: dict[str, Celler]
    gula: dict[str, set[tuple[int, int]]]


def kallmapp_ur_konfig(repo_rot: str | Path = ".") -> Path | None:
    """Läser den valfria inställningen `[buller] kallmapp` ur `config.local.toml`.

    Returnerar `None` om konfigurationen eller inställningen saknas.
    """
    from gemensamt.konfig import KonfigFel, las_konfig

    rot = Path(repo_rot)
    try:
        konfig = las_konfig(rot / "config.toml", rot / "config.local.toml")
    except KonfigFel:
        return None
    buller = konfig.get("buller", {})
    mapp = buller.get("kallmapp") if isinstance(buller, dict) else None
    return Path(mapp).expanduser() if isinstance(mapp, str) and mapp.strip() else None


def las_arbetsbok(fil: str | Path) -> Arbetsbok:
    """Läser en `.xls`-fil med xlrd. Tal blir text med full precision (`repr`)."""
    try:
        import xlrd  # type: ignore[import-not-found,import-untyped,unused-ignore]
    except ImportError as exc:
        raise BullerFel("Importen av .xls kräver paketet xlrd (pip install xlrd).") from exc
    sokvag = Path(fil)
    if not sokvag.is_file():
        raise BullerFel(f"Excel-filen saknas: {sokvag}")
    try:
        bok = xlrd.open_workbook(str(sokvag), formatting_info=True)
    except Exception as exc:  # xlrd har flera egna feltyper
        raise BullerFel(f"Kunde inte läsa {sokvag}: {exc}") from exc
    blad: dict[str, Celler] = {}
    gula: dict[str, set[tuple[int, int]]] = {}
    for ark in bok.sheets():
        celler: Celler = []
        gul: set[tuple[int, int]] = set()
        for r in range(ark.nrows):
            rad: list[str] = []
            for k in range(ark.ncols):
                varde = ark.cell_value(r, k)
                rad.append(repr(varde) if isinstance(varde, float) else str(varde).strip())
                farg = bok.xf_list[ark.cell_xf_index(r, k)].background.pattern_colour_index
                if bok.colour_map.get(farg) == _GUL:
                    gul.add((r, k))
            celler.append(rad)
        blad[ark.name] = celler
        gula[ark.name] = gul
    return Arbetsbok(sokvag, blad, gula)


def _norm(text: str) -> str:
    return "".join(text.split()).lower()


def _rad_med(celler: Celler, etikett: str, blad: str) -> int:
    for r, rad in enumerate(celler):
        if rad and _norm(rad[0]).startswith(etikett):
            return r
    raise BullerFel(f"{blad}: hittade inte raden '{etikett}'.")


def _talrad(celler: Celler, r: int, kolumner: Sequence[int], blad: str) -> list[float]:
    tal = [_som_tal(celler[r][k]) for k in kolumner]
    if any(t is None for t in tal):
        raise BullerFel(f"{blad}: rad {r + 1} saknar tal.")
    return [float(t) for t in tal if t is not None]


def _spektrumkolumner(
    celler: Celler, rubrikrad: int, kolumner: Sequence[int], blad: str
) -> list[Flyttal]:
    """Läser 27 band under en rubrikrad vars första kolumn är frekvensen."""
    r = rubrikrad + 1
    while r < len(celler) and _som_tal(celler[r][0]) is None:
        r += 1  # enhetsrad
    varden: list[list[float]] = []
    for band, frekvens in enumerate(TERSBAND_HZ):
        if r + band >= len(celler) or _som_tal(celler[r + band][0]) != frekvens:
            raise BullerFel(f"{blad}: väntade {frekvens:g} Hz på rad {r + band + 1}.")
        varden.append(_talrad(celler, r + band, kolumner, blad))
    return [np.asarray([rad[i] for rad in varden]) for i in range(len(kolumner))]


def _facit(serier: Sequence[Facitserie], fil: str, blad: str, ar_facit: bool, skal: str) -> Facit:
    return Facit(
        ar_facit=ar_facit,
        ar_facit_skal=skal,
        metodversion=METODVERSION,
        kalla={"dokument": DOKUMENT, "excelfil": fil, "blad": blad},
        serier=tuple(serier),
        avlasning={
            "metod": "maskinell import med buller.testfall.xlsimport; kontrollerad enligt "
            "buller.testfall.xlsimport.kontrollera",
            "verifierad_av_manniska": False,
        },
        regelverk=REGELVERK,
    )


def importera_rak_vag(bok: Arbetsbok) -> list[tuple[str, N2kFall, Facit]]:
    """Ett fall per blad i arbetsboken för rak väg (62 blad)."""
    ut: list[tuple[str, N2kFall, Facit]] = []
    for blad, celler in bok.blad.items():
        if not blad.isdigit():
            raise BullerFel(f"{bok.fil.name}: oväntat blad '{blad}'.")
        nr = int(blad)
        fall, facit = importera_rak_vag_blad(
            celler,
            fall_id=f"N18R{nr:03d}",
            blad=blad,
            dokument=DOKUMENT,
            metodversion=METODVERSION,
            ar_facit=True,
            ar_facit_skal=FACITSKAL,
            fordonskategori=TUNGA_FALL.get(nr, 1),
        )
        ovrigt: dict[str, Skalar] = {
            "fall_nr_i_rapporten": nr,
            "belaggning": "DAC",
            "stenstorlek_mm": 12.0,
            "anm_vader": "relativ fuktighet 70 % enligt RL 18-18 kapitel 2; lufttryck anges "
            "inte (standardvärdet i buller.typer.Vader)",
        }
        fullstandig, saknas = True, ()
        if nr in SKOGSFALL:
            # RL 18-18 tabell 2 (s. 9); femte parameterns symbol är oläslig i PDF-texten.
            ovrigt.update(
                skog_x_start_m=10.0, skog_x_slut_m=80.0, skog_nQ_per_m=0.075, skog_H_m=10.0,
                skog_okand_symbol=0.1, skog_kp=1.25, skog_a_trunc_m=0.15,
            )  # fmt: skip
            fullstandig = False
            saknas = (  # type: ignore[assignment]
                "spridningszonen (skog) kan inte uttryckas i Terrangprofil KB1/v1; "
                "parametrarna står i [ovrigt]",
            )
        fall = N2kFall(
            id=fall.id,
            namn=f"Rak väg, fall {nr} (RL 18-18)",
            typ=fall.typ,
            beskrivning=fall.beskrivning,
            status="redo" if fullstandig else "ofullstandig",
            kalla={"dokument": DOKUMENT, "excelfil": bok.fil.name, "blad": blad},
            indata_fullstandig=fullstandig,
            indata_saknas=saknas,
            vag=fall.vag,
            mottagare_x_m=fall.mottagare_x_m,
            mottagare_hojd_m=fall.mottagare_hojd_m,
            terrang=fall.terrang,
            trafik=fall.trafik,
            perioder_h=fall.perioder_h,
            vader=fall.vader,
            vader_tillagg=fall.vader_tillagg,
            ovrigt=ovrigt,
        )
        facit = _facit(facit.serier, bok.fil.name, blad, True, FACITSKAL)
        ut.append((f"{fall.id}_rak_vag", fall, facit))
    return ut


def _obligatoriska_kurvig(bok: Arbetsbok) -> dict[str, dict[float, tuple[float, bool]]]:
    """Bladet ToBeTested: fall → vind → (LAeq,24h, gulmarkerad)."""
    blad = "ToBeTested"
    if blad not in bok.blad:
        raise BullerFel(f"{bok.fil.name} saknar bladet {blad}.")
    celler = bok.blad[blad]
    rubrik = _rad_med(celler, "windcase", blad)
    vindar = [_VINDKOLUMNER[_norm(c)] for c in celler[rubrik][1:4]]
    ut: dict[str, dict[float, tuple[float, bool]]] = {}
    for r in range(rubrik + 2, len(celler)):
        namn = celler[r][0]
        if not namn or _som_tal(celler[r][1]) is None:
            break
        tal = _som_tal(namn)
        nyckel = namn if tal is None else f"{tal:g}"
        ut[nyckel] = {
            vind: (float(_talrad(celler, r, [k + 1], blad)[0]), (r, k + 1) in bok.gula[blad])
            for k, vind in enumerate(vindar)
        }
    return ut


def importera_kurvig_vag(bok: Arbetsbok) -> list[tuple[str, N2kFall, Facit]]:
    """Ett fall per mottagarblad (inte L/H-bladen, som bara är upplysning)."""
    obligatoriska = _obligatoriska_kurvig(bok)
    ut: list[tuple[str, N2kFall, Facit]] = []
    for blad, celler in bok.blad.items():
        if blad in ("ToBeTested", "Coordinates") or blad[-1] in "LH":
            continue
        rubrik = _rad_med(celler, "freq.", blad)
        vindar = [_VINDKOLUMNER[_norm(c)] for c in celler[rubrik][1:4]]
        laeq = _talrad(celler, _rad_med(celler, "laeq,24h", blad), (1, 2, 3), blad)
        lamax = _talrad(celler, _rad_med(celler, "lamax", blad), (1, 2, 3), blad)
        spektrum = _spektrumkolumner(celler, rubrik, (1, 2, 3), blad)
        serier = []
        for i, vind in enumerate(vindar):
            gul = obligatoriska.get(blad, {}).get(vind, (math.nan, False))[1]
            serier.append(
                Facitserie(
                    id=f"u{vind:g}".replace("-", "m"),
                    villkor={
                        "vind_m_s": vind,
                        "regelvariant": "kurvig_vag_gul" if gul else "kurvig_vag_motvind",
                    },
                    totalnivaer_db={"LAeq_24h": laeq[i], "LAmax": lamax[i]},
                    spektrum_db={"Leq_24h": spektrum[i]},
                )
            )
        med = blad in obligatoriska
        position = blad.split("-")[0]
        fall = N2kFall(
            id=f"N18K{blad}",
            namn=f"Kurvig väg, fall {blad} (RL 18-18)",
            typ="nord2000_kurvig_vag",
            beskrivning=f"Mottagarposition {position}; tre vindfall (0, 3 och −3 m/s, "
            "vindriktning 315° från norr). Obligatoriskt är bara LAeq,24h.",
            status="ofullstandig",
            kalla={"dokument": DOKUMENT, "excelfil": bok.fil.name, "blad": blad},
            indata_fullstandig=False,
            indata_saknas=(
                "plangeometrin (väg, skärmar, höjdkurvor, mottagare) finns i bladet "
                "Coordinates men formatet N-fall/v1 har ingen plangeometri",
                "köraren räknar bara rak väg",
            ),
            mottagare_hojd_m=3.5 if position == "7" else 1.5,
            trafik=(
                Trafikpost(1, 9000.0, 90.0, "T"),
                Trafikpost(2, 500.0, 70.0, "T"),
                Trafikpost(3, 500.0, 70.0, "T"),
            ),
            perioder_h={"T": 24.0},
            vader=_VADER_KAP3,
            vader_tillagg={"vindriktning_grader_fran_norr": 315.0},
            ovrigt={"mottagarposition": position, "axlar_kategori_3": 5},
        )
        skal = FACITSKAL if med else "Fallet står inte i bladet ToBeTested (inte obligatoriskt)."
        ut.append((f"{fall.id}_kurvig_vag", fall, _facit(serier, bok.fil.name, blad, med, skal)))
    return ut


def importera_stadsgata(bok: Arbetsbok) -> list[tuple[str, N2kFall, Facit]]:
    """Ett fall per mottagarposition; sex serier (tre höjder, två reflektionskoefficienter)."""
    ut: list[tuple[str, N2kFall, Facit]] = []
    kolumner = tuple(range(1, 7))
    for blad, celler in bok.blad.items():
        if blad == "Coordinates":
            continue
        hojder = _talrad(celler, _rad_med(celler, "hr(m)", blad), kolumner, blad)
        reflektion = _talrad(celler, _rad_med(celler, "se", blad), kolumner, blad)
        laeq = _talrad(celler, _rad_med(celler, "laeq,24h", blad), kolumner, blad)
        spektrum = _spektrumkolumner(celler, _rad_med(celler, "freq.", blad) - 1, kolumner, blad)
        serier = [
            Facitserie(
                id=f"h{hojder[i]:g}_rho{reflektion[i]:g}".replace(".", "_"),
                villkor={
                    "mottagare_hojd_m": hojder[i],
                    "energireflektionskoefficient": reflektion[i],
                },
                totalnivaer_db={"LAeq_24h": laeq[i]},
                spektrum_db={"Leq_24h": spektrum[i]},
            )
            for i in range(len(kolumner))
        ]
        fall = N2kFall(
            id=f"N18S{blad}",
            namn=f"Stadsgata, mottagarposition {blad} (RL 18-18)",
            typ="nord2000_stadsgata",
            beskrivning="Mottagare vid fasad på tre höjder, fasadernas energireflektions"
            "koefficient 1 och 0,7; reflexer av första och andra ordningen, egna fasadens "
            "reflex räknas inte.",
            status="ofullstandig",
            kalla={"dokument": DOKUMENT, "excelfil": bok.fil.name, "blad": blad},
            indata_fullstandig=False,
            indata_saknas=(
                "byggnader, väg och mottagare finns i bladet Coordinates men formatet "
                "N-fall/v1 har ingen plangeometri",
                "köraren räknar bara rak väg (inga reflexer)",
            ),
            trafik=(Trafikpost(1, 10000.0, 50.0, "T"),),
            perioder_h={"T": 24.0},
            vader=_VADER_KAP3,
            ovrigt={"mottagarposition": blad, "byggnadshojd_m": 12.0, "korfaltsbredd_m": 6.0},
        )
        ut.append((f"{fall.id}_stadsgata", fall, _facit(serier, bok.fil.name, blad, True,
                                                        FACITSKAL)))
    return ut


def importera_arsmedel(bok: Arbetsbok) -> list[tuple[str, N2kFall, Facit]]:
    """Ett fall per blad 1–4 (Lden som årsmedel)."""
    ut: list[tuple[str, N2kFall, Facit]] = []
    for blad, celler in bok.blad.items():
        if not blad.isdigit():
            continue
        frekvensrad = next(
            (r for r, rad in enumerate(celler) if len(rad) > 6 and _norm(rad[5]) == "freq."), None
        )
        ldenrad = next(
            (r for r, rad in enumerate(celler) if len(rad) > 6 and _norm(rad[5]) == "lden(db)"),
            None,
        )
        if frekvensrad is None or ldenrad is None:
            raise BullerFel(f"{bok.fil.name} blad {blad}: hittade inte Lden eller Freq.")
        r0 = frekvensrad + 2
        spektrum = []
        for band, frekvens in enumerate(TERSBAND_HZ):
            if _som_tal(celler[r0 + band][5]) != frekvens:
                raise BullerFel(f"{bok.fil.name} blad {blad}: väntade {frekvens:g} Hz.")
            spektrum.append(_talrad(celler, r0 + band, [6], blad)[0])
        antal = {
            namn: _talrad(celler, _rad_med(celler, etikett, blad), [1], blad)[0]
            for namn, etikett in (("dag", "n(day)"), ("kvall", "n(evening)"), ("natt", "n(night)"))
        }
        terrang = _las_terrang(celler, blad)
        fall = N2kFall(
            id=f"N18A{blad}",
            namn=f"Årsmedel, fall {blad} (RL 18-18)",
            typ="nord2000_arsmedel",
            beskrivning=celler[2][0],
            status="ofullstandig",
            kalla={"dokument": DOKUMENT, "excelfil": bok.fil.name, "blad": blad},
            indata_fullstandig=False,
            indata_saknas=(
                "väderklassernas andelar finns i bladet Met. statistics men är inte importerade",
                "periodernas längd och Lden-beräkningen (VTT-R-02530-06) ingår inte i köraren",
            ),
            mottagare_x_m=terrang.x_m[-1],
            mottagare_hojd_m=_talrad(celler, _rad_med(celler, "hr", blad), [1], blad)[0],
            terrang=terrang,
            ovrigt={
                "antal_fordon_dag": antal["dag"],
                "antal_fordon_kvall": antal["kvall"],
                "antal_fordon_natt": antal["natt"],
                "hastighet_km_h": _talrad(celler, _rad_med(celler, "v", blad), [1], blad)[0],
                "fordonskategori": 1,
            },
        )
        serie = Facitserie(
            "blad",
            {},
            {"Lden": _talrad(celler, ldenrad, [6], blad)[0]},
            {"Lden": np.asarray(spektrum)},
        )
        ut.append((f"{fall.id}_arsmedel", fall, _facit([serie], bok.fil.name, blad, True,
                                                       FACITSKAL)))
    return ut


def _a_summa(spektrum_db: Flyttal, a_vagning_db: Flyttal) -> float:
    return float(10.0 * np.log10(np.sum(10.0 ** ((spektrum_db + a_vagning_db) / 10.0))))


def kontrollera(
    bocker: Mapping[str, Arbetsbok],
    fall: Mapping[str, Sequence[tuple[str, N2kFall, Facit]]],
    a_vagning_db: Flyttal,
    json_rak_vag: Path | None = None,
    tolerans_a_summa_db: float = 0.05,
) -> list[str]:
    """Oberoende kontroller av de importerade värdena. Avvikelse ger `BullerFel`.

    Returnerar en rad per kontroll med antal jämförda värden och största skillnad.
    `tolerans_a_summa_db` gäller jämförelsen mellan A-vägd summa av spektrum och bladets
    totalnivå; den fångar förskjutna rader och kolumner, inte avrundning i A-vägningen.
    """
    rapport: list[str] = []
    storst = 0.0
    antal = 0
    for grupp in fall.values():
        for _, _, facit in grupp:
            for serie in facit.serier:
                for total, spektrum in (("LAeq_24h", "Leq_24h"), ("LAE", "LE"), ("Lden", "Lden")):
                    if total in serie.totalnivaer_db and spektrum in serie.spektrum_db:
                        summa = _a_summa(serie.spektrum_db[spektrum], a_vagning_db)
                        storst = max(storst, abs(summa - serie.totalnivaer_db[total]))
                        antal += 1
    if storst > tolerans_a_summa_db:
        raise BullerFel(f"A-vägd summa av spektrum avviker {storst:.3f} dB från totalnivån.")
    rapport.append(f"A-vägd summa av spektrum mot totalnivå: {antal} serier, max {storst:.4f} dB")

    storst = 0.0
    for _, n2k, facit in fall["rak_vag"]:
        s = facit.serier[0]
        vantat = 10.0 * math.log10(n2k.perioder_h["T"] * 3600.0 / n2k.trafik[0].antal_fordon)
        skillnad = s.spektrum_db["LE"] - s.spektrum_db["Leq_24h"] - vantat
        storst = max(storst, float(np.max(np.abs(skillnad))))
    if storst > 1e-4:
        raise BullerFel(f"L_E − L_eq avviker {storst:.2e} dB från 10·lg(T/N).")
    rapport.append(f"L_E − L_eq mot 10·lg(T/N): {len(fall['rak_vag'])} fall, max {storst:.1e} dB")

    obligatoriska = _obligatoriska_kurvig(bocker["kurvig_vag"])
    antal = 0
    for _, n2k, facit in fall["kurvig_vag"]:
        blad = str(n2k.kalla["blad"])
        for serie in facit.serier:
            if blad in obligatoriska:
                vantat_db = obligatoriska[blad][float(serie.villkor["vind_m_s"])][0]
                if serie.totalnivaer_db["LAeq_24h"] != vantat_db:
                    raise BullerFel(f"Kurvig väg {blad}: ToBeTested och fallbladet är olika.")
                antal += 1
    rapport.append(f"Kurvig väg: {antal} LAeq,24h lika i fallblad och ToBeTested")

    samlat = bocker["arsmedel"].blad.get("OverAll")
    if samlat is not None:
        antal = 0
        for _, n2k, facit in fall["arsmedel"]:
            k = int(str(n2k.kalla["blad"]))
            serie = facit.serier[0]
            if _som_tal(samlat[2][k]) != serie.totalnivaer_db["Lden"]:
                raise BullerFel(f"Årsmedel {k}: OverAll och fallbladet har olika Lden.")
            for band in range(ANTAL_BAND):
                if _som_tal(samlat[5 + band][k]) != float(serie.spektrum_db["Lden"][band]):
                    raise BullerFel(f"Årsmedel {k}: OverAll och fallbladet har olika spektrum.")
                antal += 1
        rapport.append(f"Årsmedel: {antal} bandvärden och 4 Lden lika i fallblad och OverAll")

    if json_rak_vag is not None and json_rak_vag.is_file():
        data: dict[str, Any] = json.loads(json_rak_vag.read_text(encoding="utf-8"))
        antal = 0
        for blad, celler in bocker["rak_vag"].blad.items():
            andra = data["sheets"][blad]
            for r, rad in enumerate(celler):
                for k, cell in enumerate(rad):
                    annan = andra[r][k] if r < len(andra) and k < len(andra[r]) else None
                    lika = (annan is None and cell == "") or (
                        _som_tal(cell) == annan
                        if isinstance(annan, int | float)
                        else cell == str(annan or "").strip()
                    )
                    if not lika:
                        raise BullerFel(f"Rak väg blad {blad} cell ({r}, {k}): xls och JSON olika.")
                    antal += 1
        rapport.append(f"Rak väg: {antal} celler lika i .xls (xlrd) och JSON-utskriften")
    return rapport


def importera_rl18(
    kallmapp: str | Path, utmapp: str | Path | None, a_vagning_db: Flyttal | None = None
) -> list[str]:
    """Importerar alla fyra arbetsböcker, kontrollerar och skriver fallen (om `utmapp` anges)."""
    rot = Path(kallmapp)
    bocker = {namn: las_arbetsbok(rot / fil) for namn, fil in ARBETSBOCKER.items()}
    fall = {
        "rak_vag": importera_rak_vag(bocker["rak_vag"]),
        "kurvig_vag": importera_kurvig_vag(bocker["kurvig_vag"]),
        "stadsgata": importera_stadsgata(bocker["stadsgata"]),
        "arsmedel": importera_arsmedel(bocker["arsmedel"]),
    }
    if a_vagning_db is None:
        from buller.akustik.band import a_vagning_db as standard_a_vagning_db

        a_vagning_db = np.asarray(standard_a_vagning_db(), dtype=np.float64)
    rapport = kontrollera(bocker, fall, a_vagning_db, rot / JSON_RAK_VAG)
    rapport.insert(0, ", ".join(f"{namn}: {len(grupp)} fall" for namn, grupp in fall.items()))
    if utmapp is not None:
        for grupp in fall.values():
            for mappnamn, n2k, facit in grupp:
                skriv_fall(n2k, Path(utmapp) / mappnamn, facit)
    return rapport


def main(argv: Sequence[str] | None = None) -> int:
    """Kommandorad: importera RL 18-18 från källmappen till testsviten."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--kallmapp", help="mapp med testfall/*.xls; annars config.local.toml")
    parser.add_argument("--ut", default="testsvit/N_nord2000", help="mapp att skriva fallen i")
    parser.add_argument("--torrkor", action="store_true", help="kontrollera utan att skriva")
    args = parser.parse_args(argv)
    kallmapp = Path(args.kallmapp) if args.kallmapp else kallmapp_ur_konfig()
    if kallmapp is None:
        print("Ange --kallmapp eller [buller] kallmapp i config.local.toml.", file=sys.stderr)
        return 2
    try:
        rapport = importera_rl18(kallmapp, None if args.torrkor else args.ut)
    except BullerFel as exc:
        print(f"Importen avbröts: {exc}", file=sys.stderr)
        return 1
    print("\n".join(rapport))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
