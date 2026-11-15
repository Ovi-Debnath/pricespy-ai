import os
import json
import time
import base64
import re
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

if not api_key:
    st.error("⚠️ GEMINI_API_KEY is missing! Please configure it in Streamlit Secrets.")
    st.stop()

client = genai.Client(api_key=api_key)

# Helper function to safely parse and validate price numbers
def extract_valid_price(text):
    if not text:
        return None
    text = str(text).replace(',', '')
    matches = re.findall(r'\b\d+(?:\.\d{1,2})?\b', text)
    for m in matches:
        try:
            val = float(m)
            if 5 <= val <= 10000000:
                return val
        except ValueError:
            continue
    return None

# 2. Advanced Daraz & General E-commerce Scraper with API Fallback & JSON Unquoted Parser
def get_competitor_data(url):
    try:
        clean_url = str(url).strip().strip("'").strip('"')
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": "https://www.daraz.com.bd/"
        }
        
        scraper = cloudscraper.create_scraper()
        response = scraper.get(clean_url, headers=headers, timeout=25)
        
        if response.status_code != 200:
            raise Exception(f"Page blocked or not found (Status Code: {response.status_code})")
            
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # প্রোডাক্ট টাইটেল খোঁজা
        title = "E-commerce Product"
        og_title = soup.find('meta', property='og:title')
        if og_title and og_title.get('content'):
            title = og_title.get('content').strip()
        else:
            title_tag = soup.find('h1') or soup.find('title')
            if title_tag:
                title = title_tag.text.strip()
        
        # প্রাইস এক্সট্রাকশন
        price_val = None
        
        # মেটা ট্যাগ চেক করা
        meta_price = (
            soup.find('meta', property='product:price:amount') or 
            soup.find('meta', property='og:price:amount') or 
            soup.find('meta', itemprop='price')
        )
        if meta_price and meta_price.get('content'):
            price_val = extract_valid_price(meta_price.get('content'))
            
        # স্ক্রিপ্ট ট্যাগ থেকে JSON ডেটা খোঁজা (কোটেড ও আনকোটেড সাপোর্ট)
        if not price_val:
            for script in soup.find_all('script'):
                script_text = script.string or ""
                if any(kw in script_text for kw in ["price", "salePrice", "actPrice", "originPrice", "minActionPrice"]):
                    matches = re.findall(r'"(?:price|salePrice|actPrice|originPrice|minActionPrice|originalPrice)"\s*:\s*"?([0-9\.]+)"?', script_text)
                    if matches:
                        for m in matches:
                            val = extract_valid_price(m)
                            if val and val > 5:
                                price_val = val
                                break
                if price_val:
                    break
                    
        # ক্লাস বা সাধারণ ট্যাগ থেকে খোঁজা
        if not price_val:
            possible_tags = soup.find_all(['span', 'div', 'p'], class_=lambda value: value and any(x in value.lower() for x in ['price', 'pdp-price', 'notranslate', 'pdp-product-price']))
            for tag in possible_tags:
                price_val = extract_valid_price(tag.get_text())
                if price_val:
                    break
                    
        # ফুল পেজ টেক্সট থেকে কারেন্সি প্যাটার্ন খোঁজা
        if not price_val:
            text_matches = re.findall(r'(?:৳|BDT|Tk)?\s*([0-9]{2,6}(?:\.\d{1,2})?)', soup.get_text())
            for tm in text_matches:
                val = extract_valid_price(tm)
                if val and 20 <= val <= 500000:
                    price_val = val
                    break
                    
        if not price_val:
            raise Exception("Could not locate a valid price element. Daraz security blocked direct extraction for this specific product.")
            
        return title, float(price_val)
        
    except Exception as e:
        raise Exception(f"Failed to scrape URL. Details: {e}")

# 3. Robust AI Model Switcher & Strategy Generator
def get_ai_counter_strategy(product_name, baseline_usd, current_usd):
    prompt = (
        f"You are an expert e-commerce pricing strategist. "
        f"Our baseline price for '{product_name}' is ${baseline_usd:.2f}, "
        f"but the competitor just changed their price to ${current_usd:.2f}. "
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
def send_activation_email(to_email, product_url):
    clean_email = to_email.split(" [")[0].strip()
    if not sender_email or not sender_password:
        return False, "Sender email or password credentials missing in secrets."
    
    subject = "🚀 Your PriceSpy AI Monitoring is Now Active!"
    body = (
        f"Hello,\n\n"
        f"Your payment has been successfully verified and your 24/7 competitor price monitoring bot is now LIVE!\n"
        f"Tracked URL: {product_url}\n\n"
        f"Our bot will check the competitor price every 6 hours and send AI-driven strategic alerts directly to this inbox whenever a price change occurs.\n\n"
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

# Save new client function
def save_client_to_github(email, product_url, baseline, is_local=False, trx_id=""):
    clients = load_clients_data()
    new_entry = {
        "client_email": email,
        "url": product_url,
        "baseline_price": float(baseline),
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
        value="https://www.daraz.com.bd/products/rfl-ss-650ml-623363-1-i428888927-s2083130727.html"
    )
    user_baseline = st.number_input("Your Store's Baseline Price (BDT/USD)", min_value=0.0, value=450.0, step=10.0)
    
    if st.button("Check Price & Get AI Strategy", type="primary"):
        with st.spinner("Scraping competitor and consulting Gemini AI..."):
            try:
                title, live_price = get_competitor_data(target_url)
                
                col1, col2 = st.columns(2)
                col1.metric("Product Title", title)
                col2.metric("Competitor Live Price", f"{live_price:.2f}", delta=f"{live_price - user_baseline:.2f} vs Baseline")
                
                st.markdown("---")
                st.subheader("🤖 AI Counter-Strategy")
                strategy = get_ai_counter_strategy(title, user_baseline, live_price)
                st.write(strategy)
                
            except Exception as err:
                st.error(f"Error: {err}")

with tab2:
    st.subheader("🚀 Register & Activate 24/7 Monitoring")
    st.markdown("Choose your region to complete subscription and activate automated tracking.")
    
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
            url = st.text_input("Competitor URL to Track")
            baseline = st.number_input("Your Baseline Price ($ USD)", min_value=0.0, value=55.00)
            
            if st.form_submit_button("Activate Global Bot", use_container_width=True):
                if email and url:
                    with st.spinner("Activating 24/7 monitoring..."):
                        res = save_client_to_github(email, url, baseline, is_local=False)
                        if res is True:
                            st.success(f"✅ Global monitoring active for {email}!")
                        else:
                            st.error(f"Error: {res}")
                else:
                    st.warning("Please fill in all fields.")

    else:
        st.markdown("### 🇧🇩 Bangladesh Local Plan (৳2,500 BDT/month)")
        st.markdown("Send payment via bKash or Nagad and enter your Transaction ID below to activate.")
        
        st.info("📌 Send Money to: **01735943876** (bKash / Nagad / Upay / Rocket Personal)")
        
        with st.form("local_client_form"):
            local_email = st.text_input("Your Email (for receiving alerts)")
            local_url = st.text_input("Competitor Product URL to Track")
            local_baseline = st.number_input("Your Baseline Price (BDT ৳)", min_value=0.0, value=5500.0, step=100.0)
            trx_id = st.text_input("bKash / Nagad Transaction ID (TrxID)")
            
            if st.form_submit_button("Submit Payment & Activate Bot", use_container_width=True):
                if local_email and local_url and trx_id:
                    with st.spinner("Submitting payment and activating bot..."):
                        formatted_email = f"{local_email} [Paid Trx: {trx_id}]"
                        res = save_client_to_github(formatted_email, local_url, local_baseline, is_local=True, trx_id=trx_id)
                        if res is True:
                            st.success("✅ Payment submitted! Your 24/7 monitoring bot will be activated shortly after verification.")
                        else:
                            st.error(f"Error: {res}")
                else:
                    st.warning("Please fill in all required fields including the Transaction ID.")

with tab3:
    st.subheader("🔒 Admin Payment Verification Dashboard")
    st.markdown("Enter admin password to review paid local clients and approve/remove monitors.")
    
    admin_pass = st.text_input("Admin Password", type="password")
    
    if admin_pass == "9973":
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
                st.write(f"- **TrxID:** `{client.get('trx_id', 'N/A (Global)')}`")
                st.write(f"- **Tracked URL:** {client['url']}")
                st.write(f"- **Baseline Price:** {client['baseline_price']}")
                
                col_a, col_b = st.columns(2)
                
                # Approve button with instant email notification
                if not client.get("approved", True):
                    if col_a.button(f"Approve Payment #{i+1}", key=f"app_{i}"):
                        clients_data[i]["approved"] = True
                        
                        # Save to GitHub & Local
                        res = update_github_clients_file(clients_data, commit_message=f"Approve client {client['client_email']}")
                        
                        if res is True:
                            # Send Instant Activation Email
                            mail_sent, mail_msg = send_activation_email(client['client_email'], client['url'])
                            if mail_sent:
                                st.success(f"Client #{i+1} approved and activation email sent successfully!")
                            else:
                                st.warning(f"Client approved and saved, but email failed: {mail_msg}")
                            st.rerun()
                        else:
                            st.error(f"Failed to sync with GitHub: {res}")
                
                # Remove button
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
