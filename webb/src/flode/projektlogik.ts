/*
 * Steg 1 · Projekt: ren logik för projektlistan (ingen DOM, så att den går att testa).
 */
import { rollNamn, type KontoLage } from "../konto.ts";

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
  return `<a class="flode-knapp svart liten" id="nytt-projekt" href="${adress}">Nytt projekt</a>`;
}

/** Roll och delning på projektraden (inloggad användare), t.ex. "Din roll: läsare · delat med organisationen". Tom utan uppgifter. */
export function rollRad(p: { roll?: string | null; organisation_synlig?: boolean }): string {
  const delar = [p.roll ? `Din roll: ${rollNamn(p.roll)}` : "", p.organisation_synlig ? "delat med organisationen" : ""].filter(Boolean);
  return delar.join(" · ");
}
