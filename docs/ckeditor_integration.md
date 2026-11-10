# CKEditor integration

The generated HTML uses a small, fixed vocabulary: `div p span strong em u s sub
sup br a img table tbody tr td bdo`, `dir`/`lang` attributes, `pc-*` classes,
inline styles from an allowlist, and MathML elements for equations. CKEditor's
content filter decides what survives when OutSystems loads the HTML into the
editor, so **the editor configuration is part of the integration**.

## Measured behaviour

Round trip `setData(html)` → `getData()` for all 12 claims (6 EN + 6 AR) of
the synthetic fixture set. Text is compared by its non-whitespace characters in
order. Scripts: `tests/ckeditor/ckeditor4_compat.js` and
`tests/ckeditor/ckeditor5_compat.js`. Raw results:
`docs/benchmarks/ckeditor4_compat.json` and `docs/benchmarks/ckeditor5_compat.json`.

| Editor / configuration | Text identical | Lost elements |
|---|---|---|
| CKEditor 4.22.1 standard, **default ACF** | 12/12 | 6 `sub`, 2 `sup`, 15 `dir`, 8 `math` (all equations) |
| CKEditor 4.22.1, `allowedContent: true` | 12/12 | none |
| CKEditor 4.22.1, recommended `extraAllowedContent` (below) | 12/12 | none |
| CKEditor 5 (47.4), typical plugins | 12/12 | `sub`, `sup`, `dir`, all MathML |
| CKEditor 5, + Subscript/Superscript | 12/12 | `dir`, all MathML |
| CKEditor 5, + General HTML Support (allow all) | 12/12 | none |

"Text identical" can hide real damage. With default filters the equation
characters survive as flat text ("dydx=x2") while the fraction is destroyed.
Element counts must be checked as well, not only text.

Images (Base64 data URIs), tables, `rowspan`/`colspan` and links survived in
every configuration.

## Required configuration

### CKEditor 4

```js
CKEDITOR.replace('editor', {
  extraAllowedContent: [
    'div p span strong em u s sub sup br bdo(pc-*)[dir,lang]{*}',
    'a[href,rel]',
    'img(pc-*)[src,alt,width,height]{vertical-align}',
    'table(pc-*)[dir]{*}; tbody tr; td th[colspan,rowspan]{*}',
    'math[xmlns,display]; mrow mi mn mo mtext mfrac msup msub msubsup msqrt mroot munder mover ' +
      'munderover mmultiscripts mprescripts mtable mtr mtd menclose mphantom mspace mstyle none[*]'
  ].join('; ')
});
```

### CKEditor 5

Enable `Subscript`, `Superscript`, `Table`, `TableProperties`, `TableCellProperties`,
`Image`/`ImageInline`, `Link`, `Font`, `Alignment` **and** `GeneralHtmlSupport` with:

```js
htmlSupport: { allow: [{ name: /.*/, attributes: true, classes: true, styles: true }] }
```

(Narrow the pattern to the vocabulary above if your security policy requires it.)

### If MathML cannot be allowed

Set `PATENT__CONTENT__EQUATION_MODE=image`. Every equation is then rendered from
its original OMML to a PNG `<img>` (alt text = linear form of the equation).
Images survive every tested configuration. `sub`/`sup`/`dir` still need the
configuration above.

## Display notes

* Chromium-based browsers render MathML Core natively. Verify the browsers your
  users run.
* The HTML carries no stylesheet. `pc-claim`, `pc-table`, `pc-textbox`, `pc-image`,
  `pc-floating`, `pc-equation`, `pc-label`, `pc-noteref` and `pc-link` are hooks
  for optional styling in OutSystems.
* Floating Word objects are shown at their anchor position in the text flow
  (class `pc-floating`).

## Test page

`GET /test/ckeditor4` (off by default; `test_page.enabled: true` or
`PATENT__TEST_PAGE__ENABLED=true`, then restart) serves a browser test bench:

1. Choose the CKEditor 4 version (CDN: 4.11.4 … 4.22.1, `standard-all`/`full-all`/`basic`)
   or paste the URL of the `ckeditor.js` your OutSystems application loads (browser
   Network tab), so the exact build is tested.
2. Choose the editor configuration: default content filter, the recommended
   `extraAllowedContent`, filter off, or paste the OutSystems editor config as JSON.
3. Pick the English and/or Arabic DOCX (`/word_to_json`) or one DOCX
   (`/convert_word_to_html`) and click **Convert and test**.

The page calls the service from the same address, so no CORS is needed. It then runs
`setData` → `getData` for every claim and lists per claim what the editor removed:
elements (`sub`, `sup`, `math`, tables, images…), `dir`, styles, tabs, text and image
data. Click a claim to see the original rendering next to the live editor, with
element counts and the HTML before and after. API errors are shown with their code and
details. Everything runs in the browser and nothing is stored. The browser needs
access to `cdn.ckeditor.com`, unless you use a custom URL.

### Known CKEditor 4 behaviour: tabs
Word tabs are emitted as `<span style="white-space:pre">\t</span>`. CKEditor 4 (all
configurations) turns the tab into one space and drops some of them, so the gap after
a claim number ("1.→A method") narrows. Text is unchanged. Measured on the customer
samples with the recommended configuration, tabs were the **only** loss: 21/21 claims in
two documents, 11/17 and 3/50 in two others. CKEditor keeps `&emsp;`/`&nbsp;`, so the fix
would be to emit tabs as em spaces (not done yet: it changes the HTML of most claims).

## Still to verify (Phase 8)

The exact CKEditor version/build and configuration used by the OutSystems
application, tested with real patent output and at maximum claim size. The
scripts above can be pointed at any CKEditor package and response file.
