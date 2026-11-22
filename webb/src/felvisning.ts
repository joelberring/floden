/** Fel i beräkningar: förklaring på vanlig svenska överst, den tekniska texten under "Teknisk information". */

function esc(text: string): string {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

/** HTML för ett misslyckat beräkningssvar. Utan förklaring visas den tekniska texten som förut. */
export function felHtml(teknisk: string | null | undefined, forklaring: string | null | undefined): string {
  const text = teknisk ?? "okänt fel";
  if (!forklaring) return `<p class="fel-text" role="alert">Beräkningen misslyckades: ${esc(text)}</p>`;
  return `<p class="fel-text" role="alert">${esc(forklaring)}</p>
    <details class="teknisk"><summary>Teknisk information</summary><p class="mono">${esc(text)}</p></details>`;
}

/** Upplysningar före körning (t.ex. att ett hus stänger in en gård): information, inte fel. */
export function informationHtml(information: readonly string[] | undefined): string {
  return (information ?? []).map((t) => `<p class="dampad" role="status">${esc(t)}</p>`).join("");
}

/** Varningar från en klar beräkning (t.ex. slutna områden), under beskedet att det är klart. */
export function korVarningarHtml(varningar: readonly string[] | undefined): string {
  return (varningar ?? []).map((t) => `<p class="varnings-text" role="note">${esc(t)}</p>`).join("");
}
