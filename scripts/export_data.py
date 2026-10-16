"""Exporta un snapshot inmutable y conciliado. No escribe en PostgreSQL."""
import hashlib, json, sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from audit_db import connection, encode
ROOT=Path(__file__).resolve().parents[1]
QUERIES={
"ordenes": """SELECT ocam,ruc_entidad,ruc_proveedor,fecha_formalizacion AS fecha,estado_original AS estado,
 estado_comercial,acuerdo_normalizado AS mercado,subtotal,igv,total FROM peru_compras.ordenes ORDER BY ocam""",
"entidades": "SELECT ruc,nombre_original AS nombre FROM peru_compras.entidades ORDER BY ruc",
"proveedores": "SELECT ruc,razon_social_original AS nombre FROM peru_compras.proveedores ORDER BY ruc",
"acuerdos": "SELECT acuerdo_normalizado AS mercado,string_agg(DISTINCT acuerdo_original,' | ' ORDER BY acuerdo_original) AS denominaciones_originales FROM peru_compras.acuerdos_marco GROUP BY acuerdo_normalizado ORDER BY 1",
"items": """WITH candidates AS (
 SELECT p.*,row_number() OVER(PARTITION BY p.ocam ORDER BY (v.procesamiento_id IS NOT NULL) DESC,p.finalizado_en DESC NULLS LAST,p.id DESC) AS rn
 FROM peru_compras.ocam_procesamientos p LEFT JOIN peru_compras.ocam_resultados_vigentes v ON v.procesamiento_id=p.id
 WHERE p.finalizado_en IS NOT NULL
 ), selected AS (
 SELECT p.id,p.ocam FROM candidates p JOIN peru_compras.ordenes o ON o.ocam=p.ocam
 JOIN (SELECT procesamiento_id,sum(total_item) AS suma,count(*) AS filas,count(total_item) AS completos FROM peru_compras.ocam_items GROUP BY procesamiento_id) s ON s.procesamiento_id=p.id
 WHERE rn=1 AND p.estado='procesado' AND p.reconciliado IS TRUE
 AND s.filas=s.completos AND abs(s.suma-o.total)<=0.02)
 SELECT i.id AS item_id,p.ocam,i.nro_item,i.codigo_item,i.descripcion,i.cantidad,i.unidad_medida,
 i.precio_unitario,i.total_item,i.marca,i.modelo,i.confianza_extraccion
 FROM selected p JOIN peru_compras.ocam_items i ON i.procesamiento_id=p.id ORDER BY p.ocam,i.nro_item,i.id""",
"limpieza": "SELECT ruc_entidad,entidad,tipo_demanda,monto_2025,monto_2026,ordenes_2026,meses_activos,proveedores_2026,top1_pct,crecimiento_pct,pct_monto_nuevos FROM peru_compras.vw_radar_limpieza_v1_2 ORDER BY ruc_entidad"
}
def cents(value):
    if value is None: return None
    scaled=Decimal(value)*100
    if scaled!=scaled.to_integral_value(): raise ValueError("Monto con precisión superior a centavos")
    return int(scaled)
def file_hash(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def require(value,message):
    if not value: raise ValueError(message)
def export():
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
    folder=ROOT/'data'/stamp
    folder.mkdir(parents=True,exist_ok=False)
    frames={}
    with connection() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        summary=conn.execute("SELECT * FROM peru_compras.vw_pc_resumen_general").fetchone()
        view_sums={}
        for name,amount in [('vw_pc_ranking_acuerdos','monto_comercial'),('vw_pc_ranking_mensual','monto'),('vw_pc_entidades_compras','monto_comercial'),('vw_pc_proveedores','monto_vendido')]:
            view_sums[name]=conn.execute(f'SELECT sum(ordenes) AS ordenes,sum({amount}) AS monto FROM peru_compras.{name}').fetchone()
        raw_coverage=conn.execute('SELECT count(*) AS items,count(DISTINCT p.ocam) AS ordenes FROM peru_compras.ocam_items i JOIN peru_compras.ocam_procesamientos p ON p.id=i.procesamiento_id').fetchone()
        for name,sql in QUERIES.items():
            records=conn.execute(sql).fetchall()
            df=pd.DataFrame(records)
            require(not df.empty,f'La exportación {name} está vacía: revisar antes de reemplazar el snapshot')
            money={'ordenes':['subtotal','igv','total'],'items':['total_item'],'limpieza':['monto_2025','monto_2026']}.get(name,[])
            for col in money:
                df[col+'_centimos']=pd.array([cents(v) for v in df.pop(col)],dtype='Int64')
            frames[name]=df
    orders=frames['ordenes']; commercial=orders[orders.estado_comercial]
    require(orders.ocam.is_unique,'OCAM duplicadas')
    require(not orders[['ocam','ruc_entidad','ruc_proveedor','fecha','mercado','total_centimos']].isna().any().any(),'Campos clave nulos')
    require((orders.total_centimos>=0).all(),'Montos negativos')
    for name,key in [('entidades','ruc'),('proveedores','ruc'),('acuerdos','mercado')]:
        require(frames[name][key].is_unique,f'Clave duplicada en {name}')
    for column,name,key in [('ruc_entidad','entidades','ruc'),('ruc_proveedor','proveedores','ruc'),('mercado','acuerdos','mercado')]:
        require(orders[column].isin(frames[name][key]).all(),f'Relación huérfana: {column}')
    actual={'ordenes_totales':len(orders),'ocam_distintas':orders.ocam.nunique(),
      'monto_bruto':str(Decimal(int(orders.total_centimos.sum()))/100),
      'ordenes_comerciales':len(commercial),'monto_comercial':str(Decimal(int(commercial.total_centimos.sum()))/100),
      'proveedores_distintos':orders.ruc_proveedor.nunique(),'entidades_distintas':orders.ruc_entidad.nunique(),
      'acuerdos_normalizados':orders.mercado.nunique(),'fecha_minima':str(orders.fecha.min()),'fecha_maxima':str(orders.fecha.max())}
    for key,value in actual.items():
        expected=summary[key]
        require(Decimal(value)==expected if isinstance(expected,Decimal) else str(value)==str(expected),f'No concilia PostgreSQL: {key}')
    for name,values in view_sums.items():
        require(int(values['ordenes'])==len(commercial) and cents(values['monto'])==int(commercial.total_centimos.sum()),f'No concilia {name}')
    # Vista de limpieza: contrastar cada entidad con órdenes; no exportar su puntuación predictiva.
    clean=frames['limpieza']; require(clean.ruc_entidad.is_unique,'Vista de limpieza duplica entidades')
    cleaning_market='materiales e insumos de limpieza, papeles para aseo y limpieza'
    for year in (2025,2026):
        subset=commercial[(commercial.mercado==cleaning_market) & (commercial.fecha>=datetime(year,1,1).date()) & (commercial.fecha<=datetime(year,7,31).date())]
        totals=subset.groupby('ruc_entidad').total_centimos.sum()
        require(all(int(r[f'monto_{year}_centimos'])==int(totals.get(r['ruc_entidad'],0)) for r in clean.to_dict('records')),f'Vista limpieza no concilia {year}')
    items=frames['items']
    require(items.item_id.is_unique and not items.duplicated(['ocam','nro_item']).any(),'Ítems duplicados')
    require(items.ocam.isin(orders.ocam).all(),'Ítems huérfanos')
    item_sums=items.groupby('ocam').total_item_centimos.sum()
    order_sums=orders.set_index('ocam').total_centimos.reindex(item_sums.index)
    require(((item_sums-order_sums).abs()<=2).all(),'Totales de ítems no conciliados')
    manifest={'snapshot':stamp,'extracted_at':datetime.now(timezone.utc).isoformat(),'source':'PostgreSQL peru_compras_radar / peru_compras',
      'publication_authorized':False,'summary':actual,'files':{},'reconciled':True,
      'items_coverage':{'raw_items':raw_coverage['items'],'raw_orders':raw_coverage['ordenes'],'validated_items':len(items),'validated_orders':items.ocam.nunique(),'total_orders':len(orders)},
      'definitions':{'commercial':'estado_comercial=true de la fuente; excluye RESUELTA y ORDEN DE COMPRA NULA',
        'money':'Enteros de céntimos para importes; precio unitario y cantidad conservan Decimal original',
        'items':'Procesamiento vigente aprobado si existe; en otro caso último finalizado. Sólo procesado, reconciliado y suma contra orden con diferencia <= S/ 0.02. No extrapolar.',
        'links':'No se muestran enlaces no verificados. Los enlaces de almacenamiento de la fuente se excluyeron del snapshot.',
        'cleaning':'Vista histórica enero-julio 2025 frente a enero-julio 2026; entidades con monto 2026 >= S/ 300000. Sin puntuación de oportunidad.'}}
    for name,df in frames.items():
        file=folder/(name+'.parquet')
        pq.write_table(pa.Table.from_pandas(df,preserve_index=False),file,compression='zstd')
        manifest['files'][name]={'file':file.name,'rows':len(df),'bytes':file.stat().st_size,'sha256':file_hash(file),'columns':list(df.columns)}
    (folder/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2,default=encode),encoding='utf-8')
    # Los controles históricos son alertas, no valores del dashboard ni bloqueos de actualizaciones legítimas.
    controls=json.loads((ROOT/'scripts/controles_historicos.json').read_text(encoding='utf-8'))
    historical={k:{'expected':v,'observed':actual[k],'match':Decimal(str(v))==Decimal(str(actual[k])) if k.startswith('monto') else str(v)==str(actual[k])} for k,v in controls.items()}
    report={'snapshot':stamp,'postgresql':summary,'export':actual,'historical_controls':historical,'view_sums':view_sums,'items_coverage':manifest['items_coverage'],'files':manifest['files'],'queries':QUERIES,'total_bytes':sum(x['bytes'] for x in manifest['files'].values())}
    (ROOT/'reports'/('exportacion_'+stamp+'.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2,default=encode),encoding='utf-8')
    pointer=ROOT/'data/current.json.tmp'; pointer.write_text(json.dumps({'snapshot':stamp}),encoding='utf-8'); pointer.replace(ROOT/'data/current.json')
    print(json.dumps({'snapshot':stamp,'summary':actual,'coverage':manifest['items_coverage'],'bytes':report['total_bytes'],'files':{k:v['bytes'] for k,v in manifest['files'].items()}},ensure_ascii=True,indent=2))
if __name__=='__main__':
    try: export()
    except Exception as exc:
        print(f'Exportación detenida ({type(exc).__name__}). El snapshot anterior sigue activo; revise la auditoría y la conexión local.',file=sys.stderr)
        raise
