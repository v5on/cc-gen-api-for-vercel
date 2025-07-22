import re
import random
import requests
from pycountry import countries
from countryinfo import CountryInfo
from datetime import datetime, timedelta
from flask import Flask, jsonify, request, make_response, Response 
from werkzeug.exceptions import BadRequest, NotFound
from collections import OrderedDict 
from flask import render_template
import json 

app = Flask(__name__)

# Configuration
MAX_GEN_LIMIT = 500  # Maximum cards per request
DEFAULT_GEN_LIMIT = 5  # Default cards if limit not specified

# CORS Configuration
@app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', '*')
    response.headers.add('Access-Control-Allow-Methods', '*')
    return response

# Helper functions
def get_card_type(bin: str) -> str:
    """Determine card type based on BIN"""
    clean_bin = re.sub(r'[^\d]', '', bin)
    if clean_bin.startswith('4'):
        return "visa"
    elif clean_bin.startswith(('34', '37')):
        return "amex"
    elif clean_bin.startswith(('51', '52', '53', '54', '55', '2221', '2720')):
        return "mastercard"
    elif clean_bin.startswith(('6011', '65')):
        return "discover"
    else:
        return "unknown"

def luhn_checksum(card_number: str) -> bool:
    """Validate card number using Luhn algorithm"""
    if not card_number.isdigit():
        return False

    total = 0
    reverse_digits = card_number[::-1]

    for i, digit in enumerate(reverse_digits):
        digit = int(digit)
        if i % 2 == 1: 
            digit *= 2
            if digit > 9:
                digit = (digit // 10) + (digit % 10)
        total += digit

    return total % 10 == 0

def is_full_card_number(value: str) -> bool:
    digits = re.sub(r'\D', '', value)
    return len(digits) in [15, 16] and luhn_checksum(digits)

def generate_card_number(bin: str) -> str:
    """Generate valid card number from BIN with proper length and Luhn verification"""
    clean_bin = re.sub(r'[^\d]', '', bin)

    if len(clean_bin) < 6 or len(clean_bin) > 15:
        raise ValueError("BIN must be between 6-15 digits")

    card_type = get_card_type(clean_bin)
    if card_type == "amex":
        card_length = 15
    else:
        card_length = 16

    missing_digits = card_length - 1 - len(clean_bin)
    if missing_digits < 0:
        raise ValueError("BIN too long for card type")

    middle_digits = ''.join([str(random.randint(0, 9)) for _ in range(missing_digits)])
    partial_number = clean_bin + middle_digits

    total = 0
    for i, digit in enumerate(partial_number[::-1]):
        digit = int(digit)
        if i % 2 == 0:
            digit *= 2
            if digit > 9:
                digit = (digit // 10) + (digit % 10)
        total += digit

    check_digit = (10 - (total % 10)) % 10
    full_number = partial_number + str(check_digit)

    if not luhn_checksum(full_number) or len(full_number) != card_length or not full_number.isdigit():
        return generate_card_number(bin)

    return full_number

def generate_expiry() -> tuple:
    expiry_date = datetime.now() + timedelta(days=random.randint(365, 365*5))
    return (expiry_date.strftime("%m"), expiry_date.strftime("%y"))

def generate_cvv(bin: str = None, card_type: str = None) -> str:
    if not card_type and bin:
        card_type = get_card_type(bin)

    if card_type and card_type.lower() == "amex":
        return str(random.randint(1000, 9999))
    return str(random.randint(100, 999))

def get_bin_info(bin: str) -> dict:
    bin6 = bin[:6]
    card_type = get_card_type(bin6)
    headers = {"User-Agent": "Mozilla/5.0"}

    # HandyAPI with rotating API keys
    HANDY_API_KEYS = [
        "handyapi-PUB-0YI0cklUYMv1njw6Q597r4C7KqB",
        "handyapi-PUB-KEY-2",
        "handyapi-PUB-KEY-3",
    ]

    for api_key in HANDY_API_KEYS:
        try:
            r = requests.get(
                f"https://data.handyapi.com/bin/{bin6}",
                headers={**headers, "x-api-key": api_key}
            )
            if r.status_code == 200:
                data = r.json()
                if data.get("Status") == "SUCCESS":
                    country_name = data.get("Country", {}).get("Name", "N/A")
                    country_code = get_country_code(country_name)
                    currency = get_currency_from_country_name(country_name)

                    return {
                        "type": data.get("Type"),
                        "scheme": data.get("Scheme"),
                        "tier": data.get("CardTier"),
                        "bank": data.get("Issuer"),
                        "country": country_name,
                        "currency": currency,
                        "country_code": country_code,
                        "flag": get_flag_emoji(country_code),
                        "prepaid": data.get("Prepaid") == "Yes",
                        "luhn": True
                    }
            elif r.status_code in [403, 429]:
                print(f"[HandyAPI] Rate limit hit for key: {api_key}, trying next key...")
                continue
            else:
                print(f"[HandyAPI] Unexpected response ({r.status_code}) with key: {api_key}")
                continue
        except Exception as e:
            print(f"[HandyAPI] Exception with key {api_key}:", e)

    # bingen fallback
    try:
        r = requests.get(f"https://bingen-rho.vercel.app/?bin={bin6}")
        if r.status_code == 200:
            data = r.json().get("bin_info", {})

            country_name = data.get("country", "N/A")
            country_code = data.get("country_code", get_country_code(country_name))
            currency = get_currency_from_country_name(country_name)

            return {
                "scheme": data.get("scheme", "N/A").upper(),
                "type": data.get("type", "N/A").upper(),
                "tier": data.get("brand", "N/A").upper(),
                "bank": data.get("bank", "N/A"),
                "country": country_name,
                "flag": data.get("flag", ""),
                "currency": currency,
                "country_code": country_code,
                "prepaid": False,
            }
    except Exception as e:
        print("Bingen fallback failed:", e)

    # DrLab fallback
    try:
        r = requests.get(f"https://drlabapis.onrender.com/api/bin?bin={bin6}")
        if r.status_code == 200:
            data = r.json()
            country_name = data.get("country_name", "N/A")  
            country_code = get_country_code(country_name)
            currency = get_currency_from_country_name(country_name)
            return {
                "scheme": data.get("scheme", "N/A").upper(),
                "type": data.get("type", "N/A").upper(),
                "tier": data.get("level", "N/A").upper(),
                "bank": data.get("bank", "N/A"),
                "country": country_name,
                "flag": data.get("country_emoji", ""),
                "currency": currency,
                "country_code": country_code,
                "prepaid": False,
            }
    except Exception as e:
        print("Drlab API fallback failed:", e)

    return {
        "type": "credit",
        "scheme": card_type.upper() if card_type != "unknown" else None,
        "prepaid": False,
        "luhn": True,
        "bank": None,
        "country": None,
        "country_code": None,
        "flag": None,
        "tier": None,
        "currency": None
    }

def get_country_code(country_name: str) -> str:
    try:
        return countries.lookup(country_name).alpha_2
    except:
        return None

def get_currency_from_country_name(country_name: str) -> str:
    try:
        info = CountryInfo(country_name).info()
        currencies = info.get("currencies")
        if currencies and isinstance(currencies, list):
            return currencies[0]
        return None
    except Exception as e:
        print("Currency lookup failed:", e)
        return "N/A"

def get_flag_emoji(country_code: str) -> str:
    if not country_code or len(country_code) != 2:
        return "🏳️"
    try:
        return chr(0x1F1E6 + ord(country_code[0].upper())-65) + chr(0x1F1E6 + ord(country_code[1].upper())-65)
    except:
        return "🏳️"

# Routes
@app.route("/", methods=["GET"])
def home():
    return render_template("index.html")
    response_data = OrderedDict([
        ("message", "Public CC Generator API"),
        ("endpoints", OrderedDict([
            ("/generate?bin=[bin]&limit=[ammount]&month=[MM]&year=[YY]&cvv=[cvv]", "Generate CCs (JSON)"),
            ("/generate/view?bin=[bin]&limit=[ammount]&month=[MM]&year=[YY]&cvv=[cvv]", "Download CCs as file"),
            ("/bin/[bin in 6 digit]", "Get BIN info")
        ]))
    ])
    
    json_string = json.dumps(response_data, indent=2, ensure_ascii=False) # <--- UPDATED LINE
    response = make_response(json_string)
    response.headers['Content-Type'] = 'application/json; charset=utf-8' # <--- UPDATED LINE
    return response

@app.route("/generate", methods=["GET"])
def generate_cards():
    bin = request.args.get("bin")
    if not bin or len(bin) < 6 or len(bin) > 16:
        raise BadRequest("Invalid BIN (must be 6-16 digits)")

    try:
        limit = int(request.args.get("limit", DEFAULT_GEN_LIMIT))
        if limit < 1 or limit > MAX_GEN_LIMIT:
            limit = DEFAULT_GEN_LIMIT
    except:
        limit = DEFAULT_GEN_LIMIT

    month = request.args.get("month")
    year = request.args.get("year")
    cvv = request.args.get("cvv")

    bin_info = get_bin_info(bin)
    if not bin_info:
        raise BadRequest("Couldn't fetch BIN details from any source")

    cards = []
    for _ in range(limit):
        expiry_month, expiry_year = month or generate_expiry()[0], year or generate_expiry()[1]
        expiry_year = expiry_year[-2:] if len(expiry_year) == 4 else expiry_year
        card_type = get_card_type(bin)

        if cvv and re.fullmatch(r"[0-9xX]{1,4}", cvv):
            if card_type == "amex":
                padded_cvv = cvv.rjust(4, 'x')[-4:]
                card_cvv = ''.join([
                    str(random.randint(0, 9)) if c.lower() == 'x' else c
                    for c in padded_cvv
                ])
            else:
                padded_cvv = cvv.rjust(3, 'x')[-3:]
                card_cvv = ''.join([
                    str(random.randint(0, 9)) if c.lower() == 'x' else c
                    for c in padded_cvv
                ])
        else:
            card_cvv = generate_cvv(bin=bin, card_type=card_type)

        clean_bin = re.sub(r'[^\d]', '', bin)
        full_card_input = is_full_card_number(clean_bin)
        card_number = clean_bin if full_card_input else generate_card_number(clean_bin)
            
        cards.append(OrderedDict([
            ("number", card_number),
            ("expiry", f"{expiry_month.zfill(2)}|20{expiry_year}"),
            ("cvv", card_cvv),
            ("brand", bin_info.get("scheme")),
            ("type", bin_info.get("type"))
        ]))

    response_data = OrderedDict([
        ("cards", cards),
        ("bin_info", OrderedDict([
            ("bin", bin[:6]),
            ("bank", bin_info.get("bank")),
            ("country", bin_info.get("country")),
            ("country_code", bin_info.get("country_code")),
            ("flag", bin_info.get("flag")),
            ("scheme", bin_info.get("scheme")),
            ("type", bin_info.get("type")),
            ("tier", bin_info.get("tier")),
            ("currency", bin_info.get("currency"))
        ])),
        ("generated_at", datetime.utcnow().isoformat())
    ])

    json_string = json.dumps(response_data, indent=2, ensure_ascii=False) 
    response = make_response(json_string)
    response.headers['Content-Type'] = 'application/json; charset=utf-8' 
    return response

@app.route("/generate/view", methods=["GET"])
def generate_view():
    bin = request.args.get("bin")
    if not bin or len(bin) < 6 or len(bin) > 16:
        raise BadRequest("Invalid BIN (must be 6-16 digits)")

    try:
        limit = int(request.args.get("limit", DEFAULT_GEN_LIMIT))
        if limit < 1 or limit > MAX_GEN_LIMIT:
            limit = DEFAULT_GEN_LIMIT
    except:
        limit = DEFAULT_GEN_LIMIT

    month = request.args.get("month")
    year = request.args.get("year")
    cvv = request.args.get("cvv")

    bin_info = get_bin_info(bin)
    if not bin_info:
        raise BadRequest("Couldn't fetch BIN details")

    cards = []
    for _ in range(limit):
        expiry_month, expiry_year = month or generate_expiry()[0], year or generate_expiry()[1]
        expiry_year = expiry_year[-2:] if len(expiry_year) == 4 else expiry_year
        card_type = get_card_type(bin)

        if cvv and re.fullmatch(r"[0-9xX]{1,4}", cvv):
            if card_type == "amex":
                padded_cvv = cvv.rjust(4, 'x')[-4:]
                card_cvv = ''.join([
                    str(random.randint(0, 9)) if c.lower() == 'x' else c
                    for c in padded_cvv
                ])
            else:
                padded_cvv = cvv.rjust(3, 'x')[-3:]
                card_cvv = ''.join([
                    str(random.randint(0, 9)) if c.lower() == 'x' else c
                    for c in padded_cvv
                ])
        else:
            card_cvv = generate_cvv(bin=bin, card_type=card_type)

        clean_bin = re.sub(r'[^\d]', '', bin)
        full_card_input = is_full_card_number(clean_bin)
        card_number = clean_bin if full_card_input else generate_card_number(clean_bin)

        cards.append(f"{card_number}|{expiry_month.zfill(2)}|20{expiry_year}|{card_cvv}")

    content = (
        f"BIN: {bin[:6]}\n"
        f"SCHEME: {bin_info.get('scheme')}\n"
        f"TYPE: {bin_info.get('type')}\n"
        f"TIER: {bin_info.get('tier')}\n"
        f"PREPAID: {bin_info.get('prepaid')}\n"
        f"BANK: {bin_info.get('bank')}\n"
        f"COUNTRY: {bin_info.get('country')} ({bin_info.get('country_code')}) {bin_info.get('flag')}\n"
        f"CURRENCY: {bin_info.get('currency')}\n"
        f"==============================\n" +
        "\n".join(cards)
    )

    filename = f"cards_{bin[:6]}.txt"
    response = make_response(content)
    response.headers['Content-Disposition'] = f"attachment; filename={filename}"
    response.headers['Content-Type'] = "text/plain; charset=utf-8"
    return response

@app.route("/bin/<bin>", methods=["GET"])
def bin_lookup(bin):
    bin_info = get_bin_info(bin)
    if not bin_info:
        raise NotFound("BIN not found in any source")

    response_data = OrderedDict([
        ("bin", bin[:6]),
        ("bank", bin_info.get("bank")),
        ("country", bin_info.get("country")),
        ("country_code", bin_info.get("country_code")),
        ("flag", bin_info.get("flag")),
        ("scheme", bin_info.get("scheme")),
        ("type", bin_info.get("type")),
        ("tier", bin_info.get("tier")),
        ("currency", bin_info.get("currency"))
    ])

    json_string = json.dumps(response_data, indent=2, ensure_ascii=False) 
    response = make_response(json_string)
    response.headers['Content-Type'] = 'application/json; charset=utf-8' 
    return response

@app.route("/health", methods=["GET"])
def health_check():
    return jsonify({"status": "healthy", "timestamp": datetime.utcnow().isoformat()})

if __name__ == "__main__":
    app.run()
