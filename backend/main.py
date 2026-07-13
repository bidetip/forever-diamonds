from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
from datetime import date
import database

app = FastAPI(title="Forever Diamonds API", version="1.0.0")

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
    return {"codigo_cip": codigo_cip, "stock_disponible": disponible,