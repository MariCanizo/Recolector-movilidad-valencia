# Recolector de datos de movilidad de Valencia

Guarda cada 10 minutos una "foto" de las fuentes abiertas de movilidad de Valencia para construir el histórico que necesita el proyecto (las fuentes en tiempo real no guardan su pasado).

## Qué se recoge

| Carpeta en `data/raw/` | Qué contiene | Frecuencia | Fuente y licencia |
|---|---|---|---|
| `valenbisi/` | Bicis y anclajes libres por estación, con coordenadas | Cada ejecución | Ajuntament de València · CC BY 4.0 |
| `trafico/` | Estado de cada tramo (0 fluido, 1 denso, 2 congestionado, 3 cortado…) | Cada ejecución | Ajuntament de València · CC BY 4.0 |
| `meteo_actual/` | Temperatura, lluvia, viento y rachas en Valencia | Cada ejecución | Open-Meteo |
| `meteo_prevision/` | Previsión horaria de las próximas 48 h | Una vez por hora | Open-Meteo |
| `renfe_avisos/` | Avisos de Cercanías que afectan a la zona de Valencia | Cada ejecución | Renfe (GTFS-Realtime) |
| `renfe_retrasos/` | Retraso y cancelación de cada tren de Cercanías Valencia | Cada ejecución | Renfe (GTFS-Realtime) |
| `gtfs/emt/`, `gtfs/renfe/`, `gtfs/metrovalencia/` | Horarios oficiales completos (zip GTFS) | Solo cuando cambian | EMT (CC BY 4.0), Renfe, FGV vía NAP |

Cada ejecución crea archivos nuevos (`AAAA/MM/DD/HHMM.csv.gz`) y no reescribe los anteriores. La columna `captura` indica el momento de la foto, en hora de Madrid.

**Metro y autobús EMT:** no publican tiempo real como datos abiertos, así que de ellos se guardan los horarios. No se usan APIs no oficiales de sus apps.

**Pendiente de confirmar:** las condiciones de reutilización de Renfe, Open-Meteo y el NAP, y la dirección de retrasos de Renfe (`trip_updates.pb`). Documentadlo en la actividad 1.

## Ponerlo en marcha con GitHub Actions (sin tener el ordenador encendido)

1. Crea un repositorio **aparte** para el recolector, por ejemplo `recolector-movilidad-valencia`, y sube el contenido de esta carpeta (incluida la carpeta oculta `.github`).
   - Recomendación: hazlo **público**. Los datos son abiertos y no hay secretos en el código, y en repositorios públicos GitHub Actions es gratis sin límite. En uno privado, ejecutar cada 10 minutos supera los minutos gratuitos al mes.
   - El repositorio del proyecto sigue siendo privado y enlaza a este desde `data/README.md`.
2. En el repositorio, ve a **Settings > Actions > General > Workflow permissions** y marca **Read and write permissions**. Así el robot puede guardar los datos. El workflow también declara `permissions: contents: write`.
3. Si el repositorio es un **fork**, GitHub deja los workflows apagados. Entra en la pestaña **Actions** y pulsa **I understand my workflows, go ahead and enable them**. Sin ese clic el cron no arranca. El horario solo se lee de la rama por defecto (`main`): un cambio en otra rama no se ejecuta solo.
4. Ve a la pestaña **Actions**, elige "Recoger datos de movilidad" y pulsa **Run workflow** con la rama **main**. Los archivos nuevos aparecen en `main`, en `data/raw/<fuente>/AAAA/MM/DD/`. Si el desplegable de rama es otra, el workflow igualmente escribe en `main`.
5. El cron es `7,17,27,37,47,57 * * * *` (UTC): cada 10 minutos, fuera del minuto 0 y del minuto 30, que es cuando GitHub más retrasa o descarta trabajos. Un disparo descartado no aparece en Actions. La prueba de que va solo es una ejecución con el evento **Scheduled**, sin haber pulsado Run workflow.
6. Opcional, para el horario de Metrovalencia: regístrate gratis en nap.transportes.gob.es, copia tu clave y guárdala en **Settings > Secrets and variables > Actions > New repository secret** con el nombre `NAP_API_KEY`. Nunca la escribas en el código.

Si GitHub sigue saltándose el horario, un planificador externo puede llamar a `workflow_dispatch` cada 10 minutos. Hace falta un token fino con permiso **Actions: Read and write** solo en este repositorio, guardado en el planificador y nunca en el código. Sustituye `OWNER` por el dueño del repositorio:

```bash
curl -sS -X POST \
  -H "Authorization: Bearer $GH_TOKEN" \
  -H "Accept: application/vnd.github+json" \
  https://api.github.com/repos/OWNER/Recolector-movilidad-valencia/actions/workflows/recoger.yml/dispatches \
  -d '{"ref":"main"}'
```

Revisad una vez por semana la pestaña Actions: si una fuente cambia de formato o de dirección, la ejecución lo indica en el registro y el resto de fuentes siguen guardándose. En un repositorio público, GitHub apaga los workflows programados tras 60 días sin actividad; los commits del propio recolector cuentan como actividad.

## Ejecutarlo en tu ordenador

```bash
pip install -r requirements.txt
python recolector.py                          # todas las fuentes
python recolector.py --fuentes valenbisi meteo  # solo algunas
```

## Leer los datos acumulados

```python
import glob
import pandas as pd

archivos = sorted(glob.glob("data/raw/valenbisi/**/*.csv.gz", recursive=True))
valenbisi = pd.concat(map(pd.read_csv, archivos), ignore_index=True)
```

## Atribución

Datos de Valenbisi y del estado del tráfico: Ajuntament de València, portal de datos abiertos (opendata.vlci.valencia.es), licencia CC BY 4.0. Horarios de EMT València: Ajuntament de València, CC BY 4.0. Tiempo: Open-Meteo.com. Cercanías: Renfe. Metrovalencia: FGV a través del Punto de Acceso Nacional de Transporte Multimodal.
