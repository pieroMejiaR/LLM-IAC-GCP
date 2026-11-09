"""Script para generar el informe final en formato Word (.docx) a partir de docs/informe.md.

Utiliza python-docx aplicando estilos profesionales acordes al estándar de la UNSA / GESITI.
"""
import re
from pathlib import Path
import docx
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_ALIGN_VERTICAL
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import nsdecls, qn

DOCX_OUTPUT = Path("docs/INFORME_FINAL_GESITI_IAC_GCP.docx")
MD_INPUT = Path("docs/informe.md")

# Colores del tema institucional
COLOR_PRIMARY = RGBColor(0x1F, 0x4E, 0x79)     # Azul Institucional #1F4E79
COLOR_SECONDARY = RGBColor(0x2E, 0x75, 0xB6)   # Azul Medio #2E75B6
COLOR_TEXT = RGBColor(0x26, 0x26, 0x26)        # Gris Carbón #262626
COLOR_MUTED = RGBColor(0x59, 0x59, 0x59)       # Gris Medio
COLOR_CODE = RGBColor(0x80, 0x00, 0x20)        # Borgoña / Código
HEX_PRIMARY = "1F4E79"
HEX_LIGHT_BG = "F2F5F8"
HEX_BORDER = "D3D3D3"
HEX_ALT_ROW = "F9FAFB"


def set_cell_background(cell, hex_color):
    shading_elm = parse_xml(f'<w:shd {nsdecls("w")} w:fill="{hex_color}"/>')
    cell._tc.get_or_add_tcPr().append(shading_elm)


def set_cell_margins(cell, top=100, bottom=100, left=150, right=150):
    tcPr = cell._tc.get_or_add_tcPr()
    tcMar = parse_xml(
        f'<w:tcMar {nsdecls("w")}>'
        f'<w:top w:w="{top}" w:type="dxa"/>'
        f'<w:bottom w:w="{bottom}" w:type="dxa"/>'
        f'<w:left w:w="{left}" w:type="dxa"/>'
        f'<w:right w:w="{right}" w:type="dxa"/>'
        f'</w:tcMar>'
    )
    tcPr.append(tcMar)


def add_callout(doc, text, title="NOTA"):
    table = doc.add_table(rows=1, cols=1)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    
    cell = table.cell(0, 0)
    cell.width = Inches(6.5)
    set_cell_background(cell, HEX_LIGHT_BG)
    set_cell_margins(cell, top=140, bottom=140, left=200, right=200)
    
    # Borde izquierdo grueso
    tcPr = cell._tc.get_or_add_tcPr()
    borders = parse_xml(
        f'<w:tcBorders {nsdecls("w")}>'
        f'<w:left w:val="single" w:sz="24" w:space="0" w:color="{HEX_PRIMARY}"/>'
        f'<w:top w:val="none"/>'
        f'<w:bottom w:val="none"/>'
        f'<w:right w:val="none"/>'
        f'</w:tcBorders>'
    )
    tcPr.append(borders)
    
    p = cell.paragraphs[0]
    p.paragraph_format.space_before = Pt(2)
    p.paragraph_format.space_after = Pt(2)
    p.paragraph_format.line_spacing = 1.15
    
    r_title = p.add_run(f"[{title}] ")
    r_title.bold = True
    r_title.font.name = "Calibri"
    r_title.font.size = Pt(10)
    r_title.font.color.rgb = COLOR_PRIMARY
    
    r_text = p.add_run(text)
    r_text.font.name = "Calibri"
    r_text.font.size = Pt(10)
    r_text.font.color.rgb = COLOR_TEXT


def format_table(table, col_widths=None):
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    for r_idx, row in enumerate(table.rows):
        is_header = (r_idx == 0)
        # trPr para evitar división entre páginas
        trPr = row._tr.get_or_add_trPr()
        trPr.append(parse_xml(f'<w:cantSplit {nsdecls("w")}/>'))
        if is_header:
            trPr.append(parse_xml(f'<w:tblHeader {nsdecls("w")}/>'))
            
        for c_idx, cell in enumerate(row.cells):
            cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
            set_cell_margins(cell, top=100, bottom=100, left=140, right=140)
            if col_widths and c_idx < len(col_widths):
                cell.width = Inches(col_widths[c_idx])
                
            if is_header:
                set_cell_background(cell, HEX_PRIMARY)
                for p in cell.paragraphs:
                    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    for r in p.runs:
                        r.font.name = "Calibri"
                        r.font.bold = True
                        r.font.size = Pt(9.5)
                        r.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
            else:
                if r_idx % 2 == 1:
                    set_cell_background(cell, HEX_ALT_ROW)
                else:
                    set_cell_background(cell, "FFFFFF")
                for p in cell.paragraphs:
                    for r in p.runs:
                        r.font.name = "Calibri"
                        r.font.size = Pt(9)
                        r.font.color.rgb = COLOR_TEXT


def build_docx():
    doc = docx.Document()
    
    # Configuración de página
    for section in doc.sections:
        section.top_margin = Inches(1.0)
        section.bottom_margin = Inches(1.0)
        section.left_margin = Inches(1.0)
        section.right_margin = Inches(1.0)
        section.page_width = Inches(8.5)
        section.page_height = Inches(11.0)
        
        # Encabezado y pie
        header = section.header
        p_hdr = header.paragraphs[0]
        p_hdr.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        r_hdr = p_hdr.add_run("UNSA | GESITI 2026-B — Agente IaC para Google Cloud Platform")
        r_hdr.font.name = "Calibri"
        r_hdr.font.size = Pt(8.5)
        r_hdr.font.color.rgb = COLOR_MUTED
        
        footer = section.footer
        p_ftr = footer.paragraphs[0]
        p_ftr.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r_ftr = p_ftr.add_run("Informe Técnico Experimental — Página ")
        r_ftr.font.name = "Calibri"
        r_ftr.font.size = Pt(8.5)
        r_ftr.font.color.rgb = COLOR_MUTED
        # Campo de número de página
        fldSimple = parse_xml(f'<w:fldSimple {nsdecls("w")} w:instr="PAGE"/>')
        p_ftr._p.append(fldSimple)

    # -------------------------------------------------------------
    # CARÁTULA
    # -------------------------------------------------------------
    p_inst = doc.add_paragraph()
    p_inst.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_inst.paragraph_format.space_before = Pt(20)
    p_inst.paragraph_format.space_after = Pt(2)
    r = p_inst.add_run("UNIVERSIDAD NACIONAL DE SAN AGUSTÍN DE AREQUIPA")
    r.bold = True
    r.font.name = "Calibri"
    r.font.size = Pt(16)
    r.font.color.rgb = COLOR_PRIMARY

    p_fac = doc.add_paragraph()
    p_fac.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_fac.paragraph_format.space_after = Pt(2)
    r = p_fac.add_run("FACULTAD DE INGENIERÍA DE PRODUCCIÓN Y SERVICIOS")
    r.bold = True
    r.font.name = "Calibri"
    r.font.size = Pt(13)
    r.font.color.rgb = COLOR_SECONDARY

    p_esc = doc.add_paragraph()
    p_esc.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_esc.paragraph_format.space_after = Pt(36)
    r = p_esc.add_run("ESCUELA PROFESIONAL DE INGENIERÍA DE SISTEMAS")
    r.bold = True
    r.font.name = "Calibri"
    r.font.size = Pt(12)
    r.font.color.rgb = COLOR_TEXT

    p_tipo = doc.add_paragraph()
    p_tipo.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_tipo.paragraph_format.space_after = Pt(12)
    r = p_tipo.add_run("INFORME TÉCNICO DE INVESTIGACIÓN EXPERIMENTAL")
    r.bold = True
    r.font.name = "Calibri"
    r.font.size = Pt(13)
    r.font.color.rgb = COLOR_PRIMARY

    p_title = doc.add_paragraph()
    p_title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_title.paragraph_format.space_after = Pt(36)
    p_title.paragraph_format.line_spacing = 1.2
    r = p_title.add_run(
        "EFECTO DE UN AGENTE BASADO EN MODELOS DE LENGUAJE GRANDE "
        "CON VALIDACIÓN AUTOMÁTICA Y SANDBOX SEGURO EN LA GENERACIÓN DE "
        "INFRAESTRUCTURA COMO CÓDIGO PARA GOOGLE CLOUD PLATFORM"
    )
    r.bold = True
    r.font.name = "Calibri"
    r.font.size = Pt(18)
    r.font.color.rgb = COLOR_PRIMARY

    # Cuadro de datos
    tbl_meta = doc.add_table(rows=6, cols=2)
    tbl_meta.alignment = WD_TABLE_ALIGNMENT.CENTER
    meta_data = [
        ("ASIGNATURA:", "Gestión de Sistemas y Tecnologías de Información (GESITI)"),
        ("SEMESTRE ACADÉMICO:", "2026-B (Décimo Semestre)"),
        ("DOCENTE ASESOR:", "Mg. Roberto Carlos Espinoza Ccama"),
        ("INTEGRANTES:", "• Callocondo Garay, Santiago Stuart (scallocondo@unsa.edu.pe)\n"
                         "• Llerena Morón, Gabriel Alonso (gllerenamo@unsa.edu.pe)\n"
                         "• Mejía Ramos, Piero Alexander (pmejiar@unsa.edu.pe)\n"
                         "• Ramírez Melendres, Paolo Sebastián (pramirezm@unsa.edu.pe)"),
        ("REPOSITORIO:", "https://github.com/pieroMejiaR/LLM-IAC-GCP (Tag: v1.0-experimento)"),
        ("FECHA DE PRESENTACIÓN:", "10 de noviembre de 2026")
    ]
    for idx, (label, val) in enumerate(meta_data):
        row = tbl_meta.rows[idx]
        c0, c1 = row.cells[0], row.cells[1]
        c0.width = Inches(2.2)
        c1.width = Inches(4.3)
        p0 = c0.paragraphs[0]
        p0.paragraph_format.space_after = Pt(4)
        r0 = p0.add_run(label)
        r0.bold = True
        r0.font.name = "Calibri"
        r0.font.size = Pt(10)
        r0.font.color.rgb = COLOR_PRIMARY
        
        p1 = c1.paragraphs[0]
        p1.paragraph_format.space_after = Pt(4)
        p1.paragraph_format.line_spacing = 1.15
        r1 = p1.add_run(val)
        r1.font.name = "Calibri"
        r1.font.size = Pt(10)
        r1.font.color.rgb = COLOR_TEXT

    p_city = doc.add_paragraph()
    p_city.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_city.paragraph_format.space_before = Pt(48)
    r = p_city.add_run("AREQUIPA – PERÚ\n2026")
    r.bold = True
    r.font.name = "Calibri"
    r.font.size = Pt(11)
    r.font.color.rgb = COLOR_MUTED

    doc.add_page_break()

    # -------------------------------------------------------------
    # PROCESAMIENTO DEL MARKDOWN
    # -------------------------------------------------------------
    md_text = MD_INPUT.read_text(encoding="utf-8")
    lines = md_text.splitlines()

    in_code_block = False
    code_lines = []
    code_lang = ""
    in_table = False
    table_rows_data = []

    def flush_table():
        nonlocal in_table, table_rows_data
        if not table_rows_data:
            in_table = False
            return
        
        # Filtra separadores markdown | --- | --- |
        clean_rows = []
        for r in table_rows_data:
            if all(re.match(r"^:?-+:?$", cell.strip()) for cell in r if cell.strip()):
                continue
            clean_rows.append(r)
            
        if clean_rows:
            num_cols = max(len(r) for r in clean_rows)
            tbl = doc.add_table(rows=len(clean_rows), cols=num_cols)
            for r_i, row_data in enumerate(clean_rows):
                for c_i in range(num_cols):
                    val = row_data[c_i] if c_i < len(row_data) else ""
                    cell = tbl.cell(r_i, c_i)
                    p = cell.paragraphs[0]
                    p.paragraph_format.space_before = Pt(2)
                    p.paragraph_format.space_after = Pt(2)
                    p.paragraph_format.line_spacing = 1.05
                    # Procesa formato básico dentro de celdas
                    add_formatted_runs(p, val.strip())
            format_table(tbl)
            doc.add_paragraph().paragraph_format.space_after = Pt(4)
            
        table_rows_data = []
        in_table = False

    def flush_code():
        nonlocal in_code_block, code_lines, code_lang
        if code_lines:
            code_text = "\n".join(code_lines)
            t = doc.add_table(rows=1, cols=1)
            t.alignment = WD_TABLE_ALIGNMENT.CENTER
            c = t.cell(0, 0)
            c.width = Inches(6.5)
            set_cell_background(c, "F4F4F6")
            set_cell_margins(c, top=80, bottom=80, left=120, right=120)
            
            p = c.paragraphs[0]
            p.paragraph_format.space_before = Pt(0)
            p.paragraph_format.space_after = Pt(0)
            p.paragraph_format.line_spacing = 1.0
            r = p.add_run(code_text)
            r.font.name = "Consolas"
            r.font.size = Pt(8.5)
            r.font.color.rgb = RGBColor(0x33, 0x33, 0x33)
            doc.add_paragraph().paragraph_format.space_after = Pt(4)
        code_lines = []
        code_lang = ""
        in_code_block = False

    def add_formatted_runs(para, text):
        # Maneja negrita **texto** y código `codigo`
        parts = re.split(r"(\*\*.*?\*\*|`.*?`)", text)
        for part in parts:
            if not part:
                continue
            if part.startswith("**") and part.endswith("**"):
                r = para.add_run(part[2:-2])
                r.bold = True
                r.font.name = "Calibri"
                r.font.color.rgb = COLOR_TEXT
            elif part.startswith("`") and part.endswith("`"):
                r = para.add_run(part[1:-1])
                r.font.name = "Consolas"
                r.font.size = Pt(9.5)
                r.font.color.rgb = COLOR_CODE
            else:
                r = para.add_run(part)
                r.font.name = "Calibri"
                r.font.color.rgb = COLOR_TEXT

    # Salta hasta el Resumen Ejecutivo (la carátula ya se generó)
    start_processing = False
    for line in lines:
        if "## RESUMEN EJECUTIVO" in line.upper():
            start_processing = True

        if not start_processing:
            continue

        # Bloques de código ```
        if line.startswith("```"):
            if in_code_block:
                flush_code()
            else:
                if in_table:
                    flush_table()
                in_code_block = True
                code_lang = line[3:].strip()
                code_lines = []
            continue

        if in_code_block:
            code_lines.append(line)
            continue

        # Tablas markdown | col | col |
        if line.strip().startswith("|") and line.strip().endswith("|"):
            if not in_table:
                in_table = True
                table_rows_data = []
            cells = [c for c in line.strip().split("|")[1:-1]]
            table_rows_data.append(cells)
            continue
        elif in_table:
            flush_table()

        # Notas / Callouts
        if line.startswith("> **Nota"):
            callout_text = line[2:].strip()
            add_callout(doc, callout_text, title="NOTA DE FACTURACIÓN")
            continue

        # Encabezados
        if line.startswith("# "):
            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(18)
            p.paragraph_format.space_after = Pt(6)
            p.paragraph_format.keep_with_next = True
            r = p.add_run(line[2:].strip())
            r.bold = True
            r.font.name = "Calibri"
            r.font.size = Pt(16)
            r.font.color.rgb = COLOR_PRIMARY
            continue

        if line.startswith("## "):
            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(16)
            p.paragraph_format.space_after = Pt(4)
            p.paragraph_format.keep_with_next = True
            r = p.add_run(line[3:].strip())
            r.bold = True
            r.font.name = "Calibri"
            r.font.size = Pt(14)
            r.font.color.rgb = COLOR_PRIMARY
            continue

        if line.startswith("### "):
            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(12)
            p.paragraph_format.space_after = Pt(3)
            p.paragraph_format.keep_with_next = True
            r = p.add_run(line[4:].strip())
            r.bold = True
            r.font.name = "Calibri"
            r.font.size = Pt(12)
            r.font.color.rgb = COLOR_SECONDARY
            continue

        if line.startswith("#### "):
            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(10)
            p.paragraph_format.space_after = Pt(2)
            p.paragraph_format.keep_with_next = True
            r = p.add_run(line[5:].strip())
            r.bold = True
            r.font.name = "Calibri"
            r.font.size = Pt(11)
            r.font.color.rgb = COLOR_TEXT
            continue

        # Líneas horizontales ---
        if line.strip() == "---":
            continue

        # Listas de viñetas
        if line.strip().startswith("- ") or line.strip().startswith("* "):
            p = doc.add_paragraph(style='List Bullet')
            p.paragraph_format.space_before = Pt(1)
            p.paragraph_format.space_after = Pt(2)
            p.paragraph_format.line_spacing = 1.15
            add_formatted_runs(p, line.strip()[2:])
            continue

        # Listas numeradas
        m_num = re.match(r"^(\d+)\.\s+(.*)$", line.strip())
        if m_num:
            p = doc.add_paragraph(style='List Number')
            p.paragraph_format.space_before = Pt(1)
            p.paragraph_format.space_after = Pt(2)
            p.paragraph_format.line_spacing = 1.15
            add_formatted_runs(p, m_num.group(2))
            continue

        # Párrafos normales
        if line.strip():
            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(0)
            p.paragraph_format.space_after = Pt(6)
            p.paragraph_format.line_spacing = 1.15
            add_formatted_runs(p, line.strip())

    if in_table:
        flush_table()
    if in_code_block:
        flush_code()

    DOCX_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    doc.save(DOCX_OUTPUT)
    print(f"Documento Word generado exitosamente en: {DOCX_OUTPUT.resolve()}")


if __name__ == "__main__":
    build_docx()
