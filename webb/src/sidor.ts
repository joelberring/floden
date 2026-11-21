/*
 * Sidorna utanför kartan: startsida, översikt, projekt och förvaltning.
 * Varje sida är en funktion som ger HTML ur data från API:t.
 *
 * Projekt, förvaltningar, medlemmar och körhistorik har ingen backend än (kontoservern kommer
 * senare). Där står platshållare i hakparentes: inga påhittade tal och inga namn
 * (AGENTS.md: tal kommer bara ur beräkningar, inga personuppgifter).
 */
import type { ModulInfo, OmradeSammanfattning } from "./types";

export function esc(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

export type SidId = "start" | "oversikt" | "projekt" | "karta" | "forvaltning";

const ORGANISATION = "[Organisation] / [Förvaltning]";

export function omradeNamn(o: OmradeSammanfattning): string {
  return `${o.kod} – ${o.namn}`;
}

function etikett(text: string, tagg = "h2"): string {
  return `<${tagg} class="etikett">${esc(text)}</${tagg}>`;
}

function statusText(m: ModulInfo): string {
  return m.status === "i_drift" ? "I drift" : "Kommande";
}

/* ---------- Sidhuvud ---------- */

export function sidhuvudStart(): string {
  return `
    <div class="huvud-inre">
      <a class="ordmarke" href="#/">Floden</a>
      <nav class="huvud-nav" aria-label="Startsidan">
        <a href="#/?till=moduler" data-ankare="moduler">Moduler</a>
        <a href="#/?till=principer" data-ankare="principer">Principer</a>
        <a class="knapp knapp-svart" href="#/oversikt">Logga in</a>
      </nav>
    </div>`;
}

export function sidhuvudApp(aktiv: SidId, omradeId: string | null): string {
  const lank = (id: SidId, text: string, href: string): string =>
    `<a href="${href}"${aktiv === id ? ' aria-current="page"' : ""}>${esc(text)}</a>`;
  const omr = omradeId ? `/${encodeURIComponent(omradeId)}` : "";
  return `
    <div class="huvud-inre">
      <div class="huvud-vanster">
        <a class="ordmarke" href="#/">Floden</a>
        <!-- TODO(kontoservern): byt organisation/förvaltning när kontot har flera medlemskap. -->
        <button type="button" class="org-vaxlare" disabled title="Kommer med kontoservern">${esc(ORGANISATION)}</button>
      </div>
      <nav class="huvud-nav" aria-label="Huvudmeny">
        ${lank("oversikt", "Översikt", "#/oversikt")}
        ${lank("projekt", "Projekt", `#/projekt${omr}`)}
        ${lank("karta", "Karta", `#/karta${omr}`)}
        ${lank("forvaltning", "Förvaltning", "#/forvaltning")}
        <!-- TODO(kontoservern): kontomeny (namn, utloggning) när inloggning finns. -->
        <button type="button" class="konto-knapp" aria-label="Ditt konto (kommer med kontoservern)" disabled>[I]</button>
      </nav>
    </div>`;
}

/* ---------- Startsida ---------- */

export function sidaStart(moduler: ModulInfo[]): string {
  const modulKort = moduler
    .map(
      (m) => `
      <article class="modul-kort">
        <span class="modul-nr">${esc(m.nr)}</span>
        <h3>${esc(m.namn)}</h3>
        <p>${esc(m.beskrivning)}</p>
        <span class="modul-status ${m.status === "i_drift" ? "i-drift" : ""}">${statusText(m)}</span>
      </article>`,
    )
    .join("");
  return `
    <section class="hjalte sidbredd">
      <h1 class="hjalte-rubrik">Floden av data och utredningar – på ett ställe.</h1>
      <div class="hjalte-text">
        <p class="ingress">Skyfall, luft, buller och dagsljus räknade på samma grund, för samma projekt.</p>
        <div class="knapp-par">
          <!-- TODO(kontoservern): starta SSO-inloggning mot kommunens inloggningstjänst. -->
          <a class="knapp knapp-svart knapp-stor" href="#/oversikt">Logga in med kommunens konto</a>
          <!-- TODO(kontoservern): lösenord eller inbjudan för konsulter. -->
          <a class="knapp knapp-stor" href="#/oversikt">Annan inloggning</a>
        </div>
      </div>
    </section>

    <section id="moduler" class="linje-topp">
      <div class="sidbredd">
        <div class="sektionsrad">
          ${etikett("Moduler")}
          <span class="dampad">Samma markmodell · samma projekt · samma rapportkontroll</span>
        </div>
        <div class="modul-rutnat">${modulKort || '<p class="dampad">[Modullistan kunde inte hämtas]</p>'}</div>
      </div>
    </section>

    <section id="principer" class="sidbredd principer">
      <div class="princip">
        ${etikett("Beräknat, inte gissat", "h3")}
        <p>Varje tal kommer från en körning med manifest.</p>
      </div>
      <div class="princip">
        ${etikett("Öppen kärna", "h3")}
        <p>GPL v3. Samma indata ger samma resultat.</p>
      </div>
      <div class="princip">
        ${etikett("Er data stannar hos er", "h3")}
        <p>Interna kartor och ledningar körs i kommunens egen miljö.</p>
      </div>
    </section>

    <footer class="sidfot">
      <span>Floden</span>
      <span class="sidfot-lankar">
        <a href="https://github.com/joelberring/floden">Källkod</a>
        <a href="#/?till=principer" data-ankare="principer">Personuppgifter</a>
      </span>
    </footer>`;
}

/* ---------- Översikt ---------- */

export function sidaOversikt(moduler: ModulInfo[], omraden: OmradeSammanfattning[]): string {
  const iDrift = moduler.filter((m) => m.status === "i_drift");
  const omradeRader = omraden
    .map(
      (o) => `
      <a class="lista-rad" href="#/projekt/${encodeURIComponent(o.omrade_id)}">
        <span class="lista-titel">${esc(omradeNamn(o))}</span>
        <span class="dampad">${esc(o.grupp)} · ${esc(o.beskrivning)}</span>
        <span class="modul-lista">${iDrift
          .map((m) => `<span style="color:${esc(m.kartfarg)}">${esc(m.namn)}</span>`)
          .join("")}</span>
        <span aria-hidden="true">→</span>
      </a>`,
    )
    .join("");
  return `
    <div class="statusrad" role="status">
      <div class="sidbredd statusrad-inre">
        <!-- TODO(kontoservern): visas när inloggningen har kopplat kontot till en förvaltning. -->
        <span>Du kopplas till rätt förvaltning när kommunens inloggning är på plats. [Förvaltning]</span>
        <span class="knapp-par">
          <button type="button" class="knapp knapp-svart knapp-liten" disabled>Stämmer</button>
          <button type="button" class="textknapp" disabled>Byt förvaltning</button>
        </span>
      </div>
    </div>

    <div class="sidbredd innehall">
      <div class="rubrikrad">
        <h1 class="sidrubrik">God morgon, [förnamn].</h1>
        <div class="knapp-par">
          <button type="button" class="knapp knapp-svart" disabled title="Kommer med projektlagret">Nytt projekt</button>
          <a class="knapp" href="#/karta">Öppna kartan</a>
        </div>
      </div>

      <section>
        <div class="sektionsrad linje-under-stark">
          ${etikett("Områden")}
          <span class="dampad">Projekt per förvaltning kommer med kontoservern. Tills dess: områdena i Floden.</span>
        </div>
        ${omradeRader || '<p class="dampad tom">[Inga områden hittades – starta API:t]</p>'}
      </section>

      <div class="tvadelad">
        <section class="huvuddel">
          <div class="sektionsrad linje-under-stark">${etikett("Senaste körningar")}</div>
          <p class="dampad tom">[Körhistorik sparas när projektlagret finns. Varje körning har sitt manifest, run.json.]</p>
        </section>
        <aside class="biddel">
          <section class="sidoblock">
            ${etikett("Beräkningsservern")}
            <p>[Läge] – startar vid behov</p>
            <p class="dampad">I kö [antal] · månadens budget [belopp]</p>
          </section>
          <section class="sidoblock">
            ${etikett("Moduler")}
            <ul class="enkel-lista">
              ${moduler.map((m) => `<li>${esc(m.namn)} – ${statusText(m).toLowerCase()}</li>`).join("")}
            </ul>
          </section>
        </aside>
      </div>
    </div>`;
}

/* ---------- Projekt ---------- */

export function sidaProjekt(
  moduler: ModulInfo[],
  omrade: OmradeSammanfattning | undefined,
  valdModul: string,
): string {
  const id = omrade ? encodeURIComponent(omrade.omrade_id) : "";
  const vald = moduler.find((m) => m.modul_id === valdModul) ?? moduler[0];
  const flikar = moduler
    .map((m) => {
      const pa = vald !== undefined && m.modul_id === vald.modul_id;
      return `
      <a class="modul-flik${pa ? " vald" : ""}" href="#/projekt/${id}?modul=${encodeURIComponent(m.modul_id)}"${pa ? ' aria-current="true"' : ""}>
        <span class="modul-flik-status">${statusText(m)}</span>
        <span class="modul-flik-namn">${esc(m.namn)}</span>
      </a>`;
    })
    .join("");
  const matt = [
    "Max djup vid entréer",
    "Entréer över gränsdjup",
    "Ändring hos grannar",
    "Behövlig fördröjning",
    "Spann P10–P90",
  ];
  const platshallare = ["[– m]", "[antal]", "[± m]", "[– m³]", "[– … – m]"];
  const tabell = `
    <div class="tabell-ruta">
      <table class="jamfor-tabell">
        <thead><tr><th scope="col"><span class="visuellt-dold">Mått</span></th><th scope="col">Före</th><th scope="col">Alt A</th><th scope="col">Alt B</th></tr></thead>
        <tbody>
          ${matt
            .map(
              (namn, i) =>
                `<tr><th scope="row">${esc(namn)}</th><td>${i === 2 || i === 3 ? "–" : platshallare[i]}</td><td>${platshallare[i]}</td><td>${platshallare[i]}</td></tr>`,
            )
            .join("")}
        </tbody>
      </table>
    </div>`;
  const vattenDel = `
    <section>
      <div class="sektionsrad linje-under-stark">
        ${etikett("Före och efter")}
        <span class="dampad">Kör alternativen på kartan; talen hämtas ur körningarnas manifest.</span>
      </div>
      ${tabell}
      <p class="dampad liten-text">Hakparentes = plats för resultat. Alternativ sparas när projektlagret finns.</p>
    </section>`;
  const annanDel = vald
    ? `
    <section class="tom-modul">
      <h2 class="sektionsrubrik">${esc(vald.namn)} – ingen utredning än</h2>
      <p>Modulen använder projektets markmodell och byggnader. ${vald.status === "i_drift" ? "" : "Den kommer i en senare version."}</p>
      <button type="button" class="knapp" disabled>Starta utredning</button>
    </section>`
    : "";
  const rapporter = ["Skyfallsöversikt", "Detaljplan (före/efter)", "Räddningstjänst"];
  return `
    <div class="sidbredd innehall">
      <nav class="brodsmulor" aria-label="Brödsmulor">
        <a href="#/forvaltning">[Förvaltning]</a><span aria-hidden="true">/</span><span>${omrade ? esc(omradeNamn(omrade)) : "[Projekt]"}</span>
      </nav>
      <div class="rubrikrad">
        <div>
          <h1 class="sidrubrik stor">${omrade ? esc(omradeNamn(omrade)) : "[Projekt]"}</h1>
          <p class="dampad">${omrade ? `${esc(omrade.grupp)} · ${omrade.bredd_px} × ${omrade.hojd_px} celler à ${omrade.upplosning_m} m` : "[Område]"}</p>
        </div>
        <div class="knapp-par">
          <a class="knapp knapp-svart" href="#/karta/${id}">Öppna kartan</a>
          <a class="knapp" href="#/karta/${id}">Ny körning</a>
        </div>
      </div>
      <nav class="modul-flikar" aria-label="Utredningar i projektet">${flikar}</nav>

      <div class="tvadelad">
        <div class="huvuddel">
          ${vald && vald.modul_id === "vatten" ? vattenDel : annanDel}
          <section>
            <div class="sektionsrad linje-under-stark">${etikett("Rapporter")}</div>
            ${rapporter
              .map(
                (r) => `<div class="lista-rad enkel"><span>${esc(r)}</span><a href="#/karta/${id}">Skapa på kartan</a></div>`,
              )
              .join("")}
            <p class="dampad liten-text">Talkontrollen stoppar en rapport där ett tal saknar källa i körningen.</p>
          </section>
        </div>
        <aside class="biddel">
          <section class="sidoblock">
            <div class="sektionsrad">${etikett("Medlemmar")}<button type="button" class="textknapp" disabled>Bjud in</button></div>
            <p class="dampad">[Medlemmar och roller kommer med kontoservern]</p>
          </section>
          <section class="sidoblock">
            ${etikett("Underlag")}
            <ul class="enkel-lista">
              <li>Laser – Lantmäteriet (LAS/LAZ)</li>
              <li>Baskarta – DXF/DWG, lagerprofil</li>
              <li>Belagd yta – laserintensitet, kandidater</li>
              <li>Jordarter – SGU</li>
            </ul>
            <p class="ram-text">Gränsdjup för entréer är inte fastställt med räddningstjänsten.</p>
          </section>
        </aside>
      </div>
    </div>`;
}

/* ---------- Förvaltning ---------- */

export function sidaForvaltning(): string {
  const regler: [string, string, string, string, string][] = [
    ["1", "Inloggningens tenant", "[tenant-id]", "[Organisation]", "Automatisk"],
    ["2", "Grupp i inloggningstjänsten", "[grupp]", "[Förvaltning]", "Automatisk"],
    ["3", "Attribut department", "[avdelning]", "[Förvaltning]", "Bekräftas"],
    ["4", "E-postdomän", "[domän]", "[Organisation]", "Bara organisation"],
  ];
  return `
    <div class="sidbredd innehall">
      <div class="rubrikrad">
        <div>
          <p class="dampad">[Organisation]</p>
          <h1 class="sidrubrik stor">[Förvaltning]</h1>
        </div>
        <dl class="nyckeltal">
          <div><dt>Medlemmar</dt><dd>[antal]</dd></div>
          <div><dt>Projekt</dt><dd>[antal]</dd></div>
          <div><dt>Att bekräfta</dt><dd>[antal]</dd></div>
        </dl>
      </div>

      <div class="tvadelad">
        <div class="huvuddel">
          <section>
            <div class="sektionsrad linje-under-stark">
              ${etikett("Att bekräfta")}
              <span class="dampad">Föreslaget av inloggningen, inte säkert nog att koppla direkt</span>
            </div>
            <p class="dampad tom">[Förslag visas här när kommunens inloggning är kopplad]</p>
          </section>

          <section>
            <div class="sektionsrad linje-under-stark">
              ${etikett("Kopplingsregler")}
              <button type="button" class="textknapp" disabled>Ny regel</button>
            </div>
            <div class="tabell-ruta">
              <table class="data-tabell">
                <thead><tr><th scope="col">Ordning</th><th scope="col">Signal från inloggningen</th><th scope="col">Värde</th><th scope="col">Ger</th><th scope="col">Koppling</th></tr></thead>
                <tbody>
                  ${regler
                    .map(
                      ([nr, sig, v, ger, k]) =>
                        `<tr><td class="tal">${nr}</td><td>${esc(sig)}</td><td class="mono">${esc(v)}</td><td>${esc(ger)}</td><td class="versal">${esc(k)}</td></tr>`,
                    )
                    .join("")}
                </tbody>
              </table>
            </div>
            <p class="dampad liten-text">Förvaltning ger aldrig tillgång till projektdata. Den ges per projekt.</p>
          </section>

          <section>
            <div class="sektionsrad linje-under-stark">${etikett("Medlemmar")}</div>
            <p class="dampad tom">[Medlemmar, roller och hur de kopplades kommer med kontoservern]</p>
          </section>
        </div>

        <aside class="biddel">
          <section class="sidoblock">
            ${etikett("Inloggning")}
            <ul class="enkel-lista rader">
              <li>[Inloggningstjänst]<span class="dampad">Tenant [id] · [verifierad]</span></li>
              <li>E-postdomän [domän]<span class="dampad">Ger bara organisationen</span></li>
              <li>Konsulter<span class="dampad">Inbjudan per projekt</span></li>
            </ul>
          </section>
          <section class="sidoblock">
            ${etikett("Organisationsadmin")}
            <p>[Namn]</p>
            <p class="dampad">Äger reglerna och bekräftar förslag.</p>
          </section>
          <section class="sidoblock ram">
            ${etikett("Data som stannar i kommunen")}
            <p>Baskarta, tät laser och ledningsdata körs i kommunens egen instans.</p>
          </section>
        </aside>
      </div>
    </div>`;
}
