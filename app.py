"""Radar Perú Compras Web V1: aplicación pública sobre snapshot local conciliado."""
import math
import pandas as pd
import plotly.express as px
import streamlit as st
from radar.data import (current_snapshot,load_orders,table,filter_orders,metrics,grouped,monthly,
                        concentration,comparable_growth,money,money_text,normalize,order_export,safe_csv)

st.set_page_config(page_title="Radar Perú Compras",page_icon="🔎",layout="wide",initial_sidebar_state="expanded")
st.markdown("""<style>
.block-container{padding-top:2rem;max-width:1500px;padding-bottom:3rem}
h1{letter-spacing:-.04em!important} h2,h3{letter-spacing:-.02em!important}
[data-testid="stMetric"]{background:white;border:1px solid #DCE5ED;border-radius:12px;padding:16px 18px}
[data-testid="stMetricValue"]{font-variant-numeric:tabular-nums;font-size:clamp(1.1rem,2vw,1.85rem)}
[data-testid="stSidebar"]{border-right:1px solid #DCE5ED}
@media(max-width:700px){.block-container{padding:1rem}h1{font-size:1.9rem!important}[data-testid="stMetric"]{padding:12px}}
</style>""",unsafe_allow_html=True)
try:
    folder,manifest=current_snapshot()
    orders=load_orders(str(folder),manifest['snapshot'])
except (OSError,ValueError,KeyError) as exc:
    st.title("Radar Perú Compras")
    st.error("No hay un snapshot válido disponible. El administrador debe generar o restaurar una exportación conciliada.")
    st.stop()

PAGES=['Resumen general','Buscador OCAM','Entidades compradoras','Proveedores','Inteligencia de mercados','Productos y oportunidades']
with st.sidebar:
    st.title('Radar Perú Compras')
    page=st.radio('Explorar',PAGES,label_visibility='collapsed')
    st.divider()
    st.subheader('Universo de consulta')
    universe=st.selectbox('Estado de las órdenes',['Todas las órdenes','Solo comerciales'])
    dates=st.date_input('Fecha de formalización',value=(orders.fecha.min().date(),orders.fecha.max().date()),
                       min_value=orders.fecha.min().date(),max_value=orders.fecha.max().date(),format='DD/MM/YYYY')
    with st.expander('Entidad y proveedor', expanded=True):
        enames=orders[['ruc_entidad','entidad']].drop_duplicates().set_index('ruc_entidad').entidad.to_dict()
        pnames=orders[['ruc_proveedor','proveedor']].drop_duplicates().set_index('ruc_proveedor').proveedor.to_dict()
        entities=st.multiselect('Entidades',sorted(enames),format_func=lambda k:f'{enames[k]} - {k}',placeholder='Buscar entidad por nombre o RUC')
        suppliers=st.multiselect('Proveedores',sorted(pnames),format_func=lambda k:f'{pnames[k]} - {k}',placeholder='Buscar proveedor por nombre o RUC')

    available=filter_orders(
        orders,
        commercial=universe=='Solo comerciales',
        start=dates[0] if isinstance(dates,(tuple,list)) and len(dates)==2 else None,
        end=dates[1] if isinstance(dates,(tuple,list)) and len(dates)==2 else None,
        entities=entities,
        suppliers=suppliers
    )
    market_options=sorted(available.mercado.dropna().unique().tolist())

    previous=st.session_state.get('selected_markets',[])
    valid=[m for m in previous if m in market_options]
    if valid!=previous:
        st.session_state['selected_markets']=valid

    markets=st.multiselect(
        'Acuerdo marco',
        market_options,
        placeholder='Buscar mercados disponibles',
        key='selected_markets'
    )
    st.caption(f'{len(market_options)} mercados disponibles para los filtros seleccionados.')
    st.caption('Datos históricos. Los estados corresponden a la fuente; no describen obligaciones vigentes.')
    extracted=pd.Timestamp(manifest['extracted_at']).tz_convert('America/Lima')
    st.caption('Extracción: '+extracted.strftime('%d/%m/%Y %H:%M')+' · Lima')
if not isinstance(dates,(tuple,list)) or len(dates)!=2:
    st.info('Selecciona una fecha inicial y una fecha final.'); st.stop()
start,end=dates
filtered=filter_orders(orders,commercial=universe=='Solo comerciales',start=start,end=end,entities=entities,suppliers=suppliers,markets=markets)
st.title(page)
st.caption(f"{start:%d/%m/%Y} — {end:%d/%m/%Y} · {universe} · Catálogos Electrónicos de Acuerdos Marco")
if filtered.empty:
    st.info('No hay órdenes que coincidan con los filtros. Amplía el periodo o elimina alguna selección.'); st.stop()

PALETTE=['#087E8B','#E8A33E','#3768B0','#AD607C','#5B9063','#8366AB']
def chart(fig,key):
    fig.update_layout(font=dict(family='Arial',color='#12263A'),paper_bgcolor='rgba(0,0,0,0)',plot_bgcolor='rgba(0,0,0,0)',
                      margin=dict(l=10,r=15,t=15,b=15),legend=dict(orientation='h',y=-.22),separators='.,')
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(gridcolor='#E4EBF1',zeroline=False)
    st.plotly_chart(fig,width='stretch',key=key,config={'displayModeBar':False})
def kpis(df):
    stats=metrics(df)
    for cols,values in [(st.columns(3),[('Órdenes OCAM',f"{stats['orders']:,}"),('Monto registrado',money(stats['cents'])),('Entidades compradoras',f"{stats['entities']:,}")]),
                        (st.columns(3),[('Proveedores',f"{stats['suppliers']:,}"),('Mercados',str(stats['markets'])),('Meses con compras',str(df.mes.nunique()))])]:
        for col,(label,value) in zip(cols,values): col.metric(label,value)
def trend(df,key):
    series=monthly(df)
    fig=px.line(series,x='mes',y='Monto (S/)',markers=True,color_discrete_sequence=PALETTE)
    fig.update_traces(fill='tozeroy',line_width=3,hovertemplate='%{x}<br>S/ %{y:,.2f}<extra></extra>')
    fig.update_layout(height=300,xaxis_title=None,yaxis_title='Soles')
    chart(fig,key)
def bars(frame,label,key,limit=12):
    data=frame.head(limit).copy().iloc[::-1]
    data['Monto (S/)']=data.centimos.astype(float)/100
    data['Etiqueta']=data[label].map(lambda x:str(x) if len(str(x))<65 else str(x)[:62]+'…')
    fig=px.bar(data,x='Monto (S/)',y='Etiqueta',orientation='h',color_discrete_sequence=PALETTE,custom_data=[label,'ordenes'])
    fig.update_traces(hovertemplate='%{customdata[0]}<br>S/ %{x:,.2f}<br>%{customdata[1]} órdenes<extra></extra>')
    fig.update_layout(height=max(280,len(data)*34),xaxis_title='Soles',yaxis_title=None)
    chart(fig,key)
def paged(frame,key,labels=None):
    count=len(frame)
    if not count: st.info('Sin registros para esta selección.'); return frame
    per=50
    page_no=st.number_input('Página',min_value=1,max_value=max(1,math.ceil(count/per)),value=1,step=1,key=key+'_page')
    view=frame.iloc[(page_no-1)*per:page_no*per]
    st.caption(f'{count:,} registros · mostrando {(page_no-1)*per+1:,}–{min(page_no*per,count):,}')
    st.dataframe(view.rename(columns=labels or {}),hide_index=True,width='stretch')
    return view

def download(frame,key,filename):
    if st.button('Preparar CSV de resultados filtrados',key=key+'_prepare'):
        st.download_button('Descargar CSV',data=safe_csv(frame),file_name=filename,mime='text/csv',key=key+'_download',on_click='ignore')

def ranking_table(df,column,label):
    ranked=grouped(df,column)
    ranked['Monto (S/)']=ranked.centimos.map(money_text)
    return ranked.rename(columns={column:label,'ordenes':'Órdenes','entidades':'Entidades','proveedores':'Proveedores','mercados':'Mercados'}).drop(columns='centimos')

if page=='Resumen general':
    kpis(filtered)
    commercial=filtered[filtered.estado_comercial]
    st.caption(f"Dentro de esta selección: {len(commercial):,} órdenes comerciales por {money(int(commercial.total_centimos.sum()))}. Comerciales excluye órdenes resueltas y nulas.")
    st.subheader('Evolución mensual')
    trend(filtered,'overview_trend')
    st.subheader('Acuerdos marco con mayor monto')
    bars(grouped(filtered,'mercado'),'mercado','overview_markets')
    with st.expander('Cifras por mercado y trazabilidad'):
        st.dataframe(ranking_table(filtered,'mercado','Acuerdo marco'),hide_index=True,width='stretch')
        st.caption('Fuente: ordenes, entidades, proveedores y acuerdos_marco del snapshot conciliado. Una fila por OCAM; los ítems no multiplican los montos. Importes calculados en céntimos exactos.')
        st.caption('Cobertura completa del snapshot: '+manifest['summary']['fecha_minima']+' a '+manifest['summary']['fecha_maxima']+'.')

elif page=='Buscador OCAM':
    a,b,c=st.columns([2,1,1])
    query=a.text_input('Número OCAM',placeholder='Ejemplo: OCAM-2025-155')
    min_amount=b.number_input('Monto mínimo (S/)',min_value=0.0,value=0.0,step=100.0)
    max_amount=c.number_input('Monto máximo (S/)',min_value=0.0,value=float(filtered.total_centimos.max())/100,step=100.0)
    results=filter_orders(filtered,commercial=False,query=query,minimum=round(min_amount*100),maximum=round(max_amount*100))
    choices={'Fecha más reciente':('fecha',False),'Monto mayor':('total_centimos',False),'Monto menor':('total_centimos',True),'Número OCAM':('ocam',True)}
    sort=st.selectbox('Ordenar por',list(choices)); col,asc=choices[sort]
    results=results.sort_values([col,'ocam'] if col!='ocam' else ['ocam'],ascending=asc)
    st.caption(f'{len(results):,} órdenes · {money(int(results.total_centimos.sum()))}')
    visible=order_export(results)
    slice_=paged(visible,'ocam',{'ocam':'OCAM','fecha':'Fecha','entidad':'Entidad','proveedor':'Proveedor','mercado':'Acuerdo marco','total_soles':'Total (S/)'})
    download(visible,'ocam','ordenes_filtradas.csv')
    if not slice_.empty:
        selected=st.selectbox('Detalle de una orden de esta página',slice_.ocam.tolist(),index=None,placeholder='Selecciona una OCAM')
        if selected:
            record=results[results.ocam==selected].iloc[0]
            st.subheader(selected)
            left,right=st.columns(2)
            left.write('**Entidad:** '+record.entidad); left.write('**RUC:** '+record.ruc_entidad)
            right.write('**Proveedor:** '+record.proveedor); right.write('**RUC:** '+record.ruc_proveedor)
            st.write('**Acuerdo marco:** '+record.mercado)
            st.write(f"**Fecha:** {record.fecha:%d/%m/%Y} · **Estado registrado:** {record.estado} · **Total:** {money(record.total_centimos)}")
            st.caption('No hay un enlace oficial verificado disponible en este snapshot para mostrar.')
            items=table(folder,manifest,'items'); detail=items[items.ocam==selected].copy()
            if detail.empty: st.info('Esta OCAM no tiene ítems conciliados incluidos en la muestra.')
            else:
                detail['Total (S/)']=detail.total_item_centimos.map(money_text)
                st.dataframe(detail[['nro_item','descripcion','cantidad','unidad_medida','precio_unitario','Total (S/)']],hide_index=True,width='stretch')

elif page in ('Entidades compradoras','Proveedores'):
    is_entity=page=='Entidades compradoras'; key='ruc_entidad' if is_entity else 'ruc_proveedor'
    name='entidad' if is_entity else 'proveedor'; other='ruc_proveedor' if is_entity else 'ruc_entidad'
    other_name='proveedor' if is_entity else 'entidad'
    query=st.text_input('Buscar por nombre o RUC',placeholder='Escribe parte del nombre, con o sin tildes')
    directory=filtered[[key,name]].drop_duplicates().copy()
    search=(directory[key]+' '+directory[name]).map(normalize)
    for word in normalize(query).split(): directory=directory[search.loc[directory.index].str.contains(word,regex=False)]
    subset=filtered[filtered[key].isin(directory[key])]
    if subset.empty: st.info('No se encontraron coincidencias.'); st.stop()
    ranked=grouped(subset,key).merge(directory,on=key,validate='one_to_one')
    st.subheader('Ranking por monto')
    bars(ranked,name,'directory_ranking')
    display=ranked.copy(); display['Monto (S/)']=display.centimos.map(money_text)
    paged(display.drop(columns='centimos'),'directory',{key:'RUC',name:'Nombre','ordenes':'Órdenes','entidades':'Entidades','proveedores':'Proveedores','mercados':'Mercados'})
    download(display.drop(columns='centimos'),'directory','ranking_filtrado.csv')
    names=directory.set_index(key)[name].to_dict()
    options={names[k]+' · '+k:k for k in ranked[key].tolist()}
    selected_label=st.selectbox('Analizar en detalle',list(options),index=None,placeholder='Selecciona una entidad' if is_entity else 'Selecciona un proveedor')
    selection=options.get(selected_label)
    if selection:
        detail=filtered[filtered[key]==selection]
        st.subheader(names[selection]); kpis(detail)
        st.subheader('Evolución histórica'); trend(detail,'directory_trend')
        st.subheader('Principales proveedores' if is_entity else 'Principales entidades compradoras')
        partners=grouped(detail,other).merge(detail[[other,other_name]].drop_duplicates(),on=other,validate='one_to_one')
        bars(partners,other_name,'directory_partners')
        conc=concentration(detail,other)
        x,y=st.columns(2)
        x.metric('Participación del principal proveedor' if is_entity else 'Participación del principal comprador',f"{conc['top1']:.1f}%" if conc['top1'] is not None else 'Sin base')
        y.metric('Participación de los tres principales',f"{conc['top3']:.1f}%" if conc['top3'] is not None else 'Sin base')
        st.caption('Concentración = monto de los principales actores / monto total de esta selección. No mide exclusividad contractual.')
        st.subheader('Mercados en los que compra' if is_entity else 'Acuerdos marco en los que participa')
        st.dataframe(ranking_table(detail,'mercado','Acuerdo marco'),hide_index=True,width='stretch')

elif page=='Inteligencia de mercados':
    ranking=grouped(filtered,'mercado')
    st.subheader('Ranking de acuerdos marco'); bars(ranking,'mercado','markets_ranking',limit=22)
    st.dataframe(ranking_table(filtered,'mercado','Acuerdo marco'),hide_index=True,width='stretch')
    st.subheader('Comparar evolución mensual')
    comparison=st.multiselect('Mercados a comparar',ranking.mercado.tolist(),default=ranking.mercado.head(3).tolist())
    if comparison:
        values=pd.concat([monthly(filtered[filtered.mercado==m]).assign(Mercado=m) for m in comparison],ignore_index=True)
        fig=px.line(values,x='mes',y='Monto (S/)',color='Mercado',markers=True,color_discrete_sequence=PALETTE)
        fig.update_layout(height=400,xaxis_title=None,yaxis_title='Soles',showlegend=False)
        chart(fig,'markets_compare'); st.caption('El color identifica cada mercado; el nombre completo aparece al pasar el cursor. Todos usan la misma escala.')
    st.subheader('Crecimiento en periodos comparables')
    growth,description=comparable_growth(filtered,start,end)
    if growth.empty: st.info('El periodo seleccionado no contiene meses completos comunes de dos años para comparar.')
    else:
        st.caption(description+'. La variación no compara un año completo con un año parcial.')
        growth['Monto anterior (S/)']=growth.anterior_centimos.map(money_text)
        growth['Monto reciente (S/)']=growth.reciente_centimos.map(money_text)
        st.dataframe(growth[['mercado','Monto anterior (S/)','Monto reciente (S/)','crecimiento_pct','base']].rename(columns={'mercado':'Mercado','crecimiento_pct':'Variación (%)','base':'Base'}),hide_index=True,width='stretch')
    st.subheader('Concentración de proveedores')
    records=[]
    for market,group in filtered.groupby('mercado', observed=True):
        conc=concentration(group,'ruc_proveedor'); records.append({'Mercado':market,'Principal (%)':conc['top1'],'Tres principales (%)':conc['top3'],'Proveedores':group.ruc_proveedor.nunique()})
    st.dataframe(pd.DataFrame(records).sort_values('Principal (%)',ascending=False),hide_index=True,width='stretch')
    st.caption('Participaciones calculadas sobre el monto de cada mercado dentro del universo seleccionado.')

elif page=='Productos y oportunidades':
    items=table(folder,manifest,'items')
    selected_items=items[items.ocam.isin(filtered.ocam)].copy()
    coverage=selected_items.ocam.nunique()
    st.warning(f'Cobertura parcial: {coverage:,} de {len(filtered):,} órdenes de la selección tienen ítems conciliados ({coverage/len(filtered)*100:.2f}%). No extrapolar a todo el mercado.')
    st.caption('Precios y demanda históricos. No son cotizaciones vigentes ni oportunidades comerciales garantizadas.')
    a,b,c=st.columns(3); a.metric('Ítems conciliados',f'{len(selected_items):,}'); b.metric('Órdenes cubiertas',f'{coverage:,}'); c.metric('Órdenes sin detalle',f'{len(filtered)-coverage:,}')
    query=st.text_input('Buscar producto, código, marca o modelo')
    if not selected_items.empty:
        text=selected_items[['descripcion','codigo_item','marca','modelo']].fillna('').astype(str).agg(' '.join,axis=1).map(normalize)
        for word in normalize(query).split(): selected_items=selected_items[text.loc[selected_items.index].str.contains(word,regex=False)]
        units=st.multiselect('Unidad de medida',sorted(selected_items.unidad_medida.dropna().unique()))
        if units: selected_items=selected_items[selected_items.unidad_medida.isin(units)]
    if selected_items.empty: st.info('No hay ítems validados que coincidan con la selección.')
    else:
        st.subheader('Productos con mayor monto en la muestra')
        product_rank=selected_items.groupby('descripcion',dropna=False).agg(centimos=('total_item_centimos','sum'),ordenes=('ocam','nunique')).reset_index()
        bars(product_rank.sort_values('centimos',ascending=False),'descripcion','products_rank')
        display=selected_items.copy(); display['Total histórico (S/)']=display.total_item_centimos.map(money_text)
        display=display.drop(columns=['total_item_centimos','item_id','confianza_extraccion'])
        paged(display,'products',{'ocam':'OCAM','descripcion':'Descripción','cantidad':'Cantidad','unidad_medida':'Unidad','precio_unitario':'Precio unitario histórico (S/)'})
        download(display,'products','items_filtrados.csv')
        st.caption('Cantidades y precios conservan la unidad y precisión originales. Conciliación monetaria no implica verificación semántica de cada descripción; no se comparan precios entre productos o unidades distintas.')
    with st.expander('Referencia histórica: demanda de limpieza, enero–julio de 2025 y 2026'):
        st.caption('Referencia fija independiente de los filtros laterales: vista vw_radar_limpieza_v1_2, sólo entidades con compras comerciales de limpieza de al menos S/ 300.000 en enero–julio de 2026. Importes conciliados contra las órdenes; no es una predicción.')
        cleaning=table(folder,manifest,'limpieza').copy()
        for year in (2025,2026): cleaning[f'Monto {year} (S/)']=cleaning.pop(f'monto_{year}_centimos').map(money_text)
        st.dataframe(cleaning.rename(columns={'ruc_entidad':'RUC','entidad':'Entidad','crecimiento_pct':'Variación (%)','top1_pct':'Principal proveedor (%)','pct_monto_nuevos':'Monto con nuevos proveedores (%)','tipo_demanda':'Base de comparación','ordenes_2026':'Órdenes 2026','meses_activos':'Meses activos','proveedores_2026':'Proveedores 2026'}),hide_index=True,width='stretch')
        st.caption('Nuevos proveedores: sin ventas a esa entidad en el mismo periodo de 2025. Sin base previa, la variación porcentual queda vacía. No se publica el radar_score de la vista.')
