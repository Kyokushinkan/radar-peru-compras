"""Actualizador mensual OCAM. Etapa 1: validacion de solo lectura."""
import argparse
import sys
import unicodedata
from collections import Counter
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from audit_db import connection


def clave(valor):
    texto = unicodedata.normalize("NFKD", str(valor))
    return "".join(
        c for c in texto if not unicodedata.combining(c)
    ).strip().lower()


def validar(csv, periodo):
    inicio = datetime.strptime(periodo, "%Y%m")
    df = pd.read_csv(
        csv, sep=";", encoding="cp1252",
        dtype=str, keep_default_na=False
    )
    df.columns = [clave(c).upper() for c in df.columns]

    requeridas = {
        "ORDEN_ELECTRONICA", "FECHA_FORMALIZACION",
        "ESTADO_ORDEN_ELECTRONICA", "SUB_TOTAL",
        "IGV", "TOTAL", "ACUERDO_MARCO",
        "RUC_ENTIDAD", "RUC_PROVEEDOR",
    }
    faltantes = requeridas - set(df.columns)
    if faltantes:
        raise ValueError(f"Columnas faltantes: {sorted(faltantes)}")

    ocam = df["ORDEN_ELECTRONICA"].str.strip()
    fechas = pd.to_datetime(
        df["FECHA_FORMALIZACION"],
        dayfirst=True, errors="coerce", format="mixed"
    )

    if df.empty or ocam.eq("").any() or ocam.nunique() != len(df):
        raise ValueError("OCAM vacias o duplicadas")

    if fechas.isna().any():
        raise ValueError("Fechas invalidas")

    if not fechas.dt.strftime("%Y%m").eq(periodo).all():
        raise ValueError("Fechas fuera del periodo")

    total_csv = Decimal("0")
    for i, fila in df.iterrows():
        subtotal = Decimal(fila["SUB_TOTAL"].strip())
        igv = Decimal(fila["IGV"].strip())
        total = Decimal(fila["TOTAL"].strip())
        if subtotal + igv != total:
            raise ValueError(f"Importe no concilia en fila {i+1}")
        total_csv += total

    with connection() as conn:
        registros = conn.execute("""
            SELECT acuerdo_original, acuerdo_normalizado
            FROM peru_compras.acuerdos_marco
        """).fetchall()

        mapa = {}
        for r in registros:
            mapa.setdefault(clave(r["acuerdo_original"]), set()).add(
                r["acuerdo_normalizado"]
            )

        for acuerdo in df["ACUERDO_MARCO"].unique():
            if len(mapa.get(clave(acuerdo), set())) != 1:
                raise ValueError(f"Acuerdo sin correspondencia unica: {acuerdo}")

        existentes = {
            str(r["ocam"]).strip()
            for r in conn.execute(
                "SELECT ocam FROM peru_compras.ordenes"
            ).fetchall()
        }

        historico = len(existentes)

    repetidas = set(ocam) & existentes
    estados = Counter(df["ESTADO_ORDEN_ELECTRONICA"].str.strip())
    comerciales = estados["ACEPTADA"] + estados["PAGADA"]

    print("=== VALIDACION MENSUAL RADAR V1.5 ===")
    print("PERIODO:", periodo)
    print("REGISTROS_CSV:", len(df))
    print("OCAM_UNICAS:", ocam.nunique())
    print("OCAM_EXISTENTES:", len(repetidas))
    print("OCAM_NUEVAS:", len(df) - len(repetidas))
    print("COMERCIALES_CSV:", comerciales)
    print("MONTO_CSV:", total_csv)
    print("ACUERDOS_CSV:", df["ACUERDO_MARCO"].nunique())
    print("HISTORICO_BD:", historico)
    print("VALIDACION_ESTRUCTURAL=PASS")

    if repetidas:
        print("ESTADO_IMPORTACION=YA_EXISTEN_ORDENES")
        print("IMPORTACION_BLOQUEADA=SI")
        raise ValueError(
            "IMPORTACION_BLOQUEADA: existen "
            + str(len(repetidas)) + " OCAM registradas"
        )
    else:
        print("ESTADO_IMPORTACION=LISTO_PARA_SIMULACION")
        print("IMPORTACION_BLOQUEADA=NO")

    print("POSTGRESQL_MODIFICADO=NO")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("accion", choices=["validar"])
    parser.add_argument("--csv", required=True, type=Path)
    parser.add_argument("--periodo", required=True)
    args = parser.parse_args()

    if len(args.periodo) != 6 or not args.periodo.isdigit():
        parser.error("El periodo debe tener formato AAAAMM")

    if not args.csv.is_file():
        parser.error("Archivo CSV no encontrado")

    validar(args.csv, args.periodo)


if __name__ == "__main__":
    main()
