import os
import json
import time
import base64
import re
import hmac
import html as html_lib
import smtplib
from email.mime.text import MIMEText
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

# Helper function: 100% Future-proof Universal Daraz URL Converter
def clean_daraz_url(url):
    if not url:
        return url
    url = str(url).strip().strip("'").strip('"')
    
    # স্পেস বা ট্র্যাকিং প্যারামিটারের ঝামেলা ঠিক করা
    url = url.replace("d_", "d ")
    
    if "daraz.com.bd" in url:
        match = re.search(r'i(\d+)[^\d]*s(\d+)', url)
        if match:
            i_val = match.group(1)
            s_val = match.group(2)
            
            base_query = ""
            if "?" in url:
                base_query = "?" + url.split("?", 1)[1]
            
            return f"https://www.daraz.com.bd/products/-i{i_val}-s{s_val}.html{base_query}"
            
    return url

# Helper function to safely parse and validate price numbers
UNIT_PATTERN = r'(?!\s*(?:ml|ltr|lt|l|gm|g|kg|mg|pcs|pc|cm|mm|inch|oz|watt|w|v|mah|gb|tb|%))'

def extract_valid_price(text, require_currency=False):
    if not text:
        return None
    text = html_lib.unescape(str(text)).replace(',', '')
    if require_currency:
        pattern = r'(?:৳|Tk\.?|BDT)\s*(\d+(?:\.\d{1,2})?)' + UNIT_PATTERN
    else:
        pattern = r'(?<![\w.])(\d+(?:\.\d{1,2})?)(?![\w.])' + UNIT_PATTERN
    matches = re.findall(pattern, text, flags=re.IGNORECASE)
    for m in matches:
        try:
            val = float(m)
            if 5 <= val <= 10000000:
                return val
        except ValueError:
            continue
    return None

def _find_price_in_jsonld(soup):
    def walk(node):
        if isinstance(node, dict):
            offers = node.get("offers")
            if offers:
                for o in (offers if isinstance(offers, list) else [offers]):
                    if isinstance(o, dict):
                        for key in ("price", "lowPrice"):
                            v = extract_valid_price(o.get(key))
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

def _parse_html_for_price(html_text):
    soup = BeautifulSoup(html_text, 'html.parser')
    price_val = _find_price_in_jsonld(soup)

    if not price_val:
        meta_price = (
            soup.find('meta', property='product:price:amount') or
            soup.find('meta', property='og:price:amount') or
            soup.find('meta', itemprop='price')
        )
        if meta_price and meta_price.get('content'):
            price_val = extract_valid_price(meta_price.get('content'))

    if not price_val:
        for tag in soup.find_all(['span', 'div'], class_=True):
            cls = " ".join(tag.get('class', [])).lower()
            if 'deleted' in cls:
                continue
            if 'pdp-price_type_normal' in cls or 'pdp-product-price' in cls:
                price_val = (
                    extract_valid_price(tag.get_text(), require_currency=True)
                    or extract_valid_price(tag.get_text())
                )
                if price_val:
                    break

    if not price_val:
        raw = html_text.replace('\\"', '"').replace('\\u09f3', '৳').replace('\\u002F', '/')
        key_pattern = (
            r'"(?:salePrice|actPrice|minActionPrice|price)"\s*:\s*'
            r'(?:\{[^{}]*?"(?:value|text)"\s*:\s*)?"?(?:৳\s*)?(\d[\d,]*\.?\d*)'
        )
        for m in re.findall(key_pattern, raw):
            val = extract_valid_price(m)
            if val:
                price_val = val
                break

    if not price_val:
        for bad in soup.find_all(['title', 'h1', 'script', 'style', 'noscript']):
            bad.decompose()
        price_val = extract_valid_price(soup.get_text(" "), require_currency=True)

    return price_val

def _fetch_via_reader(clean_url):
    r = requests.get(
        "https://r.jina.ai/" + clean_url,
        headers={"Accept": "text/plain", "X-Return-Format": "markdown"},
        timeout=60
    )
    if r.status_code != 200:
        raise Exception(f"Reader fallback failed (Status Code: {r.status_code})")

    text = r.text
    title = None
    tm = re.search(r'^Title:\s*(.+)$', text, flags=re.MULTILINE)
    if tm:
        title = tm.group(1).replace("| Daraz.com.bd", "").strip()

    hm = re.search(r'^# .+$', text, flags=re.MULTILINE)
    search_area = text[hm.end():] if hm else text
    price = extract_valid_price(search_area, require_currency=True)
    return title, price

def get_competitor_data(url):
    try:
        clean_url = clean_daraz_url(url)
        
        # ফ্ল্যাশ সেল এবং অ্যান্টি-বট বাইপাস করার জন্য উন্নত হেডার্স
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": "https://www.daraz.com.bd/",
            "Cache-Control": "no-cache"
        }

        title = "E-commerce Product"
        price_val = None
        direct_error = None

        # প্রথমে ডিরেক্ট স্ক্রুপিং ট্রাই করবে
        try:
            scraper = cloudscraper.create_scraper()
            response = scraper.get(clean_url, headers=headers, timeout=25)

            if response.status_code != 200:
                raise Exception(f"Blocked (Status: {response.status_code})")

            html_text = response.text
            low = html_text.lower()
            if "punish" in low or "captcha" in low or "x5secdata" in low:
                raise Exception("Captcha / Security block detected")

            soup = BeautifulSoup(html_text, 'html.parser')
            og_title = soup.find('meta', property='og:title')
            if og_title and og_title.get('content'):
                title = og_title.get('content').strip()
            else:
                title_tag = soup.find('h1') or soup.find('title')
                if title_tag:
                    title = title_tag.text.strip()

            price_val = _parse_html_for_price(html_text)
            if not price_val:
                raise Exception("Price element not found in HTML")
        except Exception as e:
            direct_error = str(e)
            price_val = None

        # যদি ডিরেক্ট স্ক্রুপিং ফেইল করে, তবে স্বয়ংক্রিয়ভাবে রিডার ফলব্যাক ব্যবহার করবে (ফ্ল্যাশ সেলের জন্য শতভাগ কার্যকর)
        if not price_val:
            try:
                r_title, r_price = _fetch_via_reader(clean_url)
                if r_price:
                    price_val = r_price
                    if r_title:
                        title = r_title
            except Exception as e2:
                raise Exception(f"Direct: {direct_error} | Fallback: {e2}")

        if not price_val:
            raise Exception(f"Could not extract price. Direct error: {direct_error}")

        return title, float(price_val)

    except Exception as e:
        raise Exception(f"Failed to scrape URL. Details: {e}")

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

# Save new client function with auto URL cleaning
def save_client_to_github(email, product_items, plan_name, is_local=False, trx_id=""):
    cleaned_items = []
    for item in product_items:
        cleaned_url = clean_daraz_url(item.get('url'))
        cleaned_items.append({
            "url": cleaned_url,
            "baseline_price": item.get('baseline_price')
        })

    clients = load_clients_data()
    new_entry = {
        "client_email": email,
        "products": cleaned_items,
        "plan": plan_name,
        "approved": not is_local,
        "trx_id": trx_id
    }
    clients.append(new_entry)
    res = update_github_clients_file(clients, commit_message=f"Add client monitoring for {email}")
    return res

# --- UI TABS ---
tab1, tab2, tab3 = st.tabs(["🔍 Instant Price Check", "⚙️ 24/7 Client Onboarding", "🔒 Admin Dashboard"])

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
                    col2.metric("Competitor Live Price", f"{live_price:.2f}", delta=f"{live_price - user_baseline:.2f} vs Baseline" if user_baseline > 0 else "Live Price")
                    
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
            url = st.text_input("Competitor URL to Track", placeholder="Paste competitor product URL here...")
            baseline = st.number_input("Your Baseline Price ($ USD)", min_value=0.0, value=0.0, step=1.0)
            
            if st.form_submit_button("Activate Global Bot", use_container_width=True):
                if email and url:
                    with st.spinner("Activating 24/7 monitoring..."):
                        product_items = [{"url": url.strip(), "baseline_price": float(baseline)}]
                        res = save_client_to_github(email, product_items, plan_name="Global Plan", is_local=False)
                        if res is True:
                            st.success(f"✅ Global monitoring active for {email}!")
                        else:
                            st.error(f"Error: {res}")
                else:
                    st.warning("Please fill in all fields.")

    else:
        st.markdown("### 🇧🇩 Bangladesh Local Packages")
        
        local_plan = st.selectbox(
            "Select Your Package", 
            [
                "Single Product Starter (৳1,000/mo — 1 URL)",
                "Growth Package (৳2,500/mo — Up to 5 Products)", 
                "Pro Package (৳5,000/mo — Up to 15 Products)"
            ]
        )
        
        if "Single Product" in local_plan:
            max_links = 1
            plan_name = "Single Product (1 URL)"
        elif "Growth" in local_plan:
            max_links = 5
            plan_name = "Growth (5 URLs)"
        else:
            max_links = 15
            plan_name = "Pro (15 URLs)"

        if payment_number:
            if st.button("📲 Show Payment Number"):
                st.session_state["show_pay_number"] = True
            if st.session_state.get("show_pay_number"):
                st.info(f"📌 Send Money to: **{payment_number}** (bKash / Nagad / Upay / Rocket Personal)")
        else:
            st.warning("Payment number is not configured yet. Please contact support.")
        
        with st.form("local_client_form"):
            local_email = st.text_input("Your Email (for receiving alerts)")
            st.markdown(f"**Enter Competitor URLs & Your Baseline Prices (Up to {max_links} products):**")
            
            product_inputs = []
            for idx in range(max_links):
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
                product_inputs.append((u_val, p_val))
            
            trx_id = st.text_input("bKash / Nagad Transaction ID (TrxID)")
            
            if st.form_submit_button("Submit Payment & Activate Bot", use_container_width=True):
                valid_products = [
                    {"url": u.strip(), "baseline_price": float(p)} 
                    for u, p in product_inputs if u.strip()
                ]
                
                if local_email and len(valid_products) > 0 and trx_id:
                    with st.spinner("Submitting payment and activating bot..."):
                        formatted_email = f"{local_email} [Paid Trx: {trx_id}]"
                        res = save_client_to_github(
                            formatted_email, 
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
                    st.warning("Please enter your email, at least 1 product URL, and your Transaction ID.")

with tab3:
    st.subheader("🔒 Admin Payment Verification Dashboard")
    st.markdown("Admin password দিয়ে পেমেন্ট ভেরিফাই করুন এবং লিংক টেস্ট করে অ্যাক্টিভ করুন।")
    
    admin_pass = st.text_input("Admin Password", type="password")
    
    if not admin_password:
        st.error("⚠️ ADMIN_PASSWORD is not configured! Please add it in Streamlit Secrets.")
    elif admin_pass and hmac.compare_digest(admin_pass.encode("utf-8"), str(admin_password).encode("utf-8")):
        st.success("Access Granted!")
        
        try:
            clients_data = load_clients_data()
            st.write(f"Total Active/Pending Monitors: **{len(clients_data)}**")
            
            if len(clients_data) == 0:
                st.info("No clients found.")
            
            for i, client in enumerate(clients_data):
                st.markdown("---")
                status_badge = "🟢 Approved" if client.get("approved", True) else "🟡 Pending Payment"
                st.write(f"**Client #{i+1}** — Status: {status_badge}")
                st.write(f"- **Email / Info:** `{client['client_email']}`")
                st.write(f"- **Plan:** `{client.get('plan', 'Standard')}`")
                st.write(f"- **TrxID:** `{client.get('trx_id', 'N/A (Global)')}`")
                st.write(f"- **Tracked Products:**")
                
                products = client.get('products', [])
                for idx, item in enumerate(products):
                    st.write(f"  {idx+1}. {item.get('url')} — **Baseline:** `৳{item.get('baseline_price')}`")
                
                col_a, col_b = st.columns(2)
                
                if not client.get("approved", True):
                    if col_a.button(f"Approve Payment #{i+1}", key=f"app_{i}"):
                        with st.spinner("Cleaning URLs, testing live prices before approval..."):
                            verified_list = []
                            link_error = None
                            
                            cleaned_products = []
                            for p_item in products:
                                raw_u = p_item.get('url')
                                clean_u = clean_daraz_url(raw_u)
                                b_price = p_item.get('baseline_price')
                                
                                try:
                                    p_title, l_price = get_competitor_data(clean_u)
                                    verified_list.append({
                                        "url": clean_u,
                                        "title": p_title,
                                        "baseline_price": b_price,
                                        "live_price": l_price
                                    })
                                    cleaned_products.append({
                                        "url": clean_u,
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
                                res = update_github_clients_file(clients_data, commit_message=f"Approve client {client['client_email']}")
                                
                                if res is True:
                                    mail_sent, mail_msg = send_activation_email(client['client_email'], verified_list)
                                    if mail_sent:
                                        st.success(f"Client #{i+1} approved! URLs auto-cleaned, tested, and activation email sent successfully!")
                                    else:
                                        st.warning(f"Client approved and saved, but email failed: {mail_msg}")
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
