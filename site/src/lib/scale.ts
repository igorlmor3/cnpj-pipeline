/** Passo "bonito" (1, 2, 2.5, 5 x 10^n) para dividir [0, max] em cerca de `count` intervalos. */
export function niceStep(max: number, count: number): number {
  if (!(max > 0) || count < 1) return 1;
  const raw = max / count;
  const power = 10 ** Math.floor(Math.log10(raw));
  const fraction = raw / power;
  const nice = fraction <= 1 ? 1 : fraction <= 2 ? 2 : fraction <= 2.5 ? 2.5 : fraction <= 5 ? 5 : 10;
  return nice * power;
}

export function niceTicks(max: number, count = 4): { ticks: number[]; top: number } {
  const step = niceStep(max, count);
  const top = Math.max(step, Math.ceil(max / step) * step);
  const ticks: number[] = [];
  for (let value = 0; value <= top + step / 1e6; value += step) ticks.push(Number(value.toPrecision(12)));
  return { ticks, top };
}

/** Indices de rotulos do eixo x: no maximo `maxTicks`, sempre incluindo o primeiro. */
export function evenTicks(length: number, maxTicks: number): number[] {
  if (length <= 0) return [];
  const limit = Math.max(1, maxTicks);
  const step = Math.max(1, Math.ceil(length / limit));
  const out: number[] = [];
  for (let i = 0; i < length; i += step) out.push(i);
  return out;
}
