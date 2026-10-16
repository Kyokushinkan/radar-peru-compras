import json, math, unittest
from decimal import Decimal
from pathlib import Path
import pandas as pd
from streamlit.testing.v1 import AppTest
from radar.data import *
ROOT=Path(__file__).resolve().parents[1]
class DataTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.folder,cls.manifest=current_snapshot()
  cls.orders=load_orders(str(cls.folder),cls.manifest['snapshot'])
 def test_reconciliation_against_audited_postgresql(self):
  summary=json.loads((ROOT/'reports/auditoria_postgresql.json').read_text(encoding='utf-8'))['summary']
  df=self.orders
  self.assertEqual(len(df),summary['ordenes_totales'])
  self.assertEqual(df.ocam.nunique(),summary['ocam_distintas'])
  self.assertEqual(Decimal(int(df.total_centimos.sum()))/100,Decimal(summary['monto_bruto']))
  commercial=df[df.estado_comercial]
  self.assertEqual(len(commercial),summary['ordenes_comerciales'])
  self.assertEqual(Decimal(int(commercial.total_centimos.sum()))/100,Decimal(summary['monto_comercial']))
  self.assertEqual(df.ruc_entidad.nunique(),summary['entidades_distintas'])
  self.assertEqual(df.ruc_proveedor.nunique(),summary['proveedores_distintos'])
  self.assertEqual(df.mercado.nunique(),summary['acuerdos_normalizados'])
 def test_dimensions_and_no_join_multiplication(self):
  raw=table(self.folder,self.manifest,'ordenes')
  self.assertEqual(len(raw),len(self.orders))
  self.assertTrue(self.orders[['entidad','proveedor']].notna().all().all())
  for name,key in [('entidades','ruc'),('proveedores','ruc'),('acuerdos','mercado')]:
   self.assertTrue(table(self.folder,self.manifest,name)[key].is_unique)
 def test_item_coverage_and_reconciliation(self):
  items=table(self.folder,self.manifest,'items')
  self.assertFalse(items.duplicated(['ocam','nro_item']).any())
  totals=items.groupby('ocam').total_item_centimos.sum()
  order_totals=self.orders.set_index('ocam').total_centimos.reindex(totals.index)
  self.assertTrue(((totals-order_totals).abs()<=2).all())
  self.assertEqual(items.ocam.nunique(),self.manifest['items_coverage']['validated_orders'])
  self.assertLess(items.ocam.nunique(),len(self.orders))
 def test_filter_intersection_and_reset(self):
  row=self.orders.iloc[0]
  selected=filter_orders(self.orders,commercial=False,start=row.fecha,end=row.fecha,entities=[row.ruc_entidad],suppliers=[row.ruc_proveedor],markets=[row.mercado],query=row.ocam,minimum=int(row.total_centimos),maximum=int(row.total_centimos))
  self.assertEqual(selected.ocam.tolist(),[row.ocam])
  self.assertEqual(len(filter_orders(self.orders,commercial=False)),len(self.orders))
  self.assertTrue(filter_orders(self.orders,commercial=False,query='not-found-987654').empty)
 def test_literal_accent_search(self):
  self.assertEqual(normalize('  MUNICIPALIDAD Áncash  '),'municipalidad ancash')
  self.assertTrue(filter_orders(self.orders,commercial=False,query='.*').empty)
 def test_aggregate_conservation(self):
  for key in ['mercado','ruc_entidad','ruc_proveedor']:
   result=grouped(self.orders,key)
   self.assertEqual(int(result.centimos.sum()),int(self.orders.total_centimos.sum()))
   self.assertEqual(int(result.ordenes.sum()),len(self.orders))
  self.assertEqual(int(monthly(self.orders).centimos.sum()),int(self.orders.total_centimos.sum()))
 def test_exact_export_and_formula_safety(self):
  df=order_export(self.orders.head(3))
  self.assertEqual(Decimal(df.total_soles.iloc[0])*100,int(self.orders.total_centimos.iloc[0]))
  text=safe_csv(pd.DataFrame({'name':['=cmd()','+SUM(A1)','@test','normal'],'total':['123.45']*4})).decode('utf-8-sig')
  self.assertIn("'=cmd()",text); self.assertIn('123.45',text)
 def test_comparable_periods_and_no_base(self):
  df=pd.DataFrame({'fecha':pd.to_datetime(['2025-02-01','2025-12-01','2026-02-01','2026-03-01']),
                   'mercado':['A','A','A','B'],'total_centimos':[100,10000,200,100]})
  out,label=comparable_growth(df,'2025-01-01','2026-07-31')
  a=out[out.mercado=='A'].iloc[0]; b=out[out.mercado=='B'].iloc[0]
  self.assertEqual(a.crecimiento_pct,100); self.assertTrue(pd.isna(b.crecimiento_pct))
  self.assertNotIn('12',label)
  self.assertTrue(comparable_growth(df,'2026-01-01','2026-07-31')[0].empty)
 def test_concentration_denominator(self):
  df=pd.DataFrame({'ruc':['a','b','c','d'],'total_centimos':[50,25,15,10]})
  result=concentration(df,'ruc')
  self.assertEqual(result['top1'],50); self.assertEqual(result['top3'],90)
 def test_public_allowlist(self):
  forbidden={'fuente_json','ruta_local','ruta_fuente','lock_token','worker_id','evidencia_texto','password','aprobado_por'}
  for name in self.manifest['files']:
   self.assertFalse(forbidden.intersection(table(self.folder,self.manifest,name).columns))
 def test_all_file_hashes(self):
  for name in self.manifest['files']:
   self.assertGreater(len(table(self.folder,self.manifest,name)),0)

class AppTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.app=AppTest.from_file(str(ROOT/'app.py'),default_timeout=90)
 def assert_ok(self):
  self.assertEqual(len(self.app.exception),0,[e.message for e in self.app.exception])
 def test_six_pages_and_details(self):
  app=self.app.run(); self.assert_ok()
  self.assertEqual(app.metric[0].value,'142,587')
  for page in ['Buscador OCAM','Entidades compradoras','Proveedores','Inteligencia de mercados','Productos y oportunidades']:
   app.sidebar.radio[0].set_value(page).run(); self.assert_ok()
   if page=='Buscador OCAM':
    detail=next(x for x in app.selectbox if x.label=='Detalle de una orden de esta página')
    detail.select_index(0).run(); self.assert_ok()
    next(x for x in app.text_input if x.label=='Número OCAM').set_value('ZZZ-NO-EXISTE').run(); self.assert_ok()
   if page in ['Entidades compradoras','Proveedores']:
    detail=next(x for x in app.selectbox if x.label=='Analizar en detalle')
    detail.select_index(0).run(); self.assert_ok()
  app.sidebar.radio[0].set_value('Resumen general').run()
  app.sidebar.selectbox[0].set_value('Solo comerciales').run(); self.assert_ok()
  self.assertEqual(app.metric[0].value,'142,537')
  app.sidebar.selectbox[0].set_value('Todas las órdenes').run(); self.assert_ok()
  self.assertEqual(app.metric[0].value,'142,587')
  app.sidebar.multiselect[0].set_value([app.sidebar.multiselect[0].options[0]]).run(); self.assert_ok()
  self.assertEqual(app.metric[4].value,'1')
  app.sidebar.multiselect[0].set_value([]).run(); self.assert_ok()
  self.assertEqual(app.metric[4].value,'22')
if __name__=='__main__': unittest.main()

