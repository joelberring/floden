/*
 * Adresserna i hash-routern, utan DOM så att de går att testa: #/ · #/projekt/<id> · #/fragor · #/om · #/matematik · #/forvaltning · #/nycklar · #/administration · #/arbetsbank/<omrade>.
 */
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
    projekt: "projekt",
    oversikt: "projekt", // den gamla översikten är sammanslagen med projektet
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
  projekt: "Projekt – Floden",
  fragor: "Utredningsfrågor – Floden",
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
