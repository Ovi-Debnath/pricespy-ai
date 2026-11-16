import os
import json
import time
import base64
import smtplib
from email.mime.text import MIMEText
import requests
from bs4 import BeautifulSoup
from google import genai

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
SENDER_EMAIL = os.environ.get("SENDER_EMAIL")
SENDER_PASSWORD = os.environ.get("SENDER_PASSWORD")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_REPO = os.environ.get("GITHUB_REPO")
CONTACT_GMAIL = os.environ.get("CONTACT_GMAIL") or "ovidebnath63@gmail.com"

# Load clients from private clients.json (Supports GitHub API for Private Repos & Local fallback)
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
        except Exception as e:
            print(f"GitHub API fetch error: {e}")

    # Fallback to local file if exists
    file_path = "clients.json"
    if os.path.exists(file_path):
        with open(file_path, "r") as f:
            return json.load(f)
    return []

def get_product_data(product_url):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }
    response = requests.get(product_url, headers=headers, timeout=25)
    soup = BeautifulSoup(response.text, 'html.parser')
    
    og = soup.find('meta', property='og:title')
    title = og.get('content').strip() if og and og.get('content') else (soup.find('h1').text.strip() if soup.find('h1') else "E-commerce Product")
    
    # Simple price parser fallback
    price_tag = soup.find('span', class_='pdp-price') or soup.find('span', class_='price')
    price_text = price_tag.text if price_tag else "0"
    clean_price = ''.join(c for c in price_text if c.isdigit() or c == '.')
    price_val = float(clean_price) if clean_price else 0.0
    
    return title, price_val

def get_ai_strategy(product_name, old_price, new_price):
    if not GEMINI_API_KEY:
        return "• Review pricing manually.\n• Monitor competitor stock levels.\n• Maintain value proposition."
        
    client = genai.Client(api_key=GEMINI_API_KEY)
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
    # Clean email address (removes "[Paid via bKash ...]" tag if present)
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
    except Exception as e:
        print(f"Failed to send email to {clean_to_email}: {e}")

def run_automation():
    clients = load_clients()
    print(f"Loaded {len(clients)} client(s) from storage.")
    
    for client_item in clients:
        # Check if client is approved and active
        if not client_item.get("approved", True):
            continue
            
        client_email = client_item.get("client_email")
        products = client_item.get("products", [])
        
        for item in products:
            try:
                url = item.get("url")
                baseline_price = item.get("baseline_price", 0.0)
                saved_name = item.get("product_name", "Product")
                
                title, current_price = get_product_data(url)
                if not title or title == "E-commerce Product":
                    title = saved_name
                    
                print(f"Checking for {client_email} | {title} | Live: ৳{current_price:,.2f} | Baseline: ৳{baseline_price:,.2f}")
                
                # Compare live price with stored live price or baseline
                old_live = item.get("live_price", baseline_price)
                if current_price > 0 and current_price != old_live:
                    strategy = get_ai_strategy(title, baseline_price, current_price)
                    send_alert_email(
                        client_email,
                        title,
                        baseline_price,
                        current_price,
                        strategy,
                        url
                    )
                    # Update live price in memory
                    item["live_price"] = current_price
            except Exception as e:
                print(f"Error processing product for {client_email}: {e}")
                
    # Optional: Save updated prices back to GitHub if needed
    # (You can implement git commit / push or github contents API update here if desired)

if __name__ == "__main__":
    run_automation()
