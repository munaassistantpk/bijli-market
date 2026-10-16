"""BijliMarket - Pakistan ka electrical marketplace (PSM-style, electrical ke liye)."""
import os
import sqlite3
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, g, session

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "bijlimarket-dev-key")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "bijli123")
DB_PATH = os.path.join(os.path.dirname(__file__), "bijlimarket.db")

CATEGORIES = [
    "LED Bulbs",
    "LED Downlights",
    "LED Panels",
    "Battens & Tubes",
    "Flood Lights",
    "Street Lights",
    "Highbay Lights",
    "Garden & Outdoor",
    "LED Strips & Profiles",
    "LED Drivers",
    "Wires & Cables",
    "Switches & Sockets",
    "Breakers & Panels",
    "Fans",
    "Other Electrical",
]


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.execute(
        """CREATE TABLE IF NOT EXISTS listings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL, category TEXT NOT NULL,
            price INTEGER NOT NULL, detail TEXT DEFAULT '',
            seller TEXT NOT NULL, phone TEXT NOT NULL,
            city TEXT DEFAULT '', created TEXT NOT NULL)"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS requirements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product TEXT NOT NULL, qty TEXT DEFAULT '',
            city TEXT DEFAULT '', name TEXT NOT NULL,
            phone TEXT NOT NULL, note TEXT DEFAULT '',
            created TEXT NOT NULL)"""
    )
    db.commit()
    # Seed from bundled JSON on first run (Render free tier has ephemeral disk)
    if db.execute("SELECT COUNT(*) FROM listings").fetchone()[0] == 0:
        seed_file = os.path.join(os.path.dirname(__file__), "seed_data.json")
        if os.path.exists(seed_file):
            import json
            rows = json.load(open(seed_file, encoding="utf-8"))
            db.executemany(
                "INSERT INTO listings (title, category, price, detail, seller, phone, city, created)"
                " VALUES (?,?,?,?,?,?,?,?)",
                [(r["title"], r["category"], r["price"], r.get("detail", ""),
                  r["seller"], r["phone"], r.get("city", ""), r["created"]) for r in rows],
            )
            db.commit()
    db.close()


# Ensure DB exists + seeded at startup (works under gunicorn too)
init_db()


def wa_link(phone, text):
    digits = "".join(c for c in phone if c.isdigit())
    if digits.startswith("0"):
        digits = "92" + digits[1:]
    from urllib.parse import quote
    return f"https://wa.me/{digits}?text={quote(text)}"


@app.route("/")
def index():
    db = get_db()
    q = request.args.get("q", "").strip()
    cat = request.args.get("cat", "").strip()
    sql = "SELECT * FROM listings"
    clauses, params = [], []
    if cat:
        clauses.append("category = ?")
        params.append(cat)
    if q:
        clauses.append("(title LIKE ? OR detail LIKE ?)")
        params += [f"%{q}%", f"%{q}%"]
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY id DESC"
    listings = db.execute(sql, params).fetchall()
    counts = db.execute(
        "SELECT category, COUNT(*) c FROM listings GROUP BY category"
    ).fetchall()
    return render_template(
        "index.html", listings=listings, categories=CATEGORIES,
        counts={r["category"]: r["c"] for r in counts},
        q=q, cat=cat, wa_link=wa_link,
    )


@app.route("/listing/<int:lid>")
def listing_detail(lid):
    db = get_db()
    item = db.execute("SELECT * FROM listings WHERE id=?", (lid,)).fetchone()
    if not item:
        return redirect(url_for("index"))
    related = db.execute(
        "SELECT * FROM listings WHERE category=? AND id!=? ORDER BY id DESC LIMIT 6",
        (item["category"], lid),
    ).fetchall()
    msg = f"Assalam-o-Alaikum! BijliMarket par '{item['title']}' (Rs {item['price']:,}) dekha. Kya available hai?"
    return render_template(
        "listing.html", item=item, related=related, wa=wa_link(item["phone"], msg),
        wa_link=wa_link,
    )


@app.route("/requirements", methods=["GET", "POST"])
def requirements():
    db = get_db()
    if request.method == "POST":
        product = request.form.get("product", "").strip()
        name = request.form.get("name", "").strip()
        phone = request.form.get("phone", "").strip()
        if product and name and phone:
            db.execute(
                "INSERT INTO requirements (product, qty, city, name, phone, note, created)"
                " VALUES (?,?,?,?,?,?,?)",
                (product, request.form.get("qty", "").strip(),
                 request.form.get("city", "").strip(), name, phone,
                 request.form.get("note", "").strip(),
                 datetime.now().strftime("%Y-%m-%d %H:%M")),
            )
            db.commit()
            return redirect(url_for("requirements", posted=1))
    reqs = db.execute("SELECT * FROM requirements ORDER BY id DESC").fetchall()
    return render_template(
        "requirements.html", reqs=reqs, wa_link=wa_link,
        posted=request.args.get("posted"),
    )


@app.route("/sell", methods=["GET", "POST"])
def sell():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        category = request.form.get("category", "").strip()
        price = request.form.get("price", "").strip()
        seller = request.form.get("seller", "").strip()
        phone = request.form.get("phone", "").strip()
        if title and category and price.isdigit() and seller and phone:
            db = get_db()
            db.execute(
                "INSERT INTO listings (title, category, price, detail, seller, phone, city, created)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (title, category, int(price),
                 request.form.get("detail", "").strip(), seller, phone,
                 request.form.get("city", "").strip(),
                 datetime.now().strftime("%Y-%m-%d %H:%M")),
            )
            db.commit()
            return redirect(url_for("index", posted=1))
        return render_template("sell.html", categories=CATEGORIES, error=True)
    return render_template(
        "sell.html", categories=CATEGORIES, posted=request.args.get("posted"),
    )


@app.route("/about")
def about():
    return render_template("about.html")


@app.route("/admin", methods=["GET", "POST"])
def admin():
    if request.method == "POST":
        if request.form.get("password", "") == ADMIN_PASSWORD:
            session["admin"] = True
            return redirect(url_for("admin"))
        return render_template("admin_login.html", error=True)
    if not session.get("admin"):
        return render_template("admin_login.html")
    db = get_db()
    listings = db.execute("SELECT * FROM listings ORDER BY id DESC").fetchall()
    reqs = db.execute("SELECT * FROM requirements ORDER BY id DESC").fetchall()
    return render_template("admin.html", listings=listings, reqs=reqs)


@app.route("/admin/delete-listing/<int:lid>", methods=["POST"])
def admin_delete_listing(lid):
    if not session.get("admin"):
        return redirect(url_for("admin"))
    db = get_db()
    db.execute("DELETE FROM listings WHERE id=?", (lid,))
    db.commit()
    return redirect(url_for("admin"))


@app.route("/admin/delete-req/<int:rid>", methods=["POST"])
def admin_delete_req(rid):
    if not session.get("admin"):
        return redirect(url_for("admin"))
    db = get_db()
    db.execute("DELETE FROM requirements WHERE id=?", (rid,))
    db.commit()
    return redirect(url_for("admin"))


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin", None)
    return redirect(url_for("index"))


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
