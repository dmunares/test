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
from selenium.common.exceptions import TimeoutException, StaleElementReferenceException
import requests

# ---------- CONFIG ----------
SEARCH_PAGE = "https://www.centris.ca/en/properties~for-sale?uc=4"
OUT_DIR = "centris_cache"
IMAGES_DIR = os.path.join(OUT_DIR, "images")
SEEN_FILE = os.path.join(OUT_DIR, "seen_listings.json")
DELAY_SECONDS = 0.3  # Reduced delay between properties
MIN_PURPLE_AREA = 1000  # Reduced to catch smaller ducks
ALERTS_FILE = os.path.join(OUT_DIR, "alerts.log")
ANALYZED_FILE = os.path.join(OUT_DIR, "analyzed_properties.txt")  # File for analyzed properties with image counts
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
PAGE_LOAD_WAIT = 1.5  # Reduced wait after clicking next page
MAX_PAGES = 300  # maximum pages to process (safety limit)
SCROLL_STEP = 500  # pixels to scroll at a time
MAX_IMAGES_PER_PROPERTY = 15  # Limit images per property for speed
# ----------------------------

os.makedirs(IMAGES_DIR, exist_ok=True)
if not os.path.exists(SEEN_FILE):
    with open(SEEN_FILE, "w") as f:
        json.dump([], f)

# Initialize analyzed properties file with header
if not os.path.exists(ANALYZED_FILE):
    with open(ANALYZED_FILE, "w") as f:
        f.write("Real-time log of analyzed properties\n")
        f.write("Format: timestamp | URL | Images: count\n")
        f.write("=" * 80 + "\n\n")

def load_seen():
    with open(SEEN_FILE, "r") as f:
        return set(json.load(f))

def save_seen(seen):
    """Save seen listings to file in a readable format."""
    with open(SEEN_FILE, "w") as f:
        # Save as sorted list for readability
        sorted_listings = sorted(list(seen))
        json.dump(sorted_listings, f, indent=2)
    
    # Also create a human-readable text file with all listings
    seen_txt_file = SEEN_FILE.replace(".json", ".txt")
    with open(seen_txt_file, "w") as f:
        f.write(f"Total listings detected: {len(seen)}\n")
        f.write(f"Last updated: {datetime.utcnow().isoformat()}Z\n")
        f.write("=" * 80 + "\n\n")
        for idx, listing_url in enumerate(sorted_listings, 1):
            f.write(f"{idx}. {listing_url}\n")

def save_analyzed_property(listing_url, image_count):
    """Save analyzed property with image count to the analyzed properties file in real-time."""
    try:
        # Append to file in real-time for immediate visibility
        with open(ANALYZED_FILE, "a") as f:  # Append mode for real-time logging
            timestamp = datetime.utcnow().isoformat() + "Z"
            f.write(f"{timestamp} | {listing_url} | Images: {image_count}\n")
            f.flush()  # Force immediate write to disk
        
        # Also maintain a sorted summary file (updated less frequently)
        # Read existing analyzed properties for summary
        analyzed_properties = {}
        summary_file = ANALYZED_FILE.replace(".txt", "_summary.txt")
        
        if os.path.exists(ANALYZED_FILE):
            with open(ANALYZED_FILE, "r") as f:
                lines = f.readlines()
                for line in lines:
                    if "|" in line and "Images:" in line:
                        # Parse: "timestamp | URL | Images: X"
                        parts = line.split("|")
                        if len(parts) >= 3:
                            url = parts[1].strip()
                            img_part = parts[2].strip()
                            if "Images:" in img_part:
                                count = int(img_part.replace("Images:", "").strip())
                                # Keep the latest count for each URL
                                if url not in analyzed_properties:
                                    analyzed_properties[url] = count
        
        # Update summary file periodically (every 10 properties or so)
        # For now, update it each time but this could be optimized
        with open(summary_file, "w") as f:
            f.write(f"Total analyzed properties: {len(analyzed_properties)}\n")
            f.write(f"Last updated: {datetime.utcnow().isoformat()}Z\n")
            f.write("=" * 80 + "\n\n")
            sorted_props = sorted(analyzed_properties.items())
            for idx, (url, count) in enumerate(sorted_props, 1):
                f.write(f"{idx}. {url} | Images: {count}\n")
    except Exception as e:
        print(f"Warning: Failed to save analyzed property: {e}")

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
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.TAG_NAME, "a"))
        )
    except TimeoutException:
        pass
    
    # Try multiple selectors for Centris listings
    selectors = [
        (By.TAG_NAME, "a"),
        (By.CSS_SELECTOR, "a[href*='/property']"),
        (By.CSS_SELECTOR, "a[href*='/properties']"),
        (By.CSS_SELECTOR, "a[href*='/real-estate']"),
    ]
    
    for selector_type, selector_value in selectors:
        try:
            elements = driver.find_elements(selector_type, selector_value)
            for elem in elements:
                try:
                    href = elem.get_attribute("href")
                    if href and "/en/" in href and ("/property" in href or "/properties" in href or "/real-estate" in href):
                        # Clean up the URL
                        if "?" in href:
                            href = href.split("?")[0]
                        links.add(href)
                except (StaleElementReferenceException, Exception):
                    continue
        except Exception:
            continue
    
    return sorted(links)

def extract_image_urls_from_listing(driver, listing_url):
    try:
        driver.get(listing_url)
        # Reduced wait for page to load
        time.sleep(1.5)
        
        # Wait for images to load (reduced timeout)
        try:
            WebDriverWait(driver, 3).until(
                EC.presence_of_element_located((By.TAG_NAME, "img"))
            )
        except TimeoutException:
            pass
        
        imgs = []
        
        # Try multiple ways to get image URLs
        for img in driver.find_elements(By.TAG_NAME, "img"):
            try:
                # Check src attribute
                src = img.get_attribute("src")
                if src and not src.startswith("data:"):
                    if src.lower().endswith((".jpg", ".jpeg", ".png", ".webp")) or "photo" in src.lower() or "image" in src.lower():
                        imgs.append(src)
                
                # Check data-src (lazy loading)
                data_src = img.get_attribute("data-src")
                if data_src and not data_src.startswith("data:"):
                    if data_src.lower().endswith((".jpg", ".jpeg", ".png", ".webp")) or "photo" in data_src.lower() or "image" in data_src.lower():
                        imgs.append(data_src)
                
                # Check srcset
                srcset = img.get_attribute("srcset")
                if srcset:
                    for url in srcset.split(","):
                        url = url.strip().split()[0] if url.strip() else ""
                        if url and not url.startswith("data:") and (url.lower().endswith((".jpg", ".jpeg", ".png", ".webp")) or "photo" in url.lower()):
                            imgs.append(url)
            except (StaleElementReferenceException, Exception):
                continue
        
        # Also check for background images in divs
        try:
            divs = driver.find_elements(By.CSS_SELECTOR, "div[style*='background-image']")
            for div in divs:
                try:
                    style = div.get_attribute("style")
                    if style and "url(" in style:
                        url = style.split("url(")[1].split(")")[0].strip('"\'')
                        if url and not url.startswith("data:") and (url.lower().endswith((".jpg", ".jpeg", ".png", ".webp")) or "photo" in url.lower()):
                            imgs.append(url)
                except Exception:
                    continue
        except Exception:
            pass
        
        return sorted(set(imgs))
    except Exception as e:
        print(f"  Error extracting images from {listing_url}: {e}")
        return []

def download_image(url, out_path):
    try:
        headers = {"User-Agent": USER_AGENT}
        r = requests.get(url, headers=headers, timeout=10, stream=True)  # Reduced timeout, use streaming
        r.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
        return True
    except:
        return False

def detect_purple_blob_image(path):
    """Detect purple/lavender colored objects (like ducks) in an image."""
    img = cv2.imread(path)
    if img is None:
        return False
    
    # Resize for faster processing (smaller size = faster)
    scale = 500.0 / max(img.shape[:2])  # Reduced from 800 for faster processing
    if scale < 1:
        img = cv2.resize(img, (int(img.shape[1]*scale), int(img.shape[0]*scale)), interpolation=cv2.INTER_AREA)
    
    # Convert to HSV for better color detection
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    
    # Expanded purple/lavender color ranges in HSV
    # Purple range 1: Deep purple
    lower1 = np.array([130, 20, 100])
    upper1 = np.array([160, 200, 255])
    
    # Purple range 2: Medium purple
    lower2 = np.array([115, 20, 80])
    upper2 = np.array([140, 180, 255])
    
    # Purple range 3: Light purple/lavender
    lower3 = np.array([135, 30, 100])
    upper3 = np.array([165, 255, 255])
    
    # Lavender range: Lighter, more pastel purple
    lower4 = np.array([125, 15, 150])
    upper4 = np.array([145, 100, 255])
    
    # Purple range 5: Very light lavender
    lower5 = np.array([130, 10, 180])
    upper5 = np.array([150, 80, 255])
    
    # Create combined mask for all purple/lavender shades
    mask = cv2.inRange(hsv, lower1, upper1)
    mask = cv2.bitwise_or(mask, cv2.inRange(hsv, lower2, upper2))
    mask = cv2.bitwise_or(mask, cv2.inRange(hsv, lower3, upper3))
    mask = cv2.bitwise_or(mask, cv2.inRange(hsv, lower4, upper4))
    mask = cv2.bitwise_or(mask, cv2.inRange(hsv, lower5, upper5))
    
    # Morphological operations to clean up the mask
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    
    # Find contours
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return False
    
    areas = [cv2.contourArea(c) for c in cnts]
    max_area = max(areas)
    
    if max_area < MIN_PURPLE_AREA:
        return False
    
    # Analyze the largest purple blob
    max_idx = np.argmax(areas)
    c = cnts[max_idx]
    x, y, w, h = cv2.boundingRect(c)
    area = areas[max_idx]
    
    # Calculate shape properties
    hull_area = cv2.contourArea(cv2.convexHull(c))
    solidity = float(area) / hull_area if hull_area > 0 else 0
    aspect = float(w) / max(h, 1)
    
    # Check if it meets criteria for a potential duck-like object
    # Ducks can have various shapes, so we're more lenient
    if area >= MIN_PURPLE_AREA:
        # More flexible shape detection - ducks can be various shapes
        if 0.3 < solidity < 0.98 and 0.2 < aspect < 3.0:
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

def scroll_to_bottom(driver):
    """Scroll to the bottom of the page to ensure Next button is visible."""
    # Faster scroll - go directly to bottom with minimal checks
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
    time.sleep(0.5)
    # One quick check for lazy loading
    final_height = driver.execute_script("return document.body.scrollHeight")
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
    time.sleep(0.3)

def find_and_click_next_button(driver):
    """Find and click the 'Next' button to go to the next page."""
    # First, scroll to bottom to ensure Next button is visible
    scroll_to_bottom(driver)
    
    # Common selectors for "Next" buttons in pagination
    next_button_selectors = [
        # Text-based selectors (English and French)
        (By.XPATH, "//a[contains(text(), 'Next')]"),
        (By.XPATH, "//button[contains(text(), 'Next')]"),
        (By.XPATH, "//a[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'next')]"),
        (By.XPATH, "//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'next')]"),
        (By.XPATH, "//a[contains(text(), 'Suivant')]"),  # French for "Next"
        (By.XPATH, "//button[contains(text(), 'Suivant')]"),
        # Aria-label based
        (By.CSS_SELECTOR, "a[aria-label*='Next']"),
        (By.CSS_SELECTOR, "a[aria-label*='next']"),
        (By.CSS_SELECTOR, "button[aria-label*='Next']"),
        (By.CSS_SELECTOR, "button[aria-label*='next']"),
        # Class-based (common patterns)
        (By.CSS_SELECTOR, "a.next"),
        (By.CSS_SELECTOR, "button.next"),
        (By.CSS_SELECTOR, ".next"),
        (By.CSS_SELECTOR, "[class*='next']"),
        (By.CSS_SELECTOR, "[class*='Next']"),
        # Icon-based (right arrow)
        (By.CSS_SELECTOR, "a[class*='arrow-right']"),
        (By.CSS_SELECTOR, "button[class*='arrow-right']"),
        (By.CSS_SELECTOR, "a[class*='arrow']:last-child"),
        # Pagination specific
        (By.CSS_SELECTOR, ".pagination a:last-child"),
        (By.CSS_SELECTOR, ".pagination button:last-child"),
        (By.CSS_SELECTOR, "[class*='pagination'] a:last-child"),
        # Centris-specific patterns (common in real estate sites)
        (By.CSS_SELECTOR, "[data-testid*='next']"),
        (By.CSS_SELECTOR, "[id*='next']"),
        (By.XPATH, "//*[@data-testid and contains(@data-testid, 'next')]"),
        # Right arrow icon (common pagination pattern)
        (By.CSS_SELECTOR, "svg[class*='arrow'] + a"),
        (By.CSS_SELECTOR, "i[class*='arrow-right']"),
        (By.CSS_SELECTOR, "span[class*='arrow-right']"),
    ]
    
    # First, try to find pagination container and look for next button within it
    pagination_containers = [
        (By.CSS_SELECTOR, ".pagination"),
        (By.CSS_SELECTOR, "[class*='pagination']"),
        (By.CSS_SELECTOR, "[class*='Pagination']"),
        (By.CSS_SELECTOR, "nav[aria-label*='pagination']"),
        (By.CSS_SELECTOR, "nav[aria-label*='Pagination']"),
    ]
    
    for container_selector_type, container_selector_value in pagination_containers:
        try:
            containers = driver.find_elements(container_selector_type, container_selector_value)
            for container in containers:
                try:
                    # Look for next button within this container
                    links = container.find_elements(By.TAG_NAME, "a")
                    buttons = container.find_elements(By.TAG_NAME, "button")
                    
                    for elem in links + buttons:
                        try:
                            text = elem.text.lower()
                            aria_label = (elem.get_attribute("aria-label") or "").lower()
                            class_attr = (elem.get_attribute("class") or "").lower()
                            
                            if ("next" in text or "suivant" in text or 
                                "next" in aria_label or "next" in class_attr or
                                "arrow" in class_attr):
                                if elem.is_displayed() and elem.is_enabled():
                                    disabled = elem.get_attribute("disabled")
                                    aria_disabled = elem.get_attribute("aria-disabled")
                                    
                                    if not disabled and aria_disabled != "true":
                                        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", elem)
                                        time.sleep(0.5)
                                        try:
                                            elem.click()
                                            return True
                                        except:
                                            driver.execute_script("arguments[0].click();", elem)
                                            return True
                        except:
                            continue
                except:
                    continue
        except:
            continue
    
    # If container search didn't work, try direct selectors
    for selector_type, selector_value in next_button_selectors:
        try:
            elements = driver.find_elements(selector_type, selector_value)
            for elem in elements:
                try:
                    # Check if element is visible and enabled
                    if elem.is_displayed() and elem.is_enabled():
                        # Check if it's not disabled
                        disabled = elem.get_attribute("disabled")
                        aria_disabled = elem.get_attribute("aria-disabled")
                        class_attr = elem.get_attribute("class") or ""
                        
                        if not disabled and aria_disabled != "true" and "disabled" not in class_attr.lower():
                            # Scroll element into view
                            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", elem)
                            time.sleep(0.5)
                            
                            # Try to click
                            try:
                                elem.click()
                                return True
                            except:
                                # If regular click fails, try JavaScript click
                                driver.execute_script("arguments[0].click();", elem)
                                return True
                except (StaleElementReferenceException, Exception):
                    continue
        except Exception:
            continue
    
    return False

def main():
    seen = load_seen()
    print(f"Loaded {len(seen)} seen listings.")
    print("Starting Centris property scan for purple/lavender ducks...")
    print("="*80)
    
    driver = init_driver()
    
    try:
        print(f"Loading search page: {SEARCH_PAGE}")
        driver.get(SEARCH_PAGE)
        time.sleep(5)  # Initial page load
        
        # Wait for initial content
        try:
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.TAG_NAME, "a"))
            )
        except TimeoutException:
            print("Warning: Page took too long to load initial content")
        
        page_count = 0
        all_seen_links = set(seen)
        search_page_url = driver.current_url  # Save the search page URL
        
        while page_count < MAX_PAGES:
            page_count += 1
            print(f"\n{'='*80}")
            print(f"=== PAGE {page_count}/{MAX_PAGES} ===")
            print(f"Current URL: {driver.current_url}")
            print(f"{'='*80}")
            
            # Scroll to top of page to ensure we start from the beginning
            driver.execute_script("window.scrollTo(0, 0);")
            time.sleep(0.5)  # Reduced wait
            
            # Extract currently visible listings on this page
            links = extract_listing_links_selenium(driver)
            new_links = set(links) - all_seen_links
            print(f"Total listings on page: {len(links)}, New listings: {len(new_links)}")
            
            # Add all detected listings to seen (even if we don't process them yet)
            # This ensures we track all listings that were detected
            for listing_url in links:
                if listing_url not in all_seen_links:
                    all_seen_links.add(listing_url)
            # Save immediately so we have a record of all detected listings
            save_seen(all_seen_links)
            
            # Process each new listing
            if new_links:
                for idx, listing_url in enumerate(new_links, 1):
                    print(f"\nProcessing [{idx}/{len(new_links)}]: {listing_url}")
                    imgs = extract_image_urls_from_listing(driver, listing_url)
                    total_images = len(imgs)
                    print(f"  Found {total_images} images")
                    
                    if not imgs:
                        print("  No images found, skipping...")
                        # Save with 0 images analyzed
                        save_analyzed_property(listing_url, 0)
                        save_seen(all_seen_links)
                        driver.get(search_page_url)
                        time.sleep(1)  # Reduced wait
                        continue
                    
                    # Limit images per property for speed
                    images_to_analyze = imgs[:MAX_IMAGES_PER_PROPERTY]
                    if total_images > MAX_IMAGES_PER_PROPERTY:
                        print(f"  ⚡ Limiting to first {MAX_IMAGES_PER_PROPERTY} images for speed")
                    
                    analyzed_count = 0
                    for img_idx, img_url in enumerate(images_to_analyze, 1):
                        fname = f"{abs(hash(img_url))}.jpg"
                        outp = os.path.join(IMAGES_DIR, fname)
                        
                        if not os.path.exists(outp):
                            if not download_image(img_url, outp):
                                continue
                        # Reduced output - only show every 5 images or on last
                        if img_idx % 5 == 0 or img_idx == len(images_to_analyze):
                            print(f"  [{img_idx}/{len(images_to_analyze)}] Processing...")
                        
                        try:
                            if detect_purple_blob_image(outp):
                                log_alert(listing_url, outp)
                            analyzed_count += 1
                        except Exception as e:
                            pass  # Skip errors silently for speed
                    
                    # Save analyzed property with image count
                    save_analyzed_property(listing_url, analyzed_count)
                    print(f"  ✓ Analyzed {analyzed_count} images")
                    
                    # Listing already added to all_seen_links above, just save
                    save_seen(all_seen_links)
                    
                    # Go back to search page before processing next listing
                    driver.get(search_page_url)
                    time.sleep(1)  # Reduced from 2 seconds
                    
                    # Wait for page to reload (reduced timeout)
                    try:
                        WebDriverWait(driver, 5).until(  # Reduced from 10
                            EC.presence_of_element_located((By.TAG_NAME, "a"))
                        )
                    except TimeoutException:
                        pass
                    
                    time.sleep(DELAY_SECONDS)
            else:
                print("No new listings on this page")
            
            # Save current page's links before clicking Next (for comparison)
            current_page_links = set(links)
            
            # Try to click "Next" button to go to next page
            # Note: find_and_click_next_button() will scroll to bottom first
            print("\nLooking for 'Next' button...")
            if find_and_click_next_button(driver):
                time.sleep(PAGE_LOAD_WAIT)
                
                # Wait for new page content to load (reduced timeout)
                try:
                    WebDriverWait(driver, 8).until(  # Reduced from 15
                        EC.presence_of_element_located((By.TAG_NAME, "a"))
                    )
                except TimeoutException:
                    pass
                
                # Update search page URL in case it changed
                current_url = driver.current_url
                if current_url != search_page_url:
                    search_page_url = current_url
                
                # Additional wait for dynamic content (reduced)
                time.sleep(1)  # Reduced from 2
                
                # Scroll to top of new page
                driver.execute_script("window.scrollTo(0, 0);")
                time.sleep(0.5)  # Reduced from 1
                
                # Log page info (but don't stop - continue until Next button not found)
                new_page_links = extract_listing_links_selenium(driver)
                new_page_links_set = set(new_page_links)
                new_unseen_listings = new_page_links_set - all_seen_links
                
                print(f"New page loaded: {len(new_page_links)} listings, {len(new_unseen_listings)} new")
                # Continue to next iteration - will stop only if Next button not found
            else:
                print("No 'Next' button found or it's disabled. Reached last page.")
                # Debug: Print pagination elements found
                try:
                    pagination_elements = driver.find_elements(By.CSS_SELECTOR, "[class*='pagination'], nav, [aria-label*='pagination']")
                    if pagination_elements:
                        print(f"Found {len(pagination_elements)} pagination-related elements")
                        for i, elem in enumerate(pagination_elements[:3]):  # Show first 3
                            try:
                                print(f"  Element {i+1}: tag={elem.tag_name}, class={elem.get_attribute('class')}, text={elem.text[:50]}")
                            except:
                                pass
                except:
                    pass
                break
        
        print("\n" + "="*80)
        print("SCAN COMPLETE!")
        print(f"Total listings detected: {len(all_seen_links)}")
        print(f"Total pages processed: {page_count}")
        print(f"\nAll detected listings saved to:")
        print(f"  - {SEEN_FILE} (JSON format)")
        print(f"  - {SEEN_FILE.replace('.json', '.txt')} (Human-readable text)")
        print(f"\nAnalyzed properties with image counts:")
        print(f"  - {ANALYZED_FILE} (Real-time log, chronological order)")
        print(f"  - {ANALYZED_FILE.replace('.txt', '_summary.txt')} (Sorted summary)")
        print(f"\nCheck {ALERTS_FILE} for purple duck alerts")
        print("="*80)
    
    except KeyboardInterrupt:
        print("\n\nScan interrupted by user. Saving progress...")
        save_seen(all_seen_links)
    except Exception as e:
        print(f"\nError during scan: {e}")
        import traceback
        traceback.print_exc()
    finally:
        driver.quit()
        print("Browser closed.")

if __name__ == "__main__":
    main()
