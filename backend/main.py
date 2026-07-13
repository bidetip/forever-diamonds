from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
from datetime import date
import database

app = FastAPI(title="Forever Diamonds API", version="1.0.0")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

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

@app.get("/")
def inicio():
    return {"sistema": "Forever Diamonds", "estado": "activo"}

@app.get("/api/status")
def status():
    return {"status": "OK"}

@app.get("/api/catalogo")
def catalogo():
    return database.get_catalogo()

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
    datos = {"codigo_cip": venta.codigo_cip, "fecha_venta": venta.fecha_venta, "cliente": venta.cliente, "precio_venta": precio_venta, "ajuste_precio": venta.ajuste_precio, "precio_final": precio_final, "metodo_pago": venta.metodo_pago, "tipo_venta": venta.tipo_venta, "vendedor": venta.vendedor, "observaciones": venta.observaciones or "", "costo_adq": precio_info["costo_landed"]}
    resultado = database.registrar_venta(datos)
    if "error" in resultado:
        raise HTTPException(status_code=500, detail=resultado["error"])
    resultado["precio_venta"] = precio_venta
    resultado["precio_final"] = precio_final
    resultado["mensaje"] = "Venta " + resultado["id_venta"] + " registrada correctamente"
    return resultado

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

@app.get("/api/resumen")
def resumen():
    conn = database.get_conn()
    cur = conn.cursor()
    total_ventas = cur.execute("SELECT COUNT(*), COALESCE(SUM(precio_final),0) FROM ventas WHERE estado='Activa'").fetchone()
    conn.close()
    return {"total_ventas": total_ventas[0], "monto_ventas": round(total_ventas[1], 2)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)