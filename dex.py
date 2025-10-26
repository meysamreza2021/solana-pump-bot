import requests
import pandas as pd
from datetime import datetime, timezone
import time
import io
import os  # برای خواندن env variables

# خواندن از GitHub secrets (env)
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', 'YOUR_BOT_TOKEN_HERE')
CHAT_ID = os.environ.get('CHAT_ID', 'YOUR_CHAT_ID_HERE')

print(f"TOKEN loaded: {TELEGRAM_TOKEN[:10]}...")  # دیباگ: اول ۱۰ کاراکتر
print(f"CHAT_ID loaded: {CHAT_ID}")

# تابع برای parse سن تقریبی از last_updated (به ساعت)
def parse_age(last_updated):
    try:
        if not last_updated:
            return float('inf')
        dt = datetime.fromisoformat(last_updated.replace('Z', '+00:00'))
        age_hours = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
        return age_hours
    except:
        return float('inf')

# تابع ارسال به تلگرام (متن + فایل از buffer)
def send_to_telegram(message, excel_buffer=None):
    url = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage'
    payload = {'chat_id': CHAT_ID, 'text': message, 'parse_mode': 'HTML'}
    response = requests.post(url, json=payload)
    print(f"Send message status: {response.status_code}")
    print(f"Send message response: {response.text}")
    if response.status_code != 200:
        print(f"خطا در ارسال متن: {response.text}")
        return

    if excel_buffer and len(excel_buffer.getvalue()) > 0:  # ارسال فایل اکسل از buffer
        excel_buffer.seek(0)
        files = {'document': ('filtered_pump_coins_gecko.xlsx', excel_buffer, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')}
        data = {'chat_id': CHAT_ID, 'caption': 'فایل اکسل فیلترشده (با Name و Contract)'}
        file_response = requests.post(f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument', files=files, data=data)
        print(f"Send file status: {file_response.status_code}")
        print(f"Send file response: {file_response.text}")
        if file_response.status_code != 200:
            print(f"خطا در ارسال فایل: {file_response.text}")

# گام ۱: گرفتن لیست توکن‌های Solana و ذخیره contract addresses
list_url = 'https://api.coingecko.com/api/v3/coins/list?include_platform=true'
list_response = requests.get(list_url)
print(f"List response status: {list_response.status_code}")
if list_response.status_code != 200:
    send_to_telegram("خطا در لیست CoinGecko. بررسی اینترنت یا VPN.")
    exit()

coins_list = list_response.json()
solana_ids = []
id_to_contract = {}
for coin in coins_list:
    platform = coin.get('platforms', {})
    solana_addr = platform.get('solana')
    if solana_addr:
        solana_ids.append(coin['id'])
        id_to_contract[coin['id']] = solana_addr

print(f"تعداد توکن‌های Solana پیدا شده: {len(solana_ids)}")

# گام ۲: گرفتن markets برای top Solana (batch ۱۰۰ تا، با sleep برای rate limit)
rows = []
page = 1
per_page = 100
while len(rows) < 1000:
    ids_str = ','.join(solana_ids[(page-1)*per_page:page*per_page])
    if not ids_str:
        break
    markets_url = f'https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&ids={ids_str}&order=volume_desc&per_page={per_page}&page=1&price_change_percentage=1h,24h&sparkline=false'
    markets_response = requests.get(markets_url)
    print(f"Markets page {page} status: {markets_response.status_code}")
    if markets_response.status_code != 200:
        print(f"خطا در markets page {page}. صبر کنید...")
        time.sleep(5)
        continue

    markets = markets_response.json()
    for coin in markets:
        name = coin.get('name', 'N/A')  # نام کامل
        symbol = coin.get('symbol', 'N/A').upper()
        price_change_1h = coin.get('price_change_percentage_1h_in_currency', 0) or 0
        vol_24h = coin.get('total_volume', 0)
        mc = coin.get('market_cap', 0)
        last_updated = coin.get('last_updated')
        age_h = parse_age(last_updated)
        id_ = coin.get('id', 'N/A')
        contract = id_to_contract.get(id_, 'N/A')

        rows.append([id_, name, f"{symbol}/SOL", symbol, price_change_1h, vol_24h, mc, age_h, contract])  # اضافه کردن name

    print(f"Page {page}: {len(markets)} توکن پردازش شد. کل: {len(rows)}")
    page += 1
    time.sleep(2)

# ساخت DataFrame با ستون Name
df = pd.DataFrame(rows, columns=['Coin ID', 'Name', 'Pair Name', 'Symbol', '1h Change %', '24h Vol (USD)', 'MC (USD)', 'Age (h)', 'Contract Address'])

# فیلترها
filtered_df = df[
    (df['1h Change %'] > 10) &
    (df['24h Vol (USD)'] > 1000000) &
    (df['MC (USD)'] < 10000000) &
    ((df['Age (h)'] < 24) | pd.isna(df['Age (h)']))
].copy()

# ذخیره در buffer (با Name و Contract)
output_buffer = io.BytesIO()
with pd.ExcelWriter(output_buffer, engine='openpyxl') as writer:
    filtered_df.to_excel(writer, index=False)
output_buffer.seek(0)

# پیام تلگرام: زمان + جدول top 10 با Name, Symbol, Contract (لینک Solscan)
now = datetime.now().strftime('%Y-%m-%d %H:%M')
message = f"<b>گزارش پامپ کوین‌های Solana</b> ({now})\n\nتعداد فیلترشده: {len(filtered_df)}\n\n"
if len(filtered_df) > 0:
    top10 = filtered_df.nlargest(10, '24h Vol (USD)')
    for _, row in top10.iterrows():
        contract_link = f"https://solscan.io/token/{row['Contract Address']}"
        message += f"<b>{row['Name']}</b> ({row['Symbol']}) | 1h: {row['1h Change %']:.2f}% | Vol: ${row['24h Vol (USD)']:,.0f} | MC: ${row['MC (USD)']:,.0f}\nContract: <a href='{contract_link}'>{row['Contract Address'][:8]}...</a>\n\n"
    message += "نکته: روی Contract کلیک کن تا در Solscan باز بشه."
else:
    message += "هیچ توکنی با شرایط مطابقت ندارد.\n\nنمونه top 5 by volume:\n"
    sample = df.nlargest(5, '24h Vol (USD)')
    for _, row in sample.iterrows():
        contract_link = f"https://solscan.io/token/{row['Contract Address']}"
        message += f"<b>{row['Name']}</b> ({row['Symbol']}) | Vol: ${row['24h Vol (USD)']:,.0f}\nContract: <a href='{contract_link}'>{row['Contract Address'][:8]}...</a>\n\n"

# ارسال
send_to_telegram(message, output_buffer if len(filtered_df) > 0 else None)

print("گزارش به تلگرام فرستاده شد!")
