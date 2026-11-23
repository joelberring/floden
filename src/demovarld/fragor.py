"""Demofrågorna (K20) som text. Samma mall skriver `exempel/fragor/demo_*.toml` och testlägets
fråge-filer, så att den incheckade frågefilen och det som `floden demo` kör aldrig skiljer sig.

Valen som kommer ur världen redovisas i frågefilernas kommentarer (de visas också i webben när
frågan är vald)."""

from __future__ import annotations

import math
import os
from pathlib import Path

from demovarld.varld import DemoVarld

FRAGA_DAGSLJUS = "demo_dagsljus"
FRAGA_VATTEN = "demo_vatten"
FRAGA_EKOLOGI = "demo_ekologi"
EKOLOGI_NATVERK = "barrskog-nacka"

HOJD_MAX_M = 30.0
H_MIN_GRADER = 10.0
ATERKOMSTTID_AR = 100.0
KLIMATFAKTOR = 1.4
VARAKTIGHET_MIN = 360.0


KLIMATFAKTOR_TEXT = f"{KLIMATFAKTOR:g}".replace(".", ",")
RADIE_M = HOJD_MAX_M / math.tan(math.radians(H_MIN_GRADER))


def _rel(fran: Path, till: Path) -> str:
    return Path(os.path.relpath(till, fran)).as_posix()


def text_dagsljus(v: DemoVarld, fragedir: Path, varld_dir: Path, *, riktningar: int = 180) -> str:
    """Frågefilen för dagsljus före/efter (höjt hus och nytt hus)."""
    bas = _rel(fragedir, varld_dir / "dagsljus" / "bas")
    k9h = _rel(fragedir, varld_dir / "dagsljus" / "k9_hojd.json")
    k9n = _rel(fragedir, varld_dir / "dagsljus" / "k9_nytt_hus.json")
    x0, y0, x1, y1 = v.fokus_3011
    return f"""# Demo: dagsljus före/efter i demovärlden (syntetisk, K1 v2). Bygg världen och kör frågan med
#   floden demo
#
# Scenarier: bas, `hojd` (K9 byggnad_hojd: det låga huset i nordöstra gården höjs från 4 till 15 m)
# och `nytt_hus` (K9 byggnad_ny: ett 18 m-hus, 14 x 40 m, i sydvästra gården).
#
# Varför varningen om påverkansradien inte gäller här (redovisat val):
#   Påverkansradien är hojd_max_m / tan(h_min_grader) = {HOJD_MAX_M:g} / tan({H_MIN_GRADER:g} grader) = {RADIE_M:.0f} m.
#   Världen är {v.sida_m:g} x {v.sida_m:g} m men frågan avgränsas med `fokus` till mittområdet
#   ({v.fokus_m[2] - v.fokus_m[0]:g} x {v.fokus_m[3] - v.fokus_m[1]:g} m, de fyra mittkvarteren). Det är {min(v.fokus_m[0], v.fokus_m[1]):g} m marginal till världens
#   kant åt alla håll, mer än radien, så ingen stråle från ett resultat lämnar höjdfältet.
#   Marginalrutorna ingår som hinder men får inga egna resultat.
#   hojd_max_m = {HOJD_MAX_M:g} m: högsta byggnad är 21 m och marken varierar knappt 4 m, så
#   höjdspannet i data är under {HOJD_MAX_M:g} m (lösaren varnar annars).
#   h_min_grader = {H_MIN_GRADER:g}: hinder som skymmer mindre än {H_MIN_GRADER:g} grader bortom {RADIE_M:.0f} m ingår inte. Det är exakt för
#   solhöjder över {H_MIN_GRADER:g} grader; under dem (första och sista timmen vid vårdagjämningen) kan
#   soltimmarna vara något för höga. Det är lika i före och efter, så skillnaden påverkas inte av
#   avgränsningen. Gårdsstudier är känsliga för skuggor från närmaste lamellerna, inte för
#   byggnader {RADIE_M:.0f} m bort. Vegetation ingår inte (standard, se docs/DAGSLJUS.md avsnitt 12).
kontrakt = "K20/v1"
fraga_id = "demo-dagsljus"
beskrivning = "Soltimmar på marken och dagsljus på fasader före och efter: ett höjt hus och ett nytt hus."

[scenarier.bas]
bas = "{bas}"

[scenarier.hojd]
bas = "{bas}"
k9 = ["{k9h}"]

[scenarier.nytt_hus]
bas = "{bas}"
k9 = ["{k9n}"]

[[analyser]]
id = "sol"
losare = "dagsljus.soltimmar_mark"
fokus = [{x0:g}, {y0:g}, {x1:g}, {y1:g}]
parametrar = {{ datum = ["2026-03-20"], steg_min = 10, azimutriktningar = {riktningar}, cellstorlek_m = {v.upplosning_m:g}, hojd_max_m = {HOJD_MAX_M:g}, h_min_grader = {H_MIN_GRADER:g} }}

[[analyser]]
id = "vsc"
losare = "dagsljus.vsc_fasad"
fokus = [{x0:g}, {y0:g}, {x1:g}, {y1:g}]
parametrar = {{ cellstorlek_m = {v.upplosning_m:g}, hojd_max_m = {HOJD_MAX_M:g}, h_min_grader = {H_MIN_GRADER:g}, fasad_steg_m = 6.0, vaningshojd_m = 3.0 }}

[[jamforelser]]
analys = "sol"
utdata = "soltimmar"
fore = "bas"
efter = "hojd"
troskel = 0.1

[[jamforelser]]
analys = "sol"
utdata = "soltimmar"
fore = "bas"
efter = "nytt_hus"
troskel = 0.1
"""  # noqa: E501


def text_vatten(v: DemoVarld, fragedir: Path, varld_dir: Path) -> str:
    """Frågefilen för blåytor (maxdjup) före/efter ett nytt hus, Stockholmsregn 2024."""
    bas = _rel(fragedir, varld_dir / "vatten" / "bas")
    k9n = _rel(fragedir, varld_dir / "vatten" / "k9_nytt_hus.json")
    return f"""# Demo: blåytor (maxdjup) före/efter ett nytt hus i demovärlden (syntetisk, K1 v2). Bygg världen och
# kör frågan med
#   floden demo
#
# Regnet ur IDF-källan Stockholmsregn 2024 (Tabell 7, utan klimatfaktor i tabellen):
#   återkomsttid {ATERKOMSTTID_AR:g} år, varaktighet {VARAKTIGHET_MIN:g} min (6 timmar), klimatfaktor {KLIMATFAKTOR:g} (rapportens
#   rekommendation, uttrycklig parameter), blockregn. Källa, faktor och tabellens sha256 står i
#   varje jobbs run.json och visas i resultatets sammanfattning. Statisk fyllning
#   (Fill-Spill-Merge): inget flöde i tid, ingen infiltration och inget ledningsnät, alltså
#   maxdjup i sänkor, inte en dynamisk simulering.
#
# Takvatten på (Joels beslut 2026-11-22): regnet på taken rinner ned längs fasaderna. Okänd
# takform räknas som brutet tak: hälften till varje långsida (ringhus: hälften till gården).
# Avtappning 0 mm/h (standard): ledningsnätet antas fullt vid skyfallet.
#
# Scenario `nytt_hus`: ett 18 m-hus (14 x 40 m) byggs ut från södra lamellen i sydvästra gården
# och spärrar gårdens avrinning från skålen (0,5 m djup) mot porten. Vattnet dämms upp och
# djupet ökar uppströms. Husets egna celler visas som tak, inte som mark.
#
# Vattenlösaren tar en K1-ruta, så vattenvärlden är fokusområdet ({v.fokus_m[2] - v.fokus_m[0]:g} x {v.fokus_m[3] - v.fokus_m[1]:g} m) som en ruta.
# Vatten som rinner över rutans kant räknas som utflöde (ut_m3 i mätvärdena).
kontrakt = "K20/v1"
fraga_id = "demo-vatten"
beskrivning = "Största vattendjup vid Stockholmsregn 2024 ({ATERKOMSTTID_AR:g} år, klimatfaktor {KLIMATFAKTOR_TEXT}), före och efter ett nytt hus."

[scenarier.fore]
bas = "{bas}"

[scenarier.efter]
bas = "{bas}"
k9 = ["{k9n}"]

[[analyser]]
id = "skyfall"
losare = "vatten.statisk_fyllning"
parametrar = {{ idf = "stockholmsregn-2024", aterkomsttid_ar = {ATERKOMSTTID_AR:g}, varaktighet_min = {VARAKTIGHET_MIN:g}, klimatfaktor = {KLIMATFAKTOR:g}, regntyp = "block", takvatten = true }}

[[jamforelser]]
analys = "skyfall"
utdata = "max_djup"
fore = "fore"
efter = "efter"
troskel = 0.01
"""  # noqa: E501


def text_ekologi(v: DemoVarld, fragedir: Path, varld_dir: Path) -> str:
    """Frågefilen: ekologisk konnektivitet före/efter ett nytt hus (skiss, nätverket är ett val)."""
    bas = _rel(fragedir, varld_dir / "vatten" / "bas")
    k9n = _rel(fragedir, varld_dir / "vatten" / "k9_nytt_hus.json")
    nmd = _rel(fragedir, varld_dir / "ekologi" / "nmd.tif")
    return f"""# Demo: ekologisk konnektivitet före/efter ett nytt hus i demovärlden (syntetisk, K1 v2). Bygg
# världen och kör frågan med
#   floden demo
#
# SKISS. Nätverket `{EKOLOGI_NATVERK}` (Barrskogssamband, Nacka 2020, NMD) är ett habitatnätverk med
# signalarter: parametrarna (habitat, minsta area, tröskel, motstånd per NMD-klass) har källa och sida i
# data/ekologi/natverk/{EKOLOGI_NATVERK}.toml. Markunderlaget är ett PÅHITTAT NMD-lager (indata `nmd`,
# 10 m, gårdarna delade i tallskog och ädellövskog); ritade hus läggs ovanpå som NMD 51. Resultatet
# visar hur ett nytt hus ändrar konnektiviteten i det påhittade kvarteret; det säger ingenting om
# verkliga arter. Bytbart till `adellov-nacka` (Ädellövsamband) med `natverk` nedan.
#
# Samma bas och samma K9 som vattenfrågan: fokusområdet ({v.fokus_m[2] - v.fokus_m[0]:g} x {v.fokus_m[3] - v.fokus_m[1]:g} m) som en ruta, ett 18 m-hus i sydvästra
# gården. Intervallet [ECA_min, ECA_max] är källbaserat: lägsta och högsta värde mellan nätverkets
# tröskelscenarier (1 000 och 3 000 effektiva m).
kontrakt = "K20/v1"
fraga_id = "demo-ekologi"
beskrivning = "Ekologisk konnektivitet (ECA och flaskhalsar, skiss) före och efter ett nytt hus."

[scenarier.fore]
bas = "{bas}"

[scenarier.efter]
bas = "{bas}"
k9 = ["{k9n}"]

[[analyser]]
id = "konnektivitet"
losare = "ekologi.konnektivitet"
parametrar = {{ natverk = "{EKOLOGI_NATVERK}" }}
indata = {{ nmd = "{nmd}" }}

[[jamforelser]]
analys = "konnektivitet"
utdata = "stromtathet"
fore = "fore"
efter = "efter"
troskel = 0.01
"""  # noqa: E501
