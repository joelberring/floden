import type {
  AdminProjekt,
  AdminOrganisation,
  AdminOrganisationDetalj,
  AdminProjektDetalj,
  HalsaSvar,
  KorBegaran,
  KorSvar,
  ModulerSvar,
  NyckelStatus,
  McpTokenSvar,
  NyMcpToken,
  OauthSvar,
  NycklarSvar,
  OmradenSvar,
  ProjektInstallningar,
  ProjektListaSvar,
  RaderaSvar,
  ProjektSvar,
  OmradeLagerSvar,
  RapportBegaran,
  GeografierSvar,
  RapportAmne,
  RapportBestallningBegaran,
  RapportVal,
  RapportJobb,
  SkapaProjektBegaran,
  SkapaProjektSvar,
  RapportSvar,
  RegnKatalogSvar,
  RiskBegaran,
  RiskSvar,
  RuttBegaran,
  RuttSvar,
} from "./types";
import type { MarkhojdSvar } from "./flode/markhojd.ts";
import { csrfHuvud, settKontoLage } from "./konto";
import { ApiFel, OinloggadFel } from "./apifel";

export { ApiFel, OinloggadFel };
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

export async function lasJson<T>(url: string, init?: RequestInit): Promise<T> {
  // Alla POST bär CSRF-huvudet (docs/KONTO.md avsnitt 8). Utan inloggning finns ingen kaka
  // och då skickas inget huvud.
  const metod = (init?.method ?? "GET").toUpperCase();
  const huvuden: Record<string, string> = {
    ...((init?.headers as Record<string, string> | undefined) ?? {}),
    ...(metod === "GET" || metod === "HEAD" ? {} : csrfHuvud(document.cookie)),
  };
  let svar: Response;
  try {
    svar = await fetch(url, { ...init, headers: huvuden });
  } catch (e) {
    throw new ApiFel(e instanceof Error ? e.message : String(e), 0);
  }
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
    throw new ApiFel(felObj.fel ?? `API-fel HTTP ${svar.status}`, svar.status);
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

export function hamtaProjektLista(arkiverade = false): Promise<ProjektListaSvar> {
  return lasJson<ProjektListaSvar>(`${API_BAS}/projekt${arkiverade ? "?arkiverade=1" : ""}`);
}

export function hamtaGeografier(): Promise<GeografierSvar> {
  return lasJson<GeografierSvar>(`${API_BAS}/geografier`);
}

export function skapaProjekt(begaran: SkapaProjektBegaran): Promise<SkapaProjektSvar> {
  return lasJson<SkapaProjektSvar>(`${API_BAS}/projekt`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(begaran),
  });
}

/** Ämnen som en rapport kan skapas för (med förklaring när den inte kan det). */
export function hamtaRapportAmnen(projekt: string): Promise<{ amnen: RapportAmne[] }> {
  return lasJson<{ amnen: RapportAmne[] }>(`${API_BAS}/projekt/${encodeURIComponent(projekt)}/rapporter`);
}

export function startaRapportJobb(
  projekt: string,
  amne: string,
  bestallning: RapportBestallningBegaran,
  val: RapportVal = {},
): Promise<RapportJobb> {
  return lasJson<RapportJobb>(`${API_BAS}/projekt/${encodeURIComponent(projekt)}/rapporter`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      amne,
      bestallning,
      ...(val.korning_id ? { korning_id: val.korning_id } : {}),
      ...(val.scenario ? { scenario: val.scenario } : {}),
      ...(val.fragor && val.fragor.length > 0 ? { fragor: val.fragor } : {}),
    }),
  });
}

export function hamtaRapportJobb(statusUrl: string): Promise<RapportJobb> {
  return lasJson<RapportJobb>(statusUrl);
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

/** Markhöjden (m RH 2000) i en punkt ur projektets markmodell, för avläsning under pekaren. */
export function hamtaMarkhojd(projekt: string, e: number, n: number): Promise<MarkhojdSvar> {
  const q = `e=${Math.round(e * 100) / 100}&n=${Math.round(n * 100) / 100}`;
  return lasJson<MarkhojdSvar>(`${API_BAS}/kartverktyg/${encodeURIComponent(projekt)}/hojd?${q}`);
}

/* ---------- Nycklar (/konto/nycklar): bara kontots egna, aldrig utläsbara ---------- */

const JSON_HUVUD = { "Content-Type": "application/json" };

export function hamtaNycklar(): Promise<NycklarSvar> {
  return lasJson<NycklarSvar>("/konto/nycklar");
}

export function sparaNyckel(typ: string, varden: Record<string, string>): Promise<NyckelStatus> {
  return lasJson<NyckelStatus>(`/konto/nycklar/${encodeURIComponent(typ)}`, {
    method: "PUT",
    headers: JSON_HUVUD,
    body: JSON.stringify(varden),
  });
}

export function taBortNyckel(typ: string): Promise<{ status: string }> {
  return lasJson<{ status: string }>(`/konto/nycklar/${encodeURIComponent(typ)}`, { method: "DELETE" });
}

/* ---------- MCP-token (/konto/mcp-token): visas en gång vid skapandet ---------- */

export function hamtaMcpTokens(): Promise<McpTokenSvar> {
  return lasJson<McpTokenSvar>("/konto/mcp-token");
}

export function skapaMcpToken(namn: string): Promise<NyMcpToken> {
  return lasJson<NyMcpToken>("/konto/mcp-token", {
    method: "POST",
    headers: JSON_HUVUD,
    body: JSON.stringify({ namn }),
  });
}

export function aterkallaMcpToken(id: string): Promise<{ status: string }> {
  return lasJson<{ status: string }>(`/konto/mcp-token/${encodeURIComponent(id)}`, { method: "DELETE" });
}

/* ---------- Anslutna appar (/konto/oauth): Claude-appen och andra OAuth-klienter ---------- */

export function hamtaOauthAnslutningar(): Promise<OauthSvar> {
  return lasJson<OauthSvar>("/konto/oauth");
}

export function aterkallaOauthAnslutning(id: string): Promise<{ status: string }> {
  return lasJson<{ status: string }>(`/konto/oauth/${encodeURIComponent(id)}`, { method: "DELETE" });
}

/* ---------- Administration (/konto/admin): superadmin och organisationsadmin ---------- */

export function hamtaAdminProjekt(): Promise<{ projekt: AdminProjekt[] }> {
  return lasJson<{ projekt: AdminProjekt[] }>("/konto/admin/projekt");
}

export function hamtaAdminProjektDetalj(id: string): Promise<AdminProjektDetalj> {
  return lasJson<AdminProjektDetalj>(`/konto/admin/projekt/${encodeURIComponent(id)}`);
}

export function bjudIn(projektId: string, epost: string, roll: string): Promise<{ status: string }> {
  return lasJson<{ status: string }>(`/konto/admin/projekt/${encodeURIComponent(projektId)}/inbjudningar`, {
    method: "POST",
    headers: JSON_HUVUD,
    body: JSON.stringify({ epost, roll }),
  });
}

export function sattProjektRoll(projektId: string, kontoId: string, roll: string): Promise<{ status: string }> {
  return lasJson<{ status: string }>(
    `/konto/admin/projekt/${encodeURIComponent(projektId)}/medlemmar/${encodeURIComponent(kontoId)}`,
    { method: "PUT", headers: JSON_HUVUD, body: JSON.stringify({ roll }) },
  );
}

export function taBortProjektRoll(projektId: string, kontoId: string): Promise<{ status: string }> {
  return lasJson<{ status: string }>(
    `/konto/admin/projekt/${encodeURIComponent(projektId)}/medlemmar/${encodeURIComponent(kontoId)}`,
    { method: "DELETE" },
  );
}

export function sparraKonto(kontoId: string, sparrad: boolean): Promise<{ status: string }> {
  return lasJson<{ status: string }>(`/konto/admin/konton/${encodeURIComponent(kontoId)}`, {
    method: "PUT",
    headers: JSON_HUVUD,
    body: JSON.stringify({ sparrad }),
  });
}

export function sattProjektDelning(projektId: string, synlig: boolean): Promise<{ status: string }> {
  return lasJson<{ status: string }>(`/konto/projekt/${encodeURIComponent(projektId)}/delning`, {
    method: "PUT",
    headers: JSON_HUVUD,
    body: JSON.stringify({ organisation_synlig: synlig }),
  });
}

/* ---------- Administration av organisationen: medlemmar och roller ---------- */

export function hamtaAdminOrganisationer(): Promise<{ organisationer: AdminOrganisation[] }> {
  return lasJson<{ organisationer: AdminOrganisation[] }>("/konto/admin/organisationer");
}

export function hamtaAdminOrganisationDetalj(id: string): Promise<AdminOrganisationDetalj> {
  return lasJson<AdminOrganisationDetalj>(`/konto/admin/organisationer/${encodeURIComponent(id)}`);
}

export function bjudInOrganisation(orgId: string, epost: string, roll: string): Promise<{ status: string }> {
  return lasJson<{ status: string }>(`/konto/admin/organisationer/${encodeURIComponent(orgId)}/inbjudningar`, {
    method: "POST",
    headers: JSON_HUVUD,
    body: JSON.stringify({ epost, roll }),
  });
}

export function sattOrganisationsRoll(orgId: string, kontoId: string, roll: string): Promise<{ status: string }> {
  return lasJson<{ status: string }>(
    `/konto/admin/organisationer/${encodeURIComponent(orgId)}/medlemmar/${encodeURIComponent(kontoId)}`,
    { method: "PUT", headers: JSON_HUVUD, body: JSON.stringify({ roll }) },
  );
}

export function taBortOrganisationsMedlem(orgId: string, kontoId: string): Promise<{ status: string }> {
  return lasJson<{ status: string }>(
    `/konto/admin/organisationer/${encodeURIComponent(orgId)}/medlemmar/${encodeURIComponent(kontoId)}`,
    { method: "DELETE" },
  );
}

export function bytOrganisationsnamn(orgId: string, namn: string): Promise<{ status: string }> {
  return lasJson<{ status: string }>(`/konto/admin/organisationer/${encodeURIComponent(orgId)}`, {
    method: "PUT",
    headers: JSON_HUVUD,
    body: JSON.stringify({ namn }),
  });
}

/* ---------- Projektinställningar ---------- */

const projektUrl = (id: string, del: string): string => `/konto/projekt/${encodeURIComponent(id)}/${del}`;

export function hamtaProjektInstallningar(id: string): Promise<ProjektInstallningar> {
  return lasJson<ProjektInstallningar>(projektUrl(id, "installningar"));
}

export function bytProjektNamn(id: string, namn: string): Promise<{ status: string; namn: string }> {
  return lasJson(`${API_BAS}/projekt/${encodeURIComponent(id)}/namn`, { method: "PUT", headers: JSON_HUVUD, body: JSON.stringify({ namn }) });
}

export function arkiveraProjekt(id: string, arkiverad: boolean): Promise<{ status: string }> {
  return lasJson(projektUrl(id, "arkiv"), { method: "PUT", headers: JSON_HUVUD, body: JSON.stringify({ arkiverad }) });
}

export function raderaProjekt(id: string, bekraftaNamn: string): Promise<RaderaSvar> {
  return lasJson<RaderaSvar>(`${API_BAS}/projekt/${encodeURIComponent(id)}/radera`, {
    method: "POST",
    headers: JSON_HUVUD,
    body: JSON.stringify({ bekrafta_namn: bekraftaNamn }),
  });
}

export function bjudInTillProjekt(id: string, epost: string, roll: string): Promise<{ status: string }> {
  return lasJson(projektUrl(id, "inbjudningar"), { method: "POST", headers: JSON_HUVUD, body: JSON.stringify({ epost, roll }) });
}

export function aterkallaProjektInbjudan(id: string, inbjudan: string): Promise<{ status: string }> {
  return lasJson(`${projektUrl(id, "inbjudningar")}/${encodeURIComponent(inbjudan)}`, { method: "DELETE" });
}

export function andraMedlemsRoll(id: string, konto: string, roll: string): Promise<{ status: string }> {
  return lasJson(`${projektUrl(id, "medlemmar")}/${encodeURIComponent(konto)}`, { method: "PUT", headers: JSON_HUVUD, body: JSON.stringify({ roll }) });
}

export function taBortMedlem(id: string, konto: string): Promise<{ status: string }> {
  return lasJson(`${projektUrl(id, "medlemmar")}/${encodeURIComponent(konto)}`, { method: "DELETE" });
}
