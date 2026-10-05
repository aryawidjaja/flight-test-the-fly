# Paper: submission files

Generated from the web paper (app/guide.js rendered in headless Chrome) by `node scripts/export_paper.mjs`, which regenerates every file here except `template.tex` (the hand-written LaTeX preamble).
`paper.pdf` is the print of the web page (23 pages); `main.tex` (pandoc + `template.tex`, written for pdfLaTeX with every non-ASCII character mapped; built and checked here with Tectonic, so arXiv's compile preview is the pdfLaTeX check) compiles to `main.pdf` (21 pages) with `figures/fig*.pdf`; `paper.docx` is the Word version with `figures/fig*.png` (300 dpi) and native equations; `paper.html` is the sanitized DOM (TeX math, figure images) that pandoc converts; `references.bib` holds the reference list; `arxiv.zip` is the source bundle to upload to arXiv (main.tex with an inline bibliography, plus the figure PDFs); `abstract.txt` is the plain-text abstract (1897 characters).

## Suggested arXiv metadata

- **Title:** Flight-testing a fruit-fly connectome as an aircraft yaw damper
- **Author:** Mutaqin Aryawijaya (Independent researcher)
- **Abstract:** contents of `abstract.txt`
- **Comments:** 21 pages, 5 figures, 5 tables; code and interactive replay at https://fly.aryawijaya.com
- **Licence:** CC BY 4.0
