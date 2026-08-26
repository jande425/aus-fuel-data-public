#!/usr/bin/env python3
"""
Unified Australian Fuel Price Fetcher (NORMALIZED OUTPUT v4)

v4 FIX: Ensures state abbreviation is always included in address line for all states.

Fetches fuel prices from all available state APIs:
- NSW, TAS, ACT (via NSW FuelCheck API)
- QLD (via Queensland Fuel Prices API)
- VIC (via Service Victoria API)
- SA (via SA Fuel Pricing API)
- WA (via FuelWatch RSS)
- NT (via MyFuel NT API)

NORMALIZED OUTPUT FORMAT:
- All fuel types standardized to: E10, U91, U95, U98, Diesel, LPG
- Stations pre-joined with their prices (no separate prices array)
- All timestamps in ISO 8601 format
- Addresses include state abbreviation (QLD, SA, WA, etc.)
- Cleaner, smaller JSON optimized for mobile app consumption

Outputs:
- data/{state}/latest.json - Latest data for each state/territory (NORMALIZED)
- data/AUS/latest.json - Combined Australia-wide data (NORMALIZED)
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

# =============================================================================
# CONFIGURATION
# =============================================================================

# NSW/TAS/ACT API
NSW_BASE_URL = "https://api.onegov.nsw.gov.au"
NSW_FUEL_API_SECRET = os.environ.get("NSW_FUEL_API_SECRET")
NSW_FUEL_API_KEY = os.environ.get("NSW_FUEL_API_KEY")
NSW_FUEL_AUTH_HEADER = os.environ.get("NSW_FUEL_AUTH_HEADER")

# QLD API
QLD_BASE_URL = "https://fppdirectapi-prod.fuelpricesqld.com.au"
QLD_TOKEN = os.environ.get("QLD_TOKEN")

# VIC API
VIC_BASE_URL = "https://api.fuel.service.vic.gov.au/open-data/v1"
VIC_TOKEN = os.environ.get("VIC_TOKEN")

# SA API
SA_BASE_URL = "https://fppdirectapi-prod.safuelpricinginformation.com.au"
SA_TOKEN = os.environ.get("SA_TOKEN")

# WA FuelWatch RSS
WA_BASE_URL = "https://www.fuelwatch.wa.gov.au/fuelwatch/fuelWatchRSS"

# NT MyFuel API
NT_BASE_URL = "https://myfuelnt.nt.gov.au/api"
NT_TOKEN_URL = "https://myfuelnt.nt.gov.au/api/token"  # Token endpoint on same domain
NT_USERNAME = os.environ.get("NT_USERNAME")
NT_TOKEN = os.environ.get("NT_TOKEN")  # This is the password

# ACT suburbs for address-based detection
ACT_SUBURBS = {
    # Major districts
    'CANBERRA', 'BELCONNEN', 'WODEN', 'TUGGERANONG', 'GUNGAHLIN', 'WESTON CREEK',
    # Inner North
    'CIVIC', 'BRADDON', 'DICKSON', 'AINSLIE', 'HACKETT', 'WATSON', 'DOWNER',
    'LYNEHAM', 'OCONNOR', "O'CONNOR", 'TURNER', 'ACTON', 'REID', 'CAMPBELL',
    # Inner South  
    'KINGSTON', 'MANUKA', 'GRIFFITH', 'NARRABUNDAH', 'RED HILL', 'FORREST',
    'BARTON', 'PARKES', 'YARRALUMLA', 'DEAKIN', 'CURTIN', 'HUGHES', 'GARRAN',
    # Belconnen
    'BRUCE', 'KALEEN', 'GIRALANG', 'LAWSON', 'MACGREGOR', 'CHARNWOOD',
    'DUNLOP', 'FRASER', 'FLOREY', 'LATHAM', 'HIGGINS', 'HOLT', 'KIPPAX',
    'SCULLIN', 'PAGE', 'WEETANGERA', 'HAWKER', 'COOK', 'MACQUARIE', 'ARANDA',
    # Gungahlin
    'MITCHELL', 'FRANKLIN', 'HARRISON', 'AMAROO', 'BONNER', 'CASEY',
    'CRACE', 'FORDE', 'JACKA', 'MONCRIEFF', 'NGUNNAWAL', 'NICHOLLS',
    'PALMERSTON', 'TAYLOR', 'THROSBY', 'KENNY',
    # Woden
    'PHILLIP', 'LYONS', 'CHIFLEY', 'PEARCE', 'TORRENS', 'MAWSON',
    'FARRER', 'ISAACS', 'OMALLEY', "O'MALLEY",
    # Weston Creek
    'RIVETT', 'STIRLING', 'WARAMANGA', 'FISHER', 'CHAPMAN', 'DUFFY', 'HOLDER',
    # Tuggeranong
    'WANNIASSA', 'KAMBAH', 'GREENWAY', 'OXLEY', 'BONYTHON', 'GORDON',
    'CONDER', 'BANKS', 'CALWELL', 'CHISHOLM', 'GILMORE', 'ISABELLA PLAINS',
    'MONASH', 'RICHARDSON', 'THEODORE', 'MACARTHUR',
    # Other
    'FYSHWICK', 'PIALLIGO', 'MAJURA', 'SYMONSTON', 'HUME', 'OAKS ESTATE',
    'JERRABOMBERRA',  # Often considered ACT-adjacent
    # Jervis Bay Territory (administered by ACT)
    'JERVIS BAY',
}

# Browser-like headers for WA
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Accept": "application/rss+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.7",
    "Accept-Language": "en-AU,en;q=0.9",
}

# =============================================================================
# FUEL TYPE NORMALIZATION
# =============================================================================

# Map all state-specific fuel codes to standard codes
# ALL fuel types are kept, just normalized to consistent naming
FUEL_TYPE_MAP = {
    # === PRIMARY FUELS (most common) ===
    "E10": "E10",
    "U91": "U91",
    "U95": "U95",
    "U98": "U98",
    "LPG": "LPG",
    "Diesel": "Diesel",
    "Premium Diesel": "Premium Diesel",
    
    # QLD/SA numeric codes
    "2": "U91",      # "Unleaded" in QLD/SA
    
    # NSW/TAS/VIC premium codes
    "P95": "U95",
    "P98": "U98",
    
    # Diesel variations
    "DL": "Diesel",
    "DSL": "Diesel",
    
    # === PREMIUM DIESEL (kept separate) ===
    "PDL": "Premium Diesel",
    "PDSL": "Premium Diesel",
    "PD": "Premium Diesel",
    "PREMIUMDIESEL": "Premium Diesel",
    "PREMIUM DIESEL": "Premium Diesel",
    
    # === ETHANOL FUELS ===
    "E85": "E85",
    "19": "E85",        # QLD/SA code for E85
    
    # === BIODIESEL ===
    "B20": "B20",
    
    # === ALTERNATIVE FUELS ===
    "EV": "EV",         # EV Charging
    "LNG": "LNG",       # Liquefied Natural Gas
    "CNG": "CNG",       # Compressed Natural Gas
    "H2": "H2",         # Hydrogen
    
    # === OTHER ===
    "21": "Opal",       # QLD/SA Opal fuel
    "11": "LRP",        # Lead Replacement Petrol
    "6": "ULSD",        # Ultra Low Sulfur Diesel
    "13": "E5",         # Premium E5
}

# All fuel types (used for cheapest lists and app UI)
ALL_FUEL_TYPES = [
    "E10", "U91", "U95", "U98", "Diesel", "LPG",  # Common
    "Premium Diesel", "E85", "B20",                 # Less common
    "EV", "LNG", "CNG", "H2", "Opal", "LRP", "ULSD", "E5", "LAF"  # Specialty (LAF = Low Aromatic Fuel)
]

# =============================================================================
# BRAND NORMALIZATION
# =============================================================================

# Map variant brand names to canonical names
# Key: lowercase variant, Value: canonical name
BRAND_NORMALIZATION = {
    # 7-Eleven variants
    "7 eleven": "7-Eleven",
    "7eleven": "7-Eleven",
    
    # Ampol variants (keep sub-brands separate, just normalize case)
    "ampol": "Ampol",
    
    # Apco variants
    "apco": "Apco",
    "apco service stations": "Apco",
    
    # Astron variants
    "astron": "Astron",
    
    # Atlas variants
    "atlas": "Atlas Fuel",
    "atlas fuel": "Atlas Fuel",
    
    # Bennetts Petroleum variants
    "bennetts petroleum": "Bennetts Petroleum",
    "ampol bennetts petroleum": "Bennetts Petroleum",
    
    # Bowser Bean variants
    "bowser bean": "Bowser Bean",
    "bp bowser bean": "Bowser Bean",
    "shell bowser bean": "Bowser Bean",
    
    # IOR variants
    "ior": "IOR",
    "ior group": "IOR",
    "ior pty ltd": "IOR",
    "ior pty": "IOR",
    
    # Lowes variants
    "lowes": "Lowes",
    "lowes petroleum bp": "Lowes",
    "lowes petroleum": "Lowes",
    
    # Metro variants (keep Fuel vs Petroleum separate as they may be different companies)
    "metro fuel": "Metro Fuel",
    "metro petroleum": "Metro Petroleum",
    
    # Mobil variants
    "mobil": "Mobil",
    "mobil 1 carlingford car care": "Mobil",
    
    # OMG variants
    "omg caltex": "OMG",
    "omg metro": "OMG",
    
    # On the Run variants
    "on the run": "On the Run",
    "on therun": "On the Run",
    "ontherun": "On the Run",
    
    # Puma variants
    "puma": "Puma Energy",
    "puma energy": "Puma Energy",
    
    # Solo variants
    "solo": "Solo",
    
    # Tas Petroleum variants
    "tas petroleum": "Tas Petroleum",
    "tas petroleum caltex": "Tas Petroleum",
    "tas petroleum shell": "Tas Petroleum",
    
    # U-Go variants
    "ugo": "U-Go",
    "u-go": "U-Go",
    "u go": "U-Go",
    
    # Shell Coles Express -> Coles Express (commonly known as)
    "shell coles express": "Coles Express",
    
    # Reddy Express variants
    "shell reddy express": "Reddy Express",
    "reddy express": "Reddy Express",
    "c3": "Reddy Express",  # NT code
    
    # EG Ampol
    "eg ampol": "EG Ampol",
}


def normalize_brand(brand: str) -> str:
    """
    Normalize brand name to canonical form.
    Returns the normalized brand name.
    """
    if not brand:
        return ""
    
    brand_stripped = brand.strip()
    brand_lower = brand_stripped.lower()
    
    # Check for exact match in normalization map
    if brand_lower in BRAND_NORMALIZATION:
        return BRAND_NORMALIZATION[brand_lower]
    
    # Return original with proper casing if not in map
    return brand_stripped


def title_case_address(address: str) -> str:
    """
    Convert address to proper title case, handling special cases.
    e.g., "123 MAIN STREET, SYDNEY NSW 2000" -> "123 Main Street, Sydney NSW 2000"
    """
    if not address:
        return ""
    
    # Check if address is mostly uppercase (more than 70% uppercase letters)
    letters = [c for c in address if c.isalpha()]
    if not letters:
        return address
    
    uppercase_ratio = sum(1 for c in letters if c.isupper()) / len(letters)
    if uppercase_ratio < 0.7:
        # Address is not all caps, return as-is
        return address
    
    # Words that should stay uppercase
    uppercase_words = {'NSW', 'VIC', 'QLD', 'SA', 'WA', 'TAS', 'NT', 'ACT', 'PO', 'GPO', 'RMB', 'RSD', 'PO BOX'}
    
    # Words that should stay lowercase (unless at start)
    lowercase_words = {'of', 'the', 'and', 'in', 'on', 'at', 'to', 'for', 'a', 'an'}
    
    # Common abbreviations that should stay uppercase
    abbreviations = {'CBD', 'RD', 'ST', 'AVE', 'DR', 'PL', 'CT', 'CL', 'TCE', 'HWY', 'FWY', 'BLVD', 'LN', 'WAY', 'CRS', 'CCT', 'GR', 'PDE', 'SQ'}
    
    words = address.split()
    result = []
    
    for i, word in enumerate(words):
        word_upper = word.upper().rstrip('.,;:')
        
        # Check if it's a state abbreviation or special word
        if word_upper in uppercase_words:
            result.append(word.upper())
        # Check if it's a common abbreviation
        elif word_upper in abbreviations:
            result.append(word.upper())
        # Check if it's a postcode (4 digits)
        elif word.isdigit() and len(word) == 4:
            result.append(word)
        # Check if it's a street number or unit number
        elif word.replace('/', '').replace('-', '').isdigit():
            result.append(word)
        # Check if lowercase word (but not at start)
        elif word_upper.lower() in lowercase_words and i > 0:
            result.append(word.lower())
        # Otherwise title case
        else:
            # Handle words with apostrophes like O'CONNOR
            if "'" in word:
                parts = word.split("'")
                result.append("'".join(p.capitalize() for p in parts))
            # Handle hyphenated words
            elif "-" in word:
                parts = word.split("-")
                result.append("-".join(p.capitalize() for p in parts))
            else:
                result.append(word.capitalize())
    
    return ' '.join(result)


def normalize_fuel_type(raw_code: str) -> str | None:
    """
    Normalize a fuel type code to standard format.
    Returns the normalized code, or the original if unknown.
    Returns None only if the input is empty.
    """
    if not raw_code:
        return None
    
    code = str(raw_code).strip()
    code_upper = code.upper()
    
    # Direct lookup (case-insensitive)
    if code_upper in FUEL_TYPE_MAP:
        return FUEL_TYPE_MAP[code_upper]
    if code in FUEL_TYPE_MAP:
        return FUEL_TYPE_MAP[code]
    
    # Return original code if not in map (preserve unknown types)
    # Clean it up a bit
    return code_upper if code_upper else code


def normalize_timestamp(ts: str, state: str) -> str:
    """
    Normalize various timestamp formats to ISO 8601.
    Returns empty string if parsing fails.
    """
    if not ts:
        return ""
    
    ts = str(ts).strip()
    
    # Already ISO 8601 with Z
    if ts.endswith("Z"):
        return ts
    
    # Try various formats
    formats = [
        # ISO 8601 variants
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        # NSW/TAS format: DD/MM/YYYY HH:MM:SS AM/PM
        "%d/%m/%Y %I:%M:%S %p",
        "%d/%m/%Y %H:%M:%S",
        # Date only
        "%Y-%m-%d",
    ]
    
    for fmt in formats:
        try:
            dt = datetime.strptime(ts.split("+")[0].split("Z")[0], fmt)
            return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            continue
    
    # Return original if can't parse
    return ts


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def stable_station_code(state: str, name: str, address: str, lat: str, lng: str) -> str:
    """Generate a stable station code from station details."""
    raw = f"{state}|{name}|{address}|{lat}|{lng}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def safe_float(x) -> float | None:
    """Safely convert to float."""
    if x is None:
        return None
    try:
        return float(str(x).replace("$", "").replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def detect_state_from_address(address: str) -> str | None:
    """
    Detect state from address string.
    Priority: 
    1. Explicit state abbreviation in address (e.g., " ACT", " NSW", " TAS")
    2. ACT suburb names (only if NSW not mentioned)
    3. Postcode prefix as fallback
    """
    if not address:
        return None
    
    addr_upper = address.upper()
    
    # 1. Check for explicit state abbreviation in address (highest priority)
    has_nsw = ' NSW ' in addr_upper or addr_upper.endswith(' NSW') or ', NSW' in addr_upper or ' NSW,' in addr_upper
    has_act = ' ACT ' in addr_upper or addr_upper.endswith(' ACT') or ', ACT' in addr_upper or ' ACT,' in addr_upper
    has_tas = ' TAS ' in addr_upper or addr_upper.endswith(' TAS') or ', TAS' in addr_upper or ' TAS,' in addr_upper
    
    # If explicit state found, use it (NSW takes priority over ACT suburb matching)
    if has_act:
        return 'ACT'
    if has_tas:
        return 'TAS'
    if has_nsw:
        return 'NSW'
    
    # 2. Check for ACT suburb names in address (only if no explicit state found)
    # This prevents "123 Main St, Fyshwick NSW 2609" from being tagged as ACT
    for suburb in ACT_SUBURBS:
        # Check for suburb as whole word (with word boundaries)
        if f' {suburb} ' in addr_upper or f' {suburb},' in addr_upper or addr_upper.endswith(f' {suburb}'):
            return 'ACT'
    
    # 3. Fallback to postcode prefix detection
    postcode_match = re.search(r'\b(\d{4})\b', address)
    if postcode_match:
        postcode = postcode_match.group(1)
        if postcode.startswith('7'):
            return 'TAS'
        if postcode.startswith('2'):
            return 'NSW'  # Default 2xxx to NSW (ACT should be caught above)
    
    return None


def extract_list(payload, preferred_keys):
    """Extract list from API response that may be wrapped in dict."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for k in preferred_keys:
            if k in payload and isinstance(payload[k], list):
                return payload[k]
        for v in payload.values():
            if isinstance(v, list):
                return v
    return []


# =============================================================================
# NSW/TAS/ACT FETCHER
# =============================================================================

def fetch_nsw_tas_act() -> dict[str, Any]:
    """Fetch data from NSW FuelCheck API (includes TAS and ACT)."""
    print("\n[NSW/TAS/ACT] Fetching from NSW FuelCheck API...")
    
    if not NSW_FUEL_API_KEY or not NSW_FUEL_AUTH_HEADER:
        print("  WARNING: NSW API credentials not set, skipping...")
        return {"stations": [], "prices": []}
    
    try:
        # Get access token
        auth_url = f"{NSW_BASE_URL}/oauth/client_credential/accesstoken"
        auth_response = requests.get(
            auth_url,
            params={"grant_type": "client_credentials"},
            headers={"Authorization": NSW_FUEL_AUTH_HEADER}
        )
        auth_response.raise_for_status()
        access_token = auth_response.json()["access_token"]
        
        # Common headers
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=utf-8",
            "apikey": NSW_FUEL_API_KEY,
            "transactionid": str(uuid.uuid4()),
            "requesttimestamp": datetime.now(timezone.utc).strftime("%d/%m/%Y %I:%M:%S %p")
        }
        
        # Fetch prices
        prices_response = requests.get(
            f"{NSW_BASE_URL}/FuelPriceCheck/v2/fuel/prices",
            params={"states": "NSW|TAS"},
            headers=headers
        )
        prices_response.raise_for_status()
        prices = prices_response.json().get("prices", [])
        
        # Fetch reference data
        headers["if-modified-since"] = "01/01/2000 12:00:00 AM"
        headers["transactionid"] = str(uuid.uuid4())
        ref_response = requests.get(
            f"{NSW_BASE_URL}/FuelCheckRefData/v2/fuel/lovs",
            params={"states": "NSW|TAS"},
            headers=headers
        )
        ref_response.raise_for_status()
        ref_data = ref_response.json()
        
        stations = ref_data.get("stations", {}).get("items", [])
              
        # Step 1: Detect correct state (ACT/TAS/NSW) for each station based on address
        for station in stations:
            detected = detect_state_from_address(station.get("address", ""))
            if detected:
                station["state"] = detected

        # Step 2: Build map from ORIGINAL station code -> CORRECTED state
        # This is AFTER we've detected ACT, so ACT stations will have state="ACT"
        station_state_map = {}
        for s in stations:
            orig_code = s.get("code")
            corrected_state = s.get("state")
            if orig_code and corrected_state:
                # Convert to string for consistent matching
                station_state_map[str(orig_code)] = corrected_state

        # Step 3: Apply CORRECTED state to each price
        # The price's stationcode is an INTEGER, station code is STRING
        # Convert price stationcode to string for matching
        for p in prices:
            orig_stationcode = p.get("stationcode")
            # Convert to string for matching!
            stationcode_str = str(orig_stationcode) if orig_stationcode is not None else None
            
            if stationcode_str and stationcode_str in station_state_map:
                p["state"] = station_state_map[stationcode_str]
            else:
                # Default fallback
                if not p.get("state"):
                    p["state"] = "NSW"

        # Step 4: Prefix station codes with their CORRECTED state
        for s in stations:
            orig_code = s.get("code")
            corrected_state = s.get("state")
            if orig_code and corrected_state:
                s["code"] = f"{corrected_state}:{orig_code}"
            s.pop("brandid", None)
            s.pop("stationid", None) 

        # Step 5: Prefix price stationcodes with the CORRECTED state
        # Convert integer stationcode to string first
        for p in prices:
            orig_stationcode = p.get("stationcode")
            corrected_state = p.get("state")
            if orig_stationcode is not None and corrected_state:
                p["stationcode"] = f"{corrected_state}:{orig_stationcode}"
                   
        # Count by state (for debugging)
        state_counts = {}
        for s in stations:
            state = s.get('state', 'Unknown')
            state_counts[state] = state_counts.get(state, 0) + 1
        
        price_state_counts = {}
        for p in prices:
            state = p.get('state', 'Unknown')
            price_state_counts[state] = price_state_counts.get(state, 0) + 1
            
        print(f"  Stations: {state_counts}")
        print(f"  Prices by state: {price_state_counts}")
        print(f"  Total prices: {len(prices)}")
        
        return {
            "stations": stations,
            "prices": prices
        }
        
    except Exception as e:
        print(f"  ERROR: {e}")
        import traceback
        traceback.print_exc()
        return {"stations": [], "prices": []}


# =============================================================================
# QLD FETCHER
# =============================================================================

def fetch_qld() -> dict[str, Any]:
    """Fetch data from Queensland Fuel Prices API."""
    print("\n[QLD] Fetching from Queensland Fuel Prices API...")
    
    if not QLD_TOKEN:
        print("  WARNING: QLD_TOKEN not set, skipping...")
        return {"stations": [], "prices": []}
    
    try:
        headers = {
            "Authorization": f"FPDAPI SubscriberToken={QLD_TOKEN}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Aus-Fuel-Data/1.0",
        }
        
        params = {"countryId": 21, "geoRegionLevel": 3, "geoRegionId": 1}
        
        # Fetch brands
        brands_response = requests.get(
            f"{QLD_BASE_URL}/Subscriber/GetCountryBrands",
            params={"countryId": 21},
            headers=headers,
            timeout=60
        )
        brands_response.raise_for_status()
        brands_data = brands_response.json()
        
        brand_lookup = {}
        for b in extract_list(brands_data, ["Brands", "brands"]):
            if isinstance(b, dict) and b.get("BrandId"):
                brand_lookup[b["BrandId"]] = b.get("Name", "")
        
        # Fetch fuel types
        fuels_response = requests.get(
            f"{QLD_BASE_URL}/Subscriber/GetCountryFuelTypes",
            params={"countryId": 21},
            headers=headers,
            timeout=60
        )
        fuels_response.raise_for_status()
        fuels_data = fuels_response.json()
        
        fuel_lookup = {}
        for ft in extract_list(fuels_data, ["FuelTypes", "fuelTypes"]):
            if isinstance(ft, dict) and ft.get("FuelId"):
                fid = ft["FuelId"]
                name = (ft.get("Name", "") or "").upper()
                if "E10" in name:
                    code = "E10"
                elif "UNLEADED 91" in name or name.strip() == "U91":
                    code = "U91"
                elif "UNLEADED 95" in name or "95" in name:
                    code = "U95"
                elif "UNLEADED 98" in name or "98" in name:
                    code = "U98"
                elif "PREMIUM DIESEL" in name:
                    code = "Premium Diesel"
                elif "DIESEL" in name:
                    code = "Diesel"
                elif "LPG" in name:
                    code = "LPG"
                else:
                    code = str(fid)
                fuel_lookup[int(fid)] = code
        
        # Fetch sites
        sites_response = requests.get(
            f"{QLD_BASE_URL}/Subscriber/GetFullSiteDetails",
            params=params,
            headers=headers,
            timeout=60
        )
        sites_response.raise_for_status()
        sites_data = sites_response.json()
        
        # Fetch prices
        prices_response = requests.get(
            f"{QLD_BASE_URL}/Price/GetSitesPrices",
            params=params,
            headers=headers,
            timeout=60
        )
        prices_response.raise_for_status()
        prices_data = prices_response.json()
        
        # Transform stations
        stations = []
        site_list = sites_data.get("S", []) if isinstance(sites_data, dict) else sites_data
        for site in site_list:
            if not isinstance(site, dict):
                continue
            brand_id = site.get("B")
            sid = str(site.get("S", ""))
            
            # QLD API fields: A=Address, P=Postcode, N=Name
            street = site.get('A', '')
            postcode = site.get('P', '')
            name = site.get("N", "")
            
            # Simple: just ensure QLD is in the address
            address = f"{street}, QLD {postcode}".strip(", ")
            
            stations.append({
                "brand": brand_lookup.get(brand_id, ""),
                "code": f"QLD:{sid}",
                "name": name,
                "address": address,
                "location": {
                    "latitude": site.get("Lat"),
                    "longitude": site.get("Lng")
                },
                "state": "QLD"
            })
        
        # Transform prices
        prices = []
        unavailable_fuels = []
        price_list = extract_list(prices_data, ["SitePrices", "Prices"])
        for p in price_list:
            if not isinstance(p, dict):
                continue
            raw_price = p.get("Price", 0)
            fuel_id = p.get("FuelId")
            fuel_code = fuel_lookup.get(int(fuel_id), str(fuel_id)) if fuel_id else ""
            site_id = str(p.get("SiteId", ""))
            if raw_price == 9999:
                unavailable_fuels.append({
                    "stationcode": f"QLD:{site_id}",
                    "state": "QLD",
                    "fueltype": fuel_code,
                })
                continue
            prices.append({
                "stationcode": f"QLD:{site_id}",
                "state": "QLD",
                "fueltype": fuel_code,
                "price": raw_price / 10.0,
                "lastupdated": p.get("TransactionDateUtc", "")
            })

        print(f"  Stations: {len(stations)}, Prices: {len(prices)}, Unavailable: {len(unavailable_fuels)}")

        return {"stations": stations, "prices": prices, "unavailable_fuels": unavailable_fuels}
        
    except Exception as e:
        print(f"  ERROR: {e}")
        return {"stations": [], "prices": []}


# =============================================================================
# VIC FETCHER
# =============================================================================

def fetch_vic() -> dict[str, Any]:
    """Fetch data from Victoria Fuel API."""
    print("\n[VIC] Fetching from Service Victoria API...")
    
    if not VIC_TOKEN:
        print("  WARNING: VIC_TOKEN not set, skipping...")
        return {"stations": [], "prices": []}
    
    try:
        def get_headers():
            return {
                "User-Agent": "FuelDashboard/1.0",
                "x-consumer-id": VIC_TOKEN,
                "x-transactionid": str(uuid.uuid4()),
                "Accept": "application/json"
            }
        
        # Fetch brands
        brands_response = requests.get(f"{VIC_BASE_URL}/fuel/reference-data/brands", headers=get_headers())
        brands_response.raise_for_status()
        raw_brands = brands_response.json()
        brands_list = raw_brands.get("brands", []) if isinstance(raw_brands, dict) else raw_brands
        brand_lookup = {b.get("id", ""): b.get("name", "") for b in brands_list if isinstance(b, dict)}
        
        # Fetch prices (includes station data)
        prices_response = requests.get(f"{VIC_BASE_URL}/fuel/prices", headers=get_headers())
        prices_response.raise_for_status()
        raw_prices = prices_response.json()
        price_details = raw_prices.get("fuelPriceDetails", []) if isinstance(raw_prices, dict) else raw_prices
        
        stations = []
        prices = []
        unavailable_fuels = []
        seen_stations = set()

        for item in price_details:
            if not isinstance(item, dict):
                continue
            station_info = item.get("fuelStation", {})
            station_id = station_info.get("id", "")

            if station_id and station_id not in seen_stations:
                seen_stations.add(station_id)
                loc = station_info.get("location", {})
                brand_id = station_info.get("brandId", "")
                stations.append({
                    "brand": brand_lookup.get(brand_id, ""),
                    "code": f"VIC:{station_id}",
                    "name": station_info.get("name", ""),
                    "address": f"{station_info.get('address', '')}, {station_info.get('suburb', '')}, {station_info.get('postcode', '')}".strip(", "),
                    "location": {
                        "latitude": loc.get("latitude"),
                        "longitude": loc.get("longitude")
                    },
                    "state": "VIC"
                })

            for fp in item.get("fuelPrices", []):
                fuel_type = fp.get("fuelType", "")
                is_available = fp.get("isAvailable", True)
                if is_available is False:
                    unavailable_fuels.append({
                        "stationcode": f"VIC:{station_id}",
                        "state": "VIC",
                        "fueltype": fuel_type,
                    })
                    continue
                price_val = safe_float(fp.get("price"))
                if price_val and price_val > 0:
                    prices.append({
                        "stationcode": f"VIC:{station_id}",
                        "state": "VIC",
                        "fueltype": fuel_type,
                        "price": price_val,
                        "lastupdated": fp.get("updatedAt", "")
                    })

        print(f"  Stations: {len(stations)}, Prices: {len(prices)}, Unavailable: {len(unavailable_fuels)}")

        return {"stations": stations, "prices": prices, "unavailable_fuels": unavailable_fuels}
        
    except Exception as e:
        print(f"  ERROR: {e}")
        import traceback
        traceback.print_exc()
        return {"stations": [], "prices": []}


# =============================================================================
# SA FETCHER
# =============================================================================

def fetch_sa() -> dict[str, Any]:
    """Fetch data from South Australia Fuel Pricing API."""
    print("\n[SA] Fetching from SA Fuel Pricing API...")
    
    if not SA_TOKEN:
        print("  WARNING: SA_TOKEN not set, skipping...")
        return {"stations": [], "prices": []}
    
    try:
        headers = {
            "Authorization": f"FPDAPI SubscriberToken={SA_TOKEN}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Aus-Fuel-Data/1.0",
        }
        
        params = {"countryId": 21, "geoRegionLevel": 3, "geoRegionId": 4}
        
        # Fetch brands
        brands_response = requests.get(
            f"{SA_BASE_URL}/Subscriber/GetCountryBrands",
            params={"countryId": 21},
            headers=headers,
            timeout=60
        )
        brands_response.raise_for_status()
        brands_data = brands_response.json()
        
        brand_lookup = {}
        for b in extract_list(brands_data, ["Brands", "brands"]):
            if isinstance(b, dict) and b.get("BrandId"):
                brand_lookup[b["BrandId"]] = b.get("Name", "")
        
        # Fetch fuel types
        fuels_response = requests.get(
            f"{SA_BASE_URL}/Subscriber/GetCountryFuelTypes",
            params={"countryId": 21},
            headers=headers,
            timeout=60
        )
        fuels_response.raise_for_status()
        fuels_data = fuels_response.json()
        
        fuel_lookup = {}
        for ft in extract_list(fuels_data, ["FuelTypes", "fuelTypes"]):
            if isinstance(ft, dict) and ft.get("FuelId"):
                fid = ft["FuelId"]
                name = (ft.get("Name", "") or "").upper()
                if "E10" in name:
                    code = "E10"
                elif "UNLEADED 91" in name:
                    code = "U91"
                elif "95" in name:
                    code = "U95"
                elif "98" in name:
                    code = "U98"
                elif "PREMIUM DIESEL" in name:
                    code = "Premium Diesel"
                elif "DIESEL" in name:
                    code = "Diesel"
                elif "LPG" in name:
                    code = "LPG"
                else:
                    code = str(fid)
                fuel_lookup[int(fid)] = code
        
        # Fetch sites
        sites_response = requests.get(
            f"{SA_BASE_URL}/Subscriber/GetFullSiteDetails",
            params=params,
            headers=headers,
            timeout=60
        )
        sites_response.raise_for_status()
        sites_data = sites_response.json()
        
        # Fetch prices
        prices_response = requests.get(
            f"{SA_BASE_URL}/Price/GetSitesPrices",
            params=params,
            headers=headers,
            timeout=60
        )
        prices_response.raise_for_status()
        prices_data = prices_response.json()
        
        # Transform stations
        stations = []
        site_list = sites_data.get("S", []) if isinstance(sites_data, dict) else sites_data
        for site in site_list:
            if not isinstance(site, dict):
                continue
            brand_id = site.get("B")
            sid = str(site.get("S", ""))
            
            # SA API fields: A=Address, P=Postcode, N=Name
            street = site.get('A', '')
            postcode = site.get('P', '')
            name = site.get("N", "")
            
            # Simple: just ensure SA is in the address
            address = f"{street}, SA {postcode}".strip(", ")
            
            stations.append({
                "brand": brand_lookup.get(brand_id, ""),
                "code": f"SA:{sid}",
                "name": name,
                "address": address,
                "location": {
                    "latitude": site.get("Lat"),
                    "longitude": site.get("Lng")
                },
                "state": "SA"
            })
        
        # Transform prices
        prices = []
        unavailable_fuels = []
        price_list = extract_list(prices_data, ["SitePrices", "Prices"])
        for p in price_list:
            if not isinstance(p, dict):
                continue
            raw_price = p.get("Price", 0)
            fuel_id = p.get("FuelId")
            fuel_code = fuel_lookup.get(int(fuel_id), str(fuel_id)) if fuel_id else ""
            site_id = str(p.get("SiteId", ""))
            if raw_price == 9999:
                unavailable_fuels.append({
                    "stationcode": f"SA:{site_id}",
                    "state": "SA",
                    "fueltype": fuel_code,
                })
                continue
            prices.append({
                "stationcode": f"SA:{site_id}",
                "state": "SA",
                "fueltype": fuel_code,
                "price": raw_price / 10.0,
                "lastupdated": p.get("TransactionDateUtc", "")
            })

        print(f"  Stations: {len(stations)}, Prices: {len(prices)}, Unavailable: {len(unavailable_fuels)}")

        return {"stations": stations, "prices": prices, "unavailable_fuels": unavailable_fuels}
        
    except Exception as e:
        print(f"  ERROR: {e}")
        return {"stations": [], "prices": []}


# =============================================================================
# WA FETCHER
# =============================================================================

def fetch_wa() -> dict[str, Any]:
    """Fetch data from WA FuelWatch RSS."""
    print("\n[WA] Fetching from FuelWatch RSS...")
    
    # WA FuelWatch product IDs mapped to standard fuel types
    # See: https://www.fuelwatch.wa.gov.au/fuelwatch/fuelWatchRSS
    WA_PRODUCTS = {
        "U91": 1,           # Unleaded Petrol
        "U95": 2,           # Premium Unleaded
        "Diesel": 4,        # Diesel
        "LPG": 5,           # LPG
        "U98": 6,           # 98 RON
        "E85": 10,          # E85
        "PremiumDiesel": 11, # Brand Diesel
    }
    
    try:
        session = requests.Session()
        session.headers.update(BROWSER_HEADERS)
        
        stations_by_code = {}
        prices = []
        
        for fueltype, product_id in WA_PRODUCTS.items():
            params = {"Product": str(product_id), "Day": "today"}

            for attempt in range(3):
                try:
                    response = session.get(WA_BASE_URL, params=params, timeout=60)
                    if response.status_code == 403:
                        wait = (attempt + 1) * 3
                        print(f"    WARNING: FuelWatch returned 403 for {fueltype} (attempt {attempt+1}/3), retrying in {wait}s...")
                        time.sleep(wait)
                        continue
                    response.raise_for_status()
                    break
                except Exception as e:
                    wait = (attempt + 1) * 3
                    print(f"    WARNING: FuelWatch error for {fueltype} (attempt {attempt+1}/3): {e}, retrying in {wait}s...")
                    time.sleep(wait)
            else:
                print(f"    ERROR: Failed to fetch {fueltype} (Product {product_id}) after 3 attempts — skipping")
                continue
            
            root = ET.fromstring(response.content)
            channel = root.find("channel")
            if channel is None:
                continue
            
            for item in channel.findall("item"):
                data = {child.tag: (child.text or "").strip() for child in item}
                
                name = data.get("trading-name") or data.get("title") or ""
                if not data.get("trading-name") and ": " in name:
                    name = name.split(": ", 1)[1].strip()
                
                brand = data.get("brand", "")
                address = data.get("address", "")
                suburb = data.get("location", "")  # WA provides suburb in "location" field
                lat_s = data.get("latitude", "")
                lng_s = data.get("longitude", "")
                price_s = data.get("price", "")
                date_s = data.get("date", "")
                
                # Simple: build address with suburb and ensure WA is included
                if suburb:
                    norm_address = f"{address}, {suburb} WA".strip(", ")
                else:
                    norm_address = f"{address}, WA".strip(", ")
                
                code = f"WA:{stable_station_code('WA', name, norm_address, lat_s, lng_s)}"
                
                if code not in stations_by_code:
                    station_obj = {
                        "brand": brand,
                        "code": code,
                        "name": name,
                        "address": norm_address,
                        "location": {
                            "latitude": safe_float(lat_s),
                            "longitude": safe_float(lng_s)
                        },
                        "state": "WA"
                    }

                    # Extract phone number (WA-specific)
                    phone = data.get("phone", "").strip()
                    if phone:
                        station_obj["phone"] = phone

                    # Extract site-features: amenities + opening hours (WA-specific)
                    # Format: "Fuel Cards ATM Toilets EFTPOS Air, Open 24 hours"
                    # or "Fuel Cards ATM, Open Mon-Fri: 05:00-22:00, Sat: 06:00-21:00"
                    site_features = data.get("site-features", "").strip()
                    if site_features:
                        open_idx = site_features.find("Open")
                        if open_idx >= 0:
                            amenities_str = site_features[:open_idx].strip().rstrip(",").strip()
                            hours_str = site_features[open_idx:].strip()
                            if amenities_str:
                                station_obj["features"] = amenities_str
                            if hours_str:
                                station_obj["openingHours"] = hours_str
                        else:
                            station_obj["features"] = site_features

                    stations_by_code[code] = station_obj
                
                price_val = safe_float(price_s)
                if price_val:
                    lastupdated = f"{date_s}T00:00:00Z" if date_s else datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    prices.append({
                        "stationcode": code,
                        "state": "WA",
                        "fueltype": fueltype,
                        "price": price_val,
                        "lastupdated": lastupdated
                    })
        
        # Fetch tomorrow's prices (available after 2:30 PM AWST = 06:30 UTC)
        utc_now = datetime.now(timezone.utc)
        awst_hour = (utc_now.hour + 8) % 24  # AWST = UTC+8
        if awst_hour >= 14 and (awst_hour > 14 or utc_now.minute >= 30):
            print("  [WA] Fetching tomorrow's prices (available after 2:30 PM AWST)...")
            tomorrow_count = 0
            for fueltype, product_id in WA_PRODUCTS.items():
                params = {"Product": str(product_id), "Day": "tomorrow"}
                for attempt in range(3):
                    try:
                        response = session.get(WA_BASE_URL, params=params, timeout=60)
                        if response.status_code == 403:
                            wait = (attempt + 1) * 3
                            print(f"    WARNING: FuelWatch tomorrow 403 for {fueltype} (attempt {attempt+1}/3), retrying in {wait}s...")
                            time.sleep(wait)
                            continue
                        response.raise_for_status()
                        break
                    except Exception as e:
                        wait = (attempt + 1) * 3
                        print(f"    WARNING: FuelWatch tomorrow error for {fueltype} (attempt {attempt+1}/3): {e}, retrying in {wait}s...")
                        time.sleep(wait)
                else:
                    continue

                root = ET.fromstring(response.content)
                channel = root.find("channel")
                if channel is None:
                    continue

                for item in channel.findall("item"):
                    data = {child.tag: (child.text or "").strip() for child in item}

                    name = data.get("trading-name") or data.get("title") or ""
                    if not data.get("trading-name") and ": " in name:
                        name = name.split(": ", 1)[1].strip()

                    address = data.get("address", "")
                    suburb = data.get("location", "")
                    lat_s = data.get("latitude", "")
                    lng_s = data.get("longitude", "")
                    price_s = data.get("price", "")

                    if suburb:
                        norm_address = f"{address}, {suburb} WA".strip(", ")
                    else:
                        norm_address = f"{address}, WA".strip(", ")

                    code = f"WA:{stable_station_code('WA', name, norm_address, lat_s, lng_s)}"
                    price_val = safe_float(price_s)

                    if code in stations_by_code and price_val:
                        if "tomorrowPrices" not in stations_by_code[code]:
                            stations_by_code[code]["tomorrowPrices"] = {}
                        normalized_fuel = normalize_fuel_type(fueltype) or fueltype
                        stations_by_code[code]["tomorrowPrices"][normalized_fuel] = price_val
                        tomorrow_count += 1

            print(f"  [WA] Tomorrow's prices: {tomorrow_count} entries")
        else:
            print("  [WA] Tomorrow's prices not yet available (released at 2:30 PM AWST)")

        stations = list(stations_by_code.values())
        print(f"  Stations: {len(stations)}, Prices: {len(prices)}")

        return {"stations": stations, "prices": prices}
        
    except Exception as e:
        print(f"  ERROR: {e}")
        return {"stations": [], "prices": []}


# =============================================================================
# NT FETCHER
# =============================================================================

def fetch_nt() -> dict[str, Any]:
    """
    Fetch data from Northern Territory MyFuel API.
    
    API Flow:
    1. POST to /api/token with username/password to get Bearer token
    2. GET /api/v1/getReferenceData for outlets, brands, and fuel codes
    3. POST /api/v1/getFuelPrice/fuelOutletIdentifier for prices (max 10 per request)
    """
    print("\n[NT] Fetching from MyFuel NT API...")
    
    if not NT_USERNAME or not NT_TOKEN:
        missing = []
        if not NT_USERNAME:
            missing.append("NT_USERNAME")
        if not NT_TOKEN:
            missing.append("NT_TOKEN")
        print(f"  WARNING: Missing environment variables: {', '.join(missing)}")
        print(f"  Ensure these are set in GitHub Secrets and passed to the workflow.")
        return {"stations": [], "prices": []}
    
    try:
        # Step 1: Get access token
        print("  Authenticating...")
        auth_response = requests.post(
            NT_TOKEN_URL,
            data={
                "grant_type": "password",
                "username": NT_USERNAME,
                "password": NT_TOKEN
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30
        )
        
        if auth_response.status_code == 400:
            error_data = auth_response.json()
            print(f"  ERROR: Authentication failed - {error_data.get('error_description', error_data.get('error', 'Unknown error'))}")
            return {"stations": [], "prices": []}
        
        auth_response.raise_for_status()
        auth_data = auth_response.json()
        access_token = auth_data.get("access_token")
        
        if not access_token:
            print("  ERROR: No access token in response")
            return {"stations": [], "prices": []}
        
        print(f"  Token obtained (expires in {auth_data.get('expires_in', 'unknown')}s)")
        
        # Common headers for subsequent requests
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        
        # Step 2: Get reference data (outlets, brands, fuels)
        print("  Fetching reference data...")
        ref_response = requests.get(
            f"{NT_BASE_URL}/v1/getReferenceData",
            headers=headers,
            timeout=60
        )
        ref_response.raise_for_status()
        ref_data = ref_response.json()
        
        # Build brand lookup - NT uses BrandIdentifier and BrandName
        brand_lookup = {}
        brands_raw = ref_data.get("Brands", ref_data.get("brands", []))
        for brand in brands_raw:
            if isinstance(brand, dict):
                # NT uses BrandIdentifier (e.g., "AF") and BrandName (e.g., "Ausfuel")
                brand_id = (brand.get("BrandIdentifier") or brand.get("brandIdentifier") or
                           brand.get("BrandId") or brand.get("brandId") or 
                           brand.get("Id") or brand.get("id"))
                brand_name = (brand.get("BrandName") or brand.get("brandName") or 
                             brand.get("Name") or brand.get("name") or "")
                if brand_id and brand_name:
                    brand_lookup[str(brand_id)] = brand_name
        
        # Build fuel type lookup
        fuel_lookup = {}
        fuels_raw = ref_data.get("Fuels", ref_data.get("fuels", []))
        for fuel in fuels_raw:
            if isinstance(fuel, dict):
                fuel_code = (fuel.get("FuelCode") or fuel.get("fuelCode") or 
                            fuel.get("Code") or fuel.get("code"))
                fuel_name = (fuel.get("FuelName") or fuel.get("fuelName") or 
                            fuel.get("Description") or fuel.get("description") or 
                            fuel.get("Name") or fuel.get("name") or "").upper()
                if fuel_code:
                    # Map NT fuel codes to standard codes
                    if "E10" in fuel_name:
                        normalized = "E10"
                    elif "UNLEADED 91" in fuel_name or "ULP 91" in fuel_name or fuel_name == "U91" or "ULP" == fuel_name:
                        normalized = "U91"
                    elif "95" in fuel_name or "PULP 95" in fuel_name:
                        normalized = "U95"
                    elif "98" in fuel_name or "PULP 98" in fuel_name:
                        normalized = "U98"
                    elif "DIESEL" in fuel_name and "PREMIUM" not in fuel_name:
                        normalized = "Diesel"
                    elif "PREMIUM DIESEL" in fuel_name:
                        normalized = "Premium Diesel"
                    elif "LPG" in fuel_name or "AUTOGAS" in fuel_name:
                        normalized = "LPG"
                    else:
                        normalized = str(fuel_code)
                    fuel_lookup[str(fuel_code)] = normalized
        
        print(f"  Found {len(brand_lookup)} brands, {len(fuel_lookup)} fuel types")
        
        # Process outlets (stations)
        outlets = ref_data.get("Outlets", ref_data.get("outlets", []))
        stations = []
        outlet_ids = []
        
        for outlet in outlets:
            if not isinstance(outlet, dict):
                continue
            
            outlet_id = (outlet.get("FuelOutletIdentifier") or outlet.get("fuelOutletIdentifier") or 
                        outlet.get("Identifier"))
            if not outlet_id:
                continue
            
            outlet_ids.append(str(outlet_id))
            
            # Get brand - NT uses FuelBrandIdentifier which is a code like "IN", "SH", "BP"
            brand_code = outlet.get("FuelBrandIdentifier") or outlet.get("fuelBrandIdentifier") or ""
            # Use brand lookup if available, otherwise use the code directly
            brand_name = brand_lookup.get(str(brand_code), brand_code) if brand_code else ""
            
            # Get outlet name - NT uses "OutletName"
            outlet_name = (outlet.get("OutletName") or outlet.get("outletName") or 
                          outlet.get("Name") or outlet.get("name") or "")
            
            # Build address - NT has separate Address, Suburb, Postcode fields
            address = outlet.get("Address") or outlet.get("address") or ""
            suburb = outlet.get("Suburb") or outlet.get("suburb") or ""
            postcode = outlet.get("Postcode") or outlet.get("postcode") or ""
            
            address_parts = []
            if address:
                address_parts.append(address)
            if suburb:
                address_parts.append(suburb)
            if postcode:
                address_parts.append(str(postcode))
            address_parts.append("NT")
            
            full_address = ", ".join(filter(None, address_parts))
            
            # Get location - NT has Latitude/Longitude at top level
            lat = outlet.get("Latitude") or outlet.get("latitude")
            lng = outlet.get("Longitude") or outlet.get("longitude")
            
            stations.append({
                "brand": brand_name,
                "code": f"NT:{outlet_id}",
                "name": outlet_name,
                "address": full_address,
                "location": {
                    "latitude": safe_float(lat),
                    "longitude": safe_float(lng)
                },
                "state": "NT"
            })
        
        print(f"  Found {len(stations)} outlets")
        
        # Step 3: Fetch prices for all outlets (max 10 per request)
        print("  Fetching prices...")
        prices = []
        unavailable_fuels = []

        # Split outlet_ids into chunks of 10
        chunk_size = 10
        chunks = [outlet_ids[i:i + chunk_size] for i in range(0, len(outlet_ids), chunk_size)]

        for chunk_idx, chunk in enumerate(chunks):
            try:
                price_response = requests.post(
                    f"{NT_BASE_URL}/v1/getFuelPrice/fuelOutletIdentifier",
                    headers=headers,
                    json={"FuelOutletIdentifier": chunk},
                    timeout=30
                )

                if price_response.status_code != 200:
                    print(f"    Chunk {chunk_idx + 1}/{len(chunks)}: Error {price_response.status_code}")
                    continue

                price_data = price_response.json()

                # Process price response - it's an array of outlets with their fuels
                if isinstance(price_data, list):
                    for outlet_prices in price_data:
                        if not isinstance(outlet_prices, dict):
                            continue

                        outlet_id = outlet_prices.get("FuelOutletIdentifier") or outlet_prices.get("fuelOutletIdentifier")
                        available_fuels = outlet_prices.get("AvailableFuel") or outlet_prices.get("availableFuel") or []

                        for fuel in available_fuels:
                            if not isinstance(fuel, dict):
                                continue

                            fuel_code = (fuel.get("FuelCode") or fuel.get("fuelCode") or
                                        fuel.get("Code") or fuel.get("code"))
                            price_raw = fuel.get("Price") or fuel.get("price")
                            is_available = fuel.get("IsAvailable") or fuel.get("isAvailable")

                            # Normalize fuel type
                            normalized_fuel = fuel_lookup.get(str(fuel_code), str(fuel_code)) if fuel_code else ""

                            if is_available is False:
                                unavailable_fuels.append({
                                    "stationcode": f"NT:{outlet_id}",
                                    "state": "NT",
                                    "fueltype": normalized_fuel,
                                })
                                continue

                            # Convert price to float (handle string prices)
                            price_val = safe_float(price_raw)

                            # Skip invalid prices
                            if not price_val or price_val <= 0:
                                continue

                            prices.append({
                                "stationcode": f"NT:{outlet_id}",
                                "state": "NT",
                                "fueltype": normalized_fuel,
                                "price": price_val,  # Already in cents per litre
                                "lastupdated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                            })

                # Small delay between requests to be nice to the API
                if chunk_idx < len(chunks) - 1:
                    time.sleep(0.2)

            except Exception as chunk_error:
                print(f"    Chunk {chunk_idx + 1}/{len(chunks)}: Error - {chunk_error}")
                continue

        print(f"  Stations: {len(stations)}, Prices: {len(prices)}, Unavailable: {len(unavailable_fuels)}")

        return {"stations": stations, "prices": prices, "unavailable_fuels": unavailable_fuels}
        
    except Exception as e:
        print(f"  ERROR: {e}")
        import traceback
        traceback.print_exc()
        return {"stations": [], "prices": []}


# =============================================================================
# NORMALIZATION & OUTPUT
# =============================================================================

def normalize_and_join(all_stations: list, all_prices: list, all_unavailable: list | None = None) -> tuple[list, list, list]:
    """
    Join stations with their prices and normalize fuel types, brands, and addresses.
    Returns a tuple of (stations with prices, fuel types found, stations without prices).
    """
    # Build station lookup
    station_map = {s["code"]: s.copy() for s in all_stations}

    # Initialize prices dict for each station and normalize brands/addresses
    for code in station_map:
        station_map[code]["prices"] = {}
        station_map[code]["unavailable_fuels"] = set()
        station_map[code]["updated"] = ""
        # Normalize brand name
        original_brand = station_map[code].get("brand", "")
        station_map[code]["brand"] = normalize_brand(original_brand)
        # Normalize address (fix ALL CAPS)
        original_address = station_map[code].get("address", "")
        station_map[code]["address"] = title_case_address(original_address)
        # Also normalize station name if it's ALL CAPS
        original_name = station_map[code].get("name", "")
        if original_name and original_name.isupper():
            station_map[code]["name"] = title_case_address(original_name)

    # Track all fuel types found
    fuel_types_found = set()

    # Group prices by station
    for price in all_prices:
        code = price.get("stationcode")
        if code not in station_map:
            continue

        raw_fuel = price.get("fueltype", "")
        normalized_fuel = normalize_fuel_type(raw_fuel)

        # Skip empty fuel types
        if not normalized_fuel:
            continue

        price_val = price.get("price")
        if price_val and price_val > 0:
            station_map[code]["prices"][normalized_fuel] = price_val
            fuel_types_found.add(normalized_fuel)

            # Track most recent update
            ts = normalize_timestamp(price.get("lastupdated", ""), station_map[code].get("state", ""))
            if ts > station_map[code]["updated"]:
                station_map[code]["updated"] = ts

    # Group unavailable fuels by station
    for entry in (all_unavailable or []):
        code = entry.get("stationcode")
        if code not in station_map:
            continue
        raw_fuel = entry.get("fueltype", "")
        normalized_fuel = normalize_fuel_type(raw_fuel)
        if normalized_fuel:
            station_map[code]["unavailable_fuels"].add(normalized_fuel)

    # Filter out stations with no prices and track stats
    result = []
    stations_no_prices = []
    for station in station_map.values():
        # Flatten location for simpler JSON
        loc = station.pop("location", {})
        station["lat"] = loc.get("latitude")
        station["lng"] = loc.get("longitude")

        # Convert unavailable_fuels set to sorted list; exclude any that now have a price
        unavailable = station.pop("unavailable_fuels", set())
        unavailable -= set(station.get("prices", {}).keys())
        if unavailable:
            station["unavailable_fuels"] = sorted(unavailable)

        if station["prices"]:
            result.append(station)
        else:
            # Remove empty prices dict for cleaner output
            station.pop("prices", None)
            station.pop("updated", None)
            stations_no_prices.append(station)
    
    # Report stations without prices
    if stations_no_prices:
        no_price_by_state = {}
        for s in stations_no_prices:
            st = s.get("state", "Unknown")
            no_price_by_state[st] = no_price_by_state.get(st, 0) + 1
        print(f"  NOTE: {len(stations_no_prices)} stations filtered out (no prices): {no_price_by_state}")
    
    # Sort fuel types: common ones first, then others alphabetically
    sorted_fuels = []
    for ft in ALL_FUEL_TYPES:
        if ft in fuel_types_found:
            sorted_fuels.append(ft)
            fuel_types_found.discard(ft)
    sorted_fuels.extend(sorted(fuel_types_found))
    
    return result, sorted_fuels, stations_no_prices


def save_normalized_combined(all_data: dict[str, dict], timestamp_str: str, now: datetime):
    """Save normalized, combined Australia-wide data."""
    data_dir = Path("data/AUS")
    data_dir.mkdir(parents=True, exist_ok=True)
    
    # Combine all stations and prices
    all_stations = []
    all_prices = []
    all_unavailable = []
    states_included = []

    for state, data in all_data.items():
        stations = data.get("stations", [])
        prices = data.get("prices", [])

        # Debug: print what we're adding
        if stations:
            print(f"  Adding {state}: {len(stations)} stations, {len(prices)} prices")
            states_included.append(state)
            all_stations.extend(stations)
            all_prices.extend(prices)
            all_unavailable.extend(data.get("unavailable_fuels", []))
        else:
            print(f"  Skipping {state}: no stations")

    # Normalize and join
    normalized_stations, fuel_types_found, _ = normalize_and_join(all_stations, all_prices, all_unavailable)
    
    # Count by state
    state_counts = {}
    for s in normalized_stations:
        st = s.get("state", "Unknown")
        state_counts[st] = state_counts.get(st, 0) + 1
    
    output = {
        "source": "AUS_Combined_FuelData",
        "version": 2,  # Normalized format version
        "states": sorted(states_included),
        "fetched_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "station_count": len(normalized_stations),
        "fuel_types": fuel_types_found,  # All fuel types found in data
        "stations": normalized_stations
    }
    
    # Save timestamped file
    filename = f"aus_fuel_data_{timestamp_str}.json"
    filepath = data_dir / filename
    with open(filepath, "w") as f:
        json.dump(output, f, separators=(',', ':'))  # Compact JSON
    
    # Save latest file (pretty for debugging)
    latest_path = data_dir / "latest.json"
    with open(latest_path, "w") as f:
        json.dump(output, f, indent=2)
    
    file_size = filepath.stat().st_size / 1024
    print(f"\n  Saved {filepath} ({file_size:.1f} KB)")
    print(f"  Stations by state: {state_counts}")
    print(f"  Total: {len(normalized_stations)} stations with prices")


def save_state_data(state: str, data: dict, timestamp_str: str, now: datetime):
    """Save NORMALIZED data for a single state/territory."""
    state_lower = state.lower()
    data_dir = Path(f"data/{state_lower}")
    data_dir.mkdir(parents=True, exist_ok=True)
    
    # Normalize and join for this state
    stations = data.get("stations", [])
    prices = data.get("prices", [])
    unavailable = data.get("unavailable_fuels", [])
    normalized_stations, fuel_types_found, _ = normalize_and_join(stations, prices, unavailable)
    
    output = {
        "source": f"{state}_FuelData",
        "version": 2,
        "state": state,
        "fetched_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "station_count": len(normalized_stations),
        "fuel_types": fuel_types_found,
        "stations": normalized_stations
    }
    
    # Save timestamped file
    filename = f"{state_lower}_fuel_data_{timestamp_str}.json"
    filepath = data_dir / filename
    with open(filepath, "w") as f:
        json.dump(output, f, separators=(',', ':'))  # Compact JSON
    
    # Save latest file (pretty for debugging)
    latest_path = data_dir / "latest.json"
    with open(latest_path, "w") as f:
        json.dump(output, f, indent=2)
    
    file_size = filepath.stat().st_size / 1024
    print(f"  Saved {filepath} ({file_size:.1f} KB) - {len(normalized_stations)} stations")


def save_brands_list(normalized_stations: list, now: datetime):
    """Save a list of all unique brands across all stations."""
    data_dir = Path("data")
    data_dir.mkdir(parents=True, exist_ok=True)
    
    # Collect unique brands with counts by state
    brand_stats = {}
    for station in normalized_stations:
        brand = station.get("brand", "").strip()
        state = station.get("state", "Unknown")
        if brand:
            if brand not in brand_stats:
                brand_stats[brand] = {"count": 0, "states": set()}
            brand_stats[brand]["count"] += 1
            brand_stats[brand]["states"].add(state)
    
    # Convert to sorted list
    brands_list = []
    for brand, stats in sorted(brand_stats.items()):
        brands_list.append({
            "name": brand,
            "station_count": stats["count"],
            "states": sorted(stats["states"])
        })
    
    output = {
        "fetched_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "brand_count": len(brands_list),
        "brands": brands_list
    }
    
    filepath = data_dir / "brands.json"
    with open(filepath, "w") as f:
        json.dump(output, f, indent=2)
    
    print(f"  Saved {filepath} ({len(brands_list)} brands)")


def save_stations_no_prices(stations_no_prices: list, now: datetime):
    """
    Save metadata for stations that were filtered out due to having no prices.
    Useful for debugging and tracking stations that may need attention.
    """
    data_dir = Path("data")
    data_dir.mkdir(parents=True, exist_ok=True)
    
    # Count by state
    state_counts = {}
    for s in stations_no_prices:
        st = s.get("state", "Unknown")
        state_counts[st] = state_counts.get(st, 0) + 1
    
    # Count by brand
    brand_counts = {}
    for s in stations_no_prices:
        brand = s.get("brand", "Unknown")
        brand_counts[brand] = brand_counts.get(brand, 0) + 1
    
    # Sort stations by state, then by name
    sorted_stations = sorted(stations_no_prices, key=lambda x: (x.get("state", ""), x.get("name", "")))
    
    output = {
        "fetched_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "description": "Stations excluded from main data due to no current price information",
        "station_count": len(stations_no_prices),
        "by_state": dict(sorted(state_counts.items())),
        "by_brand": dict(sorted(brand_counts.items(), key=lambda x: -x[1])),  # Sort by count descending
        "stations": sorted_stations
    }
    
    filepath = data_dir / "stations_no_prices.json"
    with open(filepath, "w") as f:
        json.dump(output, f, indent=2)
    
    file_size = filepath.stat().st_size / 1024
    print(f"  Saved {filepath} ({file_size:.1f} KB) - {len(stations_no_prices)} stations")


def save_prices_compact(normalized_stations: list, now: datetime):
    """
    Save compact prices-only file for quick updates.
    Format: {"station_code": {"U91": 189.9, "Diesel": 195.5, "_updated": "2025-..."}, ...}
    """
    data_dir = Path("data")
    data_dir.mkdir(parents=True, exist_ok=True)
    
    prices_compact = {}
    for station in normalized_stations:
        code = station.get("code", "")
        prices = station.get("prices", {})
        updated = station.get("updated", "")
        tomorrow_prices = station.get("tomorrowPrices")
        if code and prices:
            prices_compact[code] = prices.copy()
            if updated:
                prices_compact[code]["_updated"] = updated
            if tomorrow_prices:
                prices_compact[code]["_tomorrowPrices"] = tomorrow_prices
    
    output = {
        "fetched_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "station_count": len(prices_compact),
        "prices": prices_compact
    }
    
    filepath = data_dir / "prices_compact.json"
    with open(filepath, "w") as f:
        json.dump(output, f, separators=(',', ':'))
    
    file_size = filepath.stat().st_size / 1024
    print(f"  Saved {filepath} ({file_size:.1f} KB)")


def save_stations_metadata(normalized_stations: list, now: datetime):
    """
    Save station metadata (rarely changes) separate from prices.
    Includes: code, name, brand, address, lat, lng, state
    """
    data_dir = Path("data")
    data_dir.mkdir(parents=True, exist_ok=True)
    
    stations_meta = []
    for station in normalized_stations:
        stations_meta.append({
            "code": station.get("code", ""),
            "name": station.get("name", ""),
            "brand": station.get("brand", ""),
            "address": station.get("address", ""),
            "lat": station.get("lat"),
            "lng": station.get("lng"),
            "state": station.get("state", "")
        })
    
    output = {
        "fetched_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "station_count": len(stations_meta),
        "stations": stations_meta
    }
    
    filepath = data_dir / "stations_metadata.json"
    with open(filepath, "w") as f:
        json.dump(output, f, separators=(',', ':'))
    
    file_size = filepath.stat().st_size / 1024
    print(f"  Saved {filepath} ({file_size:.1f} KB)")


def save_cheapest_by_fuel(normalized_stations: list, now: datetime, limit: int = 100):
    """
    Save top N cheapest stations for each fuel type.
    Perfect for "Global cheapest" feature.
    """
    data_dir = Path("data/cheapest")
    data_dir.mkdir(parents=True, exist_ok=True)
    
    for fuel_type in ALL_FUEL_TYPES:
        # Collect stations with this fuel type
        stations_with_fuel = []
        for station in normalized_stations:
            prices = station.get("prices", {})
            if fuel_type in prices:
                stations_with_fuel.append({
                    "code": station.get("code", ""),
                    "name": station.get("name", ""),
                    "brand": station.get("brand", ""),
                    "address": station.get("address", ""),
                    "lat": station.get("lat"),
                    "lng": station.get("lng"),
                    "state": station.get("state", ""),
                    "price": prices[fuel_type],
                    "updated": station.get("updated", "")
                })
        
        # Sort by price and take top N
        stations_with_fuel.sort(key=lambda x: x["price"])
        cheapest = stations_with_fuel[:limit]
        
        output = {
            "fetched_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "fuel_type": fuel_type,
            "station_count": len(cheapest),
            "stations": cheapest
        }
        
        filepath = data_dir / f"{fuel_type.lower()}.json"
        with open(filepath, "w") as f:
            json.dump(output, f, separators=(',', ':'))
        
        if cheapest:
            print(f"  Saved {filepath} (top {len(cheapest)}, cheapest: {cheapest[0]['price']}¢)")
        else:
            print(f"  Saved {filepath} (no stations)")


def save_index_file(all_data: dict, normalized_stations: list, now: datetime):
    """
    Save index file with metadata about all data files.
    Apps can check this to see if they need to download updates.
    """
    import hashlib
    
    data_dir = Path("data")
    data_dir.mkdir(parents=True, exist_ok=True)
    
    # Calculate state stats
    state_stats = {}
    for station in normalized_stations:
        state = station.get("state", "Unknown")
        if state not in state_stats:
            state_stats[state] = {"station_count": 0, "price_count": 0}
        state_stats[state]["station_count"] += 1
        state_stats[state]["price_count"] += len(station.get("prices", {}))
    
    # Add file info for each state
    states_info = {}
    for state in sorted(state_stats.keys()):
        state_lower = state.lower()
        filepath = Path(f"data/{state_lower}/latest.json")
        checksum = ""
        file_size = 0
        if filepath.exists():
            content = filepath.read_bytes()
            checksum = hashlib.md5(content).hexdigest()[:8]
            file_size = len(content)
        
        states_info[state] = {
            "station_count": state_stats[state]["station_count"],
            "price_count": state_stats[state]["price_count"],
            "file": f"{state_lower}/latest.json",
            "checksum": checksum,
            "size_bytes": file_size
        }
    
    # Collect all unique fuel types
    all_fuel_types = set()
    for station in normalized_stations:
        all_fuel_types.update(station.get("prices", {}).keys())
    
    output = {
        "last_updated": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total_stations": len(normalized_stations),
        "total_states": len(states_info),
        "fuel_types": sorted(all_fuel_types),
        "states": states_info,
        "files": {
            "full_data": "AUS/latest.json",
            "prices_compact": "prices_compact.json",
            "stations_metadata": "stations_metadata.json",
            "brands": "brands.json",
            "cheapest": {ft.lower(): f"cheapest/{ft.lower()}.json" for ft in ALL_FUEL_TYPES}
        }
    }
    
    filepath = data_dir / "index.json"
    with open(filepath, "w") as f:
        json.dump(output, f, indent=2)
    
    print(f"  Saved {filepath}")


def save_geographic_tiles(normalized_stations: list, now: datetime, tile_size: int = 2):
    """
    Save stations grouped by geographic tiles.
    Tile size is in degrees (2° = ~220km).
    Apps can fetch only nearby tiles based on user location.
    """
    import math
    
    data_dir = Path("data/tiles")
    data_dir.mkdir(parents=True, exist_ok=True)
    
    # Group stations by tile
    tiles = {}
    for station in normalized_stations:
        lat = station.get("lat")
        lng = station.get("lng")
        if lat is None or lng is None:
            continue
        
        # Calculate tile coordinates (floor to tile boundary)
        tile_lat = math.floor(lat / tile_size) * tile_size
        tile_lng = math.floor(lng / tile_size) * tile_size
        tile_key = f"lat_{tile_lat}_lng_{tile_lng}"
        
        if tile_key not in tiles:
            tiles[tile_key] = {
                "bounds": {
                    "min_lat": tile_lat,
                    "max_lat": tile_lat + tile_size,
                    "min_lng": tile_lng,
                    "max_lng": tile_lng + tile_size
                },
                "stations": []
            }
        
        tiles[tile_key]["stations"].append(station)
    
    # Save each tile
    tile_index = []
    for tile_key, tile_data in tiles.items():
        output = {
            "fetched_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "tile": tile_key,
            "bounds": tile_data["bounds"],
            "station_count": len(tile_data["stations"]),
            "stations": tile_data["stations"]
        }
        
        filepath = data_dir / f"{tile_key}.json"
        with open(filepath, "w") as f:
            json.dump(output, f, separators=(',', ':'))
        
        tile_index.append({
            "tile": tile_key,
            "bounds": tile_data["bounds"],
            "station_count": len(tile_data["stations"]),
            "file": f"tiles/{tile_key}.json"
        })
    
    # Save tile index
    index_output = {
        "fetched_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tile_size_degrees": tile_size,
        "tile_count": len(tile_index),
        "total_stations": len(normalized_stations),
        "tiles": sorted(tile_index, key=lambda x: (x["bounds"]["min_lat"], x["bounds"]["min_lng"]))
    }
    
    index_path = data_dir / "index.json"
    with open(index_path, "w") as f:
        json.dump(index_output, f, indent=2)
    
    print(f"  Saved {len(tiles)} geographic tiles to {data_dir}/")


# =============================================================================
# HISTORY / ANALYTICS
# =============================================================================

# City bounding boxes: (min_lat, max_lat, min_lng, max_lng)
CITY_REGIONS: dict[str, dict[str, tuple[float, float, float, float]]] = {
    "NSW": {
        "Sydney":       (-34.17, -33.36, 150.50, 151.60),
        "Newcastle":    (-33.05, -32.75, 151.55, 151.85),
        "Wollongong":   (-34.60, -34.32, 150.75, 151.05),
        "Canberra":     (-35.50, -35.10, 148.95, 149.25),  # ACT metro overlaps
    },
    "VIC": {
        "Melbourne":    (-38.10, -37.55, 144.55, 145.55),
        "Geelong":      (-38.30, -38.05, 144.25, 144.50),
        "Ballarat":     (-37.65, -37.50, 143.80, 143.92),
        "Bendigo":      (-36.82, -36.70, 144.24, 144.35),
    },
    "QLD": {
        "Brisbane":     (-27.70, -27.15, 152.75, 153.30),
        "Gold Coast":   (-28.25, -27.80, 153.30, 153.55),
        "Sunshine Coast":(-26.85, -26.55, 152.90, 153.20),
        "Townsville":   (-19.40, -19.20, 146.70, 146.85),
        "Cairns":       (-17.00, -16.85, 145.70, 145.80),
        "Toowoomba":    (-27.65, -27.50, 151.90, 152.00),
    },
    "SA": {
        "Adelaide":     (-35.20, -34.70, 138.45, 138.80),
    },
    "WA": {
        "Perth":        (-32.20, -31.65, 115.65, 116.10),
    },
    "TAS": {
        "Hobart":       (-43.00, -42.75, 147.20, 147.45),
        "Launceston":   (-41.50, -41.40, 147.10, 147.20),
    },
    "NT": {
        "Darwin":       (-12.55, -12.35, 130.80, 131.05),
    },
    "ACT": {
        "Canberra":     (-35.50, -35.10, 148.95, 149.25),
    },
}


def classify_city(station: dict) -> str | None:
    """Return the city name for a station, or None if it falls outside all metro areas."""
    state = station.get("state", "")
    lat = station.get("lat")
    lng = station.get("lng")
    if lat is None or lng is None:
        return None
    cities = CITY_REGIONS.get(state, {})
    for city_name, (min_lat, max_lat, min_lng, max_lng) in cities.items():
        if min_lat <= lat <= max_lat and min_lng <= lng <= max_lng:
            return city_name
    return None


def generate_history(normalized_stations: list[dict], all_data: dict, now: datetime):
    """
    Generate/update daily price history files.

    On each run, upsert today's date entry with avg/min/max per fuel type per city.
    """
    today = now.strftime("%Y-%m-%d")
    history_dir = Path("data/history")
    history_dir.mkdir(parents=True, exist_ok=True)

    # Group stations by state → city
    state_city_stations: dict[str, dict[str, list[dict]]] = {}
    for station in normalized_stations:
        state = station.get("state", "")
        if not state:
            continue
        city = classify_city(station) or f"Regional {state}"
        state_city_stations.setdefault(state, {}).setdefault(city, []).append(station)

    all_states = sorted(state_city_stations.keys())

    # Process each state
    for state in all_states:
        state_file = history_dir / f"{state.lower()}.json"

        # Load existing history
        if state_file.exists():
            with open(state_file, "r") as f:
                state_history = json.load(f)
        else:
            state_history = {
                "state": state,
                "generated_at": "",
                "cities": [],
                "history": {},
            }

        cities_data = state_city_stations[state]
        # Update cities list
        all_cities = sorted(cities_data.keys())
        state_history["cities"] = all_cities
        state_history["generated_at"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")

        for city, stations in cities_data.items():
            if city not in state_history["history"]:
                state_history["history"][city] = {}

            # Calculate today's stats per fuel type
            fuel_prices: dict[str, list[float]] = {}
            for s in stations:
                for fuel, price in s.get("prices", {}).items():
                    if isinstance(price, (int, float)) and price > 0:
                        fuel_prices.setdefault(fuel, []).append(price)

            day_stats = {}
            for fuel, prices in fuel_prices.items():
                if len(prices) == 0:
                    continue
                day_stats[fuel] = {
                    "avg": round(sum(prices) / len(prices), 1),
                    "min": round(min(prices), 1),
                    "max": round(max(prices), 1),
                    "samples": len(prices),
                }

            # Upsert today
            state_history["history"][city][today] = day_stats

        # Save
        with open(state_file, "w") as f:
            json.dump(state_history, f, separators=(',', ':'))

    # Generate national.json
    national_file = history_dir / "national.json"
    if national_file.exists():
        with open(national_file, "r") as f:
            national_history = json.load(f)
    else:
        national_history = {"generated_at": "", "history": {}}

    national_history["generated_at"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    # Compute national stats from all stations
    fuel_prices_national: dict[str, list[float]] = {}
    for s in normalized_stations:
        for fuel, price in s.get("prices", {}).items():
            if isinstance(price, (int, float)) and price > 0:
                fuel_prices_national.setdefault(fuel, []).append(price)

    day_stats_national = {}
    for fuel, prices in fuel_prices_national.items():
        if len(prices) == 0:
            continue
        day_stats_national[fuel] = {
            "avg": round(sum(prices) / len(prices), 1),
            "min": round(min(prices), 1),
            "max": round(max(prices), 1),
            "samples": len(prices),
        }
    national_history["history"][today] = day_stats_national

    with open(national_file, "w") as f:
        json.dump(national_history, f, separators=(',', ':'))

    # Generate index.json
    all_dates = set()
    for state in all_states:
        state_file_check = history_dir / f"{state.lower()}.json"
        if state_file_check.exists():
            with open(state_file_check, "r") as f:
                sh = json.load(f)
            for city_hist in sh.get("history", {}).values():
                all_dates.update(city_hist.keys())

    sorted_dates = sorted(all_dates) if all_dates else [today]
    index_data = {
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "states": [s.lower() for s in all_states],
        "date_range": {
            "from": sorted_dates[0],
            "to": sorted_dates[-1],
        },
    }

    index_file = history_dir / "index.json"
    with open(index_file, "w") as f:
        json.dump(index_data, f, indent=2)

    print(f"  History updated for {len(all_states)} states, {today}")
    print(f"  Date range: {sorted_dates[0]} to {sorted_dates[-1]} ({len(sorted_dates)} days)")


# =============================================================================
# MAIN
# =============================================================================

def main():
    now = datetime.now(timezone.utc)
    timestamp_str = now.strftime("%Y%m%d_%H%M%S")
    
    print("=" * 70)
    print("Australian Fuel Price Fetcher - NORMALIZED OUTPUT v4")
    print("=" * 70)
    print(f"Timestamp: {now.isoformat()}")
    print(f"Fuel types: {ALL_FUEL_TYPES}")
    
    # Fetch all states
    nsw_tas_act_data = fetch_nsw_tas_act()
    qld_data = fetch_qld()
    vic_data = fetch_vic()
    sa_data = fetch_sa()
    wa_data = fetch_wa()
    nt_data = fetch_nt()  # NEW: Northern Territory
    
    # Split NSW/TAS/ACT data by state
    nsw_data = {"stations": [], "prices": []}
    tas_data = {"stations": [], "prices": []}
    act_data = {"stations": [], "prices": []}
    
    # Split stations by detected state
    for station in nsw_tas_act_data.get("stations", []):
        state = station.get("state", "NSW")
        if state == "NSW":
            nsw_data["stations"].append(station)
        elif state == "TAS":
            tas_data["stations"].append(station)
        elif state == "ACT":
            act_data["stations"].append(station)
    
    # Split prices by detected state
    for price in nsw_tas_act_data.get("prices", []):
        state = price.get("state", "NSW")
        if state == "NSW":
            nsw_data["prices"].append(price)
        elif state == "TAS":
            tas_data["prices"].append(price)
        elif state == "ACT":
            act_data["prices"].append(price)
    
    # Debug: Print split results
    print(f"\n  Split results:")
    print(f"    NSW: {len(nsw_data['stations'])} stations, {len(nsw_data['prices'])} prices")
    print(f"    TAS: {len(tas_data['stations'])} stations, {len(tas_data['prices'])} prices")
    print(f"    ACT: {len(act_data['stations'])} stations, {len(act_data['prices'])} prices")
    
    # Collect all state data (alphabetical order)
    all_data = {
        "ACT": act_data,
        "NSW": nsw_data,
        "NT": nt_data,   # NEW: Northern Territory
        "QLD": qld_data,
        "SA": sa_data,
        "TAS": tas_data,
        "VIC": vic_data,
        "WA": wa_data,
    }
    
    # Save individual state files (NORMALIZED format)
    print("\n" + "=" * 70)
    print("Saving State/Territory Data (NORMALIZED)")
    print("=" * 70)
    
    for state, data in all_data.items():
        if data.get("stations"):
            save_state_data(state, data, timestamp_str, now)
        else:
            print(f"  Skipping {state} - no data")
    
    # Save normalized combined data
    print("\n" + "=" * 70)
    print("Saving Normalized Combined Australia Data")
    print("=" * 70)
    
    # Get normalized stations for additional outputs
    all_stations_raw = []
    all_prices_raw = []
    all_unavailable_raw = []
    for state, data in all_data.items():
        if data.get("stations"):
            all_stations_raw.extend(data.get("stations", []))
            all_prices_raw.extend(data.get("prices", []))
            all_unavailable_raw.extend(data.get("unavailable_fuels", []))

    normalized_stations, _, stations_no_prices = normalize_and_join(all_stations_raw, all_prices_raw, all_unavailable_raw)
    
    # Save main combined file
    save_normalized_combined(all_data, timestamp_str, now)
    
    # Save optimized data structures
    print("\n" + "=" * 70)
    print("Saving Optimized Data Structures")
    print("=" * 70)
    
    # 1. Brands list
    save_brands_list(normalized_stations, now)
    
    # 2. Compact prices (for quick refreshes)
    save_prices_compact(normalized_stations, now)
    
    # 3. Station metadata (rarely changes)
    save_stations_metadata(normalized_stations, now)
    
    # 4. Stations without prices (for debugging/tracking)
    save_stations_no_prices(stations_no_prices, now)
    
    # 5. Cheapest by fuel type (for Global mode)
    print("\n  Cheapest by fuel type:")
    save_cheapest_by_fuel(normalized_stations, now, limit=100)
    
    # 6. Geographic tiles (for location-based fetching)
    print("\n  Geographic tiles:")
    save_geographic_tiles(normalized_stations, now, tile_size=2)
    
    # 7. Index file (for smart app updates)
    print("\n  Index file:")
    save_index_file(all_data, normalized_stations, now)

    # 8. History / Analytics data
    print("\n  History / Analytics:")
    generate_history(normalized_stations, all_data, now)

    print("\n" + "=" * 70)
    print("Done!")
    print("=" * 70)


if __name__ == "__main__":
    main()
