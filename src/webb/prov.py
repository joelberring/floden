"""API för webbläsarens WebGPU-prov (`#/prov/webgpu`): indata och facit för fasta provfall.

    GET /api/prov/webgpu/fall          katalog över provfall
    GET /api/prov/webgpu/fall/<id>     indata + serverns f64-facit (se benchmark.provfall, K37)

Bara superadmin när inloggning är på (kontrollen sker i `FlodenApiApp.hantera_http`); med
inloggning av, lokalt, är den öppen som resten av API:t. Provet är skissläge: klientens f32-tal
får aldrig användas som rapporttal.
"""

from __future__ import annotations

from typing import Any

PREFIX = "/api/prov/"


def prov_svar(verb: str, sokvag: str) -> tuple[int, dict[str, Any]]:
    """(status, JSON-objekt) för en sökväg under `/api/prov/`."""
    from benchmark.provfall import FALL_KATALOG, FORMAT_PROVFALL, MARKNING, provfall_svar

    delar = [d for d in sokvag.split("/") if d][2:]
    if verb != "GET":
        return 405, {"fel": "Metoden är inte tillåten.", "status": 405}
    if delar[:2] == ["webgpu", "fall"] and len(delar) == 2:
        return 200, {"format": FORMAT_PROVFALL, "markning": MARKNING, "fall": list(FALL_KATALOG)}
    if delar[:2] == ["webgpu", "fall"] and len(delar) == 3:
        svar = provfall_svar(delar[2])
        if svar is None:
            return 404, {"fel": f"Okänt provfall: {delar[2]}", "status": 404}
        return 200, svar
    return 404, {"fel": f"Okänd endpoint: {verb} {sokvag}", "status": 404}
