"""Skrivning av en bullerkörning till fil: resultat, överföringslager och `run.json`."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import numpy as np

from buller.lagen.korning import BullerKorning
from buller.operator.npzfil import skriv_npz_deterministiskt
from buller.typer import TERSBAND_HZ
from gemensamt.hash import sha256_fil
from gemensamt.manifest import FilPost, RunManifest, skriv_manifest

RESULTATFORMAT = "floden.buller.resultat/v1"
RESULTATFIL = "buller_resultat.npz"
LAGERFIL = "overforingslager.npz"
MANIFESTFIL = "run.json"


def skriv_korning(korning: BullerKorning, katalog: str | Path) -> RunManifest:
    """Skriver körningen till `katalog` och returnerar manifestet med utdataposter.

    Filer: `buller_resultat.npz` (nivå per mottagare och band, A-vägd nivå, garanterad
    felgräns, läge och märkning), `overforingslager.npz` (för omkombination och inkrementell
    omräkning) och `run.json` (K0/v1) med sha256 för de två andra. Npz-filerna skrivs
    deterministiskt: samma körning ger samma kontrollsummor.
    """
    mal = Path(katalog)
    mal.mkdir(parents=True, exist_ok=True)
    resultat = korning.resultat
    resultatfil = skriv_npz_deterministiskt(
        mal / RESULTATFIL,
        {
            "format": np.array(RESULTATFORMAT),
            "lage": np.array(resultat.lage),
            "markning": np.array(resultat.markning),
            "mottagare_id": resultat.mottagare_id,
            "tersband_hz": np.asarray(TERSBAND_HZ, dtype=np.float64),
            "niva_db": resultat.niva_db,
            "niva_a_db": resultat.niva_a_db,
            "felgrans_db": resultat.felgrans_db,
        },
    )
    lagerfil = korning.lager.spara(mal / LAGERFIL)
    manifest = dataclasses.replace(
        korning.manifest,
        utdata=[
            *korning.manifest.utdata,
            FilPost(roll="buller_resultat", fil=RESULTATFIL, sha256=sha256_fil(resultatfil)),
            FilPost(roll="overforingslager", fil=LAGERFIL, sha256=sha256_fil(lagerfil)),
        ],
    )
    skriv_manifest(manifest, mal / MANIFESTFIL)
    return manifest
