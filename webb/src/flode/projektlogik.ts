/*
 * Steg 1 · Projekt: ren logik för projektlistan (ingen DOM, så att den går att testa).
 */
import { rollNamn, type KontoLage } from "../konto.ts";
import { esc } from "../sidor.ts";

export interface ProjektRadMeta {
  roll?: string | null;
  organisation_synlig?: boolean;
  organisation_namn?: string | null;
  organisation?: string | null;
}

export interface ProjektRadIndata extends ProjektRadMeta {
  id: string;
  namn: string;
  undertitel: string;
  paahittat: boolean;
  statusText: string;
  href: string;
  vald: boolean;
  miniatyr: string | null;
}

/** Vad ett klick på projektraden gör: första klicket väljer, klicket på det valda öppnar projektet. */
export function radHandling(vald: boolean): string {
  return vald ? "Öppna projektet →" : "Visa på kartan";
}

/**
 * Knappen "Nytt projekt". Servern nekar att skapa projekt utan inloggning (lokalt läge), så då visas en förklaring i
 * stället för en knapp som bara leder till ett felmeddelande.
 */
export function nyttProjektHtml(lage: KontoLage["lage"], adress: string): string {
  if (lage === "av") {
    return `<p class="flode-liten flode-dampad" id="nytt-projekt-not">Nya projekt skapas på servern med inloggning. Lokalt: <code>floden projekt skapa</code>.</p>`;
  }
  return `<a class="flode-knapp svart liten" id="nytt-projekt" href="${esc(adress)}">Nytt projekt</a>`;
}

/** Hämtar trimmat organisationsnamn ur projektposten (`organisation_namn` eller `organisation`), annars null. */
export function projektOrganisationNamn(p: Pick<ProjektRadMeta, "organisation_namn" | "organisation">): string | null {
  const namn = (p.organisation_namn ?? p.organisation ?? "").trim();
  return namn || null;
}

/** Delningstillstånd i klartext när det anges på projektposten. */
export function delningText(synlig?: boolean): string {
  if (synlig === true) return "delat med organisationen";
  if (synlig === false) return "privat";
  return "";
}

/**
 * Organisation, roll och delning på projektraden, t.ex.
 * "Stockholms stad · Din roll: utredare · delat med organisationen". Tom utan uppgifter.
 */
export function rollRad(p: ProjektRadMeta): string {
  const org = projektOrganisationNamn(p);
  const delar = [
    org ?? "",
    p.roll ? `Din roll: ${rollNamn(p.roll)}` : "",
    delningText(p.organisation_synlig),
  ].filter(Boolean);
  return delar.join(" · ");
}

/** Strukturerad rad med organisation, användarens roll och delningstillstånd för ett projektkort. */
export function projektMetaHtml(p: ProjektRadMeta): string {
  const org = projektOrganisationNamn(p);
  const ovrigt = [p.roll ? `Din roll: ${rollNamn(p.roll)}` : "", delningText(p.organisation_synlig)].filter(Boolean);
  if (!org && ovrigt.length === 0) return "";
  const delar: string[] = [];
  if (org) delar.push(`<span class="flode-projekt-org">${esc(org)}</span>`);
  if (ovrigt.length > 0) delar.push(`<span class="flode-dampad">${esc(ovrigt.join(" · "))}</span>`);
  return `<span class="flode-projekt-meta" data-projekt-meta>${delar.join(`<span class="flode-dampad" aria-hidden="true"> · </span>`)}</span>`;
}

/** Renderar en rad/kort i projektlistan (Steg 1). */
export function projektRadHtml(r: ProjektRadIndata): string {
  const meta = projektMetaHtml(r);
  return `<a class="flode-projekt${r.vald ? " vald" : ""}" href="${esc(r.href)}" data-projekt="${esc(r.id)}"${r.vald ? ' aria-current="true"' : ""}>
    ${r.miniatyr ? `<img src="${esc(r.miniatyr)}" alt="" />` : `<span class="flode-miniatyr-tom" aria-hidden="true"></span>`}
    <span class="flode-projekt-text">
      <span class="flode-projekt-namn">${esc(r.namn)}</span>
      <span class="flode-dampad">${esc(r.paahittat ? "Påhittat" : "Verkligt")} · ${esc(r.undertitel)}</span>
      <span class="flode-dampad">${esc(r.statusText)}</span>
      ${meta}
      <span class="flode-projekt-handling">${esc(radHandling(r.vald))}</span>
    </span>
  </a>`;
}

/**
 * Tomtext i projektlistan (U5g).
 * I vanlig webbdrift / inloggat läge visas vägledning till "Nytt projekt" (aldrig utvecklarkommandon);
 * `floden demo`-tipset visas enbart i lokalt ej-inloggat demoläge (`lage === "av"`).
 */
export function tomProjektlistaHtml(
  lage: KontoLage["lage"],
  arkiverade = false,
  nyttAdress = "#/nytt",
): string {
  if (arkiverade) {
    return `<p class="flode-dampad">Det finns inga arkiverade projekt.</p>`;
  }
  if (lage === "av") {
    return `<div class="flode-tomlista" data-tom-lage="av">
      <p class="flode-dampad">Det finns inga projekt än i lokalt demoläge. Bygg demokvarteret med <code>floden demo</code> och ladda sedan om sidan.</p>
    </div>`;
  }
  if (lage === "ut") {
    return `<div class="flode-tomlista" data-tom-lage="ut">
      <p class="flode-dampad">Logga in för att se dina projekt.</p>
    </div>`;
  }
  return `<div class="flode-tomlista" data-tom-lage="${esc(lage)}">
    <p class="flode-tomlista-rubrik">Du har inga projekt än.</p>
    <p class="flode-dampad">Skapa ditt första projekt via <strong>Nytt projekt</strong>, eller be din organisations administratör att bjuda in dig till ett befintligt projekt.</p>
    <p class="flode-tomlista-handling"><a class="flode-knapp svart liten" href="${esc(nyttAdress)}">Nytt projekt</a></p>
  </div>`;
}
