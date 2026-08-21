"""
scraper.py
----------
Core Flipkart seller price scraping logic for target sellers:
RetailNet, Siril, Saara, PETILANTE Online, OptimVRcommerce, HSAtlastradeFashion
Auto-syncs freshly scraped records to Render Cloud Dashboard.
"""

import time
import re
import os
import pandas as pd
import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service

# Render Cloud API Sync Endpoint
RENDER_URL = "https://flipkart-price-tracker-y0hp.onrender.com/api/update_price"

# Target Sellers List
TARGET_SELLERS = [
    "RetailNet", 
    "Siril", 
    "Saara", 
    "PETILANTE Online", 
    "OptimVRcommerce", 
    "HSAtlastradeFashion"
]

WAIT_TIME = 15
DELAY_BETWEEN_REQUESTS = 2
HEADLESS = True
DEBUG = False


def setup_driver():
    options = Options()
    if HEADLESS:
        options.add_argument("--headless=new")
    
    # Server/Windows Stability Flags
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-extensions")
    options.add_argument("--remote-debugging-port=9222")
    options.add_argument("--window-size=1200,1800")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )

    # 1. Check for Render Custom Chrome Path (installed via build.sh)
    render_chrome_bin = "/opt/render/project/src/chrome/opt/google/chrome/chrome"
    # 2. Check for System Chromium Path
    system_chrome_bin = "/usr/bin/chromium-browser"

    if os.path.exists(render_chrome_bin):
        options.binary_location = render_chrome_bin
    elif os.path.exists(system_chrome_bin):
        options.binary_location = system_chrome_bin

    try:
        service = Service(ChromeDriverManager().install())
        return webdriver.Chrome(service=service, options=options)
    except Exception:
        return webdriver.Chrome(options=options)


def extract_pid_from_url(url: str) -> str:
    try:
        match = re.search(r"[?&]pid=([^&]+)", url, re.IGNORECASE)
        if match:
            return match.group(1)
    except Exception:
        pass
    return ""


def build_all_sellers_url(fsn: str) -> str:
    encoded_fsn = re.sub(r"\s+", "", str(fsn).strip())
    return f"https://www.flipkart.com/sellers?pid={encoded_fsn}"


def extract_all_target_sellers(driver, fsn: str) -> dict:
    seller_data = {s: "" for s in TARGET_SELLERS}
    url = build_all_sellers_url(fsn)

    seller_patterns = {
        "RetailNet": r"\bretailnet\b",
        "Siril": r"\bsiril\b",
        "Saara": r"\bsaara\b",
        "PETILANTE Online": r"\bpetilante(\s*online)?\b",
        "OptimVRcommerce": r"\boptim(\s*vr\s*commerce)?\b",
        "HSAtlastradeFashion": r"\b(hsatlastradefashion|hsatlastrade|hsa)\b"
    }

    try:
        driver.get(url)
        WebDriverWait(driver, WAIT_TIME).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
        time.sleep(2)

        page_text = driver.find_element(By.TAG_NAME, "body").text
        lines = [line.strip() for line in page_text.splitlines() if line.strip()]

        for i, line in enumerate(lines):
            line_lower = line.lower()
            
            for seller_name, pattern in seller_patterns.items():
                if re.search(pattern, line_lower, re.IGNORECASE) and not seller_data[seller_name]:
                    
                    for j in range(i + 1, min(i + 11, len(lines))):
                        current_line_lower = lines[j].lower()
                        
                        if any(re.search(p, current_line_lower, re.IGNORECASE) for s, p in seller_patterns.items() if s != seller_name):
                            break
                            
                        price_matches = re.findall(r"₹\s*([\d,]+)", lines[j])
                        if price_matches:
                            for p in price_matches:
                                clean_p = int(p.replace(",", ""))
                                if clean_p < 5000:
                                    seller_data[seller_name] = clean_p
                                    break
                        
                        if seller_data[seller_name]:
                            break

    except TimeoutException:
        print(f"  [TIMEOUT] FSN={fsn}")
    except Exception as e:
        print(f"  [ERROR] FSN={fsn} -> {e}")

    return seller_data


def get_price(fsn: str) -> dict:
    driver = setup_driver()
    try:
        sellers_info = extract_all_target_sellers(driver, fsn)
        main_price = sellers_info.get("RetailNet") or next((v for v in sellers_info.values() if v), None)
        return {
            'title': 'Multi-Seller Product',
            'price': float(main_price) if main_price else None,
            'sellers': sellers_info
        }
    finally:
        driver.quit()


def process_file(input_path: str, output_path: str, progress_callback=None):
    df = pd.read_excel(input_path) if input_path.endswith('.xlsx') else pd.read_csv(input_path)

    fsn_col = None
    link_col = None
    for col in df.columns:
        col_lower = str(col).lower()
        if "fsn" in col_lower:
            fsn_col = col
        if "link" in col_lower:
            link_col = col

    if fsn_col is None:
        raise ValueError(f"Excel me 'FSN' naam ka column nahi mila. Columns: {list(df.columns)}")

    for seller in TARGET_SELLERS:
        df[f"{seller} Price"] = ""

    total = len(df)
    driver = setup_driver()

    try:
        for idx, row in df.iterrows():
            fsn_value = row[fsn_col]
            if pd.isna(fsn_value) or not str(fsn_value).strip():
                if link_col is not None:
                    url = row[link_col]
                    if not pd.isna(url) and str(url).strip().startswith("http"):
                        fsn_value = extract_pid_from_url(str(url).strip())
                if pd.isna(fsn_value) or not str(fsn_value).strip():
                    continue

            fsn_value = str(fsn_value).strip()

            if progress_callback:
                progress_callback(idx + 1, total, f"Fetching FSN: {fsn_value}")

            sellers_info = extract_all_target_sellers(driver, fsn_value)

            for seller in TARGET_SELLERS:
                df.at[idx, f"{seller} Price"] = sellers_info.get(seller, "")

            # Push Scraped Data directly to Render Cloud URL
            try:
                payload = {
                    "fsn": fsn_value,
                    "sellers": sellers_info
                }
                requests.post(RENDER_URL, json=payload, timeout=5)
            except Exception as e:
                print(f"Cloud Push Failed for FSN {fsn_value}: {e}")

            time.sleep(DELAY_BETWEEN_REQUESTS)

    finally:
        driver.quit()

    df.to_excel(output_path, index=False)
    return output_path
