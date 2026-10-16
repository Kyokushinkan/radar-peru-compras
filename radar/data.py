"""Capa analítica sin credenciales ni conexiones de red."""
from pathlib import Path
from decimal import Decimal
import hashlib, json, unicodedata
import pandas as pd
import streamlit as st
ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'data'
def normalize(value):
    return ''.join(c for c in unicodedata.normalize('NFKD',str(value or '').lower()) if not unicodedata.combining(c)).strip()
def current_snapshot():
    pointer=json.loads((DATA/'current.json').read_text(encoding='utf-8'))
    folder=(DATA/pointer['snapshot']).resolve()
    if folder.parent!=DATA.resolve(): raise ValueError('Snapshot inválido')
    manifest=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
    if not manifest.get('reconciled'): raise ValueError('Snapshot no conciliado')
    return folder,manifest
@st.cache_data(show_spinner=False,max_entries=12)
def load_table(folder_string,name,digest):
    folder=Path(folder_string); path=folder/(name+'.parquet')
    if hashlib.sha256(path.read_bytes()).hexdigest()!=digest: raise ValueError('El archivo no coincide con su manifiesto')
    return pd.read_parquet(path)
def table(folder,manifest,name):
    return load_table(str(folder),name,manifest['files'][name]['sha256'])
@st.cache_data(show_spinner=False,max_entries=2)
def load_orders(folder_string,signature):
    folder=Path(folder_string)
    manifest=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
    orders=table(folder,manifest,'ordenes')
    entities=table(folder,manifest,'entidades').rename(columns={'ruc':'ruc_entidad','nombre':'entidad'})
    suppliers=table(folder,manifest,'proveedores').rename(columns={'ruc':'ruc_proveedor','nombre':'proveedor'})
    orders=orders.merge(entities,on='ruc_entidad',validate='many_to_one').merge(suppliers,on='ruc_proveedor',validate='many_to_one')
    orders['fecha']=pd.to_datetime(orders.fecha)
    orders['mes']=orders.fecha.dt.to_period('M').astype(str)
    orders['busqueda_ocam']=orders.ocam.map(normalize)
    return orders

def filter_orders(df,commercial=True,start=None,end=None,entities=(),suppliers=(),markets=(),query='',minimum=None,maximum=None):
    mask=pd.Series(True,index=df.index)
    if commercial: mask &= df.estado_comercial
    if start is not None: mask &= df.fecha>=pd.Timestamp(start)
    if end is not None: mask &= df.fecha<=pd.Timestamp(end)
    for col,values in [('ruc_entidad',entities),('ruc_proveedor',suppliers),('mercado',markets)]:
        if values: mask &= df[col].isin(values)
    for word in normalize(query).split(): mask &= df.busqueda_ocam.str.contains(word,regex=False,na=False)
    if minimum is not None: mask &= df.total_centimos>=minimum
    if maximum is not None: mask &= df.total_centimos<=maximum
    return df.loc[mask]

def metrics(df):
    return {'orders':df.ocam.nunique(),'cents':int(df.total_centimos.sum()),'entities':df.ruc_entidad.nunique(),'suppliers':df.ruc_proveedor.nunique(),'markets':df.mercado.nunique()}

def grouped(df,column):
    return df.groupby(column,dropna=False).agg(ordenes=('ocam','nunique'),centimos=('total_centimos','sum'),entidades=('ruc_entidad','nunique'),proveedores=('ruc_proveedor','nunique'),mercados=('mercado','nunique')).reset_index().sort_values('centimos',ascending=False)

def monthly(df):
    if df.empty: return pd.DataFrame(columns=['mes','centimos','ordenes','Monto (S/)'])
    result=df.groupby('mes').agg(centimos=('total_centimos','sum'),ordenes=('ocam','nunique'))
    months=pd.period_range(df.fecha.min(),df.fecha.max(),freq='M').astype(str)
    result=result.reindex(months,fill_value=0).rename_axis('mes').reset_index()
    result['Monto (S/)']=result.centimos.astype(float)/100
    return result

def concentration(df,column):
    amounts=df.groupby(column).total_centimos.sum().sort_values(ascending=False)
    total=int(amounts.sum())
    return {'top1':float(amounts.head(1).sum())/total*100 if total else None,
            'top3':float(amounts.head(3).sum())/total*100 if total else None,
            'hhi':float(((amounts/total*100)**2).sum()) if total else None}

def comparable_growth(df,start,end):
    """Compara los dos últimos años con meses completos comunes en el filtro."""
    years=sorted(df.fecha.dt.year.unique())
    if len(years)<2: return pd.DataFrame(),''
    before,after=years[-2:]
    start,end=pd.Timestamp(start),pd.Timestamp(end)
    common=[]
    for m in range(1,13):
        dates=[pd.Timestamp(year=int(y),month=m,day=1) for y in (before,after)]
        if all(d>=start and d+pd.offsets.MonthEnd(0)<=end for d in dates): common.append(m)
    if not common: return pd.DataFrame(),''
    subset=df[df.fecha.dt.month.isin(common) & df.fecha.dt.year.isin([before,after])].copy()
    subset['anio']=subset.fecha.dt.year
    values=subset.pivot_table(index='mercado',columns='anio',values='total_centimos',aggfunc='sum',fill_value=0)
    for year in (before,after):
        if year not in values: values[year]=0
    values=values.rename(columns={before:'anterior_centimos',after:'reciente_centimos'}).reset_index()
    values['crecimiento_pct']=[(int(b)-int(a))/int(a)*100 if int(a)>0 else None for a,b in zip(values.anterior_centimos,values.reciente_centimos)]
    values['base']=values.anterior_centimos.map(lambda v:'Sin base comparable' if v==0 else 'Comparable')
    return values,f'{before} frente a {after}; meses completos comunes: '+', '.join(str(m).zfill(2) for m in common)

def money(cents):
    return 'S/ '+format(Decimal(int(cents))/100,',.2f')

def money_text(cents): return format(Decimal(int(cents))/100,'.2f')

def order_export(df):
    columns=['ocam','fecha','ruc_entidad','entidad','ruc_proveedor','proveedor','mercado','estado','estado_comercial']
    result=df[columns].copy()
    result['fecha']=result.fecha.dt.strftime('%Y-%m-%d')
    for source,label in [('subtotal_centimos','subtotal_soles'),('igv_centimos','igv_soles'),('total_centimos','total_soles')]:
        result[label]=df[source].map(lambda v:'' if pd.isna(v) else money_text(v))
    return result

def safe_csv(df):
    # Neutraliza fórmulas de hoja de cálculo sin cambiar cifras almacenadas.
    result=df.copy()
    for column in result.select_dtypes(include=['object','string']).columns:
        result[column]=result[column].map(lambda v:"'"+v if isinstance(v,str) and v.lstrip().startswith(('=','+','-','@','\t','\r')) else v)
    return result.to_csv(index=False,sep=';').encode('utf-8-sig')
