// CKEditor 5 round-trip compatibility check (Node + Playwright).
//   node tests/ckeditor/ckeditor5_compat.js <ckeditor5 package dir> <response.json> <out.json>
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

(async () => {
  const [ckDir, responsePath, outPath] = process.argv.slice(2);
  const claims = JSON.parse(fs.readFileSync(responsePath, 'utf8')).claims;
  const browser = await chromium.launch();
  const page = await browser.newPage();
  page.on('pageerror', e => console.error('pageerror', e.message));
  await page.setContent('<!doctype html><html><head><meta charset="utf-8"></head><body><div id="e"></div></body></html>');
  await page.addScriptTag({ path: path.resolve(ckDir, 'dist/browser/ckeditor5.umd.js') });
  const results = await page.evaluate(async (claims) => {
    const C = window.CKEDITOR;
    const base = [C.Essentials, C.Paragraph, C.Bold, C.Italic, C.Underline, C.Strikethrough, C.Link, C.Table,
                  C.TableProperties, C.TableCellProperties, C.Image, C.ImageInline, C.Alignment, C.Font];
    const configs = {
      typical: { plugins: base },
      with_sub_sup: { plugins: [...base, C.Subscript, C.Superscript] },
      ghs_full: {
        plugins: [...base, C.Subscript, C.Superscript, C.GeneralHtmlSupport, C.HtmlComment],
        htmlSupport: { allow: [{ name: /.*/, attributes: true, classes: true, styles: true }] },
      },
    };
    const metrics = (html) => {
      const d = document.createElement('div');
      d.innerHTML = html;
      const q = (s) => d.querySelectorAll(s).length;
      return {
        text: (d.textContent || '').replace(/\s+/g, ''), img: q('img[src^="data:image/"]'), table: q('table'),
        rowspan: q('[rowspan]'), colspan: q('[colspan]'), sub: q('sub'), sup: q('sup'), dir: q('[dir]'),
        math: q('math'), mfrac: q('mfrac'), strong: q('strong'), link: q('a[href]'),
      };
    };
    const out = {};
    for (const [name, cfg] of Object.entries(configs)) {
      const host = document.getElementById('e');
      host.innerHTML = '';
      const editor = await C.ClassicEditor.create(host, Object.assign({ licenseKey: 'GPL' }, cfg));
      const totals = {}, lost = {};
      let textEqual = 0, sample = null;
      for (const c of claims) {
        for (const lang of ['claimEn', 'claimAr']) {
          const before = metrics(c[lang]);
          editor.setData(c[lang]);
          const after = metrics(editor.getData());
          if (before.text === after.text) textEqual++;
          else if (!sample) {
            let i = 0; while (i < before.text.length && before.text[i] === after.text[i]) i++;
            sample = { claim: c.claimNumber, lang, before: before.text.slice(Math.max(0, i - 20), i + 20),
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
      await editor.destroy();
      out[name] = { version: C.version || 'n/a', claimsChecked: claims.length * 2, textIdentical: textEqual,
                    firstTextDifference: sample, totals, lost };
    }
    return out;
  }, claims);
  await browser.close();
  fs.writeFileSync(outPath, JSON.stringify(results, null, 2));
  for (const [k, v] of Object.entries(results)) {
    console.log(k.padEnd(14), `text identical ${v.textIdentical}/${v.claimsChecked}`, 'lost:', JSON.stringify(v.lost),
                v.firstTextDifference ? 'first diff: ' + JSON.stringify(v.firstTextDifference) : '');
  }
})();
