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
