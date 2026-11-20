# T04 – Projektlapp och före/efter

**Mål:** ett nytt projekt läggs in som lapp (K3), bara berörda delar räknas om, och före/efter visas på under en minut.

**Läs först:** `docs/DATAKONTRAKT.md` (K1, K2, K3, K6).
**Beroenden:** T02, T03.

## Steg
1. Läs lappar enligt K3: höjdytor, byggnader med entréhöjd, gator med sektion, magasin och markslag.
2. Import från LandXML (ytmodeller från projektering). IFC senare.
3. Parametriska gator: mittlinje + sektion ur `config.toml` → profil (samma kod som T01b).
4. Tillämpa lappen på berörda rutor och skapa nya recept (ursprungsrutorna ändras inte).
5. Bestäm berörda noder i sänkgrafen och bygg om bara det delträdet och det som ligger nedströms. Spara frågor om datastrukturer i `docs/MATEMATIK.md` (Q4).
6. Kör samma scenario före och efter. Skriv differens: djup, tid, nya drabbade entréer, och volym som måste fördröjas för att ingen granne ska få det sämre.
7. Flera lappar sida vid sida (alternativ A och B).

## Acceptans
- En påhittad lapp i provområdet (ett kvarter och en ny gata) ger före/efter på under en minut.
- Lokal omräkning ger samma resultat som full omräkning (testa).

## Stoppa och fråga
- Vilket verkligt eller påhittat projekt som ska användas som exempel (Joel väljer).
