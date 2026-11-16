"""
Recolector de datos de movilidad de Valencia.

Cada ejecución guarda una "foto" del momento de cada fuente en
data/raw/<fuente>/AAAA/MM/DD/HHMM.csv.gz (un archivo por ejecución, sin reescribir los anteriores).
Pensado para ejecutarse cada 10 minutos (por ejemplo con GitHub Actions) y acumular histórico.

Fuentes:
  valenbisi  Disponibilidad de Valenbisi (Ajuntament de València, CC BY 4.0). Se actualiza cada 10 min.
  trafico    Estado del tráfico por tramo (Ajuntament de València, CC BY 4.0). Se actualiza cada 3 min.
  meteo      Tiempo actual y previsión horaria en Valencia (Open-Meteo, sin clave).
  renfe      Avisos y retrasos en tiempo real de Cercanías, filtrados a la zona de Valencia (Renfe).
  gtfs       Horarios oficiales (EMT, Renfe y, con clave del NAP, Metrovalencia). Solo se guardan si cambian.

Metro y autobús EMT no publican tiempo real como datos abiertos: de ellos se guardan los horarios (GTFS).
No se usan APIs no oficiales de las apps.

Uso:
  python recolector.py                    # todas las fuentes
  python recolector.py --fuentes valenbisi meteo
Variables de entorno opcionales:
  NAP_API_KEY   clave gratuita del Punto de Acceso Nacional (nap.transportes.gob.es) para el GTFS de Metrovalencia
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import logging
import os
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

ZONA = ZoneInfo("Europe/Madrid")
CABECERAS = {"User-Agent": "recolector-movilidad-valencia (proyecto academico)"}

URL_VALENBISI = "https://geoportal.valencia.es/server/rest/services/OPENDATA/Trafico/MapServer/228/query?where=1=1&outFields=*&f=geojson"
URL_TRAFICO = "https://geoportal.valencia.es/server/rest/services/OPENDATA/Trafico/MapServer/192/query?where=1=1&outFields=*&f=geojson"
URL_METEO = (
    "https://api.open-meteo.com/v1/forecast?latitude=39.4699&longitude=-0.3763&timezone=Europe%2FMadrid&forecast_days=2"
    "&current=temperature_2m,relative_humidity_2m,precipitation,rain,weather_code,wind_speed_10m,wind_gusts_10m"
    "&hourly=temperature_2m,precipitation_probability,precipitation,weather_code,wind_speed_10m,wind_gusts_10m"
)
URL_RENFE_AVISOS = "https://gtfsrt.renfe.com/alerts.pb"
URL_RENFE_RETRASOS = "https://gtfsrt.renfe.com/trip_updates.pb"
GTFS = {
    "emt": ("https://opendata.vlci.valencia.es/dataset/ab058cf8-ad3e-4d9c-ac89-0c6367ecf351/resource/"
            "c81b69e6-c082-44dc-acc6-66fc417b4e66/download/google_transit.zip", None),
    "renfe": ("https://ssl.renfe.com/ftransit/Fichero_CER_FOMENTO/fomento_transit.zip", None),
    "metrovalencia": ("https://nap.transportes.gob.es/api/Fichero/download/1168", "NAP_API_KEY"),
}
# Zona del núcleo de Cercanías Valencia (de Castellón a Moixent y de Utiel a la costa).
LAT_MIN, LAT_MAX, LON_MIN, LON_MAX = 38.80, 40.05, -1.35, 0.10

log = logging.getLogger("recolector")


def descargar(url: str, cabeceras: dict | None = None, timeout: int = 60) -> bytes:
    respuesta = requests.get(url, headers={**CABECERAS, **(cabeceras or {})}, timeout=timeout)
    respuesta.raise_for_status()
    return respuesta.content


def guardar(tabla: pd.DataFrame, raiz: Path, fuente: str, ahora: datetime) -> Path:
    destino = raiz / fuente / ahora.strftime("%Y/%m/%d") / f"{ahora:%H%M}.csv.gz"
    destino.parent.mkdir(parents=True, exist_ok=True)
    tabla.to_csv(destino, index=False, compression="gzip")
    log.info("%s: %d filas -> %s", fuente, len(tabla), destino)
    return destino


def geojson_a_tabla(contenido: bytes, con_coordenadas: bool) -> pd.DataFrame:
    filas = []
    for entidad in json.loads(contenido).get("features", []):
        fila = dict(entidad.get("properties") or {})
        geometria = entidad.get("geometry") or {}
        if con_coordenadas and geometria.get("type") == "Point":
            fila["lon"], fila["lat"] = geometria["coordinates"][:2]
        filas.append(fila)
    return pd.DataFrame(filas)


# ---------- Fuentes ----------

def valenbisi(raiz: Path, ahora: datetime) -> None:
    tabla = geojson_a_tabla(descargar(URL_VALENBISI), con_coordenadas=True)
    if tabla.empty:
        raise ValueError("Valenbisi ha devuelto 0 estaciones")
    tabla.insert(0, "captura", ahora.isoformat())
    guardar(tabla, raiz, "valenbisi", ahora)


def trafico(raiz: Path, ahora: datetime) -> None:
    # Solo atributos (tramo y estado). La geometría de los tramos no cambia: se guarda aparte una vez.
    contenido = descargar(URL_TRAFICO)
    tabla = geojson_a_tabla(contenido, con_coordenadas=False)
    if tabla.empty:
        raise ValueError("Tráfico ha devuelto 0 tramos")
    tabla.insert(0, "captura", ahora.isoformat())
    guardar(tabla, raiz, "trafico", ahora)
    geometria = raiz / "trafico" / "tramos_geometria.geojson"
    if not geometria.exists():
        geometria.write_bytes(contenido)


def meteo(raiz: Path, ahora: datetime) -> None:
    datos = json.loads(descargar(URL_METEO))
    actual = pd.DataFrame([datos["current"]])
    actual.insert(0, "captura", ahora.isoformat())
    guardar(actual, raiz, "meteo_actual", ahora)
    # La previsión se guarda una vez por hora: permite medir después cómo de buena era.
    if ahora.minute < 10:
        prevision = pd.DataFrame(datos["hourly"])
        prevision.insert(0, "captura", ahora.isoformat())
        guardar(prevision, raiz, "meteo_prevision", ahora)


def _ultimo_gtfs(raiz: Path, operador: str) -> Path | None:
    archivos = sorted((raiz / "gtfs" / operador).glob("*.zip"))
    return archivos[-1] if archivos else None


def _leer_txt(zip_gtfs: zipfile.ZipFile, nombre: str) -> pd.DataFrame:
    datos = zip_gtfs.read(nombre)
    for codificacion in ("utf-8-sig", "latin-1"):
        try:
            tabla = pd.read_csv(io.BytesIO(datos), dtype=str, encoding=codificacion)
            tabla.columns = tabla.columns.str.strip()
            return tabla.apply(lambda c: c.str.strip())
        except UnicodeDecodeError:
            continue
    raise ValueError(f"No se pudo leer {nombre}")


def _filtro_renfe_valencia(zip_bytes: bytes) -> tuple[set, set, dict]:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        paradas = _leer_txt(z, "stops.txt")
        paradas[["stop_lat", "stop_lon"]] = paradas[["stop_lat", "stop_lon"]].astype(float)
        en_zona = paradas[paradas.stop_lat.between(LAT_MIN, LAT_MAX) & paradas.stop_lon.between(LON_MIN, LON_MAX)]
        horarios = _leer_txt(z, "stop_times.txt")[["trip_id", "stop_id"]]
        viajes_zona = set(horarios.loc[horarios.stop_id.isin(en_zona.stop_id), "trip_id"])
        viajes = _leer_txt(z, "trips.txt")[["trip_id", "route_id"]]
        linea_de_viaje = dict(viajes.loc[viajes.trip_id.isin(viajes_zona)].values)
    return set(en_zona.stop_id), set(linea_de_viaje.values()), linea_de_viaje


def renfe(raiz: Path, ahora: datetime) -> None:
    from google.transit import gtfs_realtime_pb2  # pip install gtfs-realtime-bindings

    zip_local = _ultimo_gtfs(raiz, "renfe")
    if zip_local:
        zip_bytes = zip_local.read_bytes()
    else:  # primera ejecución: se descarga el horario y se guarda para las siguientes
        zip_bytes = descargar(GTFS["renfe"][0], timeout=180)
        destino = raiz / "gtfs" / "renfe" / f"{ahora:%Y-%m-%d}.zip"
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(zip_bytes)
    paradas, lineas, linea_de_viaje = _filtro_renfe_valencia(zip_bytes)

    def leer(url: str):
        feed = gtfs_realtime_pb2.FeedMessage()
        feed.ParseFromString(descargar(url))
        return feed

    texto = lambda t: t.translation[0].text if t.translation else ""
    avisos = []
    for e in leer(URL_RENFE_AVISOS).entity:
        afectados = e.alert.informed_entity
        if any(a.route_id in lineas or a.stop_id in paradas or a.trip.trip_id in linea_de_viaje for a in afectados):
            avisos.append({
                "captura": ahora.isoformat(), "aviso_id": e.id,
                "titulo": texto(e.alert.header_text), "descripcion": texto(e.alert.description_text),
                "causa": e.alert.cause, "efecto": e.alert.effect,
                "lineas": "|".join(sorted({a.route_id for a in afectados if a.route_id})),
                "paradas": "|".join(sorted({a.stop_id for a in afectados if a.stop_id})),
            })
    guardar(pd.DataFrame(avisos, columns=["captura", "aviso_id", "titulo", "descripcion", "causa", "efecto", "lineas", "paradas"]),
            raiz, "renfe_avisos", ahora)

    retrasos = []
    for e in leer(URL_RENFE_RETRASOS).entity:
        tu = e.trip_update
        if tu.trip.trip_id not in linea_de_viaje:
            continue
        siguiente = tu.stop_time_update[0] if len(tu.stop_time_update) else None
        retraso = tu.delay or (siguiente and (siguiente.arrival.delay or siguiente.departure.delay)) or 0
        retrasos.append({
            "captura": ahora.isoformat(), "trip_id": tu.trip.trip_id, "route_id": linea_de_viaje[tu.trip.trip_id],
            "retraso_s": int(retraso), "proxima_parada": siguiente.stop_id if siguiente else "",
            "cancelado": int(tu.trip.schedule_relationship == 3),
        })
    guardar(pd.DataFrame(retrasos, columns=["captura", "trip_id", "route_id", "retraso_s", "proxima_parada", "cancelado"]),
            raiz, "renfe_retrasos", ahora)


def gtfs(raiz: Path, ahora: datetime) -> None:
    """Guarda los horarios de cada operador solo cuando cambian (se comparan por huella SHA-256)."""
    for operador, (url, variable_clave) in GTFS.items():
        cabeceras = {}
        if variable_clave:
            clave = os.environ.get(variable_clave)
            if not clave:
                log.info("gtfs %s: omitido (falta %s)", operador, variable_clave)
                continue
            cabeceras = {"ApiKey": clave}
        try:
            contenido = descargar(url, cabeceras, timeout=180)
            zipfile.ZipFile(io.BytesIO(contenido)).testzip()
        except Exception as error:  # noqa: BLE001 - se registra y se sigue con el resto
            log.warning("gtfs %s: error %s", operador, error)
            continue
        huella = hashlib.sha256(contenido).hexdigest()
        anterior = _ultimo_gtfs(raiz, operador)
        if anterior and hashlib.sha256(anterior.read_bytes()).hexdigest() == huella:
            log.info("gtfs %s: sin cambios", operador)
            continue
        destino = raiz / "gtfs" / operador / f"{ahora:%Y-%m-%d}.zip"
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(contenido)
        log.info("gtfs %s: nueva versión -> %s", operador, destino)


FUENTES = {"valenbisi": valenbisi, "trafico": trafico, "meteo": meteo, "gtfs": gtfs, "renfe": renfe}


def main() -> int:
    parser = argparse.ArgumentParser(description="Guarda una foto de las fuentes de movilidad de Valencia")
    parser.add_argument("--salida", default="data/raw", help="Carpeta raíz de los datos")
    parser.add_argument("--fuentes", nargs="+", choices=list(FUENTES), default=list(FUENTES))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    ahora = datetime.now(ZONA).replace(second=0, microsecond=0)
    raiz = Path(args.salida)
    fallos = []
    for nombre in args.fuentes:  # gtfs va antes que renfe: renfe usa el último horario guardado
        try:
            FUENTES[nombre](raiz, ahora)
        except Exception as error:  # noqa: BLE001 - un fallo en una fuente no detiene las demás
            log.error("%s: %s", nombre, error)
            fallos.append(nombre)
    if fallos:
        log.warning("Fuentes con error: %s", ", ".join(fallos))
    return 1 if len(fallos) == len(args.fuentes) else 0


if __name__ == "__main__":
    sys.exit(main())
