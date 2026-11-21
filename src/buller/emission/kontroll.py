"""Kontrollutskrift av källmodellens tabeller, för avprickning mot källdokumenten."""

from __future__ import annotations

from buller.emission.modell import BULLERTYPER, Emissionsdata
from buller.typer import TERSBAND_HZ

_ATT_MATA_IN = (
    "## Att mata in för hand",
    "",
    "Fälten ovan är tomma. Koefficienterna står i SP Rapport 2006:12, bilaga A (tabell A.1,",
    "s. 46) och fördelningen på delkällor i avsnitt 2.2 (s. 9). Inga värden får gissas.",
)

_INTE_INFORT = (
    "## Inte infört i koden",
    "",
    "- Direktivitet: SP 2006:12 ekvation (2.9)–(2.12) och tabell 2.4 (s. 13–16).",
    "- Våt vägbana: ekvation (2.6a–c) (s. 13).",
    "- Dubbdäck: tabell A.3 är inläst (ovan) men används inte; hur andelen dubbade däck vägs",
    "  in står inte i avsnitt 8.1.",
    "- Övriga rader i tabell 2.1 (temperaturkoefficient för andra beläggningar än DAC), porösa",
    "  beläggningar och användardefinierade beläggningskorrektioner (avsnitt 8.3, s. 40).",
    "- Extra delkälla på 3,5 m för tunga fordon med högt avgasrör (avsnitt 2.2).",
    "- Motorbroms för kategori 3 och lutning som ekvivalent acceleration (text efter ekv. 2.8).",
    "- Maximalnivå: avsnitt 2.6, ekvation (2.13)–(2.16) (s. 16–20).",
    "- Standardfördelning mellan kategori 2 och 3 (tabell 2.10, s. 21) och vägytans impedans",
    "  (tabell 10.1, s. 43) hör till inställningar respektive utbredning.",
)


def _korrektioner_md(data: Emissionsdata) -> list[str]:
    k = data.korrektioner
    rader = ["", "## Korrektioner", ""]
    for namn, avsnitt in (
        ("Antal axlar", k.axlar),
        ("Beläggning", k.belaggning),
        ("Beläggningens ålder", k.belaggningsalder),
        ("Lufttemperatur", k.temperatur),
        ("Acceleration", k.acceleration),
    ):
        if avsnitt is None:
            rader.append(f"- {namn}: saknas i datafilen")
            continue
        varden = "; ".join(f"{n} = {v}" for n, v in avsnitt.items() if n != "kalla")
        rader.append(f"- ☐ {namn}: {varden}. Källa: {avsnitt.get('kalla', '')}")
    kolumner: list[tuple[str, list[float]]] = []
    if k.regional is not None:
        rader.append(
            f"- Regional korrektion av aR för {', '.join(k.regional['lander'])}. "
            f"Källa: {k.regional.get('kalla', '')}"
        )
        kolumner.append(("tabell A.2, tillägg aR (dB)", list(k.regional["aR_tillagg_db"])))
    if k.dubbdack is not None:
        rader.append(
            f"- Dubbdäck (används: {'ja' if k.dubbdack.get('anvands') else 'nej'}). "
            f"Källa: {k.dubbdack.get('kalla', '')}"
        )
        kolumner.append(("tabell A.3, a (dB)", list(k.dubbdack["a_db"])))
        kolumner.append(("tabell A.3, b (dB)", list(k.dubbdack["b_db"])))
    if kolumner:
        rader += [
            "",
            "| Frekvens (Hz) | " + " | ".join(n for n, _ in kolumner) + " | ☐ |",
            "|" + " --- |" * (len(kolumner) + 2),
        ]
        for band, frekvens in enumerate(TERSBAND_HZ):
            celler = " | ".join(f"{v[band]:g}" for _, v in kolumner)
            rader.append(f"| {frekvens:g} | {celler} | ☐ |")
    return rader


def kontrollutskrift_md(data: Emissionsdata) -> str:
    """Markdown med alla inlästa tabeller, en rad per tersband som i källtabellerna."""
    rader = [
        "# Kontroll av källmodellens tabeller",
        "",
        "Genererad av `buller.emission.kontroll.kontrollutskrift_md`. Ändra inte för hand:",
        "rätta datafilen och generera om.",
        "",
        f"- Datafil: `src/buller/emission/data/{data.fil.name}` (dataversion {data.dataversion})",
        f"- Syntetisk: {'ja' if data.syntetisk else 'nej'}",
        f"- Verifierad av människa: {'ja' if data.verifierad_av_manniska else 'nej'}",
        f"- Fullständig: {'ja' if data.fullstandig else 'NEJ – tabeller saknas, se nedan'}",
        "",
        "## Källor",
        "",
    ]
    rader += [f"- {k.get('id', '?')}: {k.get('dokument', '')} {k.get('url', '')}".rstrip()
              for k in data.kallor]
    rader += [
        "",
        "## Delkällor",
        "",
        f"Källa: {data.delkallor_kalla}",
        "",
        "| Delkälla | Höjd över vägbanan (m) | Kontrollerad |",
        "| --- | --- | --- |",
    ]
    rader += [f"| {i + 1} | {h:g} | ☐ |" for i, h in enumerate(data.delkallhojder_m)]

    for kategori in sorted(data.kategorinamn):
        rader += ["", f"## Kategori {kategori}: {data.kategorinamn[kategori]}", ""]
        if kategori not in data.kategorier:
            saknade = [s for s in data.saknas if s.startswith(f"kategorier.{kategori}.")]
            rader.append("**Inte ifylld.** Tomma fält:")
            rader.append("")
            rader += [f"- `{s}`" for s in saknade]
            continue
        tabeller = data.kategorier[kategori].tabeller
        for bullertyp in BULLERTYPER:
            t = tabeller[bullertyp]
            rader += [
                f"- {bullertyp}: formtyp `{t.formtyp}`, referenshastighet "
                f"{t.referenshastighet_km_h:g} km/h, källa: {t.kalla}"
            ]
        huvud = ["Frekvens (Hz)"]
        for bullertyp in BULLERTYPER:
            huvud += [f"a {bullertyp} (dB)", f"b {bullertyp} (dB)"]
            huvud += [f"andel {bullertyp} {h:g} m" for h in data.delkallhojder_m]
        rader += ["", "| " + " | ".join(huvud) + " | ☐ |", "|" + " --- |" * (len(huvud) + 1)]
        for band, frekvens in enumerate(TERSBAND_HZ):
            celler = [f"{frekvens:g}"]
            for bullertyp in BULLERTYPER:
                t = tabeller[bullertyp]
                celler += [f"{t.a_db[band]:g}", f"{t.b_db[band]:g}"]
                celler += [f"{t.fordelning[j, band]:g}" for j in range(len(data.delkallhojder_m))]
            rader.append("| " + " | ".join(celler) + " | ☐ |")
    rader += _korrektioner_md(data)
    rader += ["", *(_INTE_INFORT if data.fullstandig else _ATT_MATA_IN)]
    return "\n".join(rader) + "\n"
