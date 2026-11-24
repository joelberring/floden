/*
 * Adresserna i hash-routern, utan DOM så att de går att testa. Sidor utanför flödet: #/fragor (frågekatalogen) ·
 * #/om · #/matematik · #/forvaltning · #/nycklar · #/administration · #/prov/webgpu · #/arbetsbank/<omrade>.
 * Flödet (#/ och #/p/<projekt>/<steg>) tolkas av flode/adress.ts. Äldre adresser omdirigeras (`omdirigering`).
 */
import { tolkaFlodeAdress } from "./flode/adress.ts";
import { PROJEKT_ID, type SidId } from "./sidor.ts";

export type RuttSida = SidId | "arbetsbank" | "prov";

export interface Rutt {
  sida: RuttSida;
  id: string | null;
  parametrar: URLSearchParams;
}

export function tolkaHash(hash: string): Rutt {
  const utan = hash.replace(/^#/, "");
  const [sokvag = "", fraga = ""] = utan.split("?", 2);
  const delar = sokvag.split("/").filter((d) => d.length > 0);
  const parametrar = new URLSearchParams(fraga);
  const sidor: Record<string, RuttSida> = {
    fragor: "fragor",
    om: "om",
    matematik: "matematik", // Matematik och forskning (kontomenyn)
    forvaltning: "forvaltning",
    nycklar: "nycklar",
    administration: "administration",
    arbetsbank: "arbetsbank",
    prov: "prov", // #/prov/webgpu: provsida för superadmin, ingen länk i menyn
  };
  const sida = delar[0] ? sidor[delar[0]] : undefined;
  if (!sida) {
    return { sida: "start", id: null, parametrar };
  }
  return { sida, id: delar[1] ? decodeURIComponent(delar[1]) : null, parametrar };
}

export const SIDTITLAR: Record<RuttSida, string> = {
  start: "Floden – se hur ett nytt hus påverkar sol och skyfall",
  fragor: "Frågekatalog – Floden",
  om: "Om beräkningarna – Floden",
  matematik: "Matematik och forskning – Floden",
  forvaltning: "Förvaltning – Floden",
  nycklar: "Nycklar – Floden",
  administration: "Administration – Floden",
  arbetsbank: "Arbetsbänk – Floden",
  prov: "Prov – Floden",
};


/**
 * Den gamla kartsidan (`#/karta[/<projekt>][?vy=<vy>]`) finns inte kvar; adressen leder till
 * motsvarande steg i flödet: med `vy` till Resultat (samma vy), annars till Förslag där man ritar.
 * Ger null för alla andra adresser.
 */
export function kartaOmdirigering(hash: string): string | null {
  const utan = hash.replace(/^#/, "");
  const [sokvag = "", fraga = ""] = utan.split("?", 2);
  const delar = sokvag.split("/").filter((d) => d.length > 0);
  if (delar[0] !== "karta") return null;
  let projekt = PROJEKT_ID;
  if (delar[1]) {
    try {
      projekt = decodeURIComponent(delar[1]);
    } catch {
      return "#/";
    }
  }
  const vy = new URLSearchParams(fraga).get("vy");
  const bas = `#/p/${encodeURIComponent(projekt)}`;
  return vy ? `${bas}/resultat?vy=${encodeURIComponent(vy)}` : `${bas}/forslag`;
}

/**
 * Alla äldre adresser som inte längre är egna sidor, och adresser som inte finns, leder till flödet:
 * - `#/karta...`: se `kartaOmdirigering`.
 * - `#/projekt/<id>` (och `#/oversikt/<id>`): projektets första steg i flödet, `#/p/<id>/forslag`
 *   (samma mål som "Öppna projektet" i projektlistan). Utan id: projektlistan `#/`.
 * - `#/fragor/<ref>`: frågekatalogen `#/fragor` (den äldre körsidan för enskilda frågor finns inte kvar).
 * - en okänd adress: projektlistan `#/`.
 * Ger null för adresser som är sidor eller flödessteg.
 */
export function omdirigering(hash: string): string | null {
  const karta = kartaOmdirigering(hash);
  if (karta) return karta;
  const [sokvag = ""] = hash.replace(/^#/, "").split("?", 1);
  const delar = sokvag.split("/").filter((d) => d.length > 0);
  const forsta = delar[0];
  if (forsta === "projekt" || forsta === "oversikt") {
    if (!delar[1]) return "#/";
    try {
      return `#/p/${encodeURIComponent(decodeURIComponent(delar[1]))}/forslag`;
    } catch {
      return "#/";
    }
  }
  if (forsta === "fragor" && delar.length > 1) return "#/fragor";
  if (delar.length === 0 || tolkaFlodeAdress(hash)) return null;
  return tolkaHash(hash).sida === "start" ? "#/" : null;
}
