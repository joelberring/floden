/*
 * Om Floden (#/om): vad Floden kan nu per ämne, arbetsflödet, planen och en jämförelse med andra verktyg där
 * det som är uppmätt i Floden, det som tillverkarna själva har publicerat och det som inte är mätt hålls isär.
 * Innehållet kommer ur data.generated.ts, som genereras ur data/om/floden.toml (och metodregistret för
 * metodstatus), så att sidan och docs/OM-FLODEN.md inte kan glida isär. Alla tal går genom tal.ts. Under
 * jämförelsen ligger de äldre avsnitten om modulerna och kontrollen av beräkningarna.
 */
import { esc, omBerakningarSektioner } from "../sidor.ts";
import { formatera, formateraHogst } from "../tal.ts";
import type { ModulInfo, OmradeSammanfattning } from "../types.ts";
import { OM } from "./data.generated.ts";
import type { OmAmne, OmAndra, OmAndraTal, OmData, OmLank, OmTal, OmUppmatt } from "./typer.ts";

/** Brödtext ur datafilen: HTML-säker, med `kod` mellan bakåtcitattecken som <code>. */
function txt(text: string): string {
  return esc(text).replace(/`([^`]+)`/g, "<code>$1</code>");
}

function lank(l: OmLank): string {
  return l.href
    ? `<a href="${esc(l.href)}" target="_blank" rel="noopener noreferrer"><code>${esc(l.text)}</code></a>`
    : `<code>${esc(l.text)}</code>`;
}

/** Ett testat eller mätt tal som text: "55 av 80" eller "mer än 10 minuter". */
export function talText(t: { godkanda?: number; av?: number; varde?: number; prefix?: string; enhet?: string; decimaler?: number }): string {
  if (t.godkanda !== undefined && t.av !== undefined) return `${formatera(t.godkanda, 0)} av ${formatera(t.av, 0)}`;
  if (t.varde === undefined) return "";
  const prefix = t.prefix ? `${t.prefix} ` : "";
  const enhet = t.enhet ? ` ${t.enhet}` : "";
  return `${prefix}${formatera(t.varde, t.decimaler ?? 0)}${enhet}`;
}

/** En faktor: en decimal under tio, annars heltal; intervall som "1,2 till 1,8". */
export function faktorText(lag: number, hog: number): string {
  const f = (v: number): string => formateraHogst(v, v >= 10 ? 0 : 1);
  return lag === hog ? f(lag) : `${f(lag)} till ${f(hog)}`;
}

function talRad(t: OmTal): string {
  const tal = talText(t);
  const namn = txt(t.text ?? t.namn ?? "");
  return `<li>${namn}${tal ? `: <strong>${esc(tal)}</strong>` : ""} <span class="flode-liten flode-dampad">källa ${lank(t.kalla)}</span></li>`;
}

function lista(poster: string[]): string {
  return poster.length ? `<ul class="mat-lista om-lista">${poster.map((p) => `<li>${txt(p)}</li>`).join("")}</ul>` : "";
}

export function amneHtml(a: OmAmne): string {
  const metoder = a.metoder
    .map(
      (m) =>
        `<tr><td>${esc(m.namn)}</td><td><span class="flode-etikett saknas" data-status="${esc(m.status)}">${esc(m.status)}</span></td><td>${esc(m.flodeText)}</td></tr>`,
    )
    .join("");
  return `
    <article class="mat-metod" id="om-amne-${esc(a.id)}">
      <h3 class="mat-h3">${esc(a.namn)}</h3>
      <p class="flode-liten flode-dampad">Kan</p>
      ${lista(a.kan)}
      <p class="flode-liten flode-dampad">Metoder och metodstatus</p>
      <div class="tabell-ruta"><table class="data-tabell om-tabell">
        <thead><tr><th scope="col">Metod</th><th scope="col">Metodstatus</th><th scope="col">Var den nås</th></tr></thead>
        <tbody>${metoder}</tbody>
      </table></div>
      <p class="flode-liten flode-dampad">Testat</p>
      <ul class="mat-lista om-lista">${[...a.testat, ...a.nyckeltal].map(talRad).join("")}</ul>
      <p class="flode-liten flode-dampad">Begränsningar</p>
      ${lista(a.begransningar)}
    </article>`;
}

function uppmattRad(u: OmUppmatt): string {
  const jam =
    u.a_varde !== undefined && u.b_varde !== undefined && u.decimaler !== undefined
      ? `${esc(u.a_namn ?? "")} ${formatera(u.a_varde, u.decimaler)} ${esc(u.enhet ?? "")} mot ${esc(u.b_namn ?? "")} ${formatera(u.b_varde, u.decimaler)} ${esc(u.enhet ?? "")}`
      : "Hybrid mot full 2D, båda i Floden";
  const faktor =
    u.faktorLag !== null && u.faktorHog !== null ? `cirka ${faktorText(u.faktorLag, u.faktorHog)} gånger` : "";
  return `<tr>
    <td><strong>${txt(u.titel)}</strong><br><span class="flode-liten flode-dampad">${txt(u.anm)}</span></td>
    <td>${jam}</td>
    <td>${esc(faktor)}</td>
    <td>${txt(u.maskin)}</td>
    <td>${lank(u.kalla)}</td>
  </tr>`;
}

function andraTal(t: OmAndraTal): string {
  return `<li>${txt(t.text)}: <strong>${esc(talText(t))}</strong></li>`;
}

export function andraHtml(x: OmAndra, hamtad: string): string {
  const lankar = x.lankar
    .map((l) => `<a href="${esc(l.url)}" target="_blank" rel="noopener noreferrer">${esc(l.text)}</a>`)
    .join(", ");
  return `
    <article class="mat-metod" id="om-andra-${esc(x.id)}">
      <div class="flode-rubrikrad">
        <h4 class="mat-h3">${esc(x.verktyg)}</h4>
        <span class="mat-etiketter"><span class="flode-etikett delvis">${esc(x.amne)}</span></span>
      </div>
      <p class="mat-text"><strong>Utgivare:</strong> ${esc(x.upphov)}</p>
      <p class="mat-text"><strong>Vad som är publicerat:</strong> ${txt(x.vad)}</p>
      <p class="mat-text"><strong>Metod:</strong> ${txt(x.metod)}</p>
      <p class="mat-text"><strong>Licens:</strong> ${txt(x.licens)}</p>
      <p class="mat-text"><strong>Pris:</strong> ${txt(x.pris)}</p>
      <p class="mat-text"><strong>Körtid:</strong> ${txt(x.kortid)}</p>
      ${x.tal?.length ? `<ul class="mat-lista om-lista">${x.tal.map(andraTal).join("")}</ul>` : ""}
      <p class="flode-liten flode-dampad">Källa, hämtad ${esc(hamtad)}: ${lankar}</p>
    </article>`;
}

function rubrikLista(poster: { rubrik: string; text: string; kalla: OmLank }[]): string {
  return `<ul class="mat-lista om-lista">${poster
    .map((p) => `<li><strong>${esc(p.rubrik)}.</strong> ${txt(p.text)} <span class="flode-liten flode-dampad">källa ${lank(p.kalla)}</span></li>`)
    .join("")}</ul>`;
}

const NAV = [
  { id: "om-kan", namn: "Vad Floden kan nu" },
  { id: "om-plan", namn: "Plan" },
  { id: "om-jamforelse", namn: "Jämförelse med andra verktyg" },
  { id: "om-berakningar", namn: "Moduler och kontroll" },
];

/** Själva Om Floden-sidan, utan de äldre avsnitten om modulerna. */
export function omFlodenHtml(data: OmData = OM): string {
  const nav = NAV.map((n) => `<a href="#/om" data-hopp="${esc(n.id)}">${esc(n.namn)}</a>`).join("");
  const steg = data.steg
    .map((s) => `<li><strong>${esc(s.namn)}.</strong> ${txt(s.text)}</li>`)
    .join("");
  const plan = data.plan
    .map(
      (p) =>
        `<tr><td><strong>${esc(p.rubrik)}</strong></td><td>${txt(p.text)}</td><td><span class="flode-etikett delvis">${esc(p.status)}</span></td><td>${esc(p.uppdrag)}</td></tr>`,
    )
    .join("");
  const kortid = data.kortid.rader
    .map((r) => `<tr><td>${esc(r.amne)}</td><td>${txt(r.fall)}</td><td>${esc(formatera(r.varde_s, r.decimaler))} s</td></tr>`)
    .join("");
  return `
    <div class="mat-sida om-sida">
      <h1 class="flode-h">Om Floden</h1>
      <p class="mat-text flode-dampad">${txt(data.ingress)}</p>
      <p class="mat-text"><strong>Metodstatus:</strong> ${esc(data.statusText)} Skiss: ${esc(formatera(data.metodAntal.skiss, 0))} av ${esc(formatera(data.metodAntal.alla, 0))} metoder i registret. Sidan genereras ur <a href="${esc(data.kodbas)}" target="_blank" rel="noopener noreferrer"><code>docs/OM-FLODEN.md</code></a>; uppdaterad ${esc(data.uppdaterad)}.</p>
      <nav class="mat-nav" aria-label="Avsnitt">${nav}</nav>

      <section class="mat-doman" aria-labelledby="om-kan">
        <h2 class="mat-h2" id="om-kan">Vad Floden kan nu</h2>
        <article class="mat-metod">
          <h3 class="mat-h3">Arbetsflödet</h3>
          <ol class="mat-lista om-lista">${steg}</ol>
        </article>
        ${data.amnen.map(amneHtml).join("")}
      </section>

      <section class="mat-doman" aria-labelledby="om-plan">
        <h2 class="mat-h2" id="om-plan">Plan</h2>
        <p class="mat-text flode-dampad">Ur PLAN.md. Inga datum som inte står där.</p>
        <div class="tabell-ruta"><table class="data-tabell om-tabell">
          <thead><tr><th scope="col">Område</th><th scope="col">Vad</th><th scope="col">Status</th><th scope="col">Uppdrag</th></tr></thead>
          <tbody>${plan}</tbody>
        </table></div>
      </section>

      <section class="mat-doman" aria-labelledby="om-jamforelse">
        <h2 class="mat-h2" id="om-jamforelse">Jämförelse med andra verktyg</h2>
        <p class="mat-text flode-dampad">${txt(data.jamforelseIngress)}</p>

        <h3 class="mat-h3" id="om-uppmatt">A. Uppmätt i Floden</h3>
        <p class="mat-text flode-dampad">Floden mot Floden. Varje tal har sin källa i repot.</p>
        <div class="tabell-ruta"><table class="data-tabell om-tabell">
          <thead><tr><th scope="col">Fall</th><th scope="col">Jämförelse</th><th scope="col">Faktor</th><th scope="col">Maskin</th><th scope="col">Källa</th></tr></thead>
          <tbody>${data.uppmatt.map(uppmattRad).join("")}</tbody>
        </table></div>
        <p class="mat-text"><strong>Körtider i Floden.</strong> Median av fem körningar efter en uppvärmning. ${txt(data.kortid.maskin)} Källa: ${lank(data.kortid.kalla)}.</p>
        <div class="tabell-ruta"><table class="data-tabell om-tabell">
          <thead><tr><th scope="col">Ämne</th><th scope="col">Fall</th><th scope="col">Körtid</th></tr></thead>
          <tbody>${kortid}</tbody>
        </table></div>

        <h3 class="mat-h3" id="om-andra">B. Offentliga uppgifter om andra verktyg</h3>
        <p class="mat-text flode-dampad">${txt(data.andra.anm)}</p>
        ${data.andra.rader.map((x) => andraHtml(x, data.andra.hamtad)).join("")}

        <h3 class="mat-h3" id="om-ej-matt">C. ${esc(data.ejMatt.rubrik)}</h3>
        <p class="mat-text om-ej-matt">${txt(data.ejMatt.text)}</p>

        <h3 class="mat-h3" id="om-skillnader">D. Skillnader som går att belägga</h3>
        ${rubrikLista(data.skillnader)}

        <h3 class="mat-h3" id="om-saknas">E. Vad andra verktyg har som Floden saknar</h3>
        ${rubrikLista(data.saknas)}
      </section>
    </div>`;
}

/** Hela sidan: Om Floden och därunder modulerna och kontrollen av beräkningarna. */
export function sidaOmFloden(moduler: ModulInfo[], kontrollfall: OmradeSammanfattning[], data: OmData = OM): string {
  return `${omFlodenHtml(data)}
    <div class="sidbredd innehall om-berakningar" id="om-berakningar">${omBerakningarSektioner(moduler, kontrollfall)}</div>`;
}

/** Kopplar avsnittslänkarna (hopp inom sidan utan att byta adress). */
export function kopplaOm(rot: HTMLElement): void {
  rot.querySelectorAll<HTMLAnchorElement>("a[data-hopp]").forEach((a) =>
    a.addEventListener("click", (h) => {
      h.preventDefault();
      document.getElementById(a.dataset.hopp ?? "")?.scrollIntoView({ block: "start" });
    }),
  );
}
