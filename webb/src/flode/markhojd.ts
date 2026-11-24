/*
 * Markhöjden under pekaren i steg 2 och 4: `GET /api/kartverktyg/<projekt>/hojd?e=&n=` läser cellen ur projektets
 * `terrang.tif` (marken utan byggnader, m RH 2000). Samma mönster som värdena för ett resultat (`planeraAvlasning` i
 * `resultat.ts`): kort paus efter senaste rörelsen, svar som kommit för sent kastas, inget visas utanför rutorna.
 * Inget räknas om i klienten; talet formateras med `tal.ts`.
 */
import { formatera } from "../tal.ts";

export interface MarkhojdSvar {
  inom: boolean;
  hojd_m: number | null;
  enhet?: string;
  hojdsystem?: string;
}

/** "Markhöjd 12,34 m RH 2000", eller null utanför rutorna. Saknas värdet i cellen står det. */
export function markhojdText(s: MarkhojdSvar | null): string | null {
  if (!s || !s.inom) return null;
  if (s.hojd_m === null || !Number.isFinite(s.hojd_m)) return "Markhöjd saknas här";
  return `Markhöjd ${formatera(s.hojd_m, 2)} m ${s.hojdsystem ?? "RH 2000"}`;
}

/**
 * Pekarlyssnare: `(e, n)` eller `(null, null)` när pekaren lämnar kartan. `visa` får texten eller null.
 * `hamta` är anropet mot servern (injicerbart för test).
 */
export function markhojdAvlasning(
  hamta: (e: number, n: number) => Promise<MarkhojdSvar>,
  visa: (text: string | null) => void,
  pausMs = 90,
): { ror(e: number | null, n: number | null): void; avsluta(): void } {
  let timer: number | null = null;
  let nr = 0;
  let slut = false;
  return {
    ror(e, n) {
      if (timer !== null) clearTimeout(timer);
      timer = null;
      if (e === null || n === null) {
        nr += 1;
        visa(null);
        return;
      }
      timer = setTimeout(() => {
        const mitt = ++nr;
        hamta(e, n).then(
          (s) => {
            if (mitt === nr && !slut) visa(markhojdText(s));
          },
          () => {
            if (mitt === nr && !slut) visa(null);
          },
        ) ;
      }, pausMs) as unknown as number;
    },
    avsluta() {
      slut = true;
      if (timer !== null) clearTimeout(timer);
      timer = null;
    },
  };
}
