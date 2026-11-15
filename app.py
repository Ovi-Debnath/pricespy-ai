import os
import json
import time
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

# 1. API Key Configuration
# Looks for Streamlit Secrets first, then environment variables
api_key = st.secrets.get("GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY")

if not api_key:
    st.error("⚠️ GEMINI_API_KEY is missing! Please configure it in Streamlit Secrets or environment variables.")
    st.stop()

# Initialize Gemini Client
client = genai.Client(api_key=api_key)

# 2. Function to fetch live competitor price
def get_competitor_data(url):
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        response = requests.get(url, headers=headers, timeout=15)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Scrape title and price (Optimized for books.toscrape.com or standard e-commerce selectors)
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
    
    # Filter for active Flash text models dynamically
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
    
    # Fallback default models if list fails
    if not available_models:
        available_models = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]

    # Try models sequentially until one succeeds
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

# 4. Function to save new clients to clients.json automatically
def save_client_to_db(email, product_url, baseline):
    file_path = "clients.json"
    try:
        if os.path.exists(file_path):
            with open(file_path, "r") as f:
                clients = json.load(f)
        else:
            clients = []
            
        # Append new client watch item
        clients.append({
            "client_email": email,
            "url": product_url,
            "baseline_price": float(baseline)
        })
        
        with open(file_path, "w") as f:
            json.dump(clients, f, indent=4)
        return True
    except Exception as e:
        return str(e)

# --- UI TABS / SECTIONS ---
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
    st.subheader("🚀 Register for 24/7 Background Monitoring")
    st.markdown("Add your store products to the automated monitoring queue. Our background bot will check prices every 6 hours and email you alerts.")
    
    with st.form("client_onboarding_form"):
        client_email = st.text_input("Your Email (for alerts)")
        client_url = st.text_input("Competitor URL to Track", value="http://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html")
        client_baseline = st.number_input("Your Baseline Price ($ USD) for this item", min_value=0.0, value=55.00, step=1.0)
        
        submit_client = st.form_submit_button("Activate 24/7 Spy Bot", use_container_width=True)
        
        if submit_client:
            if client_email and client_url:
                result = save_client_to_db(client_email, client_url, client_baseline)
                if result is True:
                    st.success(f"Successfully registered {client_email}! Our 24/7 bot will now monitor this URL.")
                    st.info("Tip: If running locally, make sure to commit and push the updated `clients.json` file to GitHub so the cloud action can read it.")
                else:
                    st.error(f"Failed to save: {result}")
            else:
                st.warning("Please provide both your email and the competitor URL.")
