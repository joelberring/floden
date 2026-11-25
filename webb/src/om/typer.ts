/* Formen på src/om/data.generated.ts (genereras av tools/generera_om.py ur data/om/floden.toml). */
export interface OmLank {
  text: string;
  /** Länk till filen på GitHub; null när sökvägen inte finns. */
  href: string | null;
}

export interface OmMetod {
  id: string;
  namn: string;
  /** Klassningen ur data/mcp/metodstatus.toml: skiss eller rapport. */
  status: string;
  flodeText: string;
}

/** Ett testat eller mätt tal: antingen godkanda av av, eller ett värde med enhet. */
export interface OmTal {
  text?: string;
  namn?: string;
  prefix?: string;
  godkanda?: number;
  av?: number;
  varde?: number;
  enhet?: string;
  decimaler?: number;
  kalla: OmLank;
}

export interface OmAmne {
  id: string;
  namn: string;
  metoder: OmMetod[];
  kan: string[];
  testat: OmTal[];
  nyckeltal: OmTal[];
  begransningar: string[];
}

export interface OmUppmatt {
  id: string;
  amne: string;
  titel: string;
  a_namn?: string;
  a_varde?: number;
  b_namn?: string;
  b_varde?: number;
  enhet?: string;
  decimaler?: number;
  faktorLag: number | null;
  faktorHog: number | null;
  maskin: string;
  anm: string;
  kalla: OmLank;
}

export interface OmKortid {
  id: string;
  amne: string;
  fall: string;
  varde_s: number;
  decimaler: number;
}

export interface OmAndraTal {
  text: string;
  prefix?: string;
  varde: number;
  enhet: string;
  decimaler: number;
}

export interface OmAndra {
  id: string;
  amne: string;
  verktyg: string;
  upphov: string;
  vad: string;
  licens: string;
  pris: string;
  metod: string;
  kortid: string;
  lankar: { text: string; url: string }[];
  tal?: OmAndraTal[];
}

export interface OmRubrik {
  rubrik: string;
  text: string;
  kalla: OmLank;
}

export interface OmData {
  uppdaterad: string;
  ingress: string;
  statusText: string;
  jamforelseIngress: string;
  /** Adressen till docs/OM-FLODEN.md på GitHub. */
  kodbas: string;
  metodAntal: { alla: number; skiss: number; rapport: number };
  steg: { id: string; namn: string; text: string }[];
  amnen: OmAmne[];
  plan: { rubrik: string; text: string; status: string; uppdrag: string }[];
  uppmatt: OmUppmatt[];
  kortid: { kalla: OmLank; maskin: string; rader: OmKortid[] };
  andra: { hamtad: string; anm: string; rader: OmAndra[] };
  ejMatt: { rubrik: string; text: string };
  skillnader: OmRubrik[];
  saknas: OmRubrik[];
}
