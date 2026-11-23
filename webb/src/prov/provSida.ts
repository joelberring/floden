/*
 * HTML för provsidan `#/prov/webgpu`. Ren funktion av tillståndet, så att "ingen WebGPU",
 * "inte superadmin" och resultattabellen går att testa utan webbläsare.
 */
import type { ProvTillgang } from "./provtillgang.ts";
import { TILLGANG_TEXT } from "./provtillgang.ts";
import type { WebGpuStatus } from "./webgpuStatus.ts";
import { SKISSLAGE_TEXT } from "./benchmarkFormat.ts";

export interface ProvFallInfo {
  id: string;
  typ: string;
  namn: string;
  beskrivning: string;
}

export interface ProvResultatRad {
  fallId: string;
  falt: string;
  enhet: string;
  /** Tider i millisekunder: median, minsta, största och antal tidtagna körningar. */
  medianMs: number;
  minMs: number;
  maxMs: number;
  n: number;
  forstaMs: number;
  storstaFel: number;
  medelFel: number;
  andelOverTolerans: number;
  tolerans: number;
  antalIckeAndliga: number;
  godkand: boolean;
  /** Serverns tid för samma beräkning (f64, numpy), i ms. */
  serverMs: number | null;
}

export interface ProvTillstand {
  tillgang: ProvTillgang | "laddar";
  webgpu: WebGpuStatus | "laddar";
  fall: readonly ProvFallInfo[];
  resultat: readonly ProvResultatRad[];
  /** Fall som inte kunde köras (fel eller hoppat över): id och orsak. */
  problem: readonly { fallId: string; text: string }[];
  /** Pågående körning, eller null. */
  pagar: string | null;
  fel: string | null;
  upprepningar: number;
  toleranser: Readonly<Record<string, number>>;
}

export function esc(t: string): string {
  return t
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** Tal med svensk decimalkomma och `siffror` gällande siffror (vetenskaplig form för små tal). */
export function talText(v: number, siffror = 3): string {
  if (!Number.isFinite(v)) return "ej ändligt";
  if (v === 0) return "0";
  const a = Math.abs(v);
  if (a >= 1000 && a < 1e6) return String(Math.round(v));
  const t = a < 1e-3 || a >= 1e6 ? v.toExponential(siffror - 1) : v.toPrecision(siffror);
  return t.replace(".", ",");
}

export function procentText(andel: number): string {
  return `${talText(andel * 100, 3)} %`;
}

function webgpuAvsnitt(w: WebGpuStatus | "laddar"): string {
  if (w === "laddar") return `<p>Undersöker WebGPU …</p>`;
  if (!w.tillganglig) {
    return `<p class="prov-ingen-webgpu" role="alert"><strong>Ingen WebGPU.</strong> ${esc(w.meddelande)}</p>`;
  }
  const a = w.adapter;
  const rader = Object.entries(w.begransningar ?? {})
    .map(([k, v]) => `<tr><th scope="row">${esc(k)}</th><td>${esc(String(v))}</td></tr>`)
    .join("");
  const namn = [a?.vendor, a?.architecture, a?.device, a?.description].filter((x) => x).join(" · ");
  return `
    <p class="prov-webgpu-ok">${esc(w.meddelande)}</p>
    <p>Adapter: ${esc(namn || "okänd (webbläsaren döljer uppgifterna)")}</p>
    <details><summary>Begränsningar och funktioner</summary>
      <table class="prov-tabell"><tbody>${rader}</tbody></table>
      <p>${w.funktioner.length} funktioner: ${esc(w.funktioner.join(", ") || "inga")}</p>
    </details>`;
}

function resultatTabell(rader: readonly ProvResultatRad[]): string {
  if (rader.length === 0) return `<p class="prov-tom">Inga resultat än.</p>`;
  const rad = (r: ProvResultatRad): string => `
    <tr class="${r.godkand ? "prov-ok" : "prov-over"}">
      <th scope="row">${esc(r.fallId)}<br /><span class="prov-falt">${esc(r.falt)}</span></th>
      <td>${talText(r.medianMs)} ms<br /><span class="prov-sma">min ${talText(r.minMs)}, max ${talText(r.maxMs)}, n = ${r.n}; första ${talText(r.forstaMs)}</span></td>
      <td>${r.serverMs === null ? "–" : `${talText(r.serverMs)} ms`}</td>
      <td>${talText(r.storstaFel)} ${esc(r.enhet)}</td>
      <td>${talText(r.medelFel)} ${esc(r.enhet)}</td>
      <td>${procentText(r.andelOverTolerans)}<br /><span class="prov-sma">tolerans ${talText(r.tolerans)} ${esc(r.enhet)}${r.antalIckeAndliga > 0 ? `; ${r.antalIckeAndliga} icke-ändliga` : ""}</span></td>
      <td>${r.godkand ? "inom toleransen" : "ÖVER toleransen"}</td>
    </tr>`;
  return `
    <table class="prov-tabell" data-prov="resultat">
      <thead><tr><th>Fält</th><th>GPU-tid (median)</th><th>Servertid (f64)</th><th>Största fel</th><th>Medelfel</th><th>Celler över tolerans</th><th>Utfall</th></tr></thead>
      <tbody>${rader.map(rad).join("")}</tbody>
    </table>`;
}

export function provSidaHtml(t: ProvTillstand): string {
  const rubrik = `<h1 class="etikett prov-rubrik">Prov i webbläsaren: WebGPU</h1>`;
  if (t.tillgang === "laddar") return `<section class="prov">${rubrik}<p>Laddar …</p></section>`;
  if (t.tillgang !== "tillaten") {
    return `<section class="prov">${rubrik}<p class="prov-forbjuden" role="alert">${esc(TILLGANG_TEXT[t.tillgang])}</p></section>`;
  }
  const kanKora = typeof t.webgpu === "object" && t.webgpu.tillganglig && t.pagar === null;
  const dis = kanKora ? "" : " disabled";
  const toleransFalt = Object.entries(t.toleranser)
    .map(
      ([k, v]) => `<label class="prov-falt-rad">Tolerans ${esc(k)}
        <input type="number" step="any" min="0" value="${esc(String(v))}" data-prov-tolerans="${esc(k)}" />
      </label>`,
    )
    .join("");
  const problem = t.problem
    .map((p) => `<li><strong>${esc(p.fallId)}</strong>: ${esc(p.text)}</li>`)
    .join("");
  return `
    <section class="prov" aria-labelledby="prov-rubrik">
      ${rubrik}
      <p class="prov-skiss" role="note"><strong>Skissläge.</strong> ${esc(SKISSLAGE_TEXT)}</p>
      <p>Skuggarna <code>dagsljus_horisont.wgsl</code> och <code>vatten_2d.wgsl</code> körs här på ditt grafikkort i f32.
        Samma fall räknas på servern i f64 (facit). Sidan visar skillnaden och tiden, och du kan ladda ned
        resultatet som JSON (<code>floden-benchmark/v1</code>, källa webbläsare).</p>
      <h2 class="etikett">1. WebGPU</h2>
      ${webgpuAvsnitt(t.webgpu)}
      <h2 class="etikett">2. Fall</h2>
      <ul class="prov-fall">${t.fall.map((f) => `<li><strong>${esc(f.namn)}</strong><br />${esc(f.beskrivning)}</li>`).join("")}</ul>
      <div class="prov-kontroller">
        <label class="prov-falt-rad">Tidtagna körningar (efter en uppvärmning)
          <input type="number" min="1" max="50" step="1" value="${t.upprepningar}" data-prov-upprepningar />
        </label>
        ${toleransFalt}
      </div>
      <div class="prov-knappar">
        <button type="button" class="knapp" data-prov="kor-dagsljus"${dis}>Kör dagsljus</button>
        <button type="button" class="knapp" data-prov="kor-vatten"${dis}>Kör vatten 2D</button>
        <button type="button" class="knapp knapp-svart" data-prov="kor-alla"${dis}>Kör alla</button>
        <button type="button" class="knapp" data-prov="ladda-ned">Ladda ned JSON</button>
      </div>
      ${t.pagar ? `<p class="prov-pagar" role="status">${esc(t.pagar)}</p>` : ""}
      ${t.fel ? `<p class="prov-fel" role="alert">${esc(t.fel)}</p>` : ""}
      ${problem ? `<ul class="prov-problem">${problem}</ul>` : ""}
      <h2 class="etikett">3. Resultat</h2>
      ${resultatTabell(t.resultat)}
    </section>`;
}
