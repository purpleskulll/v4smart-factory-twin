// ============================================================================
// Screenshots des Dashboards DURCH den Edge (Basic Auth + TLS + WebSocket).
//
// Runs in a throwaway Playwright container attached to the v4smart_edge network,
// so it takes exactly the path a real browser takes (TLS + basic auth + WS).
//
// Zweck: die Sichtprüfung, die kein HTTP-Status ersetzt — laufen die drei Views,
// steht das Verbindungs-Badge auf LIVE, kommen echte Werte an?
// ============================================================================
const { chromium } = require('playwright');

const host = process.env.TWIN_HOST || 'twin.example.com';
const user = process.env.BASIC_AUTH_USER || 'admin';
const pass = process.env.PW || '';
const outDir = process.env.OUT_DIR || '/workspace/artifacts';

const VIEWS = [
  { name: 'control-center', tab: 'Control Center' },
  { name: 'scada-live', tab: 'SCADA Live' },
  { name: 'mes-log', tab: 'MES/ERP Log' },
];

(async () => {
  const browser = await chromium.launch({ args: ['--no-sandbox'] });
  const context = await browser.newContext({
    ignoreHTTPSErrors: true, // interne Caddy-Zertifikate
    httpCredentials: { username: user, password: pass },
    viewport: { width: 1600, height: 1000 },
  });
  const page = await context.newPage();

  const errors = [];
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', (e) => errors.push(String(e)));

  await page.goto(`https://${host}/`, { waitUntil: 'networkidle', timeout: 30000 });

  // Auf echte Live-Daten warten, nicht auf das bloße Rendern.
  await page.waitForSelector('text=LIVE', { timeout: 20000 });
  await page.waitForTimeout(4000); // Sparklines mit Punkten füllen

  for (const view of VIEWS) {
    await page.getByRole('button', { name: view.tab }).click();
    await page.waitForTimeout(2500);
    await page.screenshot({ path: `${outDir}/${view.name}.png` });
    console.log(`screenshot: ${view.name}.png`);
  }

  // Belege aus dem laufenden DOM (statt nur "sieht gerendert aus").
  const badge = await page.locator('header span:has-text("LIVE")').count();
  const summary = await page.evaluate(() => {
    const text = document.body.innerText;
    return {
      hasLive: text.includes('LIVE'),
      hasReconnect: text.includes('RECONNECT'),
      machineCards: document.body.innerText.match(/Maschine \d+/g)?.length ?? 0,
      sparklines: document.querySelectorAll('svg polyline').length,
    };
  });
  console.log('DOM:', JSON.stringify({ badge, ...summary }));
  console.log('Konsolenfehler:', errors.length ? errors.slice(0, 5) : 'keine');

  await browser.close();
  process.exit(summary.hasLive && !summary.hasReconnect ? 0 : 1);
})();
