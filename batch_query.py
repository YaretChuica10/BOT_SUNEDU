"""Consultas consecutivas y exportación del avance a Excel."""

from datetime import datetime
from pathlib import Path
import re
import sys
import time
import unicodedata

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
import pyautogui

from rpa_query import RpaError, consultar_rpa

PERSONA = re.compile(r"(?m)^([^\n]+?)\s+DNI\s*:?\s*(\d{8})\s*$")
FECHA_LABEL = (
    r"Fecha\s+(?:de\s+)?(?:diploma|matr[ií]cula|egreso|expedici[oó]n|"
    r"resoluci[oó]n|inscripci[oó]n|emisi[oó]n)\s*:"
)
FECHA = re.compile(
    rf"(?P<etiqueta>{FECHA_LABEL})\s*(?P<valor>[^\n]*?)(?=\s*{FECHA_LABEL}|\n|$)",
    re.IGNORECASE,
)
COLUMNAS_FECHAS = [
    "Fecha de diploma", "Fecha matrícula", "Fecha egreso", "Fecha de expedición",
    "Fecha de resolución", "Fecha de inscripción", "Fecha de emisión",
]


def extraer_grados(texto, dni):
    """Interpreta el texto de las filas y conserva cada bloque de origen."""
    personas = list(PERSONA.finditer(texto))
    if not personas or any(persona.group(2) != dni for persona in personas):
        raise ValueError("No se pudo asociar cada fila de resultados con el DNI consultado.")
    grados = []
    for index, persona in enumerate(personas):
        end = personas[index + 1].start() if index + 1 < len(personas) else len(texto)
        bloque = texto[persona.end():end].strip()
        fechas = list(FECHA.finditer(bloque))
        if not fechas:
            raise ValueError("Se encontró una fila de graduado sin fechas reconocibles. Revisar el texto capturado.")
        grado = " ".join(bloque[:fechas[0].start()].split())
        # La institución ocupa la línea inmediatamente posterior a las fechas.
        lineas = [line.strip() for line in bloque[fechas[-1].end():].splitlines() if line.strip()]
        if not grado or not lineas:
            raise ValueError("No se identificaron el grado y la institución de todas las filas.")
        record = {
            "DNI": dni,
            "Nombres": persona.group(1).strip(),
            "Grado o título": grado,
            "Institución": lineas[0],
        }
        for fecha in fechas:
            label = "".join(c for c in unicodedata.normalize("NFD", fecha.group("etiqueta").lower()) if not unicodedata.combining(c))
            for columna in COLUMNAS_FECHAS:
                word = "".join(c for c in unicodedata.normalize("NFD", columna.split()[-1].lower()) if not unicodedata.combining(c))
                if word in label:
                    record[columna] = fecha.group("valor").strip()
                    break
        grados.append(record)
    return grados


def leer_dnis(path):
    dnis = Path(path).read_text(encoding="utf-8-sig").split()
    if not dnis or any(not re.fullmatch(r"[0-9]{8}", dni) for dni in dnis):
        raise ValueError("El archivo debe contener DNIs de ocho dígitos separados por espacios o líneas.")
    return list(dict.fromkeys(dnis))


def escribir_hoja(sheet, headers, rows, widths):
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    sheet.freeze_panes = "B2"
    sheet.auto_filter.ref = sheet.dimensions
    sheet.sheet_view.showGridLines = False
    for index, width in enumerate(widths, 1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    for row in sheet:
        for cell in row:
            if isinstance(cell.value, str):
                # Los valores del sitio se escriben como texto literal en Excel.
                cell.data_type = "s"
                cell.number_format = "@"
            cell.font = Font(name="Arial", size=10)
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    for cell in sheet[1]:
        cell.font = Font(name="Arial", size=10, bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="174A7E")
    sheet.row_dimensions[1].height = 32


def guardar_excel(consultas, grados, destino):
    workbook = Workbook()
    summary = workbook.active
    summary.title = "Consultas"
    headers = ["DNI", "Estado", "Grados", "Fecha de consulta", "Duración (s)", "Detalle"]
    escribir_hoja(summary, headers, [[row.get(key) for key in headers] for row in consultas],
                  [13, 24, 10, 22, 16, 85])
    for row in summary.iter_rows(min_row=2):
        row[3].number_format = "dd/mm/yyyy hh:mm:ss"
        row[4].number_format = "0.0"
        summary.row_dimensions[row[0].row].height = 60
    details = workbook.create_sheet("Grados")
    headers_grados = ["DNI", "Nombres", "Grado o título", "Institución", *COLUMNAS_FECHAS]
    escribir_hoja(details, headers_grados,
                  [[row.get(key, "") for key in headers_grados] for row in grados],
                  [13, 40, 58, 58, *([23] * len(COLUMNAS_FECHAS))])
    for index in range(2, details.max_row + 1):
        details.row_dimensions[index].height = 60
    # Permite revisar una extracción fallida sin perder el texto original.
    sources = workbook.create_sheet("Texto SUNEDU")
    source_rows = []
    for row in consultas:
        text = row.get("Texto", "")
        for index, start in enumerate(range(0, len(text), 30000), 1):
            source_rows.append([row["DNI"], index, text[start:start + 30000]])
    escribir_hoja(sources, ["DNI", "Parte", "Texto capturado"], source_rows, [13, 10, 110])
    for index in range(2, sources.max_row + 1):
        sources.row_dimensions[index].height = 120
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporal = destino.with_suffix(".tmp.xlsx")
    try:
        workbook.save(temporal)
        temporal.replace(destino)
    finally:
        workbook.close()
        temporal.unlink(missing_ok=True)


def consultar_lote(dnis, destino):
    consultas = [{"DNI": dni, "Estado": "Pendiente"} for dni in dnis]
    grados = []
    guardar_excel(consultas, grados, destino)
    for index, consulta in enumerate(consultas, 1):
        print(f"[Lote] Consulta {index}/{len(consultas)}.", file=sys.stderr, flush=True)
        start = time.monotonic()
        consulta["Fecha de consulta"] = datetime.now()
        detener = False
        try:
            texto = consultar_rpa(consulta["DNI"], cerrar_al_terminar=True)
            consulta["Texto"] = texto
            normal = "".join(c for c in unicodedata.normalize("NFD", texto.lower()) if not unicodedata.combining(c))
            if "no encontro resultados" in normal or "no se encontraron resultados" in normal:
                consulta.update({"Estado": "Sin resultados", "Grados": 0})
            else:
                registros = extraer_grados(texto, consulta["DNI"])
                grados.extend(registros)
                consulta.update({"Estado": "Con resultados", "Grados": len(registros)})
        except ValueError as error:
            consulta.update({"Estado": "Revisar extracción", "Detalle": str(error)})
        except RpaError as error:
            consulta.update({"Estado": "Error de consulta", "Detalle": str(error)})
        except (KeyboardInterrupt, pyautogui.FailSafeException):
            consulta.update({"Estado": "Cancelada", "Detalle": "Lote detenido por el usuario."})
            detener = True
        except Exception as error:
            consulta.update({"Estado": "Error de consulta", "Detalle": f"Error interno de tipo {type(error).__name__}."})
        finally:
            consulta["Duración (s)"] = round(time.monotonic() - start, 1)
            guardar_excel(consultas, grados, destino)
        print(f"[Lote] {consulta['Estado']}.", file=sys.stderr, flush=True)
        if detener:
            break
    return consultas, grados
