import os
import json
import time
import base64
import streamlit as st
import requests
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

# 1. API & GitHub Configuration
api_key = st.secrets.get("GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY")
github_token = st.secrets.get("GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")
github_repo = st.secrets.get("GITHUB_REPO") or os.environ.get("GITHUB_REPO")

if not api_key:
    st.error("⚠️ GEMINI_API_KEY is missing! Please configure it in Streamlit Secrets.")
    st.stop()

client = genai.Client(api_key=api_key)

# 2. Function to fetch live competitor price
def get_competitor_data(url):
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        response = requests.get(url, headers=headers, timeout=15)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        title = soup.find('h1').text.strip()
        price_text = soup.find('p', class_='price_color').text
        clean_price = price_text.replace('$', '').replace('£', '').replace('Â', '').strip()
        return title, float(clean_price)
    except Exception as e:
        raise Exception(f"Failed to scrape URL. Check if the URL is valid. Details: {e}")

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

# 4. Sync new clients directly to GitHub clients.json
def save_client_to_github(email, product_url, baseline):
    new_entry = {
        "client_email": email,
        "url": product_url,
        "baseline_price": float(baseline)
    }

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
                sha = file_data["sha"]
                decoded_content = base64.b64decode(file_data["content"]).decode("utf-8")
                clients = json.loads(decoded_content)
            else:
                sha = None
                clients = []

            clients.append(new_entry)
            updated_content = json.dumps(clients, indent=4)
            encoded_content = base64.b64encode(updated_content.encode("utf-8")).decode("utf-8")

            payload = {
                "message": f"Add client monitoring for {email}",
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

    try:
        file_path = "clients.json"
        if os.path.exists(file_path):
            with open(file_path, "r") as f:
                clients = json.load(f)
        else:
            clients = []
        clients.append(new_entry)
        with open(file_path, "w") as f:
            json.dump(clients, f, indent=4)
        return True
    except Exception as e:
        return str(e)

# --- UI TABS ---
tab1, tab2 = st.tabs(["🔍 Instant Price Check", "⚙️ 24/7 Client Onboarding"])

with tab1:
    st.subheader("Run Instant Competitor Analysis")
    target_url = st.text_input(
        "Competitor Product URL", 
        value="http://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html"
    )
    user_baseline = st.number_input("Your Store's Baseline Price ($ USD)", min_value=0.0, value=55.00, step=1.0)
    
    if st.button("Check Price & Get AI Strategy", type="primary"):
        with st.spinner("Scraping competitor and consulting Gemini AI..."):
            try:
                title, live_price = get_competitor_data(target_url)
                
                col1, col2 = st.columns(2)
                col1.metric("Product Title", title)
                col2.metric("Competitor Live Price", f"${live_price:.2f}", delta=f"${live_price - user_baseline:.2f} vs Baseline")
                
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
                        res = save_client_to_github(email, url, baseline)
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
                        res = save_client_to_github(formatted_email, local_url, local_baseline)
                        if res is True:
                            st.success("✅ Payment submitted! Your 24/7 monitoring bot will be activated shortly after verification.")
                        else:
                            st.error(f"Error: {res}")
                else:
                    st.warning("Please fill in all required fields including the Transaction ID.")
