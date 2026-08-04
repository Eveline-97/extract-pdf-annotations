#!/usr/bin/env python3
"""
extract_pdf_annotations.py

Extracts all annotations (highlights, notes/sticky notes, underlines,
strikeouts, free text, etc.) from one or more PDF files, including:

  - page number
  - annotation type
  - highlight/annotation color
  - author
  - creation date / modification date
  - the note text (for sticky notes / free text annotations)
  - the underlying document text covered by the annotation
    (for highlights, underlines, strikeouts, squiggly underlines)

Unlike the old AppleScript approach, this reads the annotation objects
directly out of the PDF file structure (via PyMuPDF), so it does not
need Preview to be open, does not use UI scripting, and will not break
when macOS or Preview's UI changes.

Usage:
    python3 extract_pdf_annotations.py file1.pdf [file2.pdf ...] [-o output.json|.csv|.txt]

    -o/--output   Output file. Format is inferred from the extension
                  (.json, .csv, or .txt). Defaults to annotations printed
                  as text to stdout.
    -f/--force    (kept for CLI compatibility with the old script; currently
                  always re-extracts, since reading annotations is fast and
                  has no meaningful "staleness" the way UI scraping did)

Requires: pip install pymupdf --break-system-packages
"""

import argparse
import csv
import json
import sys
from pathlib import Path

try:
    import fitz  # PyMuPDF
except ImportError:
    sys.exit(
        "PyMuPDF is required. Install it with:\n"
        "    pip3 install pymupdf --break-system-packages\n"
        "(or `pip3 install pymupdf` inside a virtualenv)"
    )

# Maps PyMuPDF annotation type names to the categories the old script cared about
TEXT_MARKUP_TYPES = {"Highlight", "Underline", "StrikeOut", "Squiggly"}
NOTE_TYPES = {"Text", "FreeText"}


def color_to_name_and_hex(color_tuple):
    """Convert an (r, g, b) 0-1 float tuple to a hex string and a rough name."""
    if not color_tuple:
        return None, None
    r, g, b = [round(c * 255) for c in color_tuple[:3]]
    hex_code = f"#{r:02X}{g:02X}{b:02X}"

    # Rough, human-friendly naming for the common highlighter colors
    named = {
        (255, 255, 0): "Yellow",
        (0, 255, 0): "Green",
        (0, 255, 255): "Blue",
        (255, 0, 255): "Pink",
        (255, 165, 0): "Orange",
        (255, 0, 0): "Red",
        (128, 0, 128): "Purple",
    }
    closest = min(
        named.items(),
        key=lambda kv: sum((a - b) ** 2 for a, b in zip(kv[0], (r, g, b))),
    )
    # Only trust the "closest" guess if it's reasonably close
    dist = sum((a - b) ** 2 for a, b in zip(closest[0], (r, g, b)))
    name = closest[1] if dist < 4000 else None
    return name, hex_code


def get_covered_text(page, annot):
    """
    For markup annotations (highlight/underline/strikeout/squiggly),
    extract the actual document text that lies under the annotation's
    quad points.
    """
    try:
        quads = annot.vertices
        if not quads:
            return ""
        # vertices come as flat list of points, 4 per quad (for markup annots)
        text_parts = []
        for i in range(0, len(quads), 4):
            quad_points = quads[i:i + 4]
            if len(quad_points) < 4:
                continue
            xs = [p[0] for p in quad_points]
            ys = [p[1] for p in quad_points]
            rect = fitz.Rect(min(xs), min(ys), max(xs), max(ys))
            snippet = page.get_textbox(rect)
            if snippet:
                text_parts.append(snippet.strip())
        return " ".join(text_parts)
    except Exception:
        return ""


def extract_annotations(pdf_path):
    """Return a list of dicts, one per annotation, for the given PDF."""
    results = []
    doc = fitz.open(pdf_path)

    for page_index in range(len(doc)):
        page = doc[page_index]
        for annot in page.annots() or []:
            info = annot.info  # dict: title (author), content, subject, name, creationDate, modDate
            atype = annot.type[1]  # human-readable type name, e.g. "Highlight", "Text"

            colors = annot.colors or {}
            stroke = colors.get("stroke")
            fill = colors.get("fill")
            color_name, color_hex = color_to_name_and_hex(stroke or fill)

            covered_text = ""
            if atype in TEXT_MARKUP_TYPES:
                covered_text = get_covered_text(page, annot)

            note_text = info.get("content", "") or ""

            results.append({
                "file": str(pdf_path),
                "page": page_index + 1,
                "type": atype,
                "color_name": color_name,
                "color_hex": color_hex,
                "author": info.get("title", "") or "",
                "created": info.get("creationDate", "") or "",
                "modified": info.get("modDate", "") or "",
                "note": note_text.strip(),
                "highlighted_text": covered_text,
            })

    doc.close()
    return results


def write_txt(all_annots, out_path):
    lines = []
    for a in all_annots:
        lines.append(f"File: {a['file']}")
        lines.append(f"Page: {a['page']}")
        lines.append(f"Type: {a['type']}")
        if a["color_name"] or a["color_hex"]:
            color_str = a["color_name"] or ""
            if a["color_hex"]:
                color_str += f" ({a['color_hex']})" if color_str else a["color_hex"]
            lines.append(f"Color: {color_str}")
        lines.append(f"Author: {a['author']}")
        lines.append(f"Created: {a['created']}")
        lines.append(f"Modified: {a['modified']}")
        if a["highlighted_text"]:
            lines.append(f"Highlighted text: {a['highlighted_text']}")
        if a["note"]:
            lines.append(f"Note: {a['note']}")
        lines.append("")  # blank line between entries
    content = "\n".join(lines)
    if out_path:
        Path(out_path).write_text(content, encoding="utf-8")
    else:
        print(content)


def write_csv(all_annots, out_path):
    fieldnames = ["file", "page", "type", "color_name", "color_hex",
                  "author", "created", "modified", "note", "highlighted_text"]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for a in all_annots:
            writer.writerow(a)


def write_json(all_annots, out_path):
    content = json.dumps(all_annots, indent=2, ensure_ascii=False)
    if out_path:
        Path(out_path).write_text(content, encoding="utf-8")
    else:
        print(content)


def main():
    parser = argparse.ArgumentParser(
        description="Extract highlights and notes (with underlying text) from PDF files."
    )
    parser.add_argument("pdfs", nargs="+", help="PDF file(s) to process")
    parser.add_argument("-o", "--output", help="Output file (.json, .csv, or .txt). "
                                                 "If omitted, prints text to stdout.")
    parser.add_argument("-f", "--force", action="store_true",
                         help="Kept for compatibility with the old script; no-op here.")
    args = parser.parse_args()

    all_annots = []
    for pdf in args.pdfs:
        pdf_path = Path(pdf)
        if not pdf_path.exists():
            print(f"Skipping (not found): {pdf}", file=sys.stderr)
            continue
        print(f"Extracting annotations from: {pdf_path.name}", file=sys.stderr)
        all_annots.extend(extract_annotations(pdf_path))

    if not all_annots:
        print("No annotations found.", file=sys.stderr)

    if args.output:
        ext = Path(args.output).suffix.lower()
        if ext == ".json":
            write_json(all_annots, args.output)
        elif ext == ".csv":
            write_csv(all_annots, args.output)
        else:
            write_txt(all_annots, args.output)
        print(f"Wrote {len(all_annots)} annotation(s) to {args.output}", file=sys.stderr)
    else:
        write_txt(all_annots, None)


if __name__ == "__main__":
    main()