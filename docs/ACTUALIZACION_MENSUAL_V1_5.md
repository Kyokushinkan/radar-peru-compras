# Radar Peru Compras V1.5 - Actualizacion mensual

## Estado

Procedimiento preparado para futuros archivos oficiales de Peru Compras.

Base publica de referencia V1.4:
- 154883 OCAM.
- Ultimo periodo incorporado: septiembre de 2026.
- PostgreSQL: peru_compras_radar.
- Esquema: peru_compras.

IMPORTANTE: El importador definitivo permanece bloqueado.
No ejecutar una importacion real hasta completar las pruebas.

## 1. Preparar el archivo

Guardar el CSV oficial en Downloads.

Ejemplo para octubre de 2026:

ReportePCBienes202610.csv

## 2. Validar

```powershell
$periodo = "202610"
$csv = Join-Path $env:USERPROFILE "Downloads\ReportePCBienes$periodo.csv"

.\.venv\Scripts\python.exe .\scripts\import_mensual.py validar --csv $csv --periodo $periodo
```

La validacion debe finalizar sin excepciones y mostrar:
VALIDACION_ESTRUCTURAL=PASS
IMPORTACION_BLOQUEADA=NO

Si existen duplicados, detener el procedimiento.

## 3. Simular

```powershell
.\.venv\Scripts\python.exe .\scripts\simular_mensual.py --csv $csv --periodo $periodo
```

Comprobar:
VALIDACION_TRANSACCIONAL=PASS
TRANSACCION=ROLLBACK
CAMBIOS_PERMANENTES=NINGUNO

No continuar si alguna comprobacion falla.

## 4. Importar con autorizacion

PENDIENTE DE HABILITACION Y PRUEBAS.

Interfaz prevista:

```powershell
.\.venv\Scripts\python.exe .\scripts\import_mensual_definitivo.py --csv $csv --periodo $periodo --autorizar "CONFIRMAR-$periodo"
```

Actualmente esta orden debe permanecer bloqueada.
No retirar el bloqueo sin completar las pruebas transaccionales.

## 5. Exportar snapshot

Solo despues de confirmar una importacion real y auditar PostgreSQL.

```powershell
.\.venv\Scripts\python.exe .\scripts\export_data.py
```

ADVERTENCIA: este comando crea un snapshot y actualiza
data/current.json. No ejecutarlo durante pruebas.

Verificar:
- data/current.json
- manifest.json del nuevo snapshot
- archivos Parquet
- reporte de exportacion

## 6. Ejecutar pruebas

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

No publicar si existen pruebas fallidas.

## 7. Publicar

Revisar primero los archivos modificados:

```powershell
git status --short
```

Agregar exclusivamente los scripts aprobados y los archivos
del nuevo snapshot. No utilizar git add .

Revisar el commit antes de publicarlo.

```powershell
git diff --cached --stat
git diff --cached --check
```

Tras la aprobacion del commit:

```powershell
git push origin main
```

Verificar el dashboard:
https://radar-peru-compras.streamlit.app/

## 8. Controles obligatorios

- No importar un periodo ya registrado.
- No importar OCAM duplicadas.
- Conciliar subtotal, IGV y total.
- Verificar entidades, proveedores y mercados.
- Simular con ROLLBACK antes de importar.
- Confirmar integridad de PostgreSQL despues del COMMIT.
- Exportar solamente despues de una importacion validada.
- Publicar solo archivos revisados.
- Conservar los respaldos y controles historicos.

## Pendientes para cerrar V1.5

1. Revisar y probar la autorizacion definitiva de COMMIT.
2. Probar una simulacion completa con un periodo nuevo.
3. Probar el flujo de importacion y exportacion.
4. Verificar la publicacion del nuevo snapshot.

Hasta entonces, V1.4 permanece como version publica activa.
