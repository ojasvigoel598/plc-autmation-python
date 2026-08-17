"""
Generate docs/PLCSim_Engineering_Guide.docx from docs/guide_content.txt.

Stdlib only.  The markup is trivial:

    #H1 Section        heading level 1
    #H2 Sub-section     heading level 2
    #H3 Sub-sub          heading level 3
    #P  ...             paragraph
    #B  ...             bullet point
    #C                  start of a code block (following lines, until #E)
    #T  h1 | h2         table header; following lines are rows until #E
    #E                  end of code block / table
    #PB                 page break

Usage:  python docs/engineering_guide.py
Output: docs/PLCSim_Engineering_Guide.docx
"""

from __future__ import annotations

import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "guide_content.txt"
OUT = HERE / "PLCSim_Engineering_Guide.docx"


def esc(t: str) -> str:
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def para(text: str, style: str = "Normal") -> str:
    return (f'<w:p><w:pPr><w:pStyle w:val="{style}"/></w:pPr>'
            f'<w:r><w:t xml:space="preserve">{esc(text)}</w:t></w:r></w:p>')


def code_block(lines: list[str]) -> str:
    inner = "".join(
        '<w:p><w:pPr><w:spacing w:after="0"/></w:pPr>'
        '<w:r><w:rPr><w:rFonts w:ascii="Consolas" w:hAnsi="Consolas"/>'
        f'<w:sz w:val="18"/></w:rPr>'
        f'<w:t xml:space="preserve">{esc(l)}</w:t></w:r></w:p>'
        for l in lines)
    return ('<w:tbl><w:tblPr><w:tblW w:w="0" w:type="auto"/>'
            '<w:tblBorders>'
            '<w:top w:val="single" w:sz="4" w:color="BFBFBF"/>'
            '<w:left w:val="single" w:sz="4" w:color="BFBFBF"/>'
            '<w:bottom w:val="single" w:sz="4" w:color="BFBFBF"/>'
            '<w:right w:val="single" w:sz="4" w:color="BFBFBF"/>'
            '</w:tblBorders>'
            '<w:tblCellMar><w:left w:w="120" w:type="dxa"/>'
            '<w:right w:w="120" w:type="dxa"/></w:tblCellMar></w:tblPr>'
            '<w:tr><w:tc><w:tcPr><w:shd w:val="clear" w:fill="F2F2F2"/>'
            f'</w:tcPr>{inner}</w:tc></w:tr></w:tbl>'
            '<w:p><w:pPr><w:pStyle w:val="Normal"/></w:pPr>'
            '<w:r><w:t></w:t></w:r></w:p>')


def table(headers: list[str], rows: list[list[str]]) -> str:
    def cell(t: str, bold: bool = False) -> str:
        rpr = "<w:rPr><w:b/></w:rPr>" if bold else ""
        shd = ('<w:tcPr><w:shd w:val="clear" w:fill="DEEAF6"/></w:tcPr>'
               if bold else "<w:tcPr/>")
        return (f"<w:tc>{shd}<w:p><w:r>{rpr}"
                f'<w:t xml:space="preserve">{esc(t)}</w:t></w:r></w:p></w:tc>')
    borders = ('<w:tblBorders>'
               '<w:top w:val="single" w:sz="6" w:color="4472C4"/>'
               '<w:left w:val="single" w:sz="6" w:color="4472C4"/>'
               '<w:bottom w:val="single" w:sz="6" w:color="4472C4"/>'
               '<w:right w:val="single" w:sz="6" w:color="4472C4"/>'
               '<w:insideH w:val="single" w:sz="4" w:color="9DC3E6"/>'
               '<w:insideV w:val="single" w:sz="4" w:color="9DC3E6"/>'
               '</w:tblBorders>')
    head_row = "<w:tr>" + "".join(cell(c, True) for c in headers) + "</w:tr>"
    body = "".join("<w:tr>" + "".join(cell(c) for c in r) + "</w:tr>"
                   for r in rows)
    return ('<w:tbl><w:tblPr><w:tblW w:w="0" w:type="auto"/>' + borders +
            f'</w:tblPr>{head_row}{body}</w:tbl>')


def parse(lines: list[str]) -> str:
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("#H1 "):
            out.append(para(line[4:], "Heading1"))
        elif line.startswith("#H2 "):
            out.append(para(line[4:], "Heading2"))
        elif line.startswith("#H3 "):
            out.append(para(line[4:], "Heading3"))
        elif line.startswith("#P "):
            out.append(para(line[3:]))
        elif line.startswith("#B "):
            out.append(para(line[3:], "ListBullet"))
        elif line.startswith("#PB"):
            out.append('<w:p><w:pPr><w:pStyle w:val="Normal"/></w:pPr>'
                       '<w:r><w:br w:type="page"/></w:r></w:p>')
        elif line.startswith("#T "):
            headers = [c.strip() for c in line[3:].split("|")]
            rows: list[list[str]] = []
            i += 1
            while i < len(lines) and not lines[i].startswith("#E"):
                rows.append([c.strip() for c in lines[i].split("|")])
                i += 1
            out.append(table(headers, rows))
        elif line.startswith("#C"):
            block: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].startswith("#E"):
                block.append(lines[i])
                i += 1
            out.append(code_block(block))
        elif line.strip() == "":
            pass
        else:
            raise ValueError(f"unknown markup: {line[:40]!r}")
        i += 1
    return "".join(out)


def styles_xml() -> str:
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:docDefaults><w:rPrDefault><w:rPr>'
            '<w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/><w:sz w:val="22"/>'
            '</w:rPr></w:rPrDefault></w:docDefaults>'
            '<w:style w:type="paragraph" w:default="1" w:styleId="Normal">'
            '<w:name w:val="Normal"/><w:qFormat/>'
            '<w:pPr><w:spacing w:after="120"/></w:pPr></w:style>'
            '<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/>'
            '<w:basedOn w:val="Normal"/><w:pPr><w:jc w:val="center"/></w:pPr>'
            '<w:rPr><w:b/><w:sz w:val="48"/><w:color w:val="1F3864"/></w:rPr></w:style>'
            '<w:style w:type="paragraph" w:styleId="Subtitle"><w:name w:val="Subtitle"/>'
            '<w:basedOn w:val="Normal"/><w:pPr><w:jc w:val="center"/></w:pPr>'
            '<w:rPr><w:i/><w:sz w:val="28"/><w:color w:val="2E74B5"/></w:rPr></w:style>'
            '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/>'
            '<w:basedOn w:val="Normal"/><w:next w:val="Normal"/>'
            '<w:pPr><w:keepNext/><w:spacing w:before="320" w:after="160"/></w:pPr>'
            '<w:rPr><w:b/><w:sz w:val="32"/><w:color w:val="1F3864"/></w:rPr></w:style>'
            '<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/>'
            '<w:basedOn w:val="Normal"/><w:next w:val="Normal"/>'
            '<w:pPr><w:keepNext/><w:spacing w:before="240" w:after="120"/></w:pPr>'
            '<w:rPr><w:b/><w:sz w:val="26"/><w:color w:val="2E74B5"/></w:rPr></w:style>'
            '<w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/>'
            '<w:basedOn w:val="Normal"/><w:next w:val="Normal"/>'
            '<w:pPr><w:keepNext/><w:spacing w:before="160" w:after="80"/></w:pPr>'
            '<w:rPr><w:b/><w:sz w:val="23"/><w:color w:val="404040"/></w:rPr></w:style>'
            '<w:style w:type="paragraph" w:styleId="ListBullet">'
            '<w:name w:val="List Bullet"/><w:basedOn w:val="Normal"/>'
            '<w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>'
            '<w:spacing w:after="60"/></w:pPr></w:style>'
            '</w:styles>')


def numbering_xml() -> str:
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:abstractNum w:abstractNumId="0">'
            '<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="bullet"/>'
            '<w:lvlText w:val="&#8226;"/><w:lvlJc w:val="left"/>'
            '<w:pPr><w:ind w:left="720" w:hanging="360"/></w:pPr></w:lvl>'
            '</w:abstractNum>'
            '<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>'
            '</w:numbering>')


def build() -> None:
    lines = SRC.read_text(encoding="utf-8").splitlines()
    body = parse(lines)
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                f'<w:body>{body}'
                '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/>'
                '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/>'
                '</w:sectPr></w:body></w:document>')
    content_types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                     '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                     '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                     '<Default Extension="xml" ContentType="application/xml"/>'
                     '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                     '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
                     '<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>'
                     '</Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '</Relationships>')
    doc_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>'
                '</Relationships>')
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", document)
        z.writestr("word/_rels/document.xml.rels", doc_rels)
        z.writestr("word/styles.xml", styles_xml())
        z.writestr("word/numbering.xml", numbering_xml())
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    build()
