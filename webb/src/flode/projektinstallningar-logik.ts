/*
 * Projektinställningar: ren logik och HTML (ingen DOM, så att den går att testa). Servern avgör vem som får vad;
 * klienten döljer bara det som ändå skulle nekas (`far_andra`, `far_andra_delning`, `far_arkivera_radera`).
 */
import { rollNamn } from "../konto.ts";
import { esc } from "../sidor.ts";
import { formatera } from "../tal.ts";
import type { InstallningarMedlem, ProjektInstallningar, RaderaSvar } from "../types.ts";

export const DEMO_PROJEKT_ID = "demokvarteret";

/** Radera-knappen är tillåten först när projektets namn är skrivet exakt (blanksteg runt om räknas inte). */
export function raderaTillaten(namn: string, skrivet: string): boolean {
  return namn.length > 0 && skrivet.trim() === namn;
}

function datum(sek: number): string {
  return new Date(sek * 1000).toISOString().slice(0, 10);
}

/** Vad som togs bort, som en mening. Alla tal går genom tal.ts. */
export function raderadText(b: RaderaSvar["borttaget"]): string {
  const delar: string[] = [];
  const rad = (n: number, ental: string, flertal: string): void => {
    if (n > 0) delar.push(`${formatera(n, 0)} ${n === 1 ? ental : flertal}`);
  };
  rad(b.korningar, "körning", "körningar");
  rad(b.rapporter, "rapport", "rapporter");
  rad(b.bestallningar, "beställning", "beställningar");
  rad(b.roller, "projektroll", "projektroller");
  rad(b.inbjudningar, "inbjudan", "inbjudningar");
  return `Projektet är raderat${delar.length ? `: ${delar.join(", ")} togs bort` : ""}. Geografin och de delade cacherna är orörda.`;
}

function rollVal(roller: string[], vald: string, attr: string): string {
  const val = roller.map((r) => `<option value="${esc(r)}"${r === vald ? " selected" : ""}>${esc(rollNamn(r))}</option>`).join("");
  return `<select class="flode-falt" ${attr}>${val}</select>`;
}

function medlemsRad(d: ProjektInstallningar, m: InstallningarMedlem): string {
  const namn = `${esc(m.namn)}${m.ar_du ? " (du)" : ""}${m.ar_agare ? ` <span class="flode-dampad">ägare</span>` : ""}${m.sparrad ? ` <span class="flode-dampad">spärrad</span>` : ""}`;
  if (!d.far_andra) return `<tr><td>${namn}</td><td>${esc(rollNamn(m.roll))}</td><td></td></tr>`;
  return `<tr><td>${namn}<br /><span class="flode-dampad">${esc(m.epost ?? "")}</span></td>
    <td>${rollVal(d.roller, m.roll, `data-medlem-roll="${esc(m.konto_id)}" aria-label="Roll för ${esc(m.namn)}"`)}</td>
    <td><button type="button" class="flode-knapp liten" data-ta-bort-medlem="${esc(m.konto_id)}">Ta bort</button></td></tr>`;
}

function medlemmarHtml(d: ProjektInstallningar): string {
  const rader = d.medlemmar.length
    ? `<table class="flode-tabell"><thead><tr><th scope="col">Namn</th><th scope="col">Roll</th><th scope="col"><span class="flode-dold">Åtgärd</span></th></tr></thead>
        <tbody>${d.medlemmar.map((m) => medlemsRad(d, m)).join("")}</tbody></table>`
    : `<p class="flode-dampad">Inga har en uttrycklig roll i projektet.</p>`;
  const inbjudna = d.inbjudningar.filter((i) => i.status === "oppen");
  const inbjudningar = d.far_andra
    ? inbjudna.length
      ? `<h3 class="flode-h3">Öppna inbjudningar</h3><ul class="flode-lista-enkel">${inbjudna
          .map(
            (i) =>
              `<li>${esc(i.epost)} · ${esc(rollNamn(i.roll))} · giltig till ${esc(datum(i.giltig_till))} <button type="button" class="flode-knapp liten" data-aterkalla-inbjudan="${esc(i.id)}">Återkalla</button></li>`,
          )
          .join("")}</ul>`
      : ""
    : "";
  const bjud = d.far_andra
    ? `<form data-bjud-in class="flode-rad-form">
        <label>E-postadress <input class="flode-falt" type="email" name="epost" required autocomplete="off" /></label>
        <label>Roll ${rollVal(d.roller, "lasare", 'name="roll"')}</label>
        <button type="submit" class="flode-knapp svart liten">Bjud in</button>
      </form>
      <p class="flode-dampad flode-liten">Läsare får läsa, utredare får även rita och köra, ägare ändrar dessutom inställningarna. En inbjudan tas emot när personen loggar in med den adressen.</p>`
    : `<p class="flode-dampad flode-liten">Bara projektets ägare och organisationens administratörer ändrar medlemmar.</p>`;
  return `<section class="flode-ruta" aria-labelledby="pi-medlemmar"><h2 id="pi-medlemmar" class="flode-h2">Medlemmar</h2>${rader}${inbjudningar}${bjud}</section>`;
}

/** Hela sidan Projektinställningar. `meddelande` visas överst (bekräftelse eller fel). */
export function installningarHtml(d: ProjektInstallningar, meddelande = "", fel = false): string {
  const p = d.projekt;
  const namn = d.far_andra
    ? `<form data-byt-namn class="flode-rad-form"><label>Projektets namn <input class="flode-falt" type="text" name="namn" value="${esc(p.namn)}" maxlength="120" required autocomplete="off" /></label>
        <button type="submit" class="flode-knapp liten">Byt namn</button></form>`
    : `<p><strong>${esc(p.namn)}</strong></p>`;
  const delning = `<p><label><input type="checkbox" data-delning${p.organisation_synlig ? " checked" : ""}${d.far_andra_delning ? "" : " disabled"} />
      Synligt för organisationen ${esc(p.organisation)}</label><br />
      <span class="flode-dampad flode-liten">${p.organisation_synlig ? "Organisationens ägare och administratörer utreder i projektet, ledare läser." : "Privat: bara de som har en roll i projektet ser det."}${d.far_andra_delning ? "" : " Bara projektets utredare, ägare och skaparen ändrar detta."}</span></p>`;
  const arkiv = d.far_arkivera_radera
    ? `<section class="flode-ruta" aria-labelledby="pi-arkiv"><h2 id="pi-arkiv" class="flode-h2">Arkivera och radera</h2>
        <p>${p.arkiverad ? "Projektet är <strong>arkiverat</strong>: det syns inte i projektlistan men finns kvar och kan återställas." : "Ett arkiverat projekt döljs i projektlistan men finns kvar och kan återställas."}</p>
        <p><button type="button" class="flode-knapp liten" data-arkiv="${p.arkiverad ? "nej" : "ja"}">${p.arkiverad ? "Återställ projektet" : "Arkivera projektet"}</button></p>
        <h3 class="flode-h3">Radera permanent</h3>
        <p>Raderingen tar bort projektets mapp med ritade förslag och frågor, dess körningar, rapporter och beställningar, projektrollerna och inbjudningarna. <strong>Det går inte att ångra.</strong> Geografin och de delade cacherna rörs inte.</p>
        <form data-radera class="flode-rad-form"><label>Skriv projektets namn (<strong>${esc(p.namn)}</strong>) för att bekräfta <input class="flode-falt" type="text" name="bekrafta" autocomplete="off" /></label>
          <button type="submit" class="flode-knapp liten" disabled>Radera projektet</button></form>
      </section>`
    : `<section class="flode-ruta"><h2 class="flode-h2">Arkivera och radera</h2><p class="flode-dampad">Bara projektets ägare och superadmin arkiverar eller raderar ett projekt.</p></section>`;
  const roll = d.din_roll ? `Din roll: ${esc(rollNamn(d.din_roll))}${d.ar_agare ? " (ägare)" : ""}${d.din_roll_kalla === "organisation" ? ", genom organisationsrollen" : ""}.` : "Du har ingen roll i projektet.";
  return `<div class="flode-sida-smal">
    <h1 class="flode-h">Projektinställningar</h1>
    <p class="flode-dampad">${esc(p.organisation)} · ${roll}${d.far_andra ? "" : " Du kan se inställningarna men inte ändra dem."}</p>
    <div data-meddelande class="${fel ? "flode-fel" : "flode-dampad"}" role="${fel ? "alert" : "status"}"${meddelande ? "" : " hidden"}>${esc(meddelande)}</div>
    <section class="flode-ruta" aria-labelledby="pi-namn"><h2 id="pi-namn" class="flode-h2">Namn och synlighet</h2>${namn}${delning}</section>
    ${medlemmarHtml(d)}
    ${arkiv}
    <p><a href="#/p/${encodeURIComponent(p.id)}/forslag">Tillbaka till projektet</a> · <a href="#/">Projektlistan</a></p>
  </div>`;
}

/** Länken under projektlistan: "Visa arkiverade" och tillbaka. */
export function arkiveradeLankHtml(visarArkiverade: boolean, adress: string): string {
  return visarArkiverade
    ? `<p class="flode-liten"><a href="${adress}" data-arkiverade="nej">Visa aktiva projekt</a></p>`
    : `<p class="flode-liten"><a href="${adress}?arkiverade=1" data-arkiverade="ja">Visa arkiverade</a></p>`;
}
