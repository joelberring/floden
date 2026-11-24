/*
 * Fel med en handling: ren logik utan DOM. Varje fel som en användare kan möta i flödet översätts till
 * en rubrik, en förklaring på svenska och (om det finns en) en sak att göra. Ingen "Är servern igång?" till
 * någon som sitter på en server: nätverksfel och serverfel skiljs från behörighet.
 */
import { ApiFel, OinloggadFel } from "../apifel.ts";
import { inloggningsAdress, type KontoLage } from "../konto.ts";

export type FelSlag = "inloggning" | "behorighet" | "lokalt" | "natverk" | "server" | "annat";

export interface Handling {
  text: string;
  /** Adress (hash-adress eller servern-adress). Saknas den är handlingen bara en text. */
  href?: string;
  /** Länken laddar om sidan (för "Försök igen" på samma adress). */
  laddaOm?: boolean;
}

export interface FelBesked {
  slag: FelSlag;
  rubrik: string;
  text: string;
  handlingar: Handling[];
}

/** Vilken sorts fel det är, ur felobjektet. */
export function felSlag(e: unknown): { slag: "inloggning" | "behorighet" | "natverk" | "server" | "annat"; status: number } {
  if (e instanceof OinloggadFel) return { slag: "inloggning", status: 401 };
  if (e instanceof ApiFel) {
    if (e.status === 401) return { slag: "inloggning", status: 401 };
    if (e.status === 403) return { slag: "behorighet", status: 403 };
    if (e.status === 0) return { slag: "natverk", status: 0 };
    if (e.status >= 500) return { slag: "server", status: e.status };
    return { slag: "annat", status: e.status };
  }
  if (e instanceof TypeError) return { slag: "natverk", status: 0 };
  return { slag: "annat", status: -1 };
}

export const ROLLER_SKAPA = "Projekt kan skapas av superadmin och av organisationsadmin eller ägare i en organisation.";

/**
 * Besked när sidan Nytt projekt inte kan visa formuläret. `lage` är kontoläget (av = lokalt utan inloggning).
 * `atervand` är adressen man ska komma tillbaka till efter inloggning.
 */
export function nyttProjektFel(e: unknown, lage: KontoLage, atervand = "/#/nytt"): FelBesked {
  const { slag } = felSlag(e);
  const projektlistan: Handling = { text: "Till projektlistan", href: "#/" };
  if (slag === "inloggning" || (slag === "behorighet" && lage.lage === "ut")) {
    return {
      slag: "inloggning",
      rubrik: "Du behöver logga in för att skapa ett projekt",
      text: "Projekt skapas av inloggade användare med rätt roll. Logga in och försök igen.",
      handlingar: [{ text: "Logga in", href: inloggningsAdress(atervand) }, projektlistan],
    };
  }
  if (slag === "behorighet" && lage.lage === "av") {
    return {
      slag: "lokalt",
      rubrik: "Nya projekt skapas inte i webben när Floden körs lokalt utan inloggning",
      text: "Rollerna finns i kontosystemet, som är avstängt här. Skapa projektet med kommandot floden projekt skapa (docs/KOM-IGANG.md) och ladda om sidan, eller slå på inloggning på servern.",
      handlingar: [projektlistan],
    };
  }
  if (slag === "behorighet") {
    return {
      slag: "behorighet",
      rubrik: "Du har inte rätt att skapa projekt",
      text: `${ROLLER_SKAPA} Be din organisationsadmin om rollen, eller be att få en roll i ett befintligt projekt.`,
      handlingar: [projektlistan],
    };
  }
  if (slag === "natverk") {
    return {
      slag: "natverk",
      rubrik: "Det gick inte att nå servern",
      text: "Kontrollera din anslutning och försök igen. Om det inte hjälper: kontakta den som driftar Floden.",
      handlingar: [{ text: "Försök igen", href: "#/nytt", laddaOm: true }, projektlistan],
    };
  }
  if (slag === "server") {
    return {
      slag: "server",
      rubrik: "Servern kunde inte svara just nu",
      text: "Det är ett fel hos servern, inte hos dig. Försök igen om en stund; kvarstår felet, kontakta den som driftar Floden.",
      handlingar: [{ text: "Försök igen", href: "#/nytt", laddaOm: true }, projektlistan],
    };
  }
  return {
    slag: "annat",
    rubrik: "Geografierna gick inte att hämta",
    text: e instanceof Error ? e.message : String(e),
    handlingar: [{ text: "Försök igen", href: "#/nytt", laddaOm: true }, projektlistan],
  };
}

/** Utan mappad geografi: vem som kan göra något åt det. */
export function ingenGeografi(superadmin: boolean): FelBesked {
  return {
    slag: "annat",
    rubrik: "Det finns ingen geografi att skapa projekt i än",
    text: superadmin
      ? "Mappa en geografi med floden geografi mappa (docs/DRIFT.md) och ladda om sidan."
      : "En geografi är det område där data finns. Be en superadmin mappa en med floden geografi mappa (docs/DRIFT.md).",
    handlingar: [{ text: "Till projektlistan", href: "#/" }],
  };
}

/** Ett fel för en vanlig sida (frågor, resultat, rapport): vad som hänt och vad man kan göra. */
export function sidFel(e: unknown, vad: string, lage: KontoLage, atervand: string): FelBesked {
  const { slag } = felSlag(e);
  const projektlistan: Handling = { text: "Till projektlistan", href: "#/" };
  const igen: Handling = { text: "Försök igen", href: atervand, laddaOm: true };
  if (slag === "inloggning" || (slag === "behorighet" && lage.lage === "ut")) {
    return { slag: "inloggning", rubrik: `${vad} kräver inloggning`, text: "Din inloggning har gått ut eller saknas. Logga in och försök igen.", handlingar: [{ text: "Logga in", href: inloggningsAdress(atervand) }, projektlistan] };
  }
  if (slag === "behorighet") {
    return { slag: "behorighet", rubrik: `Du har inte rätt att se ${vad.toLowerCase()}`, text: "Din roll i projektet räcker inte till. Be projektets ägare eller din organisationsadmin om rollen läsare eller utredare.", handlingar: [projektlistan] };
  }
  if (slag === "natverk") {
    return { slag: "natverk", rubrik: "Det gick inte att nå servern", text: `${vad} kunde inte hämtas. Kontrollera anslutningen och försök igen.`, handlingar: [igen, projektlistan] };
  }
  if (slag === "server") {
    return { slag: "server", rubrik: "Servern kunde inte svara just nu", text: `${vad} kunde inte hämtas. Försök igen om en stund; kvarstår felet, kontakta den som driftar Floden.`, handlingar: [igen, projektlistan] };
  }
  return { slag: "annat", rubrik: `${vad} gick inte att hämta`, text: e instanceof Error ? e.message : String(e), handlingar: [igen, projektlistan] };
}

/** HTML för ett felbesked (rubrik, text, handlingar). `esc` skickas in så att modulen inte beror på sidor.ts. */
export function felBeskedHtml(b: FelBesked, esc: (s: string) => string, id = ""): string {
  const knappar = b.handlingar
    .map((h, i) => `<a class="flode-knapp${i === 0 ? " svart" : ""}" href="${esc(h.href ?? "#/")}"${h.laddaOm ? " data-ladda-om" : ""}>${esc(h.text)}</a>`)
    .join("");
  return `<div class="flode-ruta varning flode-felbesked"${id ? ` id="${esc(id)}"` : ""} role="alert" tabindex="-1">
    <p><strong>${esc(b.rubrik)}</strong></p><p>${esc(b.text)}</p>
    <div class="flode-handlingar">${knappar}</div></div>`;
}

/** Kopplar "Försök igen"-länkarna i `rot`: ett klick laddar om sidan (adressen är densamma, så länken ensam gör inget). */
export function kopplaLaddaOm(rot: HTMLElement, signal?: AbortSignal): void {
  rot.addEventListener(
    "click",
    (h) => {
      if ((h.target as Element).closest("[data-ladda-om]")) {
        h.preventDefault();
        window.location.reload();
      }
    },
    { signal },
  );
}
