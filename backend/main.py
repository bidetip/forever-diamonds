from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
from datetime import date
import database

app = FastAPI(title="Forever Diamonds API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── MODELOS ───────────────────────────────────────────────
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

# ── STATUS ────────────────────────────────────────────────
@app.get("/")
def inicio():
    return {"sistema": "Forever Diamonds", "estado": "activo", "version": "2.0.0"}

@app.get("/api/status")
def status():
    return {"status": "OK"}

# ── CATÁLOGO Y STOCK ──────────────────────────────────────
@app.get("/api/catalogo")
def catalogo():
    return database.get_catalogo()

@app.get("/api/stock/{codigo_cip}")
def stock(codigo_cip: str):
    disponible = database.get_stock(codigo_cip)
    return {
        "codigo_cip": codigo_cip,
        "stock_disponible": disponible,
        "disponible": disponible > 0
    }

@app.get("/api/precio/{codigo_cip}")
def precio(codigo_cip: str, fecha: str = str(date.today())):
    resultado = database.get_precio_venta(codigo_cip, fecha)
    if "error" in resultado:
        raise HTTPException(status_code=404, detail=resultado["error"])
    return resultado

# ── VENTAS ────────────────────────────────────────────────
@app.get("/api/ventas/todas")
def todas_las_ventas(
    desde: Optional[str] = None,
    hasta: Optional[str] = None,
    tipo: Optional[str] = None,
    cliente: Optional[str] = None
):
    """Retorna todas las ventas con filtros opcionales."""
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
        raise HTTPException(
            status_code=400,
            detail=f"Sin stock disponible para {venta.codigo_cip}"
        )
    precio_info = database.get_precio_venta(venta.codigo_cip, venta.fecha_venta)
    if "error" in precio_info:
        raise HTTPException(status_code=400, detail=precio_info["error"])

    precio_venta = precio_info["precio_venta"]
    precio_final = round(precio_venta - venta.ajuste_precio, 2)

    datos = {
        "codigo_cip":    venta.codigo_cip,
        "fecha_venta":   venta.fecha_venta,
        "cliente":       venta.cliente,
        "precio_venta":  precio_venta,
        "ajuste_precio": venta.ajuste_precio,
        "precio_final":  precio_final,
        "metodo_pago":   venta.metodo_pago,
        "tipo_venta":    venta.tipo_venta,
        "vendedor":      venta.vendedor,
        "observaciones": venta.observaciones or "",
        "costo_adq":     precio_info["costo_landed"],
    }
    resultado = database.registrar_venta(datos)
    if "error" in resultado:
        raise HTTPException(status_code=500, detail=resultado["error"])

    # Registrar cliente si no existe
    database.registrar_cliente_si_no_existe(venta.cliente)

    resultado["precio_venta"] = precio_venta
    resultado["precio_final"] = precio_final
    resultado["mensaje"] = f"Venta {resultado['id_venta']} registrada — Precio Final: ${precio_final:,.2f}"
    return resultado

@app.get("/api/ventas/{id_venta}/saldo")
def saldo(id_venta: str):
    resultado = database.get_saldo_venta(id_venta)
    if "error" in resultado:
        raise HTTPException(status_code=404, detail=resultado["error"])
    return resultado

# ── PAGOS ─────────────────────────────────────────────────
@app.post("/api/pagos")
def nuevo_pago(pago: NuevoPago):
    if pago.monto <= 0:
        raise HTTPException(status_code=400, detail="El monto debe ser mayor a $0")
    resultado = database.registrar_pago(pago.dict())
    if "error" in resultado:
        raise HTTPException(status_code=400, detail=resultado["error"])
    if resultado.get("pagado_completo"):
        resultado["mensaje"] = "¡Venta pagada al 100%!"
    else:
        resultado["mensaje"] = f"Pago registrado. Saldo restante: ${resultado['nuevo_saldo']:,.2f}"
    return resultado

# ── CLIENTES ──────────────────────────────────────────────
@app.get("/api/clientes")
def listar_clientes(q: Optional[str] = None):
    """Lista clientes, con búsqueda opcional."""
    conn = database.get_conn()
    cur = conn.cursor()
    if q:
        rows = cur.execute(
            "SELECT * FROM clientes WHERE UPPER(nombre) LIKE ? ORDER BY nombre",
            (f"%{q.upper()}%",)
        ).fetchall()
    else:
        rows = cur.execute(
            "SELECT * FROM clientes ORDER BY nombre"
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.post("/api/clientes")
def nuevo_cliente(cliente: NuevoCliente):
    """Registra un cliente nuevo."""
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
        return {"success": True, "id": id_nuevo, "nombre": cliente.nombre.strip(), "mensaje": f"Cliente '{cliente.nombre}' registrado correctamente"}
    except Exception as e:
        conn.rollback()
        conn.close()
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/clientes/buscar")
def buscar_clientes(q: str):
    """Busca clientes para el autocompletado."""
    if len(q) < 1:
        return []
    conn = database.get_conn()
    cur = conn.cursor()
    rows = cur.execute(
        "SELECT nombre FROM clientes WHERE UPPER(nombre) LIKE ? ORDER BY nombre LIMIT 10",
        (f"%{q.upper()}%",)
    ).fetchall()
    conn.close()
    return [r['nombre'] for r in rows]

# ── PROVEEDORES ───────────────────────────────────────────
@app.get("/api/proveedores")
def proveedores():
    conn = database.get_conn()
    cur = conn.cursor()
    rows = cur.execute("SELECT * FROM proveedores").fetchall()
    conn.close()
    return [dict(r) for r in rows]

# ── RESUMEN DASHBOARD ─────────────────────────────────────
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
        "total_ventas":     total_ventas[0],
        "monto_ventas":     round(total_ventas[1], 2),
        "creditos_activos": cxc[0],
        "saldo_cxc":        round(cxc[1], 2)
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
