/*
 * Adresserna i hash-routern, utan DOM så att de går att testa: #/ · #/projekt/<id> · #/karta/<id> ·
 * #/fragor · #/om · #/matematik · #/forvaltning · #/nycklar · #/administration · #/arbetsbank/<omrade>.
 */
import type { SidId } from "./sidor";

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
    karta: "karta",
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
  karta: "Karta – Floden",
  fragor: "Utredningsfrågor – Floden",
  om: "Om beräkningarna – Floden",
  matematik: "Matematik och forskning – Floden",
  forvaltning: "Förvaltning – Floden",
  nycklar: "Nycklar – Floden",
  administration: "Administration – Floden",
  arbetsbank: "Arbetsbänk – Floden",
  prov: "Prov – Floden",
};

