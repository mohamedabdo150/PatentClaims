# Results on the customer sample documents

Sample set received 2026-11-09 (8 files; `CLAIMS copy.docx` is identical to
`CLAIMS.docx`). The documents are confidential and are **not** in this repository.
`tests/regression/test_customer_samples.py` runs against them when
`PATENT_SAMPLES_DIR` points to their folder, and asserts only counts, numbers and
error codes.

## What the documents showed
* Claims are delimited by marker paragraphs **`{start}`** and **`{next}`**. `{next}`
  also appears inline at the end of a claim's last paragraph. This became the
  default **`marker`** profile.
* English claim numbers come from **Word list numbering** (`1.`, `1-`) or text
  (`2-The compound`). Some claims have no `{next}` between them (`CLAIMS.docx`
  claims 18–21): a new numbered paragraph also closes the previous claim.
* The Arabic document was converted from PDF. Words are split into separate
  paragraphs, numbers are written `-1`/`-8` and sometimes appear after the claim's
  first lines, and most claims sit in **layout tables** (text cell + number cell)
  with `{next}` inside the cells.

## Results (`marker` profile)

| Document | Language | Result | Claims | Content |
|---|---|---|---|---|
| CLAIMS.docx | EN | converted | 21 | 1 PNG structure |
| CLAIMS-English Amendment.docx | EN | converted | 21 | 1 PNG |
| CLclaims.docx | EN | converted | 50 | 16 images (11 EMF; 16 ChemDraw OLE from previews), 2 tables |
| bigClimsEn.docx (6 MB) | EN | converted | 17 | **779 images (778 EMF chemical structures)**, 6 tables, 12.6 MB HTML; 69 s sequential → **24 s** with `libreoffice_parallelism: 4` (4 vCPU), byte-identical output |
| Claims - Arabic.docx | AR | **rejected, `CLAIM_EXTRACTION_FAILED`** | 21 blocks | source defects, see below |
| Spec_E.docx | EN | rejected, `CLAIMS_SECTION_NOT_FOUND` | — | description document, no claims markers |
| high tension system Full Description.docx | EN | rejected, `CLAIMS_SECTION_NOT_FOUND` | — | description document |

For every converted document, the extractor accounted for exactly the analyzer's
text characters, image references and tables per claim, and every image was embedded.

### Arabic sample: defects to fix in the source
Compared with the English `CLAIMS.docx` (claim dependencies and content matched
manually; no claim text is reproduced here):

| Arabic block | Corresponds to | Problem |
|---|---|---|
| 8th block | EN claim 8 | its number `-8` was placed **after** the `{next}` marker |
| 9th block | EN claim 9 | **no `-9` anywhere**; picks up the displaced `-8` |
| 11th block | EN claim 11 | its number `-11` was placed after the `{next}` marker (in claim 12's block) |

Pairing by position here would have put Arabic claim 9's text next to English
claim 8, so the service returns an explicit error naming the blocks. With these
three positions corrected (test copy only), `CLAIMS.docx` + Arabic converts:
**21 paired claims in 0.3 s**, both chemical structures embedded, Arabic RTL
content in reading order.

A fourth apparent defect (claims 13/14) was not a source error. The `-13` number
cell sits beside a text cell that ends with `{next}`. This is handled by the
layout-row rule (a number-only cell belongs to its own row).
