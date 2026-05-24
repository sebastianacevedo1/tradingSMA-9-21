from binance.client import Client
from binance.exceptions import BinanceAPIException

# Coloca tus llaves aquí
API_KEY = 'ugrQPLNPrbnoe9eb8wB6UQTz4IgbXNCAnNdn1gdH3biGP0DXu0PE95AI0WmCKWPx'
SECRET_KEY = 'qhthUAR6MsABz9KguAyzTn9DPm4wv0E4MMmzNzwIucHvGCiqexv8e43biEz5PAW8'

try:
    print("Conectando de forma segura a Binance...")
    # Inicializamos el cliente oficial de Binance
    client = Client(API_KEY, SECRET_KEY)

    # Revisamos el estado del servidor de Binance
    status = client.get_system_status()
    print(f"Estado del sistema: {status['msg']}")

    # Traemos la información de tu cuenta Spot
    print("Leyendo balances de la billetera...")
    account = client.get_account()
    balances = account['balances']

    print("\nTUS FONDOS DISPONIBLES:")
    encontro_saldo = False
    
    for crypto in balances:
        disponible = float(crypto['free'])
        bloqueado = float(crypto['locked']) # Dinero retenido en órdenes abiertas
        
        # Solo mostramos las monedas donde tengas algo de saldo
        if disponible > 0 or bloqueado > 0:
            print(f"💰 {crypto['asset']}: Disponible = {disponible} | En Órdenes = {bloqueado}")
            encontro_saldo = True

    if not encontro_saldo:
        print("Tu billetera Spot está en 0. Necesitas transferir algunos fondos (como USDT) para que el bot opere.")

except BinanceAPIException as e:
    print(f"\n❌ Error de Binance: {e.message} (Código de estado: {e.status_code})")
except Exception as e:
    print(f"\n❌ Error inesperado: {e}")