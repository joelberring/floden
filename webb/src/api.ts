import type {
  HalsaSvar,
  KorBegaran,
  KorSvar,
  OmradenSvar,
  OmradeLagerSvar,
  RegnKatalogSvar,
  RiskBegaran,
  RiskSvar,
} from "./types";

const API_BAS = "/api";

async function lasJson<T>(url: string, init?: RequestInit): Promise<T> {
  const svar = await fetch(url, init);
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
