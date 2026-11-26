# ruff: noqa: E501
"""Vilken körning en rapport bygger på (U2, K48): användarens förslag först, demokörningen sist.

Samma val för webben (`POST /api/projekt/<id>/rapporter`) och MCP (`rapport_skapa` utan `korningar`). En körning
är ett *underlag* av ett av tre slag (se `FlodenApiApp.korningsunderlag`):

- `forslag`: körd för ett ritat förslag (scenario) i projektet;
- `lage_idag`: projektets egen körning av befintligt läge (U5a, bara `fore`/`bas`); kan ligga till grund för en
  nulägesrapport (läget idag) i steg 3 (Rapport) utan ritförslag;
- `egen`: annan klar körning som hör till projektet (till exempel startad via MCP);
- `demo`: demokörningen som följer med projektet, utan eget förslag.

Förvalet är användarens senaste körning för förslaget (`forslag`), annars den senaste egna körningen (`egen` med
jämförelse före `lage_idag` utan jämförelse), och demokörningen bara om inget annat finns. Då står det i rapporten
(Underlag i handlingsrutan). En körning som saknas, hör till ett annat projekt eller inte har ämnets analys ger
`UnderlagFel` (404), inte en tyst reserv.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from rapport.skissrapport import RapportTjanst

RANG: Final[dict[str, int]] = {"forslag": 0, "egen": 1, "lage_idag": 1, "demo": 2, "okand": 3}
"""Förvalsordning: lägre rang väljs först."""


class UnderlagFel(Exception):
    """Ingen körning att bygga rapporten på. `status` är HTTP-status för webben (404 saknas, 409 inget att välja)."""

    def __init__(self, text: str, status: int = 404) -> None:
        super().__init__(text)
        self.status = status


def underlagstext(underlag: dict[str, Any] | None, sbk: bool = True) -> str:
    """Raden om underlaget i handlingsrutan, eller tom text när slaget inte är känt."""
    if not underlag:
        return ""
    slag = underlag.get("slag")
    sc = underlag.get("scenario")
    if slag == "forslag":
        namn = str(sc.get("namn")) if isinstance(sc, dict) and sc.get("namn") else None
        return f"gäller förslaget ”{namn}”" if namn else "gäller ett ritat förslag"
    if slag == "demo":
        return "demokörningen som följer med projektet; inget eget förslag är beräknat"
    if slag == "lage_idag":
        return "läget idag: projektets befintliga läge, utan ritat förslag (ingen jämförelse före och efter)"
    if slag == "egen":
        return "gäller en egen körning i projektet (inget sparat förslag är kopplat till den)" if not sbk else "egen körning i projektet"
    return ""


def korningar_for(rt: RapportTjanst, projekt: str, amne: str) -> list[dict[str, Any]]:
    """Klara körningar i projektet som rapporten om `amne` kan bygga på (ämnets analys med jämförelse, eller
    projektets körning av läget idag när nulägesrapport stöds), i förvalsordning: användarens förslag, egna
    körningar (med jämförelse före läget idag), demokörningen; nyast först inom varje grupp."""
    app = rt.tjanst.app
    ut: list[dict[str, Any]] = []
    for k in app.fragor.lista_korningar()["korningar"]:
        kid = str(k["korning_id"])
        if k.get("status") != "klar" or app.projekt_for_korning(kid) != projekt:
            continue
        try:
            post = rt._korningspost(kid, amne)  # noqa: SLF001 - samma kontroll som rapport_skapa
        except Exception:  # noqa: BLE001 - körningen duger inte för ämnet
            continue
        u = app.korningsunderlag(kid)
        har_jamforelser = bool(post["jamforelser"])
        # Läget idag (nuläge utan ritförslag) stöds för ämnen som har nulägesrapport (skyfall, dagsljus m.fl.),
        # men inte för ekologi vars kontroll kräver före, efter och skillnad.
        if not har_jamforelser and (
            u["slag"] != "lage_idag" or not post["scenarier"] or amne == "ekologi"
        ):
            continue
        ut.append(
            {
                "korning_id": post["korning_id"],
                "fraga_id": post["fraga_id"],
                "skapad": k.get("skapad"),
                "slag": u["slag"],
                "scenario": u["scenario"],
                "_har_jamforelser": har_jamforelser,
            }
        )
    ut.sort(key=lambda x: str(x["skapad"] or ""), reverse=True)
    ut.sort(key=lambda x: (RANG.get(str(x["slag"]), 3), 0 if x["_har_jamforelser"] else 1))
    for post_ut in ut:
        post_ut.pop("_har_jamforelser", None)
    return ut


def valj_korning(
    rt: RapportTjanst,
    projekt: str,
    amne: str,
    *,
    korning_id: str | None = None,
    scenario: str | None = None,
) -> dict[str, Any]:
    """Körningen rapporten ska bygga på, som posten ur `korningar_for`.

    `korning_id` anges uttryckligen (valideras mot projektet, ämnet och eventuellt förslag), annars väljs
    användarens senaste körning för `scenario` (förslagets slug), och utan förslag förvalet ovan (inklusive
    läget idag när projektet saknar förslagskörning). Utan körning alls: `UnderlagFel` med vad som ska göras."""
    namn = {"skyfall": "skyfall", "dagsljus": "dagsljus och sol", "ekologi": "ekologi", "luft": "luft"}.get(amne, amne)
    kand = korningar_for(rt, projekt, amne)
    if scenario is not None:
        if not rt.tjanst.app.kartverktyg.finns_scenario(projekt, scenario):
            raise UnderlagFel(f"Förslaget '{scenario}' finns inte i projektet.", 404)
        kand_sc = [k for k in kand if isinstance(k["scenario"], dict) and k["scenario"].get("slug") == scenario]
    else:
        kand_sc = kand
    if korning_id is not None:
        post = next((k for k in kand if k["korning_id"] == korning_id), None)
        if post is None:
            # Samma svar för en körning som saknas och en i ett annat projekt: ingen ledtråd om andras körningar.
            raise UnderlagFel(f"Körningen '{korning_id}' finns inte i projektet eller duger inte för {amne}.", 404)
        if scenario is not None and post not in kand_sc:
            raise UnderlagFel(f"Körningen '{korning_id}' hör inte till förslaget '{scenario}'.", 400)
        return post
    if kand_sc:
        return kand_sc[0]
    if scenario is not None:
        raise UnderlagFel(
            f"Förslaget '{scenario}' har ingen klar beräkning av {namn} med före och efter. "
            "Kör beräkningen i steg 2 (Karta & analys) först.",
            409,
        )
    raise UnderlagFel(
        f"Det finns ingen klar beräkning av {namn} (läget idag eller förslag) i projektet. "
        "Kör en beräkning i steg 2 (Karta & analys) först.",
        409,
    )
