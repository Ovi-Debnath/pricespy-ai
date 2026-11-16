import os
import json
import time
import base64
import re
import html as html_lib
import smtplib
from email.mime.text import MIMEText
from urllib.parse import urlparse
import requests
import cloudscraper
from bs4 import BeautifulSoup
from google import genai

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
SENDER_EMAIL = os.environ.get("SENDER_EMAIL")
SENDER_PASSWORD = os.environ.get("SENDER_PASSWORD")
GITHUB_TOKEN = os.environ.get("PRICESPY_DATA_TOKEN") or os.environ.get("GITHUB_TOKEN")
GITHUB_REPO = os.environ.get("PRICESPY_DATA_REPO") or os.environ.get("GITHUB_REPO")
CONTACT_GMAIL = os.environ.get("CONTACT_GMAIL") or "ovidebnath63@gmail.com"

if GEMINI_API_KEY:
    client = genai.Client(api_key=GEMINI_API_KEY)
else:
    client = None


# Load clients from private repo
def load_clients():
    if GITHUB_TOKEN and GITHUB_REPO:
        api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/clients.json"
        headers = {
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json"
        }
        try:
            res = requests.get(api_url, headers=headers, timeout=15)
            if res.status_code == 200:
                file_data = res.json()
                decoded_content = base64.b64decode(file_data["content"]).decode("utf-8")
                return json.loads(decoded_content)
            else:
                print(f"GitHub API fetch failed with status {res.status_code}: {res.text}")
        except Exception as e:
            print(f"GitHub API fetch error: {e}")

    file_path = "clients.json"
    if os.path.exists(file_path):
        with open(file_path, "r") as f:
            return json.load(f)
    return []


# Save updated live prices back to private repo
def save_clients_to_github(clients_list):
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return
    api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/clients.json"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json"
    }
    try:
        res = requests.get(api_url, headers=headers, timeout=15)
        sha = res.json().get("sha") if res.status_code == 200 else None
        updated_content = json.dumps(clients_list, indent=4)
        encoded_content = base64.b64encode(updated_content.encode("utf-8")).decode("utf-8")
        payload = {
            "message": "Auto-update live prices from 24/7 bot",
            "content": encoded_content
        }
        if sha:
            payload["sha"] = sha
        requests.put(api_url, headers=headers, json=payload, timeout=15)
    except Exception as e:
        print(f"Error updating clients.json on GitHub: {e}")


# =====================================================================
# FULL DARAZ SCRAPER (EXACT MATCH WITH APP.PY)
# =====================================================================
def parse_daraz_ids(url):
    try:
        path = urlparse(str(url).strip()).path
    except Exception:
        return None, None
    filename = path.rsplit("/", 1)[-1]
    m = re.search(r'-i(\d+)(?:-s(\d+))?\.html', filename, flags=re.IGNORECASE)
    if not m:
        return None, None
    return m.group(1), m.group(2)


def _to_price(v):
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        val = float(v)
    else:
        s = html_lib.unescape(str(v)).replace(",", "")
        m = re.search(r'\d+(?:\.\d+)?', s)
        if not m:
            return None
        val = float(m.group())
    return val if 0 < val < 10_000_000 else None


_CUR_RE = re.compile(
    r'(?<![A-Za-z])(?:৳|Tk\.?|BDT)\s*'
    r'(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?!\d)',
    flags=re.IGNORECASE
)


def _currency_prices(text):
    text = html_lib.unescape(str(text or ""))
    out = []
    for m in _CUR_RE.finditer(text):
        v = _to_price(m.group(1))
        if v:
            out.append(v)
    return out


def _price_from_sku_infos(html_text, sku_id):
    dec = json.JSONDecoder()
    variants = [html_text, html_text.replace('\\"', '"')]
    for src in variants:
        for m in re.finditer(r'"skuInfos"\s*:\s*(?=\{)', src):
            try:
                infos, _ = dec.raw_decode(src, m.end())
            except ValueError:
                continue
            if not isinstance(infos, dict):
                continue

            order = []
            if sku_id and str(sku_id) in infos:
                order.append(str(sku_id))
            if "0" in infos:
                order.append("0")
            order += [k for k in infos if k not in order]

            for k in order:
                info = infos.get(k)
                if not isinstance(info, dict):
                    continue
                price = info.get("price")
                if not isinstance(price, dict):
                    continue
                sale = price.get("salePrice")
                if isinstance(sale, dict):
                    cands = [sale.get("value"), sale.get("text")]
                else:
                    cands = [sale]
                for c in cands:
                    p = _to_price(c)
                    if p:
                        return p
    return None


def _find_price_in_jsonld(soup):
    def walk(node):
        if isinstance(node, dict):
            offers = node.get("offers")
            if offers:
                for o in (offers if isinstance(offers, list) else [offers]):
                    if isinstance(o, dict):
                        for key in ("price", "lowPrice"):
                            v = _to_price(o.get(key))
                            if v:
                                return v
            for v in node.values():
                r = walk(v)
                if r:
                    return r
        elif isinstance(node, list):
            for v in node:
                r = walk(v)
                if r:
                    return r
        return None

    for s in soup.find_all('script', type='application/ld+json'):
        try:
            data = json.loads(s.string or "")
        except Exception:
            continue
        r = walk(data)
        if r:
            return r
    return None


def _visible_price_from_html(html_text):
    soup = BeautifulSoup(html_text, 'html.parser')
    for wanted in ('pdp-price_type_normal', 'pdp-product-price'):
        for tag in soup.find_all(['span', 'div'], class_=True):
            cls = " ".join(tag.get('class', [])).lower()
            if 'deleted' in cls or wanted not in cls:
                continue
            txt = tag.get_text(" ")
            ps = _currency_prices(txt)
            v = ps[0] if ps else _to_price(txt)
            if v:
                return v
    return None


def _structured_price_from_html(html_text, sku_id=None):
    price_val = _price_from_sku_infos(html_text, sku_id)
    if price_val:
        return price_val

    soup = BeautifulSoup(html_text, 'html.parser')
    price_val = _find_price_in_jsonld(soup)
    if price_val:
        return price_val

    meta_price = (
        soup.find('meta', property='product:price:amount') or
        soup.find('meta', property='og:price:amount') or
        soup.find('meta', itemprop='price')
    )
    if meta_price and meta_price.get('content'):
        price_val = _to_price(meta_price.get('content'))
        if price_val:
            return price_val

    raw = (html_text.replace('\\"', '"')
                     .replace('\\u09f3', '৳')
                     .replace('\\u002F', '/'))
    key_pattern = (
        r'"(?:salePrice|actPrice|minActionPrice)"\s*:\s*'
        r'(?:\{[^{}]*?"value"\s*:\s*)?"?(\d[\d,]*\.?\d*)'
    )
    for m in re.findall(key_pattern, raw):
        price_val = _to_price(m)
        if price_val:
            return price_val
    return None


def _parse_title(html_text):
    soup = BeautifulSoup(html_text, 'html.parser')
    og = soup.find('meta', property='og:title')
    t = None
    if og and og.get('content'):
        t = og.get('content').strip()
    else:
        tag = soup.find('h1') or soup.find('title')
        if tag:
            t = tag.get_text(strip=True)
    if t:
        t = re.sub(r'\s*\|\s*Daraz\.com\.bd\s*$', '', t).strip()
    return t or None


def _looks_blocked(html_text):
    low = html_text.lower()
    return any(x in low for x in ("/punish?", "_____tmd_____", "x5secdata"))


_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.daraz.com.bd/",
    "Cache-Control": "no-cache",
}


def _fetch_direct(url):
    scraper = cloudscraper.create_scraper(browser={"browser": "chrome", "platform": "windows", "desktop": True})
    r = scraper.get(url, headers=_HEADERS, timeout=25)
    if r.status_code != 200:
        raise Exception(f"HTTP {r.status_code}")
    return r.text


def _fetch_via_reader_markdown(url):
    r = requests.get(
        "https://r.jina.ai/" + url,
        headers={"Accept": "text/plain", "X-Return-Format": "markdown", "X-No-Cache": "true"},
        timeout=60,
    )
    if r.status_code != 200:
        raise Exception(f"HTTP {r.status_code}")
    text = r.text
    title = None
    tm = re.search(r'^Title:\s*(.+)$', text, flags=re.MULTILINE)
    if tm:
        title = re.sub(r'\s*\|\s*Daraz\.com\.bd\s*$', '', tm.group(1)).strip()

    hm = re.search(r'^# .+$', text, flags=re.MULTILINE)
    area = text[hm.end():] if hm else text
    for line in area.splitlines():
        low = line.lower()
        if '%' in line or any(w in low for w in ("off", "voucher", "save", "shipping", "delivery", "discount", "coupon")):
            continue
        ps = _currency_prices(line)
        if ps:
            return title, ps[0]
    return title, 0.0


def _fetch_via_reader_html(url):
    r = requests.get(
        "https://r.jina.ai/" + url,
        headers={"Accept": "text/plain", "X-Return-Format": "html", "X-No-Cache": "true"},
        timeout=60,
    )
    if r.status_code != 200:
        raise Exception(f"HTTP {r.status_code}")
    return r.text


def get_product_data(url):
    _, sku_id = parse_daraz_ids(url)
    title = None
    json_htmls = []

    try:
        html_text = _fetch_direct(url)
        if not _looks_blocked(html_text):
            title = _parse_title(html_text) or title
            price = _visible_price_from_html(html_text)
            if price and price > 0:
                return (title or "E-commerce Product"), float(price)
            json_htmls.append(html_text)
    except Exception as e:
        print(f"Direct fetch info: {e}")

    try:
        r_title, r_price = _fetch_via_reader_markdown(url)
        title = title or r_title
        if r_price and r_price > 0:
            return (title or "E-commerce Product"), float(r_price)
    except Exception as e:
        print(f"Reader markdown info: {e}")

    try:
        html_text = _fetch_via_reader_html(url)
        if not _looks_blocked(html_text):
            title = title or _parse_title(html_text)
            price = _visible_price_from_html(html_text)
            if price and price > 0:
                return (title or "E-commerce Product"), float(price)
            json_htmls.append(html_text)
    except Exception as e:
        print(f"Reader HTML info: {e}")

    for h in json_htmls:
        price = _structured_price_from_html(h, sku_id)
        if price and price > 0:
            return (title or "E-commerce Product"), float(price)

    return (title or "E-commerce Product"), 0.0


def get_ai_strategy(product_name, old_price, new_price):
    if not client:
        return "• Review pricing manually.\n• Monitor competitor stock levels.\n• Maintain value proposition."
        
    prompt = (
        f"You are an expert e-commerce pricing strategist. "
        f"Our baseline price for '{product_name}' is ৳{old_price:.2f}, "
        f"but the competitor just changed their price to ৳{new_price:.2f}. "
        f"Provide 3 concise, highly actionable bullet points on how our store should respond immediately "
        f"to protect our profit margins and market share."
    )
    
    available_models = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]
    for model_name in available_models:
        try:
            response = client.models.generate_content(model=model_name, contents=prompt)
            if response and response.text:
                return response.text
        except Exception:
            time.sleep(0.5)
            continue
            
    return "• Review pricing manually.\n• Monitor competitor stock levels.\n• Maintain value proposition."


def send_alert_email(to_email, product_name, old_price, new_price, strategy, url):
    clean_to_email = to_email.split(" [")[0].strip()

    subject = f"🚨 Price Alert: {product_name} is now ৳{new_price:,.2f}"
    body = (
        f"Competitor Price Shift Detected!\n\n"
        f"Product: {product_name}\n"
        f"Your Baseline Price: ৳{old_price:,.2f}\n"
        f"New Competitor Price: ৳{new_price:,.2f}\n"
        f"Product Link: {url}\n\n"
        f"--- AI RECOMMENDED COUNTER-STRATEGY ---\n"
        f"{strategy}\n\n"
        f"If you need support, contact us at: {CONTACT_GMAIL}\n\n"
        f"- Automated Alert by PriceSpy AI"
    )
    
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = SENDER_EMAIL
    msg["To"] = clean_to_email

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            server.send_message(msg)
        print(f"Alert email successfully sent to {clean_to_email}!")
        return True
    except Exception as e:
        print(f"Failed to send email to {clean_to_email}: {e}")
        return False


def run_automation():
    clients = load_clients()
    print(f"Loaded {len(clients)} client(s) from storage.")
    any_updated = False
    
    for client_item in clients:
        if not client_item.get("approved", True):
            continue
            
        client_email = client_item.get("client_email", "").split(" [")[0].strip()
        products = client_item.get("products", [])
        
        for item in products:
            try:
                url = item.get("url")
                baseline_price = float(item.get("baseline_price", 0.0))
                saved_name = item.get("product_name", "Product")
                
                title, current_price = get_product_data(url)
                if not title or title == "E-commerce Product":
                    title = saved_name
                    
                old_live = float(item.get("live_price", 0.0))
                print(f"Checking for {client_email} | {title} | Live: ৳{current_price:,.2f} | Stored Live: ৳{old_live:,.2f} | Baseline: ৳{baseline_price:,.2f}")
                
                # যদি লাইভ প্রাইস পাওয়া যায় (> 0) এবং আগের live_price এর চেয়ে আলাদা হয়
                if current_price > 0 and current_price != old_live:
                    strategy = get_ai_strategy(title, baseline_price, current_price)
                    sent = send_alert_email(
                        client_email,
                        title,
                        baseline_price,
                        current_price,
                        strategy,
                        url
                    )
                    if sent:
                        item["live_price"] = current_price
                        any_updated = True
                elif current_price == 0.0:
                    print(f"⚠️ Warning: Scraped price is 0.0 for {url}. Scraper might be blocked or product unavailable.")
            except Exception as e:
                print(f"Error processing product for {client_email}: {e}")

    if any_updated:
        save_clients_to_github(clients)


if __name__ == "__main__":
    run_automation()
