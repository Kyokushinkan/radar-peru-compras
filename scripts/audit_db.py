"""Auditoría local PostgreSQL: transacción READ ONLY, sin cambios de esquema."""
import json, os
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
import psycopg
from psycopg.rows import dict_row
ROOT=Path(__file__).resolve().parents[1]
def connection():
    return psycopg.connect(host=os.getenv("PGHOST","127.0.0.1"), port=os.getenv("PGPORT","5432"),
        dbname=os.getenv("PGDATABASE","peru_compras_radar"), user=os.getenv("PGUSER","postgres"),
        options="-c default_transaction_read_only=on -c statement_timeout=120000", row_factory=dict_row)
def encode(value):
    if isinstance(value,(Decimal,date,datetime)): return str(value)
    raise TypeError(type(value).__name__)
def audit():
    out={"extracted_at":datetime.now().astimezone().isoformat(),"mode":"READ ONLY"}
    with connection() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        def query(sql): return conn.execute(sql).fetchall()
        out["columns"]=query("SELECT table_name,column_name,data_type,numeric_precision,numeric_scale FROM information_schema.columns WHERE table_schema='peru_compras' ORDER BY table_name,ordinal_position")
        out["constraints"]=query("SELECT c.relname AS table_name, con.conname, con.contype, pg_get_constraintdef(con.oid) AS definition FROM pg_constraint con JOIN pg_class c ON c.oid=con.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='peru_compras'")
        out["views"]={}
        views=query("SELECT viewname,definition FROM pg_views WHERE schemaname='peru_compras'")
        for view in views:
            name=view["viewname"]
            out["views"][name]={"definition":view["definition"],"count":query('SELECT count(*) AS n FROM peru_compras.'+name)[0]["n"],"sample":query('SELECT * FROM peru_compras.'+name+' LIMIT 3')}
        out["summary"]=query("SELECT * FROM peru_compras.vw_pc_resumen_general")[0]
        out["quality"]=query("SELECT * FROM peru_compras.vw_pc_calidad_datos")[0]
        out["tables"]={}
        for name in ["ordenes","entidades","proveedores","acuerdos_marco","ocam_items","ocam_documentos","ocam_procesamientos","ocam_resultados_vigentes"]:
            out["tables"][name]=query("SELECT count(*) AS rows, pg_total_relation_size('peru_compras."+name+"') AS bytes FROM peru_compras."+name)[0]
        out["order_states"]=query("SELECT estado_original,estado_comercial,count(*) AS n,sum(total) AS amount FROM peru_compras.ordenes GROUP BY 1,2 ORDER BY n DESC")
        out["item_coverage"]=query("SELECT p.estado,p.reconciliado,count(DISTINCT p.id) AS processings,count(i.id) AS items,count(DISTINCT p.ocam) AS orders FROM peru_compras.ocam_procesamientos p LEFT JOIN peru_compras.ocam_items i ON i.procesamiento_id=p.id GROUP BY 1,2")
        out["active_coverage"]=query("SELECT p.estado,p.reconciliado,count(DISTINCT v.ocam) AS orders,count(i.id) AS items FROM peru_compras.ocam_resultados_vigentes v JOIN peru_compras.ocam_procesamientos p ON p.id=v.procesamiento_id LEFT JOIN peru_compras.ocam_items i ON i.procesamiento_id=p.id GROUP BY 1,2")
        out["url_sample"]=query("SELECT orden_digitalizada FROM peru_compras.ordenes WHERE orden_digitalizada IS NOT NULL LIMIT 3")
        out["dimensions"]=query("SELECT 'entidades' AS tabla,count(*) AS filas,count(DISTINCT ruc) AS claves FROM peru_compras.entidades UNION ALL SELECT 'proveedores',count(*),count(DISTINCT ruc) FROM peru_compras.proveedores UNION ALL SELECT 'acuerdos_marco',count(*),count(DISTINCT acuerdo_normalizado) FROM peru_compras.acuerdos_marco")
        out["orphans"]=query("SELECT count(*) FILTER(WHERE e.ruc IS NULL) AS entidad,count(*) FILTER(WHERE p.ruc IS NULL) AS proveedor,count(*) FILTER(WHERE a.acuerdo_normalizado IS NULL) AS acuerdo FROM peru_compras.ordenes o LEFT JOIN peru_compras.entidades e ON e.ruc=o.ruc_entidad LEFT JOIN peru_compras.proveedores p ON p.ruc=o.ruc_proveedor LEFT JOIN (SELECT DISTINCT acuerdo_normalizado FROM peru_compras.acuerdos_marco) a ON a.acuerdo_normalizado=o.acuerdo_normalizado")[0]
        out["view_sums"]={}
        for name,amount in [("vw_pc_ranking_acuerdos","monto_comercial"),("vw_pc_ranking_mensual","monto"),("vw_pc_entidades_compras","monto_comercial"),("vw_pc_proveedores","monto_vendido")]:
            out["view_sums"][name]=query(f"SELECT sum(ordenes) AS ordenes,sum({amount}) AS monto FROM peru_compras.{name}")[0]
    (ROOT/'reports/auditoria_postgresql.json').write_text(json.dumps(out,default=encode,ensure_ascii=False,indent=2),encoding='utf-8')
    for key in ["summary","quality","tables","item_coverage","active_coverage","dimensions","orphans","view_sums","url_sample"]:
        print(key,json.dumps(out[key],default=encode,ensure_ascii=True))
if __name__=='__main__': audit()
