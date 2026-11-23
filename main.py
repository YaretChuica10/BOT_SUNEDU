"""Entrada de consultas RPA individuales y por lote con salida a Excel."""

import getpass
import argparse
from datetime import datetime
from pathlib import Path
import sys

import pyautogui

from rpa_query import RpaError, consultar_rpa

def main():
    parser = argparse.ArgumentParser(description="Consultas de SUNEDU por DNI.")
    parser.add_argument("--lote", metavar="ARCHIVO", help="Archivo de DNIs, uno por línea.")
    parser.add_argument("--salida", metavar="EXCEL", help="Archivo Excel del lote.")
    args = parser.parse_args()
    try:
        if args.lote:
            from batch_query import consultar_lote, leer_dnis
            dnis = leer_dnis(args.lote)
            destino = Path(args.salida) if args.salida else Path("resultados") / f"sunedu_{datetime.now():%Y%m%d_%H%M%S_%f}.xlsx"
            if destino.exists():
                raise RpaError("El Excel de salida ya existe. Usa otro nombre para conservarlo.")
            consultas, grados = consultar_lote(dnis, destino)
            print(f"Excel guardado en {destino.resolve()}")
            print(f"Consultas con resultados: {sum(row['Estado'] == 'Con resultados' for row in consultas)}/{len(consultas)}. Grados: {len(grados)}.")
            return 0 if all(row["Estado"] in ("Con resultados", "Sin resultados") for row in consultas) else 1
        dni = getpass.getpass("DNI autorizado (8 dígitos, entrada oculta): ").strip()
        print(consultar_rpa(dni))
        return 0
    except RpaError as error:
        print(str(error), file=sys.stderr)
        return 1
    except (OSError, ValueError) as error:
        print(f"No se pudo procesar el archivo de entrada o guardar el Excel ({type(error).__name__}).", file=sys.stderr)
        return 1
    except pyautogui.FailSafeException:
        print("RPA detenida mediante la esquina de seguridad del mouse.", file=sys.stderr)
        return 130
    except (KeyboardInterrupt, EOFError):
        print("\nConsulta cancelada.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
