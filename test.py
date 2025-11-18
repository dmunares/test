import time
import os
import json
import cv2
import numpy as np
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import requests

# ---------- CONFIG ----------
SEARCH_PAGE = "https://www.centris.ca/en/properties~for-sale?uc=4"
OUT_DIR = "centris_cache"
IMAGES_DIR = os.path.join(OUT_DIR, "images")
SEEN_FILE = os.path.join(OUT_DIR, "seen_listings.json")
DELAY_SECONDS = 1
MIN_PURPLE_AREA = 1500
ALERTS_FILE = os.path.join(OUT_DIR, "alerts.log")
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
SCROLL_PAUSE = 3  # seconds after each scroll
MAX_SCROLLS = 300
# ----------------------------

os.makedirs(IMAGES_DIR, exist_ok=True)
if not os.path.exists(SEEN_FILE):
    with open(SEEN_FILE, "w") as f:
        json.dump([], f)

def load_seen():
    with open(SEEN_FILE, "r") as f:
        return set(json.load(f))

def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        json.dump(sorted(list(seen)), f)

def init_driver():
    chrome_options = Options()
    chrome_options.add_argument(f"user-agent={USER_AGENT}")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option('useAutomationExtension', False)
    driver = webdriver.Chrome(options=chrome_options)
    return driver

def extract_listing_links_selenium(driver):
    links = set()
    try:
        WebDriverWait(driver, 5).until(
            EC.presence_of_element_located((By.TAG_NAME, "a"))
        )
    except:
        pass
    
    elements = driver.find_elements(By.TAG_NAME, "a")
    for elem in elements:
        try:
            href = elem.get_attribute("href")
            if href and "/en/" in href and ("/property" in href or "/properties" in href or "/real-estate" in href):
                links.add(href)
        except:
            continue
    return sorted(links)

def extract_image_urls_from_listing(driver, listing_url):
    try:
        driver.get(listing_url)
        time.sleep(2)
        imgs = []
        for img in driver.find_elements(By.TAG_NAME, "img"):
            try:
                src = img.get_attribute("src")
                if src and not src.startswith("data:") and (src.lower().endswith((".jpg", ".jpeg", ".png", ".webp")) or "photo" in src.lower() or "image" in src.lower()):
                    imgs.append(src)
            except:
                continue
        return sorted(set(imgs))
    except:
        return []

def download_image(url, out_path):
    try:
        headers = {"User-Agent": USER_AGENT}
        r = requests.get(url, headers=headers, timeout=20)
        r.raise_for_status()
        with open(out_path, "wb") as f:
            f.write(r.content)
        return True
    except:
        return False

def detect_purple_blob_image(path):
    img = cv2.imread(path)
    if img is None:
        return False
    scale = 800.0 / max(img.shape[:2])
    if scale < 1:
        img = cv2.resize(img, (int(img.shape[1]*scale), int(img.shape[0]*scale)), interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    lower1 = np.array([130, 20, 120])
    upper1 = np.array([160, 180, 255])
    lower2 = np.array([115, 25, 100])
    upper2 = np.array([135, 160, 255])
    lower3 = np.array([135, 40, 80])
    upper3 = np.array([155, 255, 255])
    mask = cv2.bitwise_or(cv2.inRange(hsv, lower1, upper1), cv2.inRange(hsv, lower2, upper2))
    mask = cv2.bitwise_or(mask, cv2.inRange(hsv, lower3, upper3))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5,5))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    areas = [cv2.contourArea(c) for c in cnts]
    if not areas:
        return False
    max_area = max(areas)
    if max_area < MIN_PURPLE_AREA:
        return False
    max_idx = np.argmax(areas)
    c = cnts[max_idx]
    x,y,w,h = cv2.boundingRect(c)
    area = areas[max_idx]
    hull_area = cv2.contourArea(cv2.convexHull(c))
    solidity = float(area)/hull_area if hull_area>0 else 0
    aspect = float(w)/max(h,1)
    if area >= MIN_PURPLE_AREA and 0.5 < solidity < 0.95 and 0.4 < aspect < 2.5:
        return True
    return False

def log_alert(listing_url, image_path):
    entry = f"{datetime.utcnow().isoformat()}Z | listing: {listing_url} | image: {image_path}\n"
    with open(ALERTS_FILE, "a") as f:
        f.write(entry)
    print("\n" + "="*80)
    print(">>> ALERT: POSSIBLE PURPLE DUCK FOUND! <<<")
    print(f"Listing: {listing_url}")
    print(f"Image: {image_path}")
    print("="*80 + "\n")

def main():
    seen = load_seen()
    print(f"Loaded {len(seen)} seen listings.")
    
    driver = init_driver()
    
    try:
        driver.get(SEARCH_PAGE)
        time.sleep(3)
        
        scroll_count = 0
        all_seen_links = set(seen)
        
        while scroll_count < MAX_SCROLLS:
            scroll_count += 1
            print(f"\n=== SCROLL {scroll_count}/{MAX_SCROLLS} ===")
            
            # Extract currently visible listings
            links = extract_listing_links_selenium(driver)
            new_links = set(links) - all_seen_links
            print(f"Found {len(new_links)} new listings")
            
            # Process each new listing
            for idx, listing_url in enumerate(new_links, 1):
                print(f"Processing [{idx}/{len(new_links)}]: {listing_url}")
                imgs = extract_image_urls_from_listing(driver, listing_url)
                print(f"  Found {len(imgs)} images")
                
                for img_url in imgs:
                    fname = f"{abs(hash(img_url))}.jpg"
                    outp = os.path.join(IMAGES_DIR, fname)
                    if not os.path.exists(outp):
                        if not download_image(img_url, outp):
                            continue
                    try:
                        if detect_purple_blob_image(outp):
                            log_alert(listing_url, outp)
                    except Exception as e:
                        print(f"  Error analyzing image: {e}")
                
                all_seen_links.add(listing_url)
                save_seen(all_seen_links)
                time.sleep(DELAY_SECONDS)
            
            # Scroll down to load more
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(SCROLL_PAUSE)
            
            # Stop if no new listings loaded
            if not new_links:
                print("No new listings loaded, stopping early.")
                break
        
        print("\nSCAN COMPLETE!")
        print(f"Total listings scanned: {len(all_seen_links)}")
        print(f"Check {ALERTS_FILE} for alerts")
    
    finally:
        driver.quit()

if __name__ == "__main__":
    main()


i want to be able to go through centris website scroll dynamically properties and analyse image of all properties to find lavender/purple duck
