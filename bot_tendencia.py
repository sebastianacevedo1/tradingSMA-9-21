"""
Bot de Trading REAL Tendencial para Binance
Estrategia: Cruce de Medias Móviles Simples (SMA)

ADVERTENCIA: Este código ejecuta operaciones con dinero real en la cuenta Spot de Binance.
"""

import json
import time
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional
import urllib.request

from binance.client import Client
from binance.exceptions import BinanceAPIException, BinanceOrderException

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ─── CONFIGURACIÓN CRUCIAL: Pon tus credenciales reales aquí ──────────────────
API_KEY    = ""
SECRET_KEY = ""

# Parámetros de Telegram (Ya probados y funcionales)
TELEGRAM_ACTIVO  = True
TELEGRAM_TOKEN   = ""
TELEGRAM_CHAT_ID = ""

# Parámetros del Mercado
PAR_TRADING     = "BTCUSDT"
BASE_ASSET      = "BTC"   # Moneda que compras
QUOTE_ASSET     = "USDT"  # Moneda con la que pagas
INTERVALO_VELAS = Client.KLINE_INTERVAL_1MINUTE
PERIODO_RAPIDO  = 9
PERIODO_LENTO   = 21
INTERVALO_LOOP  = 10             
VELAS_LIMITE    = 50             
ARCHIVO_ESTADO  = Path("estado_bot_real.json")  # Cambiamos el archivo para no mezclar con pruebas

# Parámetros de Gestión de Riesgo (Modifícalos a tu gusto)
STOP_LOSS_PCT   = 0.01   # 1.0%
TAKE_PROFIT_PCT = 0.02   # 2.0%


# ─── Alertas Telegram ─────────────────────────────────────────────────────────
def enviar_telegram(mensaje: str) -> None:
    """Envía una notificación HTTP síncrona a Telegram en texto plano para evitar errores 400."""
    if not TELEGRAM_ACTIVO:
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    
    # Eliminamos los asteriscos de formato para que sea texto limpio y no confunda a la API
    texto_limpio = mensaje.replace("*", "")
    
    payload = json.dumps({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": texto_limpio
        # Quitamos la línea de parse_mode para que no intente validar HTML ni Markdown
    }).encode("utf-8")

    headers = {"Content-Type": "application/json"}
    
    try:
        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=5) as response:
            if response.status != 200:
                log.error("Error al enviar a Telegram: Código de estado %s", response.status)
    except Exception as exc:
        log.error("No se pudo conectar con la API de Telegram: %s", exc)


# ─── Estado Real del Bot ──────────────────────────────────────────────────────
@dataclass
class Estado:
    usdt: float           = 0.0
    btc: float            = 0.0
    en_posicion: bool     = False
    operaciones: int      = 0
    pnl_total: float      = 0.0
    precio_entrada: float = 0.0
    cruce_anterior: Optional[bool] = None

    _precio_actual: float = field(default=0.0, init=False, repr=False)

    @property
    def valor_total(self) -> float:
        return self.usdt + self.btc * self._precio_actual

    def set_precio_actual(self, precio: float) -> None:
        self._precio_actual = precio

    def guardar(self) -> None:
        datos = {k: v for k, v in asdict(self).items() if not k.startswith("_") and k != "cruce_anterior"}
        ARCHIVO_ESTADO.write_text(json.dumps(datos, indent=2))

    @classmethod
    def cargar(cls, client: Client) -> "Estado":
        """Carga el historial técnico pero sincroniza los balances reales directamente desde Binance."""
        instancia = cls()
        if ARCHIVO_ESTADO.exists():
            try:
                datos = json.loads(ARCHIVO_ESTADO.read_text())
                instancia = cls(**datos)
                log.info("♻️  Historial de operaciones restaurado desde el archivo JSON.")
            except Exception as exc:
                log.warning("No se pudo leer el archivo de estado anterior: %s", exc)
        
        # Sincronización forzada con tu billetera real de Binance
        instancia.sincronizar_balances_reales(client)
        return instancia

    def sincronizar_balances_reales(self, client: Client) -> None:
        """Consulta la API de Binance para saber cuánto dinero real tienes en la billetera Spot."""
        try:
            saldo_usdt = float(client.get_asset_balance(asset=QUOTE_ASSET)["free"])
            saldo_btc  = float(client.get_asset_balance(asset=BASE_ASSET)["free"])
            
            self.usdt = saldo_usdt
            self.btc  = saldo_btc
            
            # Si tienes una cantidad significativa de BTC, asumimos que estás dentro de una posición
            # (Fijamos un umbral mínimo de 0.0001 BTC para ignorar polvos/remanentes de comisiones)
            if self.btc > 0.0001:
                self.en_posicion = True
            else:
                self.en_posicion = False
                self.btc = 0.0

            log.info(f"💰 [SINCRO REAL] Balance en Binance -> USDT disponible: ${self.usdt:,.2f} | BTC libre: {self.btc:.6f}")
        except Exception as exc:
            log.error("❌ Error crítico al sincronizar balances reales con Binance: %s", exc)


# ─── Mercado ──────────────────────────────────────────────────────────────────
def obtener_indicadores(client: Client) -> tuple[Optional[float], Optional[float], Optional[float]]:
    try:
        velas = client.get_klines(symbol=PAR_TRADING, interval=INTERVALO_VELAS, limit=VELAS_LIMITE)
        cierres = [float(v[4]) for v in velas if v[4] and float(v[4]) > 0]

        if len(cierres) < PERIODO_LENTO:
            return None, None, None

        sma_rapida = sum(cierres[-PERIODO_RAPIDO:]) / PERIODO_RAPIDO
        sma_lenta  = sum(cierres[-PERIODO_LENTO:])  / PERIODO_LENTO
        precio     = cierres[-1]

        return sma_rapida, sma_lenta, precio
    except Exception as exc:
        log.error("Error al obtener datos de mercado: %s", exc)
    return None, None, None


# ─── Trading Real (Órdenes a Mercado) ─────────────────────────────────────────
def ejecutar_compra_real(client: Client, estado: Estado, precio: float) -> None:
    try:
        log.info(f"🛒 Enviando ORDEN DE COMPRA REAL a Binance por un total de ${estado.usdt:,.2f} USDT...")
        
        # Ajuste de seguridad: dejamos 1 USDT libre para cubrir variaciones de comisiones flotantes
        monto_compra = floor_float(estado.usdt - 1.0, 2)
        
        if monto_compra < 11.0:
            log.error("❌ Saldo insuficiente en USDT para cumplir el mínimo de compra de Binance ($10 USDT).")
            return

        # 🚀 ORDEN REAL DE COMPRA EN SPOT
        orden = client.order_market_buy(symbol=PAR_TRADING, quoteOrderQty=monto_compra)
        
        # Procesamos la respuesta oficial de Binance
        precio_ejecutado = sum(float(f['price']) * float(f['qty']) for f in orden['fills']) / sum(float(f['qty']) for f in orden['fills']) if orden['fills'] else precio
        btc_adquirido = float(orden['executedQty'])
        
        estado.btc            = btc_adquirido
        estado.precio_entrada = precio_ejecutado
        estado.usdt           = 0.0
        estado.en_posicion    = True
        estado.operaciones   += 1
        
        log.info(f"🟩 COMPRA REAL EXITOSA | Precio Promedio: ${precio_ejecutado:,.2f} | Comprado: {btc_adquirido:.6f} BTC")
        
        precio_sl = precio_ejecutado * (1 - STOP_LOSS_PCT)
        precio_tp = precio_ejecutado * (1 + TAKE_PROFIT_PCT)
        
        estado.guardar()
        enviar_telegram(f"🟩 COMPRA REAL EJECUTADA\n\n• Precio: ${precio_ejecutado:,.2f} USDT\n• Comprado: {btc_adquirido:.6f} BTC\n• SL: ${precio_sl:,.2f} | TP: ${precio_tp:,.2f}")

    except (BinanceAPIException, BinanceOrderException) as exc:
        log.error("❌ La orden de compra fue rechazada por Binance: %s", exc)
        enviar_telegram(f"❌ ERROR CRÍTICO: Binance rechazó la orden de compra real. Detalles: {exc}")
        estado.sincronizar_balances_reales(client) # Re-sincronizar para evitar bloqueos


def ejecutar_venta_real(client: Client, estado: Estado, precio: float, motivo: str = "ESTRATEGIA") -> None:
    try:
        log.info(f"🛒 Enviando ORDEN DE VENTA REAL a Binance por un total de {estado.btc:.6f} BTC...")
        
        # Truncamos los decimales de BTC según las reglas de Binance para evitar errores de precisión
        cantidad_venta = floor_float(estado.btc, 5)

        # 🚀 ORDEN REAL DE VENTA EN SPOT
        orden = client.order_market_sell(symbol=PAR_TRADING, quantity=cantidad_venta)
        
        precio_ejecutado = sum(float(f['price']) * float(f['qty']) for f in orden['fills']) / sum(float(f['qty']) for f in orden['fills']) if orden['fills'] else precio
        usdt_recibido = float(orden['cummulativeQuoteQty'])
        
        ganancia = (precio_ejecutado - estado.precio_entrada) * cantidad_venta
        estado.usdt        = usdt_recibido
        estado.pnl_total  += ganancia
        estado.btc         = 0.0
        estado.en_posicion = False
        estado.operaciones += 1
        
        log.info(f"🟥 VENTA REAL EXITOSA ({motivo}) | Precio: ${precio_ejecutado:,.2f} | Recibido: ${usdt_recibido:,.2f} USDT")
        
        estado.guardar()
        icono = "🚨" if "STOP" in motivo else "💰" if "PROFIT" in motivo else "⚡"
        enviar_telegram(f"{icono} VENTA REAL EJECUTADA ({motivo})\n\n• Precio Venta: ${precio_ejecutado:,.2f} USDT\n• Resultado: {ganancia:+,.2f} USDT\n• Saldo Total: ${usdt_recibido:,.2f} USDT")

    except (BinanceAPIException, BinanceOrderException) as exc:
        log.error("❌ La orden de venta fue rechazada por Binance: %s", exc)
        enviar_telegram(f"❌ ERROR CRÍTICO: ¡No se pudo ejecutar la venta real! Cierre la posición manualmente en la app. Detalles: {exc}")
        estado.sincronizar_balances_reales(client)


# ─── Estrategia ───────────────────────────────────────────────────────────────
def evaluar_senal(client: Client, estado: Estado, sma_r: float, sma_l: float, precio: float) -> None:
    estado.set_precio_actual(precio)
    rapida_arriba = sma_r > sma_l

    log.info(f"REAL | BTC: ${precio:,.2f} | SMA(9): {sma_r:,.2f} | SMA(21): {sma_l:,.2f} | Balance Total: ${estado.valor_total:,.2f} USDT")

    # 1. Monitoreo de Riesgo Real
    if estado.en_posicion:
        precio_sl = estado.precio_entrada * (1 - STOP_LOSS_PCT)
        precio_tp = estado.precio_entrada * (1 + TAKE_PROFIT_PCT)

        if precio <= precio_sl:
            log.warning(f"🚨 STOP LOSS REAL GATILLADO!")
            ejecutar_venta_real(client, estado, precio, motivo="STOP LOSS")
            estado.cruce_anterior = rapida_arriba
            return

        if precio >= precio_tp:
            log.info(f"💰 TAKE PROFIT REAL GATILLADO!")
            ejecutar_venta_real(client, estado, precio, motivo="TAKE PROFIT")
            estado.cruce_anterior = rapida_arriba
            return

    # 2. Calibración en Frío
    if estado.cruce_anterior is None:
        estado.cruce_anterior = rapida_arriba
        log.info("📡 Calibración inicial de mercado completada en producción.")
        enviar_telegram(f"🤖 Bot Real en Marcha\nMonitoreando {PAR_TRADING}. Sincronizado con tus fondos reales.")
        return

    # 3. Decisiones de Cruce
    hubo_cruce = rapida_arriba != estado.cruce_anterior

    if hubo_cruce:
        if rapida_arriba and not estado.en_posicion:
            log.info("⚡ Cruce alcista en mercado real.")
            ejecutar_compra_real(client, estado, precio)
        elif not rapida_arriba and estado.en_posicion:
            log.info("⚡ Cruce bajista en mercado real.")
            ejecutar_venta_real(client, estado, precio, motivo="CRUCE MEDIAS")

    estado.cruce_anterior = rapida_arriba


# ─── Utilidades ───────────────────────────────────────────────────────────────
def floor_float(n: float, decimals: int) -> float:
    """Trunca los decimales sin redondear hacia arriba para evitar rechazos de Binance."""
    factor = 10 ** decimals
    return int(n * factor) / factor


# ─── Main Loop ────────────────────────────────────────────────────────────────
def main() -> None:
    # Verificación de credenciales básicas
    if not API_KEY or API_KEY.startswith("TU_"):
        raise ValueError("Error: Debes configurar tus claves API reales de Binance para operar.")

    client = Client(API_KEY, SECRET_KEY)
    estado = Estado.cargar(client)

    log.info("🚀 BOT EN PRODUCCIÓN INICIADO — PAR: %s", PAR_TRADING)
    log.info("%s", "═" * 60)

    while True:
        try:
            sma_r, sma_l, precio = obtener_indicadores(client)
            if precio is not None:
                evaluar_senal(client, estado, sma_r, sma_l, precio)
        except Exception as exc:
            log.error("Error mitigado dentro del loop principal: %s", exc)

        time.sleep(INTERVALO_LOOP)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("🛑 Bot detenido manualmente por el operador.")
    except Exception as exc:
        log.critical("💥 Apagado general por error fatal: %s", exc, exc_info=True)