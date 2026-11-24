/*
 * Kompletterande analysverktyg i resultatpanelen: monotont MKN-intervall (`POST /api/analys/luft-mkn`)
 * och bullerskärmsval med CELF (`POST /api/analys/buller-skarmar`). Alla tal räknas på servern.
 */
import { lasJson } from "../api.ts";
import { esc } from "../sidor.ts";
import {
  formateraBullerSkarmar,
  formateraMknIntervall,
  tolkaSkarmkandidater,
  tolkaTallista,
  type BullerSkarmarSvar,
  type MknIntervallSvar,
} from "./nyckeltal.ts";

export function extraAnalyserHtml(): string {
  return `
    <details class="flode-extra" id="rs-extra">
      <summary class="flode-liten">Fler analyser: MKN-intervall (PM10) och bullerskärmar</summary>
      <div class="flode-extra-block">
        <p class="flode-liten flode-dampad"><strong>Luft · MKN-intervall (PM10)</strong> – ange minst 24 timvärden (µg/m³, lokalt bidrag).</p>
        <textarea id="rs-mkn-serie" rows="2" class="flode-inmatning" aria-label="Timvärden PM10">32, 38, 45, 52, 60, 58, 49, 41, 35, 30, 28, 26, 30, 36, 44, 55, 62, 57, 48, 40, 34, 31, 29, 27</textarea>
        <div class="flode-extra-rad">
          <label class="flode-liten">Bakgrund (µg/m³) <input id="rs-mkn-bakg" type="number" step="0.5" value="12" /></label>
          <button type="button" class="flode-knapp liten" id="rs-mkn-kor">Beräkna MKN-intervall</button>
        </div>
        <div id="rs-mkn-ut" class="flode-liten"></div>
      </div>
      <div class="flode-extra-block">
        <p class="flode-liten flode-dampad"><strong>Buller · Skärmval (CELF)</strong> – grundnivå per mottagare (dB) och skärmkandidater (reduktion i dB).</p>
        <label class="flode-liten">Grundnivåer (dB) <input id="rs-buller-bas" type="text" value="62, 65, 58, 67, 61" /></label>
        <label class="flode-liten">Skärmar (<code>namn: dB, ...</code>)
          <textarea id="rs-buller-skarmar" rows="3" class="flode-inmatning">Skärm A: 6, 4, 1, 0, 2
Skärm B: 0, 3, 2, 7, 5
Skärm C: 3, 5, 0, 4, 1</textarea>
        </label>
        <div class="flode-extra-rad">
          <label class="flode-liten">Riktvärde (dB) <input id="rs-buller-rikt" type="number" step="1" value="60" /></label>
          <label class="flode-liten">Antal skärmar <input id="rs-buller-k" type="number" min="1" max="20" value="2" /></label>
          <button type="button" class="flode-knapp liten" id="rs-buller-kor">Välj bullerskärmar</button>
        </div>
        <div id="rs-buller-ut" class="flode-liten"></div>
      </div>
    </details>`;
}

export function kopplaExtraAnalyser(rot: HTMLElement, signal: AbortSignal): void {
  const mknKnapp = rot.querySelector<HTMLButtonElement>("#rs-mkn-kor");
  const mknUt = rot.querySelector<HTMLElement>("#rs-mkn-ut");
  mknKnapp?.addEventListener(
    "click",
    async () => {
      if (!mknUt) return;
      const serieText = rot.querySelector<HTMLTextAreaElement>("#rs-mkn-serie")?.value ?? "";
      const bakg = Number(rot.querySelector<HTMLInputElement>("#rs-mkn-bakg")?.value ?? "0");
      const serie = tolkaTallista(serieText);
      if (!serie || serie.length < 24) {
        mknUt.innerHTML = `<p class="flode-fel">Ange minst 24 giltiga timvärden.</p>`;
        return;
      }
      mknUt.innerHTML = `<p class="flode-dampad">Beräknar MKN-intervall …</p>`;
      try {
        const svar = await lasJson<MknIntervallSvar>("/api/analys/luft-mkn", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            timvis_lokal_ug_m3: serie,
            bakgrund_ug_m3: Number.isFinite(bakg) ? bakg : 0,
            emissionsfaktor_intervall: [0.8, 1.25],
            depositionsfaktor_intervall: [0.8, 1.25],
          }),
        });
        const f = formateraMknIntervall(svar);
        mknUt.innerHTML = `<p>${esc(f.status)}</p><dl class="flode-tal">${f.tal.map((x) => `<div><dt>${esc(x.etikett)}</dt><dd>${esc(x.varde)}</dd></div>`).join("")}</dl>`;
      } catch (e) {
        mknUt.innerHTML = `<p class="flode-fel">${esc(e instanceof Error ? e.message : String(e))}</p>`;
      }
    },
    { signal },
  );

  const bullerKnapp = rot.querySelector<HTMLButtonElement>("#rs-buller-kor");
  const bullerUt = rot.querySelector<HTMLElement>("#rs-buller-ut");
  bullerKnapp?.addEventListener(
    "click",
    async () => {
      if (!bullerUt) return;
      const basText = rot.querySelector<HTMLInputElement>("#rs-buller-bas")?.value ?? "";
      const skarmText = rot.querySelector<HTMLTextAreaElement>("#rs-buller-skarmar")?.value ?? "";
      const rikt = Number(rot.querySelector<HTMLInputElement>("#rs-buller-rikt")?.value ?? "60");
      const k = Math.max(1, Math.round(Number(rot.querySelector<HTMLInputElement>("#rs-buller-k")?.value ?? "2")));
      const bas = tolkaTallista(basText);
      const skarmar = tolkaSkarmkandidater(skarmText);
      if (!bas || !skarmar) {
        bullerUt.innerHTML = `<p class="flode-fel">Kontrollera formatet för grundnivåer och skärmrader.</p>`;
        return;
      }
      bullerUt.innerHTML = `<p class="flode-dampad">Beräknar skärmval …</p>`;
      try {
        const svar = await lasJson<BullerSkarmarSvar>("/api/analys/buller-skarmar", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            bas_niva_db: bas,
            skarm_reduktion_db: skarmar,
            k,
            riktvarde_db: Number.isFinite(rikt) ? rikt : 60,
          }),
        });
        const f = formateraBullerSkarmar(svar);
        bullerUt.innerHTML = `
          <p>${esc(f.sammanfattning)}</p>
          ${
            f.rader.length > 0
              ? `<ul>${f.rader.map((r) => `<li><strong>${esc(r.skarm_id)}</strong>: +${esc(r.marginal_vinst)} (totalt ${esc(r.kumulativ_vinst)}, ${esc(r.nya_godkanda)} nya godkända)</li>`).join("")}</ul>`
              : ""
          }`;
      } catch (e) {
        bullerUt.innerHTML = `<p class="flode-fel">${esc(e instanceof Error ? e.message : String(e))}</p>`;
      }
    },
    { signal },
  );
}
