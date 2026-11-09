// Render an HTML file in Chromium and save a full-page screenshot: node screenshot.js in.html out.png [width]
const { chromium } = require('playwright');
(async () => {
  const [inp, out, width] = process.argv.slice(2);
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: parseInt(width || '1300', 10), height: 900 } });
  await p.goto('file://' + require('path').resolve(inp));
  await p.screenshot({ path: out, fullPage: true });
  await b.close();
})();
