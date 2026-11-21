"""Kontrollutskrift av källmodellens tabeller, för avprickning mot källdokumenten."""

from __future__ import annotations

from buller.emission.direktivitet import Direktivitetsdata
from buller.emission.modell import BULLERTYPER, Emissionsdata
from buller.typer import TERSBAND_HZ

_VERTIKALA_FORMLER = {
    "k_sin_psi": "{k:g}·sin ψ",
    "k_ett_minus_cos2_psi": "{k:g}·(1 − cos² ψ)",
    "k_ett_minus_cos_psi": "{k:g}·(1 − cos ψ)",
}

_ATT_MATA_IN = (
    "## Att mata in för hand",
    "",
    "Fälten ovan är tomma. Koefficienterna står i SP Rapport 2006:12, bilaga A (tabell A.1,",
    "s. 46) och fördelningen på delkällor i avsnitt 2.2 (s. 9). Inga värden får gissas.",
)

_INTE_INFORT = (
    "## Inte infört i koden",
    "",
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


def _direktivitet_md(direktivitet: Direktivitetsdata | None) -> list[str]:
    """Direktivitetens koefficienter, uppställda som i SP 2006:12 avsnitt 2.5."""
    rader = ["", "## Direktivitet", ""]
    if direktivitet is None:
        return [*rader, "Saknas i datafilen: källmodellen räknar bara rundstrålande källor."]
    rader += [
        f"Källa: {direktivitet.kalla}",
        "",
        "∆L = ∆L_H(f, φ) + ∆L_V(f, ψ). φ är vinkeln mellan fordonets färdriktning och riktningen",
        "mot mottagaren i horisontalplanet, ψ riktningens vinkel över horisontalplanet.",
        "",
        "### Horisontell direktivitet",
        "",
        "Formtyp `k0_plus_k1_abs_cos_fi`: (k0 + k1·|cos φ|)·√cos ψ, koefficienter [k0, k1].",
        "Formtyp `polynom_i_pi_halva_minus_fi`: (Σ cₙ·(π/2 − φ)ⁿ)·√cos ψ med φ i radianer,",
        "koefficienter [c0, c1, c2, …]. Delkällor utan rad har ingen horisontell direktivitet.",
        "",
        "| Delkälla (m) | Formtyp | Koefficienter (dB) | Band (Hz) | Bullertyp i texten | Källa "
        "| ☐ |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for post in direktivitet.horisontell:
        if post.lagsta_frekvens_hz is None and post.hogsta_frekvens_hz is None:
            band = "alla"
        else:
            lagst = "" if post.lagsta_frekvens_hz is None else f"{post.lagsta_frekvens_hz:g}"
            hogst = "" if post.hogsta_frekvens_hz is None else f"{post.hogsta_frekvens_hz:g}"
            band = f"{lagst}–{hogst}, noll i övriga"
        koefficienter = ", ".join(f"{k:g}" for k in post.koefficienter_db)
        rader.append(
            f"| {post.hojd_m:g} | `{post.formtyp}` | {koefficienter} | {band} "
            f"| {post.bullertyp} | {post.kalla} | ☐ |"
        )
    hojder = [hojd for hojd, _, _ in direktivitet.vertikal_tabell]
    rader += [
        "",
        "### Vertikal direktivitet",
        "",
        f"Källa: {direktivitet.vertikal_kalla}. Band som tabellen inte tar upp har ingen",
        "vertikal direktivitet.",
        "",
        "| Frekvens (Hz) | " + " | ".join(f"hs = {h:g} m" for h in hojder) + " | ☐ |",
        "|" + " --- |" * (len(hojder) + 2),
    ]
    for nr, grupp in enumerate(direktivitet.vertikal_bandgrupper_hz):
        celler = []
        for _, former, k_db in direktivitet.vertikal_tabell:
            formel = _VERTIKALA_FORMLER.get(former[nr])
            celler.append("0" if formel is None else formel.format(k=k_db[nr]))
        frekvenser = ", ".join(f"{f:g}" for f in grupp)
        rader.append(f"| {frekvenser} | " + " | ".join(celler) + " | ☐ |")
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
    if data.fullstandig or data.direktivitet is not None:
        rader += _direktivitet_md(data.direktivitet)
    rader += ["", *(_INTE_INFORT if data.fullstandig else _ATT_MATA_IN)]
    return "\n".join(rader) + "\n"
