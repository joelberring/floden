/*
 * Inloggningsläge i klienten.
 *
 * Servern avgör allt (docs/KONTO.md): klienten läser bara `GET /konto/jag` och visar rätt
 * knappar. Med `inloggning = "av"` (standard lokalt) finns inga inloggningsknappar alls.
 * Med `"pa"` visas Googleknappen, "Inloggad som" och Logga ut. Alla POST bär CSRF-huvudet
 * `X-Floden-CSRF` (dubbel inskickning: samma värde som kakan, som servern jämför).
 */
export type KontoLage =
  | { lage: "okand" }
  | { lage: "av" }
  | { lage: "ut" }
  | {
      lage: "in";
      namn: string;
      epost: string;
      medlemskap: Medlemskap[];
      projekt: KontoProjekt[];
      /** Superadmin (sätts bara med serverkommando). */
      superadmin?: boolean;
      /** Superadmin eller organisationsadmin: får använda Administration. */
      admin?: boolean;
    };

export interface Medlemskap {
  organisation: string;
  forvaltning: string | null;
  roll: string;
}

export interface KontoProjekt {
  id: string;
  namn: string;
  roll: string;
}

/** Rollens namn i klartext (kontoservern lagrar id utan å, ä, ö: `lasare`, `agare`). */
export function rollNamn(roll: string): string {
  const namn: Record<string, string> = { lasare: "läsare", utredare: "utredare", medlem: "medlem", ledare: "ledare", organisationsadmin: "organisationsadmin", agare: "ägare", superadmin: "superadmin" };
  return namn[roll] ?? roll;
}

export const CSRF_HUVUD = "X-Floden-CSRF";
const CSRF_KAKNAMN = ["__Host-floden-csrf", "floden_csrf"];

let aktuellt: KontoLage = { lage: "okand" };
const lyssnare: Array<() => void> = [];

export function kontoLage(): KontoLage {
  return aktuellt;
}

export function vidKontoandring(f: () => void): void {
  lyssnare.push(f);
}

export function settKontoLage(l: KontoLage): void {
  aktuellt = l;
  for (const f of lyssnare) f();
}

/** CSRF-värdet ur en `document.cookie`-sträng, eller null när kakan saknas. */
export function csrfFranKakor(kakor: string): string | null {
  for (const del of kakor.split(";")) {
    const i = del.indexOf("=");
    if (i < 0) continue;
    const namn = del.slice(0, i).trim();
    if (CSRF_KAKNAMN.includes(namn)) {
      try {
        return decodeURIComponent(del.slice(i + 1).trim());
      } catch {
        return null;
      }
    }
  }
  return null;
}

export function csrfHuvud(kakor: string): Record<string, string> {
  const v = csrfFranKakor(kakor);
  return v ? { [CSRF_HUVUD]: v } : {};
}

/** Tolkar svaret från `GET /konto/jag` (status och JSON-kropp). */
export function tolkaKontoSvar(status: number, data: unknown): KontoLage {
  if (status === 401) return { lage: "ut" };
  const d = (data ?? {}) as {
    inloggning?: string;
    konto?: { namn?: string; epost?: string; superadmin?: boolean; admin?: boolean };
    medlemskap?: Array<{ organisation?: string; forvaltning?: string | null; roll?: string }>;
    projekt?: Array<{ id?: string; namn?: string; roll?: string }>;
  };
  if (status === 200 && d.inloggning === "av") return { lage: "av" };
  if (status === 200 && d.inloggning === "pa" && d.konto) {
    return {
      lage: "in",
      namn: d.konto.namn ?? "",
      epost: d.konto.epost ?? "",
      superadmin: d.konto.superadmin === true,
      admin: d.konto.admin === true || d.konto.superadmin === true,
      medlemskap: (d.medlemskap ?? []).map((m) => ({
        organisation: m.organisation ?? "",
        forvaltning: m.forvaltning ?? null,
        roll: m.roll ?? "",
      })),
      projekt: (d.projekt ?? []).map((p) => ({ id: p.id ?? "", namn: p.namn ?? "", roll: p.roll ?? "" })),
    };
  }
  return { lage: "okand" };
}

export async function laddaKonto(): Promise<void> {
  try {
    const svar = await fetch("/konto/jag", { headers: { Accept: "application/json" } });
    let data: unknown = null;
    try {
      data = await svar.json();
    } catch {
      data = null;
    }
    settKontoLage(tolkaKontoSvar(svar.status, data));
  } catch {
    settKontoLage({ lage: "okand" });
  }
}

export function inloggningsAdress(atervand: string): string {
  return `/konto/logga-in/google?atervand=${encodeURIComponent(atervand)}`;
}

export async function loggaUt(): Promise<void> {
  await fetch("/konto/logga-ut", {
    method: "POST",
    headers: csrfHuvud(document.cookie),
  });
  window.location.assign("/");
}

function esc(t: string): string {
  return t.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

/** Kontodelen i sidhuvudet. Tom när inloggning är av (inget att visa, inga platshållare). */
export function kontoHuvudHtml(): string {
  const l = aktuellt;
  if (l.lage === "ut") {
    return `<a class="knapp knapp-svart" href="${esc(inloggningsAdress("/"))}" data-konto="logga-in">Logga in med Google</a>`;
  }
  if (l.lage === "in") {
    const vem = l.namn || l.epost;
    return `<a href="#/nycklar" data-konto="nycklar">Nycklar</a>
      ${l.admin ? `<a href="#/administration" data-konto="administration">Administration</a>` : ""}
      <span class="konto-inloggad" data-konto="inloggad">Inloggad som <strong>${esc(vem)}</strong></span>
      <button type="button" class="knapp" data-konto="logga-ut">Logga ut</button>`;
  }
  return "";
}

/** Inloggningsknappen på startsidans hjälte (tom när ingen inloggning behövs). */
export function kontoHjalteHtml(): string {
  const l = aktuellt;
  if (l.lage === "ut") {
    return `<a class="knapp knapp-stor" href="${esc(inloggningsAdress("/"))}" data-konto="logga-in">Logga in med Google</a>`;
  }
  return "";
}

/**
 * Förvaltningssidan och förvaltningsväljaren visas bara när inloggning är på och kontoservern
 * har uppgifter om användaren (minst ett medlemskap). Annars finns de inte alls.
 */
export function forvaltningSynlig(l: KontoLage = aktuellt): boolean {
  return l.lage === "in" && l.medlemskap.length > 0;
}

/**
 * Arbetsbänken (den äldre kartan med kontrollfallen) nås bara med adressen `#/arbetsbank` och
 * visas bara för superadmin, eller alltid när inloggning är av (lokalt). Servern avgör inte detta:
 * det är en synlighetsregel i klienten, inte ett skydd.
 */
export function arbetsbankTillaten(l: KontoLage = aktuellt): boolean {
  return l.lage === "av" || (l.lage === "in" && l.superadmin === true);
}

/** Administration visas för superadmin och organisationsadmin. */
export function adminTillaten(l: KontoLage = aktuellt): boolean {
  return l.lage === "in" && l.admin === true;
}

/** Namn att hälsa med, eller null när ingen är inloggad (då hälsar sidan inte alls). */
export function halsningsnamn(l: KontoLage = aktuellt): string | null {
  if (l.lage !== "in") return null;
  const namn = l.namn.trim().split(/\s+/)[0] ?? "";
  return namn || null;
}

/** Notis på frågesidan. */
export function kontoNotis(): string {
  const l = aktuellt;
  if (l.lage === "av") return "Körs lokalt utan inloggning. Resultat sparas på den här datorn.";
  if (l.lage === "in") return "Inloggad. Resultat sparas på servern.";
  if (l.lage === "ut") return "Logga in för att se och köra utredningsfrågor.";
  return "";
}

export function startaKontoLyssning(): void {
  document.addEventListener("click", (h) => {
    const m = h.target instanceof Element ? h.target.closest("[data-konto='logga-ut']") : null;
    if (m) {
      h.preventDefault();
      void loggaUt();
    }
  });
}
