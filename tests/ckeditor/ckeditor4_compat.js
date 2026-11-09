// CKEditor 4 round-trip compatibility check (run with Node + Playwright).
//   node tests/ckeditor/ckeditor4_compat.js <ckeditor4 dir> <response.json> <out.json>
// Loads every claim HTML into a real CKEditor 4 instance (setData -> getData) under several
// configurations and reports what the editor preserved.
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const RECOMMENDED_EXTRA = [
  'div p span strong em u s sub sup br bdo(pc-*)[dir,lang]{*}',
  'a[href,rel]',
  'img(pc-*)[src,alt,width,height]{vertical-align}',
  'table(pc-*)[dir]{*}; tbody tr; td th[colspan,rowspan]{*}',
  'math[xmlns,display]; mrow mi mn mo mtext mfrac msup msub msubsup msqrt mroot munder mover munderover mmultiscripts mprescripts mtable mtr mtd menclose mphantom mspace mstyle none[*]',
].join('; ');

const CONFIGS = {
  default_acf: {},
  acf_disabled: { allowedContent: true },
  recommended: { extraAllowedContent: RECOMMENDED_EXTRA },
};

(async () => {
  const [ckDir, responsePath, outPath] = process.argv.slice(2);
  const claims = JSON.parse(fs.readFileSync(responsePath, 'utf8')).claims;
  const browser = await chromium.launch();
  const page = await browser.newPage();
  await page.goto('file://' + path.resolve(ckDir, 'samples', 'index.html')).catch(() => {});
  await page.setContent('<!doctype html><html><head><meta charset="utf-8"></head><body><textarea id="e"></textarea></body></html>');
  await page.addScriptTag({ path: path.resolve(ckDir, 'ckeditor.js') });
  const results = {};
  for (const [name, cfg] of Object.entries(CONFIGS)) {
    results[name] = await page.evaluate(async ({ cfg, claims, basePath }) => {
      window.CKEDITOR_BASEPATH = basePath;
      CKEDITOR.basePath = basePath;
      if (CKEDITOR.instances.e) CKEDITOR.instances.e.destroy(true);
      const editor = CKEDITOR.replace('e', Object.assign({ versionCheck: false, language: 'en' }, cfg));
      await new Promise(r => editor.on('instanceReady', r));
      const metrics = (html) => {
        const d = document.createElement('div');
        d.innerHTML = html;
        const q = (s) => d.querySelectorAll(s).length;
        // Non-whitespace characters in document order: any lost/added/reordered character is detected;
        // CKEditor's newline formatting between block elements is not content.
        const text = (d.textContent || '').replace(/\s+/g, '');
        return {
          text, img: q('img[src^="data:image/"]'), table: q('table'), rowspan: q('[rowspan]'), colspan: q('[colspan]'),
          sub: q('sub'), sup: q('sup'), dir: q('[dir]'), math: q('math'), mfrac: q('mfrac'), strong: q('strong'),
          styled: q('[style]'), link: q('a[href]'),
        };
      };
      const totals = {};
      const lost = {};
      let textEqual = 0;
      for (const c of claims) {
        for (const lang of ['claimEn', 'claimAr']) {
          const before = metrics(c[lang]);
          const out = await new Promise(r => editor.setData(c[lang], { callback: () => r(editor.getData()) }));
          const after = metrics(out);
          if (before.text === after.text) textEqual++;
          else if (!window.__sample) {
            let i = 0; while (i < before.text.length && before.text[i] === after.text[i]) i++;
            window.__sample = { claim: c.claimNumber, lang, before: before.text.slice(Math.max(0, i - 20), i + 20),
                                after: after.text.slice(Math.max(0, i - 20), i + 20) };
          }
          for (const k of Object.keys(before)) {
            if (k === 'text') continue;
            totals[k] = (totals[k] || 0) + before[k];
            const diff = before[k] - after[k];
            if (diff > 0) lost[k] = (lost[k] || 0) + diff;
          }
        }
      }
      const sample = window.__sample; window.__sample = null;
      return { firstTextDifference: sample, version: CKEDITOR.version, claimsChecked: claims.length * 2, textIdentical: textEqual, totals, lost };
    }, { cfg, claims, basePath: 'file://' + path.resolve(ckDir) + '/' });
  }
  await browser.close();
  fs.writeFileSync(outPath, JSON.stringify(results, null, 2));
  for (const [k, v] of Object.entries(results)) {
    console.log(k.padEnd(14), `CKEditor ${v.version}`, `text identical ${v.textIdentical}/${v.claimsChecked}`, 'lost:', JSON.stringify(v.lost));
  }
})();
