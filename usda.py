"""Optional USDA FoodData Central lookup (stdlib only).

Needs a free API key in data/config.json ("usda_api_key").
Get one at https://api.nal.usda.gov (free signup).
Falls back gracefully — the app works fully on the built-in table without a key.
"""
import json
import urllib.parse
import urllib.request

BASE = "https://api.nal.usda.gov/fdc/v1"

# FDC nutrient numbers -> our per-100g schema keys
NUTRIENT_MAP = {
    1008: "calories",      # Energy (kcal)
    1004: "fat_g",         # Total lipid (fat)
    1258: "sat_fat_g",     # Fatty acids, total saturated
    1257: "cholesterol_mg",
    1093: "sodium_mg",
    1005: "carbs_g",       # Carbohydrate, by difference
    1079: "fiber_g",       # Fiber, total dietary
    269: "sugars_g",       # Sugars, total
    1003: "protein_g",
    1092: "potassium_mg",
}


def _get(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "deadass-bbq-toolkit/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def search_usda(query, api_key, n=5):
    """Search FDC; return [{fdc_id, description, nutrients{per-100g}}]."""
    if not api_key or not query.strip():
        return []
    params = urllib.parse.urlencode({
        "api_key": api_key,
        "query": query.strip(),
        "pageSize": n,
        "dataType": "Foundation,SR Legacy",
    })
    data = _get("%s/foods/search?%s" % (BASE, params))
    out = []
    for food in data.get("foods", []):
        nutrients = {}
        for fn in food.get("foodNutrients", []):
            num = fn.get("nutrientNumber") or fn.get("nutrient", {}).get("number")
            try:
                num = int(float(num))
            except (TypeError, ValueError):
                continue
            key = NUTRIENT_MAP.get(num)
            if key and fn.get("unitName", "G").upper() == "G":
                try:
                    nutrients[key] = float(fn["value"])
                except (KeyError, TypeError, ValueError):
                    pass
        out.append({"fdc_id": str(food.get("fdcId", "")),
                    "description": food.get("description", ""),
                    "nutrients": nutrients})
    return out


def get_food(fdc_id, api_key):
    return _get("%s/food/%s?api_key=%s"
                % (BASE, urllib.parse.quote_plus(str(fdc_id)),
                   urllib.parse.quote_plus(api_key)))


def to_table_entry(food):
    """Convert one FDC food record to our nutrition_table.json schema."""
    nutrients = {}
    for fn in food.get("foodNutrients", []):
        num = fn.get("nutrientNumber") or fn.get("nutrient", {}).get("number")
        try:
            num = int(float(num))
        except (TypeError, ValueError):
            continue
        key = NUTRIENT_MAP.get(num)
        if key and fn.get("unitName", "G").upper() == "G":
            try:
                nutrients[key] = float(fn["value"])
            except (KeyError, TypeError, ValueError):
                pass
    entry = {k: round(nutrients.get(k, 0), 2) for k in
             ["calories", "fat_g", "sat_fat_g", "cholesterol_mg", "sodium_mg",
              "carbs_g", "fiber_g", "sugars_g", "protein_g", "potassium_mg"]}
    entry["source"] = "USDA FoodData Central (fdcId %s)" % food.get("fdcId", "?")
    return entry
