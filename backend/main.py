from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
from datetime import date
import io, re
import openpyxl
import database

app = FastAPI(title="Forever Diamonds API", version="3.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

class NuevaVenta(BaseModel):
    codigo_cip: str
    fecha_venta: str
    cliente: str
    ajuste_precio: float = 0
    metodo_pago: str
    tipo_venta: str
    vendedor: str
    observaciones: Optional[str] = ""

class NuevoPago(BaseModel):
    id_venta: str
    fecha_pago: str
    monto: float
    metodo_pago: str
    vendedor: str
    notas: Optional[str] = ""

class NuevoCliente(BaseModel):
    nombre: str
    telefono: Optional[str] = ""
    email: Optional[str] = ""
    notas: Optional[str] = ""

class NuevoLote(BaseModel):
    nombre: str
    descripcion: Optional[str] = ""

class FilaCompra(BaseModel):
    codigo_cip: str
    codigo_proveedor: Optional[str] = ""
    descripcion: str
    detalle_tecnico: Optional[str] = ""
    material: str
    talla: Optional[str] = ""
    peso_g: Optional[float] = None
    precio_lista: float
    descuento: float = 0
    precio_neto: float
    proveedor: str
    fecha_compra: str
    factura: str
    cantidad: int = 1
    valor_total: float

class ConfirmarCompra(BaseModel):
    lote: str
    filas: List[FilaCompra]

@app.get("/")
def inicio():
    return {"sistema": "Forever Diamonds", "estado": "activo", "version": "3.0.0"}

@app.get("/api/status")
def status():
    return {"status": "OK"}

@app.get("/api/catalogo")
def catalogo():
    return database.get_catalogo()

@app.get("/api/stock/resumen")
def stock_resumen():
    conn = database.get_conn()
    cur = conn.cursor()
    stocks = cur.execute("""
        SELECT c.codigo_cip,
            COALESCE(SUM(c.cantidad),0) - COALESCE(v.vendidas,0) + COALESCE(d.devueltas,0) as stock_real
        FROM compras c
        LEFT JOIN (SELECT codigo_cip, COUNT(*) as vendidas FROM ventas WHERE estado='Activa' GROUP BY codigo_cip) v ON c.codigo_cip = v.codigo_cip
        LEFT JOIN (SELECT codigo_cip, COUNT(*) as devueltas FROM devoluciones WHERE estado='Procesada' GROUP BY codigo_cip) d ON c.codigo_cip = d.codigo_cip
        GROUP BY c.codigo_cip
    """).fetchall()
    conn.close()
    agotados = sum(1 for s in stocks if s['stock_real'] <= 0)
    con_stock = sum(1 for s in stocks if s['stock_real'] > 0)
    return {"total": len(stocks), "con_stock": con_stock, "agotados": agotados}

@app.get("/api/stock/agotados")
def stock_agotados():
    conn = database.get_conn()
    cur = conn.cursor()
    stocks = cur.execute("""
        SELECT c.codigo_cip, MAX(c.descripcion) as descripcion,
            COALESCE(SUM(c.cantidad),0) - COALESCE(v.vendidas,0) + COALESCE(d.devueltas,0) as stock_real
        FROM compras c
        LEFT JOIN (SELECT codigo_cip, COUNT(*) as vendidas FROM ventas WHERE estado='Activa' GROUP BY codigo_cip) v ON c.codigo_cip = v.codigo_cip
        LEFT JOIN (SELECT codigo_cip, COUNT(*) as devueltas FROM devoluciones WHERE estado='Procesada' GROUP BY codigo_cip) d ON c.codigo_cip = d.codigo_cip
        GROUP BY c.codigo_cip
        HAVING stock_real <= 0
        ORDER BY c.codigo_cip
    """).fetchall()
    conn.close()
    return {"agotados": [dict(r) for r in stocks], "total": len(stocks)}

@app.get("/api/stock/{codigo_cip}")
def stock(codigo_cip: str):
    disponible = database.get_stock(codigo_cip)
    return {"codigo_cip": codigo_cip, "stock_disponible": disponible, "disponible": disponible > 0}

@app.get("/api/precio/{codigo_cip}")
def precio(codigo_cip: str, fecha: str = str(date.today())):
    resultado = database.get_precio_venta(codigo_cip, fecha)
    if "error" in resultado:
        raise HTTPException(status_code=404, detail=resultado["error"])
    return resultado

@app.get("/api/ventas/todas")
def todas_las_ventas(
    desde: Optional[str] = None,
    hasta: Optional[str] = None,
    tipo: Optional[str] = None,
    cliente: Optional[str] = None
):
    conn = database.get_conn()
    cur = conn.cursor()
    query = """
        SELECT v.id_venta, v.codigo_cip, v.descripcion, v.cliente,
               v.fecha_venta, v.precio_venta, v.ajuste_precio,
               v.precio_final, v.metodo_pago, v.tipo_venta,
               v.vendedor, v.observaciones, v.estado,
               COALESCE(SUM(p.monto),0) as total_pagado
        FROM ventas v
        LEFT JOIN pagos p ON v.id_venta = p.id_venta
        WHERE 1=1
    """
    params = []
    if desde:
        query += " AND v.fecha_venta >= ?"
        params.append(desde)
    if hasta:
        query += " AND v.fecha_venta <= ?"
        params.append(hasta)
    if tipo:
        query += " AND v.tipo_venta = ?"
        params.append(tipo)
    if cliente:
        query += " AND UPPER(v.cliente) LIKE ?"
        params.append(f"%{cliente.upper()}%")
    query += " GROUP BY v.id_venta ORDER BY v.fecha_venta DESC, v.id DESC"
    ventas = cur.execute(query, params).fetchall()
    conn.close()
    result = []
    for row in ventas:
        v = dict(row)
        v['saldo'] = round(v['precio_final'] - v['total_pagado'], 2)
        v['pagado_completo'] = v['saldo'] <= 0
        result.append(v)
    return {
        "ventas": result,
        "total": len(result),
        "total_monto": round(sum(v['precio_final'] for v in result), 2),
        "total_saldo": round(sum(v['saldo'] for v in result if v['saldo'] > 0), 2)
    }

@app.get("/api/ventas/buscar")
def buscar(q: str):
    if len(q) < 2:
        raise HTTPException(status_code=400, detail="Ingresa al menos 2 caracteres")
    resultados = database.buscar_ventas(q)
    for v in resultados:
        saldo = round(v['precio_final'] - v['total_pagado'], 2)
        v['saldo'] = saldo
        v['pagado_completo'] = saldo <= 0
    return {"resultados": resultados}

@app.post("/api/ventas")
def nueva_venta(venta: NuevaVenta):
    stock_disponible = database.get_stock(venta.codigo_cip)
    if stock_disponible <= 0:
        raise HTTPException(status_code=400, detail="Sin stock disponible para " + venta.codigo_cip)
    precio_info = database.get_precio_venta(venta.codigo_cip, venta.fecha_venta)
    if "error" in precio_info:
        raise HTTPException(status_code=400, detail=precio_info["error"])
    precio_venta = precio_info["precio_venta"]
    precio_final = round(precio_venta - venta.ajuste_precio, 2)
    datos = {
        "codigo_cip": venta.codigo_cip,
        "fecha_venta": venta.fecha_venta,
        "cliente": venta.cliente,
        "precio_venta": precio_venta,
        "ajuste_precio": venta.ajuste_precio,
        "precio_final": precio_final,
        "metodo_pago": venta.metodo_pago,
        "tipo_venta": venta.tipo_venta,
        "vendedor": venta.vendedor,
        "observaciones": venta.observaciones or "",
        "costo_adq": precio_info["costo_landed"],
    }
    resultado = database.registrar_venta(datos)
    if "error" in resultado:
        raise HTTPException(status_code=500, detail=resultado["error"])
    database.registrar_cliente_si_no_existe(venta.cliente)
    resultado["precio_venta"] = precio_venta
    resultado["precio_final"] = precio_final
    resultado["mensaje"] = "Venta " + resultado["id_venta"] + " registrada"
    return resultado

@app.get("/api/ventas/{id_venta}/saldo")
def saldo(id_venta: str):
    resultado = database.get_saldo_venta(id_venta)
    if "error" in resultado:
        raise HTTPException(status_code=404, detail=resultado["error"])
    return resultado

@app.post("/api/pagos")
def nuevo_pago(pago: NuevoPago):
    if pago.monto <= 0:
        raise HTTPException(status_code=400, detail="El monto debe ser mayor a $0")
    resultado = database.registrar_pago(pago.dict())
    if "error" in resultado:
        raise HTTPException(status_code=400, detail=resultado["error"])
    if resultado.get("pagado_completo"):
        resultado["mensaje"] = "Venta pagada al 100%"
    else:
        resultado["mensaje"] = "Pago registrado correctamente"
    return resultado

@app.get("/api/clientes")
def listar_clientes(q: Optional[str] = None):
    conn = database.get_conn()
    cur = conn.cursor()
    if q:
        rows = cur.execute("SELECT * FROM clientes WHERE UPPER(nombre) LIKE ? ORDER BY nombre", (f"%{q.upper()}%",)).fetchall()
    else:
        rows = cur.execute("SELECT * FROM clientes ORDER BY nombre").fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.post("/api/clientes")
def nuevo_cliente(cliente: NuevoCliente):
    if not cliente.nombre.strip():
        raise HTTPException(status_code=400, detail="El nombre es obligatorio")
    conn = database.get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO clientes (nombre, telefono, email, notas) VALUES (?,?,?,?)",
            (cliente.nombre.strip(), cliente.telefono or "", cliente.email or "", cliente.notas or "")
        )
        conn.commit()
        id_nuevo = cur.lastrowid
        conn.close()
        return {"success": True, "id": id_nuevo, "nombre": cliente.nombre.strip()}
    except Exception as e:
        conn.rollback()
        conn.close()
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/proveedores")
def proveedores():
    conn = database.get_conn()
    cur = conn.cursor()
    rows = cur.execute("SELECT * FROM proveedores").fetchall()
    conn.close()
    return [dict(r) for r in rows]

# ═══════════════ COMPRAS (Módulo 4) ═══════════════
TIPOS_VALIDOS = {'AN', 'AR', 'BR', 'CO', 'DI', 'PU'}

def _siguiente_numero_cip(cur):
    cur.execute("SELECT codigo_cip FROM compras")
    maxnum = 0
    for row in cur.fetchall():
        m = re.search(r'-(\d+)$', row['codigo_cip'] or '')
        if m:
            maxnum = max(maxnum, int(m.group(1)))
    return maxnum

@app.get("/api/lotes")
def listar_lotes():
    conn = database.get_conn()
    cur = conn.cursor()
    rows = cur.execute("SELECT * FROM lotes ORDER BY id DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.post("/api/lotes")
def crear_lote(lote: NuevoLote):
    if not lote.nombre.strip():
        raise HTTPException(status_code=400, detail="El nombre del lote es obligatorio")
    conn = database.get_conn()
    cur = conn.cursor()
    existe = cur.execute("SELECT id FROM lotes WHERE nombre=?", (lote.nombre.strip(),)).fetchone()
    if existe:
        conn.close()
        raise HTTPException(status_code=400, detail="Ya existe un lote con ese nombre")
    cur.execute(
        "INSERT INTO lotes (nombre, descripcion, estado, fecha_apertura, total_compras, total_gastos, factor_costo) VALUES (?,?,?,?,0,0,1.0)",
        (lote.nombre.strip(), lote.descripcion or "", "Abierto", str(date.today()))
    )
    conn.commit()
    id_nuevo = cur.lastrowid
    conn.close()
    return {"success": True, "id": id_nuevo, "nombre": lote.nombre.strip()}

@app.post("/api/compras/preview")
async def preview_compra_excel(file: UploadFile = File(...), lote: str = Form(...)):
    contenido = await file.read()
    try:
        wb = openpyxl.load_workbook(io.BytesIO(contenido), data_only=True)
        ws = wb.active
    except Exception as e:
        raise HTTPException(status_code=400, detail="No se pudo leer el archivo Excel: " + str(e))

    filas_raw = list(ws.iter_rows(values_only=True))
    if len(filas_raw) < 2:
        raise HTTPException(status_code=400, detail="El Excel no tiene filas de datos")

    encabezado = [str(c).strip().lower() if c else "" for c in filas_raw[0]]
    requeridas = ["tipo_joya", "material", "codigo_proveedor", "descripcion", "precio_lista", "proveedor", "fecha_compra", "factura"]
    faltantes = [r for r in requeridas if r not in encabezado]
    if faltantes:
        raise HTTPException(status_code=400, detail="Faltan columnas en el Excel: " + ", ".join(faltantes))

    idx = {nombre: encabezado.index(nombre) for nombre in encabezado if nombre}

    conn = database.get_conn()
    cur = conn.cursor()
    siguiente = _siguiente_numero_cip(cur) + 1

    def val(row, campo, default=None):
        i = idx.get(campo)
        if i is None or i >= len(row):
            return default
        v = row[i]
        return v if v not in (None, "") else default

    filas_resultado = []
    total_valor = 0.0
    advertencias = 0
    for row in filas_raw[1:]:
        if row is None or all(c is None for c in row):
            continue
        tipo = str(val(row, "tipo_joya", "") or "").strip().upper()
        material = str(val(row, "material", "") or "").strip().upper()
        if tipo not in TIPOS_VALIDOS:
            conn.close()
            raise HTTPException(status_code=400, detail="tipo_joya inválido: '" + tipo + "' (debe ser AN, AR, BR, CO, DI o PU)")
        if not material:
            conn.close()
            raise HTTPException(status_code=400, detail="Falta 'material' en una fila")
        codigo_cip = "FD-" + tipo + "-" + material + "-" + str(siguiente).zfill(5)
        siguiente += 1

        precio_lista = float(val(row, "precio_lista", 0) or 0)
        descuento = float(val(row, "descuento", 0) or 0)
        cantidad = int(val(row, "cantidad", 1) or 1)
        precio_neto = round(precio_lista - descuento, 2)
        valor_total = round(precio_neto * cantidad, 2)
        proveedor = str(val(row, "proveedor", "") or "").strip()
        codigo_proveedor = str(val(row, "codigo_proveedor", "") or "").strip()
        factura = str(val(row, "factura", "") or "").strip()
        fecha_raw = val(row, "fecha_compra")
        fecha_compra = fecha_raw.strftime("%Y-%m-%d") if hasattr(fecha_raw, "strftime") else str(fecha_raw or "")
        comprador = str(val(row, "comprador_factura", "") or "").strip()

        advertencia_comprador = bool(comprador) and comprador.upper() != "FOREVER DIAMONDS"
        dup = cur.execute(
            "SELECT COUNT(*) as n FROM compras WHERE proveedor=? AND factura=? AND codigo_proveedor=?",
            (proveedor, factura, codigo_proveedor)
        ).fetchone()
        advertencia_duplicado = dup["n"] > 0
        if advertencia_comprador or advertencia_duplicado:
            advertencias += 1

        total_valor += valor_total
        filas_resultado.append({
            "codigo_cip": codigo_cip,
            "codigo_proveedor": codigo_proveedor,
            "descripcion": str(val(row, "descripcion", "") or "").strip(),
            "detalle_tecnico": str(val(row, "detalle_tecnico", "") or "").strip(),
            "material": material,
            "talla": str(val(row, "talla", "") or "").strip(),
            "peso_g": float(val(row, "peso_g")) if val(row, "peso_g") is not None else None,
            "precio_lista": precio_lista,
            "descuento": descuento,
            "precio_neto": precio_neto,
            "proveedor": proveedor,
            "fecha_compra": fecha_compra,
            "factura": factura,
            "cantidad": cantidad,
            "valor_total": valor_total,
            "comprador_factura": comprador,
            "advertencia_comprador": advertencia_comprador,
            "advertencia_duplicado": advertencia_duplicado
        })
    conn.close()

    if not filas_resultado:
        raise HTTPException(status_code=400, detail="No se encontraron filas válidas en el Excel")

    return {
        "lote": lote,
        "filas": filas_resultado,
        "total_filas": len(filas_resultado),
        "total_valor": round(total_valor, 2),
        "advertencias": advertencias
    }

@app.post("/api/compras/confirmar")
def confirmar_compra(datos: ConfirmarCompra):
    if not datos.filas:
        raise HTTPException(status_code=400, detail="No hay filas para registrar")
    conn = database.get_conn()
    cur = conn.cursor()

    lote_row = cur.execute("SELECT * FROM lotes WHERE nombre=?", (datos.lote,)).fetchone()
    if not lote_row:
        cur.execute(
            "INSERT INTO lotes (nombre, descripcion, estado, fecha_apertura, total_compras, total_gastos, factor_costo) VALUES (?,?,?,?,0,0,1.0)",
            (datos.lote, "", "Abierto", str(date.today()))
        )
        conn.commit()

    proveedores_afectados = set()
    for f in datos.filas:
        cur.execute("""
            INSERT INTO compras (codigo_cip, codigo_proveedor, descripcion, detalle_tecnico,
                material, talla, peso_g, precio_lista, descuento, precio_neto, proveedor,
                fecha_compra, factura, cantidad, lote, valor_total)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (f.codigo_cip, f.codigo_proveedor, f.descripcion, f.detalle_tecnico,
              f.material, f.talla, f.peso_g, f.precio_lista, f.descuento, f.precio_neto,
              f.proveedor, f.fecha_compra, f.factura, f.cantidad, datos.lote, f.valor_total))
        proveedores_afectados.add(f.proveedor)
    conn.commit()

    total_compras_lote = cur.execute(
        "SELECT COALESCE(SUM(valor_total),0) as t FROM compras WHERE lote=?", (datos.lote,)
    ).fetchone()["t"]
    total_gastos_lote = cur.execute(
        "SELECT COALESCE(total_gastos,0) as t FROM lotes WHERE nombre=?", (datos.lote,)
    ).fetchone()["t"]
    factor = round((total_compras_lote + total_gastos_lote) / total_compras_lote, 10) if total_compras_lote > 0 else 1.0
    cur.execute("UPDATE lotes SET total_compras=?, factor_costo=? WHERE nombre=?",
                (total_compras_lote, factor, datos.lote))

    for prov in proveedores_afectados:
        stats = cur.execute(
            "SELECT COUNT(DISTINCT factura) as nf, COALESCE(SUM(valor_total),0) as tc, MAX(fecha_compra) as uc FROM compras WHERE proveedor=?",
            (prov,)
        ).fetchone()
        existe_prov = cur.execute("SELECT id FROM proveedores WHERE nombre=?", (prov,)).fetchone()
        if existe_prov:
            cur.execute(
                "UPDATE proveedores SET nro_facturas=?, total_compras=?, ultima_compra=? WHERE nombre=?",
                (stats["nf"], stats["tc"], stats["uc"], prov)
            )
    conn.commit()
    conn.close()

    return {
        "success": True,
        "insertados": len(datos.filas),
        "lote": datos.lote,
        "factor_costo_actualizado": factor,
        "codigos_cip": [f.codigo_cip for f in datos.filas]
    }

@app.get("/api/resumen")
def resumen():
    conn = database.get_conn()
    cur = conn.cursor()
    total_ventas = cur.execute(
        "SELECT COUNT(*), COALESCE(SUM(precio_final),0) FROM ventas WHERE estado='Activa'"
    ).fetchone()
    cxc = cur.execute("""
        SELECT COUNT(*), COALESCE(SUM(v.precio_final - COALESCE(p.pagado,0)),0)
        FROM ventas v
        LEFT JOIN (SELECT id_venta, SUM(monto) as pagado FROM pagos GROUP BY id_venta) p
        ON v.id_venta = p.id_venta
        WHERE v.tipo_venta='Crédito' AND v.estado='Activa'
        AND ROUND(v.precio_final - COALESCE(p.pagado,0), 2) > 0
    """).fetchone()
    conn.close()
    return {
        "total_ventas": total_ventas[0],
        "monto_ventas": round(total_ventas[1], 2),
        "creditos_activos": cxc[0],
        "saldo_cxc": round(cxc[1], 2)
    }

@app.get("/api/estado-resultados")
def estado_resultados(desde: Optional[str] = None, hasta: Optional[str] = None):
    conn = database.get_conn()
    cur = conn.cursor()
    filtro = "WHERE v.estado='Activa'"
    params = []
    if desde:
        filtro += " AND v.fecha_venta >= ?"
        params.append(desde)
    if hasta:
        filtro += " AND v.fecha_venta <= ?"
        params.append(hasta)
    r = cur.execute(f"""
        SELECT COUNT(*) as n_ventas,
            COALESCE(SUM(v.precio_venta),0) as precio_teorico,
            COALESCE(SUM(v.ajuste_precio),0) as total_ajustes,
            COALESCE(SUM(v.precio_final),0) as ventas_brutas,
            COALESCE(SUM(v.costo_adq),0) as costo_landed
        FROM ventas v {filtro}
    """, params).fetchone()
    filtro_dev = "WHERE 1=1"
    params_dev = []
    if desde:
        filtro_dev += " AND fecha_devolucion >= ?"
        params_dev.append(desde)
    if hasta:
        filtro_dev += " AND fecha_devolucion <= ?"
        params_dev.append(hasta)
    dev = cur.execute(f"SELECT COALESCE(SUM(monto_devolver),0) as total_dev FROM devoluciones {filtro_dev}", params_dev).fetchone()
    filtro_op = "WHERE 1=1"
    params_op = []
    if desde:
        filtro_op += " AND fecha >= ?"
        params_op.append(desde)
    if hasta:
        filtro_op += " AND fecha <= ?"
        params_op.append(hasta)
    opex = cur.execute(f"SELECT COALESCE(SUM(monto),0) as total_opex FROM gastos_operativos {filtro_op}", params_op).fetchone()
    conn.close()
    ventas_brutas = round(r['ventas_brutas'], 2)
    precio_teorico = round(r['precio_teorico'], 2)
    total_ajustes = round(r['total_ajustes'], 2)
    costo_landed = round(r['costo_landed'], 2)
    total_dev = round(dev['total_dev'], 2)
    total_opex = round(opex['total_opex'], 2)
    ingresos_netos = round(ventas_brutas - total_dev, 2)
    ganancia_bruta = round(ingresos_netos - costo_landed, 2)
    margen_bruto = round(ganancia_bruta / ingresos_netos * 100, 2) if ingresos_netos > 0 else 0
    markup = round(ganancia_bruta / costo_landed * 100, 2) if costo_landed > 0 else 0
    ganancia_neta = round(ganancia_bruta - total_opex, 2)
    margen_neto = round(ganancia_neta / ingresos_netos * 100, 2) if ingresos_netos > 0 else 0
    return {
        "n_ventas": r['n_ventas'],
        "precio_teorico": precio_teorico,
        "total_ajustes": total_ajustes,
        "ventas_brutas": ventas_brutas,
        "total_devoluciones": total_dev,
        "ingresos_netos": ingresos_netos,
        "costo_landed": costo_landed,
        "ganancia_bruta": ganancia_bruta,
        "margen_bruto_pct": margen_bruto,
        "markup_pct": markup,
        "opex": total_opex,
        "ganancia_neta": ganancia_neta,
        "margen_neto_pct": margen_neto
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)