# Kontroll av källmodellens tabeller

Genererad av `buller.emission.kontroll.kontrollutskrift_md`. Ändra inte för hand:
rätta datafilen och generera om.

- Datafil: `src/buller/emission/data/nord2000_road_v3.toml` (dataversion 3)
- Syntetisk: nej
- Verifierad av människa: nej
- Fullständig: ja

## Källor

- SP: H. G. Jonasson, Acoustic Source Modelling of Nordic Road Vehicles, SP Rapport 2006:12 (rev. 2006-10-17) https://kunskapscentrumbuller.se/documents/Source%20modelling%20report%20final%20rev%20061017.pdf
- UG: User's Guide Nord2000 Road, DELTA AV 1171/06 (Kragh, Jonasson, Plovsing, Sarinen 2006) https://egra.cedex.es/EGRA-ingles/I-Documentacion/National_Methods/Users_Guide_Nord2000_Road.pdf

## Delkällor

Källa: SP Rapport 2006:12 avsnitt 2.2 (s. 9); UG avsnitt 3.1.1 (s. 12)

| Delkälla | Höjd över vägbanan (m) | Kontrollerad |
| --- | --- | --- |
| 1 | 0.01 | ☐ |
| 2 | 0.3 | ☐ |
| 3 | 0.75 | ☐ |

## Kategori 1: lätta fordon (personbilar, skåpbilar m.m.)

- rull: formtyp `a_plus_b_lg_v_genom_vref`, referenshastighet 70 km/h, källa: SP Rapport 2006:12, tabell A.1 (s. 46), ekvation (A.1)/(2.1); fördelning avsnitt 2.2 (s. 9)
- drivlina: formtyp `a_plus_b_ganger_v_minus_vref_genom_vref`, referenshastighet 70 km/h, källa: SP Rapport 2006:12, tabell A.1 (s. 46), ekvation (A.3)/(2.7); fördelning avsnitt 2.2 (s. 9)

| Frekvens (Hz) | a rull (dB) | b rull (dB) | andel rull 0.01 m | andel rull 0.3 m | andel rull 0.75 m | a drivlina (dB) | b drivlina (dB) | andel drivlina 0.01 m | andel drivlina 0.3 m | andel drivlina 0.75 m | ☐ |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 25 | 69.9 | 33 | 0.8 | 0.2 | 0 | 89.8 | 2 | 0.2 | 0.8 | 0 | ☐ |
| 31.5 | 69.9 | 33 | 0.8 | 0.2 | 0 | 91.6 | 2 | 0.2 | 0.8 | 0 | ☐ |
| 40 | 69.9 | 33 | 0.8 | 0.2 | 0 | 91.5 | 0 | 0.2 | 0.8 | 0 | ☐ |
| 50 | 74.9 | 30 | 0.8 | 0.2 | 0 | 92.5 | 0 | 0.2 | 0.8 | 0 | ☐ |
| 63 | 74.9 | 30 | 0.8 | 0.2 | 0 | 96.6 | 2 | 0.2 | 0.8 | 0 | ☐ |
| 80 | 74.9 | 30 | 0.8 | 0.2 | 0 | 94.2 | 2 | 0.2 | 0.8 | 0 | ☐ |
| 100 | 79.3 | 41 | 0.8 | 0.2 | 0 | 92 | 4 | 0.2 | 0.8 | 0 | ☐ |
| 125 | 82.5 | 41.2 | 0.8 | 0.2 | 0 | 87.4 | 2 | 0.2 | 0.8 | 0 | ☐ |
| 160 | 81.3 | 42.3 | 0.8 | 0.2 | 0 | 86.1 | 2 | 0.2 | 0.8 | 0 | ☐ |
| 200 | 80.9 | 41.8 | 0.8 | 0.2 | 0 | 86.1 | 6 | 0.2 | 0.8 | 0 | ☐ |
| 250 | 78.9 | 38.6 | 0.8 | 0.2 | 0 | 87.2 | 8.2 | 0.2 | 0.8 | 0 | ☐ |
| 315 | 78.8 | 35.5 | 0.8 | 0.2 | 0 | 86.5 | 8.2 | 0.2 | 0.8 | 0 | ☐ |
| 400 | 80.5 | 31.7 | 0.8 | 0.2 | 0 | 85.6 | 8.2 | 0.2 | 0.8 | 0 | ☐ |
| 500 | 87 | 25.9 | 0.8 | 0.2 | 0 | 80.6 | 8.2 | 0.2 | 0.8 | 0 | ☐ |
| 630 | 88.7 | 26.5 | 0.8 | 0.2 | 0 | 80.7 | 8.2 | 0.2 | 0.8 | 0 | ☐ |
| 800 | 90.8 | 32.5 | 0.8 | 0.2 | 0 | 78.8 | 8.2 | 0.2 | 0.8 | 0 | ☐ |
| 1000 | 93.3 | 37.7 | 0.8 | 0.2 | 0 | 79.3 | 8.2 | 0.2 | 0.8 | 0 | ☐ |
| 1250 | 92.5 | 41.4 | 0.8 | 0.2 | 0 | 82.4 | 8.2 | 0.2 | 0.8 | 0 | ☐ |
| 1600 | 92.8 | 41.6 | 0.8 | 0.2 | 0 | 83.7 | 8.2 | 0.2 | 0.8 | 0 | ☐ |
| 2000 | 90.4 | 42.3 | 0.8 | 0.2 | 0 | 83.4 | 9.5 | 0.2 | 0.8 | 0 | ☐ |
| 2500 | 88.4 | 38.9 | 0.8 | 0.2 | 0 | 81.3 | 9.5 | 0.2 | 0.8 | 0 | ☐ |
| 3150 | 85.6 | 39.5 | 0.8 | 0.2 | 0 | 81.8 | 9.5 | 0.2 | 0.8 | 0 | ☐ |
| 4000 | 82.7 | 39.6 | 0.8 | 0.2 | 0 | 79.9 | 9.5 | 0.2 | 0.8 | 0 | ☐ |
| 5000 | 79.7 | 39.8 | 0.8 | 0.2 | 0 | 77.9 | 9.5 | 0.2 | 0.8 | 0 | ☐ |
| 6300 | 75.6 | 40.2 | 0.8 | 0.2 | 0 | 75.1 | 9.5 | 0.2 | 0.8 | 0 | ☐ |
| 8000 | 72 | 40.8 | 0.8 | 0.2 | 0 | 73.1 | 9.5 | 0.2 | 0.8 | 0 | ☐ |
| 10000 | 67.5 | 41 | 0.8 | 0.2 | 0 | 69.5 | 9.5 | 0.2 | 0.8 | 0 | ☐ |

## Kategori 2: medeltunga fordon, två axlar

- rull: formtyp `a_plus_b_lg_v_genom_vref`, referenshastighet 70 km/h, källa: SP Rapport 2006:12, tabell A.1 (s. 46), ekvation (A.1)/(2.1); fördelning avsnitt 2.2 (s. 9)
- drivlina: formtyp `a_plus_b_ganger_v_minus_vref_genom_vref`, referenshastighet 70 km/h, källa: SP Rapport 2006:12, tabell A.1 (s. 46), ekvation (A.3)/(2.7); fördelning avsnitt 2.2 (s. 9)

| Frekvens (Hz) | a rull (dB) | b rull (dB) | andel rull 0.01 m | andel rull 0.3 m | andel rull 0.75 m | a drivlina (dB) | b drivlina (dB) | andel drivlina 0.01 m | andel drivlina 0.3 m | andel drivlina 0.75 m | ☐ |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 25 | 76.5 | 33 | 0.8 | 0 | 0.2 | 97 | 0 | 0.2 | 0 | 0.8 | ☐ |
| 31.5 | 76.5 | 33 | 0.8 | 0 | 0.2 | 97.7 | 0 | 0.2 | 0 | 0.8 | ☐ |
| 40 | 76.5 | 33 | 0.8 | 0 | 0.2 | 98.5 | 0 | 0.2 | 0 | 0.8 | ☐ |
| 50 | 78.5 | 30 | 0.8 | 0 | 0.2 | 98.5 | 0 | 0.2 | 0 | 0.8 | ☐ |
| 63 | 79.5 | 30 | 0.8 | 0 | 0.2 | 101.5 | 0 | 0.2 | 0 | 0.8 | ☐ |
| 80 | 79.5 | 30 | 0.8 | 0 | 0.2 | 101.4 | 0 | 0.2 | 0 | 0.8 | ☐ |
| 100 | 82.5 | 41 | 0.8 | 0 | 0.2 | 97 | 0 | 0.2 | 0 | 0.8 | ☐ |
| 125 | 84.3 | 41.2 | 0.8 | 0 | 0.2 | 96.5 | 0 | 0.2 | 0 | 0.8 | ☐ |
| 160 | 84.3 | 42.3 | 0.8 | 0 | 0.2 | 95.2 | 0 | 0.2 | 0 | 0.8 | ☐ |
| 200 | 84.3 | 41.8 | 0.8 | 0 | 0.2 | 99.6 | 0 | 0.2 | 0 | 0.8 | ☐ |
| 250 | 87.4 | 38.6 | 0.8 | 0 | 0.2 | 100.7 | 8.5 | 0.2 | 0 | 0.8 | ☐ |
| 315 | 88.2 | 35.5 | 0.8 | 0 | 0.2 | 101 | 8.5 | 0.2 | 0 | 0.8 | ☐ |
| 400 | 92 | 31.7 | 0.8 | 0 | 0.2 | 98.3 | 8.5 | 0.2 | 0 | 0.8 | ☐ |
| 500 | 94.1 | 25.9 | 0.8 | 0 | 0.2 | 94.2 | 8.5 | 0.2 | 0 | 0.8 | ☐ |
| 630 | 96.5 | 26.5 | 0.8 | 0 | 0.2 | 92.4 | 8.5 | 0.2 | 0 | 0.8 | ☐ |
| 800 | 96.8 | 32.5 | 0.8 | 0 | 0.2 | 93.4 | 12.5 | 0.2 | 0 | 0.8 | ☐ |
| 1000 | 95.6 | 37.7 | 0.8 | 0 | 0.2 | 95.5 | 12.5 | 0.2 | 0 | 0.8 | ☐ |
| 1250 | 93 | 41.4 | 0.8 | 0 | 0.2 | 96 | 12.5 | 0.2 | 0 | 0.8 | ☐ |
| 1600 | 93.9 | 41.6 | 0.8 | 0 | 0.2 | 93.8 | 12.5 | 0.2 | 0 | 0.8 | ☐ |
| 2000 | 91.5 | 42.3 | 0.8 | 0 | 0.2 | 93.4 | 12.5 | 0.2 | 0 | 0.8 | ☐ |
| 2500 | 88.1 | 38.9 | 0.8 | 0 | 0.2 | 92.1 | 12.5 | 0.2 | 0 | 0.8 | ☐ |
| 3150 | 86.1 | 39.5 | 0.8 | 0 | 0.2 | 90.1 | 12.5 | 0.2 | 0 | 0.8 | ☐ |
| 4000 | 84.2 | 39.6 | 0.8 | 0 | 0.2 | 87.9 | 12.5 | 0.2 | 0 | 0.8 | ☐ |
| 5000 | 80.3 | 39.8 | 0.8 | 0 | 0.2 | 85.6 | 12.5 | 0.2 | 0 | 0.8 | ☐ |
| 6300 | 77.3 | 40.2 | 0.8 | 0 | 0.2 | 85.7 | 8.5 | 0.2 | 0 | 0.8 | ☐ |
| 8000 | 77.3 | 40.8 | 0.8 | 0 | 0.2 | 82.6 | 8.5 | 0.2 | 0 | 0.8 | ☐ |
| 10000 | 77.3 | 41 | 0.8 | 0 | 0.2 | 79.5 | 8.5 | 0.2 | 0 | 0.8 | ☐ |

## Kategori 3: tunga fordon, tre eller fler axlar (tabellen avser fyra axlar)

- rull: formtyp `a_plus_b_lg_v_genom_vref`, referenshastighet 70 km/h, källa: SP Rapport 2006:12, tabell A.1 (s. 46), ekvation (A.1)/(2.1); fördelning avsnitt 2.2 (s. 9)
- drivlina: formtyp `a_plus_b_ganger_v_minus_vref_genom_vref`, referenshastighet 70 km/h, källa: SP Rapport 2006:12, tabell A.1 (s. 46), ekvation (A.3)/(2.7); fördelning avsnitt 2.2 (s. 9)

| Frekvens (Hz) | a rull (dB) | b rull (dB) | andel rull 0.01 m | andel rull 0.3 m | andel rull 0.75 m | a drivlina (dB) | b drivlina (dB) | andel drivlina 0.01 m | andel drivlina 0.3 m | andel drivlina 0.75 m | ☐ |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 25 | 79.5 | 33 | 0.8 | 0 | 0.2 | 97.7 | 0 | 0.2 | 0 | 0.8 | ☐ |
| 31.5 | 79.5 | 33 | 0.8 | 0 | 0.2 | 97.3 | 0 | 0.2 | 0 | 0.8 | ☐ |
| 40 | 79.5 | 33 | 0.8 | 0 | 0.2 | 98.2 | 0 | 0.2 | 0 | 0.8 | ☐ |
| 50 | 81.5 | 30 | 0.8 | 0 | 0.2 | 103.3 | 0 | 0.2 | 0 | 0.8 | ☐ |
| 63 | 82.5 | 30 | 0.8 | 0 | 0.2 | 107.9 | 0 | 0.2 | 0 | 0.8 | ☐ |
| 80 | 82.5 | 30 | 0.8 | 0 | 0.2 | 105.4 | 0 | 0.2 | 0 | 0.8 | ☐ |
| 100 | 85.5 | 41 | 0.8 | 0 | 0.2 | 101 | 0 | 0.2 | 0 | 0.8 | ☐ |
| 125 | 87.3 | 41.2 | 0.8 | 0 | 0.2 | 101 | 0 | 0.2 | 0 | 0.8 | ☐ |
| 160 | 87.3 | 42.3 | 0.8 | 0 | 0.2 | 101.3 | 0 | 0.2 | 0 | 0.8 | ☐ |
| 200 | 87.3 | 41.8 | 0.8 | 0 | 0.2 | 101.3 | 0 | 0.2 | 0 | 0.8 | ☐ |
| 250 | 90.4 | 38.6 | 0.8 | 0 | 0.2 | 102.5 | 8.5 | 0.2 | 0 | 0.8 | ☐ |
| 315 | 91.2 | 35.5 | 0.8 | 0 | 0.2 | 103 | 8.5 | 0.2 | 0 | 0.8 | ☐ |
| 400 | 95 | 31.7 | 0.8 | 0 | 0.2 | 102 | 8.5 | 0.2 | 0 | 0.8 | ☐ |
| 500 | 97.1 | 25.9 | 0.8 | 0 | 0.2 | 101.4 | 8.5 | 0.2 | 0 | 0.8 | ☐ |
| 630 | 99.5 | 26.5 | 0.8 | 0 | 0.2 | 99.4 | 8.5 | 0.2 | 0 | 0.8 | ☐ |
| 800 | 99.8 | 32.5 | 0.8 | 0 | 0.2 | 95.1 | 8.5 | 0.2 | 0 | 0.8 | ☐ |
| 1000 | 98.6 | 37.7 | 0.8 | 0 | 0.2 | 95.8 | 8.5 | 0.2 | 0 | 0.8 | ☐ |
| 1250 | 96 | 41.4 | 0.8 | 0 | 0.2 | 95.3 | 8.5 | 0.2 | 0 | 0.8 | ☐ |
| 1600 | 96.9 | 41.6 | 0.8 | 0 | 0.2 | 92.2 | 8.5 | 0.2 | 0 | 0.8 | ☐ |
| 2000 | 94.5 | 42.3 | 0.8 | 0 | 0.2 | 93.2 | 8.5 | 0.2 | 0 | 0.8 | ☐ |
| 2500 | 91.1 | 38.9 | 0.8 | 0 | 0.2 | 90.7 | 8.5 | 0.2 | 0 | 0.8 | ☐ |
| 3150 | 89.1 | 39.5 | 0.8 | 0 | 0.2 | 88.8 | 8.5 | 0.2 | 0 | 0.8 | ☐ |
| 4000 | 87.2 | 39.6 | 0.8 | 0 | 0.2 | 87.5 | 8.5 | 0.2 | 0 | 0.8 | ☐ |
| 5000 | 83.3 | 39.8 | 0.8 | 0 | 0.2 | 85.9 | 8.5 | 0.2 | 0 | 0.8 | ☐ |
| 6300 | 80.3 | 40.2 | 0.8 | 0 | 0.2 | 86.9 | 8.5 | 0.2 | 0 | 0.8 | ☐ |
| 8000 | 80.3 | 40.8 | 0.8 | 0 | 0.2 | 83.8 | 8.5 | 0.2 | 0 | 0.8 | ☐ |
| 10000 | 80.3 | 41 | 0.8 | 0 | 0.2 | 80.3 | 8.5 | 0.2 | 0 | 0.8 | ☐ |

## Korrektioner

- ☐ Antal axlar: kategori = 3; tabellen_avser_antal_axlar = 4. Källa: SP Rapport 2006:12, bilaga A (s. 46), ekvation (A.2)
- ☐ Beläggning: kategorier = [1]; RS_db = {'DAC': -0.3, 'SMA': 0.3}; per_mm_db = 0.25; referens_stenstorlek_mm = 11.0; giltig_stenstorlek_mm = [8.0, 16.0]. Källa: SP Rapport 2006:12, ekvation (2.3) (s. 10) och sammanfattningen (s. 6)
- ☐ Beläggningens ålder: kategorier = [1]; k2 = 0.2; k1 = -1.2; k0 = 1.6; galler_till_ar = 2.0. Källa: SP Rapport 2006:12, ekvation (2.4) (s. 11) och sammanfattningen (s. 6)
- ☐ Lufttemperatur: referens_c = 20.0; K_db_per_grad = {'DAC': 0.1}; faktor_kategori_2_och_hogre = 0.5. Källa: SP Rapport 2006:12, ekvation (2.5) (s. 11) och tabell 2.1 (s. 12)
- ☐ Acceleration: C_db_per_m_s2 = {'1': 4.4, '2': 5.6, '3': 5.6}; storsta_belopp_m_s2 = 2.0. Källa: SP Rapport 2006:12, ekvation (2.8) och tabell 2.2 (s. 13)
- Regional korrektion av aR för SE, NO, FI. Källa: SP Rapport 2006:12, tabell A.2 (s. 47) och avsnitt 6.2 (s. 31)
- Dubbdäck (används: nej). Källa: SP Rapport 2006:12, tabell A.3 (s. 47), ekvation (8.1) (s. 38)

| Frekvens (Hz) | tabell A.2, tillägg aR (dB) | tabell A.3, a (dB) | tabell A.3, b (dB) | ☐ |
| --- | --- | --- | --- | --- |
| 25 | 0 | 0 | 0 | ☐ |
| 31.5 | 0 | 0 | 0 | ☐ |
| 40 | 0 | 0 | 0 | ☐ |
| 50 | 0 | 0 | 0 | ☐ |
| 63 | 0 | 0 | 0 | ☐ |
| 80 | 0 | 0 | 0 | ☐ |
| 100 | 0 | 0 | 0 | ☐ |
| 125 | 0 | 0.3 | -4.1 | ☐ |
| 160 | 0 | 1.4 | -6 | ☐ |
| 200 | 0 | 1.5 | -8.5 | ☐ |
| 250 | 1 | 0.9 | -4.1 | ☐ |
| 315 | 1 | 1.2 | 1.7 | ☐ |
| 400 | 1 | 1.5 | 0.6 | ☐ |
| 500 | 1 | 1.9 | -4.6 | ☐ |
| 630 | 2 | 1.8 | -3.9 | ☐ |
| 800 | 2 | 0.8 | -2.7 | ☐ |
| 1000 | 2 | 0.5 | -4.2 | ☐ |
| 1250 | 2 | 0.2 | -11.7 | ☐ |
| 1600 | -1 | -0.2 | -11.7 | ☐ |
| 2000 | -2 | -0.4 | -14.9 | ☐ |
| 2500 | -3 | 0.5 | -17.6 | ☐ |
| 3150 | -4 | 0.8 | -21.8 | ☐ |
| 4000 | -4 | 0.9 | -21.6 | ☐ |
| 5000 | -3 | 2.1 | -19.2 | ☐ |
| 6300 | -1 | 5 | -14.6 | ☐ |
| 8000 | 0 | 7.3 | -9.9 | ☐ |
| 10000 | 2 | 10 | -10.2 | ☐ |

## Direktivitet

Källa: SP Rapport 2006:12 avsnitt 2.5 (s. 13–16), ekvation (2.9), figur 2.2; sammanfattningen (s. 6–7)

∆L = ∆L_H(f, φ) + ∆L_V(f, ψ). φ är vinkeln mellan fordonets färdriktning och riktningen
mot mottagaren i horisontalplanet, ψ riktningens vinkel över horisontalplanet.

### Horisontell direktivitet

Formtyp `k0_plus_k1_abs_cos_fi`: (k0 + k1·|cos φ|)·√cos ψ, koefficienter [k0, k1].
Formtyp `polynom_i_pi_halva_minus_fi`: (Σ cₙ·(π/2 − φ)ⁿ)·√cos ψ med φ i radianer,
koefficienter [c0, c1, c2, …]. Delkällor utan rad har ingen horisontell direktivitet.

| Delkälla (m) | Formtyp | Koefficienter (dB) | Band (Hz) | Bullertyp i texten | Källa | ☐ |
| --- | --- | --- | --- | --- | --- | --- |
| 0.01 | `k0_plus_k1_abs_cos_fi` | -2.5, 4 | 800–6300, noll i övriga | rull | SP Rapport 2006:12, ekvation (2.12a–b) (s. 15); sammanfattningen (s. 7) | ☐ |
| 0.75 | `polynom_i_pi_halva_minus_fi` | 0.6, 0.22, -1.425, 1.546 | alla | drivlina | SP Rapport 2006:12, ekvation (2.11) (s. 14); sammanfattningen (s. 7) | ☐ |

### Vertikal direktivitet

Källa: SP Rapport 2006:12, tabell 2.4 (s. 16). Band som tabellen inte tar upp har ingen
vertikal direktivitet.

| Frekvens (Hz) | hs = 0.01 m | hs = 0.3 m | hs = 0.75 m | ☐ |
| --- | --- | --- | --- | --- |
| 50, 63, 80 | 0 | -2·sin ψ | 0 | ☐ |
| 100, 125, 160 | 0 | -4·sin ψ | 0 | ☐ |
| 200, 250, 315 | -2·(1 − cos² ψ) | -5·(1 − cos² ψ) | -2·(1 − cos² ψ) | ☐ |
| 400, 500, 630 | -3·(1 − cos² ψ) | -5·(1 − cos² ψ) | -3·(1 − cos² ψ) | ☐ |
| 800, 1000, 1250 | -4·(1 − cos² ψ) | -6·(1 − cos² ψ) | -3·(1 − cos² ψ) | ☐ |
| 1600, 2000, 2500 | -4·(1 − cos² ψ) | -6·(1 − cos² ψ) | -2·(1 − cos² ψ) | ☐ |
| 3150, 4000, 5000 | 0 | -5·(1 − cos² ψ) | -2·(1 − cos ψ) | ☐ |
| 6300, 8000, 10000 | 0 | -8·(1 − cos ψ) | -2·(1 − cos ψ) | ☐ |

## Inte infört i koden

- Våt vägbana: ekvation (2.6a–c) (s. 13).
- Dubbdäck: tabell A.3 är inläst (ovan) men används inte; hur andelen dubbade däck vägs
  in står inte i avsnitt 8.1.
- Övriga rader i tabell 2.1 (temperaturkoefficient för andra beläggningar än DAC), porösa
  beläggningar och användardefinierade beläggningskorrektioner (avsnitt 8.3, s. 40).
- Extra delkälla på 3,5 m för tunga fordon med högt avgasrör (avsnitt 2.2).
- Motorbroms för kategori 3 och lutning som ekvivalent acceleration (text efter ekv. 2.8).
- Maximalnivå: avsnitt 2.6, ekvation (2.13)–(2.16) (s. 16–20).
- Standardfördelning mellan kategori 2 och 3 (tabell 2.10, s. 21) och vägytans impedans
  (tabell 10.1, s. 43) hör till inställningar respektive utbredning.
