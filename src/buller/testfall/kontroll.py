"""Kontrollutskrift av digitaliserade testfall, för avprickning mot källdokumenten."""

from __future__ import annotations

from collections.abc import Sequence

from buller.testfall.format import Facit, N2kFall
from buller.typer import TERSBAND_HZ


def _tal(varde: float) -> str:
    return f"{varde:.2f}"


def kontrollutskrift_md(fall_och_facit: Sequence[tuple[N2kFall, Facit]]) -> str:
    """Markdown med indata och facit per fall, uppställt som bladen i källrapporten."""
    rader = [
        "# Kontroll av digitaliserade testfall",
        "",
        "Genererad av `buller.testfall.kontroll.kontrollutskrift_md`. Ändra inte för hand:",
        "rätta fallfilerna och generera om. Bocka av varje tabell mot källdokumentet.",
    ]
    for fall, facit in fall_och_facit:
        kalla = ", ".join(f"{k}: {v}" for k, v in fall.kalla.items())
        rader += [
            "",
            f"## {fall.id} – {fall.namn}",
            "",
            f"- Beskrivning: {fall.beskrivning}",
            f"- Källa: {kalla}",
            f"- Får användas som facit: {'ja' if facit.ar_facit else 'NEJ'} "
            f"({facit.ar_facit_skal})",
            f"- Indata fullständig: {'ja' if fall.indata_fullstandig else 'nej'}",
        ]
        rader += [f"  - saknas: {s}" for s in fall.indata_saknas]
        if fall.trafik:
            rader += ["", "| Kategori | Antal fordon | Hastighet (km/h) | Period | ☐ |",
                      "| --- | --- | --- | --- | --- |"]
            rader += [
                f"| {p.kategori} | {p.antal_fordon:g} | {p.hastighet_km_h:g} | {p.period} "
                f"({fall.perioder_h[p.period]:g} h) | ☐ |"
                for p in fall.trafik
            ]
        if fall.mottagare_hojd_m is not None:
            rader += ["", f"Mottagarhöjd hr: {fall.mottagare_hojd_m:g} m ☐"]
        if fall.vader is not None:
            v = fall.vader
            rader += [
                "",
                "| t0 (°C) | RH (%) | z0 (m) | zu (m) | u (m/s) | su (m/s) | dt/dz (K/m) "
                "| sdt/dz (K/m) | Cv² | Ct² | ☐ |",
                "|" + " --- |" * 11,
                f"| {v.temperatur_c:g} | {v.relativ_fuktighet_procent:g} | {v.z0_m:g} "
                f"| {v.zu_m:g} | {v.vind_m_s:g} | {v.std_vind_m_s:g} | {v.dt_dz_k_m:g} "
                f"| {v.std_dt_dz_k_m:g} | {v.cv2:g} | {v.ct2:g} | ☐ |",
            ]
        for nyckel, varde in fall.vader_tillagg.items():
            rader.append(f"- {nyckel}: {varde:g} ☐")
        if fall.terrang is not None:
            t = fall.terrang
            rader += ["", "| X (m) | Z (m) | Flödesresistivitet (kNs/m⁴) | Råhet (m) | ☐ |",
                      "| --- | --- | --- | --- | --- |"]
            for i, (x, z) in enumerate(zip(t.x_m, t.z_m, strict=True)):
                sista = i == len(t.x_m) - 1
                sigma = "–" if sista else f"{t.flodesresistivitet_kNsm4[i]:g}"
                rahet = "–" if sista else f"{t.rahet_m[i]:g}"
                rader.append(f"| {x:g} | {z:g} | {sigma} | {rahet} | ☐ |")
        for nyckel, ovrigt_varde in fall.ovrigt.items():
            rader.append(f"- {nyckel}: {ovrigt_varde} ☐")

        rader += ["", "Totalnivåer (dB):", ""]
        storheter = sorted({n for s in facit.serier for n in s.totalnivaer_db})
        rader += ["| Serie | Villkor | " + " | ".join(storheter) + " | ☐ |",
                  "|" + " --- |" * (len(storheter) + 3)]
        for s in facit.serier:
            villkor = ", ".join(f"{k} = {v}" for k, v in s.villkor.items()) or "–"
            varden = [_tal(s.totalnivaer_db[n]) if n in s.totalnivaer_db else "–"
                      for n in storheter]
            rader.append(f"| {s.id} | {villkor} | " + " | ".join(varden) + " | ☐ |")

        kolumner = [(s.id, n) for s in facit.serier for n in s.spektrum_db]
        rader += ["", "Spektrum (dB):", "",
                  "| Frekvens (Hz) | " + " | ".join(f"{n} [{i}]" for i, n in kolumner) + " | ☐ |",
                  "|" + " --- |" * (len(kolumner) + 2)]
        serier = {s.id: s for s in facit.serier}
        for band, frekvens in enumerate(TERSBAND_HZ):
            varden = [_tal(float(serier[i].spektrum_db[n][band])) for i, n in kolumner]
            rader.append(f"| {frekvens:g} | " + " | ".join(varden) + " | ☐ |")
    return "\n".join(rader) + "\n"
