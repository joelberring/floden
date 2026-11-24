/*
 * Osäkerhet i resultatpanelen (steg 4): intervallen för vatten (K40) och sol (SVF, K41) och de
 * känsliga överfallen (bifurkationer, "var bör vi mäta"). Ren kod: tar emot servrets svar och
 * gör nyckeltal med intervallet som [min; max] och en liten förklarande rad under. Inget tal
 * räknas fram här; allt kommer ur svaret och formateras med `tal.ts`.
 *
 * Intervallen gäller projektets markmodell (läget före), inte det du ritat: API:t tar bara
 * projektet som indata. Det står i panelen.
 */
import type { BifurkationAnalysSvar, SvfIntervallSvar, VattenIntervallSvar } from "../karta/kartapi.ts";
import { MAT_I_FALT, overfallBeskrivning, ritadeOverfall } from "../karta/overfall.ts";
import { formatera } from "../tal.ts";
import type { Tal } from "./nyckeltal.ts";

export interface OsakerhetSvar {
  vatten: VattenIntervallSvar | null;
  svf: SvfIntervallSvar | null;
  bifurkation: BifurkationAnalysSvar | null;
  /** Felmeddelanden per del, så att en del som saknas inte döljer de andra. */
  fel: string[];
}

export const OSAKERHET_NOT = "Osäkerheten gäller projektets markmodell (läget före), inte det du ritat.";

const ok = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

/** "[min; max] enhet". Semikolon skiljer talen eftersom decimalkomma används. */
export function intervallText(lo: number, hi: number, decimaler: number, enhet = ""): string {
  const e = enhet ? ` ${enhet}` : "";
  return `[${formatera(lo, decimaler)}; ${formatera(hi, decimaler)}]${e}`;
}

function vattenTal(v: VattenIntervallSvar | null, b: BifurkationAnalysSvar | null): Tal[] {
  const ut: Tal[] = [];
  if (v) {
    const s = v.sammanfattning;
    const kf = v.klimatfaktor_intervall;
    ut.push({
      etikett: "Största djup före, spann",
      varde: ok(s.storsta_min_djup_m) && ok(s.storsta_max_djup_m) ? intervallText(s.storsta_min_djup_m, s.storsta_max_djup_m, 2, "m") : "–",
      under: `${kf ? `Klimatfaktor ${formatera(kf[0], 2)} till ${formatera(kf[1], 2)}, ` : ""}${formatera(v.antal_korningar, 0)} körningar`,
    });
    ut.push({
      etikett: "Sänkor med garanterat spann",
      varde: `${formatera(s.antal_monoton_garanterad, 0)} av ${formatera(s.antal_noder, 0)}`,
      under: `${formatera(s.antal_bifurkationspaverkade, 0)} påverkas av känsliga överfall`,
    });
  }
  if (b) {
    const f = b.felindikator;
    ut.push({
      etikett: "Känsliga överfall",
      varde: `${formatera(b.antal_instabila, 0)} st`,
      under: `${MAT_I_FALT}; 2D-kontroll behövs på ${formatera(f.andel_2d_yta * 100, 1)} % av ytan (${formatera(f.antal_noder_2d, 0)} av ${formatera(f.antal_noder_totalt, 0)} sänkor)`,
    });
  }
  return ut;
}

function solTal(s: SvfIntervallSvar | null): Tal[] {
  if (!s) return [];
  return [
    {
      etikett: "Himmelsandel (SVF), medel före",
      varde: ok(s.svf_min_medel) && ok(s.svf_max_medel) ? intervallText(s.svf_min_medel, s.svf_max_medel, 3) : "–",
      under: `±${formatera(s.osakerhet_byggnad_m, 1)} m byggnadshöjd, största bredd ${formatera(s.bredd_max, 3)}, ${formatera(s.antal_osakra_celler, 0)} osäkra celler`,
    },
  ];
}

/** Nyckeltalen för osäkerheten i ett ämne; tom lista för ekologi (den har sina intervall i ordinarie tal). */
export function osakerhetTal(amne: "vatten" | "dagsljus" | "ekologi", o: OsakerhetSvar | null): Tal[] {
  if (!o) return [];
  if (amne === "vatten") return vattenTal(o.vatten, o.bifurkation);
  if (amne === "dagsljus") return solTal(o.svf);
  return [];
}

/** De instabila överfallen som rader ("mät in i fält"), högst `max` stycken. */
export function overfallRader(b: BifurkationAnalysSvar | null, max = 5): string[] {
  if (!b) return [];
  return ritadeOverfall(b.punkter)
    .slice(0, max)
    .map((p) => `${overfallBeskrivning(p)}: ${MAT_I_FALT}`);
}
