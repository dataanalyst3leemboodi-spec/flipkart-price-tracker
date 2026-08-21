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
import gc
import pandas as pd
import requests
from bs4 import BeautifulSoup

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

DELAY_BETWEEN_REQUESTS = 1

# Global requests session to reuse TCP connections and save RAM
session = requests.Session()


class DriverDummy:
    """Dummy class to prevent 'NoneType has no attribute quit' errors in web app calls."""
    def quit(self):
        pass


def setup_driver():
    return DriverDummy()


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

    # Browser Fingerprint Headers to Bypass Flipkart Cloud IP Blocking
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-User': '?1',
        'Cache-Control': 'max-age=0'
    }

    seller_patterns = {
        "RetailNet": r"\bretailnet\b",
        "Siril": r"\bsiril\b",
        "Saara": r"\bsaara\b",
        "PETILANTE Online": r"\bpetilante(\s*online)?\b",
        "OptimVRcommerce": r"\boptim(\s*vr\s*commerce)?\b",
        "HSAtlastradeFashion": r"\b(hsatlastradefashion|hsatlastrade|hsa)\b"
    }

    try:
        resp = session.get(url, headers=headers, timeout=10)
        
        # Checking if valid page HTML is received
        if resp.status_code == 200 and "flipkart" in resp.text.lower():
            soup = BeautifulSoup(resp.text, 'html.parser')
            page_text = soup.get_text(separator="\n")
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

    except Exception as e:
        print(f"  [ERROR] FSN={fsn} -> {e}")

    return seller_data


def get_price(fsn: str) -> dict:
    sellers_info = extract_all_target_sellers(None, fsn)
    main_price = sellers_info.get("RetailNet") or next((v for v in sellers_info.values() if v), None)
    return {
        'title': 'Multi-Seller Product',
        'price': float(main_price) if main_price else None,
        'sellers': sellers_info
    }


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
                session.post(RENDER_URL, json=payload, timeout=5)
            except Exception as e:
                print(f"Cloud Push Failed for FSN {fsn_value}: {e}")

            if idx % 5 == 0:
                gc.collect()

            time.sleep(DELAY_BETWEEN_REQUESTS)

    finally:
        if driver is not None and hasattr(driver, 'quit'):
            driver.quit()
        gc.collect()

    df.to_excel(output_path, index=False)
    return output_path
