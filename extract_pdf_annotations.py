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

The script reads the annotation objects directly out of the PDF file structure (via PyMuPDF).

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


DEFAULT_OUTPUT_DIR = "output"


def resolve_output_path(path_str):
    """
    If path_str is a bare filename (no folder component), place it inside
    the default 'output/' folder (created if needed). If it already
    includes a folder (relative or absolute), respect it exactly as given
    (still creating any missing parent folders).
    """
    p = Path(path_str)
    if p.parent == Path("."):
        target_dir = Path(DEFAULT_OUTPUT_DIR)
        target_dir.mkdir(parents=True, exist_ok=True)
        return target_dir / p.name
    else:
        p.parent.mkdir(parents=True, exist_ok=True)
        return p


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


def extract_annotations(pdf_path, doc=None):
    """Return a list of dicts, one per annotation, for the given PDF."""
    results = []
    close_when_done = doc is None
    if doc is None:
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

    if close_when_done:
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


def parse_pdf_date(pdf_date):
    """Convert a raw PDF date string like 'D:20240115093000+01'00'' to 'dd.mm.yyyy'.
    Falls back to the raw string if it can't be parsed."""
    if not pdf_date:
        return ""
    s = pdf_date[2:] if pdf_date.startswith("D:") else pdf_date
    if len(s) >= 8 and s[:8].isdigit():
        yyyy, mm, dd = s[0:4], s[4:6], s[6:8]
        return f"{dd}.{mm}.{yyyy}"
    return pdf_date


def build_toc_sections(doc):
    """Return a list of dicts describing each TOC entry with the page-range
    of content it owns: [{level, title, start_page, end_page}, ...]
    end_page is exclusive. Pages are 1-indexed to match annotation['page']."""
    toc = doc.get_toc(simple=True)  # [[level, title, page], ...]
    sections = []
    for i, (level, title, page) in enumerate(toc):
        end_page = toc[i + 1][2] if i + 1 < len(toc) else doc.page_count + 1
        sections.append({
            "level": level,
            "title": title,
            "start_page": page,
            "end_page": end_page,
        })
    return sections


def annot_to_markdown_block(a):
    """Render a single annotation as a markdown block."""
    lines = []
    if a["type"] in TEXT_MARKUP_TYPES:
        quoted = a["highlighted_text"] or "*(no text captured)*"
        lines.append(f'> "{quoted}" (page {a["page"]})')
        meta_parts = []
        if a["color_name"] or a["color_hex"]:
            color_str = a["color_name"] or ""
            if a["color_hex"]:
                color_str += f" ({a['color_hex']})" if color_str else a["color_hex"]
            meta_parts.append(color_str)
        if a["author"]:
            meta_parts.append(a["author"])
        if a["created"]:
            meta_parts.append(f"created {parse_pdf_date(a['created'])}")
        if a["modified"]:
            meta_parts.append(f"modified {parse_pdf_date(a['modified'])}")
        if meta_parts:
            lines.append(f"*{' · '.join(meta_parts)}*")
        # Extra note text attached to a highlight (rare, but possible)
        if a["note"]:
            lines.append(f"\n📝 {a['note']}")
    else:
        # Sticky note / free text annotation
        lines.append(f'**📝 Note** (page {a["page"]})')
        lines.append(f'> {a["note"] or "*(empty note)*"}')
        meta_parts = []
        if a["author"]:
            meta_parts.append(a["author"])
        if a["created"]:
            meta_parts.append(f"created {parse_pdf_date(a['created'])}")
        if a["modified"]:
            meta_parts.append(f"modified {parse_pdf_date(a['modified'])}")
        if meta_parts:
            lines.append(f"*{' · '.join(meta_parts)}*")
    return "\n".join(lines)


def write_markdown(annots_by_file, doc_titles, tocs, out_path):
    """
    annots_by_file: dict {file_path: [annotation dicts]}
    doc_titles: dict {file_path: title string}
    tocs: dict {file_path: list of TOC sections (see build_toc_sections)}
    """
    md_lines = []

    for file_path, annots in annots_by_file.items():
        md_lines.append(f"# {doc_titles[file_path]}\n")

        if not annots:
            md_lines.append("*(no annotations found)*\n")
            continue

        sections = tocs.get(file_path) or []

        if not sections:
            # No table of contents available: list annotations flat, in page order
            for a in sorted(annots, key=lambda x: x["page"]):
                md_lines.append(annot_to_markdown_block(a))
                md_lines.append("")
        else:
            used = set()

            # Annotations that fall before the first TOC entry's page
            first_page = sections[0]["start_page"]
            preamble = [a for a in annots if a["page"] < first_page]
            for a in sorted(preamble, key=lambda x: x["page"]):
                md_lines.append(annot_to_markdown_block(a))
                md_lines.append("")
                used.add(id(a))

            for sec in sections:
                heading_level = min(sec["level"] + 1, 6)  # H1 is reserved for file title
                md_lines.append(f"{'#' * heading_level} {sec['title']}\n")
                section_annots = [
                    a for a in annots
                    if sec["start_page"] <= a["page"] < sec["end_page"]
                ]
                if not section_annots:
                    continue
                for a in sorted(section_annots, key=lambda x: x["page"]):
                    md_lines.append(annot_to_markdown_block(a))
                    md_lines.append("")
                    used.add(id(a))

        md_lines.append("")  # blank line between files

    content = "\n".join(md_lines).rstrip() + "\n"
    Path(out_path).write_text(content, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(
        description="Extract highlights and notes (with underlying text) from PDF files."
    )
    parser.add_argument("pdfs", nargs="+", help="PDF file(s) to process")
    parser.add_argument("-o", "--output", help="Output file (.json, .csv, or .txt). "
                                                 "If omitted, prints text to stdout.")
    parser.add_argument("-f", "--force", action="store_true",
                         help="Kept for compatibility with the old script; no-op here.")
    parser.add_argument("--markdown", action="store_true",
                         help="Also save a Markdown file with highlights/notes organized "
                              "under the PDF's chapter/section headings (if a table of "
                              "contents is present).")
    parser.add_argument("--markdown-output",
                         help="Path for the Markdown file (default: annotations.md, or "
                              "<base of -o> + .md if -o was given).")
    args = parser.parse_args()

    all_annots = []
    annots_by_file = {}
    doc_titles = {}
    tocs = {}

    for pdf in args.pdfs:
        pdf_path = Path(pdf)
        if not pdf_path.exists():
            print(f"Skipping (not found): {pdf}", file=sys.stderr)
            continue
        print(f"Extracting annotations from: {pdf_path.name}", file=sys.stderr)

        doc = fitz.open(pdf_path)
        file_annots = extract_annotations(pdf_path, doc=doc)
        all_annots.extend(file_annots)

        if args.markdown:
            key = str(pdf_path)
            annots_by_file[key] = file_annots
            meta_title = (doc.metadata or {}).get("title", "").strip()
            doc_titles[key] = meta_title if meta_title else pdf_path.stem
            tocs[key] = build_toc_sections(doc)

        doc.close()

    if not all_annots:
        print("No annotations found.", file=sys.stderr)

    output_path = resolve_output_path(args.output) if args.output else None

    if args.markdown:
        if args.markdown_output:
            md_path = resolve_output_path(args.markdown_output)
        elif output_path:
            md_path = output_path.with_suffix(".md")
        else:
            md_path = resolve_output_path("annotations.md")
        write_markdown(annots_by_file, doc_titles, tocs, md_path)
        print(f"Wrote Markdown summary to {md_path}", file=sys.stderr)

    if output_path:
        ext = output_path.suffix.lower()
        if ext == ".json":
            write_json(all_annots, output_path)
        elif ext == ".csv":
            write_csv(all_annots, output_path)
        else:
            write_txt(all_annots, output_path)
        print(f"Wrote {len(all_annots)} annotation(s) to {output_path}", file=sys.stderr)
    else:
        write_txt(all_annots, None)


if __name__ == "__main__":
    main()