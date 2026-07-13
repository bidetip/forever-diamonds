import sqlite3
import os
import datetime

DB_PATH = os.environ.get('DB_PATH', 'forever_diamonds.db')

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def get_stock(codigo_cip):
    conn = get_conn()
    cur = conn.cursor()
    compradas = cur.execute("SELECT COALESCE(SUM(cantidad),0) FROM compras WHERE codigo_cip=?", (codigo_cip,)).fetchone()[0]
    vendidas = cur.execute("SELECT COUNT(*) FROM ventas WHERE codigo_cip=? AND estado='Activa'", (codigo_cip,)).fetchone()[0]
    devueltas = cur.execute("SELECT COUNT(*) FROM devoluciones WHERE codigo_cip=? AND estado='Procesada'", (codigo_cip,)).fetchone()[0]
    conn.close()
    return int(compradas) - int(vendidas) + int(devueltas)

def get_precio_venta(codigo_cip, fecha_venta):
    conn = get_conn()
    cur = conn.cursor()
    costo = cur.execute("""
        SELECT c.precio_neto, c.lote, l.factor_costo
        FROM compras c JOIN lotes l ON c.lote = l.nombre
        WHERE c.codigo_cip = ? ORDER BY c.fecha_compra ASC LIMIT 1
    """, (codigo_cip,)).fetchone()
    if not costo:
        conn.close()
        return {"error": "CIP no encontrado"}
    costo_landed = round(costo['precio_neto'] * costo['factor_costo'], 2)
    factor_venta = cur.execute("""
        SELECT factor FROM factores_venta
        WHERE vigente_desde <= ? ORDER BY vigente_desde DESC LIMIT 1
    """, (fecha_venta,)).fetchone()
    if not factor_venta:
        conn.close()
        return {"error": "No hay factor de venta configurado"}
    precio_venta = round(costo_landed * factor_venta['factor'], 2)
    conn.close()
    return {"codigo_cip": codigo_cip, "costo_landed": costo_landed, "factor_venta": factor_venta['factor'], "precio_venta": precio_venta}

def get_siguiente_id_venta():
    conn = get_conn()
    cur = conn.cursor()
    ultimo = cur.execute("SELECT id_venta FROM ventas ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    if not ultimo:
        return "VTA-0001"
    num = int(ultimo['id_venta'].split('-')[1]) + 1
    return f"VTA-{num:04d}"

def get_siguiente_id_pago():
    conn = get_conn()
    cur = conn.cursor()
    total = cur.execute("SELECT COUNT(*) as total FROM pagos").fetchone()
    conn.close()
    return f"PAG-{(total['total'] or 0) + 1:04d}"

def registrar_venta(datos):
    conn = get_conn()
    cur = conn.cursor()
    try:
        id_venta = get_siguiente_id_venta()
        cur.execute("""
            INSERT INTO ventas (id_venta,codigo_cip,descripcion,fecha_venta,cliente,
            precio_venta,ajuste_precio,precio_final,metodo_pago,tipo_venta,vendedor,observaciones,costo_adq,estado)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (id_venta, datos['codigo_cip'], datos.get('descripcion',''), datos['fecha_venta'],
              datos['cliente'], datos['precio_venta'], datos.get('ajuste_precio',0),
              datos['precio_final'], datos['metodo_pago'], datos['tipo_venta'],
              datos['vendedor'], datos.get('observaciones',''), datos.get('costo_adq',0), 'Activa'))
        conn.commit()
        return {"success": True, "id_venta": id_venta}
    except Exception as e:
        conn.rollback()
        return {"error": str(e)}
    finally:
        conn.close()

def get_saldo_venta(id_venta):
    conn = get_conn()
    cur = conn.cursor()
    venta = cur.execute("SELECT id_venta,cliente,precio_final,tipo_venta FROM ventas WHERE id_venta=?", (id_venta,)).fetchone()
    if not venta:
        conn.close()
        return {"error": "Venta no encontrada"}
    total_pagado = cur.execute("SELECT COALESCE(SUM(monto),0) FROM pagos WHERE id_venta=?", (id_venta,)).fetchone()[0]
    saldo = round(venta['precio_final'] - float(total_pagado), 2)
    conn.close()
    return {"id_venta": venta['id_venta'], "cliente": venta['cliente'], "precio_final": venta['precio_final'], "total_pagado": round(float(total_pagado),2), "saldo": saldo, "pagado_completo": saldo <= 0}

def registrar_pago(datos):
    conn = get_conn()
    cur = conn.cursor()
    try:
        saldo_info = get_saldo_venta(datos['id_venta'])
        if 'error' in saldo_info:
            return saldo_info
        if datos['monto'] > saldo_info['saldo'] + 0.01:
            return {"error": f"El monto supera el saldo pendiente (${saldo_info['saldo']:.2f})"}
        id_pago = get_siguiente_id_pago()
        cur.execute("""
            INSERT INTO pagos (id_pago,id_venta,codigo_cip,cliente,fecha_pago,monto,metodo_pago,vendedor,notas)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (id_pago, datos['id_venta'], datos.get('codigo_cip',''), datos.get('cliente',''),
              datos['fecha_pago'], datos['monto'], datos['metodo_pago'], datos['vendedor'], datos.get('notas','')))
        conn.commit()
        nuevo_saldo = round(saldo_info['saldo'] - datos['monto'], 2)
        return {"success": True, "id_pago": id_pago, "saldo_anterior": saldo_info['saldo'], "monto_pagado": datos['monto'], "nuevo_saldo": nuevo_saldo, "pagado_completo": nuevo_saldo <= 0}
    except Exception as e:
        conn.rollback()
        return {"error": str(e)}
    finally:
        conn.close()

def buscar_ventas(termino):
    conn = get_conn()
    cur = conn.cursor()
    t = f"%{termino.upper()}%"
    ventas = cur.execute("""
        SELECT v.id_venta,v.codigo_cip,v.cliente,v.fecha_venta,v.precio_final,v.tipo_venta,v.estado,
               COALESCE(SUM(p.monto),0) as total_pagado
        FROM ventas v LEFT JOIN pagos p ON v.id_venta=p.id_venta
        WHERE UPPER(v.cliente) LIKE ? OR UPPER(v.codigo_cip) LIKE ?
        GROUP BY v.id_venta ORDER BY v.fecha_venta DESC
    """, (t,t)).fetchall()
    conn.close()
    return [dict(row) for row in ventas]

def get_catalogo():
    conn = get_conn()
    cur = conn.cursor()
    productos = cur.execute("""
        SELECT DISTINCT c.codigo_cip,c.descripcion,c.material,c.talla,c.proveedor,
               SUM(c.cantidad) as unidades_compradas
        FROM compras c GROUP BY c.codigo_cip ORDER BY c.codigo_cip
    """).fetchall()
    conn.close()
    return [dict(row) for row in productos]