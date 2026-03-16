from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from html import unescape
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


BASE_URL = "https://vkusvill.ru"
CATALOG_URL = f"{BASE_URL}/goods/gotovaya-eda/"
FIXED_ADDRESS = "г. Москва ул. Образцова 24"
# Coordinates for Moscow, ul. Obraztsova, 24.
FIXED_COORDS = (55.7897968, 37.6102455)

ROOT_DIR = Path(__file__).resolve().parent
CACHE_DIR = ROOT_DIR / "cache"
DETAIL_CACHE_PATH = CACHE_DIR / "product-details.json"
PAYLOAD_CACHE_PATH = CACHE_DIR / "latest-meals.json"
LEGACY_NUTRITION_DATA_PATH = ROOT_DIR / "data" / "meals-data.js"
SEED_PAYLOAD_PATH = ROOT_DIR / "data" / "seed-meals.json"

REQUEST_TIMEOUT = 25
PAGE_WORKERS = 8
STOCK_WORKERS = 16
DETAIL_WORKERS = 10
DETAIL_TTL = timedelta(days=30)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0 Safari/537.36 "
    "vkussvil-local-parser"
)

THREAD_LOCAL = threading.local()
SCRAPE_LOCK = threading.Lock()
REFRESH_STATE_LOCK = threading.Lock()
REFRESH_THREAD: threading.Thread | None = None
REFRESH_STARTED_AT = ""
REFRESH_FINISHED_AT = ""
REFRESH_LAST_ERROR = ""
NUTRITION_INDEX: dict[str, dict[str, Any]] | None = None

SHOP_LIST_RE = re.compile(r"shopListItems\s*=\s*(\{.*?\});", re.S)
TOTAL_PRODUCTS_RE = re.compile(
    r'id="js-catalog-page-param-total-products"\s+value="(\d+)"'
)

MEAT_AND_FISH_KEYWORDS = (
    "мяс",
    "куриц",
    "курин",
    "цыпл",
    "птиц",
    "индей",
    "говяд",
    "свин",
    "баран",
    "утк",
    "крол",
    "окороч",
    "бедр",
    "голен",
    "фарш",
    "ветчин",
    "бекон",
    "колбас",
    "сосиск",
    "шпик",
    "салями",
    "пепперони",
    "бургер",
    "лосос",
    "семг",
    "сёмг",
    "форел",
    "дорад",
    "сибас",
    "сом",
    "минта",
    "судак",
    "хек",
    "палтус",
    "окун",
    "камбал",
    "рыб",
    "треск",
    "тун",
    "горбуш",
    "краб",
    "кревет",
    "мид",
    "кальмар",
    "осьмин",
    "морепродукт",
    "анчоус",
    "сардин",
    "скумбр",
    "сельд",
    "угор",
    "икр",
)

SIMPLE_TITLE_PATTERNS = (
    "грудк",
    "филе",
    "стейк",
    "эскалоп",
    "тушка",
    "дорадо",
    "сибас",
    "лосос",
    "форел",
    "рыба",
    "креветк",
    "яйц",
    "индейк",
    "куриц",
    "цыпл",
    "сом",
    "куриная грудка",
)

TRIVIAL_INGREDIENT_KEYWORDS = (
    "вода",
    "соль",
    "перец",
    "специ",
    "пряност",
    "масло",
    "уксус",
    "сахар",
    "крахмал",
    "дрожж",
    "чеснок",
    "лук",
    "паприк",
    "кориандр",
    "кумин",
    "зира",
    "тимьян",
    "розмарин",
    "базилик",
    "орегано",
    "куркум",
    "маринад",
    "лимон",
    "сок",
    "кислот",
    "соус",
    "зелень",
    "семена",
    "добавк",
    "желир",
    "антиокис",
    "регулятор",
    "ароматиз",
    "эмульг",
    "стабилиз",
)

COMPLEX_TITLE_MARKERS = (
    " с ",
    " и ",
    " по-",
    "ролл",
    "салат",
    "суп",
    "бургер",
    "бейгл",
    "сэндвич",
    "сандвич",
    "паста",
    "плов",
    "каша",
    "пюре",
    "рис",
    "греч",
    "лапша",
    "рагу",
    "жаркое",
    "буррито",
    "пельм",
    "вареник",
    "котлет",
    "биточ",
    "шницел",
    "шаурм",
    "запеканк",
    "пицц",
)

IGNORED_COMPOSITION_PARTS = (
    "вода",
    "соль",
    "перец",
    "специи",
    "пряности",
    "масло",
    "масло растительное",
    "подсолнечное масло",
    "оливковое масло",
    "регулятор кислотности",
    "антиокислитель",
    "загуститель",
    "консервант",
    "ароматизатор",
    "стабилизатор",
    "эмульгатор",
    "краситель",
    "уксус",
    "сахар",
    "крахмал",
    "дрожжи",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_session() -> requests.Session:
    session = getattr(THREAD_LOCAL, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            }
        )
        THREAD_LOCAL.session = session
    return session


def fetch_text(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> str:
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            response = get_session().get(
                url,
                params=params,
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            return response.text
        except Exception as error:  # noqa: BLE001
            last_error = error
            if attempt == 0:
                time.sleep(0.3)
    raise RuntimeError(f"Failed to fetch {url}: {last_error}") from last_error


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    value = unescape(str(value))
    value = value.replace("\xa0", " ")
    return re.sub(r"\s+", " ", value).strip()


def parse_price(value: str | None) -> float | None:
    if not value:
        return None
    clean = normalize_text(value).replace(",", ".")
    match = re.search(r"\d+(?:\.\d+)?", clean)
    return float(match.group(0)) if match else None


def extract_product_id_from_url(url: str) -> str:
    match = re.search(r"-(\d+)\.html", url or "")
    return match.group(1) if match else ""


def normalize_weight_text(weight_text: str | None) -> str:
    normalized = normalize_text(weight_text)
    if not normalized:
        return ""

    marker_index = re.search(
        r"\s(?:Как приготовить|Важные детали|Способ приготовления)\b",
        normalized,
        flags=re.I,
    )
    compact = normalized[: marker_index.start()] if marker_index else normalized
    compact = compact.strip()
    if not compact:
        return ""

    kg_match = re.search(r"(\d+(?:[.,]\d+)?)\s*кг", compact, flags=re.I)
    if kg_match:
        return f"{kg_match.group(1).replace('.', ',')} кг"

    gram_match = re.search(r"(\d+(?:[.,]\d+)?)\s*г", compact, flags=re.I)
    if gram_match:
        return f"{gram_match.group(1).replace('.', ',')} г"

    pieces_match = re.search(r"(\d+(?:[.,]\d+)?)\s*шт", compact, flags=re.I)
    if pieces_match:
        return f"{pieces_match.group(1).replace('.', ',')} шт"

    return compact


def parse_weight_to_grams(weight_text: str | None) -> float | None:
    normalized = normalize_weight_text(weight_text).replace(",", ".").lower()
    if not normalized:
        return None

    kg_match = re.search(r"(\d+(?:\.\d+)?)\s*кг(?:\s|$)", normalized)
    if kg_match:
        return float(kg_match.group(1)) * 1000

    gram_match = re.search(r"(\d+(?:\.\d+)?)\s*г(?:\s|$)", normalized)
    if gram_match:
        return float(gram_match.group(1))

    return None


def extract_image_url(card: BeautifulSoup) -> str:
    image = card.select_one("img")
    if not image:
        return ""

    candidates = [
        image.get("data-src"),
        image.get("src"),
        image.get("data-original"),
        image.get("srcset"),
        image.get("data-srcset"),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        url = candidate.split(",")[0].strip().split(" ")[0].strip()
        if url:
            return urljoin(BASE_URL, url)
    return ""


def parse_catalog_cards(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    cards: list[dict[str, Any]] = []

    for card in soup.select(".ProductCard.js-product-cart"):
        product_id = normalize_text(card.get("data-id"))
        link = card.select_one(".ProductCard__link")
        title = normalize_text(link.get_text(" ", strip=True) if link else "")
        url = urljoin(BASE_URL, link.get("href")) if link and link.get("href") else ""
        weight = normalize_text(
            card.select_one(".ProductCard__weight").get_text(" ", strip=True)
            if card.select_one(".ProductCard__weight")
            else ""
        )
        price_node = card.select_one('[itemprop="price"]')
        availability_node = card.select_one('[itemprop="availability"]')

        if not product_id or not title or not url:
            continue

        cards.append(
            {
                "id": product_id,
                "title": title,
                "url": url,
                "weight": weight,
                "price": parse_price(price_node.get("content") if price_node else None),
                "image_url": extract_image_url(card),
                "catalog_availability": normalize_text(
                    (availability_node.get("content") if availability_node else "")
                    .removeprefix("https://schema.org/")
                ),
            }
        )

    return cards


def parse_total_pages(html: str) -> int:
    soup = BeautifulSoup(html, "html.parser")
    pages = []
    for node in soup.select(".VV_Pager__Item[data-page]"):
        data_page = node.get("data-page", "").strip()
        if data_page.isdigit():
            pages.append(int(data_page))
    return max(pages) if pages else 1


def parse_total_catalog_count(html: str, fallback: int) -> int:
    match = TOTAL_PRODUCTS_RE.search(html)
    if match:
        return int(match.group(1))
    return fallback


def choose_best_nutrition_variant(
    variants: list[dict[str, Any]],
    weight_text: str | None,
) -> dict[str, Any] | None:
    weight_grams = parse_weight_to_grams(weight_text)
    best_variant = None
    best_key = None

    for variant in variants:
        protein = float(variant.get("protein") or 0)
        fats = float(variant.get("fats") or 0)
        carbs = float(variant.get("carbs") or 0)
        calories = float(variant.get("calories") or 0)
        total_protein = protein * weight_grams / 100 if weight_grams else None
        total_calories = calories * weight_grams / 100 if weight_grams else None
        total_score = (
            (total_protein / total_calories) * 100
            if total_protein is not None and total_calories not in (None, 0)
            else None
        )
        key = (
            total_score if total_score is not None else float("-inf"),
            total_protein if total_protein is not None else float("-inf"),
            -calories,
        )

        if best_key is None or key > best_key:
            best_key = key
            best_variant = {
                "manufacturer": normalize_text(variant.get("manufacturer")) or "Без уточнения",
                "protein_per_100": protein,
                "fats_per_100": fats,
                "carbs_per_100": carbs,
                "calories_per_100": calories,
                "weight": normalize_weight_text(weight_text),
                "weight_grams": weight_grams,
                "total_protein": total_protein,
                "total_calories": total_calories,
                "total_score": total_score,
            }

    return best_variant


def load_nutrition_index() -> dict[str, dict[str, Any]]:
    global NUTRITION_INDEX

    if NUTRITION_INDEX is not None:
        return NUTRITION_INDEX

    NUTRITION_INDEX = {}
    if not LEGACY_NUTRITION_DATA_PATH.exists():
        return NUTRITION_INDEX

    try:
        raw = LEGACY_NUTRITION_DATA_PATH.read_text(encoding="utf-8")
        payload = json.loads(raw.split("=", 1)[1].rstrip(" ;\n"))
    except Exception:  # noqa: BLE001
        return NUTRITION_INDEX

    for product in payload.get("products", []):
        product_id = extract_product_id_from_url(product.get("url", ""))
        if not product_id:
            continue
        best_variant = choose_best_nutrition_variant(
            product.get("variants", []),
            product.get("weight"),
        )
        if best_variant:
            NUTRITION_INDEX[product_id] = best_variant

    return NUTRITION_INDEX


def fetch_catalog_page(page: int) -> list[dict[str, Any]]:
    url = CATALOG_URL if page == 1 else f"{CATALOG_URL}?PAGEN_1={page}"
    return parse_catalog_cards(fetch_text(url))


def scrape_catalog() -> tuple[list[dict[str, Any]], int]:
    first_page_html = fetch_text(CATALOG_URL)
    products_by_id = {
        product["id"]: product for product in parse_catalog_cards(first_page_html)
    }

    total_pages = parse_total_pages(first_page_html)
    total_catalog_count = parse_total_catalog_count(
        first_page_html, fallback=len(products_by_id)
    )

    if total_pages > 1:
        with ThreadPoolExecutor(max_workers=PAGE_WORKERS) as executor:
            futures = {
                executor.submit(fetch_catalog_page, page): page
                for page in range(2, total_pages + 1)
            }
            for future in as_completed(futures):
                for product in future.result():
                    products_by_id[product["id"]] = product

    products = list(products_by_id.values())
    products.sort(key=lambda item: (item["title"].lower(), item["id"]))
    return products, total_catalog_count


def load_detail_cache() -> dict[str, dict[str, Any]]:
    if not DETAIL_CACHE_PATH.exists():
        return {}
    try:
        return json.loads(DETAIL_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def load_payload_cache() -> dict[str, Any] | None:
    if not PAYLOAD_CACHE_PATH.exists():
        return None
    try:
        return json.loads(PAYLOAD_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def save_payload_cache(payload: dict[str, Any]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    PAYLOAD_CACHE_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_seed_payload() -> dict[str, Any] | None:
    if not SEED_PAYLOAD_PATH.exists():
        return None
    try:
        return json.loads(SEED_PAYLOAD_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def save_detail_cache(cache: dict[str, dict[str, Any]]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    DETAIL_CACHE_PATH.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def is_cache_fresh(record: dict[str, Any] | None) -> bool:
    if not record:
        return False
    fetched_at = record.get("details_fetched_at")
    if not fetched_at:
        return False
    try:
        fetched_dt = datetime.fromisoformat(fetched_at)
    except ValueError:
        return False
    return datetime.now(timezone.utc) - fetched_dt <= DETAIL_TTL


def extract_composition_from_page(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")

    for selector in (
        ".VV23_DetailProdPageInfoDescItem__Desc._sostav",
        ".Product__text--composition",
    ):
        node = soup.select_one(selector)
        if node:
            text = normalize_text(node.get_text(" ", strip=True))
            if text:
                return text

    for header in soup.select("h2, h3, h4, dt"):
        title = normalize_text(header.get_text(" ", strip=True)).lower()
        if title != "состав":
            continue
        container = header.parent
        if not container:
            continue
        for selector in (
            ".VV23_DetailProdPageInfoDescItem__Desc",
            ".Product__text",
            "dd",
            "p",
        ):
            node = container.select_one(selector)
            if node:
                text = normalize_text(node.get_text(" ", strip=True))
                if text and text.lower() != "состав":
                    return text

    return ""


def split_composition(composition: str) -> list[str]:
    text = normalize_text(strip_composition_disclaimers(composition)).lower()
    if not text:
        return []
    text = re.sub(r"\([^)]*\)", "", text)
    parts = re.split(r"[,;./]", text)
    return [part.strip() for part in parts if part.strip()]


def meaningful_ingredients(composition: str) -> list[str]:
    result = []
    for part in split_composition(composition):
        if len(part) < 3:
            continue
        if any(ignored in part for ignored in IGNORED_COMPOSITION_PARTS):
            continue
        result.append(part)
    return result


def is_trivial_ingredient(part: str) -> bool:
    lowered = normalize_text(part).lower()
    if not lowered:
        return True
    return any(keyword in lowered for keyword in TRIVIAL_INGREDIENT_KEYWORDS)


def title_looks_simple(title: str) -> bool:
    normalized_title = f" {normalize_text(title).lower()} "
    if not normalized_title.strip():
        return False
    if any(marker in normalized_title for marker in COMPLEX_TITLE_MARKERS):
        return False
    return any(pattern in normalized_title for pattern in SIMPLE_TITLE_PATTERNS)


def is_meatless(composition: str) -> bool:
    normalized = normalize_text(strip_composition_disclaimers(composition)).lower()
    if not normalized:
        return False
    return not any(keyword in normalized for keyword in MEAT_AND_FISH_KEYWORDS)


def strip_composition_disclaimers(composition: str) -> str:
    text = normalize_text(composition)
    if not text:
        return ""

    markers = (
        "Продукция производится на предприятии",
        "На предприятии используются аллергены",
        "Может содержать следы",
        "Возможны следы",
        "Аллергены:",
    )

    cut_positions = [text.find(marker) for marker in markers if text.find(marker) != -1]
    if cut_positions:
        text = text[: min(cut_positions)]

    return text.strip(" .;")


def is_simple_dish(title: str, composition: str) -> bool:
    if not title_looks_simple(title):
        return False

    ingredients = meaningful_ingredients(composition)
    substantial_ingredients = [
        ingredient for ingredient in ingredients if not is_trivial_ingredient(ingredient)
    ]

    if len(substantial_ingredients) <= 1:
        return True

    primary_parts = [
        part
        for part in substantial_ingredients
        if any(pattern in part for pattern in SIMPLE_TITLE_PATTERNS)
    ]
    if len(primary_parts) == 1 and len(substantial_ingredients) <= 2:
        return True

    return len(substantial_ingredients) <= 2


def fetch_product_detail(product: dict[str, Any]) -> dict[str, Any]:
    html = fetch_text(product["url"])
    composition = extract_composition_from_page(html)
    return {
        "composition": composition,
        "is_simple_dish": is_simple_dish(product["title"], composition),
        "is_meatless": is_meatless(composition),
        "details_fetched_at": now_iso(),
    }


def haversine_distance_km(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
) -> float:
    radius = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return radius * c


def parse_stock_quantity(label: str) -> int | None:
    match = re.search(r"(\d+)", label)
    if match:
        return int(match.group(1))
    lowered = label.lower()
    if "мало" in lowered or "в наличии" in lowered:
        return 1
    return None


def fetch_stock(product: dict[str, Any]) -> dict[str, Any]:
    if product.get("catalog_availability") and product["catalog_availability"] != "InStock":
        return {
            "stock_available": False,
            "stock_quantity": None,
            "stock_text": "Нет в наличии",
            "stock_store_address": "",
            "stock_distance_km": None,
        }

    html = fetch_text(
        f"{BASE_URL}/ajax/product_load_map_rests.php",
        params={"nonajax": "Y", "id": product["id"]},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    match = SHOP_LIST_RE.search(html)
    if not match:
        return {
            "stock_available": False,
            "stock_quantity": None,
            "stock_text": "Нет в наличии",
            "stock_store_address": "",
            "stock_distance_km": None,
        }

    shops = json.loads(match.group(1))
    if not shops:
        return {
            "stock_available": False,
            "stock_quantity": None,
            "stock_text": "Нет в наличии",
            "stock_store_address": "",
            "stock_distance_km": None,
        }

    best_shop = None
    best_distance = None

    for shop in shops.values():
        try:
            distance = haversine_distance_km(
                FIXED_COORDS[0],
                FIXED_COORDS[1],
                float(shop["LAT"]),
                float(shop["LON"]),
            )
        except Exception:  # noqa: BLE001
            continue

        if best_distance is None or distance < best_distance:
            best_shop = shop
            best_distance = distance

    if not best_shop:
        return {
            "stock_available": False,
            "stock_quantity": None,
            "stock_text": "Нет в наличии",
            "stock_store_address": "",
            "stock_distance_km": None,
        }

    stock_text = normalize_text(best_shop.get("RESTS"))
    return {
        "stock_available": True,
        "stock_quantity": parse_stock_quantity(stock_text),
        "stock_text": stock_text or "В наличии",
        "stock_store_address": normalize_text(best_shop.get("ADDRESS")),
        "stock_distance_km": round(best_distance, 2) if best_distance is not None else None,
    }


def build_payload() -> dict[str, Any]:
    with SCRAPE_LOCK:
        catalog_products, total_catalog_count = scrape_catalog()
        nutrition_index = load_nutrition_index()

        stock_by_id: dict[str, dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=STOCK_WORKERS) as executor:
            futures = {
                executor.submit(fetch_stock, product): product
                for product in catalog_products
            }
            for future in as_completed(futures):
                product = futures[future]
                try:
                    stock_by_id[product["id"]] = future.result()
                except Exception:  # noqa: BLE001
                    stock_by_id[product["id"]] = {
                        "stock_available": False,
                        "stock_quantity": None,
                        "stock_text": "Не удалось проверить",
                        "stock_store_address": "",
                        "stock_distance_km": None,
                    }

        available_products = [
            {**product, **stock_by_id[product["id"]]}
            for product in catalog_products
            if stock_by_id[product["id"]]["stock_available"]
        ]

        detail_cache = load_detail_cache()
        products_needing_details = [
            product
            for product in available_products
            if not is_cache_fresh(detail_cache.get(product["id"]))
        ]

        if products_needing_details:
            with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as executor:
                futures = {
                    executor.submit(fetch_product_detail, product): product
                    for product in products_needing_details
                }
                for future in as_completed(futures):
                    product = futures[future]
                    try:
                        detail_cache[product["id"]] = future.result()
                    except Exception:  # noqa: BLE001
                        detail_cache.setdefault(
                            product["id"],
                            {
                                "composition": "",
                                "is_simple_dish": False,
                                "is_meatless": False,
                                "details_fetched_at": now_iso(),
                            },
                        )
            save_detail_cache(detail_cache)

        rows = []
        for product in available_products:
            details = detail_cache.get(
                product["id"],
                {
                    "composition": "",
                },
            )
            composition = details.get("composition", "")
            nutrition = nutrition_index.get(product["id"], {})
            rows.append(
                {
                    "id": product["id"],
                    "title": product["title"],
                    "url": product["url"],
                    "image_url": product["image_url"],
                    "weight": product["weight"],
                    "price": product["price"],
                    "manufacturer": nutrition.get("manufacturer", "Без уточнения"),
                    "protein_per_100": nutrition.get("protein_per_100"),
                    "fats_per_100": nutrition.get("fats_per_100"),
                    "carbs_per_100": nutrition.get("carbs_per_100"),
                    "calories_per_100": nutrition.get("calories_per_100"),
                    "weight_grams": nutrition.get("weight_grams"),
                    "total_protein": nutrition.get("total_protein"),
                    "total_calories": nutrition.get("total_calories"),
                    "total_score": nutrition.get("total_score"),
                    "composition": composition,
                    "is_simple_dish": is_simple_dish(product["title"], composition),
                    "is_meatless": is_meatless(composition),
                    "stock_quantity": product["stock_quantity"],
                    "stock_text": product["stock_text"],
                    "stock_store_address": product["stock_store_address"],
                    "stock_distance_km": product["stock_distance_km"],
                }
            )

        rows.sort(
            key=lambda item: (
                item["price"] is None,
                item["price"] if item["price"] is not None else float("inf"),
                item["title"].lower(),
            )
        )

        return {
            "source": CATALOG_URL,
            "address": FIXED_ADDRESS,
            "address_coordinates": {
                "lat": FIXED_COORDS[0],
                "lon": FIXED_COORDS[1],
            },
            "shop_strategy": (
                "Для каждого блюда выбирается ближайший магазин с остатком "
                "к адресу на улице Образцова, 24."
            ),
            "parsed_at": now_iso(),
            "total_catalog_count": total_catalog_count,
            "available_count": len(rows),
            "products": rows,
        }


def is_refreshing() -> bool:
    with REFRESH_STATE_LOCK:
        return REFRESH_THREAD is not None and REFRESH_THREAD.is_alive()


def get_refresh_meta() -> dict[str, Any]:
    with REFRESH_STATE_LOCK:
        return {
            "refreshing": REFRESH_THREAD is not None and REFRESH_THREAD.is_alive(),
            "refresh_started_at": REFRESH_STARTED_AT,
            "refresh_finished_at": REFRESH_FINISHED_AT,
            "refresh_last_error": REFRESH_LAST_ERROR,
        }


def refresh_payload_in_background() -> None:
    global REFRESH_THREAD
    global REFRESH_STARTED_AT
    global REFRESH_FINISHED_AT
    global REFRESH_LAST_ERROR

    def worker() -> None:
        global REFRESH_FINISHED_AT
        global REFRESH_LAST_ERROR
        try:
            payload = build_payload()
            save_payload_cache(payload)
            with REFRESH_STATE_LOCK:
                REFRESH_LAST_ERROR = ""
                REFRESH_FINISHED_AT = now_iso()
        except Exception as error:  # noqa: BLE001
            with REFRESH_STATE_LOCK:
                REFRESH_LAST_ERROR = str(error)
                REFRESH_FINISHED_AT = now_iso()

    with REFRESH_STATE_LOCK:
        if REFRESH_THREAD is not None and REFRESH_THREAD.is_alive():
            return
        REFRESH_STARTED_AT = now_iso()
        REFRESH_THREAD = threading.Thread(target=worker, daemon=True)
        REFRESH_THREAD.start()


def get_payload_for_response() -> dict[str, Any]:
    cached_payload = load_payload_cache()

    if cached_payload:
        refresh_payload_in_background()
        payload = dict(cached_payload)
        payload.update(get_refresh_meta())
        payload["served_from_cache"] = True
        payload["served_from_seed"] = False
        return payload

    seed_payload = load_seed_payload()
    if seed_payload:
        refresh_payload_in_background()
        payload = dict(seed_payload)
        payload.update(get_refresh_meta())
        payload["served_from_cache"] = True
        payload["served_from_seed"] = True
        return payload

    payload = build_payload()
    save_payload_cache(payload)
    payload = dict(payload)
    payload.update(get_refresh_meta())
    payload["served_from_cache"] = False
    payload["served_from_seed"] = False
    return payload


class AppHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(ROOT_DIR), **kwargs)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/meals":
            self.handle_meals_api()
            return
        if parsed.path == "/api/meals.js":
            self.handle_meals_js()
            return
        super().do_GET()

    def handle_meals_api(self) -> None:
        try:
            payload = get_payload_for_response()
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(HTTPStatus.OK)
        except Exception as error:  # noqa: BLE001
            body = json.dumps(
                {
                    "error": "Не удалось обновить каталог ВкусВилл.",
                    "details": str(error),
                },
                ensure_ascii=False,
            ).encode("utf-8")
            self.send_response(HTTPStatus.INTERNAL_SERVER_ERROR)

        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_meals_js(self) -> None:
        try:
            payload = get_payload_for_response()
            body = (
                "__vkussvilMealsCallback__("
                + json.dumps(payload, ensure_ascii=False)
                + ");"
            ).encode("utf-8")
            self.send_response(HTTPStatus.OK)
        except Exception as error:  # noqa: BLE001
            body = (
                "__vkussvilMealsCallback__({"
                + json.dumps(
                    {
                        "error": "Не удалось обновить каталог ВкусВилл.",
                        "details": str(error),
                    },
                    ensure_ascii=False,
                )[1:-1]
                + "});"
            ).encode("utf-8")
            self.send_response(HTTPStatus.INTERNAL_SERVER_ERROR)

        self.send_header("Content-Type", "application/javascript; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    server = ThreadingHTTPServer((host, port), AppHandler)
    print(f"Serving on http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
