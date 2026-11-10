#!/usr/bin/env python3
"""
Meesterslijpers – Google XML (RSS) productfeed -> OpenAI Ads productfeed (CSV / CSV.GZ)

Bron:   https://www.meesterslijpers.nl/feed/google.xml
Output: dist/meesterslijpers_openai.csv     (voor "Verbind je feed via een URL")
        dist/meesterslijpers_openai.csv.gz  (voor SFTP-upload)
        dist/report.txt                     (controlerapport)
Staat:  state/products.json                 (producten van eerdere runs, via de Actions-cache)

Alleen standaardbibliotheek, geen extra packages nodig.
"""

import csv
import gzip
import json
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import date, datetime, timezone

FEED_URL = os.environ.get("FEED_URL", "https://www.meesterslijpers.nl/feed/google.xml")
OUT_DIR = os.environ.get("OUT_DIR", "dist")
OUT_NAME = os.environ.get("OUT_NAME", "meesterslijpers_openai")
STATE_FILE = os.environ.get("STATE_FILE", "state/products.json")

SELLER_NAME = os.environ.get("SELLER_NAME", "Meesterslijpers")
SELLER_URL = os.environ.get("SELLER_URL", "https://www.meesterslijpers.nl/")
# Leeg = producten zonder merk overslaan (Meesterslijpers verkoopt veel merken,
# dus een vast merk invullen zou onjuist zijn).
BRAND_FALLBACK = os.environ.get("BRAND_FALLBACK", "")
# Leeg = alle merken. Anders kommagescheiden, bijv. "Kazoku,Global".
INCLUDE_BRANDS = {b.strip().lower() for b in os.environ.get("INCLUDE_BRANDS", "").split(",") if b.strip()}
KEEP_OUT_OF_STOCK = os.environ.get("KEEP_OUT_OF_STOCK", "true").lower() == "true"
# Hoe lang een product dat uit de bronfeed verdwijnt nog als uitverkocht wordt gemeld.
MISSING_DAYS = int(os.environ.get("MISSING_DAYS", "14"))
# Veiligheidsremmen
MIN_PRODUCTS = int(os.environ.get("MIN_PRODUCTS", "5000"))
MAX_DROP_PCT = float(os.environ.get("MAX_DROP_PCT", "20"))

NS_G = "http://base.google.com/ns/1.0"
NS_ATOM = "http://www.w3.org/2005/Atom"
G = "{%s}" % NS_G

COLUMNS = [
    "item_id", "title", "description", "url", "brand", "seller_name",
    "image_url", "availability", "price", "sale_price", "gtin", "mpn", "condition",
    "product_category", "seller_url", "is_ads_eligible",
]

VALID_AVAILABILITY = {"in_stock", "out_of_stock", "pre_order", "backorder", "unknown"}
AVAILABILITY_MAP = {"preorder": "pre_order", "in stock": "in_stock", "out of stock": "out_of_stock"}
VALID_CONDITION = {"new", "refurbished", "used"}
PLACEHOLDERS = {"", "-", "null", "none", "n/a", "na", "unknown", "0"}
PRICE_RE = re.compile(r"^\s*(\d+(?:[.,]\d{1,2})?)\s*([A-Za-z]{3})\s*$")
WS_RE = re.compile(r"\s+")
TAG_RE = re.compile(r"<[^>]+>")
ITEM_TAGS = (f"{{{NS_ATOM}}}entry", "entry", "item")


def clean_text(value: str) -> str:
    if not value:
        return ""
    value = TAG_RE.sub(" ", value)
    return WS_RE.sub(" ", value).strip()


def is_placeholder(value: str) -> bool:
    return value.strip().lower() in PLACEHOLDERS


def valid_gtin(code: str) -> bool:
    if not code.isdigit() or len(code) not in (8, 12, 13, 14):
        return False
    digits = [int(c) for c in code]
    check = digits.pop()
    total = sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(digits)))
    return (10 - total % 10) % 10 == check


def parse_price(raw: str):
    """'119 EUR' / '1,073.95 EUR' -> (119.0, 'EUR') of None."""
    raw = (raw or "").replace(",", "") if re.search(r"\d,\d{3}\.", raw or "") else (raw or "")
    m = PRICE_RE.match(raw)
    if not m:
        return None
    amount = float(m.group(1).replace(",", "."))
    return (amount, m.group(2).upper()) if amount > 0 else None


def fmt_price(p) -> str:
    return f"{p[0]:.2f} {p[1]}"


def sale_active(window: str, now: datetime) -> bool:
    """Geen periode = actief. Anders alleen binnen 'start/eind'."""
    if not window:
        return True
    try:
        start, end = (datetime.fromisoformat(x.strip().replace("Z", "+00:00")) for x in window.split("/"))
    except ValueError:
        return False
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    return start <= now <= end


def field(el, name: str) -> str:
    """Leest g:name, en anders een gewone <name> (sommige feeds mengen beide)."""
    found = el.find(G + name)
    if found is None:
        found = el.find(name)
    return found.text.strip() if found is not None and found.text else ""


def fix_url(url: str) -> str:
    url = url.strip().replace(" ", "%20")
    if url.startswith("http://"):
        url = "https://" + url[len("http://"):]
    return url


def download(url: str) -> str:
    path = os.path.join(OUT_DIR, "source.xml")
    req = urllib.request.Request(url, headers={"User-Agent": "meesterslijpers-openai-feed/1.0"})
    with urllib.request.urlopen(req, timeout=300) as resp, open(path, "wb") as fh:
        while chunk := resp.read(1 << 20):
            fh.write(chunk)
    return path


def count_ids(source_path: str) -> Counter:
    ids = Counter()
    for _, el in ET.iterparse(source_path, events=("end",)):
        if el.tag in ITEM_TAGS:
            ids[field(el, "id")] += 1
            el.clear()
    return ids


def convert(source_path: str):
    stats, issues, examples = Counter(), Counter(), {}
    rows = []
    now = datetime.now(timezone.utc)
    id_counts = count_ids(source_path)

    def note(kind, item_id):
        issues[kind] += 1
        lst = examples.setdefault(kind, [])
        if len(lst) < 5:
            lst.append(item_id)

    for _, el in ET.iterparse(source_path, events=("end",)):
        if el.tag not in ITEM_TAGS:
            continue
        stats["entries"] += 1

        item_id = field(el, "id")
        title = clean_text(field(el, "title"))
        description = clean_text(field(el, "description"))
        url = fix_url(field(el, "link"))
        image_url = fix_url(field(el, "image_link"))
        availability = field(el, "availability").lower()
        availability = AVAILABILITY_MAP.get(availability, availability)
        price = parse_price(field(el, "price"))
        sale = parse_price(field(el, "sale_price"))
        sale_window = field(el, "sale_price_effective_date")
        gtin = field(el, "gtin")
        mpn = clean_text(field(el, "mpn"))
        condition = field(el, "condition").lower()
        brand = clean_text(field(el, "brand"))
        category = clean_text(field(el, "google_product_category") or field(el, "product_type"))
        el.clear()

        # --- verplichte velden; rij overslaan als iets ontbreekt
        if not item_id:
            note("geen id (overgeslagen)", "?")
            continue
        if id_counts[item_id] > 1:
            # Twee verschillende producten met hetzelfde ID: beide weglaten,
            # anders wisselt het getoonde product per dag.
            note("dubbel id, alle varianten overgeslagen", item_id)
            continue
        if not title:
            note("geen titel (overgeslagen)", item_id)
            continue
        if not url.startswith("https://"):
            note("geen/ongeldige url (overgeslagen)", item_id)
            continue
        if not image_url.startswith("https://"):
            note("geen afbeelding (overgeslagen)", item_id)
            continue
        if price is None:
            note("ongeldige prijs (overgeslagen)", item_id)
            continue
        if not brand or is_placeholder(brand):
            if not BRAND_FALLBACK:
                note("geen merk (overgeslagen)", item_id)
                continue
            brand = BRAND_FALLBACK
            issues["brand = fallback"] += 1
        if INCLUDE_BRANDS and brand.lower() not in INCLUDE_BRANDS:
            stats["ander merk weggelaten"] += 1
            continue
        if availability not in VALID_AVAILABILITY:
            note("onbekende availability -> unknown", item_id)
            availability = "unknown"

        # --- opschonen / aanvullen
        if not description:
            description = title
        sale_price = ""
        if sale:
            if sale[1] == price[1] and sale[0] < price[0] and sale_active(sale_window, now):
                sale_price = fmt_price(sale)
                stats["actieprijs actief"] += 1
            else:
                stats["actieprijs genegeerd (niet lager of buiten periode)"] += 1
        if gtin and not valid_gtin(gtin):
            note("ongeldige gtin (weggelaten)", item_id)
            gtin = ""
        if condition not in VALID_CONDITION:
            condition = "new"
        if is_placeholder(category):
            category = ""
        if "%20" in url:
            note("spatie in url (vervangen door %20)", item_id)

        eligible = availability in ("in_stock", "pre_order", "backorder")
        if not eligible and not KEEP_OUT_OF_STOCK:
            stats["uitverkocht weggelaten"] += 1
            continue

        rows.append({
            "item_id": item_id,
            "title": title[:150],
            "description": description[:5000],
            "url": url,
            "brand": brand[:70],
            "seller_name": SELLER_NAME,
            "image_url": image_url,
            "availability": availability,
            "price": fmt_price(price),
            "sale_price": sale_price,
            "gtin": gtin,
            "mpn": mpn[:70],
            "condition": condition,
            "product_category": category,
            "seller_url": SELLER_URL,
            "is_ads_eligible": "true" if eligible else "false",
        })

    return rows, stats, issues, examples


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"eligible": 0, "products": {}}


def carry_over_missing(rows, state, stats):
    """Producten die uit de bronfeed verdwenen zijn nog MISSING_DAYS als uitverkocht melden.
    De bronfeed bevat alleen producten op voorraad; zonder dit zou OpenAI ze kunnen blijven tonen."""
    today = date.today()
    current = {r["item_id"] for r in rows}
    new_products = {r["item_id"]: {"row": {**r, "description": r["description"][:500]}, "missing_since": None}
                    for r in rows}
    for item_id, entry in state.get("products", {}).items():
        if item_id in current:
            continue
        since = date.fromisoformat(entry["missing_since"]) if entry.get("missing_since") else today
        if (today - since).days >= MISSING_DAYS:
            stats["verdwenen product na termijn verwijderd"] += 1
            continue
        row = {**entry["row"], "availability": "out_of_stock", "sale_price": "", "is_ads_eligible": "false"}
        rows.append(row)
        new_products[item_id] = {"row": row, "missing_since": since.isoformat()}
        stats["verdwenen product gemeld als uitverkocht"] += 1
    return new_products


def save_state(products, eligible):
    os.makedirs(os.path.dirname(STATE_FILE) or ".", exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as fh:
        json.dump({"eligible": eligible, "updated": date.today().isoformat(), "products": products}, fh)


def write_outputs(rows):
    csv_path = os.path.join(OUT_DIR, f"{OUT_NAME}.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, quoting=csv.QUOTE_MINIMAL)
        w.writeheader()
        w.writerows(rows)
    with open(csv_path, "rb") as src, gzip.open(csv_path + ".gz", "wb") as dst:
        dst.write(src.read())


def write_report(rows, stats, issues, examples, prev_eligible, brand_counts):
    lines = [
        "Meesterslijpers -> OpenAI Ads feed",
        f"Bron: {FEED_URL}",
        f"Datum: {date.today().isoformat()}",
        "",
        f"Items in XML:            {stats['entries']}",
        f"Rijen in output:         {len(rows)}",
        f"  is_ads_eligible=true:  {stats['ads_eligible']}  (vorige run: {prev_eligible or 'onbekend'})",
        f"  is_ads_eligible=false: {len(rows) - stats['ads_eligible']}",
        "",
        "Merken (eligible): " + ", ".join(f"{b} {n}" for b, n in brand_counts.most_common(8)),
        "",
        "Tellingen:",
    ]
    for kind, n in sorted(stats.items()):
        if kind not in ("entries", "ads_eligible"):
            lines.append(f"  {kind}: {n}")
    lines += ["", "Meldingen:"]
    for kind, n in issues.most_common():
        ex = ", ".join(examples.get(kind, []))
        lines.append(f"  {kind}: {n}" + (f"  (bijv. {ex})" if ex else ""))
    text = "\n".join(lines) + "\n"
    with open(os.path.join(OUT_DIR, "report.txt"), "w", encoding="utf-8") as fh:
        fh.write(text)
    print(text)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    source = sys.argv[1] if len(sys.argv) > 1 else download(FEED_URL)
    rows, stats, issues, examples = convert(source)
    stats["ads_eligible"] = sum(r["is_ads_eligible"] == "true" for r in rows)
    brand_counts = Counter(r["brand"] for r in rows if r["is_ads_eligible"] == "true")

    state = load_state()
    prev_eligible = state.get("eligible", 0)
    eligible = stats["ads_eligible"]
    products = carry_over_missing(rows, state, stats) if KEEP_OUT_OF_STOCK else {}
    write_report(rows, stats, issues, examples, prev_eligible, brand_counts)

    # Veiligheidsremmen: bij een kapotte bronfeed niets publiceren en de staat niet bijwerken.
    if eligible < MIN_PRODUCTS:
        sys.exit(f"FOUT: slechts {eligible} eligible producten (minimum {MIN_PRODUCTS}). Niet gepubliceerd.")
    if prev_eligible and eligible < prev_eligible * (1 - MAX_DROP_PCT / 100):
        sys.exit(f"FOUT: eligible daalde van {prev_eligible} naar {eligible} (meer dan {MAX_DROP_PCT:.0f}%). "
                 "Niet gepubliceerd. Controleer de bronfeed; is de daling terecht, start dan handmatig "
                 "met MAX_DROP_PCT=100.")

    write_outputs(rows)
    save_state(products, eligible)
    if source.endswith("source.xml"):
        os.remove(source)


if __name__ == "__main__":
    main()
