# Hopkoppling av hela kedjan (2026-11-21)

Scen → banor → baslinjekärna → operatorlager → skisskörning, med syntetisk emission. Skript:
`bench/buller/helkedja.py`. Test: `tests/buller/test_helkedja.py`. Baslinjekärnan är inte Nord2000,
så nivåerna är inga bullernivåer.

## Fel som hopkopplingen hittade (rättade)

1. **Mottagare inom ett källkluster.** Minsta avståndet till trädnoden blir 0, och kärnans övre gräns är bara
   definierad för positiva avstånd. `operator/klustring.py` ger nu oändlig gräns för sådana noder, så att de delas.
2. **Övre gränsen gällde en bana, inte ett par.** Ett par är energisumman av direkt bana och reflexer, och kunde
   därför överskrida kärnans gräns. Operatorlagrets kontroll stoppade körningen, som den ska.
   `Banuppsattning.ovre_grans_par` lägger till `10·lg(största antal banor per par)`.

## Mätt (en tråd, fasadpunkter på första våningen, källsegment 10 m, en delkällhöjd)

| Scen | Mottagare × källor | Banor | Banfinnare | Alla par genom kärnan | Skisskörning (ε = 0,5 dB) | Största fel | Garanterad gräns |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 300 × 300 m, 42 byggnader | 20 × 300 | 21 902 | 5,3 s | 2,2 s (360 µs/par) | 2,6 s | 0,005 dB | 0,47 dB |
| 600 × 600 m | 8 × 1 320 | 66 676 | 45,5 s | 6,8 s (642 µs/par) | 6,7 s | 0,00004 dB | 0,50 dB |

En körning på 1000 × 1000 m gav inget resultat inom 800 s (orsaken är inte utredd; minne eller tid i banfinnaren).

## Slutsatser

- **Felgränsen håller**, men den är mycket försiktig: det verkliga felet är 100–10 000 gånger mindre än gränsen.
- **Klustringen sparar nästan ingenting i tät stad med den här gränsen.** 97–100 % av paren beräknades. Gränsen
  (fri spridning + 6 dB + reflexer) tar ingen hänsyn till skärmning, som dominerar i stadsmiljö.
- **Banfinnaren är flaskhalsen**, inte kärnan: den bygger alla banor och profiler i förväg (45 s mot 7 s för kärnan
  vid 600 m) och håller en profil per bana i minnet.

## Följder för nästa steg

1. Banor och profiler ska tas fram **vid behov och i batch**, styrt av klustringens trädgenomgång, i stället för i
   förväg för alla par.
2. Övre gränsen behöver bli **skarpare** för att klustringen ska löna sig. Kandidat: en gräns som utnyttjar
   skärmning som går att garantera (t.ex. ur max-mipmap över höjdfältet). Det måste bevisas, inte antas.
3. För skissläget kan en **skattad** felgräns (stickprov i stället för garanti) vara rätt avvägning. Rapportläget
   behåller den garanterade.
