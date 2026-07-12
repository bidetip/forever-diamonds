const express = require('express');
const { google } = require('googleapis');
const cors = require('cors');

const app = express();
app.use(cors());
app.use(express.json());

const SPREADSHEET_ID = '1IfWgybziIeGPNR_RtPeoH3C0UvqY6NEomw63PoRpDPk';
const SHEET_VENTAS = 'VENTAS';
const SHEET_PAGOS  = 'PAGOS';

async function getAuthClient() {
  const credentials = process.env.GOOGLE_CREDENTIALS
    ? JSON.parse(process.env.GOOGLE_CREDENTIALS)
    : require('./credentials.json');
  const auth = new google.auth.GoogleAuth({
    credentials,
    scopes: ['https://www.googleapis.com/auth/spreadsheets'],
  });
  return auth.getClient();
}

async function getSheets() {
  const auth = await getAuthClient();
  return google.sheets({ version: 'v4', auth });
}

function generarIdPago() {
  const ts  = Date.now().toString(36).toUpperCase();
  const rnd = Math.random().toString(36).substring(2, 5).toUpperCase();
  return `PAG-${ts}-${rnd}`;
}

// RUTA 1: Registrar venta nueva
app.post('/api/ventas', async (req, res) => {
  try {
    const { vendedor, codigoCIP, fechaVenta, cliente, metodoPago, tipoVenta, precioTotal, ajustePrecio, observaciones } = req.body;
    const obligatorios = { vendedor, codigoCIP, fechaVenta, cliente, metodoPago, tipoVenta, precioTotal };
    const faltantes = Object.entries(obligatorios).filter(([, v]) => !v).map(([k]) => k);
    if (faltantes.length) return res.status(400).json({ error: `Faltan campos: ${faltantes.join(', ')}` });

    const sheets = await getSheets();
    const existing = await sheets.spreadsheets.values.get({ spreadsheetId: SPREADSHEET_ID, range: `${SHEET_VENTAS}!A:A` });
    const cipList = (existing.data.values || []).flat();
    if (cipList.includes(codigoCIP)) return res.status(409).json({ error: `El CIP ${codigoCIP} ya tiene una venta registrada.` });

    await sheets.spreadsheets.values.append({
      spreadsheetId: SPREADSHEET_ID,
      range: `${SHEET_VENTAS}!A:J`,
      valueInputOption: 'USER_ENTERED',
      resource: { values: [[codigoCIP, fechaVenta, cliente, vendedor, metodoPago, tipoVenta, parseFloat(precioTotal)||0, parseFloat(ajustePrecio)||0, observaciones||'', new Date().toISOString()]] },
    });
    res.json({ success: true, mensaje: 'Venta registrada correctamente' });
  } catch (err) {
    console.error('Error /api/ventas:', err);
    res.status(500).json({ error: 'Error interno. Intenta de nuevo.' });
  }
});

// RUTA 2: Buscar venta por CIP o cliente
app.get('/api/ventas/buscar', async (req, res) => {
  try {
    const { q } = req.query;
    if (!q || q.trim().length < 2) return res.status(400).json({ error: 'Ingresa al menos 2 caracteres.' });

    const sheets  = await getSheets();
    const termino = q.trim().toUpperCase();

    const ventasResp = await sheets.spreadsheets.values.get({ spreadsheetId: SPREADSHEET_ID, range: `${SHEET_VENTAS}!A:J` });
    const ventas = (ventasResp.data.values || []).slice(1).map(f => ({
      codigoCIP: f[0]||'', fechaVenta: f[1]||'', cliente: f[2]||'', vendedor: f[3]||'',
      metodoPago: f[4]||'', tipoVenta: f[5]||'',
      precioTotal: parseFloat(f[6])||0, ajuste: parseFloat(f[7])||0,
    }));

    const encontradas = ventas.filter(v =>
      v.codigoCIP.toUpperCase().includes(termino) || v.cliente.toUpperCase().includes(termino)
    );
    if (!encontradas.length) return res.json({ resultados: [] });

    const pagosResp = await sheets.spreadsheets.values.get({ spreadsheetId: SPREADSHEET_ID, range: `${SHEET_PAGOS}!A:H` });
    const todosPagos = (pagosResp.data.values || []).slice(1).map(p => ({ codigoCIP: p[1]||'', monto: parseFloat(p[4])||0 }));

    const resultados = encontradas.map(v => {
      const totalPagado = todosPagos.filter(p => p.codigoCIP === v.codigoCIP).reduce((s, p) => s + p.monto, 0);
      const precioFinal = v.precioTotal - v.ajuste;
      const saldo = Math.round((precioFinal - totalPagado) * 100) / 100;
      return { ...v, precioFinal: Math.round(precioFinal*100)/100, totalPagado: Math.round(totalPagado*100)/100, saldo, pagado: saldo <= 0 };
    });
    res.json({ resultados });
  } catch (err) {
    console.error('Error /api/ventas/buscar:', err);
    res.status(500).json({ error: 'Error al buscar. Intenta de nuevo.' });
  }
});

// RUTA 3: Registrar pago
app.post('/api/pagos', async (req, res) => {
  try {
    const { codigoCIP, cliente, fechaPago, monto, vendedor, notas } = req.body;
    if (!codigoCIP || !fechaPago || !monto || !vendedor) return res.status(400).json({ error: 'Faltan campos obligatorios.' });
    if (parseFloat(monto) <= 0) return res.status(400).json({ error: 'El monto debe ser mayor a $0.' });

    const sheets = await getSheets();
    const ventasResp = await sheets.spreadsheets.values.get({ spreadsheetId: SPREADSHEET_ID, range: `${SHEET_VENTAS}!A:H` });
    const venta = (ventasResp.data.values||[]).slice(1).find(f => f[0] === codigoCIP);
    if (!venta) return res.status(404).json({ error: 'No se encontró la venta.' });

    const precioFinal = (parseFloat(venta[6])||0) - (parseFloat(venta[7])||0);
    const pagosResp = await sheets.spreadsheets.values.get({ spreadsheetId: SPREADSHEET_ID, range: `${SHEET_PAGOS}!A:H` });
    const pagosPrevios = (pagosResp.data.values||[]).slice(1).filter(p => p[1]===codigoCIP).reduce((s,p) => s+(parseFloat(p[4])||0), 0);
    const saldoActual = Math.round((precioFinal - pagosPrevios)*100)/100;
    const montoNum = parseFloat(monto);

    if (montoNum > saldoActual + 0.01) return res.status(400).json({ error: `El monto ($${montoNum}) supera el saldo pendiente ($${saldoActual}).` });

    await sheets.spreadsheets.values.append({
      spreadsheetId: SPREADSHEET_ID,
      range: `${SHEET_PAGOS}!A:H`,
      valueInputOption: 'USER_ENTERED',
      resource: { values: [[generarIdPago(), codigoCIP, cliente||venta[2]||'', fechaPago, montoNum, vendedor, notas||'', new Date().toISOString()]] },
    });

    const nuevoSaldo = Math.round((saldoActual - montoNum)*100)/100;
    res.json({ success: true, saldoAnterior: saldoActual, montoPagado: montoNum, nuevoSaldo, pagadoCompleto: nuevoSaldo <= 0 });
  } catch (err) {
    console.error('Error /api/pagos:', err);
    res.status(500).json({ error: 'Error al registrar el pago. Intenta de nuevo.' });
  }
});

app.get('/api/status', (_, res) => res.json({ status: 'OK', sistema: 'Forever Diamonds' }));

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => console.log(`Servidor Forever Diamonds en puerto ${PORT}`));
