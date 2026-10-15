#!/usr/bin/env python3
"""deadass-bbq-toolkit — ingredient sourcing/costing + nutrition label maker.

Stdlib only. Run:  python3 app.py [--port 8000]
Then open http://127.0.0.1:8000 in a browser.
"""
import json
import math
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
DEMO_DATA = os.path.join(DATA, "demo")

FDA_ALLERGENS = ["milk", "eggs", "fish", "crustacean shellfish",
                 "tree nuts", "peanuts", "wheat", "soybeans", "sesame"]

# grams per unit. Volume units assume density ~1 g/ml (fine for costing
# estimates on sauces/vinegars; documented in README).
UNITS_TO_GRAMS = {
    "g": 1.0, "kg": 1000.0,
    "oz": 28.3495, "lb": 453.592,
    "fl oz": 29.5735, "ml": 1.0,
    "tsp": 4.92892, "tbsp": 14.7868, "cup": 236.588,
    "pt": 473.176, "qt": 946.353, "gal": 3785.41,
}
GRAMS_PER_OZ = 28.3495

# % Daily Value reference (2000-calorie diet, current FDA label)
DV = {"fat_g": 78, "sat_fat_g": 20, "cholesterol_mg": 300, "sodium_mg": 2300,
      "carbs_g": 275, "fiber_g": 28, "protein_g": 50, "potassium_mg": 4700}

NUTRIENT_KEYS = ["calories", "fat_g", "sat_fat_g", "cholesterol_mg", "sodium_mg",
                 "carbs_g", "fiber_g", "sugars_g", "protein_g", "potassium_mg"]


# ---------------- data layer ----------------
def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)


def nutrition_table():
    return load_json(os.path.join(DATA, "nutrition_table.json"), {})


def get_config():
    return load_json(os.path.join(DATA, "config.json"), {"usda_api_key": ""})


def get_ingredients(demo=False):
    d = load_json(os.path.join(DEMO_DATA if demo else DATA, "ingredients.json"),
                  {"ingredients": []})
    return d.get("ingredients", [])


def get_recipes(demo=False):
    d = load_json(os.path.join(DEMO_DATA if demo else DATA, "recipes.json"),
                  {"recipes": []})
    return d.get("recipes", [])


def save_ingredients(ingredients):
    save_json(os.path.join(DATA, "ingredients.json"), {"ingredients": ingredients})


def save_recipes(recipes):
    save_json(os.path.join(DATA, "recipes.json"), {"recipes": recipes})


def find_ingredient(name, ingredients):
    for ing in ingredients:
        if ing["name"].lower() == name.lower():
            return ing
    return None


def find_recipe(name, recipes):
    for r in recipes:
        if r["name"].lower() == name.lower():
            return r
    return None


# ---------------- costing ----------------
def to_grams(amount, unit):
    unit = (unit or "g").strip().lower()
    factor = UNITS_TO_GRAMS.get(unit)
    if factor is None:
        raise ValueError("Unknown unit: %r (use one of %s)"
                         % (unit, ", ".join(sorted(UNITS_TO_GRAMS))))
    return float(amount) * factor


def price_per_gram(supplier):
    grams = to_grams(supplier["size_amount"], supplier["size_unit"])
    if grams <= 0:
        raise ValueError("Supplier size must be positive")
    return float(supplier["price"]) / grams


def cheapest_supplier(ingredient):
    """Return (supplier_dict, price_per_gram) for the cheapest supplier, or (None, None)."""
    best, best_ppg = None, None
    for s in ingredient.get("suppliers", []):
        try:
            ppg = price_per_gram(s)
        except (ValueError, KeyError, TypeError, ZeroDivisionError):
            continue
        if best_ppg is None or ppg < best_ppg:
            best, best_ppg = s, ppg
    return best, best_ppg


def recipe_cost(recipe, ingredients):
    """Cost a recipe using each ingredient's cheapest supplier.
    Returns (lines, total) where lines = [{name, grams, supplier, price_per_oz, cost}]."""
    lines, total = [], 0.0
    for line in recipe.get("lines", []):
        ing = find_ingredient(line["ingredient"], ingredients)
        grams = float(line["grams"])
        if ing is None:
            lines.append({"name": line["ingredient"], "grams": grams,
                          "supplier": None, "price_per_oz": None,
                          "cost": None, "missing": True})
            continue
        sup, ppg = cheapest_supplier(ing)
        cost = grams * ppg if ppg is not None else None
        lines.append({"name": ing["name"], "grams": grams,
                      "supplier": sup["supplier"] if sup else None,
                      "price_per_oz": (ppg * GRAMS_PER_OZ) if ppg is not None else None,
                      "cost": cost, "missing": False})
        if cost is not None:
            total += cost
    return lines, total


def total_grams(recipe):
    return sum(float(l["grams"]) for l in recipe.get("lines", []))


# ---------------- nutrition ----------------
def ingredient_nutrition(ingredient, table):
    """Per-100g nutrition dict for an ingredient (zeros if unknown)."""
    entry = table.get(ingredient.get("nutrition_key", ""), {})
    if not entry:
        # try matching by normalized name
        key = ingredient["name"].lower().replace(" ", "_")
        entry = table.get(key, {})
    out = {}
    for k in NUTRIENT_KEYS:
        out[k] = float(entry.get(k, 0) or 0)
    return out


def recipe_nutrition_totals(recipe, ingredients, table):
    """Total nutrition for the whole batch (unrounded)."""
    totals = {k: 0.0 for k in NUTRIENT_KEYS}
    for line in recipe.get("lines", []):
        ing = find_ingredient(line["ingredient"], ingredients)
        if ing is None:
            continue
        nut = ingredient_nutrition(ing, table)
        factor = float(line["grams"]) / 100.0
        for k in NUTRIENT_KEYS:
            totals[k] += nut[k] * factor
    return totals


def round_label_value(key, value):
    """Simplified FDA-style rounding for a per-serving value. Documented as simplified."""
    v = float(value)
    if key == "calories":
        if v < 5:
            return 0
        if v <= 50:
            return int(5 * round(v / 5))
        return int(10 * round(v / 10))
    if key in ("sodium_mg", "potassium_mg", "cholesterol_mg"):
        if v < 5:
            return 0
        if v <= 140:
            return int(5 * round(v / 5))
        return int(10 * round(v / 10))
    # macros: nearest 0.5 g
    if v < 0.5:
        return 0
    return round(v * 2) / 2


def label_nutrition(recipe, ingredients, table, serving_g):
    """Per-serving nutrition, rounded. Returns (rounded dict, servings, total_g)."""
    tg = total_grams(recipe)
    if tg <= 0:
        return {}, 0, 0
    servings = max(1, int(round(tg / serving_g)))
    totals = recipe_nutrition_totals(recipe, ingredients, table)
    rounded = {}
    for k in NUTRIENT_KEYS:
        per_serving = totals[k] / servings
        rounded[k] = round_label_value(k, per_serving)
    return rounded, servings, tg


def dv_percent(key, rounded_value):
    ref = DV.get(key)
    if not ref:
        return None
    return int(round(100 * rounded_value / ref))


def ingredient_list_desc(recipe):
    """Ingredient names in descending order by weight (FDA requirement)."""
    lines = sorted(recipe.get("lines", []),
                   key=lambda l: float(l["grams"]), reverse=True)
    return [l["ingredient"] for l in lines]


def allergen_statement(recipe, ingredients):
    found = []
    for line in recipe.get("lines", []):
        ing = find_ingredient(line["ingredient"], ingredients)
        if ing is None:
            continue
        for a in ing.get("allergens", []):
            if a not in found:
                found.append(a)
    if not found:
        return "Contains: none of the 9 major allergens."
    return "Contains: " + ", ".join(found) + "."


def fmt_money(v):
    return "$%.2f" % v if v is not None else "—"

# ---------------- html ----------------
CSS = """
body{font-family:-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;
margin:0;background:#f5f1ea;color:#2b2118;line-height:1.5}
.wrap{max-width:960px;margin:0 auto;padding:24px 16px 64px}
nav{background:#3a2317;color:#fff;padding:10px 16px;display:flex;gap:18px;align-items:center;flex-wrap:wrap}
nav a{color:#ffd9a8;text-decoration:none;font-weight:600}
nav a:hover{text-decoration:underline}
nav .brand{font-size:1.15rem;font-weight:800;color:#fff;margin-right:8px}
.card{background:#fff;border:1px solid #e0d5c3;border-radius:10px;padding:18px;margin:14px 0}
.btn{display:inline-block;background:#b3401f;color:#fff;border:none;border-radius:8px;
padding:9px 16px;font-weight:700;cursor:pointer;text-decoration:none;font-size:.95rem}
.btn:hover{background:#93331a}
.btn.ghost{background:#fff;color:#b3401f;border:2px solid #b3401f}
table{border-collapse:collapse;width:100%;margin:10px 0}
th,td{border:1px solid #ddd0ba;padding:8px 10px;text-align:left;font-size:.92rem}
th{background:#3a2317;color:#fff}
tr.cheapest td{background:#e8f7e4;font-weight:700}
input,select,textarea{font-size:.95rem;padding:8px;border:1px solid #c9bda6;border-radius:6px;width:100%;box-sizing:border-box}
label{font-weight:600;display:block;margin:10px 0 4px}
.row{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.demo-banner{background:#fff3c4;border:2px dashed #b98a00;border-radius:10px;
padding:14px 18px;margin:14px 0;font-weight:700}
.warn{background:#fff3c4;border-left:5px solid #b98a00;padding:12px 16px;margin:14px 0}
.small{font-size:.82rem;color:#6b5d4d}
h1{margin:.4em 0}.num{text-align:right;font-variant-numeric:tabular-nums}
@media print{.noprint{display:none}body{background:#fff}.wrap{max-width:100%;padding:0}}
"""

# Classic black/white FDA Nutrition Facts styling
LABEL_CSS = """
.nf{font-family:Helvetica,Arial,sans-serif;color:#000;background:#fff;
width:300px;border:2px solid #000;padding:8px 10px;margin:10px 0}
.nf h2{font-size:2rem;margin:0;font-weight:900;letter-spacing:-1px}
.nf .rule{background:#000;height:12px;margin:6px 0}
.nf .rule.med{height:6px}
.nf .rule.thin{height:1px;background:#000;margin:2px 0}
.nf table{width:100%;border-collapse:collapse;font-size:.82rem;margin:0}
.nf td{border:none;padding:2px 0;vertical-align:top}
.nf .r{text-align:right}
.nf .b{font-weight:700}
.nf .ind1{padding-left:12px}.nf .ind2{padding-left:24px}
.nf .cal{font-size:1.9rem;font-weight:900}
.nf .foot{font-size:.68rem;margin-top:6px}
.nf .dvhead{text-align:right;font-weight:700;font-size:.78rem}
"""


def layout(title, body, demo=False):
    banner = ('<div class="demo-banner">DEMO — sample data. '
              'Add your own ingredients and recipes to use this for real.</div>'
              if demo else '')
    return ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>%s · Deadass BBQ Toolkit</title><style>%s</style></head>"
            "<body><nav><span class='brand'>Deadass BBQ Toolkit</span>"
            "<a href='/'>Dashboard</a><a href='/ingredients'>Ingredients</a>"
            "<a href='/recipes'>Recipes</a><a href='/labels'>Labels</a>"
            "<a href='/demo'>Demo</a></nav>"
            "<div class='wrap'>%s<h1>%s</h1>%s</div></body></html>"
            % (esc(title), CSS, banner, esc(title), body))


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def fmt_num(v, decimals=1):
    if v is None:
        return "—"
    if decimals == 0:
        return "%d" % int(round(v))
    return ("%%.%df" % decimals) % v


def render_label(recipe_name, rounded, servings, serving_g, total_g,
                 ingredients_desc, allergens):
    """FDA-style Nutrition Facts label HTML (planning estimate)."""
    def row(label, key, unit, indent=0, bold=False, dv=True):
        val = rounded.get(key, 0)
        cls = "b" if bold else ""
        ind = "ind1" if indent == 1 else ("ind2" if indent == 2 else "")
        cells = ("<td class='%s %s'>%s %s%s</td>"
                 % (cls, ind, esc(label), fmt_num(val, 0 if key in
                    ("sodium_mg", "potassium_mg", "cholesterol_mg", "calories") else 1), unit))
        if dv and key in DV:
            cells += "<td class='r b'>%d%%</td>" % dv_percent(key, val)
        else:
            cells += "<td class='r'></td>"
        return "<tr>%s</tr>" % cells

    pct = lambda k: dv_percent(k, rounded.get(k, 0))
    html = ["<div class='nf'>",
            "<h2>Nutrition Facts</h2>",
            "<div>%d servings per container</div>" % servings,
            "<div>Serving size %sg</div>" % fmt_num(serving_g, 0),
            "<div class='rule'></div>",
            "<div>Amount per serving</div>",
            "<table><tr><td class='b'>Calories</td>"
            "<td class='r cal'>%s</td></tr></table>"
            % fmt_num(rounded.get("calories", 0), 0),
            "<div class='rule med'></div>",
            "<div class='dvhead'>% Daily Value*</div>",
            "<table>",
            row("Total Fat", "fat_g", "g", bold=True),
            row("Saturated Fat", "sat_fat_g", "g", indent=1),
            row("Trans Fat", "trans_fat_g", "g", indent=1, dv=False),
            row("Cholesterol", "cholesterol_mg", "mg", bold=True),
            row("Sodium", "sodium_mg", "mg", bold=True),
            row("Total Carbohydrate", "carbs_g", "g", bold=True),
            row("Dietary Fiber", "fiber_g", "g", indent=1),
            row("Total Sugars", "sugars_g", "g", indent=1, dv=False),
            row("Protein", "protein_g", "g", bold=True),
            row("Potassium", "potassium_mg", "mg", indent=0),
            "</table><div class='rule thin'></div>",
            "<div class='foot'>* The % Daily Value (DV) tells you how much a nutrient in "
            "a serving of food contributes to a daily diet. 2,000 calories a day is used "
            "for general nutrition advice.</div>",
            "</div>",
            "<div class='card'><b>INGREDIENTS:</b> %s.<br><br><b>%s</b></div>"
            % (esc(", ".join(ingredients_desc)), esc(allergens)),
            "<div class='warn'><b>Planning estimate only.</b> Values are computed from "
            "estimated nutrition data with simplified rounding — verify with lab analysis "
            "or professional nutrition software (e.g. ESHA Genesis) before printing "
            "commercial labels.</div>"]
    return "\n".join(html)


def price_check_links(name):
    q = urllib.parse.quote_plus(name)
    return ("<a href='https://www.webstaurantstore.com/search/%s.html' target='_blank' rel='noopener'>WebstaurantStore</a>"
            " · <a href='https://www.amazon.com/s?k=%s' target='_blank' rel='noopener'>Amazon</a>"
            % (q, q))

# ---------------- pages ----------------
def page_dashboard(demo=False):
    ings = get_ingredients(demo)
    recs = get_recipes(demo)
    body = """
<div class='card'><h2>Module 1 — Sourcing &amp; Costing</h2>
<p>Log your ingredients and supplier prices. The app finds the <b>cheapest source
per ingredient</b> among the prices <i>you</i> log, then costs recipes and bottles
from those prices.</p>
<p class='small'>Honest note: this compares your own logged supplier prices — it does
not scrape the web. Sites block bots and prices change constantly; the
&#8220;check price online&#8221; links on each ingredient take you to the search
page so you can compare by hand in seconds.</p>
<p><a class='btn' href='/ingredients'>Manage ingredients (%d)</a>
<a class='btn ghost' href='/recipes'>Recipes (%d)</a></p></div>
<div class='card'><h2>Module 2 — Label Maker</h2>
<p>Pick a recipe, set a serving size, and generate a print-friendly
<b>Nutrition Facts</b> label with the ingredient list in descending weight order
and an allergen statement.</p>
<p class='warn'><b>Planning estimates only</b> — verify with lab analysis or
professional nutrition software (e.g. ESHA Genesis) before printing commercial labels.</p>
<p><a class='btn' href='/labels'>Make a label</a>
<a class='btn ghost' href='/demo'>Try the one-click demo</a></p></div>
""" % (len(ings), len(recs))
    return layout("Dashboard", body, demo)


def page_ingredients(demo=False):
    ings = get_ingredients(demo)
    rows = []
    for ing in sorted(ings, key=lambda i: i["name"].lower()):
        sup, ppg = cheapest_supplier(ing)
        sup_rows = []
        for s in ing.get("suppliers", []):
            try:
                p = price_per_gram(s)
                per_oz = "$%.3f/oz" % (p * GRAMS_PER_OZ)
            except (ValueError, KeyError, TypeError):
                per_oz = "bad entry"
            star = " ★ cheapest" if s is sup else ""
            sup_rows.append("<tr%s><td>%s</td><td class='num'>%s</td>"
                           "<td class='num'>%s %s</td><td class='num'>%s%s</td></tr>"
                           % (" class='cheapest'" if s is sup else "",
                              esc(s.get("supplier", "")), fmt_money(s.get("price")),
                              esc(str(s.get("size_amount", ""))),
                              esc(s.get("size_unit", "")), per_oz, star))
        if not sup_rows:
            sup_rows = ["<tr><td colspan='4' class='small'>No supplier prices logged yet.</td></tr>"]
        rows.append(
            "<div class='card'><h2>%s</h2>"
            "<p class='small'>Category: %s · Allergens: %s · Nutrition: %s<br>%s</p>"
            "<table><tr><th>Supplier</th><th>Price</th><th>Size</th><th>Unit cost</th></tr>%s</table>"
            "<p><a class='btn ghost' href='/ingredient/edit?name=%s'>Edit / add prices</a></p>"
            "</div>"
            % (esc(ing["name"]), esc(ing.get("category", "")),
               esc(", ".join(ing.get("allergens", [])) or "none"),
               esc(ing.get("nutrition_key", "custom")),
               price_check_links(ing["name"]), "".join(sup_rows),
               urllib.parse.quote_plus(ing["name"])))
    body = ("<p><a class='btn' href='/ingredient/new'>+ Add ingredient</a></p>"
            + ("".join(rows) if rows else
               "<div class='card'>No ingredients yet. Add your first one above, "
               "or <a href='/demo'>try the demo</a>.</div>"))
    return layout("Ingredients", body, demo)


def page_ingredient_form(ing=None, demo=False, usda_results=None, usda_query=""):
    ing = ing or {"name": "", "category": "", "allergens": [],
                  "nutrition_key": "", "suppliers": []}
    table = nutrition_table()
    opts = "".join(
        "<option value='%s'%s>%s</option>"
        % (k, " selected" if k == ing.get("nutrition_key") else "",
           k.replace("_", " ").title())
        for k in sorted(table) if not k.startswith("_"))
    allergen_boxes = "".join(
        "<label style='display:inline-block;margin:4px 12px 4px 0;font-weight:normal'>"
        "<input type='checkbox' name='allergens' value='%s'%s style='width:auto'> %s</label>"
        % (a, " checked" if a in ing.get("allergens", []) else "", a)
        for a in FDA_ALLERGENS)
    sup_rows = "".join(
        "<tr><td>%s</td><td class='num'>%s</td><td class='num'>%s %s</td></tr>"
        % (esc(s.get("supplier", "")), fmt_money(s.get("price")),
           esc(str(s.get("size_amount", ""))), esc(s.get("size_unit", "")))
        for s in ing.get("suppliers", []))
    usda_html = ""
    cfg = get_config()
    if cfg.get("usda_api_key"):
        res_html = ""
        if usda_results:
            items = "".join(
                "<li><b>%s</b> — %s kcal/100g "
                "<a href='/ingredient/usda_use?key=%s&name=%s'>Use this</a></li>"
                % (esc(r["description"]), fmt_num(r["nutrients"].get("calories", 0), 0),
                   urllib.parse.quote_plus(r["fdc_id"]),
                   urllib.parse.quote_plus(ing.get("name") or usda_query))
                for r in usda_results)
            res_html = "<ul>%s</ul>" % items
        usda_html = ("<div class='card'><h3>USDA FoodData Central lookup</h3>"
                     "<form method='get' action='/ingredient/usda'>"
                     "<input type='hidden' name='name' value='%s'>"
                     "<input name='q' value='%s' placeholder='Search USDA…'>"
                     "<p><button class='btn ghost' type='submit'>Search USDA</button></p>"
                     "</form>%s</div>"
                     % (esc(ing.get("name", "")), esc(usda_query), res_html))
    elif not demo:
        usda_html = ("<p class='small'>Tip: add a free USDA FoodData Central API key "
                     "to data/config.json to enable one-click nutrition lookups "
                     "(get one at api.nal.usda.gov).</p>")
    unit_opts = "".join("<option>%s</option>" % u for u in
                        ["g", "kg", "oz", "lb", "fl oz", "ml",
                         "tsp", "tbsp", "cup", "pt", "qt", "gal"])
    body = """
<form method='post' action='/ingredient/save'>
<label>Name<input name='name' value='%s' required></label>
<label>Category<input name='category' value='%s' placeholder='base, spice, sweetener…'></label>
<label>Nutrition table entry<select name='nutrition_key'>%s</select></label>
<label>Allergens (FDA 9)</label>%s
<input type='hidden' name='orig_name' value='%s'>
<p><button class='btn' type='submit'>Save ingredient</button></p>
</form>
%s
<div class='card'><h3>Supplier prices</h3>
<table><tr><th>Supplier</th><th>Price</th><th>Size</th></tr>%s</table>
<form method='post' action='/ingredient/supplier/add'>
<input type='hidden' name='name' value='%s'>
<div class='row'>
<div><label>Supplier<input name='supplier' required placeholder='Restaurant Depot'></label></div>
<div><label>Price ($)<input name='price' type='number' step='0.01' min='0' required></label></div>
</div><div class='row'>
<div><label>Size amount<input name='size_amount' type='number' step='any' min='0' required></label></div>
<div><label>Size unit<select name='size_unit'>%s</select></label></div>
</div><p><button class='btn' type='submit'>Add price</button></p>
</form></div>
""" % (esc(ing["name"]), esc(ing.get("category", "")), opts, allergen_boxes,
       esc(ing["name"]), usda_html,
       sup_rows or "<tr><td colspan='3' class='small'>None yet.</td></tr>",
       esc(ing["name"]), unit_opts)
    return layout("Edit ingredient" if ing["name"] else "Add ingredient", body, demo)


def page_recipes(demo=False):
    recs = get_recipes(demo)
    cards = "".join(
        "<div class='card'><h2>%s</h2><p class='small'>%d ingredients · %sg total</p>"
        "<p><a class='btn' href='/recipe/cost?name=%s'>Cost it</a> "
        "<a class='btn ghost' href='/label?name=%s'>Make label</a> "
        "<a class='btn ghost' href='/recipe/edit?name=%s'>Edit</a></p></div>"
        % (esc(r["name"]), len(r.get("lines", [])), fmt_num(total_grams(r), 0),
           urllib.parse.quote_plus(r["name"]), urllib.parse.quote_plus(r["name"]),
           urllib.parse.quote_plus(r["name"]))
        for r in recs)
    body = ("<p><a class='btn' href='/recipe/new'>+ New recipe</a></p>"
            + (cards if cards else
               "<div class='card'>No recipes yet. <a href='/demo'>Try the demo recipe</a>.</div>"))
    return layout("Recipes", body, demo)


def page_recipe_form(recipe=None, demo=False):
    recipe = recipe or {"name": "", "lines": []}
    ings = get_ingredients(demo)
    datalist = "".join("<option value='%s'>" % esc(i["name"]) for i in ings)
    lines_txt = "\n".join("%s, %s" % (l["ingredient"], l["grams"])
                          for l in recipe.get("lines", []))
    body = """
<form method='post' action='/recipe/save'>
<label>Recipe name<input name='name' value='%s' required></label>
<label>Ingredients — one per line as <i>Name, grams</i></label>
<datalist id='ings'>%s</datalist>
<textarea name='lines' rows='12' placeholder='Tomato Paste, 400&#10;Brown Sugar, 150'>%s</textarea>
<p class='small'>Tip: type an ingredient name and pick from the list. Unknown names are
flagged on the costing page (they need an ingredient record first).</p>
<input type='hidden' name='orig_name' value='%s'>
<p><button class='btn' type='submit'>Save recipe</button></p>
</form>
""" % (esc(recipe["name"]), datalist, esc(lines_txt), esc(recipe["name"]))
    return layout("Edit recipe" if recipe["name"] else "New recipe", body, demo)


def page_recipe_cost(name, demo=False, bottles="", bottle_oz="", retail=""):
    ings = get_ingredients(demo)
    recs = get_recipes(demo)
    recipe = find_recipe(name, recs)
    if recipe is None:
        return layout("Not found", "<div class='card'>Recipe not found.</div>", demo)
    lines, total = recipe_cost(recipe, ings)
    rows = []
    missing = False
    for l in lines:
        if l.get("missing"):
            missing = True
            rows.append("<tr><td>%s</td><td class='num'>%sg</td>"
                        "<td colspan='3' class='small'>No ingredient record — "
                        "<a href='/ingredient/new'>add it</a>.</td></tr>"
                        % (esc(l["name"]), fmt_num(l["grams"], 1)))
        else:
            rows.append("<tr><td>%s</td><td class='num'>%sg</td><td>%s</td>"
                        "<td class='num'>%s</td><td class='num'>%s</td></tr>"
                        % (esc(l["name"]), fmt_num(l["grams"], 1),
                           esc(l["supplier"] or "no prices logged"),
                           ("$%.3f/oz" % l["price_per_oz"]) if l["price_per_oz"] else "—",
                           fmt_money(l["cost"])))
    warn = ("<div class='warn'>Some lines couldn't be costed (missing ingredient or "
            "supplier prices). Add them on the Ingredients page.</div>" if missing else "")
    yield_html = ""
    try:
        nb, bo = float(bottles), float(bottle_oz)
        if nb > 0 and bo > 0:
            per_bottle = total / nb
            yield_html = ("<div class='card'><h3>Yield</h3>"
                          "<p>Batch cost <b>%s</b> ÷ %s bottles (%s oz each) = "
                          "<b>%s per bottle</b></p>"
                          % (fmt_money(total), fmt_num(nb, 0), fmt_num(bo, 1),
                             fmt_money(per_bottle)))
            if retail:
                r = float(retail)
                margin = (r - per_bottle) / r * 100 if r > 0 else 0
                yield_html += ("<p>At a $%s retail price: <b>%.1f%% margin</b> "
                               "(%.2f profit per bottle).</p>"
                               % (fmt_num(r, 2), margin, r - per_bottle))
            yield_html += "</div>"
    except (ValueError, TypeError):
        pass
    body = """
<table><tr><th>Ingredient</th><th>Amount</th><th>Cheapest supplier</th>
<th>Unit cost</th><th>Line cost</th></tr>%s</table>
<div class='card'><h3>Batch total: %s</h3>
<p class='small'>Costed from each ingredient's cheapest logged supplier.</p></div>
%s
<div class='card'><h3>Bottle costing &amp; margin</h3>
<form method='get' action='/recipe/cost'>
<input type='hidden' name='name' value='%s'>
<div class='row'>
<div><label>Bottles per batch<input name='bottles' value='%s' placeholder='24'></label></div>
<div><label>Bottle size (oz)<input name='bottle_oz' value='%s' placeholder='12'></label></div>
</div>
<label>Target retail price ($)<input name='retail' value='%s' placeholder='8.99'></label>
<p><button class='btn' type='submit'>Calculate</button></p>
</form></div>
%s
""" % ("".join(rows), fmt_money(total), warn, esc(name),
       esc(bottles), esc(bottle_oz), esc(retail), yield_html)
    return layout("Costing — " + name, body, demo)

# ---------------- labels ----------------
def page_labels(demo=False):
    recs = get_recipes(demo)
    opts = "".join("<option>%s</option>" % esc(r["name"]) for r in recs)
    body = """
<div class='card'><form method='get' action='/label'>
<label>Recipe<select name='name'>%s</select></label>
<label>Serving size (grams) — FDA reference for BBQ sauce is 30g
<input name='serving_g' value='30' type='number' step='any' min='1'></label>
<p><button class='btn' type='submit'>Generate label</button></p>
</form></div>
<div class='warn'><b>Planning estimates only.</b> Simplified rounding is applied;
verify with lab analysis or professional nutrition software before printing
commercial labels.</div>
""" % opts
    return layout("Label maker", body, demo)


def page_label(name, serving_g, demo=False):
    ings = get_ingredients(demo)
    recs = get_recipes(demo)
    table = nutrition_table()
    recipe = find_recipe(name, recs)
    if recipe is None:
        return layout("Not found", "<div class='card'>Recipe not found.</div>", demo)
    try:
        sg = float(serving_g)
        if sg <= 0:
            raise ValueError
    except (ValueError, TypeError):
        sg = 30.0
    rounded, servings, tg = label_nutrition(recipe, ings, table, sg)
    label_html = ("<style>%s</style>" % LABEL_CSS) + render_label(
        recipe["name"], rounded, servings, sg, tg,
        ingredient_list_desc(recipe), allergen_statement(recipe, ings))
    body = (label_html +
            "<p class='noprint'><button class='btn' onclick='window.print()'>"
            "Print label</button> "
            "<a class='btn ghost' href='/labels'>Back</a></p>")
    return layout("Label — " + name, body, demo)


def page_demo():
    ings = get_ingredients(demo=True)
    recs = get_recipes(demo=True)
    recipe = recs[0] if recs else None
    if recipe is None:
        return layout("Demo", "<div class='card'>Demo data missing.</div>", True)
    lines, total = recipe_cost(recipe, ings)
    table = nutrition_table()
    rounded, servings, tg = label_nutrition(recipe, ings, table, 30.0)
    rows = "".join(
        "<tr><td>%s</td><td class='num'>%sg</td><td>%s</td><td class='num'>%s</td></tr>"
        % (esc(l["name"]), fmt_num(l["grams"], 1),
           esc(l["supplier"] or "—"), fmt_money(l["cost"]))
        for l in lines if not l.get("missing"))
    body = """
<div class='card'><h2>Demo recipe: %s</h2>
<p class='small'>%d ingredients · %sg batch · costed from cheapest logged supplier prices.</p>
<table><tr><th>Ingredient</th><th>Amount</th><th>Supplier</th><th>Line cost</th></tr>%s</table>
<h3>Batch total: %s</h3>
<p>At 24 × 12&nbsp;oz bottles: <b>%s per bottle</b></p></div>
<h2>Sample label</h2>
<style>%s</style>
%s
<p><a class='btn ghost' href='/'>Back to dashboard</a></p>
""" % (esc(recipe["name"]), len(lines), fmt_num(tg, 0), rows, fmt_money(total),
       fmt_money(total / 24 if total else 0), LABEL_CSS,
       render_label(recipe["name"], rounded, servings, 30.0, tg,
                    ingredient_list_desc(recipe), allergen_statement(recipe, ings)))
    return layout("One-click demo", body, True)


# ---------------- http handler ----------------
class Handler(BaseHTTPRequestHandler):
    server_version = "DeadassBBQToolkit/1.0"

    def log_message(self, *a):
        pass

    def _send(self, html, code=200):
        data = html.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, path):
        self.send_response(303)
        self.send_header("Location", path)
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(parsed.query)
        get = lambda k, d="": q.get(k, [d])[0]
        path = parsed.path

        if path == "/":
            self._send(page_dashboard())
        elif path == "/ingredients":
            self._send(page_ingredients())
        elif path == "/ingredient/new":
            self._send(page_ingredient_form())
        elif path == "/ingredient/edit":
            ing = find_ingredient(get("name"), get_ingredients())
            self._send(page_ingredient_form(ing or {"name": get("name"),
                             "category": "", "allergens": [],
                             "nutrition_key": "", "suppliers": []}))
        elif path == "/ingredient/usda":
            self._send(self._usda_search(get("name"), get("q")))
        elif path == "/ingredient/usda_use":
            self._redirect(self._usda_use(get("key"), get("name")))
        elif path == "/recipes":
            self._send(page_recipes())
        elif path == "/recipe/new":
            self._send(page_recipe_form())
        elif path == "/recipe/edit":
            r = find_recipe(get("name"), get_recipes())
            self._send(page_recipe_form(r))
        elif path == "/recipe/cost":
            self._send(page_recipe_cost(get("name"), bottles=get("bottles"),
                                        bottle_oz=get("bottle_oz"),
                                        retail=get("retail")))
        elif path == "/labels":
            self._send(page_labels())
        elif path == "/label":
            self._send(page_label(get("name"), get("serving_g", "30")))
        elif path == "/demo":
            self._send(page_demo())
        else:
            self._send(layout("Not found",
                              "<div class='card'>Unknown page.</div>"), code=404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0))
        form = urllib.parse.parse_qs(
            self.rfile.read(length).decode("utf-8", "replace"))
        get = lambda k, d="": form.get(k, [d])[0]
        getlist = lambda k: form.get(k, [])
        path = parsed.path

        if path == "/ingredient/save":
            ings = get_ingredients()
            data = {"name": get("name").strip(),
                    "category": get("category").strip(),
                    "allergens": [a for a in getlist("allergens")
                                  if a in FDA_ALLERGENS],
                    "nutrition_key": get("nutrition_key"),
                    "suppliers": []}
            orig = get("orig_name").strip()
            old = find_ingredient(orig, ings) if orig else None
            if old is not None:
                data["suppliers"] = old.get("suppliers", [])
                ings.remove(old)
            ings.append(data)
            save_ingredients(ings)
            self._redirect("/ingredient/edit?name=" +
                           urllib.parse.quote_plus(data["name"]))
        elif path == "/ingredient/supplier/add":
            ings = get_ingredients()
            ing = find_ingredient(get("name"), ings)
            if ing is not None:
                try:
                    ing.setdefault("suppliers", []).append({
                        "supplier": get("supplier").strip(),
                        "price": float(get("price")),
                        "size_amount": float(get("size_amount")),
                        "size_unit": get("size_unit")})
                    save_ingredients(ings)
                except ValueError:
                    pass
            self._redirect("/ingredient/edit?name=" +
                           urllib.parse.quote_plus(get("name")))
        elif path == "/recipe/save":
            recs = get_recipes()
            lines = []
            for raw in get("lines").splitlines():
                raw = raw.strip()
                if not raw or "," not in raw:
                    continue
                name, grams = raw.rsplit(",", 1)
                try:
                    lines.append({"ingredient": name.strip(),
                                  "grams": float(grams.strip())})
                except ValueError:
                    continue
            data = {"name": get("name").strip(), "lines": lines}
            orig = get("orig_name").strip()
            old = find_recipe(orig, recs) if orig else None
            if old is not None:
                recs.remove(old)
            recs.append(data)
            save_recipes(recs)
            self._redirect("/recipe/cost?name=" +
                           urllib.parse.quote_plus(data["name"]))
        else:
            self._redirect("/")

    # -- USDA (optional, needs api key in data/config.json) --
    def _usda_search(self, ing_name, query):
        from usda import search_usda
        cfg = get_config()
        key = cfg.get("usda_api_key", "")
        results = search_usda(query, key, n=5) if key and query else []
        ing = find_ingredient(ing_name, get_ingredients()) or {
            "name": ing_name, "category": "", "allergens": [],
            "nutrition_key": "", "suppliers": []}
        return page_ingredient_form(ing, usda_results=results,
                                    usda_query=query)

    def _usda_use(self, fdc_id, ing_name):
        from usda import get_food, to_table_entry
        cfg = get_config()
        key = cfg.get("usda_api_key", "")
        try:
            food = get_food(fdc_id, key)
            entry = to_table_entry(food)
        except Exception:
            return "/ingredient/edit?name=" + urllib.parse.quote_plus(ing_name)
        table = nutrition_table()
        table["custom_" + "".join(
            c if c.isalnum() else "_" for c in ing_name.lower())] = entry
        save_json(os.path.join(DATA, "nutrition_table.json"), table)
        return "/ingredient/edit?name=" + urllib.parse.quote_plus(ing_name)


def main():
    port = 8000
    args = sys.argv[1:]
    if "--port" in args:
        try:
            port = int(args[args.index("--port") + 1])
        except (IndexError, ValueError):
            pass
    print("Deadass BBQ Toolkit  →  http://127.0.0.1:%d  (Ctrl+C to stop)" % port)
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
