# Avvikelser och stopp

Skriv hit när ett testfall faller efter två försök eller när underlag saknas. Senaste överst.

## 2026-11-21 – B4 – Kolumnen dL i testfallen kan inte användas som facit för punkt-till-punkt

**Vad:** dL i `teststraightroad_20181101.xls` är enligt RL 18/18 avsnitt 2 skillnaden mellan L_E och L_E i fritt
fält för hela förbifarten: en energivägd summa över 179 källpunkter, delkällhöjder och direktivitet. Den beror
alltså på källmodellen (B5) och på linjekällans integration, inte bara på spridningskärnan.

**Prövat (informativt, inget i kärnan har ändrats efter det):** fall 1–8 (plan mark, u = 0, s_u = 0) räknades med
`Nord2000Karna` för 179 källpunkter (−89°…89°, profilen sträckt med 1/cos θ), en källhöjd i taget (0,01 m och
0,30 m), utan direktivitet. Testvärdet ska då ligga mellan de två höjdernas kurvor.

- Med **lika vinkelvikt** (som SP 2006:12 ekv. 11.6 anger) ligger testvärdet utanför intervallet i 15–20 av 27 band,
  som mest 1,5–1,7 dB **över** båda kurvorna (alla åtta fall).
- Med **vikten cos θ** ligger testvärdet utanför i 0–13 band, som mest 0,43 dB. I fall 7 (hård mark) är avvikelsen
  mot 0,01 m-kurvan ≤ 0,02 dB i 25–400 Hz och hela spektrum ligger inom intervallet (≤ 0,04 dB). I fall 4–8 är
  avvikelsen mot 0,01 m-kurvan ≤ 0,15 dB i 25–400 Hz. Över ca 5 kHz ligger testvärdet 2,2 dB under 0,30 m-kurvan i
  alla fall (stämmer med att den källan bär 60 % av energin där).
- Kvar med cos θ: fall 1–3 (σ = 12,5–80) ligger 0,1–0,4 dB över båda kurvorna i 25–250 Hz, och alla mjuka fall ca
  0,4 dB över i bandet där kurvorna korsar varandra (1,25–3,15 kHz, där horisontell direktivitet finns).

**Två försök att hitta felet i kärnan:** (1) den sfäriska reflektionskoefficienten och tvåstrålesumman jämfördes med
B4a:s oberoende implementation (≤ 3·10⁻⁵ i Q, ≤ 0,005 dB i 25–315 Hz); (2) vinkelberoendet granskades: ingen term i
ekv. (115)–(133) beror på vinkeln mot vägen annat än genom avstånden. Inget fel hittat.

**Öppet (för B5/B1, inte B4):** varför testfallen motsvarar vikten cos θ. Det är samma sak som att varje källpunkt
representerar sträckan R·Δθ i stället för R·Δθ/cos θ, eller en direktivitet 10·lg cos θ. Det avgörs när källmodellen
och integrationen finns; tills dess är överensstämmelsen ovan en indikation, inte en verifiering. cos θ-vikten har
**inte** lagts in någonstans i koden.
