import os
import json
import time
import smtplib
from email.mime.text import MIMEText
import requests
from bs4 import BeautifulSoup
from google import genai

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
SENDER_EMAIL = os.environ.get("SENDER_EMAIL")
SENDER_PASSWORD = os.environ.get("SENDER_PASSWORD")

# Load all active clients from clients.json
def load_clients():
    with open("clients.json", "r") as f:
        return json.load(f)

def get_product_data(product_url):
    headers = {"User-Agent": "Mozilla/5.0"}
    response = requests.get(product_url, headers=headers, timeout=15)
    soup = BeautifulSoup(response.text, 'html.parser')
    
    title = soup.find('h1').text.strip()
    price_text = soup.find('p', class_='price_color').text
    clean_price = price_text.replace('$', '').replace('£', '').replace('Â', '').strip()
    return title, float(clean_price)

def get_ai_strategy(product_name, old_usd, new_usd):
    client = genai.Client(api_key=GEMINI_API_KEY)
    prompt = (
        f"You are an e-commerce pricing strategist. A competitor changed the price of '{product_name}' "
        f"from ${old_usd:.2f} to ${new_usd:.2f}. "
        f"Give 3 concise bullet points on how our store should respond immediately."
    )
    
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
            try:
                response = client.models.generate_content(model=name, contents=prompt)
                return response.text
            except Exception:
                time.sleep(1)
                continue
    return "Competitor price changed. Review your store pricing manually."

def send_alert_email(to_email, product_name, old_usd, new_usd, strategy, url):
    subject = f"🚨 Price Alert: {product_name} is now ${new_usd:.2f}"
    body = (
        f"Competitor Price Shift Detected!\n\n"
        f"Product: {product_name}\n"
        f"Your Baseline Price: ${old_usd:.2f}\n"
        f"New Competitor Price: ${new_usd:.2f}\n"
        f"Product Link: {url}\n\n"
        f"--- AI RECOMMENDED COUNTER-STRATEGY ---\n"
        f"{strategy}\n\n"
        f"- Automated Alert by PriceSpy AI"
    )
    
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = SENDER_EMAIL
    msg["To"] = to_email

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.send_message(msg)
    print(f"Alert email sent to {to_email}!")

def run_automation():
    clients = load_clients()
    for item in clients:
        try:
            title, current_price = get_product_data(item["url"])
            print(f"Checking for {item['client_email']}: {title} | Live: ${current_price:.2f}")
            
            if current_price != item["baseline_price"]:
                strategy = get_ai_strategy(title, item["baseline_price"], current_price)
                send_alert_email(
                    item["client_email"],
                    title,
                    item["baseline_price"],
                    current_price,
                    strategy,
                    item["url"]
                )
        except Exception as e:
            print(f"Error for {item['client_email']}: {e}")

if __name__ == "__main__":
    run_automation()
