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

-The script reads the annotation objects directly out of the PDF file structure (via PyMuPDF).

Usage:
    python3 extract_pdf_annotations.py file1.pdf [file2.pdf ...] [-o output.json|.csv|.txt]

    -o/--output   Output file. Format is inferred from the extension
                  (.json, .csv, or .txt). Defaults to annotations printed
                  as text to stdout.
    -f/--force    (kept for CLI compatibility with the old script; currently
                  always re-extracts, since reading annotations is fast and
                  has no meaningful "staleness" the way UI scraping did)

Output location:
    If you pass a bare filename to -o/--output or --markdown-output (e.g.
    "annotations.json", with no folder in the path), the file is written
    inside an "output/" folder in the current directory (created
    automatically if it doesn't exist yet). If you pass a path that
    already includes a folder (e.g. "./annotations.json", "results/x.csv",
    or an absolute path), that exact path is used as given.

Requires: pip install pymupdf --break-system-packages
"""

import argparse
import colorsys
import csv
import json
import sys
from pathlib import Path

try:
    import pymupdf as fitz  # PyMuPDF
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
    """
    Convert an (r, g, b) 0-1 float tuple to a hex string and color name
    """
    if not color_tuple:
        return None, None
    r, g, b = [round(c * 255) for c in color_tuple[:3]]
    hex_code = f"#{r:02X}{g:02X}{b:02X}"

    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    hue_deg = h * 360

    if v < 0.18:
        name = "Black"
    elif s < 0.12:
        name = "White" if v > 0.85 else "Gray"
    elif hue_deg < 15 or hue_deg >= 345:
        name = "Red"
    elif hue_deg < 35:
        name = "Orange"
    elif hue_deg < 70:
        name = "Yellow"  # Preview's yellow sits around hue 35-50
    elif hue_deg < 170:
        name = "Green"
    elif hue_deg < 200:
        name = "Cyan"
    elif hue_deg < 250:
        name = "Blue"
    elif hue_deg < 290:
        name = "Purple"
    else:
        name = "Pink"

    return name, hex_code


def get_quad_rects(annot):
    """Return a list of fitz.Rect, one per quad, for a markup annotation."""
    quads = annot.vertices
    if not quads:
        return []
    rects = []
    for i in range(0, len(quads), 4):
        quad_points = quads[i:i + 4]
        if len(quad_points) < 4:
            continue
        xs = [p[0] for p in quad_points]
        ys = [p[1] for p in quad_points]
        rects.append(fitz.Rect(min(xs), min(ys), max(xs), max(ys)))
    return rects


def get_covered_text(page, annot):
    """
    For markup annotations (highlight/underline/strikeout/squiggly),
    extract the actual document text that lies under the annotation's
    quad points.
    """
    try:
        rects = get_quad_rects(annot)
        text_parts = []
        for rect in rects:
            snippet = page.get_textbox(rect)
            if snippet:
                text_parts.append(snippet.strip())
        return " ".join(t for t in text_parts if t)
    except Exception:
        return ""


def get_annot_bbox(annot):
    """Overall bounding box (x0, y0, x1, y1) of an annotation, from its
    quads if it's a markup annotation, else from its own rect (notes)."""
    rects = get_quad_rects(annot)
    if rects:
        return (
            min(r.x0 for r in rects), min(r.y0 for r in rects),
            max(r.x1 for r in rects), max(r.y1 for r in rects),
        )
    r = annot.rect
    return (r.x0, r.y0, r.x1, r.y1)


def find_block_no(blocks, bbox):
    """Which text block (paragraph, roughly) a bbox mostly overlaps, using
    page.get_text('blocks') output. Returns -1 if no block clearly overlaps
    (e.g. annotation sits in a margin or over an image)."""
    ann_rect = fitz.Rect(bbox)
    best_no, best_area = -1, 0.0
    for b in blocks:
        block_rect = fitz.Rect(b[0], b[1], b[2], b[3])
        inter = ann_rect & block_rect
        if not inter.is_empty:
            area = inter.width * inter.height
            if area > best_area:
                best_area, best_no = area, b[5]
    return best_no


def compute_gap_text(page, bbox_a, bbox_b):
    """
    Heuristic: return whatever page text lies strictly between two
    annotation bounding boxes (bbox_a assumed to come first in reading
    order), so callers can tell whether real content was skipped between
    them. Handles both "same line" (gap is horizontal) and "different
    line" (gap spans full page width) cases.
    """
    ax0, ay0, ax1, ay1 = bbox_a
    bx0, by0, bx1, by1 = bbox_b
    vertical_overlap = min(ay1, by1) - max(ay0, by0)
    line_height = max(ay1 - ay0, by1 - by0, 1)

    if vertical_overlap > 0.3 * line_height:
        # Roughly the same line: gap is the horizontal space between them
        rect = fitz.Rect(min(ax1, bx1), min(ay0, by0), max(ax0, bx0), max(ay1, by1))
    else:
        # Different lines: gap is everything between the bottom of the
        # first and the top of the second, full page width
        rect = fitz.Rect(0, min(ay1, by1), page.rect.width, max(ay0, by0))

    if rect.is_empty or rect.width <= 0 or rect.height <= 0:
        return ""
    try:
        return page.get_textbox(rect).strip()
    except Exception:
        return ""


def extract_annotations(pdf_path, doc=None):
    """
    Return a list of dicts, one per annotation, for the given PDF.
    Each dict also carries internal (underscore-prefixed) fields used
    only for Markdown paragraph-grouping: "_group_key" (annotations that
    belong together, e.g. same page/paragraph/author) and
    "_gap_before" (True if real page text was skipped between this
    annotation and the previous one in its group). These are stripped
    before writing JSON/CSV/TXT.
    """
    results = []
    close_when_done = doc is None
    if doc is None:
        doc = fitz.open(pdf_path)

    for page_index in range(len(doc)):
        page = doc[page_index]
        blocks = page.get_text("blocks")
        page_records = []

        for annot in page.annots() or []:
            info = annot.info
            atype = annot.type[1]
            colors = annot.colors or {}
            stroke = colors.get("stroke")
            fill = colors.get("fill")
            color_name, color_hex = color_to_name_and_hex(stroke or fill)
            bbox = get_annot_bbox(annot)
            is_markup = atype in TEXT_MARKUP_TYPES

            covered_text = get_covered_text(page, annot) if is_markup else ""
            block_no = find_block_no(blocks, bbox) if is_markup else -1

            page_records.append({
                "file": str(pdf_path),
                "page": page_index + 1,
                "type": atype,
                "color_name": color_name,
                "color_hex": color_hex,
                "author": info.get("title", "") or "",
                "created": info.get("creationDate", "") or "",
                "modified": info.get("modDate", "") or "",
                "note": (info.get("content", "") or "").strip(),
                "highlighted_text": covered_text,
                "_bbox": bbox,
                "_block_no": block_no,
                "_is_markup": is_markup,
            })

        # Group markup annotations on this page by (block, author) so we
        # can later merge fragments of the same paragraph in Markdown.
        groups = {}
        for rec in page_records:
            if not rec["_is_markup"]:
                rec["_group_key"] = None
                rec["_gap_before"] = False
                continue
            key = (page_index, rec["_block_no"], rec["author"]) \
                if rec["_block_no"] != -1 else (page_index, "solo", id(rec))
            groups.setdefault(key, []).append(rec)

        for key, members in groups.items():
            members.sort(key=lambda r: (r["_bbox"][1], r["_bbox"][0]))
            for i, rec in enumerate(members):
                rec["_group_key"] = key
                if i == 0:
                    rec["_gap_before"] = False
                else:
                    gap_text = compute_gap_text(page, members[i - 1]["_bbox"], rec["_bbox"])
                    rec["_gap_before"] = bool(gap_text)

        results.extend(page_records)

    if close_when_done:
        doc.close()
    return results


def strip_internal_fields(a):
    return {k: v for k, v in a.items() if not k.startswith("_")}


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
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for a in all_annots:
            writer.writerow(a)


def write_json(all_annots, out_path):
    public = [strip_internal_fields(a) for a in all_annots]
    content = json.dumps(public, indent=2, ensure_ascii=False)
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


def merge_annotation_groups(annots):
    """
    Group markup annotations by their precomputed _group_key (paragraph +
    author), in reading order, and merge each group into one combined
    dict ready for markdown rendering: {page, color_name(s), author,
    created, modified, text (with " [...] " where content was skipped)}.
    Note-type annotations pass through unchanged, one per entry.
    """
    markup = [a for a in annots if a["type"] in TEXT_MARKUP_TYPES]
    notes = [a for a in annots if a["type"] not in TEXT_MARKUP_TYPES]

    groups = {}
    order = []
    for a in markup:
        key = a["_group_key"]
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(a)

    merged = []
    for key in order:
        members = groups[key]  # already in reading order from extraction
        text_parts = []
        for i, m in enumerate(members):
            frag = m["highlighted_text"] or "(no text captured)"
            if i > 0:
                text_parts.append("\\[...]" if m["_gap_before"] else "")
            text_parts.append(frag)
        combined_text = " ".join(t for t in text_parts if t)

        colors = []
        for m in members:
            if m["color_name"] and m["color_name"] not in colors:
                colors.append(m["color_name"])

        merged.append({
            "type": "Highlight",
            "page": members[0]["page"],
            "color_name": "/".join(colors) if colors else None,
            "author": members[0]["author"],
            "created": min((m["created"] for m in members if m["created"]), default=""),
            "modified": max((m["modified"] for m in members if m["modified"]), default=""),
            "text": combined_text,
        })

    for n in notes:
        merged.append({
            "type": n["type"],
            "page": n["page"],
            "color_name": n["color_name"],
            "author": n["author"],
            "created": n["created"],
            "modified": n["modified"],
            "text": n["note"],
        })

    return merged


def annot_to_markdown_block(a):
    """Render one merged annotation (highlight-group or note) as markdown,
    without blockquotes."""
    is_highlight = a["type"] in TEXT_MARKUP_TYPES
    meta_parts = [a["author"]] if a["author"] else []
    if is_highlight and a["color_name"]:
        meta_parts.insert(0, a["color_name"])
    if a["created"]:
        meta_parts.append(f"created {parse_pdf_date(a['created'])}")
    if a["modified"]:
        meta_parts.append(f"modified {parse_pdf_date(a['modified'])}")
    meta_line = f"*{' · '.join(meta_parts)}*" if meta_parts else ""

    if a["type"] in TEXT_MARKUP_TYPES:
        header = f"**Page {a['page']}**"
        body = f'"{a["text"]}"' if a["text"] else "*(no text captured)*"
        lines = [header, body]
    else:
        header = f'**📝 Note — page {a["page"]}**'
        body = a["text"] or "*(empty note)*"
        lines = [header, body]

    if meta_line:
        lines.append(meta_line)
    return "\n".join(lines)


def write_markdown(annots_by_file, doc_titles, tocs, out_path):
    """
    annots_by_file: dict {file_path: [annotation dicts]}
    doc_titles: dict {file_path: title string}
    tocs: dict {file_path: list of TOC sections (see build_toc_sections)}
    """
    md_lines = []

    for file_path, raw_annots in annots_by_file.items():
        md_lines.append(f"# {doc_titles[file_path]}\n")

        if not raw_annots:
            md_lines.append("*(no annotations found)*\n")
            continue

        annots = merge_annotation_groups(raw_annots)
        sections = tocs.get(file_path) or []

        if not sections:
            for a in sorted(annots, key=lambda x: x["page"]):
                md_lines.append(annot_to_markdown_block(a))
                md_lines.append("")
        else:
            first_page = sections[0]["start_page"]
            preamble = [a for a in annots if a["page"] < first_page]
            for a in sorted(preamble, key=lambda x: x["page"]):
                md_lines.append(annot_to_markdown_block(a))
                md_lines.append("")

            for sec in sections:
                heading_level = min(sec["level"] + 1, 6)
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

        md_lines.append("")

    content = "\n".join(md_lines).rstrip() + "\n"
    Path(out_path).write_text(content, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(
        description="Extract highlights and notes (with underlying text) from PDF files."
    )
    parser.add_argument("pdfs", nargs="*", help="PDF file(s) to process")
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
    parser.add_argument("--input-dir",
                         help="Instead of listing PDFs as arguments, scan this folder for "
                              "*.pdf files directly in Python. Useful if filenames contain "
                              "characters like ( ) [ ] that are awkward to quote correctly "
                              "in your shell.")
    args = parser.parse_args()

    pdf_args = list(args.pdfs)
    if args.input_dir:
        found = sorted(str(p) for p in Path(args.input_dir).glob("*.pdf"))
        if not found:
            print(f"No .pdf files found in {args.input_dir}", file=sys.stderr)
        pdf_args.extend(found)

    if not pdf_args:
        parser.error("No PDF files given. Pass PDF path(s) or use --input-dir.")

    all_annots = []
    annots_by_file = {}
    doc_titles = {}
    tocs = {}

    for pdf in pdf_args:
        pdf_path = Path(pdf)
        if not pdf_path.exists():
            print(f"Skipping (not found): {pdf}", file=sys.stderr)
            if "(" in pdf or ")" in pdf or pdf.startswith("[") or pdf.endswith("]"):
                print(
                    "  Hint: your shell may have mangled this filename because it "
                    "contains ( ) or [ ]. Wrap the whole path in quotes, e.g.\n"
                    f'  python3 extract_pdf_annotations.py "{pdf}"\n'
                    "  ...or use --input-dir to scan a folder instead of listing files.",
                    file=sys.stderr,
                )
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