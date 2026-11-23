/*
 * Matematik och forskning (#/matematik): domän, metod, formel, status och länk till koden, samt de öppna
 * frågorna Q1–Q5. Innehållet kommer ur data.generated.ts, som genereras ur data/matematik/metoder.toml och
 * data/mcp/metodstatus.toml, så att sidan inte kan glida isär från dokumentet och koden. Formlerna visas som
 * läsbar text i <code>; något formelbibliotek (KaTeX) läggs inte till för en enda sida. Full LaTeX finns i
 * docs/MATEMATIK.md. Kräver inloggning som resten av appen (routern).
 */
import { ritaFlodeHuvud } from "../flode/huvud.ts";
import { esc } from "../sidor.ts";
import { MATEMATIK } from "./data.generated.ts";
import type { FlodeNiva, MatematikData, MatematikLank, MatematikMetod } from "./typer.ts";

const NIVA_KLASS: Record<FlodeNiva, string> = {
  flode: "svarar",
  karta: "delvis",
  api: "delvis",
  cli: "delvis",
  fristaende: "saknas",
};

function lank(l: MatematikLank): string {
  return l.href
    ? `<a href="${esc(l.href)}" target="_blank" rel="noopener noreferrer"><code>${esc(l.text)}</code></a>`
    : `<span>${esc(l.text)}</span>`;
}

function lista(poster: MatematikLank[]): string {
  return poster.length ? `<ul class="mat-lista">${poster.map((p) => `<li>${lank(p)}</li>`).join("")}</ul>` : "";
}

export function metodHtml(m: MatematikMetod): string {
  const formler = m.formler.map((f) => `<code class="mat-formel">${esc(f)}</code>`).join("");
  const kontrakt = m.kontrakt.length ? m.kontrakt.map((k) => esc(k)).join(", ") : "inget eget";
  return `
    <article class="mat-metod" id="${esc(m.id)}">
      <div class="flode-rubrikrad">
        <h3 class="mat-h3">${esc(m.namn)}</h3>
        <span class="mat-etiketter">
          <span class="flode-etikett saknas" data-status="${esc(m.status)}">Metodstatus: ${esc(m.status)}</span>
          <span class="flode-etikett ${NIVA_KLASS[m.flode]}" data-flode="${esc(m.flode)}">${esc(m.flodeText)}</span>
        </span>
      </div>
      <p class="mat-text">${esc(m.sammanfattning)}</p>
      <div class="mat-formler">${formler}</div>
      <p class="flode-liten flode-dampad">Kod</p>
      ${lista(m.kod)}
      <details class="mat-mer">
        <summary>Kontrakt, facittest och anmärkning</summary>
        <p class="mat-text"><strong>Kontrakt:</strong> ${kontrakt}</p>
        <p class="flode-liten flode-dampad">Facittest</p>
        ${lista(m.facit) || '<p class="mat-text">Inga.</p>'}
        ${m.anmarkning ? `<p class="mat-text"><strong>Observera:</strong> ${esc(m.anmarkning)}</p>` : ""}
      </details>
    </article>`;
}

export function sidaMatematik(data: MatematikData = MATEMATIK): string {
  const domaner = data.doman
    .map(
      (d) => `
      <section class="mat-doman" aria-labelledby="mat-${esc(d.id)}">
        <h2 class="mat-h2" id="mat-${esc(d.id)}">${esc(d.namn)}</h2>
        ${d.metoder.map(metodHtml).join("")}
      </section>`,
    )
    .join("");
  const fragor = data.fragor
    .map(
      (q) => `
      <article class="mat-metod" id="${esc(q.id)}">
        <h3 class="mat-h3"><span class="mat-q">${esc(q.id)}</span> ${esc(q.titel)}</h3>
        <p class="mat-text">${esc(q.kort)}</p>
        <p class="flode-liten flode-dampad">Underlag</p>
        ${lista(q.underlag)}
      </article>`,
    )
    .join("");
  const nav = [...data.doman.map((d) => ({ id: `mat-${d.id}`, namn: d.namn })), { id: "mat-fragor", namn: "Öppna frågor" }]
    .map((n) => `<a href="#/matematik" data-hopp="${esc(n.id)}">${esc(n.namn)}</a>`)
    .join("");
  return `
    <div class="mat-sida">
      <h1 class="flode-h">Matematik och forskning</h1>
      <p class="mat-text flode-dampad">Hur beräkningarna hänger ihop med koden, och vilka forskningsfrågor som är öppna. Alla metoder är skisser tills en människa har klassat dem som rapportmetoder. Formlerna visas som text; fullständig LaTeX, facittest och mätvärden med datum finns i <a href="${esc(data.kodbas)}" target="_blank" rel="noopener noreferrer"><code>docs/MATEMATIK.md</code></a>.</p>
      <nav class="mat-nav" aria-label="Avsnitt">${nav}</nav>
      ${domaner}
      <section class="mat-doman" aria-labelledby="mat-fragor">
        <h2 class="mat-h2" id="mat-fragor">Öppna frågor</h2>
        ${fragor}
      </section>
    </div>`;
}

/** Ritar sidhuvudet (inget steg aktivt) och sidan; kopplar avsnittslänkarna. */
export function visaMatematik(huvud: HTMLElement, rot: HTMLElement): void {
  ritaFlodeHuvud(huvud, null, null);
  rot.innerHTML = sidaMatematik();
  rot.querySelectorAll<HTMLAnchorElement>("a[data-hopp]").forEach((a) =>
    a.addEventListener("click", (h) => {
      h.preventDefault();
      document.getElementById(a.dataset.hopp ?? "")?.scrollIntoView({ block: "start" });
    }),
  );
  window.requestAnimationFrame(() => {
    const h1 = rot.querySelector<HTMLElement>("h1");
    if (h1) {
      h1.setAttribute("tabindex", "-1");
      h1.focus({ preventScroll: true });
    }
  });
}
