# Extract PDF Annotations

## Requirements
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

## Advanced use
Using the `--markdown` flag, besides the above functionality, the script creates a markdown file with all your highlights and notes, organised within the chapter titles and subtitles. 

```bash
python3 extract_pdf_annotations.py yourfile.pdf --markdown
# writes annotations.md alongside whatever else you asked for

python3 extract_pdf_annotations.py yourfile.pdf -o data.json --markdown
# writes data.json AND data.md

python3 extract_pdf_annotations.py yourfile.pdf --markdown --markdown-output notes.md
# custom markdown filename
```

## Limitations
Beware this script will only work well if the PDF and its highlights/notes are digitally-readable, not with simply scanned PDFs. You can run your scans through OCR (e.g. ocrmypdf) first to add a text layer before annotating.

## Credits
Built with the help of Claude (Anthropic), based on a workflow originally
inspired by a 2014 [AppleScript tool](https://github.com/JensLincke/PDFMarkupAndNotesExtractor) by Jens Lincke.