"""Inicio del servicio BOT_SUNEDU."""
import asyncio
import math
import os
from pathlib import Path
from dotenv import load_dotenv
from database import SqlStore
from service import servir


def main():
    load_dotenv(Path(__file__).with_name('.env'))
    cadena = os.getenv('SQLSERVER_CONNECTION_STRING')
    if not cadena:
        raise ValueError('Falta SQLSERVER_CONNECTION_STRING en .env.')
    intervalo = float(os.getenv('SQL_POLL_SECONDS', '5'))
    if not math.isfinite(intervalo) or intervalo <= 0:
        raise ValueError('SQL_POLL_SECONDS debe ser positivo.')
    asyncio.run(servir(SqlStore(cadena), intervalo))


if __name__ == '__main__':
    try:
        main()
    except ValueError:
        print('Configuración inválida: revisa SQLSERVER_CONNECTION_STRING y SQL_POLL_SECONDS en .env.')
        raise SystemExit(1)
    except KeyboardInterrupt:
        print('Interrumpido.')
