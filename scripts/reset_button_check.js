// ============================================================================
// Bedient den "Zurücksetzen"-Knopf einer gedrosselten Maschine im echten
// Browser und prüft, ob sie danach wirklich wieder OK ist.
//
// Prüft die Kette, die ein Nutzer geht: Knopf sichtbar -> Klick ->
// POST /api/control -> Kafka -> Simulator -> Telemetrie -> Karte grün.
// ============================================================================
const { chromium } = require('playwright');

const host = process.env.TWIN_HOST;
const machine = Number(process.env.MACHINE);
const outDir = process.env.OUT_DIR || '/workspace/artifacts';

const cardFor = (page, id) =>
  page.locator('div').filter({ hasText: new RegExp(`^Maschine ${id}`) }).first();

(async () => {
  const browser = await chromium.launch({ args: ['--no-sandbox'] });
  const context = await browser.newContext({
    ignoreHTTPSErrors: true,
    httpCredentials: { username: process.env.BASIC_AUTH_USER, password: process.env.PW },
    viewport: { width: 1600, height: 1000 },
  });
  const page = await context.newPage();

  await page.goto(`https://${host}/`, { waitUntil: 'networkidle', timeout: 30000 });
  await page.getByRole('button', { name: 'SCADA Live' }).click();
  await page.waitForTimeout(3000);

  const card = cardFor(page, machine);
  const resetBtn = card.getByRole('button', { name: 'Zurücksetzen' });

  const visibleBefore = await resetBtn.count();
  console.log(`Knopf an Maschine ${machine} sichtbar: ${visibleBefore > 0}`);
  await page.screenshot({ path: `${outDir}/reset-before.png` });
  if (!visibleBefore) { await browser.close(); process.exit(1); }

  await resetBtn.first().click();
  console.log('geklickt — warte auf Statuswechsel…');

  let ok = false;
  for (let i = 0; i < 20; i++) {
    await page.waitForTimeout(1500);
    const text = await card.innerText();
    if (/\bOK\b/.test(text) && !/THROTTLED|ERROR/.test(text)) { ok = true; console.log(`wieder OK nach ~${((i + 1) * 1.5).toFixed(1)}s`); break; }
  }

  await page.screenshot({ path: `${outDir}/reset-after.png` });
  const stillThere = await card.getByRole('button', { name: 'Zurücksetzen' }).count();
  console.log(`Knopf danach noch da: ${stillThere > 0} (erwartet: false)`);

  await browser.close();
  process.exit(ok && stillThere === 0 ? 0 : 1);
})();
