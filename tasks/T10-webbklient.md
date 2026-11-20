# T10 – Webbklient

**Mål:** en enkel webbvy där man ser markmodellen, sänkor, flödesvägar och resultat, lägger in lappar och jämför före/efter.

**Beroenden:** T02 (första vy), T04 (lappar).

## Steg
1. Kartvy (MapLibre) med lager: markmodell (skuggning), byggnader, brytlinjer, sänkor, flödesvägar, maxdjup, tid till djup.
2. Lager kan tändas, släckas och tonas.
3. Före/efter: reglage eller sida vid sida.
4. Lappar: ladda upp LandXML, rita en gata (mittlinje + val av sektion), rita en byggnad med entréhöjd.
5. Kör-knapp som anropar backend och visar resultat när det är klart.
6. Visa alltid osäkerhet (percentil 10/50/90) och källor.

## Acceptans
- Joel kan lägga in en lapp och se före/efter utan kommandoraden.

## Tips
- O-Tids kartvisning och renderingsmönster kan återanvändas.
- Ingen inloggning i första versionen; körs lokalt.
