from binance.client import Client
import config
import time
import pandas as pd
import logging

# Binance API'ye bağlan
client = Client(config.API_KEY, config.API_SECRET)

# İşlem yapacağımız altcoinler
symbols = ["SHIBUSDT", "NEIROUSDT"]

# Alım ve stop fiyatlarını takip eden sözlükler
target_sell_prices = {}
stop_loss_prices = {}
buy_balances = {"SHIBUSDT": 0, "NEIROUSDT": 0}  # Her coin için alınan yatırım miktarı (USD cinsinden)
coin_amounts = {"SHIBUSDT": 0, "NEIROUSDT": 0}  # Her coin için alınan coin miktarı

# Logları başlat
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Kullanıcı USDT bakiyesini almak
def get_balance():
    try:
        balance = client.get_asset_balance(asset='USDT')
        return float(balance['free'])
    except Exception as e:
        logging.error(f"Balance çekme hatası: {e}")
        return 0

# Coin hassasiyetini almak
def get_symbol_precision(symbol):
    info = client.get_symbol_info(symbol)
    for filter in info['filters']:
        if filter['filterType'] == 'LOT_SIZE':
            precision = int(filter['stepSize'].find('1') - 1)
            return precision
    return 6  # Default hassasiyet

# Binance minimum işlem miktarını almak
def get_symbol_minimum_quantity(symbol):
    info = client.get_symbol_info(symbol)
    for filter in info['filters']:
        if filter['filterType'] == 'LOT_SIZE':
            return float(filter['minQty'])
    return 0.000001  # Default minimum miktar

# Binance minimum işlem tutarını almak (NOTIONAL)
def get_symbol_minimum_notional(symbol):
    info = client.get_symbol_info(symbol)
    for filter in info['filters']:
        if filter['filterType'] == 'NOTIONAL':
            return float(filter['minNotional'])
    return 0.0001  # Default minimum notional

# Alım işlemi yapmak (coin’e özel para ile)
def buy(symbol, current_price):
    global target_sell_prices, stop_loss_prices, buy_balances, coin_amounts
    
    balance = get_balance()
    if balance <= 1:  # Kasanın 2 USD'den az olmaması durumunda alım yapma
        logging.warning(f"Yetersiz bakiye ({balance} USDT), kasada en az 2 USD kalmalı.")
        return
    
    # Coin başına 1 USD yatırım yap
    invest_amount = 1.5  #ırım miktarını 1 USD olarak sabitledik
    precision = get_symbol_precision(symbol)  # Coin'in hassasiyetini al
    quantity = round(invest_amount / current_price, precision)  # Alım miktarını hassasiyete göre yuvarla

    # Minimum işlem tutarını kontrol et
    min_notional = get_symbol_minimum_notional(symbol)
    if quantity * current_price < min_notional:
        logging.warning(f"{symbol} için alım miktarı minimum işlem tutarını geçmiyor ({min_notional} USDT), işlem yapılmadı.")
        return

    logging.info(f"{symbol} alımı yapılıyor! Yatırım miktarı: {invest_amount} USDT, Alınacak miktar: {quantity} {symbol}")
    
    try:
        order = client.order_market_buy(symbol=symbol, quantity=quantity)
        logging.info(f"Alım işlemi başarılı: {order}")
    except Exception as e:
        logging.error(f"{symbol} alımı hatası: {e}")
        return
    
    # Alınan miktarı kaydet (coin cinsinden miktar)
    coin_amounts[symbol] += quantity
    target_sell_prices[symbol] = current_price * 1.008  # %0.8 kar hedefi
    stop_loss_prices[symbol] = current_price * 0.98  # %2 stop-loss seviyesi

# Satış işlemi yapmak
def sell(symbol, quantity):
    try:
        logging.info(f"{symbol} satılıyor! Miktar: {quantity}")
        order = client.order_market_sell(symbol=symbol, quantity=quantity)
        logging.info(f"Satış işlemi başarılı: {order}")
        
        # Satıştan sonra fiyatları sıfırla
        target_sell_prices[symbol] = None
        stop_loss_prices[symbol] = None
        coin_amounts[symbol] = 0
    except Exception as e:
        logging.error(f"{symbol} satışı hatası: {e}")

# Strateji hesaplamalarını optimize et
def calculate_indicators(df):
    # EMA hesaplama
    df['EMA12'] = df['close'].ewm(span=12, adjust=False).mean()
    df['EMA26'] = df['close'].ewm(span=26, adjust=False).mean()

    # RSI hesaplama
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.rolling(window=14).mean()
    avg_loss = loss.rolling(window=14).mean()
    rs = avg_gain / avg_loss
    df['RSI'] = 100 - (100 / (1 + rs))

    # MACD hesaplama
    df['MACD'] = df['EMA12'] - df['EMA26']
    df['MACD_signal'] = df['MACD'].ewm(span=9, adjust=False).mean()

    # Bollinger Bands hesaplama
    df['SMA20'] = df['close'].rolling(window=20).mean()
    df['stddev'] = df['close'].rolling(window=20).std()
    df['UpperBand'] = df['SMA20'] + (df['stddev'] * 2)
    df['LowerBand'] = df['SMA20'] - (df['stddev'] * 2)
    
    return df

# Ana işlem döngüsü
while True:
    for symbol in symbols:
        try:
            ticker = client.get_symbol_ticker(symbol=symbol)
            current_price = float(ticker['price'])
            logging.info(f"Anlık {symbol} Fiyatı: {current_price} USDT")

            # Mum verisi al
            klines = client.get_klines(symbol=symbol, interval=Client.KLINE_INTERVAL_1MINUTE, limit=100)
            df = pd.DataFrame(klines, columns=['time', 'open', 'high', 'low', 'close', 'volume',
                                               'close_time', 'quote_asset_volume', 'trades',
                                               'taker_base', 'taker_quote', 'ignore'])
            df['time'] = pd.to_datetime(df['time'], unit='ms')
            df['close'] = df['close'].astype(float)

            # Strateji sinyalleri için indikatör hesaplama
            df = calculate_indicators(df)

            # Sinyalleri hesapla
            signals = []
            # EMA Sinyali
            signals.append(1 if df['EMA12'].iloc[-1] > df['EMA26'].iloc[-1] else -1)
            # RSI Sinyali
            rsi_value = df['RSI'].iloc[-1]
            if rsi_value < 30:
                signals.append(1)  # Alış sinyali
            elif rsi_value > 70:
                signals.append(-1)  # Satış sinyali
            else:
                signals.append(0)  # Kararsız sinyal
            # MACD Sinyali
            signals.append(1 if df['MACD'].iloc[-1] > df['MACD_signal'].iloc[-1] else -1)
            # Bollinger Bands Sinyali
            bollinger_signal = 1 if df['close'].iloc[-1] < df['LowerBand'].iloc[-1] else (-1 if df['close'].iloc[-1] > df['UpperBand'].iloc[-1] else 0)
            signals.append(bollinger_signal)

            total_signal = sum(signals)
            logging.info(f"Toplam sinyal: {total_signal}")

            # Alım yapma kararı
            if total_signal >= 1 and coin_amounts[symbol] == 0:
                logging.info(f"{symbol} için ALIM sinyali tespit edildi!")
                buy(symbol, current_price)

            # Satış yapma kararı
            if symbol in target_sell_prices and target_sell_prices[symbol] and current_price >= target_sell_prices[symbol]:
                logging.info(f"{symbol} hedef satış fiyatına ulaştı ({target_sell_prices[symbol]} USDT), satış yapılıyor.")
                sell(symbol, coin_amounts[symbol])  # Alınan miktarı sat

            # Stop-Loss kontrolü
            elif symbol in stop_loss_prices and stop_loss_prices[symbol] and current_price <= stop_loss_prices[symbol]:
                logging.info(f"{symbol} stop-loss seviyesine ({stop_loss_prices[symbol]} USDT) ulaştı, zarar kes yapılıyor.")
                sell(symbol, coin_amounts[symbol])  # Alınan miktarı sat

            else:
                logging.info(f"{symbol} için Kararsız sinyal, işlem yapılmadı.")

        except Exception as e:
            logging.error(f"{symbol} için işlem hatası: {e}")
    
    # 10 saniye beklemeden önce tüm semboller için işlemi yapacak
    logging.info("Bekleniyor... 10 saniye")
    time.sleep(10)  # 10 saniye bekle
