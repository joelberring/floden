# ruff: noqa: E501
"""Nätverksfria enhets- och strikta negativa OSL/PII-säkerhetstester för `data_inlasning.artdatabanken`."""

from __future__ import annotations

import json
from typing import Any

import pytest

from data_inlasning.artdatabanken import (
    OSL_VARNING,
    ArtdatabankenFel,
    ar_skyddsklassad_eller_diffuserad,
    hamta_publika_artfynd,
    tolka_och_filtrera_sos_svar,
)

KARNA_3011 = (-210_000.0, 6_413_500.0, -209_500.0, 6_414_000.0)


def _publik_post(**extra: Any) -> dict[str, Any]:
    bas: dict[str, Any] = {
        "taxon": {
            "id": 102001,
            "vernacularName": "Ekoxe",
            "scientificName": "Lucanus cervus",
            "redListCategory": "NT",
            "isProtectedSpecies": True,
        },
        "event": {"startDate": "2024-06-18"},
        "location": {
            "decimalLatitude": 57.698,
            "decimalLongitude": 11.958,
            "coordinateUncertaintyInMeters": 25,
            "accessLevel": "public",
        },
    }
    bas.update(extra)
    return bas


@pytest.mark.parametrize(
    "osl_mutation",
    [
        {"isProtected": True},
        {"protected": True},
        {"sensitive": True},
        {"isSensitive": True},
        {"diffused": True},
        {"isDiffused": True},
        {"isHidden": True},
        {"isConfidential": True},
        {"blurRadius": 2500},
        {"blurRadiusInMeters": 1000},
        {"diffusionRadius": 500},
        {"accessLevel": "restricted"},
        {"accessLevel": "private"},
        {"sensitivityLevel": 3},
        {"protectionLevel": "OSL"},
        {"informationWithheld": "Koordinater diffuserade pga skyddsklassning"},
        {"dataGeneralizations": "5 km ruta"},
        {"location": {"decimalLatitude": 57.698, "decimalLongitude": 11.958, "coordinateUncertaintyInMeters": 5000}},
        {"location": {"decimalLatitude": 57.698, "decimalLongitude": 11.958, "coordinateUncertaintyInMeters": 10000}},
        {"location": {"decimalLatitude": 57.698, "decimalLongitude": 11.958, "accuracy": 5000}},
        {"location": {"decimalLatitude": 57.698, "decimalLongitude": 11.958, "isProtected": True}},
        {"location": {"decimalLatitude": 57.698, "decimalLongitude": 11.958, "diffused": "true"}},
        {"taxon": {"id": 102002, "vernacularName": "Kungsörn", "scientificName": "Aquila chrysaetos", "redListCategory": "NT", "isSensitiveSpecies": True}},
    ],
)
def test_hard_osl_sparr_kastar_ovillkorligt_alla_skyddsklassade_och_diffuserade_koordinater(
    osl_mutation: dict[str, Any],
) -> None:
    """Negativt säkerhetstest (OSL 20 kap. 1 §): skyddsklassade/diffuserade poster får ALDRIG sparas med koordinater."""
    post = _publik_post(**osl_mutation)
    assert ar_skyddsklassad_eller_diffuserad(post) is True

    res = tolka_och_filtrera_sos_svar([post], karna_3011=KARNA_3011)
    assert res.fynd == ()
    assert res.artlista == ()
    assert res.antal_skyddsklassade_filtrerade == 1
    assert res.osl_varning == OSL_VARNING


def test_hard_pii_sparr_sparar_aldrig_observator_epost_eller_fritext() -> None:
    """Negativt PII-test: observatörsnamn, e-post, personnummer och kommentarer läses/sparas ALDRIG."""
    post = _publik_post(
        recordedBy="Anna Andersson",
        reportedBy="Kalle Karlsson",
        observers=["Bo Bengtsson"],
        user="anna.andersson",
        email="anna.andersson@example.se",
        occurrenceRemarks="Sett på grannens tomt Storgatan 14, kontakta 19850101-1234",
        eventRemarks="Privat kommentar",
        comment="070-1234567",
        locality="Storgatan 14, privat trädgård",
    )
    res = tolka_och_filtrera_sos_svar([post], karna_3011=KARNA_3011)
    assert len(res.fynd) == 1
    assert len(res.artlista) == 1
    assert res.osl_varning == OSL_VARNING

    f = res.fynd[0]
    feat = f.som_geojson_feature()
    # Strikt allowlist av fält i GeoJSON-properties
    assert set(feat["properties"]) == {
        "taxon_id",
        "svenskt_namn",
        "vetenskapligt_namn",
        "rodlistekategori",
        "fridlyst",
        "fyndar",
        "osakerhet_m",
    }
    serialiserat = json.dumps(
        {
            "fynd": [feat],
            "artlista": [a.som_dict() for a in res.artlista],
            "osl_varning": res.osl_varning,
            "varningar": list(res.varningar),
        },
        ensure_ascii=False,
    )
    for hemlig in (
        "Anna Andersson",
        "Kalle Karlsson",
        "Bo Bengtsson",
        "anna.andersson",
        "example.se",
        "Storgatan 14",
        "19850101-1234",
        "Privat kommentar",
        "070-1234567",
        "recordedBy",
        "reportedBy",
    ):
        assert hemlig not in serialiserat


def test_hamta_publika_artfynd_paginering_header_och_aldrig_nyckellacka() -> None:
    hemlig_nyckel = "SLU_SUPER_SECRET_KEY_9999"
    skickade_headers: list[dict[str, str]] = []
    skickade_kroppar: list[dict[str, Any]] = []

    lc_post = {
        "taxon": {
            "id": 999,
            "vernacularName": "Gråsparv",
            "scientificName": "Passer domesticus",
            "redListCategory": "LC",
            "isProtectedSpecies": False,
        },
        "event": {"startDate": "2024-05-01"},
        "location": {"decimalLatitude": 57.699, "decimalLongitude": 11.960, "coordinateUncertaintyInMeters": 50},
    }
    skyddad_post = _publik_post(isProtected=True)

    def falsk_sos(url: str, headers: dict[str, str], kropp: bytes) -> bytes:
        skickade_headers.append(dict(headers))
        skickade_kroppar.append(json.loads(kropp.decode("utf-8")))
        if "skip=0" in url:
            return json.dumps({"totalCount": 3, "records": [_publik_post(), skyddad_post]}).encode("utf-8")
        return json.dumps({"totalCount": 3, "records": [lc_post]}).encode("utf-8")

    res = hamta_publika_artfynd(
        KARNA_3011,
        api_nyckel=hemlig_nyckel,
        buffert_m=1000.0,
        ar_bakat=10,
        referensar=2026,
        sidstorlek=2,
        hamtare=falsk_sos,
    )
    assert len(skickade_headers) == 2
    assert skickade_headers[0]["Ocp-Apim-Subscription-Key"] == hemlig_nyckel
    assert skickade_kroppar[0]["output"]["sensitiveObservations"] is False
    assert skickade_kroppar[0]["date"]["startDate"] == "2016-01-01"
    assert skickade_kroppar[0]["date"]["endDate"] == "2026-12-31"
    assert len(res.fynd) == 1
    assert res.fynd[0].svenskt_namn == "Ekoxe"
    assert res.antal_skyddsklassade_filtrerade == 1
    assert res.antal_bortfiltrerade_ej_prioriterade == 1
    assert res.osl_varning == OSL_VARNING
    assert hemlig_nyckel not in repr(res)

    # Felmeddelanden får ALDRIG läcka API-nyckeln
    def kraschande_sos(_url: str, _headers: dict[str, str], _kropp: bytes) -> bytes:
        raise RuntimeError(f"upstream failed with key {hemlig_nyckel}")

    with pytest.raises(ArtdatabankenFel) as exc_info:
        hamta_publika_artfynd(KARNA_3011, api_nyckel=hemlig_nyckel, hamtare=kraschande_sos)
    assert hemlig_nyckel not in str(exc_info.value)


def test_artdatabanken_ssrf_allowlist_och_saknad_nyckel() -> None:
    with pytest.raises(ArtdatabankenFel, match="API-nyckel"):
        hamta_publika_artfynd(KARNA_3011, api_nyckel="   ", hamtare=lambda _u: b"{}")

    for ond_url in (
        "http://api.artdatabanken.se/species-observation-system/v1",
        "https://evil.example.com/species-observation-system/v1",
        "https://user@api.artdatabanken.se/species-observation-system/v1",
    ):
        with pytest.raises(ArtdatabankenFel):
            hamta_publika_artfynd(
                KARNA_3011, api_nyckel="testnyckel", bas_url=ond_url, hamtare=lambda _u: b"{}"
            )
