/*
 * O(1)-beräkningsomfång och lugn förvarning före körning i Steg 2 (S-O4).
 *
 * Alla tal kommer direkt från serverns `berakningsomfang` (geografimanifest + fokus +
 * `FLODEN_MAX_SANKGRAF_CELLER`); klienten uppskattar eller gissar aldrig egna celltal.
 */
import { esc } from "../sidor.ts";
import { formatera } from "../tal.ts";

export type BerakningsomfangNiva = "ok" | "varning" | "over_tak";

export interface BerakningsomfangInfo {
  antal_rutor: number;
  uppskattade_celler: number;
  max_sankgraf_celler: number;
  andel_av_tak: number;
  niva: BerakningsomfangNiva;
  meddelande?: string | null;
  upplosning_m?: number | null;
}

const VARNING_ANDEL = 0.75;

function giltigtPositivtTal(v: unknown): v is number {
  return typeof v === "number" && Number.isFinite(v) && v > 0;
}

/** Normaliserar nivå utifrån serverns fält (med reserv på `andel_av_tak` / celltal). */
export function berakningsomfangNiva(o: BerakningsomfangInfo): BerakningsomfangNiva {
  if (o.niva === "ok" || o.niva === "varning" || o.niva === "over_tak") return o.niva;
  if (giltigtPositivtTal(o.max_sankgraf_celler) && o.uppskattade_celler > o.max_sankgraf_celler) {
    return "over_tak";
  }
  const andel =
    typeof o.andel_av_tak === "number" && Number.isFinite(o.andel_av_tak)
      ? o.andel_av_tak
      : giltigtPositivtTal(o.max_sankgraf_celler)
        ? o.uppskattade_celler / o.max_sankgraf_celler
        : 0;
  if (andel > 1.0) return "over_tak";
  if (andel >= VARNING_ANDEL) return "varning";
  return "ok";
}

/** Formaterar antal celler med svensk sifferform (t.ex. `~1,8 M celler` eller `~45 000 celler`). */
export function formateraCeller(celler: number): string {
  if (celler >= 100_000) {
    return `~${formatera(celler / 1_000_000, 1)} M celler`;
  }
  return `~${formatera(Math.round(celler), 0)} celler`;
}

/** Diskret omfattningsrad nära beräkningsknapparna (t.ex. `2 rutor · ~1,8 M celler`). */
export function berakningsomfangRadText(o: BerakningsomfangInfo): string {
  const rutor = Math.round(o.antal_rutor);
  const rutOrd = rutor === 1 ? "ruta" : "rutor";
  return `${formatera(rutor, 0)} ${rutOrd} · ${formateraCeller(o.uppskattade_celler)}`;
}

/** Lugn svensk förvarning när området närmar sig eller överstiger serverns cellgräns, annars null. */
export function berakningsomfangVarningText(o: BerakningsomfangInfo): string | null {
  const niva = berakningsomfangNiva(o);
  if (niva === "ok") return null;
  if (typeof o.meddelande === "string" && o.meddelande.trim()) {
    return o.meddelande.trim();
  }
  const maxC = o.max_sankgraf_celler;
  const par =
    maxC >= 100_000
      ? `${formatera(o.uppskattade_celler / 1_000_000, 1)} / ${formatera(maxC / 1_000_000, 1)} M celler`
      : `${formatera(Math.round(o.uppskattade_celler), 0)} / ${formatera(Math.round(maxC), 0)} celler`;
  const verb = niva === "over_tak" ? "överstiger" : "är nära";
  return `Området ${verb} serverns cellgräns (${par}). Rita en mindre avgränsning eller kör ett ämne i taget.`;
}

/** HTML för omfattningsrad och ev. förvarning i Steg 2 (`""` när projektet saknar `berakningsomfang`). */
export function berakningsomfangHtml(
  o: BerakningsomfangInfo | null | undefined,
  opts?: { medId?: boolean },
): string {
  if (!o || !giltigtPositivtTal(o.antal_rutor) || !giltigtPositivtTal(o.uppskattade_celler)) {
    return "";
  }
  const medId = opts?.medId !== false;
  const niva = berakningsomfangNiva(o);
  const radId = medId ? ` id="ar-omfang"` : "";
  const rad = `<p class="flode-liten flode-dampad ar-omfang"${radId}>${esc(berakningsomfangRadText(o))}</p>`;
  const varningText = berakningsomfangVarningText(o);
  if (!varningText) return rad;
  const varnId = medId ? ` id="ar-omfang-varning"` : "";
  return `${rad}<p class="flode-liten ar-omfang-varning ar-omfang-${niva}"${varnId} role="status">${esc(varningText)}</p>`;
}
