"""Transacciones SQL; cada operación usa su propia conexión."""

from contextlib import contextmanager
from datetime import datetime, date

import pyodbc
import re


def diagnostico_sql(exc):
    """Solo códigos y mensajes propios; nunca imprimir el mensaje del controlador."""
    estado = str(exc.args[0]) if exc.args else ''
    if not re.fullmatch(r'[A-Z0-9]{5}', estado):
        estado = 'XXXXX'
    detalle = ' '.join(str(arg) for arg in exc.args[1:]).lower()
    if any(t in detalle for t in ('certificate', 'certificado', 'ssl provider', 'tls')):
        causa = 'Fallo TLS/certificado. Revisar certificado del servidor y confianza del cliente.'
    elif estado == '28000':
        causa = 'Autenticación rechazada. Revisar usuario, contraseña y modo de autenticación SQL.'
    elif '(4060)' in detalle:
        causa = 'La base no existe o el usuario no tiene acceso a ella.'
    elif estado == '42S02':
        causa = 'No se encuentra una tabla. Revisar PETICIONES_SUNEDU, PERSONA, DNI y SUNEDU_V2 en la base configurada.'
    elif estado == '42S22':
        causa = 'Faltan columnas: revisar el esquema de las tablas configuradas.'
    elif '(229)' in detalle:
        causa = 'El usuario no tiene los permisos SQL requeridos.'
    elif estado.startswith('IM'):
        causa = 'No se pudo cargar el controlador ODBC. Revisar DRIVER en la conexión.'
    elif estado.startswith(('08', 'HYT')):
        causa = 'No se pudo conectar a tiempo. Revisar servidor, instancia, puerto y red.'
    else:
        causa = 'Error SQL no clasificado; revisar configuración, permisos y esquema.'
    return f'SQLSTATE {estado}: {causa}'


class SqlStore:
    def __init__(self, connection_string):
        self.connection_string = connection_string

    @contextmanager
    def conexion(self):
        con = pyodbc.connect(self.connection_string, timeout=10, autocommit=False)
        con.timeout = 30
        try:
            yield con
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    def reclamar(self, propietario):
        # UPDATE y lectura de DNI forman una sola transacción.
        with self.conexion() as con:
            fila = con.execute("""
                ;WITH siguiente AS (
                    SELECT TOP (1) * FROM dbo.PETICIONES_SUNEDU
                    WITH (UPDLOCK, READPAST, READCOMMITTEDLOCK)
                    WHERE FLAG IS NULL AND PETICIONTOMADA IS NULL
                    ORDER BY ID_PETICION
                )
                UPDATE siguiente SET PETICIONTOMADA = 1
                OUTPUT inserted.ID_PETICION, inserted.ID_PERSONA;
            """).fetchone()
            if not fila:
                return None
            documento = con.execute("""
                SELECT d.ID, d.DNI FROM dbo.PERSONA p
                JOIN dbo.DNI d ON d.ID = p.DNI WHERE p.ID = ?
            """, fila[1]).fetchone()
            # Un documento inexistente se registra como fallo sin consultar SUNEDU.
            dni = str(documento[1]).strip() if documento and documento[1] is not None else ''
            return int(fila[0]), dni

    def finalizar(self, solicitud, propietario, dni, grados, error=None):
        with self.conexion() as con:
            fila = con.execute("""
                SELECT FLAG, PETICIONTOMADA, ID_PERSONA
                FROM dbo.PETICIONES_SUNEDU WITH (UPDLOCK, HOLDLOCK)
                WHERE ID_PETICION = ?
            """, solicitud).fetchone()
            if not fila:
                raise RuntimeError('Petición inexistente')
            if fila[0] == 1:
                return
            if fila[0] is not None or fila[1] != 1:
                raise RuntimeError('La petición ya no está tomada')
            if error:
                return  # FLAG sigue NULL; el servicio informa el error por ID_PETICION.
            documento = con.execute("""
                SELECT d.DNI FROM dbo.PERSONA p JOIN dbo.DNI d ON d.ID = p.DNI
                WHERE p.ID = ?
            """, fila[2]).fetchone()
            if not documento or str(documento[0]).strip() != dni:
                raise RuntimeError('El documento de la petición cambió')
            self.guardar_grados(con, fila[2], grados)
            con.execute("""
                UPDATE dbo.PETICIONES_SUNEDU SET FLAG = 1 WHERE ID_PETICION = ?
            """, solicitud)

    def guardar_grados(self, con, id_persona, grados):
        for grado in grados:
            valores = (grado.get('Grado o título'), fecha_sql(grado.get('Fecha de diploma')),
                       fecha_sql(grado.get('Fecha matrícula')), fecha_sql(grado.get('Fecha egreso')),
                       grado.get('Institución'), id_persona)
            if not valores[0] or not valores[4]:
                raise ValueError('Falta título o institución para identificar el grado')
            existentes = con.execute("""
                SELECT ID_SUNEDU, DIPLFEC FROM dbo.SUNEDU_V2 WITH (UPDLOCK, HOLDLOCK)
                WHERE ID_PERSONA = ? AND GRADTITU = ? AND UNIVERSIDAD = ?
            """, id_persona, valores[0], valores[4]).fetchall()
            if len(existentes) > 1:
                existentes = [r for r in existentes if r[1] == valores[1]]
                if len(existentes) != 1:
                    raise ValueError('Varios registros coinciden con el grado; revisar duplicados')
            if existentes:
                con.execute("""
                    UPDATE dbo.SUNEDU_V2 SET GRADTITU = ?, DIPLFEC = ?, MATRIFEC = ?,
                        EGRESFEC = ?, UNIVERSIDAD = ?, ULTIMA_FECHA = GETDATE()
                    WHERE ID_SUNEDU = ?
                """, *valores[:5], existentes[0][0])
            else:
                con.execute("""
                    INSERT dbo.SUNEDU_V2
                    (GRADTITU, DIPLFEC, MATRIFEC, EGRESFEC, UNIVERSIDAD, ID_PERSONA, ULTIMA_FECHA)
                    VALUES (?, ?, ?, ?, ?, ?, GETDATE())
                """, *valores)

    def siguiente_fondo(self):
        with self.conexion() as con:
            fila = con.execute('EXEC dbo.SP_OBTENERDNI_SUELDO').fetchone()
            if fila is None:
                fila = con.execute('EXEC dbo.SP_OBTENERDNI_DNI').fetchone()
            return (int(fila[0]), str(fila[1]).strip()) if fila else None

    def finalizar_fondo(self, dni_id, dni, grados):
        with self.conexion() as con:
            documento = con.execute('SELECT DNI, PROCESADO_SUNEDU FROM dbo.DNI WITH (UPDLOCK, HOLDLOCK) WHERE ID = ?', dni_id).fetchone()
            if not documento or str(documento[0]).strip() != dni:
                raise ValueError('Documento de fondo cambiado o inexistente')
            if documento[1] == 1:
                return
            personas = con.execute('SELECT ID FROM dbo.PERSONA WHERE DNI = ? ORDER BY ID', dni_id).fetchall()
            if not personas:
                raise ValueError('No existe PERSONA asociada al documento de fondo')
            for persona in personas:
                self.guardar_grados(con, persona[0], grados)
            con.execute('EXEC dbo.SP_ACTUALIZAR_PROCES0_DNI @DniId = ?', dni_id)



def fecha_sql(valor):
    if valor is None or not str(valor).strip() or 'sin informaci' in str(valor).lower():
        return None
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    texto = str(valor).strip()
    for formato in ('%d/%m/%Y', '%d/%m/%y', '%Y-%m-%d', '%Y-%m-%dT%H:%M:%S'):
        try:
            return datetime.strptime(texto, formato).date()
        except ValueError:
            pass
    raise ValueError('Fecha SUNEDU no reconocida; no se completó la petición')


if __name__ == '__main__':
    import os
    from pathlib import Path
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).with_name('.env'))
    cadena = os.getenv('SQLSERVER_CONNECTION_STRING')
    if not cadena:
        print('Falta SQLSERVER_CONNECTION_STRING en .env.')
        raise SystemExit(1)
    try:
        with SqlStore(cadena).conexion() as con:
            con.execute('SELECT TOP (0) ID_PETICION, ID_PERSONA, FLAG, PETICIONTOMADA FROM dbo.PETICIONES_SUNEDU')
            con.execute('SELECT TOP (0) p.ID, d.DNI FROM dbo.PERSONA p JOIN dbo.DNI d ON d.ID = p.DNI')
            con.execute('SELECT TOP (0) GRADTITU, DIPLFEC, MATRIFEC, EGRESFEC, UNIVERSIDAD, ID_PERSONA, ULTIMA_FECHA FROM dbo.SUNEDU_V2')
        print('Conexión y lectura del esquema correctas. No se modificaron solicitudes.')
    except pyodbc.Error as exc:
        print(diagnostico_sql(exc))
        raise SystemExit(1)
