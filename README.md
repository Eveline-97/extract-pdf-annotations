# Extract PDF Annotations

Originally inspired by [JensLincke](https://github.com/JensLincke)'s [PDFMarkupAndNotesExtractor](https://github.com/JensLincke/PDFMarkupAndNotesExtractor) and vibe-coded with Claude.

## Requires
- Python 3.8+
- PyMuPDF
```bash
pip3 install pymupdf
```
## Basic use
```bash
# print to terminal
python3 extract_pdf_annotations.py yourfile.pdf

# multiple files, save as JSON
python3 extract_pdf_annotations.py *.pdf -o annotations.json

# or CSV / plain text
python3 extract_pdf_annotations.py *.pdf -o annotations.csv
python3 extract_pdf_annotations.py *.pdf -o annotations.txt
```

## Limitations
Beware this script will only work well if the PDF and its highlights/notes are digitally-readable, not with simply scanned PDFs. You can run your scans through OCR (e.g. ocrmypdf) first to add a text layer before annotating.

## TODO
- [ ] Doesn't work with () or [] in filename.