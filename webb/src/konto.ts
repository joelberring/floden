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
  | { lage: "in"; namn: string; epost: string };

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
  const d = (data ?? {}) as { inloggning?: string; konto?: { namn?: string; epost?: string } };
  if (status === 200 && d.inloggning === "av") return { lage: "av" };
  if (status === 200 && d.inloggning === "pa" && d.konto) {
    return { lage: "in", namn: d.konto.namn ?? "", epost: d.konto.epost ?? "" };
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
    return `<span class="konto-inloggad" data-konto="inloggad">Inloggad som <strong>${esc(vem)}</strong></span>
      <button type="button" class="knapp" data-konto="logga-ut">Logga ut</button>`;
  }
  return "";
}

/** Knapparna på startsidans hjälte. */
export function kontoHjalteHtml(): string {
  const l = aktuellt;
  if (l.lage === "ut") {
    return `<a class="knapp knapp-svart knapp-stor" href="${esc(inloggningsAdress("/"))}" data-konto="logga-in">Logga in med Google</a>`;
  }
  return `<a class="knapp knapp-svart knapp-stor" href="#/fragor">Öppna utredningsfrågorna</a>
    <a class="knapp knapp-stor" href="#/oversikt">Översikt</a>`;
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
