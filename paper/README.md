# Paper: submission files

Generated from the web paper (app/guide.js rendered in headless Chrome) by `node scripts/export_paper.mjs`, which regenerates every file here except `template.tex` (the hand-written LaTeX preamble).
`paper.pdf` is the print of the web page (22 pages); `main.tex` (pandoc + `template.tex`, pdfLaTeX-compatible) compiles to `main.pdf` (20 pages) with `figures/fig*.pdf`; `paper.docx` is the Word version with `figures/fig*.png` (300 dpi) and native equations; `paper.html` is the sanitized DOM (TeX math, figure images) that pandoc converts; `references.bib` holds the reference list; `abstract.txt` is the plain-text abstract (1897 characters).

## Suggested arXiv metadata

- **Title:** Flight-testing a fruit-fly connectome as an aircraft yaw damper
- **Author:** Mutaqin Aryawijaya (Independent researcher)
- **Abstract:** contents of `abstract.txt`
- **Comments:** 20 pages, 5 figures, 5 tables; code and interactive replay at https://fly.aryawijaya.com
- **Licence:** CC BY 4.0
