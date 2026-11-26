# ruff: noqa: E501
"""Deterministiska regressionstester för Våg 5 Pass 4 webb/K38/Luft-fixar (`v5-finputs`):

1. Bugg 2 (P0 · K38-läcka): `KartverktygTjanst.kor_ekologi(..., skrivskyddad=True)` och
   `GET /api/analys/ekologi` med stängt K50-ritlager märker katalogen `k9_stangda/oppenhet.toml`
   (`oppen = false`) så `Oppenhet.bedom(...)` och `Oppenhet.bedom_korning(...)` ger `oppen=False`
   och hindrar läckage via MCP.
2. Bugg 4 (P1 · Steg 3 Luft CSV): `_validera_luft_csv` + Steg 3 Luft hanterar Flodens egen
   `timserie.csv` (med `halt_bidrag_ug_m3`, `svag_vind=1`, `ogiltig=1` och tom haltcell) och
   bevarar kolumnerna till `luft.mkn`, samt avvisar tvetydig flerreceptor-CSV med HTTP 400
   (slår aldrig tyst ihop 2 receptorer × 24 h till 48 falska timmar) medan `maxcell` eller
   explicit `receptor` filtreras entydigt.
"""

from __future__ import annotations

import json
import shutil
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from floden_mcp.oppenhet import Oppenhet
from karna import ritlager as rl
from luft.vindbibliotek import CellTimserie, skriv_timserier_csv
from tests.demovarld.test_demo import Demo
from tests.floden_mcp.test_mcp import Mcp
from tests.konto.conftest import ANNA, Miljo
from tests.webb.test_kartverktyg import demohus
from tests.webb.test_kartverktyg_ritlager import objekt_av
from webb.api import FlodenApiApp
from webb.fel import FlodenWebbFel
from webb.rapportjobb import _tolka_luft_indata, _validera_luft_csv

REPO = Path(__file__).resolve().parents[2]
DEMO = "demokvarteret"
URSPRUNG = {"Origin": "https://floden.test"}


def _timserie_obj(
    *,
    receptor: str | None,
    halter: list[float],
    svag_vind_idx: set[int] | None = None,
    ogiltig_idx: set[int] | None = None,
) -> CellTimserie:
    n = len(halter)
    sv = np.zeros(n, dtype=bool)
    og = np.zeros(n, dtype=bool)
    vals = np.asarray(halter, dtype=np.float64)
    for i in svag_vind_idx or ():
        sv[i] = True
        vals[i] = float("nan")
    for i in ogiltig_idx or ():
        og[i] = True
        vals[i] = float("nan")
    return CellTimserie(
        rad=0,
        kolumn=0,
        receptor=receptor,
        timme=tuple(f"2024-01-{(i // 24) + 1:02d}T{i % 24:02d}:00:00Z" for i in range(n)),
        halt_bidrag_ug_m3=vals,
        svag_vind=sv,
        ogiltig=og,
    )


# ------------------------------------------------------------------ Bugg 2: kor_ekologi(skrivskyddad=True) + K50/K38


def test_kor_ekologi_skrivskyddad_stangt_ritlager_sparras_av_k38_och_mcp(
    demo: Demo, tmp_path: Path
) -> None:
    """Bugg 2 (P0): `kor_ekologi(..., skrivskyddad=True)` och `GET /api/analys/ekologi` för ett
    scenario med stängt K50-ritlager skriver K9 i `k9_stangda/` med `oppenhet.toml` (`oppen = false`)
    så `Oppenhet.bedom(...)` och `Oppenhet.bedom_korning(...)` ger `oppen=False` och MCP nekar."""
    mcp = Mcp(demo, tmp_path / "m")
    app = mcp.app
    cache_ek = app.fragor.cachemapp / "_ekologi_las" / DEMO

    # Skapa ett sparat scenario i demokvarteret (öppet projekt med ekologimall) med ett stängt K50-ritlager.
    fri = demohus(18)
    stangt_lager = {
        **rl.standardlager()[0],
        "lager_id": "baskarta-hus",
        "namn": "Baskartans hus",
        "kalla": "baskarta:stockholm",
        "dataklass": "intern",
    }
    lager_lista = [*rl.standardlager(), stangt_lager]
    sparat = app.kartverktyg.spara_scenario(
        DEMO,
        None,
        {
            "namn": "Ekologi Stangt K50",
            "andringar": [],
            "ritlager": {"lager": lager_lista, "objekt": [objekt_av(fri, "baskarta-hus")]},
        },
    )
    slug = sparat["slug"]

    # Kör via `GET /api/analys/ekologi` (som anropar `kor_ekologi(..., skrivskyddad=True)`).
    status_kod, _, kropp = app.hantera_http(
        "GET",
        f"/api/analys/ekologi?omrade_id={DEMO}&scenario={slug}",
        {},
        lambda: b"",
    )
    assert status_kod == 200, kropp.decode("utf-8", "replace")
    svar = json.loads(kropp)
    korning_id = svar["korning_id"]

    # 1. `k9_stangda/oppenhet.toml` finns med `oppen = false`, och ingen fil ligger i öppna `k9/`.
    stangd_k9 = cache_ek / "k9_stangda" / f"{slug}__ekologi.json"
    stangd_meta = cache_ek / "k9_stangda" / "oppenhet.toml"
    oppen_k9 = cache_ek / "k9" / f"{slug}__ekologi.json"
    assert stangd_k9.is_file()
    assert stangd_meta.is_file() and "oppen = false" in stangd_meta.read_text(encoding="utf-8")
    assert not oppen_k9.exists()

    # 2. `Oppenhet.for_tjanst` bedömer både K9-filen och hela körningen som stängd (`oppen=False`).
    oppenhet = Oppenhet.for_tjanst(mcp.tjanst)
    b_fil = oppenhet.bedom(stangd_k9)
    assert b_fil.oppen is False and "inte öppen" in b_fil.orsak
    b_kor = oppenhet.bedom_korning(korning_id)
    assert b_kor.oppen is False

    # 3. MCP blockerar körningen (både status och resultat) och döljer den i listan.
    for verktyg in ("korning_status", "resultat_sammanfattning"):
        r = mcp.anropa(verktyg, {"korning_id": korning_id})
        assert r.is_error and "inte är öppna" in str(r.content), verktyg
    lista = mcp.ok("korningar_lista")
    assert korning_id not in {k["korning_id"] for k in lista["korningar"]}

    # 4. Samma geometri i öppet lager (och därefter ändrad till stängt lager) får ej läcka via cache-id.
    oppen_sparat = app.kartverktyg.spara_scenario(
        DEMO,
        None,
        {
            "namn": "Ekologi Overgang",
            "andringar": [],
            "ritlager": {"lager": lager_lista, "objekt": [objekt_av(fri, "hus")]},
        },
    )
    ov_slug = oppen_sparat["slug"]
    res_oppen = app.kartverktyg.kor_ekologi(DEMO, ov_slug, {}, skrivskyddad=True)
    assert oppenhet.bedom_korning(res_oppen["korning_id"]).oppen is True
    assert (cache_ek / "k9" / f"{ov_slug}__ekologi.json").is_file()

    # Uppdatera samma scenario (identisk geometri / sc.nyckel) till det stängda lagret:
    app.kartverktyg.spara_scenario(
        DEMO,
        ov_slug,
        {
            "namn": "Ekologi Overgang",
            "andringar": [],
            "ritlager": {"lager": lager_lista, "objekt": [objekt_av(fri, "baskarta-hus")]},
        },
    )
    res_stangd = app.kartverktyg.kor_ekologi(DEMO, ov_slug, {}, skrivskyddad=True)
    assert res_stangd["korning_id"] != res_oppen["korning_id"]
    assert not (cache_ek / "k9" / f"{ov_slug}__ekologi.json").exists()
    assert (cache_ek / "k9_stangda" / f"{ov_slug}__ekologi.json").is_file()
    assert oppenhet.bedom_korning(res_stangd["korning_id"]).oppen is False


# ------------------------------------------------------------------ Bugg 4: _validera_luft_csv + Steg 3 Luft


def test_validera_luft_csv_flodens_timserie_och_flera_receptorer_enhetstest(tmp_path: Path) -> None:
    """Bugg 4 (P1 enhetstest): Flodens egen `timserie.csv` med `svag_vind=1`/`ogiltig=1` och tom
    haltcell passerar och bevarar kolumner; 2 receptorer utan `maxcell` eller explicit `receptor`
    ger 400 (slås aldrig tyst ihop till 48 h); `maxcell` eller explicit `receptor` filtreras entydigt."""
    # 1. Flodens egen CSV med 24 giltiga timmar + 3 svag_vind + 2 ogiltig (= 29 rader).
    serie = _timserie_obj(
        receptor=None,
        halter=[15.0 + (i % 4) for i in range(29)],
        svag_vind_idx={2, 7, 15},
        ogiltig_idx={4, 20},
    )
    fil = skriv_timserier_csv(tmp_path / "enkel.csv", [serie])
    ra_csv = fil.read_text(encoding="utf-8")
    assert ",,1,0" in ra_csv or ",1,0" in ra_csv  # tom haltcell för svag_vind=1

    validerad = _validera_luft_csv(ra_csv, "luft.serie_fore_csv")
    rader = validerad.splitlines()
    assert rader[0] == "timme,halt_bidrag_ug_m3,svag_vind,ogiltig"
    assert len(rader) == 30  # rubrik + 29 datader (svag_vind/ogiltig bevaras till luft.mkn)

    # 2. För få giltiga timmar efter bortsortering av svag_vind/ogiltig (20 giltiga + 8 svag_vind = 28 rader) -> 400!
    for_fa = _timserie_obj(
        receptor=None,
        halter=[10.0] * 28,
        svag_vind_idx=set(range(8)),
    )
    for_fa_csv = skriv_timserier_csv(tmp_path / "for_fa.csv", [for_fa]).read_text(encoding="utf-8")
    with pytest.raises(FlodenWebbFel) as exc_fa:
        _validera_luft_csv(for_fa_csv, "luft.serie_fore_csv")
    assert exc_fa.value.status_kod == 400 and "24" in str(exc_fa.value)

    # 3. Tom haltcell på giltig timme (`svag_vind=0, ogiltig=0`) -> 400!
    trasig = "timme,halt_bidrag_ug_m3,svag_vind,ogiltig\n" + "\n".join(
        f"{h},,0,0" if h == 3 else f"{h},10.0,0,0" for h in range(24)
    )
    with pytest.raises(FlodenWebbFel) as exc_tom:
        _validera_luft_csv(trasig, "luft.serie_fore_csv")
    assert exc_tom.value.status_kod == 400 and "giltigt tal" in str(exc_tom.value)

    # 4. Ogiltig flagga i `svag_vind` -> 400!
    fel_flagga = "timme,halt_bidrag_ug_m3,svag_vind,ogiltig\n" + "\n".join(
        f"{h},10.0,kanske,0" if h == 1 else f"{h},10.0,0,0" for h in range(24)
    )
    with pytest.raises(FlodenWebbFel) as exc_fl:
        _validera_luft_csv(fel_flagga, "luft.serie_fore_csv")
    assert exc_fl.value.status_kod == 400 and "svag_vind" in str(exc_fl.value)

    # 5. Två receptorer (`r1`, `r2`, 24 h var) utan `maxcell` och utan `receptor` -> 400 (ej 48 h!).
    s_r1 = _timserie_obj(receptor="r1", halter=[10.0] * 24)
    s_r2 = _timserie_obj(receptor="r2", halter=[25.0] * 24)
    tva_rec_csv = skriv_timserier_csv(tmp_path / "tva.csv", [s_r1, s_r2]).read_text(encoding="utf-8")
    with pytest.raises(FlodenWebbFel) as exc_rec:
        _validera_luft_csv(tva_rec_csv, "luft.serie_fore_csv")
    assert exc_rec.value.status_kod == 400 and "flera receptorer" in str(exc_rec.value)

    # 6. Explicit `receptor="r2"` filtrerar entydigt till 24 rader för `r2` och bevarar kolumnerna.
    tolkad_r2 = _tolka_luft_indata(
        {"serie_fore_csv": tva_rec_csv, "bakgrund_ug_m3": 10.0, "receptor": "r2"}
    )
    assert tolkad_r2["parametrar"]["receptor"] == "r2"
    r2_rader = tolkad_r2["serie_fore_csv"].splitlines()
    assert r2_rader[0] == "timme,halt_bidrag_ug_m3,svag_vind,ogiltig,receptor"
    assert len(r2_rader) == 25 and all(r.endswith(",r2") for r in r2_rader[1:])

    # 7. Flera receptorer där `maxcell` finns: väljer entydigt `maxcell` (24 rader, ej 48!).
    s_max = _timserie_obj(receptor="maxcell", halter=[30.0] * 24)
    med_max_csv = skriv_timserier_csv(tmp_path / "max.csv", [s_max, s_r2]).read_text(encoding="utf-8")
    max_val = _validera_luft_csv(med_max_csv, "luft.serie_fore_csv").splitlines()
    assert len(max_val) == 25 and all(r.endswith(",maxcell") for r in max_val[1:])

    # 8. Även enkel CSV (`tid,lokal_ug_m3,receptor`) med 2 receptorer slås inte tyst ihop!
    enkel_tva = "tid,lokal_ug_m3,receptor\n" + "\n".join(
        f"{h},10.0,r1" for h in range(24)
    ) + "\n" + "\n".join(f"{h},20.0,r2" for h in range(24)) + "\n"
    with pytest.raises(FlodenWebbFel) as exc_enk:
        _validera_luft_csv(enkel_tva, "luft.serie_fore_csv")
    assert exc_enk.value.status_kod == 400 and "flera receptorer" in str(exc_enk.value)


@pytest.fixture
def luft_webb(demo: Demo, miljo: Miljo, tmp_path: Path) -> Iterator[tuple[FlodenApiApp, dict[str, str], Path]]:
    korningsmapp = tmp_path / "korningar"
    shutil.copytree(demo.rot / "korningar", korningsmapp)
    app = FlodenApiApp(
        testsvit_rot=REPO / "testsvit",
        konfig_fil=REPO / "config.toml",
        fragemappar=[demo.fragor],
        cachemapp=demo.rot / "cache",
        korningsmapp=korningsmapp,
        kartverktygsmapp=tmp_path / "kartverktyg",
        datamappar=[tmp_path / "data"],
        rapportmapp=tmp_path / "rapporter",
        konto=miljo.adapter,
    )
    app.fragor.demomapp = demo.varld
    org = miljo.register.skapa_organisation("Exempelkommun")
    miljo.register.lagg_till_medlem(
        miljo.register.forbered_konto("anna@example.test"), org, None, "test"
    )
    anna = miljo.webblasare()
    assert anna.logga_in(miljo.idp, ANNA)[0] == 302
    rub = {
        "Cookie": "; ".join(f"{k}={v}" for k, v in anna.kakor.items()),
        **URSPRUNG,
        **anna.csrf_headers(),
    }
    try:
        yield app, rub, tmp_path
    finally:
        app.rapportjobb.stang()


def _vanta_rapportjobb(app: FlodenApiApp, rub: dict[str, str], url: str, max_s: float = 180.0) -> dict[str, Any]:
    t0 = time.monotonic()
    while time.monotonic() - t0 < max_s:
        st, _, kropp = app.hantera_http("GET", url, {"Cookie": rub["Cookie"]}, lambda: b"")
        assert st == 200, kropp
        d = json.loads(kropp)
        if d["status"] in ("klar", "fel"):
            return d
        time.sleep(0.15)
    raise AssertionError("Rapportjobbet blev aldrig klart")


def test_luft_steg3_flodens_timserie_svag_vind_ogiltig_och_receptorer_e2e(
    luft_webb: tuple[FlodenApiApp, dict[str, str], Path],
) -> None:
    """Bugg 4 (P1 E2E): Steg 3 Luft med Flodens egen `timserie.csv` (`svag_vind=1`, `ogiltig=1`,
    tom haltcell) skapar godkänd rapport med korrekt `antal_timmar`/`antal_svag_vind_timmar`/
    `antal_ogiltiga_timmar` i K0-manifestet; 2 receptorer (`r1`, `r2`) ger HTTP 400 utan jobb;
    `maxcell` + `r2` räknar enbart `maxcell` (24 h, inte 48 h)."""
    app, rub, rot = luft_webb
    url = f"/api/projekt/{DEMO}/rapporter"

    # (a) Flodens egen timserie: 48 giltiga timmar + 6 svag_vind + 4 ogiltiga (= 58 rader).
    s_fore = _timserie_obj(
        receptor=None,
        halter=[18.0 + (i % 5) for i in range(58)],
        svag_vind_idx={1, 6, 12, 25, 33, 44},
        ogiltig_idx={3, 19, 30, 50},
    )
    csv_fore = skriv_timserier_csv(rot / "fore.csv", [s_fore]).read_text(encoding="utf-8")

    st, _, body = app.hantera_http(
        "POST",
        url,
        rub,
        lambda: json.dumps(
            {"amnen": ["luft"], "luft": {"serie_fore_csv": csv_fore, "bakgrund_ug_m3": 12.0}}
        ).encode("utf-8"),
    )
    assert st == 202, body.decode("utf-8", "replace")
    jobb = json.loads(body)
    slut = _vanta_rapportjobb(app, rub, jobb["status_url"])
    assert slut["status"] == "klar", slut
    assert slut["kontroll"] == {"godkand": True, "antal_fel": 0, "exporterad": True}

    man = json.loads(
        (rot / "korningar" / jobb["korning_id"] / "resultat" / "mkn_fore" / "bas" / "run.json")
        .read_text(encoding="utf-8")
    )
    assert man["matvarden"]["antal_timmar"] == 48
    assert man["matvarden"]["antal_hela_dygn"] == 2
    assert man["matvarden"]["antal_svag_vind_timmar"] == 6
    assert man["matvarden"]["antal_ogiltiga_timmar"] == 4

    # (b) 2 receptorer (`r1`, `r2`, 24 h var) utan `maxcell` och utan `receptor` -> HTTP 400, inget jobb!
    tva_csv = skriv_timserier_csv(
        rot / "tva_rec.csv",
        [
            _timserie_obj(receptor="r1", halter=[10.0] * 24),
            _timserie_obj(receptor="r2", halter=[20.0] * 24),
        ],
    ).read_text(encoding="utf-8")
    jobb_fore = set((rot / "rapporter" / "jobb").glob("j-*.json"))
    st_bad, _, body_bad = app.hantera_http(
        "POST",
        url,
        rub,
        lambda: json.dumps(
            {"amnen": ["luft"], "luft": {"serie_fore_csv": tva_csv, "bakgrund_ug_m3": 10.0}}
        ).encode("utf-8"),
    )
    assert st_bad == 400 and "flera receptorer" in json.loads(body_bad)["fel"]
    assert set((rot / "rapporter" / "jobb").glob("j-*.json")) == jobb_fore

    # (c) 2 receptorer med `maxcell` (24 h, halt=30) och `r2` (24 h, halt=5): bara `maxcell` (24 h, ej 48 h)!
    max_csv = skriv_timserier_csv(
        rot / "max_rec.csv",
        [
            _timserie_obj(receptor="maxcell", halter=[30.0] * 24),
            _timserie_obj(receptor="r2", halter=[5.0] * 24),
        ],
    ).read_text(encoding="utf-8")
    st_max, _, body_max = app.hantera_http(
        "POST",
        url,
        rub,
        lambda: json.dumps(
            {
                "amnen": ["luft"],
                "luft": {
                    "serie_fore_csv": max_csv,
                    "bakgrund_ug_m3": 10.0,
                    "emissionsfaktor_intervall": [1.0, 1.0],
                    "depositionsfaktor_intervall": [1.0, 1.0],
                },
            }
        ).encode("utf-8"),
    )
    assert st_max == 202, body_max.decode("utf-8", "replace")
    jobb_max = json.loads(body_max)
    slut_max = _vanta_rapportjobb(app, rub, jobb_max["status_url"])
    assert slut_max["status"] == "klar", slut_max
    man_max = json.loads(
        (rot / "korningar" / jobb_max["korning_id"] / "resultat" / "mkn_fore" / "bas" / "run.json")
        .read_text(encoding="utf-8")
    )
    assert man_max["matvarden"]["antal_timmar"] == 24
    assert man_max["matvarden"]["antal_hela_dygn"] == 1
    # Med exakt bakgrund=10 + maxcell=30 och faktorer [1, 1] måste årsmedel vara exakt 40.0 (inte medel med r2=5!).
    assert man_max["matvarden"]["arsmedel_lagst_ug_m3"] == pytest.approx(40.0)
    assert man_max["matvarden"]["arsmedel_hogst_ug_m3"] == pytest.approx(40.0)
