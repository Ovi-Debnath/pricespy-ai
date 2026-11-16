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

# 1. API & GitHub & Email & Contact Gmail Configuration
api_key = st.secrets.get("GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY")
github_token = st.secrets.get("GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")
github_repo = st.secrets.get("GITHUB_REPO") or os.environ.get("GITHUB_REPO")
sender_email = st.secrets.get("SENDER_EMAIL") or os.environ.get("SENDER_EMAIL")
sender_password = st.secrets.get("SENDER_PASSWORD") or os.environ.get("SENDER_PASSWORD")
admin_password = st.secrets.get("ADMIN_PASSWORD") or os.environ.get("ADMIN_PASSWORD")
admin_gmail = st.secrets.get("ADMIN_GMAIL") or sender_email
payment_number = st.secrets.get("PAYMENT_NUMBER") or os.environ.get("PAYMENT_NUMBER")

# Contact Gmail এখন st.secrets থেকে রিড করবে (সিক্রেট হিসেবে নিরাপদ থাকবে)
contact_gmail = str(st.secrets.get("CONTACT_GMAIL", "")).strip()


# Contact Gmail is hidden until the user clicks the reveal button.
def show_contact_gmail(key, message="Contact Gmail"):
    if not contact_gmail:
        st.warning("Contact Gmail is not configured.")
        return
    if st.button("📧 Show Contact Gmail", key=key):
        st.session_state[f"{key}_visible"] = True
    if st.session_state.get(f"{key}_visible", False):
        st.info(f"{message}: {contact_gmail}")


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
