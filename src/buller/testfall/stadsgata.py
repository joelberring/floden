"""Förberedelse och beräkning för Nord2000-testgruppen Stadsgata (N18S1–N18S4).

Källa: DELTA RL 18-18 kapitel 4 (s. 13–14) och `TestCityStreet_20181101.xls`
(bladet `Coordinates`), samt DELTA AV 1106/07 avsnitt 5.20 (Delmodell 11: reflektion
mot vertikala hinder).

Geometri (RL 18-18 s. 13–14):
- Fyra rektangulära byggnader med platta tak, höjd 12 m.
- Rak väg längs x = 0 med två körfält om 6 m bredd (mittlinjer x = −3 m och +3 m).
- 10 000 lätta fordon/dygn (kategori 1), 50 km/h, jämnt fördelade på körfälten.
- Axelbredd 1,5 m (källorna flyttas 0,75 m mot mottagaren).
- Källpunktsavstånd 2 m för position 1–3, 20 m för position 4; vägpunkter tas med om
  horisontellt avstånd till mottagaren < 20 × kortaste avstånd till vägen.
- Mottagare vid fasad (0 m) på höjderna 1,5, 4 och 8 m; frifältsnivå (egna fasadens
  reflex utelämnas), inklusive 1:a och 2:a ordningens reflektioner från övriga fasader.
- Energireflektionskoefficient ρ_E ∈ {1,0, 0,7}; vind u = 0 m/s.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from buller.emission.direktivitet import Kallriktning
from buller.emission.modell import Trafikflode, Vagforhallanden
from buller.nord2000.reflektion import (
    MAX_UTANFOR_KANT_M,
    Reflektionsgeometri,
    VertikalReflektor,
    linje_korsar_segment,
    reflektionskorrektion_db,
    reflektionspunkt_plan,
    spegelpunkt_plan,
)
from buller.testfall.excelimport import Celler, _som_tal
from buller.testfall.korare import Emissionsmodell, EmissionsmodellMedDirektivitet
from buller.typer import ANTAL_BAND, TERSBAND_HZ, BullerFel, Flyttal, P2PKarna, Terrangprofil, Vader

BYGGNADSHOJD_M = 12.0
KORFALTSBREDD_STAD_M = 6.0
AXELHALVA_STAD_M = 0.75  # 1,5 m axelbredd, RL 18-18 s. 14
SIGMA_VAG_KNSM4 = 20000.0
SIGMA_MARK_KNSM4 = 20000.0  # hårdgjord stadsgata (körbana och trottoar, impedansklass G)
VAG_HALVBREDD_M = 6.0  # två 6 m-körfält: x ∈ [−6, +6]
BYGGNAD_KANT_EPS_M = 0.01


@dataclass(frozen=True)
class ByggnadPlan:
    """En rektangulär byggnad i plan med platt tak."""

    namn: str
    x_min_m: float
    x_max_m: float
    y_min_m: float
    y_max_m: float
    hojd_m: float = BYGGNADSHOJD_M

    def fasader(self, reflektionsfaktor_e: float) -> tuple[VertikalReflektor, ...]:
        """De fyra utåtriktade vertikala fasaderna (ensidig = True med utåtnormal)."""
        # Moturs ordning (CCW) ger höger normal (dy, -dx) pekande UTÅT ur rektangeln.
        return (
            VertikalReflektor(
                self.x_min_m,
                self.y_min_m,
                self.x_max_m,
                self.y_min_m,
                0.0,
                self.hojd_m,
                reflektionsfaktor_e=reflektionsfaktor_e,
                ensidig=True,
            ),
            VertikalReflektor(
                self.x_max_m,
                self.y_min_m,
                self.x_max_m,
                self.y_max_m,
                0.0,
                self.hojd_m,
                reflektionsfaktor_e=reflektionsfaktor_e,
                ensidig=True,
            ),
            VertikalReflektor(
                self.x_max_m,
                self.y_max_m,
                self.x_min_m,
                self.y_max_m,
                0.0,
                self.hojd_m,
                reflektionsfaktor_e=reflektionsfaktor_e,
                ensidig=True,
            ),
            VertikalReflektor(
                self.x_min_m,
                self.y_max_m,
                self.x_min_m,
                self.y_min_m,
                0.0,
                self.hojd_m,
                reflektionsfaktor_e=reflektionsfaktor_e,
                ensidig=True,
            ),
        )


@dataclass(frozen=True)
class StadsgataGeometri:
    """Inläst plangeometri ur bladet `Coordinates` i TestCityStreet."""

    byggnader: tuple[ByggnadPlan, ...]
    mottagare: dict[str, tuple[float, float]]  # "1".."4" -> (x_m, y_m)


def las_stadsgata_geometri(celler: Celler) -> StadsgataGeometri:
    """Tolkar bladet `Coordinates` i `TestCityStreet_20181101.xls`."""
    byggnader: list[ByggnadPlan] = []
    mottagare: dict[str, tuple[float, float]] = {}

    r = 0
    while r < len(celler):
        rad = celler[r]
        forsta = rad[0].strip() if rad else ""
        if forsta.lower().startswith("building"):
            namn = forsta
            xs: list[float] = []
            ys: list[float] = []
            # Första raden kan själv innehålla (namn, x, y) eller (namn, "X", "Y").
            if len(rad) >= 3:
                x0, y0 = _som_tal(rad[1]), _som_tal(rad[2])
                if x0 is not None and y0 is not None:
                    xs.append(float(x0))
                    ys.append(float(y0))
            r += 1
            while r < len(celler):
                rad_b = celler[r]
                if not rad_b:
                    break
                # Punkter kan ligga i kol 0–1 eller kol 1–2.
                x_val = _som_tal(rad_b[0]) if len(rad_b) >= 2 else None
                y_val = _som_tal(rad_b[1]) if len(rad_b) >= 2 else None
                if x_val is None and len(rad_b) >= 3 and not rad_b[0].strip():
                    x_val, y_val = _som_tal(rad_b[1]), _som_tal(rad_b[2])
                if x_val is None or y_val is None:
                    break
                xs.append(float(x_val))
                ys.append(float(y_val))
                r += 1
            if len(xs) < 3:
                raise BullerFel(f"Byggnad '{namn}' har färre än 3 hörnpunkter.")
            byggnader.append(ByggnadPlan(namn, min(xs), max(xs), min(ys), max(ys)))
            continue
        if forsta.lower().startswith("receiver"):
            r += 1
            pos_nr = 1
            while r < len(celler):
                rad_m = celler[r]
                x_val = _som_tal(rad_m[0]) if len(rad_m) >= 2 else None
                y_val = _som_tal(rad_m[1]) if len(rad_m) >= 2 else None
                if x_val is None and len(rad_m) >= 3 and not rad_m[0].strip():
                    x_val, y_val = _som_tal(rad_m[1]), _som_tal(rad_m[2])
                if x_val is None or y_val is None:
                    break
                mottagare[str(pos_nr)] = (float(x_val), float(y_val))
                pos_nr += 1
                r += 1
            continue
        r += 1

    if not byggnader or not mottagare:
        raise BullerFel("Kunde inte läsa byggnader och mottagare ur stadsgatans Coordinates.")
    return StadsgataGeometri(byggnader=tuple(byggnader), mottagare=mottagare)


def _fasad_tillhor_mottagare(rx: float, ry: float, ref: VertikalReflektor) -> bool:
    """Sant om mottagaren (rx, ry) ligger på själva fasaden `ref` (egna fasadens reflex)."""
    dx, dy = ref.x2_m - ref.x1_m, ref.y2_m - ref.y1_m
    langd = math.hypot(dx, dy)
    if langd <= 0.0:
        return False
    avst = abs((rx - ref.x1_m) * (dy / langd) - (ry - ref.y1_m) * (dx / langd))
    if avst > 0.05:
        return False
    s = ((rx - ref.x1_m) * dx + (ry - ref.y1_m) * dy) / (langd * langd)
    return -1.0e-3 <= s <= 1.0 + 1.0e-3


def _skarning_linje_rektangel(
    ax: float, ay: float, bx: float, by: float, b: ByggnadPlan
) -> tuple[float, float] | None:
    """Returnerar (t_in, t_ut) ∈ (0, 1) om sträckan AB går igenom byggnaden `b`."""
    dx, dy = bx - ax, by - ay
    t_min, t_max = 0.0, 1.0
    for p, q in (
        (-dx, ax - b.x_min_m),
        (dx, b.x_max_m - ax),
        (-dy, ay - b.y_min_m),
        (dy, b.y_max_m - ay),
    ):
        if abs(p) <= 1.0e-12:
            if q < 0.0:
                return None
        else:
            t = q / p
            if p < 0.0:
                t_min = max(t_min, t)
            else:
                t_max = min(t_max, t)
    if t_max - t_min <= 1.0e-5 or t_max <= 1.0e-5 or t_min >= 1.0 - 1.0e-5:
        return None
    return max(0.0, t_min), min(1.0, t_max)


def _profil_plan_och_byggnader(
    ax: float,
    ay: float,
    bx: float,
    by: float,
    kalla_hojd_m: float,
    mottagare_hojd_m: float,
    byggnader: tuple[ByggnadPlan, ...],
) -> Terrangprofil:
    """Bygger 2D-profil längs strålen AB med plan mark, vägyta (|x| ≤ 6 m) och ev. byggnadstak."""
    dist = math.hypot(bx - ax, by - ay)
    punkter: list[tuple[float, float]] = [(0.0, 0.0), (dist, 0.0)]
    harda: list[tuple[float, float]] = []

    # Vägyta: x ∈ [−6, +6].
    dx = bx - ax
    if abs(dx) > 1.0e-12:
        t1 = (-VAG_HALVBREDD_M - ax) / dx
        t2 = (VAG_HALVBREDD_M - ax) / dx
        lo, hi = max(0.0, min(t1, t2)), min(1.0, max(t1, t2))
        if hi > lo + 1.0e-6:
            harda.append((lo * dist, hi * dist))
            for t in (lo, hi):
                if 1.0e-4 < t * dist < dist - 1.0e-4:
                    punkter.append((t * dist, 0.0))
    elif -VAG_HALVBREDD_M <= ax <= VAG_HALVBREDD_M:
        harda.append((0.0, dist))

    # Eventuell byggnad i vägen (t.ex. Pos 4 bakom Building #3).
    for b in byggnader:
        traff = _skarning_linje_rektangel(ax, ay, bx, by, b)
        if traff is None:
            continue
        t_in, t_ut = traff
        d1, d2 = t_in * dist, t_ut * dist
        if d1 > BYGGNAD_KANT_EPS_M and d2 < dist - BYGGNAD_KANT_EPS_M:
            d_b1 = max(1.0e-3, d1 - BYGGNAD_KANT_EPS_M)
            d_b2 = min(dist - 1.0e-3, d2 + BYGGNAD_KANT_EPS_M)
            punkter = [p for p in punkter if not (d_b1 <= p[0] <= d_b2)]
            punkter.extend([(d_b1, 0.0), (d1, b.hojd_m), (d2, b.hojd_m), (d_b2, 0.0)])
            harda.append((d_b1, d_b2))

    punkter.sort(key=lambda p: p[0])
    sluta: list[tuple[float, float]] = [punkter[0]]
    for d, z in punkter[1:]:
        if d - sluta[-1][0] > 1.0e-4:
            sluta.append((d, z))
    x_arr = np.array([p[0] for p in sluta], dtype=np.float64)
    z_arr = np.array([p[1] for p in sluta], dtype=np.float64)
    sig = np.full(x_arr.shape[0] - 1, SIGMA_MARK_KNSM4, dtype=np.float64)
    for i in range(x_arr.shape[0] - 1):
        mitt = 0.5 * (float(x_arr[i]) + float(x_arr[i + 1]))
        if any(a <= mitt <= b for a, b in harda):
            sig[i] = SIGMA_VAG_KNSM4
    return Terrangprofil(
        x_m=x_arr,
        z_m=z_arr,
        flodesresistivitet_kNsm4=sig,
        rahet_m=np.zeros_like(sig),
        kalla_hojd_m=kalla_hojd_m,
        mottagare_hojd_m=mottagare_hojd_m,
    )


def _andra_ordningens_reflektion(
    sx: float,
    sy: float,
    rx: float,
    ry: float,
    ref1: VertikalReflektor,
    ref2: VertikalReflektor,
) -> tuple[Reflektionsgeometri, Reflektionsgeometri] | None:
    """Beräknar geometri för 2:a ordningens reflektion S → O1 (ref1) → O2 (ref2) → R."""
    s1x, s1y = spegelpunkt_plan(sx, sy, ref1.x1_m, ref1.y1_m, ref1.x2_m, ref1.y2_m)
    # Kontrollera att S ligger på rätt sida om ref1 (om ensidig).
    dx1, dy1 = ref1.x2_m - ref1.x1_m, ref1.y2_m - ref1.y1_m
    l1 = math.hypot(dx1, dy1)
    if ref1.ensidig:
        ds1 = (sx - ref1.x1_m) * (dy1 / l1) - (sy - ref1.y1_m) * (dx1 / l1)
        if ds1 <= 0.0:
            return None

    # Reflexpunkt O2 på ref2 mellan spegelkällan S1 och mottagaren R.
    g2 = reflektionspunkt_plan(s1x, s1y, rx, ry, ref2, max_utanfor_m=MAX_UTANFOR_KANT_M)
    if g2 is None:
        return None

    # Reflexpunkt O1 på ref1 mellan källan S och O2.
    g1 = reflektionspunkt_plan(sx, sy, g2.o_x_m, g2.o_y_m, ref1, max_utanfor_m=MAX_UTANFOR_KANT_M)
    if g1 is None:
        return None

    # Totala sträckor före/efter respektive reflexpunkt.
    d1 = g1.d1_m
    d12 = g1.d2_m
    d2 = g2.d2_m
    geom1 = Reflektionsgeometri(
        o_x_m=g1.o_x_m,
        o_y_m=g1.o_y_m,
        d1_m=d1,
        d2_m=d12 + d2,
        s_langs_m=g1.s_langs_m,
        reflektor_langd_m=g1.reflektor_langd_m,
        vinkel_mot_plan_rad=g1.vinkel_mot_plan_rad,
        spegel_x_m=g1.spegel_x_m,
        spegel_y_m=g1.spegel_y_m,
    )
    geom2 = Reflektionsgeometri(
        o_x_m=g2.o_x_m,
        o_y_m=g2.o_y_m,
        d1_m=d1 + d12,
        d2_m=d2,
        s_langs_m=g2.s_langs_m,
        reflektor_langd_m=g2.reflektor_langd_m,
        vinkel_mot_plan_rad=g2.vinkel_mot_plan_rad,
        spegel_x_m=g2.spegel_x_m,
        spegel_y_m=g2.spegel_y_m,
    )
    return geom1, geom2


def berakna_stadsgata(
    karna: P2PKarna,
    emission: Emissionsmodell | EmissionsmodellMedDirektivitet,
    geo: StadsgataGeometri,
    position: str,
    mottagare_hojd_m: float,
    reflektionsfaktor_e: float,
    vader: Vader,
    a_vagning_db: Flyttal,
    *,
    forhallanden: Vagforhallanden | None = None,
    direktivitet: bool = True,
    max_ordning: int = 2,
) -> tuple[float, Flyttal]:
    """Beräknar (LAeq_24h, Leq_24h) för ett stadsgatefall i RL 18-18 kap. 4."""
    if position not in geo.mottagare:
        raise BullerFel(f"Okänd mottagarposition '{position}' i stadsgata.")
    rx, ry = geo.mottagare[position]
    steg_m = 20.0 if position == "4" else 2.0

    # Alla utåtriktade fasader.
    alla_fasader: list[VertikalReflektor] = []
    for b in geo.byggnader:
        alla_fasader.extend(b.fasader(reflektionsfaktor_e))
    # Mottagarens egen fasad utelämnas som sista reflektor (frifältsnivå, RL 18-18 s. 14).
    egen_fasad_idx = {i for i, f in enumerate(alla_fasader) if _fasad_tillhor_mottagare(rx, ry, f)}

    trafik_per_korfalt = [Trafikflode(1, 5000.0, 50.0)]
    period_s = 24.0 * 3600.0
    hojder = emission.delkallhojder_m
    riktad = emission if isinstance(emission, EmissionsmodellMedDirektivitet) else None
    if not direktivitet or (riktad is not None and not riktad.stoder_direktivitet):
        riktad = None
    frekv = np.asarray(TERSBAND_HZ, dtype=np.float64)
    c0_m_s = 20.05 * math.sqrt(vader.temperatur_c + 273.15)

    # Minsta horisontella avstånd från mottagaren till vägens närmaste körfält (x = ±3 m).
    d_min_vag = min(abs(rx - (-3.0)), abs(rx - 3.0))
    max_avst = 20.0 * max(d_min_vag, 1.0)

    energi = np.zeros(ANTAL_BAND, dtype=np.float64)
    for x_korfalt in (-KORFALTSBREDD_STAD_M * 0.5, KORFALTSBREDD_STAD_M * 0.5):
        # Flytta källan med halva axelbredden mot mottagaren.
        sx = x_korfalt + (AXELHALVA_STAD_M if rx >= x_korfalt else -AXELHALVA_STAD_M)
        dx_m = rx - sx
        if abs(dx_m) >= max_avst:
            continue
        halv_y = math.sqrt(max_avst * max_avst - dx_m * dx_m)
        n_steg = max(1, int(round((2.0 * halv_y) / steg_m)))
        ds = (2.0 * halv_y) / n_steg
        y_start = ry - halv_y

        for k in range(n_steg):
            sy = y_start + (k + 0.5) * ds
            dist = math.hypot(rx - sx, ry - sy)

            # 1. Direkt väg S → R.
            profiler = [
                _profil_plan_och_byggnader(sx, sy, rx, ry, h, mottagare_hojd_m, geo.byggnader)
                for h in hojder
            ]
            if riktad is not None:
                # Färdriktning längs +y (tangent = (0, 1)).
                phi_dir = math.acos(min(1.0, max(-1.0, (ry - sy) / dist)))
                psi_dir = tuple(math.atan2(mottagare_hojd_m - h, dist) for h in hojder)
                eff_dir = riktad.ljudeffekt_db(
                    trafik_per_korfalt,
                    ds,
                    period_s,
                    hojder,
                    forhallanden,
                    Kallriktning(phi_dir, psi_dir),
                )
            else:
                eff_dir = emission.ljudeffekt_db(
                    trafik_per_korfalt, ds, period_s, hojder, forhallanden
                )
            for j, prof in enumerate(profiler):
                h_db = np.asarray(karna.overforing_db(prof, vader), dtype=np.float64)
                energi += np.power(10.0, (eff_dir[j, :] + h_db) / 10.0)

            if max_ordning < 1:
                continue

            # 2. Första ordningens reflektioner S → O → R (ej egen fasad).
            for i, ref in enumerate(alla_fasader):
                if i in egen_fasad_idx:
                    continue
                rg = reflektionspunkt_plan(sx, sy, rx, ry, ref)
                if rg is None:
                    continue
                # Blockering av andra fasader (endast för icke-skärmade gaturum; för Pos 4
                # tillåts diffraktion över byggnad på segmentet O→R, inte genom byggnad i S→O).
                if any(
                    linje_korsar_segment(sx, sy, rg.o_x_m, rg.o_y_m, f.x1_m, f.y1_m, f.x2_m, f.y2_m)
                    for idx_f, f in enumerate(alla_fasader)
                    if idx_f != i
                ):
                    continue
                if position != "4" and any(
                    linje_korsar_segment(rg.o_x_m, rg.o_y_m, rx, ry, f.x1_m, f.y1_m, f.x2_m, f.y2_m)
                    for idx_f, f in enumerate(alla_fasader)
                    if idx_f != i and idx_f not in egen_fasad_idx
                ):
                    continue
                d_tot = rg.d1_m + rg.d2_m
                if riktad is not None:
                    phi_r = math.acos(min(1.0, max(-1.0, (rg.o_y_m - sy) / rg.d1_m)))
                    psi_r = tuple(math.atan2(mottagare_hojd_m - h, d_tot) for h in hojder)
                    eff_r = riktad.ljudeffekt_db(
                        trafik_per_korfalt,
                        ds,
                        period_s,
                        hojder,
                        forhallanden,
                        Kallriktning(phi_r, psi_r),
                    )
                else:
                    eff_r = eff_dir

                x_r = np.array([0.0, d_tot], dtype=np.float64)
                z_r = np.array([0.0, 0.0], dtype=np.float64)
                sig_r = np.array([SIGMA_VAG_KNSM4], dtype=np.float64)
                rah_r = np.array([0.0], dtype=np.float64)
                for j, h in enumerate(hojder):
                    dlr = reflektionskorrektion_db(
                        frekv, c0_m_s, rg, ref, h, mottagare_hojd_m, z_mark_vid_o_m=0.0
                    )
                    if not bool(np.any(np.isfinite(dlr))):
                        continue
                    if position == "4":
                        # Utfälld väg över mellanliggande byggnad (t.ex. Building #3).
                        prof_r = _profil_plan_och_byggnader(
                            rg.spegel_x_m,
                            rg.spegel_y_m,
                            rx,
                            ry,
                            h,
                            mottagare_hojd_m,
                            geo.byggnader,
                        )
                    else:
                        prof_r = Terrangprofil(
                            x_m=x_r,
                            z_m=z_r,
                            flodesresistivitet_kNsm4=sig_r,
                            rahet_m=rah_r,
                            kalla_hojd_m=h,
                            mottagare_hojd_m=mottagare_hojd_m,
                        )
                    h_r = np.asarray(karna.overforing_db(prof_r, vader), dtype=np.float64)
                    with np.errstate(over="ignore"):
                        energi += np.power(10.0, (eff_r[j, :] + h_r + dlr) / 10.0)

            if max_ordning < 2:
                continue

            # 3. Andra ordningens reflektioner S → O1 → O2 → R (O2 ej på egen fasad).
            if position == "4":
                # (a) Källgaturummet: S → O1 → O2 → [diffraktion över byggnad] → R.
                for i1, ref1 in enumerate(alla_fasader):
                    for i2, ref2 in enumerate(alla_fasader):
                        if i2 == i1 or i2 in egen_fasad_idx:
                            continue
                        par = _andra_ordningens_reflektion(sx, sy, rx, ry, ref1, ref2)
                        if par is None:
                            continue
                        g1, g2 = par
                        if any(
                            (
                                idx_f != i1
                                and linje_korsar_segment(
                                    sx, sy, g1.o_x_m, g1.o_y_m, f.x1_m, f.y1_m, f.x2_m, f.y2_m
                                )
                            )
                            or (
                                idx_f not in (i1, i2)
                                and linje_korsar_segment(
                                    g1.o_x_m,
                                    g1.o_y_m,
                                    g2.o_x_m,
                                    g2.o_y_m,
                                    f.x1_m,
                                    f.y1_m,
                                    f.x2_m,
                                    f.y2_m,
                                )
                            )
                            for idx_f, f in enumerate(alla_fasader)
                        ):
                            continue
                        s2x, s2y = spegelpunkt_plan(
                            g1.spegel_x_m,
                            g1.spegel_y_m,
                            ref2.x1_m,
                            ref2.y1_m,
                            ref2.x2_m,
                            ref2.y2_m,
                        )
                        d_tot = g1.d1_m + g1.d2_m
                        if riktad is not None:
                            phi_r2 = math.acos(min(1.0, max(-1.0, (g1.o_y_m - sy) / g1.d1_m)))
                            psi_r2 = tuple(math.atan2(mottagare_hojd_m - h, d_tot) for h in hojder)
                            eff_r2 = riktad.ljudeffekt_db(
                                trafik_per_korfalt,
                                ds,
                                period_s,
                                hojder,
                                forhallanden,
                                Kallriktning(phi_r2, psi_r2),
                            )
                        else:
                            eff_r2 = eff_dir
                        for j, h in enumerate(hojder):
                            dlr1 = reflektionskorrektion_db(
                                frekv, c0_m_s, g1, ref1, h, mottagare_hojd_m, z_mark_vid_o_m=0.0
                            )
                            dlr2 = reflektionskorrektion_db(
                                frekv, c0_m_s, g2, ref2, h, mottagare_hojd_m, z_mark_vid_o_m=0.0
                            )
                            dlr_tot = dlr1 + dlr2
                            if not bool(np.any(np.isfinite(dlr_tot))):
                                continue
                            prof_r2 = _profil_plan_och_byggnader(
                                s2x, s2y, rx, ry, h, mottagare_hojd_m, geo.byggnader
                            )
                            h_r2 = np.asarray(karna.overforing_db(prof_r2, vader), dtype=np.float64)
                            with np.errstate(over="ignore"):
                                energi += np.power(10.0, (eff_r2[j, :] + h_r2 + dlr_tot) / 10.0)

                # (b) Mottagargaturummet (reciprocitet): S → [diffraktion] → O1 → O2 → R.
                for i2, ref2 in enumerate(alla_fasader):
                    if i2 in egen_fasad_idx:
                        continue
                    for i1, ref1 in enumerate(alla_fasader):
                        if i1 == i2:
                            continue
                        par_r = _andra_ordningens_reflektion(rx, ry, sx, sy, ref2, ref1)
                        if par_r is None:
                            continue
                        gr2, gr1 = par_r
                        # Fri sikt i mottagargaturummet (R→O2 och O2→O1); skärmning på S→O1.
                        if any(
                            (
                                idx_f != i2
                                and idx_f not in egen_fasad_idx
                                and linje_korsar_segment(
                                    rx, ry, gr2.o_x_m, gr2.o_y_m, f.x1_m, f.y1_m, f.x2_m, f.y2_m
                                )
                            )
                            or (
                                idx_f not in (i1, i2)
                                and linje_korsar_segment(
                                    gr2.o_x_m,
                                    gr2.o_y_m,
                                    gr1.o_x_m,
                                    gr1.o_y_m,
                                    f.x1_m,
                                    f.y1_m,
                                    f.x2_m,
                                    f.y2_m,
                                )
                            )
                            for idx_f, f in enumerate(alla_fasader)
                        ):
                            continue
                        if not any(
                            idx_f != i1
                            and linje_korsar_segment(
                                sx, sy, gr1.o_x_m, gr1.o_y_m, f.x1_m, f.y1_m, f.x2_m, f.y2_m
                            )
                            for idx_f, f in enumerate(alla_fasader)
                        ):
                            continue
                        r2x, r2y = spegelpunkt_plan(
                            gr2.spegel_x_m,
                            gr2.spegel_y_m,
                            ref1.x1_m,
                            ref1.y1_m,
                            ref1.x2_m,
                            ref1.y2_m,
                        )
                        g1_fr = Reflektionsgeometri(
                            gr1.o_x_m,
                            gr1.o_y_m,
                            gr1.d2_m,
                            gr1.d1_m,
                            gr1.s_langs_m,
                            gr1.reflektor_langd_m,
                            gr1.vinkel_mot_plan_rad,
                            0.0,
                            0.0,
                        )
                        g2_fr = Reflektionsgeometri(
                            gr2.o_x_m,
                            gr2.o_y_m,
                            gr2.d2_m,
                            gr2.d1_m,
                            gr2.s_langs_m,
                            gr2.reflektor_langd_m,
                            gr2.vinkel_mot_plan_rad,
                            0.0,
                            0.0,
                        )
                        d_tot = g1_fr.d1_m + g1_fr.d2_m
                        if riktad is not None:
                            d_dir = math.hypot(r2x - sx, r2y - sy)
                            phi_r2 = math.acos(min(1.0, max(-1.0, (r2y - sy) / d_dir)))
                            psi_r2 = tuple(math.atan2(mottagare_hojd_m - h, d_tot) for h in hojder)
                            eff_r2 = riktad.ljudeffekt_db(
                                trafik_per_korfalt,
                                ds,
                                period_s,
                                hojder,
                                forhallanden,
                                Kallriktning(phi_r2, psi_r2),
                            )
                        else:
                            eff_r2 = eff_dir
                        for j, h in enumerate(hojder):
                            dlr1 = reflektionskorrektion_db(
                                frekv, c0_m_s, g1_fr, ref1, h, mottagare_hojd_m, z_mark_vid_o_m=0.0
                            )
                            dlr2 = reflektionskorrektion_db(
                                frekv, c0_m_s, g2_fr, ref2, h, mottagare_hojd_m, z_mark_vid_o_m=0.0
                            )
                            dlr_tot = dlr1 + dlr2
                            if not bool(np.any(np.isfinite(dlr_tot))):
                                continue
                            prof_r2 = _profil_plan_och_byggnader(
                                sx, sy, r2x, r2y, h, mottagare_hojd_m, geo.byggnader
                            )
                            h_r2 = np.asarray(karna.overforing_db(prof_r2, vader), dtype=np.float64)
                            with np.errstate(over="ignore"):
                                energi += np.power(10.0, (eff_r2[j, :] + h_r2 + dlr_tot) / 10.0)
                continue

            for i1, ref1 in enumerate(alla_fasader):
                for i2, ref2 in enumerate(alla_fasader):
                    if i2 == i1 or i2 in egen_fasad_idx:
                        continue
                    par = _andra_ordningens_reflektion(sx, sy, rx, ry, ref1, ref2)
                    if par is None:
                        continue
                    g1, g2 = par
                    # Kontrollera fri sikt på delsträckorna S→O1, O1→O2, O2→R.
                    block = False
                    for idx_f, f in enumerate(alla_fasader):
                        if idx_f != i1 and linje_korsar_segment(
                            sx, sy, g1.o_x_m, g1.o_y_m, f.x1_m, f.y1_m, f.x2_m, f.y2_m
                        ):
                            block = True
                            break
                        if idx_f not in (i1, i2) and linje_korsar_segment(
                            g1.o_x_m, g1.o_y_m, g2.o_x_m, g2.o_y_m, f.x1_m, f.y1_m, f.x2_m, f.y2_m
                        ):
                            block = True
                            break
                        if (
                            idx_f != i2
                            and idx_f not in egen_fasad_idx
                            and linje_korsar_segment(
                                g2.o_x_m, g2.o_y_m, rx, ry, f.x1_m, f.y1_m, f.x2_m, f.y2_m
                            )
                        ):
                            block = True
                            break
                    if block:
                        continue
                    d_tot = g1.d1_m + g1.d2_m
                    if riktad is not None:
                        phi_r2 = math.acos(min(1.0, max(-1.0, (g1.o_y_m - sy) / g1.d1_m)))
                        psi_r2 = tuple(math.atan2(mottagare_hojd_m - h, d_tot) for h in hojder)
                        eff_r2 = riktad.ljudeffekt_db(
                            trafik_per_korfalt,
                            ds,
                            period_s,
                            hojder,
                            forhallanden,
                            Kallriktning(phi_r2, psi_r2),
                        )
                    else:
                        eff_r2 = eff_dir
                    x_r = np.array([0.0, d_tot], dtype=np.float64)
                    z_r = np.array([0.0, 0.0], dtype=np.float64)
                    sig_r = np.array([SIGMA_VAG_KNSM4], dtype=np.float64)
                    rah_r = np.array([0.0], dtype=np.float64)
                    for j, h in enumerate(hojder):
                        dlr1 = reflektionskorrektion_db(
                            frekv, c0_m_s, g1, ref1, h, mottagare_hojd_m, z_mark_vid_o_m=0.0
                        )
                        dlr2 = reflektionskorrektion_db(
                            frekv, c0_m_s, g2, ref2, h, mottagare_hojd_m, z_mark_vid_o_m=0.0
                        )
                        dlr_tot = dlr1 + dlr2
                        if not bool(np.any(np.isfinite(dlr_tot))):
                            continue
                        prof_r2 = Terrangprofil(
                            x_m=x_r,
                            z_m=z_r,
                            flodesresistivitet_kNsm4=sig_r,
                            rahet_m=rah_r,
                            kalla_hojd_m=h,
                            mottagare_hojd_m=mottagare_hojd_m,
                        )
                        h_r2 = np.asarray(karna.overforing_db(prof_r2, vader), dtype=np.float64)
                        with np.errstate(over="ignore"):
                            energi += np.power(10.0, (eff_r2[j, :] + h_r2 + dlr_tot) / 10.0)

    with np.errstate(divide="ignore"):
        leq_db = np.asarray(10.0 * np.log10(energi), dtype=np.float64)
    laeq_db = float(10.0 * np.log10(np.sum(np.power(10.0, (leq_db + a_vagning_db) / 10.0))))
    return laeq_db, leq_db
