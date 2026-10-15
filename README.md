# Deadass BBQ Toolkit

Ingredient sourcing & recipe costing + nutrition/ingredient label maker for
**DeadassBBQ LLC**. Python stdlib only — no dependencies, no build step.

## Run it

```bash
python3 app.py            # → http://127.0.0.1:8000
python3 app.py --port 8080
python3 check_setup.py    # prerequisite check
```

Open the URL in a browser. Pages: **Dashboard · Ingredients · Recipes · Labels**,
plus a one-click **Demo**.

## Module 1 — Sourcing & Costing

- **Ingredients page:** add ingredients (category, FDA 9 allergens, nutrition
  table entry), then log supplier prices per ingredient
  (supplier, price, pack size). Unit cost per oz/lb is computed automatically
  and the **cheapest supplier is highlighted** per ingredient.
- Each ingredient has **"check price online"** links (WebstaurantStore + Amazon
  search for the ingredient name).
- **Recipes page:** build a recipe as `Ingredient Name, grams` per line.
  The costing page prices every line at that ingredient's **cheapest logged
  supplier**, totals the batch, and — given bottles per batch, bottle size,
  and a target retail price — computes **cost per bottle and margin %**.

**Honest note on sourcing:** this app finds the cheapest source *among the
supplier prices you log yourself*. It intentionally does not scrape the web:
grocery/wholesale sites block bots aggressively, and prices change constantly.
The search links let you comparison-shop by hand in seconds; log what you find
and the app does the math from there.

## Module 2 — Label Maker

Pick a recipe and a serving size (default **30g**, the FDA reference amount for
BBQ sauce; adjustable). You get:

- A print-friendly, classic black-and-white **Nutrition Facts** label with
  **% Daily Value on a 2,000-calorie diet**.
- The **ingredient list in descending order by weight** (FDA requirement).
- An **allergen statement** aggregated from your ingredient flags
  ("Contains: milk, soy…" — or "none of the 9 major allergens").
- Simplified FDA-style rounding (calories: nearest 5 under 50 / nearest 10
  above; macros: nearest 0.5g; sodium/potassium: nearest 5mg under 140mg /
  nearest 10mg above). The rounding is deliberately simplified — see disclaimer.

> **Disclaimer — read this:** all nutrition values are **planning ESTIMATES**
> computed from estimated per-100g data. **Verify with lab analysis or
> professional nutrition software (e.g. ESHA Genesis) before printing
> commercial labels.** This tool is for recipe development and cost planning,
> not for producing legally compliant retail packaging.

## Demo

One click on **Demo** (or visit `/demo`) loads the sample **"Smoky Original"**
recipe with pre-loaded ingredients and supplier prices, then shows a costing
report and a sample label — all from demo data, clearly bannered **DEMO**.
Your real data is never touched by the demo.

## Optional: USDA FoodData Central

The built-in table covers ~30 common sauce ingredients as estimates. For real
lab-grade reference data you can enable one-click USDA lookups:

1. Get a free API key at <https://api.nal.usda.gov> (free signup).
2. Paste it into `data/config.json` as `"usda_api_key"`.
3. On the ingredient edit page, **Search USDA**, pick a result, **Use this** —
   it saves a per-100g entry into your nutrition table (marked with its FDC id).

No key is ever hardcoded; leave the field empty to use the built-in table only.

## File layout

```
app.py                  web app (stdlib http.server) + all costing/label logic
usda.py                 optional FoodData Central lookup (needs API key)
check_setup.py          prerequisite checker
README.md
data/
  nutrition_table.json  ~30 per-100g estimates (source: "estimate")
  ingredients.json      your ingredients + supplier prices
  recipes.json          your recipes
  config.json           optional USDA API key
  demo/
    ingredients.json    demo supplier/ingredient data
    recipes.json        demo "Smoky Original" recipe
```

## Assumptions (documented, not hidden)

- Volume→weight conversions assume density ≈ 1 g/ml (fine for costing
  estimates on sauces, vinegars, and syrups).
- Servings per container = batch grams ÷ serving size, rounded to whole.
- Trans fat assumed 0g; micronutrients (vitamins, calcium, iron) omitted in
  planning mode; added sugars not tracked separately from total sugars.
- Nutrition math uses your ingredient records — garbage in, garbage out.
  Keep supplier prices current for costing you can trust.
