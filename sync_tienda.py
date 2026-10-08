#!/usr/bin/env python3
"""
sync_tienda.py
--------------
Descarga el catálogo completo de Syscom (developers.syscom.mx) y genera los
archivos de datos que lee la tienda de la página (carpeta _site/data).

- NO publica precios: solo productos, fotos, características y existencias.
- Las credenciales se leen de variables de entorno (Secrets de GitHub):
      SYSCOM_CLIENT_ID
      SYSCOM_CLIENT_SECRET
  Nunca las escribas en este archivo.

Archivos que genera (dentro de _site/data):
  meta.json        fecha de actualización, totales y tabla de rutas de imágenes
  arbol.json       categorías con número de productos
  marcas.json      marcas con número de productos
  l/<id>.json      productos de cada categoría de nivel 2 (lista ligera)
  d/<n>.json       fotos y características, repartidas en 400 archivos
  buscar.txt       índice para el buscador

Si algo falla a medio camino, el script termina con error y GitHub NO publica:
la página se queda con los datos de la última actualización buena.
"""

import os
import sys
import json
import time
import shutil
import datetime
import urllib.request
import urllib.parse
import urllib.error

API_BASE = os.environ.get("SYSCOM_API", "https://developers.syscom.mx/api/v1")
SALIDA = os.environ.get("SALIDA", "_site")
CUBETAS = 400                 # archivos de detalle (fotos y características)
MAX_FOTOS = 6
MAX_CARACT = 12
PAUSA = float(os.environ.get("PAUSA", "0.25"))   # segundos entre consultas
LIMITE_MINUTOS = 50
AGENTE = "Mozilla/5.0 (compatible; TotalSecurityTienda/1.0)"
INICIO = time.time()


def log(*a):
    print(*a, flush=True)


def pedir(url, datos=None, token=None, intentos=6):
    """Hace una petición HTTP con reintentos ante límites (429) o errores del servidor."""
    espera = 5
    for i in range(intentos):
        if time.time() - INICIO > LIMITE_MINUTOS * 60:
            raise RuntimeError("Se rebasó el tiempo máximo; se conserva la versión anterior.")
        h = {"User-Agent": AGENTE, "Accept": "application/json"}
        if token:
            h["Authorization"] = "Bearer " + token
        if datos is not None:
            h["Content-Type"] = "application/x-www-form-urlencoded"
        req = urllib.request.Request(url, data=datos, headers=h, method="POST" if datos is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            cuerpo = e.read().decode(errors="replace")[:300]
            if e.code == 404:
                return None
            if e.code in (429, 500, 502, 503, 504) and i < intentos - 1:
                log(f"  HTTP {e.code}, reintento en {espera}s…")
                time.sleep(espera)
                espera = min(espera * 2, 120)
                continue
            raise RuntimeError(f"HTTP {e.code} en {url.split('?')[0]}: {cuerpo}")
        except (urllib.error.URLError, TimeoutError) as e:
            if i < intentos - 1:
                log(f"  Error de red ({e}), reintento en {espera}s…")
                time.sleep(espera)
                espera = min(espera * 2, 120)
                continue
            raise
    raise RuntimeError("No se pudo completar la petición.")


def obtener_token():
    cid = os.environ.get("SYSCOM_CLIENT_ID")
    sec = os.environ.get("SYSCOM_CLIENT_SECRET")
    if not cid or not sec:
        sys.exit("Faltan SYSCOM_CLIENT_ID / SYSCOM_CLIENT_SECRET (Secrets del repositorio).")
    datos = urllib.parse.urlencode({"grant_type": "client_credentials", "client_id": cid, "client_secret": sec}).encode()
    r = pedir(API_BASE + "/oauth/token", datos=datos)
    if not r or not r.get("access_token"):
        sys.exit("Syscom no entregó el token. Revisa las credenciales.")
    return r["access_token"]


def limpiar_texto(s):
    return " ".join(str(s or "").replace("\r", " ").replace("\n", " ").replace("\t", " ").split())


def existencia_de(p):
    e = p.get("total_existencia", p.get("existencia", 0))
    if isinstance(e, dict):
        e = e.get("disponible") or e.get("total") or sum(
            (d.get("cantidad", 0) or d.get("disponible", 0) or 0) for d in e.get("detalle", []) if isinstance(d, dict))
    try:
        return max(0, int(float(e or 0)))
    except (TypeError, ValueError):
        return 0


def fotos_de(p):
    fotos = []
    port = p.get("img_portada") or ""
    if port:
        fotos.append(port)
    for img in p.get("imagenes") or []:
        u = img if isinstance(img, str) else (img.get("imagen") or img.get("url") or img.get("path") if isinstance(img, dict) else None)
        if u and u not in fotos:
            fotos.append(u)
    return [f for f in fotos if f.startswith("http")][:MAX_FOTOS]


class Prefijos:
    """Guarda las URL de imágenes como (índice de carpeta, archivo) para que los datos pesen menos."""
    def __init__(self):
        self.lista, self.idx = [], {}

    def ref(self, url):
        if not url:
            return ""
        # La carpeta común se corta dos niveles antes del archivo (…/MARCA/ + MODELO/foto.png)
        corte = url.rfind("/", 0, url.rfind("/")) + 1
        if corte <= len("https://"):
            corte = url.rfind("/") + 1
        pre, arch = url[:corte], url[corte:]
        if pre not in self.idx:
            self.idx[pre] = len(self.lista)
            self.lista.append(pre)
        return f"{self.idx[pre]}:{arch}"


def hojas(arbol, ruta=()):
    """Recorre el árbol y devuelve (categoría hoja, nivel1, nivel2)."""
    for c in arbol:
        r = ruta + (str(c["id"]),)
        subs = c.get("subcategorias") or []
        if subs:
            yield from hojas(subs, r)
        else:
            n1 = r[0]
            n2 = r[1] if len(r) > 1 else r[0]
            yield str(c["id"]), n1, n2


def main():
    log("Descargando categorías…")
    arbol = pedir(API_BASE + "/categorias?todas=1") or []
    if not arbol:
        sys.exit("No llegaron categorías.")
    token = obtener_token()
    log("Token obtenido.")

    productos = {}     # id -> datos
    por_n2 = {}        # id nivel2 -> {id producto: hoja}
    conteo_hoja = {}
    lista_hojas = list(hojas(arbol))
    log(f"{len(lista_hojas)} categorías finales por recorrer.")
    diagnostico = False

    for n, (hoja, n1, n2) in enumerate(lista_hojas, 1):
        pagina, paginas = 1, 1
        while pagina <= paginas:
            q = urllib.parse.urlencode({"categoria": hoja, "pagina": pagina, "limit": 1000, "moneda": "mxn", "imagenes": "true"})
            r = pedir(f"{API_BASE}/productos?{q}", token=token)
            time.sleep(PAUSA)
            if not r:
                break
            lote = r.get("productos", []) if isinstance(r, dict) else (r if isinstance(r, list) else [])
            paginas = int(r.get("paginas") or 1) if isinstance(r, dict) else 1
            if lote and not diagnostico:
                diagnostico = True
                log("Campos del primer producto:", sorted(lote[0].keys()))
            for p in lote:
                pid = str(p.get("producto_id") or "")
                if not pid:
                    continue
                if pid not in productos:
                    car = [limpiar_texto(c) for c in (p.get("caracteristicas") or []) if isinstance(c, str) and c.strip()]
                    productos[pid] = {
                        "modelo": limpiar_texto(p.get("modelo")),
                        "titulo": limpiar_texto(p.get("titulo")),
                        "marca": limpiar_texto(p.get("marca")),
                        "existencia": existencia_de(p),
                        "fotos": fotos_de(p),
                        "caract": car[:MAX_CARACT],
                        "garantia": limpiar_texto(p.get("garantia")) if isinstance(p.get("garantia"), (str, int, float)) else "",
                    }
                por_n2.setdefault(n2, {}).setdefault(pid, hoja)
                conteo_hoja[hoja] = conteo_hoja.get(hoja, 0) + 1
            pagina += 1
        if n % 25 == 0:
            log(f"  {n}/{len(lista_hojas)} categorías · {len(productos)} productos")

    if len(productos) < 100:
        sys.exit(f"Solo llegaron {len(productos)} productos; se cancela para no publicar una tienda vacía.")
    log(f"Total: {len(productos)} productos únicos.")

    # ---------- Escribir archivos ----------
    data = os.path.join(SALIDA, "data")
    if os.path.isdir(data):
        shutil.rmtree(data)
    os.makedirs(os.path.join(data, "l"))
    os.makedirs(os.path.join(data, "d"))
    px = Prefijos()

    def guardar(ruta, obj):
        with open(os.path.join(data, ruta), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))

    # Listas por categoría de nivel 2
    for n2, ids in por_n2.items():
        filas = []
        for pid, hoja in ids.items():
            p = productos[pid]
            filas.append([pid, p["modelo"], p["titulo"], p["marca"], px.ref(p["fotos"][0] if p["fotos"] else ""), p["existencia"], hoja])
        guardar(f"l/{n2}.json", filas)

    # Detalles repartidos en cubetas
    cubetas = {}
    for pid, p in productos.items():
        try:
            c = int(pid) % CUBETAS
        except ValueError:
            c = sum(map(ord, pid)) % CUBETAS
        cubetas.setdefault(c, {})[pid] = {"f": [px.ref(u) for u in p["fotos"]], "c": p["caract"], "g": p["garantia"]}
    for c, obj in cubetas.items():
        guardar(f"d/{c}.json", obj)

    # Índice del buscador: id, modelo, marca, título, nivel2, foto, existencia
    n2_de = {}
    for n2, ids in por_n2.items():
        for pid in ids:
            n2_de.setdefault(pid, n2)
    with open(os.path.join(data, "buscar.txt"), "w", encoding="utf-8") as f:
        for pid, p in productos.items():
            f.write("\t".join([pid, p["modelo"], p["marca"], p["titulo"][:140], n2_de.get(pid, ""),
                               px.ref(p["fotos"][0] if p["fotos"] else ""), str(p["existencia"])]) + "\n")

    # Árbol con conteos
    def contar(c):
        subs = c.get("subcategorias") or []
        hijos = [contar(s) for s in subs]
        total = sum(h["n"] for h in hijos) if hijos else conteo_hoja.get(str(c["id"]), 0)
        return {"id": str(c["id"]), "nombre": " ".join(str(c.get("nombre", "")).split()), "n": total, "subs": hijos}
    guardar("arbol.json", [contar(c) for c in arbol])

    marcas = {}
    for p in productos.values():
        if p["marca"]:
            marcas[p["marca"]] = marcas.get(p["marca"], 0) + 1
    guardar("marcas.json", sorted(([m, k] for m, k in marcas.items()), key=lambda x: x[0].lower()))

    ahora = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=-6)))
    guardar("meta.json", {"actualizado": ahora.strftime("%Y-%m-%d %H:%M"), "productos": len(productos),
                          "cubetas": CUBETAS, "px": px.lista})
    log(f"Listo en {int(time.time() - INICIO)} s.")


if __name__ == "__main__":
    main()
