import type {
  HalsaSvar,
  KorBegaran,
  KorSvar,
  ModulerSvar,
  OmradenSvar,
  ProjektListaSvar,
  ProjektSvar,
  OmradeLagerSvar,
  RapportBegaran,
  RapportSvar,
  RegnKatalogSvar,
  RiskBegaran,
  RiskSvar,
  RuttBegaran,
  RuttSvar,
} from "./types";
import { csrfHuvud, settKontoLage } from "./konto";
import type {
  AvlasningSvar,
  DemoSvar,
  FragaPost,
  KorningLista,
  FragorSvar,
  KorningSvar,
  PlanSvar,
  ResultatSvar,
  TabellForhandsvisning,
} from "./fragor-typer";

const API_BAS = "/api";

export class OinloggadFel extends Error {
  constructor() {
    super("Inloggning krävs.");
    this.name = "OinloggadFel";
  }
}

export async function lasJson<T>(url: string, init?: RequestInit): Promise<T> {
  // Alla POST bär CSRF-huvudet (docs/KONTO.md avsnitt 8). Utan inloggning finns ingen kaka
  // och då skickas inget huvud.
  const metod = (init?.method ?? "GET").toUpperCase();
  const huvuden: Record<string, string> = {
    ...((init?.headers as Record<string, string> | undefined) ?? {}),
    ...(metod === "GET" || metod === "HEAD" ? {} : csrfHuvud(document.cookie)),
  };
  const svar = await fetch(url, { ...init, headers: huvuden });
  if (svar.status === 401) {
    settKontoLage({ lage: "ut" });
    throw new OinloggadFel();
  }
  const text = await svar.text();
  let data: unknown;
  try {
    data = JSON.parse(text);
  } catch {
    throw new Error(`Ogiltigt JSON-svar från ${url} (HTTP ${svar.status})`);
  }

  if (!svar.ok) {
    const felObj = data as { fel?: string };
    throw new Error(felObj.fel ?? `API-fel HTTP ${svar.status}`);
  }
  return data as T;
}

export function hamtaHalsa(): Promise<HalsaSvar> {
  return lasJson<HalsaSvar>(`${API_BAS}/halsa`);
}

export function hamtaOmraden(): Promise<OmradenSvar> {
  return lasJson<OmradenSvar>(`${API_BAS}/omraden`);
}

export function hamtaOmradeLager(omradeId: string): Promise<OmradeLagerSvar> {
  return lasJson<OmradeLagerSvar>(
    `${API_BAS}/omraden/${encodeURIComponent(omradeId)}/lager`,
  );
}

export function hamtaRegnKatalog(): Promise<RegnKatalogSvar> {
  return lasJson<RegnKatalogSvar>(`${API_BAS}/regn`);
}

export function korSimulering(begaran: KorBegaran): Promise<KorSvar> {
  return lasJson<KorSvar>(`${API_BAS}/kor`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(begaran),
  });
}

export function beraknaRisk(begaran: RiskBegaran): Promise<RiskSvar> {
  return lasJson<RiskSvar>(`${API_BAS}/risk`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(begaran),
  });
}

export function skapaRapport(begaran: RapportBegaran): Promise<RapportSvar> {
  return lasJson<RapportSvar>(`${API_BAS}/rapport`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(begaran),
  });
}

export function beraknaRutt(begaran: RuttBegaran): Promise<RuttSvar> {
  return lasJson<RuttSvar>(`${API_BAS}/rutt`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(begaran),
  });
}

export function hamtaModuler(): Promise<ModulerSvar> {
  return lasJson<ModulerSvar>(`${API_BAS}/moduler`);
}

export function hamtaProjektLista(): Promise<ProjektListaSvar> {
  return lasJson<ProjektListaSvar>(`${API_BAS}/projekt`);
}

export function hamtaProjekt(id: string): Promise<ProjektSvar> {
  return lasJson<ProjektSvar>(`${API_BAS}/projekt/${encodeURIComponent(id)}`);
}

/* ---------- Utredningsfrågor (K20) ---------- */

export function hamtaFragor(): Promise<FragorSvar> {
  return lasJson<FragorSvar>(`${API_BAS}/fragor`);
}

export function hamtaFraga(ref: string): Promise<FragaPost> {
  return lasJson<FragaPost>(`${API_BAS}/fragor/${encodeURIComponent(ref)}`);
}

export function planeraFraga(ref: string): Promise<PlanSvar> {
  return lasJson<PlanSvar>(`${API_BAS}/fragor/${encodeURIComponent(ref)}/plan`, {
    method: "POST",
  });
}

export function startaKorning(ref: string): Promise<KorningSvar> {
  return lasJson<KorningSvar>(`${API_BAS}/fragor/${encodeURIComponent(ref)}/kor`, {
    method: "POST",
  });
}

export function hamtaKorning(korningId: string): Promise<KorningSvar> {
  return lasJson<KorningSvar>(`${API_BAS}/korningar/${encodeURIComponent(korningId)}`);
}

export function hamtaResultat(korningId: string): Promise<ResultatSvar> {
  return lasJson<ResultatSvar>(`${API_BAS}/korningar/${encodeURIComponent(korningId)}/resultat`);
}

export function hamtaTabellForhandsvisning(url: string): Promise<TabellForhandsvisning> {
  return lasJson<TabellForhandsvisning>(url);
}

export function hamtaKorningar(): Promise<KorningLista> {
  return lasJson<KorningLista>(`${API_BAS}/korningar`);
}

export function hamtaDemo(): Promise<DemoSvar> {
  return lasJson<DemoSvar>(`${API_BAS}/demo`);
}

export function hamtaAvlasning(
  korningId: string,
  lagerId: string,
  e: number,
  n: number,
): Promise<AvlasningSvar> {
  const q = `lager=${encodeURIComponent(lagerId)}&e=${e.toFixed(2)}&n=${n.toFixed(2)}`;
  return lasJson<AvlasningSvar>(`${API_BAS}/korningar/${encodeURIComponent(korningId)}/avlasning?${q}`);
}
