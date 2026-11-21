"""Inga platshållare i klienten: varken i källkoden (textliteraler och HTML) eller i bygget.

En platshållare är en hakparentes med ett ord, `[Förvaltning]`, `[antal]`, eller ett tecken som
`[– m]`. Kod som `[e, n]` är inte det, så bara text i strängar och mallar granskas (uttryck i
`${...}` tas bort).
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
KLIENT = REPO / "webb"
# `[Förvaltning]`, `[antal]`, `[förnamn]` och tal-platshållare som `[– m]`, `[± m]`.
PLATSHALLARE = re.compile(
    r"\[[A-Za-zÅÄÖåäö]{3,}[A-Za-zÅÄÖåäö /…]*\]|\[[–±…][^\]\[]*\]"
)
LITERAL = re.compile(r"`(?:\\.|[^`\\])*`|\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*'")
KOMMENTAR = re.compile(r"/\*.*?\*/|(?<![:\"'])//[^\n]*", re.S)


def _text_i_litteraler(kalla: str) -> list[str]:
    kalla = KOMMENTAR.sub("", kalla)
    ut = []
    for m in LITERAL.finditer(kalla):
        lit = re.sub(r"\$\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", " ", m.group(0))
        ut.append(re.sub(r"<[^>]*>", " ", lit))
    return ut


def test_inga_platshallare_i_klientens_kallkod() -> None:
    filer = [
        f for f in sorted((KLIENT / "src").glob("*.ts")) if not f.name.endswith(".test.ts")
    ]
    assert filer, "hittar inga klientfiler"
    fel = []
    for fil in filer:
        for lit in _text_i_litteraler(fil.read_text(encoding="utf-8")):
            m = PLATSHALLARE.search(lit)
            if m:
                fel.append(f"{fil.name}: {m.group(0)}")
    assert not fel, fel


def test_inga_platshallare_i_index_html() -> None:
    html = (KLIENT / "index.html").read_text(encoding="utf-8")
    text = re.sub(r"<!--.*?-->", " ", html, flags=re.S)
    text = re.sub(r"<script.*?</script>", " ", text, flags=re.S)
    text = re.sub(r"<[^>]*>", " ", text)
    assert not PLATSHALLARE.search(text)


def test_inga_platshallare_i_det_byggda_bundlet() -> None:
    dist = KLIENT / "dist"
    js = sorted((dist / "assets").glob("*.js")) if dist.is_dir() else []
    if not js:
        import pytest

        pytest.skip("klienten är inte byggd (kör npm run build)")
    fel = []
    for fil in js:
        for lit in _text_i_litteraler(fil.read_text(encoding="utf-8")):
            m = PLATSHALLARE.search(lit)
            if m:
                fel.append(f"{fil.name}: {m.group(0)}")
    assert not fel, fel[:5]


def test_scannern_hittar_en_platshallare() -> None:
    assert PLATSHALLARE.search("".join(_text_i_litteraler('const x = "God morgon, [förnamn].";')))
    assert PLATSHALLARE.search("".join(_text_i_litteraler("const x = `<td>[– m]</td>`;")))
    kod = 'const x = "[Organisation] / [Förvaltning]";'
    assert PLATSHALLARE.search("".join(_text_i_litteraler(kod)))
    assert not PLATSHALLARE.search("".join(_text_i_litteraler("const [e, n] = f(); a[0]")))
