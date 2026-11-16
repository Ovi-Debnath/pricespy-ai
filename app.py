import os
import json
import time
import base64
import re
import hmac
import html as html_lib
import smtplib
from email.mime.text import MIMEText
from urllib.parse import urlparse
from datetime import datetime, timedelta
import streamlit as st
import requests
import cloudscraper
from bs4 import BeautifulSoup
from google import genai

# Page Config
st.set_page_config(
    page_title="PriceSpy AI",
    page_icon="🕵️‍♂️",
    layout="centered"
)

st.title("🕵️‍♂️ PriceSpy AI: Automated B2B Competitor Price Monitor")
st.markdown("Monitor your competitors 24/7 and get AI-driven counter-strategy alerts instantly.")

# 1. API & GitHub & Email Configuration
api_key = st.secrets.get("GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY")
github_token = st.secrets.get("GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")
github_repo = st.secrets.get("GITHUB_REPO") or os.environ.get("GITHUB_REPO")
sender_email = st.secrets.get("SENDER_EMAIL") or os.environ.get("SENDER_EMAIL")
sender_password = st.secrets.get("SENDER_PASSWORD") or os.environ.get("SENDER_PASSWORD")
admin_password = st.secrets.get("ADMIN_PASSWORD") or os.environ.get("ADMIN_PASSWORD")
payment_number = st.secrets.get("PAYMENT_NUMBER") or os.environ.get("PAYMENT_NUMBER")

if not api_key:
    st.error("⚠️ GEMINI_API_KEY is missing! Please configure it in Streamlit Secrets.")
    st.stop()

client = genai.Client(api_key=api_key)


# =====================================================================
# DARAZ URL CLEANER
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


def clean_daraz_url(url):
    if not url:
        return url
    url = str(url).strip().strip("'").strip('"').strip()

    if "daraz.com.bd" not in url:
        return url

    try:
        path = urlparse(url).path
    except Exception:
        return url
    filename = path.rsplit("/", 1)[-1]
    m = re.search(r'-i(\d+)(?:-s(\d+))?\.html', filename, flags=re.IGNORECASE)
    if not m:
        return url

    item_id, sku_id = m.group(1), m.group(2)
    slug = filename[:m.start()].strip("-") or "item"
    tail = f"-i{item_id}" + (f"-s{sku_id}" if sku_id else "")
    return f"https://www.daraz.com.bd/products/{slug}{tail}.html"


# =====================================================================
# PRICE HELPERS
# =====================================================================
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


# =====================================================================
# FETCHERS
# =====================================================================
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.daraz.com.bd/",
    "Cache-Control": "no-cache",
}


def _fetch_direct(url):
    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "desktop": True}
    )
    r = scraper.get(url, headers=_HEADERS, timeout=25)
    if r.status_code != 200:
        raise Exception(f"HTTP {r.status_code}")
    return r.text


def _fetch_via_reader_html(url):
    r = requests.get(
        "https://r.jina.ai/" + url,
        headers={
            "Accept": "text/plain",
            "X-Return-Format": "html",
            "X-No-Cache": "true",
        },
        timeout=60,
    )
    if r.status_code != 200:
        raise Exception(f"HTTP {r.status_code}")
    return r.text


_NOISE_WORDS = ("off", "voucher", "save", "shipping", "delivery", "min.",
                "spend", "discount", "cashback", "coupon")


_PRICE_LINE_RE = re.compile(
    r'^\s*(?:৳|Tk\.?|BDT)\s*(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*$',
    flags=re.IGNORECASE
)


def _price_from_markdown(text):
    hm = re.search(r'^# .+$', text, flags=re.MULTILINE)
    area = text[hm.end():] if hm else text
    lines = area.splitlines()

    for line in lines:
        m = _PRICE_LINE_RE.match(line)
        if m:
            v = _to_price(m.group(1))
            if v:
                return v

    for line in lines:
        low = line.lower()
        if '%' in line or any(w in low for w in _NOISE_WORDS):
            continue
        ps = _currency_prices(line)
        if ps:
            return ps[0]
    return None


def _fetch_via_reader_markdown(url):
    r = requests.get(
        "https://r.jina.ai/" + url,
        headers={"Accept": "text/plain", "X-Return-Format": "markdown",
                 "X-No-Cache": "true"},
        timeout=60,
    )
    if r.status_code != 200:
        raise Exception(f"HTTP {r.status_code}")
    text = r.text
    title = None
    tm = re.search(r'^Title:\s*(.+)$', text, flags=re.MULTILINE)
    if tm:
        title = re.sub(r'\s*\|\s*Daraz\.com\.bd\s*$', '', tm.group(1)).strip()
    return title, _price_from_markdown(text)


# =====================================================================
# MAIN SCRAPER
# =====================================================================
def get_competitor_data(url):
    try:
        clean_url = clean_daraz_url(url)
        _, sku_id = parse_daraz_ids(clean_url)

        title = None
        price = None
        errors = []
        json_htmls = []

        try:
            html_text = _fetch_direct(clean_url)
            if _looks_blocked(html_text):
                errors.append("direct: security block page")
            else:
                title = _parse_title(html_text) or title
                price = _visible_price_from_html(html_text)
                if price:
                    return (title or "E-commerce Product"), float(price)
                json_htmls.append(html_text)
                errors.append("direct: visible price not found")
        except Exception as e:
            errors.append(f"direct: {e}")

        try:
            r_title, r_price = _fetch_via_reader_markdown(clean_url)
            title = title or r_title
            if r_price:
                return (title or "E-commerce Product"), float(r_price)
            errors.append("reader-markdown: price not found")
        except Exception as e:
            errors.append(f"reader-markdown: {e}")

        try:
            html_text = _fetch_via_reader_html(clean_url)
            if _looks_blocked(html_text):
                errors.append("reader-html: security block page")
            else:
                title = title or _parse_title(html_text)
                price = _visible_price_from_html(html_text)
                if price:
                    return (title or "E-commerce Product"), float(price)
                json_htmls.append(html_text)
                errors.append("reader-html: visible price not found")
        except Exception as e:
            errors.append(f"reader-html: {e}")

        for h in json_htmls:
            price = _structured_price_from_html(h, sku_id)
            if price:
                return (title or "E-commerce Product"), float(price)

        if price is None or price <= 0:
            price = 0.0

        return (title or "E-commerce Product"), float(price)

    except Exception as e:
        return "E-commerce Product", 0.0


# 3. AI Strategy Generator
def get_ai_counter_strategy(product_name, baseline_price, current_price):
    prompt = (
        f"You are an expert e-commerce pricing strategist. "
        f"Our baseline price for '{product_name}' is {baseline_price:.2f}, "
        f"but the competitor just changed their price to {current_price:.2f}. "
        f"Provide 3 concise, highly actionable bullet points on how our store should respond immediately "
        f"to protect our profit margins and market share."
    )

    available_models = []
    try:
        for m in client.models.list():
            name = m.name.replace("models/", "")
            if (
                "gemini" in name
                and "flash" in name
                and "image" not in name
                and "audio" not in name
                and "tts" not in name
                and "generateContent" in m.supported_actions
            ):
                available_models.append(name)
    except Exception:
        pass

    if not available_models:
        available_models = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]

    for model_name in available_models:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt
            )
            if response and response.text:
                return response.text
        except Exception:
            time.sleep(0.5)
            continue

    return "• Review pricing manually.\n• Monitor competitor stock levels.\n• Maintain value proposition."


# 4. Helper function to load clients
def load_clients_data():
    if github_token and github_repo:
        api_url = f"https://api.github.com/repos/{github_repo}/contents/clients.json"
        headers = {
            "Authorization": f"Bearer {github_token}",
            "Accept": "application/vnd.github+json"
        }
        try:
            res = requests.get(api_url, headers=headers, timeout=10)
            if res.status_code == 200:
                file_data = res.json()
                decoded_content = base64.b64decode(file_data["content"]).decode("utf-8")
                return json.loads(decoded_content)
        except Exception:
            pass

    file_path = "clients.json"
    if os.path.exists(file_path):
        with open(file_path, "r") as f:
            return json.load(f)
    return []


# 5. Helper function to save clients to GitHub & local
def update_github_clients_file(clients_list, commit_message="Update clients.json"):
    updated_content = json.dumps(clients_list, indent=4)

    with open("clients.json", "w") as f:
        f.write(updated_content)

    if github_token and github_repo:
        api_url = f"https://api.github.com/repos/{github_repo}/contents/clients.json"
        headers = {
            "Authorization": f"Bearer {github_token}",
            "Accept": "application/vnd.github+json"
        }
        try:
            res = requests.get(api_url, headers=headers, timeout=10)
            sha = res.json().get("sha") if res.status_code == 200 else None

            encoded_content = base64.b64encode(updated_content.encode("utf-8")).decode("utf-8")
            payload = {
                "message": commit_message,
                "content": encoded_content
            }
            if sha:
                payload["sha"] = sha

            put_res = requests.put(api_url, headers=headers, json=payload, timeout=10)
            if put_res.status_code in [200, 201]:
                return True
            else:
                return f"GitHub sync error: {put_res.json().get('message', 'Unknown error')}"
        except Exception as e:
            return str(e)
    return True


# 6. Function to send Activation Email
def send_activation_email(to_email, verified_items_list):
    clean_email = to_email.split(" [")[0].strip()
    if not sender_email or not sender_password:
        return False, "Sender email or password credentials missing in secrets."

    formatted_items = []
    for item in verified_items_list:
        formatted_items.append(
            f"• Product: {item.get('title', 'N/A')}\n"
            f"  URL: {item.get('url')}\n"
            f"  Your Baseline Price: ৳{item.get('baseline_price')}\n"
            f"  Verified Live Price: ৳{item.get('live_price')}\n"
        )

    items_str = "\n".join(formatted_items)

    subject = "🚀 Your PriceSpy AI Monitoring is Now Active & Verified!"
    body = (
        f"Hello,\n\n"
        f"Your payment has been successfully verified, and our system has successfully tested your product links. Your 24/7 competitor price monitoring bot is now LIVE!\n\n"
        f"Tracked Products & Verified Status:\n{items_str}\n"
        f"Our bot will check competitor prices regularly and send AI-driven strategic alerts directly to this inbox whenever a price change occurs.\n\n"
        f"Thank you for using PriceSpy AI!"
    )

    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = sender_email
        msg["To"] = clean_email

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender_email, sender_password)
            server.send_message(msg)
        return True, "Email sent successfully"
    except Exception as e:
        return False, str(e)


# 7. Function to send Notifications
def send_notification_email(to_email, subject, body):
    clean_email = to_email.split(" [")[0].strip()
    if not sender_email or not sender_password:
        return False, "Sender email credentials missing."
    
    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = sender_email
        msg["To"] = clean_email

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender_email, sender_password)
            server.send_message(msg)
        return True, "Notification sent"
    except Exception as e:
        return False, str(e)


# Save new client function with custom product name support
def save_client_to_github(email, password, product_inputs, plan_name, is_local=False, trx_id=""):
    processed_items = []
    for item in product_inputs:
        raw_url = item.get('url')
        clean_url = clean_daraz_url(raw_url)
        baseline = item.get('baseline_price')
        custom_name = item.get('product_name') # ক্লায়েন্টের দেওয়া কাস্টম নাম
        
        # যদি ক্লায়েন্ট কাস্টম নাম না দেয়, তবে স্ক্র্যাপ করে নাম বের করবে
        if not custom_name or not custom_name.strip():
            p_title, l_price = get_competitor_data(clean_url)
        else:
            p_title = custom_name.strip()
            _, l_price = get_competitor_data(clean_url)

        processed_items.append({
            "url": clean_url,
            "product_name": p_title,
            "live_price": l_price,
            "baseline_price": baseline
        })

    created_date = datetime.now().strftime("%Y-%m-%d")
    expiry_date = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")

    clients = load_clients_data()
    
    existing_client = None
    for c in clients:
        if c['client_email'].split(" [")[0].strip().lower() == email.strip().lower():
            existing_client = c
            break

    if existing_client:
        existing_client['password'] = password
        existing_client['products'].extend(processed_items)
        existing_client['plan'] = plan_name
        existing_client['expiry_date'] = expiry_date
        existing_client['approved'] = not is_local
        existing_client['trx_id'] = trx_id
        existing_client['reminder_sent'] = False
        existing_client['expired_notified'] = False
    else:
        new_entry = {
            "client_email": email,
            "password": password,
            "products": processed_items,
            "pending_products": [],
            "plan": plan_name,
            "approved": not is_local,
            "trx_id": trx_id,
            "created_at": created_date,
            "expiry_date": expiry_date,
            "reminder_sent": False,
            "expired_notified": False
        }
        clients.append(new_entry)

    res = update_github_clients_file(clients, commit_message=f"Add/Update client monitoring for {email}")
    return res


# --- UI TABS ---
tab1, tab2, tab4, tab3 = st.tabs(["🔍 Instant Price Check", "⚙️ 24/7 Client Onboarding", "👤 Client Portal", "🔒 Admin Dashboard"])

with tab1:
    st.subheader("Run Instant Competitor Analysis")
    target_url = st.text_input(
        "Competitor Product URL",
        value="",
        placeholder="https://www.daraz.com.bd/products/your-product-link..."
    )
    user_baseline = st.number_input("Your Store's Baseline Price (BDT/USD)", min_value=0.0, value=0.0, step=10.0)

    if st.button("Check Price & Get AI Strategy", type="primary"):
        if not target_url:
            st.warning("Please enter a competitor product URL.")
        else:
            with st.spinner("Scraping competitor and consulting Gemini AI..."):
                try:
                    title, live_price = get_competitor_data(target_url)

                    col1, col2 = st.columns(2)
                    col1.metric("Product Title", title)
                    col2.metric("Competitor Live Price", f"{live_price:,.2f}", delta=f"{live_price - user_baseline:.2f} vs Baseline" if user_baseline > 0 else "Live Price")

                    st.markdown("---")
                    st.subheader("🤖 AI Counter-Strategy")
                    strategy = get_ai_counter_strategy(title, user_baseline if user_baseline > 0 else live_price, live_price)
                    st.write(strategy)

                except Exception as err:
                    st.error(f"Error: {err}")

with tab2:
    st.subheader("🚀 Register & Activate 24/7 Monitoring")
    st.markdown("Choose your region and package to complete subscription and activate automated tracking.")

    market = st.radio("Select Your Market", ["Global (International)", "Bangladesh (Local)"], horizontal=True)

    if market == "Global (International)":
        st.markdown("### 🌍 Global Plan ($49/month)")
        st.markdown("Secured via Lemon Squeezy (Accepts Credit Cards, PayPal, Apple Pay worldwide).")

        lemonsqueezy_url = "https://yourstore.lemonsqueezy.com/buy/your-product-id"
        st.markdown(f"""
            <a href="{lemonsqueezy_url}" target="_blank">
                <button style="background-color: #7c3aed; color: white; padding: 10px 20px; border: none; border-radius: 6px; font-size: 15px; font-weight: bold; cursor: pointer;">
                    Pay $49/mo via Lemon Squeezy 💳
                </button>
            </a>
        """, unsafe_allow_html=True)

        st.markdown("---")
        with st.form("global_client_form"):
            email = st.text_input("Your Email (used in checkout)")
            password = st.text_input("Create Password (for client portal login)", type="password")
            p_name_input = st.text_input("Custom Product Name (Optional)", placeholder="e.g., Wireless Earbuds")
            url = st.text_input("Competitor URL to Track", placeholder="Paste competitor product URL here...")
            baseline = st.number_input("Your Baseline Price ($ USD)", min_value=0.0, value=0.0, step=1.0)

            if st.form_submit_button("Activate Global Bot", use_container_width=True):
                if email and password and url:
                    with st.spinner("Activating 24/7 monitoring..."):
                        product_items = [{"url": url.strip(), "product_name": p_name_input.strip(), "baseline_price": float(baseline)}]
                        res = save_client_to_github(email, password, product_items, plan_name="Global Plan", is_local=False)
                        if res is True:
                            st.success(f"✅ Global monitoring active for {email}!")
                        else:
                            st.error(f"Error: {res}")
                else:
                    st.warning("Please fill in all required fields including password.")

    else:
        st.markdown("### 🇧🇩 Bangladesh Custom Package (৳1,250 per Link - 30 Days Validity)")
        
        max_links = st.number_input(
            "How many product links do you want to monitor?", 
            min_value=1, 
            max_value=100, 
            value=1, 
            step=1
        )
        
        total_cost = max_links * 1250
        plan_name = f"Custom Package ({max_links} URLs - 30 Days)"
        
        st.info(f"📌 Total Links: **{max_links}** | Total Price: **৳{total_cost:,}** (৳1,250/link for 30 days)")

        if payment_number:
            if st.button("📲 Show Payment Number"):
                st.session_state["show_pay_number"] = True
            if st.session_state.get("show_pay_number"):
                st.info(f"📌 Send Money to: **{payment_number}** (bKash / Nagad / Upay / Rocket Personal)")
        else:
            st.warning("Payment number is not configured yet. Please contact support.")

        with st.form("local_client_form"):
            local_email = st.text_input("Your Email (for receiving alerts)")
            local_pass = st.text_input("Create Password (for client portal login)", type="password")
            st.markdown(f"**Enter Product Names, Competitor URLs & Baseline Prices (Up to {max_links} products):**")

            product_inputs = []
            for idx in range(int(max_links)):
                st.markdown(f"**Product #{idx+1}**")
                name_val = st.text_input(f"Custom Product Name #{idx+1}", key=f"name_{max_links}_{idx}", placeholder="e.g., Summer T-Shirt")
                c1, c2 = st.columns([3, 1])
                u_val = c1.text_input(
                    f"Product #{idx+1} URL",
                    key=f"url_{max_links}_{idx}",
                    placeholder="https://www.daraz.com.bd/products/..."
                )
                p_val = c2.number_input(
                    f"Baseline (৳)",
                    min_value=0.0,
                    value=0.0,
                    step=50.0,
                    key=f"price_{max_links}_{idx}"
                )
                product_inputs.append((name_val, u_val, p_val))

            trx_id = st.text_input("bKash / Nagad Transaction ID (TrxID)")

            if st.form_submit_button("Submit Payment & Activate Bot", use_container_width=True):
                valid_products = [
                    {"product_name": n.strip(), "url": u.strip(), "baseline_price": float(p)} 
                    for n, u, p in product_inputs if u.strip()
                ]

                if local_email and local_pass and len(valid_products) > 0 and trx_id:
                    with st.spinner("Processing products and submitting payment..."):
                        formatted_email = f"{local_email} [Paid Trx: {trx_id}]"
                        res = save_client_to_github(
                            formatted_email,
                            local_pass,
                            valid_products,
                            plan_name=plan_name,
                            is_local=True,
                            trx_id=trx_id
                        )
                        if res is True:
                            st.success(f"✅ Payment submitted with {len(valid_products)} product(s)! Your monitoring bot will be activated shortly.")
                        else:
                            st.error(f"Error: {res}")
                else:
                    st.warning("Please enter your email, password, at least 1 product URL, and your Transaction ID.")

with tab4:
    st.subheader("👤 Client Portal: Secure Login, Update, Delete & Add URLs")
    st.markdown("আপনার রেজিস্টার্ড ইমেইল এবং পাসওয়ার্ড দিয়ে লগইন করে আপনার বর্তমান ট্র্যাক করা প্রোডাক্টগুলোর নাম, ইউআরএল বা বেসলাইন এডিট/ডিলিট করুন অথবা নতুন ইউআরএল পেমেন্টসহ সাবমিট করুন।")

    login_email = st.text_input("Email", placeholder="your-email@example.com", key="login_email_field")
    login_pass = st.text_input("Password", type="password", key="login_pass_field")

    if st.button("Login to My Portal", type="primary"):
        if not login_email or not login_pass:
            st.warning("Please enter both email and password.")
        else:
            clients_data = load_clients_data()
            found_client = None
            client_index = -1

            for idx, c in enumerate(clients_data):
                base_c_email = c['client_email'].split(" [")[0].strip().lower()
                if base_c_email == login_email.strip().lower():
                    stored_pass = c.get("password", "")
                    if stored_pass == login_pass.strip():
                        found_client = c
                        client_index = idx
                    break

            if found_client:
                st.session_state["portal_client"] = found_client
                st.session_state["portal_client_index"] = client_index
                st.success("Login successful!")
            else:
                st.error("Invalid email or password.")

    if "portal_client" in st.session_state and st.session_state["portal_client"]:
        client_obj = st.session_state["portal_client"]
        st.markdown("---")
        st.write(f"### Welcome, `{client_obj['client_email'].split(' [')[0]}`")
        st.write(f"**Subscription Expiry Date:** `{client_obj.get('expiry_date', 'N/A')}`")
        st.write(f"**Status:** `{'Active 🟢' if client_obj.get('approved', True) else 'Pending / Expired 🔴'}`")

        # ১. বর্তমান প্রোডাক্ট ম্যানেজ (Update Custom Name, URL & Baseline) ফর্ম
        st.write("#### 📝 Manage Your Existing Tracked Products (Update / Delete):")
        current_products = client_obj.get("products", [])

        if not current_products:
            st.info("No active products found.")
        else:
            with st.form("update_existing_products_form"):
                updated_products_list = []
                for p_idx, prod in enumerate(current_products):
                    st.markdown(f"**Product #{p_idx+1}** (Live Price: ৳{prod.get('live_price', 0.0):,.2f})")
                    
                    new_name = st.text_input(f"Product Name #{p_idx+1}", value=prod.get("product_name", ""), key=f"portal_edit_name_{p_idx}")
                    col_u, col_p, col_d = st.columns([3, 1, 1])
                    new_u = col_u.text_input(f"URL #{p_idx+1}", value=prod.get("url", ""), key=f"portal_edit_url_{p_idx}")
                    new_b = col_p.number_input(f"Baseline #{p_idx+1}", value=float(prod.get("baseline_price", 0.0)), step=50.0, key=f"portal_edit_base_{p_idx}")
                    keep_it = col_d.checkbox("Keep", value=True, key=f"portal_edit_keep_{p_idx}")

                    if keep_it and new_u.strip():
                        l_pr = prod.get('live_price', 0.0)
                        if new_u.strip() != prod.get("url"):
                            try:
                                _, l_pr = get_competitor_data(new_u.strip())
                            except Exception:
                                pass

                        updated_products_list.append({
                            "url": clean_daraz_url(new_u.strip()),
                            "product_name": new_name.strip() if new_name.strip() else prod.get('product_name', 'Product'),
                            "live_price": l_pr,
                            "baseline_price": float(new_b)
                        })

                if st.form_submit_button("Save Changes / Delete Unchecked"):
                    all_clients = load_clients_data()
                    c_idx = st.session_state["portal_client_index"]
                    if c_idx < len(all_clients):
                        all_clients[c_idx]["products"] = updated_products_list
                        res = update_github_clients_file(all_clients, commit_message=f"Client {client_obj['client_email']} updated/deleted products")
                        if res is True:
                            st.success("✅ Products updated successfully!")
                            st.session_state["portal_client"] = all_clients[c_idx]
                            st.rerun()
                        else:
                            st.error(f"Error: {res}")

        # পেন্ডিং প্রোডাক্টস লিস্ট
        pending_list = client_obj.get("pending_products", [])
        if pending_list:
            st.info(f"⏳ You have **{len(pending_list)}** new product(s) pending admin approval.")
            for p_idx, p_prod in enumerate(pending_list):
                st.write(f"- [Pending] **{p_prod.get('product_name')}** (Baseline: ৳{p_prod.get('baseline_price')}, TrxID: {p_prod.get('trx_id')})")

        st.markdown("---")
        st.markdown("### 🇧🇩 Bangladesh Custom Package (৳1,250 per Link - 30 Days Validity)")
        
        # ২. নতুন ইউআরএল ও কাস্টম নাম যোগ করার ফর্ম (Add New URLs)
        new_max_links = st.number_input(
            "How many new product links do you want to add?", 
            min_value=1, 
            max_value=100, 
            value=1, 
            step=1,
            key="portal_new_max_links"
        )
        
        new_total_cost = new_max_links * 1250
        st.info(f"📌 Total New Links: **{new_max_links}** | Total Price to Pay: **৳{new_total_cost:,}** (৳1,250/link)")

        if payment_number:
            if st.button("📲 Show Payment Number", key="portal_show_pay"):
                st.session_state["portal_show_pay_number"] = True
            if st.session_state.get("portal_show_pay_number"):
                st.info(f"📌 Send Money to: **{payment_number}** (bKash / Nagad / Upay / Rocket Personal)")
        else:
            st.warning("Payment number is not configured yet. Please contact support.")

        with st.form("portal_add_new_links_form"):
            st.markdown(f"**Enter Custom Names, New URLs & Baseline Prices (Up to {new_max_links} products):**")

            new_product_inputs = []
            for idx in range(int(new_max_links)):
                st.markdown(f"**New Product #{idx+1}**")
                n_val = st.text_input(f"Custom Product Name #{idx+1}", key=f"portal_name_{new_max_links}_{idx}", placeholder="e.g., Wireless Mouse")
                c1, c2 = st.columns([3, 1])
                u_val = c1.text_input(
                    f"New Product #{idx+1} URL",
                    key=f"portal_url_{new_max_links}_{idx}",
                    placeholder="https://www.daraz.com.bd/products/..."
                )
                p_val = c2.number_input(
                    f"Baseline (৳)",
                    min_value=0.0,
                    value=0.0,
                    step=50.0,
                    key=f"portal_price_{new_max_links}_{idx}"
                )
                new_product_inputs.append((n_val, u_val, p_val))

            new_trx_id = st.text_input("New bKash / Nagad Transaction ID (TrxID) for these links")

            if st.form_submit_button("Submit New URLs for Approval"):
                valid_new_inputs = [
                    (n.strip(), u.strip(), float(p)) for n, u, p in new_product_inputs if u.strip()
                ]

                if len(valid_new_inputs) > 0 and new_trx_id.strip():
                    with st.spinner("Processing products and submitting..."):
                        new_items_to_add = []
                        for n, u, p in valid_new_inputs:
                            clean_u = clean_daraz_url(u)
                            p_title = n if n else "E-commerce Product"
                            l_price = 0.0
                            try:
                                _, l_price = get_competitor_data(clean_u)
                            except Exception:
                                pass

                            new_items_to_add.append({
                                "url": clean_u,
                                "product_name": p_title,
                                "live_price": l_price,
                                "baseline_price": p,
                                "trx_id": new_trx_id.strip()
                            })

                        all_clients = load_clients_data()
                        c_idx = st.session_state["portal_client_index"]
                        
                        if c_idx < len(all_clients):
                            if "pending_products" not in all_clients[c_idx]:
                                all_clients[c_idx]["pending_products"] = []
                            all_clients[c_idx]["pending_products"].extend(new_items_to_add)

                            res = update_github_clients_file(all_clients, commit_message=f"Client {client_obj['client_email']} submitted new URLs for approval")
                            
                            if res is True:
                                st.success(f"✅ Successfully submitted {len(new_items_to_add)} new URL(s)! Waiting for admin approval.")
                                st.session_state["portal_client"] = all_clients[c_idx]
                            else:
                                st.error(f"Error: {res}")
                else:
                    st.warning("Please enter at least 1 product URL and your Transaction ID.")

with tab3:
    st.subheader("🔒 Admin Payment Verification & URL Approval Dashboard")
    st.markdown("পেমেন্ট ভেরিফাই করুন, নতুন সাবমিট করা ইউআরএলগুলোর নাম ও দাম টেস্ট করে অ্যাপ্রুভ করুন।")

    admin_pass = st.text_input("Admin Password", type="password")

    if not admin_pass:
        pass
    elif admin_pass and hmac.compare_digest(admin_pass.encode("utf-8"), str(admin_password).encode("utf-8")):
        st.success("Access Granted!")

        try:
            clients_data = load_clients_data()
            today = datetime.now()
            today_str = today.strftime("%Y-%m-%d")
            
            data_updated = False
            for client_item in clients_data:
                expiry = client_item.get("expiry_date")
                if expiry:
                    expiry_date_obj = datetime.strptime(expiry, "%Y-%m-%d")
                    delta_days = (expiry_date_obj - today).days

                    if 0 <= delta_days <= 3 and not client_item.get("reminder_sent", False):
                        sub = "⚠️ Your PriceSpy AI Subscription is Expiring Soon!"
                        body = (
                            f"Hello,\n\n"
                            f"Your 30-day competitor price monitoring subscription is expiring on {expiry}. "
                            f"Please renew your subscription soon to avoid any interruption in tracking your competitor prices.\n\n"
                            f"Thank you for using PriceSpy AI!"
                        )
                        send_notification_email(client_item['client_email'], sub, body)
                        client_item["reminder_sent"] = True
                        data_updated = True

                    if today_str > expiry:
                        if client_item.get("approved", True):
                            client_item["approved"] = False
                            data_updated = True

                        if not client_item.get("expired_notified", False):
                            sub = "❌ Your PriceSpy AI Subscription Has Expired"
                            body = (
                                f"Hello,\n\n"
                                f"Your PriceSpy AI monitoring subscription has expired on {expiry}. "
                                f"Your automated monitoring bot has been temporarily paused. Please contact support or renew your package to restart tracking.\n\n"
                                f"Thank you!"
                            )
                            send_notification_email(client_item['client_email'], sub, body)
                            client_item["expired_notified"] = True
                            data_updated = True

            if data_updated:
                update_github_clients_file(clients_data, commit_message="Auto-expire subscriptions and send notification emails")

            st.write(f"Total Active/Pending Monitors: **{len(clients_data)}**")

            if len(clients_data) == 0:
                st.info("No clients found.")

            for i, client_item in enumerate(clients_data):
                st.markdown("---")
                
                expiry = client_item.get("expiry_date", "N/A")
                is_expired = expiry != "N/A" and today_str > expiry

                if is_expired:
                    status_badge = "🔴 Expired (Deactivated)"
                elif client_item.get("approved", True):
                    status_badge = "🟢 Approved & Active"
                else:
                    status_badge = "🟡 Pending Payment"

                st.write(f"**Client #{i+1}** — Status: {status_badge}")
                st.write(f"- **Email / Info:** `{client_item['client_email']}`")
                st.write(f"- **Plan:** `{client_item.get('plan', 'Standard')}`")
                st.write(f"- **TrxID:** `{client_item.get('trx_id', 'N/A (Global)')}`")
                st.write(f"- **Created Date:** `{client_item.get('created_at', 'N/A')}` | **Expiry Date:** `{expiry}`")
                
                products = client_item.get('products', [])
                st.write(f"- **Active Tracked Products ({len(products)} links):**")
                for idx, item in enumerate(products):
                    p_name = item.get('product_name', 'Product')
                    p_url = item.get('url', '#')
                    l_pr = item.get('live_price', 0.0)
                    b_pr = item.get('baseline_price', 0.0)
                    st.markdown(f"  {idx+1}. **[{p_name}]({p_url})** — Live: `৳{l_pr:,.2f}` | Baseline: `৳{b_pr:,.2f}`")

                pending_prods = client_item.get('pending_products', [])
                if pending_prods:
                    st.warning(f"⏳ **Pending New Products ({len(pending_prods)} links awaiting approval):**")
                    for p_idx, p_item in enumerate(pending_prods):
                        st.markdown(f"  - [Trx: {p_item.get('trx_id')}] **{p_item.get('product_name')}** — Baseline: `৳{p_item.get('baseline_price')}`")
                    
                    if st.button(f"Approve Pending New URLs for Client #{i+1}", key=f"app_new_{i}"):
                        with st.spinner("Verifying new URLs before approval..."):
                            verified_new = []
                            for p_item in pending_prods:
                                verified_new.append({
                                    "url": p_item.get('url'),
                                    "product_name": p_item.get('product_name'),
                                    "live_price": p_item.get('live_price'),
                                    "baseline_price": p_item.get('baseline_price')
                                })
                            
                            clients_data[i]['products'].extend(verified_new)
                            clients_data[i]['pending_products'] = []
                            res = update_github_clients_file(clients_data, commit_message=f"Admin approved new URLs for {client_item['client_email']}")
                            if res is True:
                                st.success("✅ New URLs approved and added to active monitoring!")
                                st.rerun()
                            else:
                                st.error(f"Error: {res}")

                col_a, col_b = st.columns(2)

                if not client_item.get("approved", True) or is_expired:
                    if col_a.button(f"Approve / Renew #{i+1}", key=f"app_{i}"):
                        with st.spinner("Cleaning URLs, testing live prices and resetting 30-day expiry..."):
                            verified_list = []
                            link_error = None

                            cleaned_products = []
                            for p_item in products:
                                raw_u = p_item.get('url')
                                clean_u = clean_daraz_url(raw_u)
                                b_price = p_item.get('baseline_price')
                                cur_name = p_item.get('product_name')

                                try:
                                    _, l_price = get_competitor_data(clean_u)
                                    verified_list.append({
                                        "url": clean_u,
                                        "title": cur_name,
                                        "baseline_price": b_price,
                                        "live_price": l_price
                                    })
                                    cleaned_products.append({
                                        "url": clean_u,
                                        "product_name": cur_name,
                                        "live_price": l_price,
                                        "baseline_price": b_price
                                    })
                                except Exception as err:
                                    link_error = f"Failed to verify URL ({clean_u}): {err}"
                                    break

                            if link_error:
                                st.error(f"❌ Cannot approve! {link_error}. Please ask the client for a valid product URL.")
                            else:
                                clients_data[i]["approved"] = True
                                clients_data[i]["products"] = cleaned_products
                                clients_data[i]["created_at"] = datetime.now().strftime("%Y-%m-%d")
                                clients_data[i]["expiry_date"] = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
                                clients_data[i]["reminder_sent"] = False
                                clients_data[i]["expired_notified"] = False
                                
                                res = update_github_clients_file(clients_data, commit_message=f"Approve/Renew client {client_item['client_email']}")

                                if res is True:
                                    mail_sent, mail_msg = send_activation_email(client_item['client_email'], verified_list)
                                    if mail_sent:
                                        st.success(f"Client #{i+1} approved/renewed for another 30 days and email sent successfully!")
                                    else:
                                        st.warning(f"Client approved, but email failed: {mail_msg}")
                                    st.rerun()
                                else:
                                    st.error(f"Failed to sync with GitHub: {res}")

                if col_b.button(f"Remove Client #{i+1}", key=f"del_{i}"):
                    removed_client = clients_data.pop(i)
                    res = update_github_clients_file(clients_data, commit_message=f"Remove client {removed_client['client_email']}")
                    if res is True:
                        st.warning("Client removed and changes saved to GitHub!")
                        st.rerun()
                    else:
                        st.error(f"Failed to sync with GitHub: {res}")

        except Exception as e:
            st.error(f"Error loading data: {e}")

    elif admin_pass:
        st.error("Incorrect password!")
