"""Importacion septiembre 2026. Por defecto, siempre ROLLBACK."""
import sys
import json
import unicodedata
from pathlib import Path
from decimal import Decimal
from datetime import datetime

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from audit_db import connection

CSV = None
PERIODO = None

def clave(s):
    s = unicodedata.normalize("NFKD", str(s))
    return "".join(c for c in s if not unicodedata.combining(c)).strip().lower()

def normalizar_nombre(s):
    return str(s).strip().lower()

def ejecutar():
    if CSV is None or PERIODO is None:
        raise ValueError("Faltan parametros CSV y PERIODO")
    df = pd.read_csv(
        CSV, sep=";", encoding="cp1252",
        dtype=str, keep_default_na=False
    )

    columnas_originales = list(df.columns)
    df.columns = [clave(c).upper() for c in df.columns]

    if CSV is None or PERIODO is None:
        raise ValueError("Faltan parametros CSV y PERIODO")

    inicio_periodo = datetime.strptime(PERIODO, "%Y%m")
    if inicio_periodo.month == 12:
        fin_periodo = datetime(inicio_periodo.year + 1, 1, 1)
    else:
        fin_periodo = datetime(
            inicio_periodo.year, inicio_periodo.month + 1, 1
        )

    from datetime import timedelta
    fin_periodo_inclusivo = fin_periodo - timedelta(days=1)

    comerciales_esperadas = 0
    monto_esperado = Decimal("0")
    for _, fila in df.iterrows():
        if fila["ESTADO_ORDEN_ELECTRONICA"].strip() in {
            "ACEPTADA", "PAGADA"
        }:
            comerciales_esperadas += 1
        monto_esperado += Decimal(fila["TOTAL"].strip())



    with connection() as conn:
        conn.execute("SET TRANSACTION READ WRITE")
        try:
            antes = conn.execute(
                "SELECT count(*) AS n FROM peru_compras.ordenes"
            ).fetchone()["n"]


            historico_esperado = antes
            entidades_antes = conn.execute(
                "SELECT count(*) AS n FROM peru_compras.entidades"
            ).fetchone()["n"]
            proveedores_antes = conn.execute(
                "SELECT count(*) AS n FROM peru_compras.proveedores"
            ).fetchone()["n"]

            if antes != historico_esperado:
                raise ValueError("Historico inesperado: " + str(antes))


            # Controles previos: ninguna insercion debe ocurrir antes.
            requeridas = {
                "ORDEN_ELECTRONICA", "FECHA_FORMALIZACION",
                "ESTADO_ORDEN_ELECTRONICA", "SUB_TOTAL",
                "IGV", "TOTAL", "ACUERDO_MARCO",
                "RUC_ENTIDAD", "RUC_PROVEEDOR",
                "ENTIDAD", "PROVEEDOR",
                "TIPO_PROCEDIMIENTO", "ORDEN_DIGITALIZADA",
            }
            faltantes = requeridas - set(df.columns)
            if faltantes:
                raise ValueError(
                    "Columnas faltantes: " + str(sorted(faltantes))
                )

            if df.empty:
                raise ValueError("CSV vacio")

            ocam = df["ORDEN_ELECTRONICA"].str.strip()
            if ocam.eq("").any() or ocam.nunique() != len(df):
                raise ValueError("OCAM vacias o duplicadas")

            fechas = pd.to_datetime(
                df["FECHA_FORMALIZACION"],
                errors="coerce", format="mixed"
            )
            if fechas.isna().any():
                raise ValueError("Fechas invalidas")

            if not fechas.dt.strftime("%Y%m").eq(PERIODO).all():
                raise ValueError("Fechas fuera del periodo")

            for i, fila in df.iterrows():
                subtotal = Decimal(fila["SUB_TOTAL"].strip())
                igv = Decimal(fila["IGV"].strip())
                total = Decimal(fila["TOTAL"].strip())
                if subtotal + igv != total:
                    raise ValueError(
                        "Importe no concilia en fila " + str(i + 1)
                    )

            existentes = conn.execute("""
                SELECT count(*) AS n
                FROM peru_compras.ordenes
                WHERE fecha_formalizacion >= %s
                  AND fecha_formalizacion < %s
            """, (
                inicio_periodo.date(), fin_periodo.date()
            )).fetchone()["n"]

            if existentes != 0:
                raise ValueError(
                    "Periodo ya registrado: " + str(existentes)
                )

            repetidas = conn.execute("""
                SELECT ocam
                FROM peru_compras.ordenes
            """).fetchall()

            ocam_bd = {str(r["ocam"]).strip() for r in repetidas}
            duplicadas = set(ocam) & ocam_bd

            if duplicadas:
                raise ValueError(
                    "OCAM existentes: " + str(len(duplicadas))
                )

            print("VALIDACION_PREVIA=PASS")
            print("PERIODO:", PERIODO)
            print("OCAM_NUEVAS:", len(df))
            print("OCAM_DUPLICADAS:", len(duplicadas))
            print("PERIODO_PREVIAMENTE_REGISTRADO:", existentes)

            acuerdos = {}
            for r in conn.execute("""
                SELECT acuerdo_original, acuerdo_normalizado
                FROM peru_compras.acuerdos_marco
            """).fetchall():
                acuerdos.setdefault(
                    clave(r["acuerdo_original"]), set()
                ).add(r["acuerdo_normalizado"])

            for ruc_col, nombre_col, tabla, campo_original, campo_normal in [
                ("RUC_ENTIDAD", "ENTIDAD", "entidades",
                 "nombre_original", "nombre_normalizado"),
                ("RUC_PROVEEDOR", "PROVEEDOR", "proveedores",
                 "razon_social_original", "razon_social_normalizada")
            ]:
                personas = df[[ruc_col, nombre_col]].drop_duplicates(
                    subset=[ruc_col]
                )

                for _, r in personas.iterrows():
                    fecha_min = inicio_periodo.date()
                    fecha_max = fin_periodo_inclusivo.date()

                    conn.execute(f"""
                        INSERT INTO peru_compras.{tabla}
                        (ruc, {campo_original}, {campo_normal},
                         primera_fecha_observada, ultima_fecha_observada)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (ruc) DO NOTHING
                    """, (
                        r[ruc_col].strip(),
                        r[nombre_col].strip(),
                        normalizar_nombre(r[nombre_col]),
                        fecha_min,
                        fecha_max
                    ))

            total_csv = Decimal("0")
            comerciales = 0

            for i, r in df.iterrows():
                original = {
                    columnas_originales[j]: df.iloc[i, j]
                    for j in range(len(columnas_originales))
                }

                mercado = acuerdos.get(clave(r["ACUERDO_MARCO"]), set())

                if len(mercado) != 1:
                    raise ValueError("Mercado ambiguo en fila " + str(i))

                estado = r["ESTADO_ORDEN_ELECTRONICA"].strip()
                comercial = estado in {"ACEPTADA", "PAGADA"}

                if comercial:
                    comerciales += 1

                subtotal = Decimal(r["SUB_TOTAL"])
                igv = Decimal(r["IGV"])
                total = Decimal(r["TOTAL"])

                if subtotal + igv != total:
                    raise ValueError("Importe no concilia en fila " + str(i))

                fecha = datetime.fromisoformat(
                    r["FECHA_FORMALIZACION"].strip()
                ).date()

                conn.execute("""
                    INSERT INTO peru_compras.ordenes
                    (
                        ocam, ruc_proveedor, proveedor_original,
                        ruc_entidad, entidad_original, procedimiento,
                        estado_original, estado_comercial,
                        fecha_formalizacion, subtotal, igv, total,
                        orden_digitalizada, acuerdo_original,
                        acuerdo_normalizado, fuente_json
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s::jsonb
                    )
                """, (
                    r["ORDEN_ELECTRONICA"].strip(),
                    r["RUC_PROVEEDOR"].strip(),
                    r["PROVEEDOR"].strip(),
                    r["RUC_ENTIDAD"].strip(),
                    r["ENTIDAD"].strip(),
                    r["TIPO_PROCEDIMIENTO"].strip(),
                    estado,
                    comercial,
                    fecha,
                    subtotal,
                    igv,
                    total,
                    r["ORDEN_DIGITALIZADA"].strip(),
                    r["ACUERDO_MARCO"].strip(),
                    next(iter(mercado)),
                    json.dumps(original, ensure_ascii=False)
                ))

                total_csv += total

            despues = conn.execute("""
                SELECT count(*) AS n
                FROM peru_compras.ordenes
            """).fetchone()["n"]

            monto_periodo = conn.execute("""
                SELECT coalesce(sum(total), 0) AS monto
                FROM peru_compras.ordenes
                WHERE fecha_formalizacion >= %s
                  AND fecha_formalizacion < %s
            """, (inicio_periodo.date(), fin_periodo.date())).fetchone()["monto"]

            entidades_total = conn.execute("""
                SELECT count(*) AS n
                FROM peru_compras.entidades
            """).fetchone()["n"]

            proveedores_total = conn.execute("""
                SELECT count(*) AS n
                FROM peru_compras.proveedores
            """).fetchone()["n"]

            entidades_nuevas = entidades_total - entidades_antes
            proveedores_nuevos = proveedores_total - proveedores_antes

            relaciones_invalidas = conn.execute("""
                SELECT count(*) AS n
                FROM peru_compras.ordenes o
                LEFT JOIN peru_compras.entidades e
                    ON e.ruc = o.ruc_entidad
                LEFT JOIN peru_compras.proveedores p
                    ON p.ruc = o.ruc_proveedor
                WHERE o.fecha_formalizacion >= %s
                  AND o.fecha_formalizacion < %s
                  AND (e.ruc IS NULL OR p.ruc IS NULL)
            """, (inicio_periodo.date(), fin_periodo.date())).fetchone()["n"]

            print("=== VALIDACION DE TABLAS RELACIONADAS ===")
            print("ENTIDADES_TOTAL:", entidades_total)
            print("ENTIDADES_NUEVAS:", entidades_nuevas)
            print("PROVEEDORES_TOTAL:", proveedores_total)
            print("PROVEEDORES_NUEVOS:", proveedores_nuevos)
            print("RELACIONES_INVALIDAS:", relaciones_invalidas)

            if not (
                entidades_nuevas >= 0
                and proveedores_nuevos >= 0
                and relaciones_invalidas == 0
            ):
                raise ValueError(
                    "No concilian las entidades o proveedores"
                )

            print("=== SIMULACION TRANSACCIONAL ===")
            print("ORDENES_ANTES:", antes)
            print("ORDENES_DESPUES_DENTRO_TRANSACCION:", despues)
            print("ORDENES_AGREGADAS:", despues - antes)
            print("COMERCIALES_CSV:", comerciales)
            print("MONTO_CSV:", total_csv)
            print("MONTO_PERIODO_BD:", monto_periodo)

            aprobado = (
                despues - antes == len(df)
                and comerciales == comerciales_esperadas
                and total_csv == monto_esperado
                and monto_periodo == total_csv
            )

            print(
                "VALIDACION_TRANSACCIONAL=" +
                ("PASS" if aprobado else "FAIL")
            )

            if not aprobado:
                raise ValueError("No concilia la importacion")

        except BaseException:
            conn.rollback()
            print("TRANSACCION=ROLLBACK")
            print("CAMBIOS_PERMANENTES=NINGUNO")
            raise
        else:
            conn.rollback()
            print("TRANSACCION=ROLLBACK")
            print("CAMBIOS_PERMANENTES=NINGUNO")



if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Simulador mensual OCAM: siempre ROLLBACK"
    )
    parser.add_argument("--csv", required=True, type=Path)
    parser.add_argument("--periodo", required=True)
    args = parser.parse_args()

    if len(args.periodo) != 6 or not args.periodo.isdigit():
        parser.error("Periodo invalido: use AAAAMM")

    try:
        datetime.strptime(args.periodo, "%Y%m")
    except ValueError:
        parser.error("Mes invalido")

    if not args.csv.is_file():
        parser.error("CSV no encontrado")

    CSV = args.csv
    PERIODO = args.periodo

    ejecutar()
