/*
 * Sidorna utanför flödet: inloggningen, om beräkningarna och (bara med inloggning och kontodata)
 * förvaltning. Varje sida är en funktion som ger HTML ur data från API:t. Sidhuvudet är flödets
 * (flode/huvud.ts); projektsidan och startsidan finns inte längre, flödets steg 1 är start.
 *
 * Regel: inget i gränssnittet får se ut att fungera om det inte gör det. Det finns därför inga
 * platshållare, inga knappar utan funktion och inga tal som inte kommer ur en körning
 * (AGENTS.md: tal kommer bara ur beräkningar). Det som saknas döljs eller förklaras.
 */
import { formatera } from "./tal.ts";
import { kontoLage, rollNamn } from "./konto.ts";
import type { ModulInfo, OmradeSammanfattning } from "./types.ts";

export function esc(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

export type SidId = "start" | "fragor" | "om" | "matematik" | "forvaltning" | "nycklar" | "administration";

export const PROJEKT_ID = "demokvarteret";

export { formatera };

function etikett(text: string, tagg = "h2"): string {
  return `<${tagg} class="etikett">${esc(text)}</${tagg}>`;
}

function statusKlass(m: ModulInfo): string {
  return m.status === "finns_i_demon" ? "i-drift" : "";
}

/* ---------- Inloggning (servern med inloggning, utloggad besökare) ---------- */

/**
 * Hel sida med målningen i bakgrunden: namnet, en mening och inloggningsknappen. Samma markup på
 * dator och mobil (förslag A och D); bilden väljs efter skärmbredd. Säger medvetet lite om vad
 * Floden gör.
 */
export function sidaInloggning(inloggningsHref: string): string {
  return `
    <section class="inloggning" aria-labelledby="inloggning-rubrik">
      <img class="inloggning-bild"
        src="/bild/inloggning-1600.webp"
        srcset="/bild/inloggning-800.webp 800w, /bild/inloggning-1600.webp 1600w"
        sizes="100vw"
        alt="Målning av en bäck som rinner genom grönska mot ett ljust gult ljus" />
      <div class="inloggning-text">
        <h1 id="inloggning-rubrik" class="inloggning-namn">Floden</h1>
        <p class="inloggning-mening">Hantera floden av utredningsfrågor<br />på&nbsp;ett&nbsp;ställe.</p>
        <div class="inloggning-handling">
          <a class="inloggning-knapp" href="${esc(inloggningsHref)}" data-konto="logga-in">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4"></path><path d="M10 17l5-5-5-5"></path><path d="M15 12H3"></path></svg>
            Logga in med Google</a>
          <span class="inloggning-not">Bara för inbjudna</span>
        </div>
      </div>
    </section>`;
}

/* ---------- Om beräkningarna ---------- */

export function sidaOm(moduler: ModulInfo[], kontrollfall: OmradeSammanfattning[]): string {
  const kort = moduler
    .map((m) => {
      const losare = (m.losare ?? [])
        .map((l) => `<li>${esc(l.visningsnamn)} <span class="dampad mono">${esc(l.namn)} · version ${esc(l.version)}</span></li>`)
        .join("");
      return `
      <article class="modul-rad">
        <div>
          <h3>${esc(m.namn)}</h3>
          <p>${esc(m.beskrivning)}</p>
        </div>
        <div>
          <span class="modul-status ${statusKlass(m)}">${esc(m.status_text)}</span>
          ${
            losare
              ? `<details class="teknisk"><summary>Teknisk information</summary><ul class="enkel-lista">${losare}</ul></details>`
              : ""
          }
        </div>
      </article>`;
    })
    .join("");
  const rader = kontrollfall
    .map(
      (o) => `
      <li class="kontroll-rad">
        <span><strong>${esc(o.kod)}</strong> ${esc(o.namn)}</span>
        <span class="dampad">${esc(o.beskrivning)}</span>
      </li>`,
    )
    .join("");
  return `
    <div class="sidbredd innehall">
      <h1 class="sidrubrik stor">Om beräkningarna</h1>
      <p class="ingress om-ingress">Floden beräknar, språkmodeller gissar inte: varje tal i appen kommer ur en beräkning med känt underlag, och samma indata ger samma resultat.</p>

      <section>
        <div class="sektionsrad linje-under-stark">${etikett("Vad som finns")}</div>
        ${kort || '<p class="dampad tom">Modullistan gick inte att hämta. Ladda om sidan; kvarstår felet, kontakta den som driftar Floden.</p>'}
      </section>

      <section class="kontroll-sektion">
        <div class="sektionsrad linje-under-stark">${etikett("Kontroll av beräkningarna")}</div>
        <p class="avsnitt-ingress">Syntetiska fall med känt svar som modellen prövas mot. De är inte projekt och säger inget om verkliga platser.</p>
        ${
          rader
            ? `<details class="teknisk"><summary>${kontrollfall.length} kontrollfall</summary><ul class="kontroll-lista">${rader}</ul></details>`
            : ""
        }
      </section>
    </div>`;
}

/* ---------- Förvaltning (bara med inloggning och kontodata) ---------- */

export function sidaForvaltning(): string {
  const l = kontoLage();
  if (l.lage !== "in" || l.medlemskap.length === 0) return "";
  const rader = l.medlemskap
    .map(
      (m) =>
        `<tr><td>${esc(m.organisation)}</td><td>${esc(m.forvaltning ?? "–")}</td><td>${esc(rollNamn(m.roll))}</td></tr>`,
    )
    .join("");
  const projekt = l.projekt.length
    ? `<section>
        <div class="sektionsrad linje-under-stark">${etikett("Dina projekt")}</div>
        <ul class="enkel-lista rader">${l.projekt.map((p) => `<li>${esc(p.namn)}<span class="dampad">${esc(rollNamn(p.roll))}</span></li>`).join("")}</ul>
      </section>`
    : "";
  return `
    <div class="sidbredd innehall">
      <h1 class="sidrubrik stor">Förvaltning</h1>
      <section>
        <div class="sektionsrad linje-under-stark">${etikett("Dina medlemskap")}</div>
        <div class="tabell-ruta">
          <table class="data-tabell">
            <thead><tr><th scope="col">Organisation</th><th scope="col">Förvaltning</th><th scope="col">Roll</th></tr></thead>
            <tbody>${rader}</tbody>
          </table>
        </div>
      </section>
      ${projekt}
    </div>`;
}
