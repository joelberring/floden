/*
 * Sidorna utanför kartan: start, projekt, om beräkningarna och (bara med inloggning och
 * kontodata) förvaltning. Varje sida är en funktion som ger HTML ur data från API:t.
 *
 * Regel: inget i gränssnittet får se ut att fungera om det inte gör det. Det finns därför inga
 * platshållare, inga knappar utan funktion och inga tal som inte kommer ur en körning
 * (AGENTS.md: tal kommer bara ur beräkningar). Det som saknas döljs eller förklaras.
 */
import { forvaltningSynlig, halsningsnamn, kontoHjalteHtml, kontoHuvudHtml, kontoLage } from "./konto.ts";
import type { ModulInfo, OmradeSammanfattning, ProjektAvsnitt, ProjektSvar, RapportRad } from "./types.ts";

export function esc(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

export type SidId = "start" | "projekt" | "karta" | "fragor" | "om" | "forvaltning";

export const PROJEKT_ID = "demokvarteret";

const FORMAT: Record<number, Intl.NumberFormat> = {};

/** Tal med svensk decimalkomma och tusentalsmellanrum. */
export function formatera(v: number, decimaler: number): string {
  const f = (FORMAT[decimaler] ??= new Intl.NumberFormat("sv-SE", {
    minimumFractionDigits: decimaler,
    maximumFractionDigits: decimaler,
  }));
  return f.format(v);
}

function etikett(text: string, tagg = "h2"): string {
  return `<${tagg} class="etikett">${esc(text)}</${tagg}>`;
}

function statusKlass(m: ModulInfo): string {
  return m.status === "finns_i_demon" ? "i-drift" : "";
}

/* ---------- Sidhuvud (samma meny på alla sidor) ---------- */

export function sidhuvud(aktiv: SidId): string {
  const lank = (id: SidId, text: string, href: string): string =>
    `<a href="${href}"${aktiv === id ? ' aria-current="page"' : ""}>${esc(text)}</a>`;
  return `
    <div class="huvud-inre">
      <a class="ordmarke" href="#/">Floden</a>
      <button type="button" class="meny-knapp" aria-expanded="false" aria-controls="huvud-nav">Meny</button>
      <nav class="huvud-nav" id="huvud-nav" aria-label="Huvudmeny">
        ${lank("start", "Start", "#/")}
        ${lank("projekt", "Projekt", `#/projekt/${PROJEKT_ID}`)}
        ${lank("karta", "Karta", `#/karta/${PROJEKT_ID}`)}
        ${lank("fragor", "Utredningsfrågor", "#/fragor")}
        ${lank("om", "Om beräkningarna", "#/om")}
        ${forvaltningSynlig() ? lank("forvaltning", "Förvaltning", "#/forvaltning") : ""}
        ${kontoHuvudHtml()}
      </nav>
    </div>`;
}

/* ---------- Startsida ---------- */

export function sidaStart(moduler: ModulInfo[], harProjekt: boolean): string {
  const namn = halsningsnamn();
  const modulKort = moduler
    .map(
      (m) => `
      <article class="modul-kort">
        <h3>${esc(m.namn)}</h3>
        <p>${esc(m.beskrivning)}</p>
        <span class="modul-status ${statusKlass(m)}">${esc(m.status_text)}</span>
      </article>`,
    )
    .join("");
  const steg: Array<[string, string, string]> = [
    ["1", "Välj ett exempel", "Börja med demokvarteret, ett påhittat område med gator, gårdar och en sänka."],
    ["2", "Se före och efter", "Kartan visar hur sol och skyfall ser ut i dag och efter att ett hus byggts eller höjts."],
    ["3", "Läs av värden", "För markören över kartan: värdena för före, efter och skillnad står under den."],
  ];
  const cta = harProjekt
    ? `<a class="knapp knapp-svart knapp-stor" href="#/projekt/${PROJEKT_ID}">Öppna demokvarteret</a>
       <a class="knapp knapp-stor" href="#/karta/${PROJEKT_ID}">Gå direkt till kartan</a>`
    : `<a class="knapp knapp-stor" href="#/fragor">Öppna utredningsfrågorna</a>`;
  const demoSaknas = harProjekt
    ? ""
    : `<p class="hjalp-ruta" role="note">Demokvarteret är inte byggt på den här datorn än. Kör <code>floden demo</code> i en terminal och ladda om sidan.</p>`;
  return `
    <section class="hjalte sidbredd">
      <div>
        ${namn ? `<p class="halsning">Hej ${esc(namn)}.</p>` : ""}
        <h1 class="hjalte-rubrik">Se hur ett nytt hus påverkar sol och skyfall.</h1>
      </div>
      <div class="hjalte-text">
        <p class="ingress">Floden räknar på mark, byggnader och regn och visar före och efter på en karta. Exemplet här är ett påhittat kvarter.</p>
        <div class="knapp-par">${cta}${kontoHjalteHtml()}</div>
        ${demoSaknas}
      </div>
    </section>

    <section id="steg" class="linje-topp">
      <div class="sidbredd">
        <div class="sektionsrad">${etikett("Så använder du Floden")}</div>
        <ol class="steg-lista">
          ${steg
            .map(
              ([nr, rubrik, text]) => `
          <li class="steg">
            <span class="steg-nr" aria-hidden="true">${nr}</span>
            <h3>${esc(rubrik)}</h3>
            <p>${esc(text)}</p>
          </li>`,
            )
            .join("")}
        </ol>
      </div>
    </section>

    <section id="moduler" class="linje-topp">
      <div class="sidbredd">
        <div class="sektionsrad">
          ${etikett("Vad Floden kan räkna på")}
          <span class="dampad">Statusen gäller den här demon.</span>
        </div>
        <div class="modul-rutnat">${modulKort || '<p class="dampad">Modullistan gick inte att hämta. Är servern igång?</p>'}</div>
      </div>
    </section>

    <section id="principer" class="sidbredd principer">
      <div class="princip">
        ${etikett("Beräknat, inte gissat", "h3")}
        <p>Varje tal kommer från en beräkning som går att köra om med samma svar.</p>
      </div>
      <div class="princip">
        ${etikett("Öppen kod", "h3")}
        <p>Koden är öppen (licens GPL v3) och kan granskas av vem som helst.</p>
      </div>
      <div class="princip">
        ${etikett("Data stannar hos er", "h3")}
        <p>Den här versionen körs på din egen dator. Inget skickas någonstans.</p>
      </div>
    </section>

    <footer class="sidfot">
      <span>Floden</span>
      <span class="sidfot-lankar">
        <a href="#/om">Om beräkningarna</a>
        <a href="https://github.com/joelberring/floden">Källkod</a>
      </span>
    </footer>`;
}

/* ---------- Projekt ---------- */

function matttabell(a: ProjektAvsnitt): string {
  if (a.matt.length === 0) return "";
  const huvud = a.kolumner.map((k) => `<th scope="col" class="hoger">${esc(k.rubrik)}</th>`).join("");
  const rader = a.matt
    .map((m) => {
      const celler = a.kolumner
        .map((k) => {
          const v = m.varden[k.id];
          return `<td class="hoger">${v === undefined ? "–" : `${formatera(v, m.decimaler)} ${esc(m.enhet)}`}</td>`;
        })
        .join("");
      return `<tr><th scope="row">${esc(m.namn)}</th>${celler}</tr>`;
    })
    .join("");
  return `
    <div class="tabell-ruta">
      <table class="jamfor-tabell">
        <thead><tr><th scope="col"><span class="visuellt-dold">Mått</span></th>${huvud}</tr></thead>
        <tbody>${rader}</tbody>
      </table>
    </div>`;
}

function tekniskInfo(a: ProjektAvsnitt): string {
  const t = a.teknisk;
  const losare = t.losare
    .map((l) => `<li>${esc(l.visningsnamn)}: <span class="mono">${esc(l.namn)}</span>, version ${esc(l.version)}${l.kodversion ? `, kod ${esc(l.kodversion)}` : ""}</li>`)
    .join("");
  return `
    <details class="teknisk">
      <summary>Teknisk information</summary>
      <ul class="enkel-lista">
        ${losare}
        <li>${t.jobb} beräkningar, ${t.fran_cache} hämtade ur cachen, ${t.korda} körda (${formatera(t.total_tid_s, 1)} s)</li>
        <li>Körning: <span class="mono">${esc(t.korning_id)}</span>, fråga: <span class="mono">${esc(t.fraga_fil ?? t.fraga_id)}</span></li>
        <li>Koordinatsystem: ${esc(t.koordinatsystem)}</li>
        <li>Manifest: ${esc(t.manifest)}</li>
      </ul>
    </details>`;
}

/** Kontrollens utfall i klarspråk: grönt = får exporteras, rött = något måste rättas. */
function rapportKontroll(r: RapportRad): string {
  if (!r.kontroll) return `<span class="rapport-status ej">Inte kontrollerad</span>`;
  if (r.kontroll.godkand) return `<span class="rapport-status ok">Kontrollen godkänd</span>`;
  const n = r.kontroll.antal_fel;
  return `<span class="rapport-status fel">Kontrollen hittade ${n} fel</span>`;
}

export function rapportLista(projektId: string, rapporter: RapportRad[] | undefined): string {
  if (!rapporter || rapporter.length === 0) {
    return `<p class="dampad">Inga rapporter än. En rapport skapas med AI via <code>floden mcp</code> eller med <code>python tools/rapport_demo.py</code>.</p>`;
  }
  const bas = `/api/projekt/${encodeURIComponent(projektId)}/rapporter`;
  const rader = rapporter
    .map((r) => {
      const lankar = (["word", "pdf"] as const)
        .filter((f) => r.filer[f])
        .map(
          (f) =>
            `<a class="textlank" href="${bas}/${encodeURIComponent(r.rapport_id)}/fil/${f}" download>${f === "word" ? "Word" : "PDF"}</a>`,
        )
        .join(" · ");
      return `
      <li class="rapport-rad">
        <div>
          <strong>${esc(r.titel)}</strong>
          <span class="dampad">${esc(r.skapad.slice(0, 10))} · ${r.antal_pastaenden} påståenden om ${r.antal_fragor} frågor · skissunderlag</span>
        </div>
        <div class="rapport-hoger">${rapportKontroll(r)}${lankar ? `<span class="rapport-filer">${lankar}</span>` : `<span class="dampad">Ingen fil exporterad</span>`}</div>
      </li>`;
    })
    .join("");
  return `<ul class="enkel-lista rapport-lista">${rader}</ul>`;
}

export function sidaProjekt(projekt: ProjektSvar | null, moduler: ModulInfo[]): string {
  if (!projekt) {
    return `
    <div class="sidbredd innehall">
      <h1 class="sidrubrik stor">Projekt</h1>
      <p class="hjalp-ruta" role="note">Det finns inget projekt att visa än. Demokvarteret byggs med <code>floden demo</code>: kör det i en terminal och ladda om sidan.</p>
    </div>`;
  }
  const vyAv = (avsnitt: string): string | undefined => projekt.vyer.find((v) => v.avsnitt === avsnitt)?.id;
  const avsnitt = projekt.avsnitt
    .map((a) => {
      const vy = vyAv(a.id);
      const regn = a.regn
        ? `<p class="regn-rad"><span class="etikett-liten">Regn som användes</span> ${esc(a.regn.text)}</p>`
        : "";
      const antaganden = (a.antaganden ?? [])
        .map((t) => `<p class="regn-rad"><span class="etikett-liten">Antagande</span> ${esc(t)}</p>`)
        .join("");
      return `
      <section class="projekt-avsnitt" id="avsnitt-${esc(a.id)}">
        <div class="sektionsrad linje-under-stark">
          ${etikett(a.rubrik)}
          ${vy ? `<a class="textlank" href="#/karta/${PROJEKT_ID}?vy=${encodeURIComponent(vy)}">Se på kartan →</a>` : ""}
        </div>
        <p class="avsnitt-ingress">${esc(a.ingress)}</p>
        ${matttabell(a)}
        ${regn}
        ${antaganden}
        ${tekniskInfo(a)}
      </section>`;
    })
    .join("");
  const andringar = projekt.andringar
    .map((x) => `<li><strong>${esc(x.rubrik)}.</strong> ${esc(x.text)}</li>`)
    .join("");
  const ovriga = moduler
    .filter((m) => m.status !== "finns_i_demon")
    .map((m) => `<li><strong>${esc(m.namn)}</strong><span class="dampad">${esc(m.status_text)}</span></li>`)
    .join("");
  return `
    <div class="sidbredd innehall">
      <div class="rubrikrad">
        <div>
          <p class="projekt-etikett"><span class="chip">Påhittat kvarter</span></p>
          <h1 class="sidrubrik stor">${esc(projekt.namn)}</h1>
          <p class="dampad projekt-ingress">${esc(projekt.beskrivning)}</p>
        </div>
        <div class="knapp-par">
          <a class="knapp knapp-svart" href="#/karta/${PROJEKT_ID}">Öppna kartan</a>
        </div>
      </div>
      <p class="paahittat-ruta" role="note">${esc(projekt.paahittat_text)}</p>

      <div class="tvadelad">
        <div class="huvuddel">
          <section>
            <div class="sektionsrad linje-under-stark">${etikett("Vad som ändras i kvarteret")}</div>
            <ul class="enkel-lista andringar">${andringar}</ul>
          </section>
          ${avsnitt}
        </div>
        <aside class="biddel">
          ${
            ovriga
              ? `<section class="sidoblock">
            ${etikett("Övriga delar av Floden")}
            <ul class="enkel-lista rader">${ovriga}</ul>
          </section>`
              : ""
          }
          <section class="sidoblock">
            ${etikett("Rapporter")}
            ${rapportLista(projekt.id, projekt.rapporter)}
          </section>
          <section class="sidoblock ram">
            ${etikett("Hur man läser talen")}
            <p>Talen är beräknade för det påhittade kvarteret och säger inget om verkliga platser. Före är kvarteret som det är, efter är med förändringen.</p>
          </section>
        </aside>
      </div>
    </div>`;
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
        <a class="textlank" href="#/arbetsbank/${encodeURIComponent(o.omrade_id)}">Öppna i arbetsbänken</a>
      </li>`,
    )
    .join("");
  return `
    <div class="sidbredd innehall">
      <h1 class="sidrubrik stor">Om beräkningarna</h1>
      <p class="ingress om-ingress">Floden beräknar, språkmodeller gissar inte: varje tal i appen kommer ur en beräkning med känt underlag, och samma indata ger samma resultat.</p>

      <section>
        <div class="sektionsrad linje-under-stark">${etikett("Vad som finns")}</div>
        ${kort || '<p class="dampad tom">Modullistan gick inte att hämta. Är servern igång?</p>'}
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
        `<tr><td>${esc(m.organisation)}</td><td>${esc(m.forvaltning ?? "–")}</td><td>${esc(m.roll)}</td></tr>`,
    )
    .join("");
  const projekt = l.projekt.length
    ? `<section>
        <div class="sektionsrad linje-under-stark">${etikett("Dina projekt")}</div>
        <ul class="enkel-lista rader">${l.projekt.map((p) => `<li>${esc(p.namn)}<span class="dampad">${esc(p.roll)}</span></li>`).join("")}</ul>
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
