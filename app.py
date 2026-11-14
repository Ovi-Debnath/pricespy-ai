import time
import streamlit as st
import requests
from bs4 import BeautifulSoup
from google import genai

# 1. Page Configuration
st.set_page_config(page_title="PriceSpy AI - Competitor Intelligence", page_icon="🕵️", layout="centered")
st.title("🕵️ PriceSpy AI")
st.caption("Automated Competitor Price Monitoring & AI Counter-Strategy Engine")

# 2. Load API Key securely from Cloud Secrets (with local fallback)
if "GEMINI_API_KEY" in st.secrets:
    api_key = st.secrets["GEMINI_API_KEY"]
else:
    api_key = st.sidebar.text_input("Admin Gemini API Key (Local Test):", type="password")

# 3. Clean SaaS Input Form
with st.form("spy_form"):
    url = st.text_input(
        "Competitor Product URL",
        value="http://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html"
    )
    old_price = st.number_input("Your Baseline Price ($ USD)", min_value=0.0, value=55.00, step=1.0)
    submitted = st.form_submit_button("Analyze Competitor Price", use_container_width=True)

# 4. Scraping Engine
def get_product_data(product_url):
    headers = {"User-Agent": "Mozilla/5.0"}
    response = requests.get(product_url, headers=headers, timeout=10)
    soup = BeautifulSoup(response.text, 'html.parser')
    
    title = soup.find('h1').text.strip()
    price_text = soup.find('p', class_='price_color').text
    clean_price = price_text.replace('$', '').replace('£', '').replace('Â', '').strip()
    
    return title, float(clean_price)

# 5. AI Strategy Engine
def get_ai_strategy(key, product_name, yesterday_usd, today_usd):
    client = genai.Client(api_key=key)
    prompt = (
        f"You are an elite e-commerce pricing strategist. A competitor changed the price of '{product_name}' "
        f"from ${yesterday_usd:.2f} to ${today_usd:.2f}. "
        f"Provide 3 concise, high-impact action steps for my online store to protect profit margins and win customers."
    )

    valid_models = []
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
            valid_models.append(name)
            
    last_error = ""
    for model_name in valid_models:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
            )
            return response.text
        except Exception as e:
            last_error = str(e)
            time.sleep(1)
            continue
            
    raise Exception(f"AI service busy. Details: {last_error}")

# 6. Execution Logic
if submitted:
    if not api_key:
        st.warning("API Key missing. Please configure GEMINI_API_KEY in Streamlit Secrets.")
    else:
        with st.spinner("Scanning competitor page & generating AI strategy..."):
            try:
                title, current_price = get_product_data(url)
                
                st.divider()
                st.subheader(title)
                col1, col2 = st.columns(2)
                col1.metric("Your Baseline Price", f"${old_price:.2f}")
                col2.metric("Competitor Live Price", f"${current_price:.2f}", delta=f"${current_price - old_price:.2f}")
                
                if current_price != old_price:
                    st.error("🚨 Price Shift Detected! Generating Counter-Strategy...")
                    strategy = get_ai_strategy(api_key, title, old_price, current_price)
                    st.success("💡 AI Recommended Action Plan")
                    st.markdown(strategy)
                else:
                    st.info("✅ Prices match. No immediate action required.")
                    
            except Exception as e:
                st.error(f"Could not scan URL: {e}")