"""The motor report as a Microsoft Word document — the default export.

User, 2026-09-09: *"репорт лучше выдавать в формате doc"*, and again *"выводи
всё-таки в doc формате"*.  He edits the document before it goes to a client —
a paragraph rewritten for that customer, a section dropped, the company's own
letterhead put on it — and Word makes its own PDF at the end of that.  A PDF is
the last step of a document, not the first, so ``format=docx`` is what the route
serves unless the caller asks otherwise.

SAME DOCUMENT, TWO RENDERERS.  Every number, every row and every sentence here
comes out of :mod:`motor_ai_sim.report`: :func:`~motor_ai_sim.report
.gather_report_data` reads the stores once, the ``*_rows`` functions turn that
into cells, and the narrative paragraphs are module constants both renderers
quote.  This file owns exactly one thing — how it looks in Word.  That is the
only way a .docx and a .pdf of one machine cannot end up quoting two different
torques or two different caveats.

NOTHING IS SOLVED HERE either.  Same contract as the PDF: read the stores, print
what is in them, say "not solved" where there is nothing, never start a solve.

WHY LANDSCAPE.  The comparison tables are one column per duty on top of a wide
label column; on portrait A4 a machine with four duties wraps every label onto
three lines.  The whole document is one landscape section — one section, so the
user's own header, footer and page numbering apply to all of it when he adds
them, and so that Word's navigation pane (which the Heading 1/2 styles below
feed) is the way he moves through it rather than scrolling.
"""
from __future__ import annotations

import io
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from motor_ai_sim import report as R

log = logging.getLogger(__name__)

#: Body font.  Calibri is on every Windows and every Office install, it has the
#: Cyrillic a duty name may be written in (routes/family._DUTY_NAME_RE lets the
#: user type them off a Russian keyboard), and it is what a Slovenian engineer's
#: Word already opens with — a report the client has to re-font is a report he
#: edits badly.  Every run's font is set EXPLICITLY, ``w:eastAsia`` included,
#: because python-docx writes only ``w:ascii``/``w:hAnsi`` by default and Word
#: then picks its own theme font for anything outside Latin-1.
FONT = "Calibri"

#: The palette is the PDF's, so the two documents look like one family.
NAVY = "1F4E79"
GREY = "F2F2F2"
NOTE = "6E6E6E"
WARN = "B3261E"
AMBER = "B7791F"


def _ink(level: str) -> str:
    """`report.WARN_INK` without its leading '#': python-docx wants bare hex."""
    from motor_ai_sim import report as _R
    return str(_R.WARN_INK.get(level, "#" + AMBER)).lstrip("#")

#: Picture width — THE WHOLE TEXT WIDTH.  A landscape A4 with 1.5 cm margins
#: leaves 26.7 cm, and every figure now takes all of it (user 2026-09-14:
#: *"рисунки делай побольше, раздвигай на всю ширину страницы, для всех, чтобы
#: одинаково было"*).  At the old 16.5 cm a paired figure was two 8 cm pictures
#: with 10 cm of margin beside them, and half of each of those was a legend.
PAGE_TEXT_CM = 26.7
PIC_CM = PAGE_TEXT_CM

#: …and no taller than this.  Landscape A4 with 1.5 cm margins is 18 cm of live
#: height; 13.5 cm leaves the caption, a heading and a short table on the same
#: sheet — at 14.5 the rotor-inertia table had a page to itself with 18 % of it
#: used (CS-8) — and it is loose enough that no PAIR is ever narrowed by it: the
#: tallest pair half is 12.6 cm, so a pair always spans the full text width.
MAX_PIC_CM = 13.5


# ---------------------------------------------------------------------------
# python-docx plumbing
# ---------------------------------------------------------------------------


def _qn(tag: str):
    from docx.oxml.ns import qn
    return qn(tag)


def _set_font(run, *, size: Optional[float] = None, bold: Optional[bool] = None,
              italic: Optional[bool] = None, color: Optional[str] = None,
              name: str = FONT):
    """One run, fully specified.

    ``w:eastAsia`` is set by hand: python-docx's ``run.font.name`` writes
    ``w:ascii`` and ``w:hAnsi`` only, and Word falls back to the theme's East
    Asian font for every codepoint it decides is not Latin — which on some
    installs swallows Cyrillic duty names into a font that does not match the
    rest of the line.
    """
    from docx.shared import Pt

    run.font.name = name
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(_qn("w:rFonts"))
    if rfonts is None:
        from docx.oxml import OxmlElement
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    for attr in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
        rfonts.set(_qn(attr), name)
    if size is not None:
        run.font.size = Pt(size)
    if bold is not None:
        run.font.bold = bold
    if italic is not None:
        run.font.italic = italic
    if color is not None:
        from docx.shared import RGBColor
        run.font.color.rgb = RGBColor.from_string(color)
    return run


def _add_field(par, field: str, cached_text: str = "1") -> None:
    """One live Word field (``PAGE``/``NUMPAGES``) as a run.

    python-docx has no helper for a field code, so this writes the raw OOXML
    Word itself writes: begin/instrText/separate/end, each its own run — the
    form Word's own "Insert > Page Number" produces, so it is not a literal
    string that happens to look like a field, and Word updates it like any
    other one (print preview, F9, opening the file) rather than leaving it
    frozen at whatever this process last saw.  ``cached_text`` is the LAST
    COMPUTED VALUE a field carries between the ``separate`` and ``end``
    chars — what a viewer that never re-lays-out the document (a thumbnailer,
    a diff tool) shows instead of nothing; Word overwrites it the moment it
    opens the file.
    """
    from docx.oxml import OxmlElement

    r1 = par.add_run()
    _set_font(r1, size=8.0, color=NOTE)
    fld_begin = OxmlElement("w:fldChar")
    fld_begin.set(_qn("w:fldCharType"), "begin")
    r1._r.append(fld_begin)

    r2 = par.add_run()
    _set_font(r2, size=8.0, color=NOTE)
    instr = OxmlElement("w:instrText")
    instr.set(_qn("xml:space"), "preserve")
    instr.text = " %s " % field
    r2._r.append(instr)

    r3 = par.add_run()
    _set_font(r3, size=8.0, color=NOTE)
    fld_sep = OxmlElement("w:fldChar")
    fld_sep.set(_qn("w:fldCharType"), "separate")
    r3._r.append(fld_sep)

    _set_font(par.add_run(cached_text), size=8.0, color=NOTE)

    r5 = par.add_run()
    _set_font(r5, size=8.0, color=NOTE)
    fld_end = OxmlElement("w:fldChar")
    fld_end.set(_qn("w:fldCharType"), "end")
    r5._r.append(fld_end)


def _add_footer(doc, stamp: str) -> None:
    """The PAGE-NUMBERED FOOTER the PDF has always carried on every page and
    the .docx, until now, had NONE (B1, L13 server audit round 4): unzipped,
    the delivered .docx had zero ``<w:footerReference>`` parts and zero literal
    "page N" text anywhere, so a client opening it in real Word saw no running
    header, no confidentiality line and no page numbers at all — on a document
    that, reflowed, is 1.5x the PDF's page count.

    The PDF's own footer is ``report._decorate``: ``"{die} · {cfg}"`` on the
    left, ``"page N"`` on the right.  This is the SAME left-hand stamp; the
    right-hand side is ``"page {PAGE} of {NUMPAGES}"`` rather than a bare page
    number, because Word's own pagination (which a client's fonts, printer
    driver and page setup all move) is never the PDF's 28 — see the module
    docstring's "SAME DOCUMENT, TWO RENDERERS" note.  The two documents are
    cross-referenced by FIGURE and TABLE number, which are already identical,
    not by page.

    One call, because the whole document is one landscape section (see the
    module docstring, "WHY LANDSCAPE") — one footer therefore already applies
    to every page there is, and a second section would only give Word a seam
    to reset the numbering at.
    """
    from docx.enum.text import WD_TAB_ALIGNMENT
    from docx.shared import Cm, Pt

    sec = doc.sections[0]
    sec.footer.is_linked_to_previous = False
    par = sec.footer.paragraphs[0] if sec.footer.paragraphs \
        else sec.footer.add_paragraph()
    for run in list(par.runs):
        run._r.getparent().remove(run._r)
    par.paragraph_format.space_before = Pt(2)
    par.paragraph_format.tab_stops.add_tab_stop(
        Cm(PAGE_TEXT_CM), WD_TAB_ALIGNMENT.RIGHT)
    _set_font(par.add_run(stamp), size=8.0, color=NOTE)
    _set_font(par.add_run("\tpage "), size=8.0, color=NOTE)
    _add_field(par, "PAGE")
    _set_font(par.add_run(" of "), size=8.0, color=NOTE)
    _add_field(par, "NUMPAGES")


_TAG = re.compile(r"<[^>]+>")


def _rich(par, text: str, **kw):
    """Add ``text`` to ``par``, honouring the ``<b>`` runs the shared strings use.

    The narrative constants in :mod:`report` carry reportlab's mini-markup —
    ``<b>Thermal</b> — steady state…`` — because that is the markup the PDF
    parses.  Rather than keep two copies of five paragraphs, the bold spans are
    read here and everything else is dropped: a ``<font color=…>`` in a shared
    string becomes a plain run, which is what it should be in Word anyway (the
    warnings section colours its own runs from the level, not from markup).
    """
    pos, bold = 0, False
    for m in re.finditer(r"</?b>|<[^>]+>", text or ""):
        chunk = text[pos:m.start()]
        if chunk:
            _set_font(par.add_run(chunk), bold=bold or kw.get("bold"), **{
                k: v for k, v in kw.items() if k != "bold"})
        if m.group(0) == "<b>":
            bold = True
        elif m.group(0) == "</b>":
            bold = False
        pos = m.end()
    tail = (text or "")[pos:]
    if tail:
        _set_font(par.add_run(tail), bold=bold or kw.get("bold"), **{
            k: v for k, v in kw.items() if k != "bold"})
    return par


#: EVERY run of text outside a table is scaled by this (user 2026-09-11:
#: "увеличь весь шрифт, не только в таблицах, пропорционально").  The tables
#: had already been raised by hand twice at the user's request and sit where
#: they should; this brings the paragraphs, captions and headings up to meet
#: them without retyping forty call sites.  9 pt body -> ~10, 15 pt heading ->
#: ~17, 22 pt title -> ~25.  Tables are NOT scaled here — `_table` sets its own
#: size and is left exactly where the user last saw it.
TEXT_SCALE = 1.12


def _p(doc, text: str = "", *, size: float = 9.0, bold: bool = False,
       italic: bool = False, color: Optional[str] = None,
       space_after: float = 4.0, indent_cm: float = 0.0):
    from docx.shared import Cm, Pt

    size = round(size * TEXT_SCALE, 1)
    par = doc.add_paragraph()
    par.paragraph_format.space_after = Pt(space_after)
    par.paragraph_format.space_before = Pt(0)
    if indent_cm:
        par.paragraph_format.left_indent = Cm(indent_cm)
    if text:
        _rich(par, text, size=size, bold=bold, italic=italic, color=color)
    return par


def _bullet(doc, text: str, *, size: float = 9.0):
    return _p(doc, "• " + text, size=size, space_after=2.0, indent_cm=0.4)


def _caption(doc, text: str):
    """A figure caption: italic 8.5 pt directly under its picture."""
    return _p(doc, text, size=9.5, italic=True, color=NOTE, space_after=8.0)


def _h(doc, text: str, level: int = 1, keep_next: bool = True):
    """A REAL Word heading, so the navigation pane works and a table of contents
    the user inserts himself finds every section.

    ``keep_next`` — A HEADING NEVER ENDS A PAGE (BT-10, 2026-09-16).  The
    "Critical speeds" heading and the header row of the table under it sat
    alone at the foot of page 36 of the delivered document, with the three data
    rows (and a repeated header) on page 37.  The style a user's template
    supplies may or may not carry keep-with-next, so it is set here, on the
    paragraph — the same explicit-over-style rule every run in this module
    follows — and :func:`_table` drags its own first data row after its header.
    """
    from docx.shared import Pt

    par = doc.add_heading("", level=level)
    par.paragraph_format.space_before = Pt(10 if level == 1 else 8)
    par.paragraph_format.space_after = Pt(4)
    par.paragraph_format.keep_with_next = bool(keep_next)
    _set_font(par.add_run(text),
              size=round((15 if level == 1 else 11.5) * TEXT_SCALE, 1),
              bold=True, color=NAVY)
    return par


def _shade(cell, hex_fill: str):
    from docx.oxml import OxmlElement

    shd = OxmlElement("w:shd")
    shd.set(_qn("w:val"), "clear")
    shd.set(_qn("w:color"), "auto")
    shd.set(_qn("w:fill"), hex_fill)
    cell._tc.get_or_add_tcPr().append(shd)


_NUM = re.compile(r"^[\s−+-]*[\d][\d\s.,'’]*(?:[eE][+-]?\d+)?\s*[^\s]{0,12}$")


def _looks_numeric(s: str) -> bool:
    """Right-align a cell that is a measurement.

    A column of numbers that is not right-aligned cannot be read down, and the
    unit rides in the same cell here (``12.9 W``), so the test is "starts like a
    number and is short" rather than ``float()``.  An em dash and the word
    ``not solved`` are deliberately NOT numeric: they belong on the left with
    the other words.
    """
    s = (s or "").strip()
    if not s or s in ("—", "-"):
        return False
    return bool(_NUM.match(s))


#: TABLE TYPE IS 9.5 pt, not 8 (user 2026-09-10: "увеличь немного шрифт во всех
#: таблицах, очень уж мелко смотрится").  The document is read on a screen and
#: printed on A4; 8 pt in a shaded grid is at the edge of comfortable for both,
#: and this report is meant to be read rather than skimmed.


def _table(doc, rows: Sequence[Sequence[Any]], *, header: bool = True,   # doc OR a cell
           size: float = 10.5, widths_cm: Optional[Sequence[float]] = None,
           first_col_left: bool = True, keep_together: bool = False,
           keep_next: bool = False):
    """A restrained table: one shaded header band, hairline grid, numbers right.

    Word's own ``Table Grid`` style gives the hairline; everything else is set
    per cell because a style the user's template does not have would come back
    as an unstyled block of text on his machine.
    """
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Cm, Pt

    rows = [list(r) for r in rows]
    if not rows:
        return None
    ncol = max(len(r) for r in rows)
    t = doc.add_table(rows=len(rows), cols=ncol)
    try:
        # `doc` may be a CELL (the side-by-side frame builds the real table
        # inside one), and a cell has no style registry — reach the document's
        # through the part it belongs to.
        _styles = getattr(doc, "styles", None)
        if _styles is None:
            _styles = doc._parent.part.document.styles
        t.style = _styles["Table Grid"]
    except (KeyError, AttributeError):                      # noqa: PERF203
        pass
    t.alignment = WD_TABLE_ALIGNMENT.LEFT
    t.autofit = widths_cm is None
    for i, row in enumerate(rows):
        for j in range(ncol):
            text = "" if j >= len(row) or row[j] is None else str(row[j])
            cell = t.cell(i, j)
            par = cell.paragraphs[0]
            par.paragraph_format.space_after = Pt(0)
            par.paragraph_format.space_before = Pt(0)
            head = header and i == 0
            _set_font(par.add_run(text), size=size, bold=head,
                      color="FFFFFF" if head else None)
            if head:
                _shade(cell, NAVY)
            elif (i - (1 if header else 0)) % 2 == 1:
                _shade(cell, GREY)
            # EVERY value cell right-aligned, words included, header included
            # (user 2026-09-11: "во всех таблицах сделай alignment right").
            # Right-aligning only the numbers left "yes", "thermal" and "—"
            # hugging the left edge of a column whose numbers hugged the right,
            # and a column that reads in two directions is not a column.  The
            # label column stays left: it is the row's name, not its value.
            if not (first_col_left and j == 0):
                par.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            if widths_cm and j < len(widths_cm):
                cell.width = Cm(widths_cm[j])
    # NO ROW STRADDLES A PAGE (`w:cantSplit`, before `tblHeader` — schema
    # order).  Word and LibreOffice split a tall cell across pages cleanly;
    # the viewer the user reads reports in did not, and showed the halves as
    # loose text with the page reduced to "1 1 1 …" (2026-09-13 19:21).  A
    # table still breaks between rows, which every renderer handles.
    from docx.oxml import OxmlElement
    for r in t.rows:
        _trpr = r._tr.get_or_add_trPr()
        _trpr.insert(0, OxmlElement("w:cantSplit"))
    # …AND, WHEN ASKED, NO PAGE BREAK ANYWHERE INSIDE IT (`keepNext` on every
    # row but the last — reviewer 2026-09-14, D16: the "Fit and contacts" table
    # orphaned its last two rows onto the following page).  Only for tables
    # short enough to fit a page; a long one keeps breaking between rows, which
    # is what a long table must do.
    if keep_together:
        for r in t.rows[:-1]:
            for c in r.cells:
                for par in c.paragraphs:
                    par.paragraph_format.keep_with_next = True
    # …AND, WHEN ASKED, THE PARAGRAPH AFTER IT COMES TOO (MJ-8, audit v5): the
    # ring-mode table's note had a page of its own under the table's last two
    # rows, 11 % of a sheet.
    if keep_next:
        for c in t.rows[-1].cells:
            for par in c.paragraphs:
                par.paragraph_format.keep_with_next = True
    if header:
        t.rows[0]._tr.get_or_add_trPr().append(
            _tbl_header_repeat())
        # …AND THE HEADER BAND NEVER STANDS ALONE (BT-10, 2026-09-16): with
        # `keepNext` on its cells the first DATA row comes with it, so a table
        # that will not fit starts on the next page whole instead of leaving a
        # heading and a navy band at the foot of the previous one.
        if len(t.rows) > 1:
            for c in t.rows[0].cells:
                for par in c.paragraphs:
                    par.paragraph_format.keep_with_next = True
    return t


def _tbl_header_repeat():
    """Repeat the header row when a table breaks across pages."""
    from docx.oxml import OxmlElement

    el = OxmlElement("w:tblHeader")
    el.set(_qn("w:val"), "true")
    return el


def _table_with_chart(doc, rows, png: Optional[bytes], *,
                      table_cm: float, chart_cm: float,
                      widths_cm=None, size: float = 10.5) -> bool:
    """A data table with its chart BESIDE it, not under it.

    User 2026-09-11: *"круговую диаграмму всех потерь справа от таблицы — будет
    гораздо наглядней"*.  Word has no float, so the pair goes into one
    borderless 1x2 frame: the real table is built inside the left cell and the
    picture dropped into the right.  Falls back to the plain stacked layout
    when there is no chart, so a run that stored nothing to draw still reads.
    """
    if not png:
        _table(doc, rows, size=size, widths_cm=widths_cm)
        return False
    from docx.shared import Cm

    frame = doc.add_table(rows=1, cols=2)
    frame.autofit = False
    left, right = frame.cell(0, 0), frame.cell(0, 1)
    left.width, right.width = Cm(table_cm), Cm(chart_cm)
    # the data table, built INSIDE the left cell
    _cur = left.paragraphs[0]
    _t = _table(left, rows, size=size, widths_cm=widths_cm)
    if _cur.text == "" and len(left.paragraphs) > 1:
        _cur._element.getparent().remove(_cur._element)
    # …and the chart in the right one
    _p_img = right.paragraphs[0]
    try:
        _p_img.add_run().add_picture(io.BytesIO(png), width=Cm(chart_cm - 0.3))
    except Exception as exc:                                # noqa: BLE001
        log.debug("report_docx: side chart skipped (%s)", exc)
    return True


def _picture(doc, blob: Optional[bytes], cm: float = PIC_CM,
             max_cm: Optional[float] = None) -> bool:
    """Embed one PNG at ``cm`` wide, capped so it and its caption share a page.

    ``False`` when there was nothing to embed — a missing map is a SENTENCE in
    this report, never a blank space.  The height cap is the same rule the PDF
    applies (``_image(..., max_height=PAGE_H * 0.42)``): a full-disc rotor map
    is square, and a square picture at page width is a page tall, which would
    push every caption onto the next sheet.
    """
    from docx.shared import Cm

    if not blob:
        return False
    try:
        w, h = Cm(cm), None
        cap = float(max_cm if max_cm else MAX_PIC_CM)
        try:
            from docx.image.image import Image as _DImage
            im = _DImage.from_blob(blob)
            aspect = float(im.px_height) / float(im.px_width or 1)
            if cm * aspect > cap:
                w = Cm(cap / aspect)
                h = Cm(cap)
        except Exception:                                   # noqa: BLE001
            pass
        doc.add_picture(io.BytesIO(blob), width=w, height=h)
        # THE CAPTION STAYS WITH THE PICTURE (reviewer 2026-09-14, B11).  The
        # torque figure sat at the bottom of one page and its caption alone at
        # the top of the next, immediately above the phase-current figure, so a
        # reader attributed the torque caption to the current chart.  Word's
        # own `keepNext` on the picture's paragraph is the fix, and it costs
        # nothing on a figure that already has room.
        try:
            doc.paragraphs[-1].paragraph_format.keep_with_next = True
        except Exception:                                   # noqa: BLE001
            pass
        return True
    except Exception as exc:                                # noqa: BLE001
        log.debug("report_docx: picture skipped (%s)", exc)
        return False


#: ONE HALF of a paired figure: half the text width, less half the gap between
#: the two.  The pair therefore spans the WHOLE text width, which is what the
#: user asked for on 2026-09-14 — and a chart drawn at the width it is placed
#: at keeps its point sizes 1:1 on the page (see `report.PAIR_CM`).
PAIR_GAP_CM = 0.3
PAIR_CM = round((PAGE_TEXT_CM - PAIR_GAP_CM) / 2.0, 2)


def _picture_pair(doc, left: Optional[bytes], right: Optional[bytes], *,
                  cm: float = PAIR_CM, max_cm: Optional[float] = None) -> int:
    """The two sides of one figure in a single borderless 1x2 row.

    Both halves are embedded at the same width AND the same height, so the pair
    is framed identically even when one side's tick labels made its canvas a
    few pixels wider than the other's (CS-6, audit v5).  With only one side
    stored the picture is drawn ALONE at full width — never an empty cell — and
    the caption names the duty that is missing.  Returns how many pictures were
    embedded.
    """
    from docx.shared import Cm, Pt

    if not (left and right):
        one = left or right
        return 1 if _picture(doc, one, cm=PIC_CM, max_cm=max_cm) else 0
    try:
        from docx.image.image import Image as _DImage
        t = doc.add_table(rows=1, cols=2)
        t.autofit = False
        cap = float(max_cm if max_cm else MAX_PIC_CM)
        # ONE FRAME FOR BOTH HALVES: the taller of the two aspects, so neither
        # side is cropped and the two rectangles are identical.
        _asp = 0.0
        for blob in (left, right):
            try:
                im = _DImage.from_blob(blob)
                _asp = max(_asp, float(im.px_height) / float(im.px_width or 1))
            except Exception:                               # noqa: BLE001
                pass
        _w = float(cm)
        if _asp > 0 and _w * _asp > cap:
            _w = cap / _asp
        _h = _w * _asp if _asp > 0 else None
        for i, blob in enumerate((left, right)):
            cell = t.cell(0, i)
            cell.width = Cm(cm + PAIR_GAP_CM)
            par = cell.paragraphs[0]
            par.paragraph_format.space_after = Pt(0)
            par.paragraph_format.space_before = Pt(0)
            # THE CAPTION STAYS WITH THE PAIR (the same rule `_picture` applies
            # to a single figure): a two-up figure at the foot of a page with
            # its caption alone overleaf is read as the caption of whatever
            # follows it.
            par.paragraph_format.keep_with_next = True
            w = Cm(_w)
            h = Cm(_h) if _h else None
            par.add_run().add_picture(io.BytesIO(blob), width=w, height=h)
        # …and the row itself does not straddle a page break.
        from docx.oxml import OxmlElement
        t.rows[0]._tr.get_or_add_trPr().insert(0, OxmlElement("w:cantSplit"))
        return 2
    except Exception as exc:                                # noqa: BLE001
        log.debug("report_docx: figure pair skipped (%s)", exc)
        return 0


def _pair_fig(doc, D: Dict[str, Any], left: Optional[bytes],
              right: Optional[bytes], what: str, *, numbers: str = "",
              max_cm: Optional[float] = None, map_kind: str = "") -> bool:
    """One paired figure with its caption: the two pictures, then the sentence
    that says what they show and which duty is on which side.

    ``map_kind`` names the stored FIELD the pictures were drawn from, so the
    caption can say when that field and the record its table came from are two
    different solves (reviewer 2026-09-15)."""
    P = D.get("pair") or {}
    if not _picture_pair(doc, left, right, max_cm=max_cm):
        return False
    _caption(doc, "Fig. %d — %s" % (
        R.fig_no(D.get("fig_n")),
        R.pair_caption(what, P.get("left"), P.get("right"),
                       have=(bool(left), bool(right)), numbers=numbers,
                       note=P.get("note") or "", map_kind=map_kind)))
    return True


def _prov(D: Dict[str, Any], caption: str, kind: str) -> str:
    """A single figure's caption with the report duty's provenance sentence."""
    return R.caption_with_provenance(caption,
                                     (D.get("pair") or {}).get("left"), kind)


def _numbered(D: Dict[str, Any]):
    """The document's figure counter, or ``None`` when it does not number its
    figures — see :func:`report.fig_label` (CS-5)."""
    return D.get("fig_n") if ((D.get("pair") or {}).get("right")) else None


def _report_tag(D: Dict[str, Any]) -> str:
    """``duty 'rated 1x9 mm', 562.1 A rms at 14,200 rpm`` — the stamp every
    figure in this document carries (reviewer 2026-09-14, B9).

    The REPORT duty's own point: the mass, inertia and loss charts are drawn
    from the summary the cover is about, not from the picture duty's field.
    """
    em, d_duty = D.get("em") or {}, D.get("d_duty") or {}
    return R._map_tag(str(d_duty.get("name") or "") or None,
                      R._g(em, "rpm") or d_duty.get("rpm"),
                      R._g(em, "I_phase_rms_A") or d_duty.get("current_arms"))


def _cmp_table(doc, header: str, cols: List[Dict[str, Any]],
               rows: List[List[Any]], size: float = 9.5):
    """A comparison table: the quantity on the left, one column per duty.

    The duty names ARE the column headings, and they may be Cyrillic — which is
    the whole reason every run in this module sets its font by hand.
    """
    head = [header] + [c["duty"] for c in cols]
    return _table(doc, [head] + rows, header=True, size=size)


# ---------------------------------------------------------------------------
# The document
# ---------------------------------------------------------------------------


def build_motor_report_docx(*, die: str, cfg: str, die_doc: Dict[str, Any],
                            cfg_doc: Dict[str, Any], duty: Optional[str] = None,
                            slot: Optional[Dict[str, float]] = None,
                            pictures: Optional[str] = None) -> bytes:
    """The whole report as .docx bytes.  Reads only; solves nothing.

    Same arguments and same contract as
    :func:`motor_ai_sim.report.build_motor_report`, and the same sections in the
    same order (ten when a duty carries a cycle -- see `report.section_numbers`) — a reader who has both open must be able to put them side
    by side.
    """
    from docx import Document
    from docx.enum.section import WD_ORIENT
    from docx.shared import Cm, Pt

    from motor_ai_sim import report_progress as _RP

    D = R.gather_report_data(die=die, cfg=cfg, die_doc=die_doc, cfg_doc=cfg_doc,
                             duty=duty, slot=slot, pictures=pictures)
    # Every solver's store has been read; from here the wall clock is figures.
    _RP.stage("records")

    doc = Document()
    # One landscape section: the comparison tables are wide, and one section is
    # what keeps a header or a page number the user adds later on every page.
    # A4, explicitly: python-docx's stock template is US Letter, and a report
    # that prints with a 2 cm strip cut off the bottom on the user's own printer
    # is not a deliverable.
    sec = doc.sections[0]
    sec.orientation = WD_ORIENT.LANDSCAPE
    sec.page_width, sec.page_height = Cm(29.7), Cm(21.0)
    sec.left_margin = sec.right_margin = Cm(1.5)
    sec.top_margin = sec.bottom_margin = Cm(1.5)

    # THE SAME FOOTER THE PDF HAS ON EVERY PAGE, on every page here too (B1,
    # L13 server audit round 4) — see `_add_footer`.
    _add_footer(doc, "%s · %s" % (D["die"], D["cfg"]))

    normal = doc.styles["Normal"]
    normal.font.name = FONT
    normal.font.size = Pt(9)
    normal.element.rPr.rFonts.set(_qn("w:eastAsia"), FONT)

    props = doc.core_properties
    props.title = f"{die} {cfg} — engineering report"
    props.author = "motor_ai_sim"
    props.subject = ("Full report of every solver's last result for this "
                     "configuration")
    props.comments = ("Generated by motor_ai_sim from stored solver results; "
                      "nothing was solved to produce this document.")

    _cover(doc, D)
    _machine(doc, D)
    _duties(doc, D)
    _comparisons(doc, D)
    _em_detail(doc, D)
    _pwm_influence(doc, D)
    _thermal_detail(doc, D)
    _duty_cycle(doc, D)
    _controller(doc, D)
    _mech_detail(doc, D)
    _warnings(doc, D)
    _notes(doc, D)
    # Every section is on the page: what is left is python-docx zipping it.
    _RP.stage("tables")

    buf = io.BytesIO()
    _RP.stage("writing")
    doc.save(buf)
    return buf.getvalue()


# ── cover ───────────────────────────────────────────────────────────────────


def _cover(doc, D: Dict[str, Any]) -> None:
    from docx.shared import Pt

    title = doc.add_paragraph()
    title.paragraph_format.space_after = Pt(2)
    _set_font(title.add_run(f"{D['die']} · {D['cfg']}"),
              size=round(22 * TEXT_SCALE, 1), bold=True, color=NAVY)
    _p(doc, R.machine_subtitle(D["role"], D["geo"], D["mats"]),
       size=10, color=NOTE, space_after=2.0)
    # NO GENERATION TIMESTAMP (user 2026-09-10; reviewer 2026-09-14, D4 — the
    # PDF carried two of them a minute apart).
    _p(doc, "Duty <b>%s</b> · every number below was read "
            "from a stored solver result; nothing was solved to produce this "
            "document." % (D["d_duty"].get("name") or "—"),
       size=9.5, color=NOTE, space_after=10.0)

    # WHO OWNS THIS, first thing on the page (user 2026-09-10).
    _p(doc, R.CONFIDENTIAL_NOTICE, size=9.5, bold=True, color=WARN,
       space_after=10.0)

    _table(doc, R.headline_rows(D["role"], D["d_duty"], D["em"], D["brg"],
                                D.get("cols"), D.get("ctxs"),
                                D.get("supply")),
           size=10.5, widths_cm=[5.0, 3.4, 18.0])
    _p(doc, "Operating point taken from %s."
            % (D["em_src"] or "— nothing solved yet"),
       size=9.5, italic=True, color=NOTE, space_after=10.0)

    # THE PACK the voltage limit comes from (user 2026-09-10: "нигде не нашёл
    # информацию про батарейку и лимиты напряжения").
    _brows = R.battery_rows(D["batt"])
    _h(doc, "Battery and the voltage limit", 1)
    if len(_brows) > 1:
        _table(doc, _brows, size=10.5, widths_cm=[5.0, 3.4, 18.0])
        _p(doc, R.battery_note(D.get("cols")), size=9.5, italic=True, color=NOTE,
           space_after=8.0)
        # …AND WHY A DUTY IS BILLED ON THE TOP OF CHARGE (2026-09-16): two
        # duties of one machine on two different links, with nothing saying
        # what the nominal one could not have done.
        _above = R.dc_link_above_nominal_text(D.get("cols"), D["batt"])
        if _above:
            _p(doc, _above, size=9.5, italic=True, color=NOTE, space_after=8.0)
    else:
        _p(doc, R.BATTERY_NONE, size=9.5, color=NOTE, space_after=8.0)

    # …and what the symbols mean, for whoever reads this and did not write it.
    _h(doc, "What the symbols mean", 1)
    _table(doc, R.glossary_rows(D["em"], D.get("cols")), size=10.5,
           widths_cm=[4.4, 22.0])

    # NO GEOMETRY-DELTA NOTE (client review 2026-09-14) — see
    # `report.cover_source_note`.  What is left is one neutral sentence, and
    # only when the electromagnetic numbers did not come from a saved duty.
    _src_note = R.cover_source_note(bool(D.get("em_from_run")))
    if _src_note:
        _p(doc, _src_note, size=9, color=NOTE, italic=True, space_after=8.0)
    _p(doc, R.contents_line(D.get("sec")), size=9.5, italic=True, color=NOTE)


# ── 1 machine ───────────────────────────────────────────────────────────────


def _machine(doc, D: Dict[str, Any]) -> None:
    _h(doc, R.section_heading(D.get("sec"), "machine"), 1)
    png = None
    try:
        from motor_ai_sim.datasheet import _render_cross_section
        # Full page width, and rendered to match (user 2026-09-10: "картинку
        # машины нужно сделать побольше, чтобы были видны катушки").  At 8 cm
        # the slot fill was a smudge; the coils are the point of this drawing.
        png = _render_cross_section(R.thumb_svg_for(D["die_doc"]),
                                    px=2000)
    except Exception as exc:                                # noqa: BLE001
        log.debug("report_docx: no cross-section (%s)", exc)
    if _picture(doc, png, cm=PIC_CM):
        _caption(doc, R.fig_label(_numbered(D), R.DIE_SECTION_CAPTION))

    _h(doc, "Geometry", 2)
    _geo = D.get("geo_report") or D["geo"]
    _ghash = D.get("geo_hash")
    _no_snap = bool(D.get("geo_no_snapshot"))
    if _no_snap:
        # F1 (L13 server audit 2026-09-19), FIXED 2026-09-20 (L155/L180
        # audits) — see report._machine_page: this used to sit in an
        # `elif` after `_ghash`, and a run's own fingerprint (recovered
        # even with no design snapshot at all) made `_ghash` truthy on the
        # ordinary case, so this honest warning never printed.
        _cfg_hash = (" %s" % _ghash) if _ghash else ""
        _p(doc, "%s no geometry snapshot stored with this run — design "
                "values from the configuration file%s, not verified "
                "against what the run actually solved"
                % (R.FLAG, _cfg_hash), size=9, bold=True, color=WARN)
    elif _ghash:
        # ITEM 1 (owner review 2026-09-19), NAMING ITS DUTY since round 3 of
        # the audit — see `report.geometry_hash_line`.
        _p(doc, R.geometry_hash_line(
            _ghash, str((D.get("d_duty") or {}).get("name") or "") or None),
           size=9, italic=True)
    if D.get("geo_mismatch") and not _no_snap:
        # Only true when there IS a stored snapshot to be built from — see
        # report._machine_page.
        _p(doc, "%s the live configuration has since changed — every table, "
                "figure and number below is built from the stored snapshot "
                "above, never from what is open now" % R.FLAG,
           size=9, bold=True, color=WARN)
    _fp_note = D.get("duty_fp_note") or ""
    if _fp_note:
        # F2 (L13 server audit 2026-09-19) — see report._machine_page.
        if D.get("duty_fp_mismatch"):
            _p(doc, "%s %s" % (R.FLAG, _fp_note), size=9, bold=True,
               color=WARN)
        else:
            _p(doc, _fp_note, size=9, italic=True)
    _em_src_note = D.get("em_source_note") or ""
    if _em_src_note:
        # ONE ELECTROMAGNETIC SOURCE, NAMED (F2/N2) — see
        # `report.em_source_note`.
        _p(doc, _em_src_note, size=9, italic=True)
    # THE AS-ASSEMBLED sleeve rows, appended — never blended into the design
    # "Air gap"/"Rotor outer radius" rows above (L155/L180 audits,
    # 2026-09-20) — see report._machine_page.
    _table(doc, R.geometry_rows(_geo, D["wind"], D["slot"], D["em"])
           + R.mechanical_assembly_rows(D.get("mech_geo")),
           header=False, size=10.5,
           widths_cm=[5.2, 2.6, 1.4, 5.2, 2.6, 1.4])

    _h(doc, "Materials", 2)
    _table(doc, R.material_rows(D["mats"], D["em"], _geo, D.get("sec")), size=10.5,
           widths_cm=[4.0, 5.4, 14.0])
    # …at what temperature the magnets were taken, and what the winding is
    # insulated with, both in words (user 2026-09-11).
    _mrows = R.mass_rows(D["em"], D["mats"])
    if len(_mrows) > 2:
        _h(doc, "Masses", 2)
        # THE TABLE FULL WIDTH AND THE PIE UNDER IT, ALSO FULL WIDTH (user
        # 2026-09-14): beside the table the pie was 10 cm and its legend took
        # most of that.
        _table(doc, _mrows, size=10.5,
               widths_cm=[8.0, 4.8, 4.8, 4.8, 4.3])
        _mpie = R._mass_inertia_png(D["em"], width_cm=PIC_CM, which="mass")
        if _mpie is not None and _picture(doc, _mpie, cm=PIC_CM):
            # …and the chart says whose machine it is (B9).
            _caption(doc, R.fig_label(_numbered(D), R.MASS_PIE_CAPTION,
                                      _report_tag(D)))
        _mnote = R.mass_total_note(D["em"])
        if _mnote:
            _p(doc, _mnote, size=9.5, italic=True, color=NOTE)
        _p(doc, R.MASS_TABLE_NOTE, size=9.5, italic=True, color=NOTE)

    _h(doc, "Lamination and segmentation", 2)
    _table(doc, R.lamination_rows(D["mats"], D["em"]), size=10.5,
           widths_cm=[5.0, 9.2, 9.2])
    _p(doc, R.lamination_text(D["mats"], D["em"]), size=9)
    _p(doc, R.magnet_text(D["mats"], D["em"], D.get("ctxs") or {}), size=9)
    _p(doc, R.insulation_text(D["mats"], D.get("ctxs") or {}), size=9)

    _h(doc, "Bearings", 2)
    brg = D["brg"]
    if brg and brg.get("has_bearings"):
        # The first column carries "Windage", which Word broke mid-word at
        # 1.6 cm (reviewer 2026-09-14, D7); the new "speed verdict" column is
        # C3 — the card's own verdict, printed whatever it says.
        # …and the SKF moment term by term (user 2026-09-14: 855 W at the peak
        # duty is 10 % of every loss in the machine), plus the grease's own
        # temperature limit on the last row.
        # 2.4 cm on the first column, not 1.7: "Windage" still broke mid-word
        # at 1.7 (reviewer 2026-09-14, CS-3 / D7).
        _table(doc, R.bearing_rows(brg), size=10.5,
               widths_cm=[2.4, 4.1, 2.8, 1.9, 3.3, 1.7, 1.7, 1.7, 1.9, 1.9])
        _p(doc, R.bearing_note_text(brg), size=9.5, italic=True, color=NOTE)
    else:
        _p(doc, R.NO_BEARINGS_TEXT, size=9)


# ── 2 duties ────────────────────────────────────────────────────────────────


def _duties(doc, D: Dict[str, Any]) -> None:
    _h(doc, R.section_heading(D.get("sec"), "duties"), 1)
    cols = D["cols"]
    if not cols:
        _p(doc, R.DUTY_OVERVIEW_EMPTY, size=9, bold=True, color=WARN)
        return
    what, when = R.duty_overview_rows(cols, D["active_duty"])
    _table(doc, [list(R.DUTY_OVERVIEW_HEAD)] + what, size=10.5)
    _pe = R.point_error_text(cols)
    if _pe:
        _p(doc, _pe, size=9, italic=True, color=NOTE)
    # no "when computed" table and no paragraphs under it — see `_duty_overview`


# ── 3 the comparison tables ─────────────────────────────────────────────────


def _comparisons(doc, D: Dict[str, Any]) -> None:
    cols = D["cols"]
    _h(doc, R.section_heading(D.get("sec"), "compare"), 1)
    _p(doc, R.COMPARE_INTRO, size=9)
    if not cols:
        return

    _h(doc, "Electromagnetic", 2)
    header, rows = R.em_compare_rows(cols, D["batt"])
    _cmp_table(doc, header, cols, rows)
    # ONE BUILD, ONE MASS — the flagged sentence under the table the row is
    # in (2026-09-14).
    _mc = R.mass_consistency(cols)
    if _mc:
        _p(doc, "%s %s." % (R.FLAG, _mc["text"]), size=9, bold=True, color=WARN)
    # M2 (L13 server audit round 4): whether the row above's disagreeing
    # geometry hashes are a real geometry difference or just a remesh.
    _gn = R.geometry_snapshot_note(cols)
    if _gn:
        _p(doc, _gn, size=9.5, italic=True, color=NOTE)
    _p(doc, R.EM_COMPARE_NOTE, size=9)
    if D["brg"] and D["brg"].get("has_bearings"):
        _p(doc, R.EM_BEARING_NOTE, size=9.5, italic=True, color=NOTE)

    _h(doc, "Thermal", 2)
    header, rows = R.thermal_compare_rows(cols)
    _cmp_table(doc, header, cols, rows)
    _p(doc, R.THERMAL_COMPARE_NOTE, size=9)
    if not any(isinstance((c.get("res") or {}).get("thermal"), dict)
               for c in cols):
        _p(doc, R.THERMAL_COMPARE_EMPTY, size=9, bold=True, color=WARN)

    _h(doc, "Mechanical", 2)
    header, rows = R.mech_compare_rows(cols)
    _cmp_table(doc, header, cols, rows)
    _p(doc, R.MECH_COMPARE_NOTE, size=9)

    cheader, crows = R.crit_compare_rows(cols)
    if crows:
        _h(doc, R.CRIT_HEADING, 2)
        _cmp_table(doc, cheader, cols, crows)
        _p(doc, R.CRIT_COMPARE_NOTE, size=9)

    _h(doc, "Coupled electromagnetic / thermal loop", 2)
    header, rows = R.coupled_compare_rows(cols)
    _cmp_table(doc, header, cols, rows)
    _p(doc, R.COUPLED_COMPARE_NOTE, size=9)
    if not any(isinstance((c.get("res") or {}).get("coupled"), dict)
               for c in cols):
        _p(doc, R.COUPLED_COMPARE_EMPTY, size=9.5, italic=True, color=NOTE)


# ── 4 electromagnetic in detail ─────────────────────────────────────────────


def _em_sine(D: Dict[str, Any]):
    """The report duty's SINUSOIDAL summary, when its own run is a PWM one —
    what the low-order ripple row of the torque table reads (CS-9)."""
    col = R._col_of(D.get("cols"),
                    str((D.get("d_duty") or {}).get("name") or ""))
    return (col or {}).get("em_sine")


def _coupled(D: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The report duty's stored COUPLED record — where the 20 °C catalogue
    constants live (owner 2026-09-18).  ``None`` on a duty that has never been
    through the loop, which the table then says out loud."""
    col = R._col_of(D.get("cols"),
                    str((D.get("d_duty") or {}).get("name") or ""))
    rec = ((col or {}).get("res") or {}).get("coupled")
    return rec if isinstance(rec, dict) else None


def _drive(D: Dict[str, Any]) -> str:
    """"pwm" when the duty this report is about runs on the inverter."""
    return ("pwm" if (D.get("supply") or R.SUPPLY_SINE) != R.SUPPLY_SINE
            else "sine")


def _inverter(D: Dict[str, Any]) -> Dict[str, Any]:
    """The bridge that fed the report duty — ``{}`` on a sinusoid.  The voltage
    table needs it for the DC link, which is what the terminals really see."""
    col = R._col_of(D.get("cols"),
                    str((D.get("d_duty") or {}).get("name") or ""))
    return R.duty_inverter(col or {})


def _em_detail(doc, D: Dict[str, Any]) -> None:
    _h(doc, R.section_heading(D.get("sec"), "em"), 1)
    em, d_duty = D["em"], D["d_duty"]
    if not em:
        _p(doc, R.EM_PAGE_UNSOLVED, size=9, bold=True, color=WARN)
        return
    _p(doc, "Source: %s." % D["em_src"], size=9.5, italic=True, color=NOTE)
    if D.get("duty_fp_mismatch"):
        # F2 (L13 server audit 2026-09-19) — see report._em_page.
        _p(doc, "%s %s" % (R.FLAG, D.get("duty_fp_note") or (
            "this duty's electromagnetic data was solved on a different "
            "geometry than its thermal/coupled/mechanical records below — "
            "read this section as a SEPARATE, older machine.")),
           size=9.5, bold=True, color=WARN)

    _h(doc, "Operating point", 2)
    _table(doc, R.em_operating_rows(em, d_duty, D["geo"], D["em_run"],
                                    D["mats"], cp=_coupled(D)),
           header=False, size=10.5, widths_cm=[4.6, 5.4, 4.6, 5.4])

    _h(doc, "Torque, power and voltage", 2)
    # The pack rides along so the modulation-index row can be formed (reviewer
    # 2026-09-14 / PWM study §1.7) — it needs the pack's minimum voltage.
    _table(doc, R.em_torque_rows(em, D.get("batt"), _drive(D),
                                 _em_sine(D), _inverter(D)), size=10.5,
           widths_cm=[6.4, 3.6, 12.0])
    # The "both columns are printed" footnote belongs to the TORQUE table,
    # which has two of them (reviewer 2026-09-14, B1).
    _p(doc, R.em_k3d_note(R._g(em, "end3d.k_flux")), size=9.5, italic=True,
       color=NOTE)
    _crows = R.em_constant_rows(em, _em_sine(D), _drive(D))
    if len(_crows) > 1:
        _h(doc, "Machine constants", 2)
        _table(doc, _crows, size=10.5, widths_cm=[6.4, 3.6, 12.0])
        _p(doc, R.em_constants_note(R._g(em, "end3d.k_flux"), _drive(D)),
           size=9.5,
           italic=True, color=NOTE)
    # …AND THE SAME CONSTANTS AT 20 °C (owner 2026-09-18) — the numbers a
    # catalogue quotes, so this machine can be compared with any other.  Always
    # printed, like the PDF's: an absent subsection would read as a machine that
    # HAS no catalogue constants, which is a different statement.
    _h(doc, "Machine constants at 20 °C (catalogue values)", 2)
    _table(doc, R.cold_constant_rows(_coupled(D)), size=10.5,
           widths_cm=[6.4, 3.6, 12.0])
    _p(doc, R.COLD_CONSTANTS_NOTE, size=9.5, italic=True, color=NOTE)

    _h(doc, "Losses", 2)
    _tag_r = _report_tag(D)
    _L, _R = (D.get("pair") or {}).get("left"), (D.get("pair") or {}).get("right")
    _pie = R._loss_pie_png(em, D["brg"], width_cm=PIC_CM)
    if _R:
        # THE TABLE FULL WIDTH AND THE TWO PIES UNDER IT.  Beside the table
        # there is room for one chart, not two, and a pie squeezed to 6 cm has
        # no legend anybody can read.
        _table(doc, R.em_loss_rows(em, D["brg"]), size=10.5,
               widths_cm=[5.6, 2.4, 15.0])
        _pair_fig(doc, D,
                  R._loss_pie_png((_L or {}).get("em") or em,
                                  (_L or {}).get("brg"), width_cm=PAIR_CM),
                  R._loss_pie_png(_R.get("em") or {}, _R.get("brg"),
                                  width_cm=PAIR_CM),
                  R.LOSS_PIE_CAPTION)
    elif _pie is not None:
        # The table full width and the pie under it, at full width too (user
        # 2026-09-14) — beside the table the legend ate most of the picture.
        _table(doc, R.em_loss_rows(em, D["brg"]), size=10.5,
               widths_cm=[5.6, 2.4, 15.0])
        if _picture(doc, _pie, cm=PIC_CM):
            _caption(doc, R.fig_label(_numbered(D), R.LOSS_PIE_CAPTION,
                                      _tag_r))
    else:
        _table(doc, R.em_loss_rows(em, D["brg"]), size=10.5,
               widths_cm=[5.6, 2.4, 15.0])

    # The waveforms the losses were measured on (user 2026-09-11): torque with
    # its harmonics, the phase currents, the line voltages with their spectrum.
    # Each caption carries the duty and the point it belongs to (B9) — or, on a
    # pair, both duties (2026-09-14).
    _wf = D.get("wf") or {}
    _tag_w = D.get("em_map_tag") or _tag_r
    for _fn, _cap in ((R._torque_png, R.TORQUE_CAPTION),
                      (R._currents_png, R.CURRENTS_CAPTION),
                      (R._voltage_png,
                       R.voltage_caption((_L or {}).get("wf") if _R else _wf,
                                         _R.get("wf") if _R else None))):
        if _R:
            _pair_fig(doc, D,
                      _fn((_L or {}).get("wf") or {}, width_cm=PAIR_CM),
                      _fn(_R.get("wf") or {}, width_cm=PAIR_CM),
                      _cap)
        elif _picture(doc, _fn(_wf, width_cm=PIC_CM), cm=PIC_CM):
            _caption(doc, R.fig_label(_numbered(D),
                                      _cap + (D.get("wf_note") or ""), _tag_w))

    dem = R.em_demag_text(em, D.get("demag_corner"))
    if dem:
        _h(doc, "Demagnetisation", 2)
        _p(doc, dem, size=9)

    # ── the pictures ────────────────────────────────────────────────────────
    # Every map full width with its caption under it, and every caption carries
    # the duty and the point it belongs to (user 2026-09-08 / 2026-09-09).
    maps, maps_r = R.em_maps_pair(
        (((_L or {}).get("src") or {}).get("em") if _R else None)
        or (D.get("pic_src") or {}).get("em") or D["snap"],
        (_R.get("src") or {}).get("em") if _R else None,
        width_cm=PAIR_CM if _R else PIC_CM)
    _h(doc, "Field maps from the stored run", 2)
    # THE LEAD-IN LEADS (MJ-6, audit v5): this sentence used to be printed
    # after the last map of the section, two pages below the first pair it
    # introduces.
    # L180 M-3 (audit 2026-09-20) — see report._em_page: §4's paired lead-in
    # carries the same "Per-duty numbers are in section N" signpost §6/§7
    # already do on theirs.
    _p(doc, R.pair_owner_text("The field maps", D.get("pair"),
            R.pair_owner_tail("em", D.get("em_map_duty") or D["em_duty"],
                              bool(D.get("em_map_from_duty")),
                              "Per-duty numbers are in %s."
                              % R.compare_ref(D.get("sec"))),
            first_fig=R.fig_ahead(_numbered(D)))
       or R.map_owner_text(D.get("em_map_duty") or D["em_duty"], em, d_duty,
                           point=D.get("em_map_point"),
                           from_duty=bool(D.get("em_map_from_duty")),
                           sec=D.get("sec")),
       size=9.5, italic=True, color=NOTE)
    # The point of the PICTURES (the picture duty's stored run), not the
    # report duty's — see `report.em_map_point` (2026-09-13).
    tag = D.get("em_map_tag") or R._map_tag(
        D["em_duty"], R._g(em, "rpm") or R._g(d_duty, "rpm"),
        R._g(em, "I_phase_rms_A") or R._g(d_duty, "current_arms"))
    n_fig = 0
    for key, cap, missing in R.em_map_figures(maps, maps_r):
        _a, _b = maps.get(key), (maps_r or {}).get(key)
        if _R and (_a or _b):
            if _pair_fig(doc, D, _a, _b, cap,
                         numbers=R.em_map_numbers(key, maps, maps_r,
                                                  left_side=_L, right_side=_R),
                         map_kind="em"):
                n_fig += 1
                # UNDER EACH DEMAG MAP (item 6, owner review 2026-09-19):
                # magnet temperature, mean Br loss, BH energy loss, affected
                # area, worst element, both sides.
                if key == "demag":
                    _dn = R.demag_pair_note(_L, _R)
                    if _dn:
                        _p(doc, _dn, size=9.5, italic=True, color=NOTE)
            continue
        if _picture(doc, _a):
            n_fig += 1
            _caption(doc, "Fig. %d [%s] — %s"
                     % (n_fig, tag, _prov(D, cap, "em")))
        else:
            _caption(doc, "Fig. — %s." % missing)
    if n_fig == 0:
        _p(doc, R.EM_NO_SNAPSHOT, size=9)


# ── 5 PWM influence ─────────────────────────────────────────────────────────
# The PDF's `report._pwm_page`, in Word.  Not one number is formed here: the
# rows come from `report.pwm_rows` and the paragraphs from
# `report.pwm_influence_text`, so the two documents cannot say two different
# things about what the carrier costs.


def _pwm_influence(doc, D: Dict[str, Any]) -> None:
    _h(doc, R.section_heading(D.get("sec"), "pwm"), 1)
    _p(doc, R.pwm_intro(D["cols"],
                       str((D.get("d_duty") or {}).get("name") or "") or None),
       size=9.5)
    blocks = R.pwm_blocks(D["cols"], D.get("brg"))
    if not any(b["measured"] for b in blocks):
        _p(doc, R.PWM_NO_RECORD_AT_ALL, size=9.5, italic=True, color=NOTE)
        return
    for b in blocks:
        _h(doc, "Duty '%s'" % b["duty"], 2)
        # EACH DUTY'S CONCLUSIONS UNDER ITS OWN HEADING (A2-2) — see
        # `report.pwm_duty_text`.
        _bcol = R._col_of(D["cols"], b["duty"])
        _btext = R.pwm_duty_text(_bcol, D.get("sec"), D.get("brg")) if _bcol else []
        if not b["measured"]:
            _p(doc, b["line"], size=9.5, italic=True, color=NOTE)
            for par in _btext:
                _p(doc, par, size=9)
            continue
        n = max(1, len(b["header"]) - 1)
        label_cm = 8.2
        cell_cm = min(4.6, (26.7 - label_cm) / n)
        # ONE PAGE, NOT TWO (CS-10): the table used to break after two data
        # rows and carry the other eleven onto the next sheet.
        _table(doc, [b["header"]] + b["rows"], header=True, size=9.5,
               widths_cm=[label_cm] + [cell_cm] * n, keep_together=True)
        # WHERE THE STUDY WAS MEASURED, before its provenance line (client
        # review 2026-09-14) — see `report.pwm_point_note`.
        if b.get("point_note"):
            _p(doc, b["point_note"], size=9.5, italic=True, color=NOTE)
        if b["source"]:
            _p(doc, "Source: %s." % b["source"], size=9.5, italic=True,
               color=NOTE)
        for n in (b.get("notes") or []) if b.get("coupled") else []:
            _p(doc, n, size=9.5, italic=True, color=NOTE)
        for par in _btext:
            _p(doc, par, size=9)


# ── 6 thermal in detail ─────────────────────────────────────────────────────


def _thermal_detail(doc, D: Dict[str, Any]) -> None:
    th, cp = D["th"], D["cp"]
    # The maps on this page are the PICTURE DUTY's stored field whenever it has
    # one — so the caption and this line name that duty and its point, not the
    # machine-level owner, which is empty when another machine is loaded on the
    # server (2026-09-14; the same fix the EM maps got on 2026-09-13).
    map_duty = D.get("th_map_duty") or (D["map_owner"] or {}).get("thermal")
    map_rpm, map_cur = D.get("th_map_rpm"), D.get("th_map_cur")
    _h(doc, R.section_heading(D.get("sec"), "thermal"), 1)
    _p(doc, R.pair_owner_text(
        "The temperature maps and the charts", D.get("pair"),
        R.pair_owner_tail("thermal", map_duty,
                          bool(D.get("th_map_from_duty")),
                          "Per-duty temperatures are in %s."
                          % R.compare_ref(D.get("sec"))),
        first_fig=R.fig_ahead(_numbered(D)))
       or R.thermal_map_owner_text(map_duty,
                                   bool(D.get("th_map_from_duty")),
                                   R.point_words(map_rpm, map_cur),
                                   D.get("sec")),
       size=9.5, italic=True, color=NOTE)
    entry = th.get("field") or th.get("coupled")
    if not entry:
        _p(doc, R.THERMAL_PAGE_UNSOLVED, size=9, bold=True, color=WARN)
        return
    res = entry.get("result") or {}
    inner = res.get("field") if isinstance(res.get("field"), dict) else res
    # ITEM 12 (owner review 2026-09-19): each thermal map gets its own source
    # line, per side of a pair — never one line describing only this report's
    # own duty while a different duty's map (steady vs transient) sits beside
    # it.
    # THIS PAGE'S OWN `cp` IS THE MACHINE-LEVEL STORE'S SHAPE — its loop
    # nests under "coupling" (see `R.coupled_loop_text`), unlike the FLAT
    # per-duty record every mode check below reads.  Every item-3/4/12 check
    # reads THIS flattened copy, never `cp` directly.
    _cp_flat = (cp or {}).get("coupling") if isinstance(cp, dict) else None
    _cp_flat = _cp_flat if isinstance(_cp_flat, dict) and _cp_flat else cp
    for _line in R.thermal_pair_source_lines(
            D.get("pair"), th, entry, D.get("detail_duty"),
            bool(D.get("th_detail_from_duty")), _cp_flat, D.get("em")):
        _p(doc, _line, size=9.5, italic=True, color=NOTE)

    _h(doc, "Boundary conditions", 2)
    for line in R._cooling_words(res.get("cooling") or inner.get("cooling") or {}):
        _bullet(doc, line)

    # The stamp every chart on this page carries (B9): the duty the thermal
    # answer belongs to, at the point it was solved.
    _tag_t = R._map_tag(map_duty, res.get("rpm", inner.get("rpm")) or map_rpm,
                        map_cur)

    _L, _R = (D.get("pair") or {}).get("left"), (D.get("pair") or {}).get("right")
    _t_num = R.pair_number_clause(
        "hottest solid",
        R.thermal_solid_max_c((_L or {}).get("th_inner") or inner),
        R.thermal_solid_max_c((_R or {}).get("th_inner") or {}), 1, "°C")

    _h(doc, "Temperatures", 2)
    trows = R.thermal_temp_rows(res, inner)
    if len(trows) > 1:
        # …with the bars beside them (user 2026-09-11).  The limits drawn on
        # the chart are this machine's own: the insulation class and the
        # magnet grade, the two the warnings section judges against.
        _lims = R.temp_chart_limits(D["mats"], D.get("ctxs") or {})
        _bars = R._temp_bars_png(res, inner, _lims, width_cm=PIC_CM)
        if _R:
            # The table full width, the two sets of bars under it — one duty
            # per side, same axis, same dashed limits.
            _table(doc, trows, size=10.5, widths_cm=[7.0, 4.2])
            _pair_fig(doc, D,
                      R._temp_bars_png((_L or {}).get("th") or res,
                                       (_L or {}).get("th_inner") or inner,
                                       _lims, width_cm=PAIR_CM),
                      R._temp_bars_png(_R.get("th") or {},
                                       _R.get("th_inner") or {}, _lims,
                                       width_cm=PAIR_CM),
                      R.paired_thermal_caption(
                          R.temp_bars_caption_with_duty, _L, _R),
                      numbers=_t_num, map_kind="thermal")
        elif _bars is not None:
            _table(doc, trows, size=10.5, widths_cm=[7.0, 4.2])
            # The caption says which bars the lines apply to (D8): every dashed
            # line spans the whole axis, but only the coloured bars are judged
            # against one — a blue bar has no limit of its own.
            # ITEM 4 (owner review 2026-09-19): this page's own duty, steady or
            # transient — never a fixed "steady-state" wording.
            _is_lim0 = R.coupled_mode(_cp_flat) == "limited"
            _t_lim0 = (R._numf((R.limited_of(_cp_flat) or {}).get("t_cold_s"))
                      if _is_lim0 else None)
            if _picture(doc, _bars, cm=PIC_CM):
                _caption(doc, R.fig_label(
                    _numbered(D),
                    _prov(D, R.temp_bars_caption_with_duty(
                        None, _is_lim0, _t_lim0), "thermal"),
                    _tag_t))
        else:
            _table(doc, trows, size=10.5, widths_cm=[7.0, 4.2])
    _p(doc, R.thermal_extremes_text(res, inner), size=9.5, italic=True,
       color=NOTE)

    _h(doc, "Heat budget", 2)
    # ITEM 3 (owner review 2026-09-19): a `mode: limited` record's own
    # thermal answer is the STEADY map the lumped network was fitted to, not
    # the machine at t_lim — printing its full losses as a steady balance
    # beside a map now captioned "transient at t = …" mixes two regimes.
    # Without the node capacities and dT/dt AT t_lim this report cannot build
    # a transient balance, so the budget becomes one line and the waterfall
    # (that same overheated map's fluxes) is dropped.
    _limited_budget = R.coupled_mode(_cp_flat) == "limited"
    if _limited_budget:
        _p(doc, R.transient_budget_fallback_text(_cp_flat),
           size=9.5, italic=True, color=NOTE)
    else:
        _wf_png = R._heat_waterfall_png(res, inner, width_cm=PIC_CM)
        if _R:
            _table(doc, R.thermal_budget_rows(res, inner, D.get("em")),
                   size=10.5, widths_cm=[6.4, 2.6])
            _pair_fig(doc, D,
                      R._heat_waterfall_png((_L or {}).get("th") or res,
                                            (_L or {}).get("th_inner") or inner,
                                            width_cm=PAIR_CM),
                      R._heat_waterfall_png(_R.get("th") or {},
                                            _R.get("th_inner") or {},
                                            width_cm=PAIR_CM),
                      R.heat_chart_caption_pair(
                          (_L or {}).get("th") or res,
                          (_L or {}).get("th_inner") or inner,
                          (_R or {}).get("th") or {},
                          (_R or {}).get("th_inner") or {}))
        elif _wf_png is not None:
            _table(doc, R.thermal_budget_rows(res, inner, D.get("em")),
                   size=10.5, widths_cm=[6.4, 2.6])
            # The caption's hatched-bar sentence is added only when that bar
            # is actually drawn (reviewer 2026-09-14, B2).
            if _picture(doc, _wf_png, cm=PIC_CM):
                _caption(doc, R.fig_label(_numbered(D),
                                          R.heat_chart_caption(res, inner),
                                          _tag_t))
        else:
            _table(doc, R.thermal_budget_rows(res, inner, D.get("em")),
                   size=10.5, widths_cm=[6.4, 2.6])
        # The line that makes the three totals on this page add up, with the
        # duty named (reviewer 2026-09-14).
        _rec = R.thermal_budget_reconcile_text(res, inner, D.get("em"),
                                               map_duty,
                                               em_duty=D.get("detail_duty"))
        if _rec:
            _p(doc, _rec, size=9.5, italic=True, color=NOTE)

    # The rated duty's own field when one is stored (2026-09-10) — and the
    # other duty's beside it, on one colour bar, since 2026-09-14.
    if _R:
        _ml, _mr = R.thermal_map_pair(
            ((_L or {}).get("src") or {}).get("thermal") or (_L or {}).get("th")
            or (D.get("detail_src") or {}).get("thermal") or res,
            (_R.get("src") or {}).get("thermal") or _R.get("th"),
            width_cm=PAIR_CM)
        if not _pair_fig(doc, D, _ml, _mr,
                         R.paired_thermal_caption(
                             R.thermal_map_caption_with_duty, _L, _R),
                         numbers=_t_num, map_kind="thermal"):
            _p(doc, R.THERMAL_MAP_MISSING, size=9.5, italic=True, color=NOTE)
    elif _picture(doc, R._thermal_map(
            (D.get("detail_src") or {}).get("thermal") or res,
            width_cm=PIC_CM), cm=PIC_CM):
        _is_lim1 = R.coupled_mode(_cp_flat) == "limited"
        _t_lim1 = (R._numf((R.limited_of(_cp_flat) or {}).get("t_cold_s"))
                  if _is_lim1 else None)
        _caption(doc, R.fig_label(
            _numbered(D),
            _prov(D, R.thermal_map_caption_with_duty(None, _is_lim1, _t_lim1),
                 "thermal"),
            R._map_tag(map_duty, res.get("rpm", inner.get("rpm")) or map_rpm,
                       map_cur)))
    else:
        _p(doc, R.THERMAL_MAP_MISSING, size=9.5, italic=True, color=NOTE)

    if cp:
        _h(doc, "Coupled loop", 2)
        _p(doc, R.coupled_loop_text(cp), size=9)
        # The route's sentence in the client's words, and the point miss in the
        # same figure and sign as every other page (MJ-5 / CS-1, audit v7).
        warn = R.coupled_warning_words(
            cp.get("coupling") or {},
            (R.duty_point_error(R._col_of(
                D.get("cols"),
                str((D.get("d_duty") or {}).get("name") or "")) or {})
             or {}).get("pct"))
        if warn:
            _p(doc, "%s %s" % (R.FLAG, warn), size=9, bold=True, color=WARN)


# ── duty cycle ──────────────────────────────────────────────────────────────
# The PDF's `report._duty_cycle_page`, in Word.  Printed only when a duty of
# this configuration has been through the duty-cycle solver; every number and
# every sentence comes from `report`, so the two documents cannot disagree
# about what the settled cycle reached.


def _duty_cycle(doc, D: Dict[str, Any]) -> None:
    if not D.get("has_duty_cycle"):
        return
    _h(doc, R.section_heading(D.get("sec"), "duty_cycle"), 1)
    _p(doc, R.DUTY_CYCLE_INTRO, size=9.5)
    # The torque of every duty of this configuration — the profile figure is a
    # torque step, and a segment's torque is the duty's that runs in it.
    _tq = R.duty_cycle_torques(D["cols"])
    for c in D["cols"]:
        rec = R.duty_cycle_record(c)
        _h(doc, "Duty '%s'" % c.get("duty"), 2)
        if rec is None:
            _p(doc, R.DUTY_CYCLE_NOT_RUN, size=9.5, italic=True, color=NOTE)
            continue
        ctx = (D.get("ctxs") or {}).get(c.get("duty")) or {}
        # The allowable regime first — the one line this section is read for.
        _reg = R.duty_cycle_regime_text(rec)
        if _reg:
            _p(doc, _reg, size=9.5, bold=True)
        _p(doc, R.duty_cycle_profile_text(rec), size=9)
        if not (rec.get("cycle") or {}).get("converged"):
            _p(doc, "%s %s" % (R.FLAG, R.DUTY_CYCLE_NOT_CONVERGED),
               size=9, bold=True, color=WARN)
        _table(doc, R.duty_cycle_rows(c, ctx), size=10.5,
               widths_cm=[6.2, 7.0, 13.5])
        _wl, _ml = R.duty_cycle_limits(c, ctx)
        for blob, caption in R.duty_cycle_figures(rec, _wl, _ml, torques=_tq):
            if _picture(doc, blob):
                _caption(doc, R.fig_label(_numbered(D), caption,
                                          "duty '%s'" % c.get("duty")))


# ── controller ──────────────────────────────────────────────────────────────
# The PDF's `report._controller_page`, in Word.  Printed only when a duty of
# this configuration has an inverter answer; every number and every sentence
# comes from `report`, so the two documents cannot disagree about what the
# controller costs or how hot it gets.


def _controller(doc, D: Dict[str, Any]) -> None:
    if not D.get("has_controller"):
        return
    _h(doc, R.section_heading(D.get("sec"), "controller"), 1)
    _p(doc, R.CONTROLLER_INTRO, size=9.5)
    _p(doc, R.CONTROLLER_SHAFT_NOTE, size=9, italic=True, color=NOTE)
    for c in D["cols"]:
        rec = R.controller_record(c)
        _h(doc, "Duty '%s'" % c.get("duty"), 2)
        if rec is None:
            _p(doc, R.CONTROLLER_NOT_RUN, size=9.5, italic=True, color=NOTE)
            continue
        _p(doc, R.controller_device_text(rec), size=9.5)
        for v in rec.get("violations") or []:
            _p(doc, "%s %s" % (R.FLAG, v), size=9, bold=True, color=WARN)
        _table(doc, R.controller_rows(rec), size=10.5,
               widths_cm=[5.4, 4.0, 17.3])
        _table(doc, R.controller_bridge_rows(rec), size=10.5,
               widths_cm=[4.0, 3.2, 4.4, 5.6, 9.5])
        _p(doc, R.controller_assumption_text(rec), size=9, italic=True, color=NOTE)
        _p(doc, R.controller_source_text(rec), size=9, italic=True, color=NOTE)


# ── 7 mechanical in detail ──────────────────────────────────────────────────


def _mech_detail(doc, D: Dict[str, Any]) -> None:
    me = D["me"]
    map_duty = D.get("me_map_duty") or (D["map_owner"] or {}).get("rotor_stress")
    map_rpm, map_cur = D.get("me_map_rpm"), D.get("me_map_cur")
    _h(doc, R.section_heading(D.get("sec"), "mech"), 1)
    _p(doc, R.pair_owner_text(
        "The stress, displacement and safety-factor maps", D.get("pair"),
        R.pair_owner_tail("rotor-stress", map_duty,
                          bool(D.get("me_map_from_duty")),
                          R.mech_pair_tail_text(R.has_campbell(
                              (me.get("critical_speeds") or {}).get("result")
                              or {}), D.get("sec"))),
        first_fig=R.fig_ahead(_numbered(D)))
       or R.mech_map_owner_text(
        map_duty, bool(D.get("me_map_from_duty")),
        R.point_words(map_rpm, map_cur),
        # The opening sentence names the Campbell diagram only when the record
        # has the sweep to draw one (reviewer 2026-09-14, MJ-3).
        campbell=R.has_campbell((me.get("critical_speeds") or {}).get("result")
                                or {}),
        sec=D.get("sec")),
       size=9.5, italic=True, color=NOTE)

    entry = me.get("rotor_stress")
    if not entry:
        _p(doc, R.MECH_PAGE_UNSOLVED, size=9, bold=True, color=WARN)
    else:
        res = entry.get("result") or {}
        case_name = (res.get("primary_case")
                     or next(iter(res.get("cases") or {}), None))
        case = (res.get("cases") or {}).get(case_name) or {}
        _p(doc, R.mech_source_text(entry, res, case_name, case,
                                   D.get("detail_duty"),
                                   bool(D.get("me_detail_from_duty"))), size=9.5,
           italic=True, color=NOTE)
        temps = R.mech_temps_text(res)
        if temps:
            _p(doc, temps, size=9.5, italic=True, color=NOTE)

        _L, _R = ((D.get("pair") or {}).get("left"),
                  (D.get("pair") or {}).get("right"))
        _msrc = (D.get("detail_src") or {}).get("rotor_stress") or res
        _lsrc = ((_L or {}).get("src") or {}).get("rotor_stress") \
            or (_L or {}).get("mech") or _msrc
        _rsrc = ((_R or {}).get("src") or {}).get("rotor_stress") \
            or (_R or {}).get("mech")
        _sf_num = R.pair_number_clause(
            "lowest", R._worst_sf((_L or {}).get("case") or case),
            R._worst_sf((_R or {}).get("case")), 2)
        _tag = R._map_tag(map_duty, case.get("rpm", res.get("rpm")) or map_rpm,
                          map_cur)

        _h(doc, "Stress and safety factors", 2)
        prows = R.mech_part_rows(case)
        if len(prows) > 1:
            # FULL WIDTH, CHART BELOW (reviewer 2026-09-14, A3).  Nested beside
            # the safety-factor bars this eight-column table overran its cell:
            # the Criterion column was cut mid-unit and the Strength and SF
            # columns — the central answer of the mechanical section — were not
            # on the page at all.
            _table(doc, prows, size=9.5,
                   widths_cm=[3.0, 4.4, 2.6, 2.6, 3.4, 2.6, 2.6, 1.8])
            if _R:
                _pair_fig(doc, D,
                          R._sf_bars_png((_L or {}).get("case") or case,
                                         width_cm=PAIR_CM),
                          R._sf_bars_png((_R or {}).get("case") or {},
                                         width_cm=PAIR_CM),
                          R.SF_CHART_CAPTION, numbers=_sf_num)
            else:
                _sf = R._sf_bars_png(case, width_cm=PIC_CM)
                if _picture(doc, _sf, cm=PIC_CM):
                    _caption(doc, R.fig_label(_numbered(D),
                                              R.SF_CHART_CAPTION, _tag))
        _p(doc, R.mech_percentile_text(case), size=9.5, italic=True, color=NOTE)

        _h(doc, "Fit and contacts", 2)
        # …and it does not orphan its last rows onto the next page (D16).
        _table(doc, R.mech_fit_rows(case, res), size=10.5, widths_cm=[7.0, 3.0],
               keep_together=True)
        (png, case_used), (png_r, case_r) = R.mech_map_pair(
            _lsrc, _rsrc if _R else None,
            width_cm=PAIR_CM if _R else PIC_CM)
        _vm_cap = ("Von Mises stress over %s, MPa, averaged onto each part's "
                   "own nodes." % R.mech_case_words(case_used, case_r))
        _vm_num = R.pair_number_clause(
            "highest averaged", R._peak_vm((_L or {}).get("case") or case),
            R._peak_vm((_R or {}).get("case")), 1, "MPa")
        if _R:
            if not _pair_fig(doc, D, png, png_r, _vm_cap, numbers=_vm_num,
                             map_kind="rotor_stress"):
                _p(doc, R.MECH_MAP_MISSING, size=9.5, italic=True, color=NOTE)
        elif _picture(doc, png):
            _caption(doc, R.fig_label(
                _numbered(D), _prov(
                    D, "Von Mises stress over the '%s' case, MPa, averaged "
                       "onto each part's own nodes." % case_used,
                    "rotor_stress"), _tag))
        else:
            _p(doc, R.MECH_MAP_MISSING, size=9.5, italic=True, color=NOTE)
        # …and the two the tab shows beside it (user 2026-09-10).
        _extra, _extra_r = R.mech_extra_pair(
            _lsrc, _rsrc if _R else None,
            width_cm=PAIR_CM if _R else PIC_CM)
        for _k, _cap, _missing in R.mech_extra_captions():
            _a, _b = _extra.get(_k), (_extra_r or {}).get(_k)
            if _R and (_a or _b):
                _pair_fig(doc, D, _a, _b, _cap, map_kind="rotor_stress")
            elif _picture(doc, _a):
                _caption(doc, R.fig_label(
                    _numbered(D), _prov(D, _cap, "rotor_stress"), _tag))
            else:
                _p(doc, "%s." % _missing, size=9.5, italic=True, color=NOTE)

        crows = R.mech_contact_rows(case, res)
        if len(crows) > 1:
            # THE HEADER DOES NOT SIT ALONE AT THE FOOT OF A PAGE (MJ-8).
            _table(doc, crows, size=10.5, widths_cm=[6.0, 3.4, 2.4, 10.0],
                   keep_together=True)

        _jrows = R.rotor_inertia_rows(D["em"])
        if len(_jrows) > 1:
            _h(doc, "Rotor inertia", 2)
            _table(doc, _jrows, size=10.5, widths_cm=[6.0, 3.4, 12.0])
            _p(doc, R.ROTOR_INERTIA_NOTE, size=9.5, italic=True, color=NOTE)
        # 12 cm, not the document's 13.5: the three-row inertia table above it
        # is small, and at the full cap it had a page of its own with 18 % of
        # the sheet used (CS-8).
        if _picture(doc, R._mass_inertia_png(D["em"], width_cm=PIC_CM,
                                             which="inertia"),
                    cm=PIC_CM, max_cm=12.0):
            _caption(doc, R.fig_label(_numbered(D),
                                      R.mass_j_caption(D["em"]),
                                      _report_tag(D)))

    # ── ring modes: the table and the 3 × 4 gallery (2026-09-11) ───────────
    mres = R.modes_source(me, (D.get("detail_src") or {}).get("modes"))
    _h(doc, R.modes_heading(mres), 2)
    if not mres:
        _p(doc, R.MODES_PAGE_UNSOLVED, size=9)
    else:
        # THE EXCITATION COLUMN IS REBUILT from this duty's own carrier and
        # speed (reviewer 2026-09-14, A1) — the stored one belongs to whatever
        # machine the server held when the modal step ran.
        _m_rpm = D.get("modes_rpm") or mres.get("rpm") or map_rpm
        _m_fs = D.get("modes_f_switch_hz")
        # …and the carrier the run REALLY switched at (A2-1): the modulator is
        # synchronous, so a 24 kHz request became 24,383 / 24,808 Hz.
        _m_fs_eff = D.get("modes_f_switch_eff_hz")
        _m_slots = D.get("slots")
        mrows = R.mode_rows(mres, rpm=_m_rpm, slots=_m_slots, f_switch_hz=_m_fs,
                            f_switch_eff_hz=_m_fs_eff)
        if len(mrows) > 1:
            # ONE PAGE, AND ITS NOTE WITH IT (MJ-8, audit v5): the last two
            # rows and the note under them had a page to themselves.
            _table(doc, mrows, size=10.5, widths_cm=[1.2, 2.4, 1.2, 7.0, 3.0],
                   keep_together=True, keep_next=True)
            _p(doc, R.modes_excitation_note(_m_rpm, _m_slots, _m_fs,
                                            D.get("modes_duty") or map_duty,
                                            _m_fs_eff),
               size=9.5, italic=True, color=NOTE)
        # FULL PAGE WIDTH for the gallery (user 2026-09-11): twelve small
        # cross-sections at the 16.5 cm the maps use were thumbnails.  The
        # height cap is the landscape page's own live height, so it still
        # shares a sheet with its caption.
        # …AND NOT SO TALL THAT ITS CAPTION CANNOT FOLLOW IT (MJ-4, reviewer
        # 2026-09-14).  At 17 cm the gallery plus its caption is taller than
        # the 18 cm of live height a landscape A4 has, so Word had nowhere to
        # honour the `keepNext` `_picture` sets: page 35 was the 3 × 4 grid
        # alone and page 36 opened with its caption, immediately above the
        # "Critical speeds" heading a reader then attributed it to.  15 cm
        # leaves the caption room on the same sheet.
        if _picture(doc, R._mode_gallery_png(mres, width_cm=PIC_CM),
                    cm=PIC_CM, max_cm=15.0):
            _n = min(len(mrows) - 1,
                     R.MODES_GALLERY_ROWS * R.MODES_GALLERY_COLS)
            _caption(doc, R.fig_label(
                _numbered(D),
                _prov(D, R.MODES_CAPTION
                      % (_n, mres.get("body") or "iron", "6 %"), "modes"),
                R._map_tag(map_duty, mres.get("rpm") or map_rpm)))
        else:
            _p(doc, R.MODES_GALLERY_MISSING, size=9.5, italic=True, color=NOTE)

    _h(doc, "Critical speeds", 2)
    crit_entry = me.get("critical_speeds")
    if not crit_entry:
        _p(doc, R.CRIT_PAGE_UNSOLVED, size=9)
        return
    crit = crit_entry.get("result") or {}
    rows = R.crit_detail_rows(crit)
    if len(rows) > 1:
        _table(doc, rows, size=10.5, widths_cm=[1.8, 3.0, 3.6, 3.6, 4.4])
    else:
        _p(doc, R.CRIT_NO_CROSSING, size=9)
    verdict = R.crit_verdict_text(crit)
    if verdict:
        _p(doc, verdict, size=9.5, italic=True, color=NOTE)
    # NO BRANCHES, NO FIGURE AND NO CAPTION (reviewer 2026-09-14, A4) — one
    # line of text instead of an empty axes under a sentence describing curves.
    if _picture(doc, R._campbell_png(crit)):
        _caption(doc, R.fig_label(
            _numbered(D), _prov(D, R.CAMPBELL_CAPTION, "critical_speeds"),
            R._map_tag(map_duty, crit.get("rated_rpm") or map_rpm)))
    else:
        _p(doc, R.CAMPBELL_NOT_STORED, size=9.5, italic=True, color=NOTE)


# ── 8 warnings ──────────────────────────────────────────────────────────────


def _warnings(doc, D: Dict[str, Any]) -> None:
    """Every duty against every limit, with what to do about it — one table.

    User, 2026-09-09: *"нужно делать предупреждения, если что-то близко к
    пределам, и предложения, как этого избежать"*.  In Word the remedy is a
    sixth COLUMN rather than a paragraph list under the table: a cell wraps, and
    the reader who is going to act on a row wants the fix beside the number, not
    two pages further down.  The row is coloured by its level — red is over,
    amber is inside 10 % of the limit.
    """
    _h(doc, R.section_heading(D.get("sec"), "warnings"), 1)
    ordered = R.all_duty_warnings(D["cols"], D["ctxs"])
    reds = [w for w in ordered if w["level"] == "red"]
    ambers = [w for w in ordered if w["level"] == "amber"]
    greens = [w for w in ordered if w["level"] == "green"]

    if not ordered:
        _p(doc, R.WARNINGS_NONE, size=9)
    else:
        _p(doc, R.warnings_headline(len(reds), len(ambers), len(greens)), size=9)
        rows = [list(R.WARNINGS_HEAD_DOCX)]
        for w in ordered:
            cells = R.warning_row(w)          # level, duty, quantity, v, l, m
            # A green check has no remedy: there is nothing to do about a
            # quantity that passed (user 2026-09-10).
            # The remedy column is for RED rows only: green passed, and amber
            # is inside its limit with the margin printed beside it.
            rows.append(cells[1:] + [str(w["remedy"]) if w["level"] == "red"
                                     else ""])
        t = _table(doc, rows, size=10.5,
                   widths_cm=[2.6, 5.0, 2.8, 2.6, 2.2, 11.5])
        # Colour the words, not the cells: a shaded row would fight the zebra
        # and print as a grey block on the user's mono laser.
        for i, w in enumerate(ordered, start=1):
            colour = _ink(w["level"])
            for j in range(len(rows[0])):
                for par in t.cell(i, j).paragraphs:
                    for run in par.runs:
                        _set_font(run, size=9.5, color=colour,
                                  bold=(j == 1 and w["level"] == "red"))
        _p(doc, "")
        _h(doc, "What to do about each of them", 2)
        # RED only (user 2026-09-10) — an amber row states its own margin in
        # the table and needs no paragraph.
        for w in [x for x in ordered if x["level"] == "red"]:
            par = _p(doc, "", space_after=1.0, indent_cm=0.4)
            _set_font(par.add_run("• %s — %s: %s" % (
                w["duty"], w["quantity"], R.warning_sentence(w))),
                size=9, bold=True, color=_ink(w["level"]))
            # ONE LINE + ONE REMEDY (user 2026-09-14): the rule's own basis is
            # in the limits table below and is not repeated here.
            _p(doc, str(w["remedy"]), size=9, space_after=4.0, indent_cm=0.9)

    _h(doc, "The rules, and where each limit comes from", 2)
    ex = R.limit_rules_context(D["ctxs"])
    _table(doc, R.limit_rules_rows(ex), size=10.5,
           widths_cm=[5.4, 3.0, 18.0])
    _p(doc, R.WARNINGS_RULES_NOTE, size=9.5, italic=True, color=NOTE)


# ── 9 assumptions, notes and sources ────────────────────────────────────────


def _notes(doc, D: Dict[str, Any]) -> None:
    _h(doc, R.section_heading(D.get("sec"), "notes"), 1)
    # THE LIST DOES NOT BREAK (CS-8, audit v5): the last page of the document
    # was one orphaned bullet on 6 % of a sheet.
    _bs = R.assumption_bullets(D.get("sec"))
    for i, b in enumerate(_bs):
        par = _bullet(doc, b)
        if i < len(_bs) - 1:
            par.paragraph_format.keep_with_next = True
    # Nothing after the assumptions — see `report._notes_page` for why the
    # "Not included" list, the read-only closing and the "ANOTHER machine"
    # paragraph went together (user 2026-09-11).
