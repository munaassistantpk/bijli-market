"""BijliMarket v3 - Pakistan ka electrical marketplace.
Features: listings, demand board, bidding, in-app chat (text + voice),
favorites, deals + reviews, live rates (floor+trend), market summary + mood,
seller profiles, brand directory, events, admin panel.
WhatsApp integration REMOVED - in-app chat only.
"""
import os
import re
import sqlite3
import uuid
from datetime import datetime, date
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, g, session
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "bijlimarket-dev-key-change-me")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "bijli123")
DB_PATH = os.path.join(os.path.dirname(__file__), "bijlimarket.db")

CATEGORIES = [
    "LED Bulbs", "LED Downlights", "LED Panels", "Battens & Tubes",
    "Flood Lights", "Street Lights", "Highbay Lights", "Garden & Outdoor",
    "LED Strips & Profiles", "LED Drivers", "Wires & Cables",
    "Switches & Sockets", "Breakers & Panels", "Fans", "Other Electrical",
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
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE IF NOT EXISTS listings (
        id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
        category TEXT NOT NULL, price INTEGER NOT NULL, detail TEXT DEFAULT '',
        seller TEXT NOT NULL, phone TEXT NOT NULL, city TEXT DEFAULT '',
        seller_uid INTEGER DEFAULT NULL, created TEXT NOT NULL)""")
    # migrate old DBs
    cols = [r[1] for r in db.execute("PRAGMA table_info(listings)").fetchall()]
    if "seller_uid" not in cols:
        db.execute("ALTER TABLE listings ADD COLUMN seller_uid INTEGER DEFAULT NULL")
    db.execute("""CREATE TABLE IF NOT EXISTS requirements (
        id INTEGER PRIMARY KEY AUTOINCREMENT, product TEXT NOT NULL,
        qty TEXT DEFAULT '', city TEXT DEFAULT '', name TEXT NOT NULL,
        phone TEXT NOT NULL, note TEXT DEFAULT '', uid INTEGER DEFAULT NULL,
        created TEXT NOT NULL)""")
    cols = [r[1] for r in db.execute("PRAGMA table_info(requirements)").fetchall()]
    if "uid" not in cols:
        db.execute("ALTER TABLE requirements ADD COLUMN uid INTEGER DEFAULT NULL")
    db.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
        phone TEXT UNIQUE NOT NULL, pw TEXT NOT NULL, city TEXT DEFAULT '',
        created TEXT NOT NULL)""")
    cols = [r[1] for r in db.execute("PRAGMA table_info(users)").fetchall()]
    if "bio" not in cols:
        db.execute("ALTER TABLE users ADD COLUMN bio TEXT DEFAULT ''")
    if "is_verified" not in cols:
        db.execute("ALTER TABLE users ADD COLUMN is_verified INTEGER DEFAULT 0")
    db.execute("""CREATE TABLE IF NOT EXISTS favorites (
        uid INTEGER NOT NULL, lid INTEGER NOT NULL,
        UNIQUE(uid, lid))""")
    db.execute("""CREATE TABLE IF NOT EXISTS bids (
        id INTEGER PRIMARY KEY AUTOINCREMENT, req_id INTEGER NOT NULL,
        bidder_uid INTEGER NOT NULL, price INTEGER NOT NULL,
        note TEXT DEFAULT '', created TEXT NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT, sender_uid INTEGER NOT NULL,
        receiver_uid INTEGER NOT NULL, lid INTEGER DEFAULT NULL,
        text TEXT NOT NULL, seen INTEGER DEFAULT 0, created TEXT NOT NULL)""")
    cols = [r[1] for r in db.execute("PRAGMA table_info(messages)").fetchall()]
    if "audio" not in cols:
        db.execute("ALTER TABLE messages ADD COLUMN audio TEXT DEFAULT ''")
    db.execute("""CREATE TABLE IF NOT EXISTS reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT, deal_id INTEGER NOT NULL,
        reviewer_uid INTEGER NOT NULL, reviewee_uid INTEGER NOT NULL,
        rating INTEGER NOT NULL, comment TEXT DEFAULT '',
        created TEXT NOT NULL,
        UNIQUE(deal_id, reviewer_uid))""")
    db.execute("""CREATE TABLE IF NOT EXISTS price_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL,
        category TEXT NOT NULL, floor_price INTEGER NOT NULL,
        UNIQUE(day, category))""")
    db.execute("""CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
        body TEXT DEFAULT '', created TEXT NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS deals (
        id INTEGER PRIMARY KEY AUTOINCREMENT, lid INTEGER NOT NULL,
        buyer_uid INTEGER NOT NULL, seller_uid INTEGER NOT NULL,
        price INTEGER NOT NULL, status TEXT DEFAULT 'proposed',
        created TEXT NOT NULL)""")
    db.commit()

    # Seed owner user + link seed listings
    if db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        db.execute(
            "INSERT INTO users (name, phone, pw, city, created) VALUES (?,?,?,?,?)",
            ("Imran Electric Store", "03219807144",
             generate_password_hash(ADMIN_PASSWORD), "Abbottabad",
             datetime.now().strftime("%Y-%m-%d %H:%M")))
        db.commit()
    owner = db.execute("SELECT id FROM users WHERE phone='03219807144'").fetchone()
    if owner:
        db.execute("UPDATE listings SET seller_uid=? WHERE seller_uid IS NULL",
                   (owner["id"],))
        db.execute("UPDATE requirements SET uid=? WHERE uid IS NULL",
                   (owner["id"],))
        db.execute("UPDATE users SET is_verified=1 WHERE id=?", (owner["id"],))
        db.commit()

    # Seed listings from bundled JSON on first run
    if db.execute("SELECT COUNT(*) FROM listings").fetchone()[0] == 0:
        seed_file = os.path.join(os.path.dirname(__file__), "seed_data.json")
        if os.path.exists(seed_file):
            import json
            rows = json.load(open(seed_file, encoding="utf-8"))
            ouid = owner["id"] if owner else None
            db.executemany(
                "INSERT INTO listings (title, category, price, detail, seller, phone, city, seller_uid, created)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                [(r["title"], r["category"], r["price"], r.get("detail", ""),
                  r["seller"], r["phone"], r.get("city", ""), ouid, r["created"])
                 for r in rows])
            db.commit()
    db.close()
    snapshot_prices()


def snapshot_prices():
    """Roz ka category-wise floor price record karo (trend ke liye)."""
    db = sqlite3.connect(DB_PATH)
    today = date.today().isoformat()
    for (cat,) in db.execute("SELECT DISTINCT category FROM listings").fetchall():
        fl = db.execute("SELECT MIN(price) FROM listings WHERE category=?",
                        (cat,)).fetchone()[0]
        if fl:
            db.execute(
                "INSERT OR IGNORE INTO price_history (day, category, floor_price)"
                " VALUES (?,?,?)", (today, cat, fl))
    db.commit()
    db.close()


def current_user():
    uid = session.get("uid")
    if not uid:
        return None
    return get_db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def login_required(f):
    @wraps(f)
    def wrap(*a, **kw):
        if not session.get("uid"):
            return redirect(url_for("login", next=request.path))
        return f(*a, **kw)
    return wrap


@app.context_processor
def inject_user():
    u = current_user()
    unread = 0
    if u:
        unread = get_db().execute(
            "SELECT COUNT(*) FROM messages WHERE receiver_uid=? AND seen=0",
            (u["id"],)).fetchone()[0]
    return dict(me=u, unread=unread)


def seller_stats(uid):
    """Seller ki rating (avg, count) + verification."""
    db = get_db()
    r = db.execute(
        "SELECT AVG(rating) a, COUNT(*) c FROM reviews WHERE reviewee_uid=?",
        (uid,)).fetchone()
    u = db.execute("SELECT is_verified FROM users WHERE id=?", (uid,)).fetchone()
    avg = round(r["a"], 1) if r["a"] else 0
    return {"avg": avg, "count": r["c"],
            "verified": bool(u and u["is_verified"])}


def stars_html(avg):
    full = int(round(avg))
    return "★" * full + "☆" * (5 - full)


GENERIC_WORDS = {"LED", "PROFILE", "LIGHT", "BULB", "TUBE", "PANEL", "FAN",
                 "WIRE", "CABLE", "SWITCH", "STRIP", "BATTEN", "FLOOD",
                 "STREET", "HIGHBAY", "DRIVER", "DOWNLIGHT"}


def brand_of(title):
    """Listing title se brand nikalo (pehla lafz, safai ke sath)."""
    w = (title or "").strip().split()
    if not w:
        return "Diger"
    first = w[0].upper()
    if first.startswith("OST"):
        return "Ostric"
    if any(ch.isdigit() for ch in w[0]) or "-" in w[0]:
        return "Diger"  # model code, brand nahi
    b = re.sub(r"[^A-Za-z]", "", w[0]).upper()
    if not b or b in GENERIC_WORDS or len(b) <= 2:
        return "Diger"
    return b[:18].title()


def market_mood(trends):
    """Fear/greed jaisa mood meter: 0-100. Zyada = khareedar ke haq me (qeemat gir rahi)."""
    ups = sum(1 for t in trends.values() if t == "up")
    downs = sum(1 for t in trends.values() if t == "down")
    total = sum(1 for t in trends.values() if t in ("up", "down"))
    if not total:
        return {"score": 50, "label": "Nayi market — data jama ho raha hai"}
    score = round(50 + (downs - ups) / total * 50)
    if score >= 65:
        label = "Khareedar ke haq me — qeematen gir rahi hain 📉"
    elif score <= 35:
        label = "Bechne wale ke haq me — qeematen barh rahi hain 📈"
    else:
        label = "Mutawazin bazar — sakoon se khareeden ⚖️"
    return {"score": score, "label": label}


# ---------- Home / listings ----------
@app.route("/")
def index():
    db = get_db()
    q = request.args.get("q", "").strip()
    cat = request.args.get("cat", "").strip()
    sql = ("SELECT l.*, u.name AS uname FROM listings l "
           "LEFT JOIN users u ON u.id=l.seller_uid")
    clauses, params = [], []
    if cat:
        clauses.append("l.category=?"); params.append(cat)
    if q:
        clauses.append("(l.title LIKE ? OR l.detail LIKE ?)")
        params += [f"%{q}%", f"%{q}%"]
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY l.id DESC"
    listings = db.execute(sql, params).fetchall()
    counts = {r["category"]: r["c"] for r in db.execute(
        "SELECT category, COUNT(*) c FROM listings GROUP BY category").fetchall()}
    # market summary: floor price per category + trend
    floors, trends = {}, {}
    for (c,) in db.execute("SELECT DISTINCT category FROM listings").fetchall():
        r = db.execute(
            "SELECT floor_price FROM price_history WHERE category=? ORDER BY day DESC LIMIT 1",
            (c,)).fetchone()
        fl = r["floor_price"] if r else None
        floors[c] = fl
        if fl is None:
            trends[c] = "new"
            continue
        old = db.execute(
            "SELECT floor_price FROM price_history WHERE category=? AND day<date('now')"
            " ORDER BY day DESC LIMIT 1", (c,)).fetchone()
        if not old:
            trends[c] = "new"
        elif old["floor_price"] < fl:
            trends[c] = "up"
        elif old["floor_price"] > fl:
            trends[c] = "down"
        else:
            trends[c] = "flat"
    sellers = db.execute("SELECT COUNT(DISTINCT seller_uid) FROM listings").fetchone()[0]
    demands = db.execute("SELECT COUNT(*) FROM requirements").fetchone()[0]
    mood = market_mood(trends)
    evs = db.execute("SELECT * FROM events ORDER BY id DESC LIMIT 3").fetchall()
    favs = set()
    if session.get("uid"):
        favs = {r["lid"] for r in db.execute(
            "SELECT lid FROM favorites WHERE uid=?", (session["uid"],)).fetchall()}
    return render_template("index.html", listings=listings, categories=CATEGORIES,
                           counts=counts, q=q, cat=cat, floors=floors,
                           trends=trends, sellers=sellers, demands=demands,
                           events=evs, favs=favs, mood=mood)


@app.route("/listing/<int:lid>")
def listing_detail(lid):
    db = get_db()
    item = db.execute(
        "SELECT l.*, u.name AS uname FROM listings l LEFT JOIN users u ON u.id=l.seller_uid"
        " WHERE l.id=?", (lid,)).fetchone()
    if not item:
        return redirect(url_for("index"))
    floor = db.execute(
        "SELECT MIN(price) FROM listings WHERE category=?", (item["category"],)).fetchone()[0]
    n = db.execute("SELECT COUNT(*) FROM listings WHERE category=?",
                   (item["category"],)).fetchone()[0]
    related = db.execute(
        "SELECT * FROM listings WHERE category=? AND id!=? ORDER BY id DESC LIMIT 6",
        (item["category"], lid)).fetchall()
    is_fav = session.get("uid") and db.execute(
        "SELECT 1 FROM favorites WHERE uid=? AND lid=?",
        (session["uid"], lid)).fetchone()
    sstats = seller_stats(item["seller_uid"]) if item["seller_uid"] else None
    return render_template("listing.html", item=item, related=related,
                           floor=floor, ncat=n, is_fav=bool(is_fav),
                           sstats=sstats, stars=stars_html)


@app.route("/favorite/<int:lid>", methods=["POST"])
@login_required
def favorite(lid):
    db = get_db()
    ex = db.execute("SELECT 1 FROM favorites WHERE uid=? AND lid=?",
                    (session["uid"], lid)).fetchone()
    if ex:
        db.execute("DELETE FROM favorites WHERE uid=? AND lid=?", (session["uid"], lid))
    else:
        db.execute("INSERT INTO favorites (uid, lid) VALUES (?,?)", (session["uid"], lid))
    db.commit()
    return redirect(request.referrer or url_for("listing_detail", lid=lid))


@app.route("/favorites")
@login_required
def favorites():
    db = get_db()
    items = db.execute(
        "SELECT l.* FROM favorites f JOIN listings l ON l.id=f.lid WHERE f.uid=? ORDER BY l.id DESC",
        (session["uid"],)).fetchall()
    return render_template("favorites.html", items=items)


# ---------- Auth ----------
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        phone = request.form.get("phone", "").strip()
        pw = request.form.get("pw", "")
        if name and phone and len(pw) >= 4:
            db = get_db()
            try:
                cur = db.execute(
                    "INSERT INTO users (name, phone, pw, city, created) VALUES (?,?,?,?,?)",
                    (name, phone, generate_password_hash(pw),
                     request.form.get("city", "").strip(),
                     datetime.now().strftime("%Y-%m-%d %H:%M")))
                db.commit()
                session["uid"] = cur.lastrowid
                return redirect(request.args.get("next") or url_for("index"))
            except sqlite3.IntegrityError:
                return render_template("register.html", error="Yeh number pehle se registered hai.")
        return render_template("register.html", error="Naam, number aur kam-az-kam 4 harf ka password zaroori hai.")
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        phone = request.form.get("phone", "").strip()
        pw = request.form.get("pw", "")
        u = get_db().execute("SELECT * FROM users WHERE phone=?", (phone,)).fetchone()
        if u and check_password_hash(u["pw"], pw):
            session["uid"] = u["id"]
            return redirect(request.args.get("next") or url_for("index"))
        return render_template("login.html", error="Number ya password ghalat hai.")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.pop("uid", None)
    return redirect(url_for("index"))


@app.route("/account", methods=["GET", "POST"])
@login_required
def account():
    db = get_db()
    u = current_user()
    if request.method == "POST":
        bio = request.form.get("bio", "").strip()[:300]
        city = request.form.get("city", "").strip()[:60]
        db.execute("UPDATE users SET bio=?, city=? WHERE id=?", (bio, city, u["id"]))
        db.commit()
        return redirect(url_for("account"))
    my_listings = db.execute("SELECT * FROM listings WHERE seller_uid=? ORDER BY id DESC",
                             (u["id"],)).fetchall()
    my_bids = db.execute(
        "SELECT b.*, r.product FROM bids b JOIN requirements r ON r.id=b.req_id"
        " WHERE b.bidder_uid=? ORDER BY b.id DESC", (u["id"],)).fetchall()
    st = seller_stats(u["id"])
    return render_template("account.html", my_listings=my_listings, my_bids=my_bids,
                           st=st, stars=stars_html)


# ---------- Demand board + bidding ----------
@app.route("/requirements", methods=["GET", "POST"])
def requirements():
    db = get_db()
    if request.method == "POST":
        if not session.get("uid"):
            return redirect(url_for("login", next=url_for("requirements")))
        product = request.form.get("product", "").strip()
        u = current_user()
        if product:
            db.execute(
                "INSERT INTO requirements (product, qty, city, name, phone, note, uid, created)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (product, request.form.get("qty", "").strip(),
                 request.form.get("city", "").strip() or u["city"],
                 u["name"], u["phone"], request.form.get("note", "").strip(),
                 u["id"], datetime.now().strftime("%Y-%m-%d %H:%M")))
            db.commit()
            return redirect(url_for("requirements", posted=1))
    reqs = db.execute(
        "SELECT r.*, (SELECT COUNT(*) FROM bids b WHERE b.req_id=r.id) AS bidcount"
        " FROM requirements r ORDER BY r.id DESC").fetchall()
    bids = {}
    for r in reqs:
        bids[r["id"]] = db.execute(
            "SELECT b.*, u.name AS bname FROM bids b JOIN users u ON u.id=b.bidder_uid"
            " WHERE b.req_id=? ORDER BY b.price ASC", (r["id"],)).fetchall()
    return render_template("requirements.html", reqs=reqs, bids=bids,
                           posted=request.args.get("posted"))


@app.route("/bid/<int:rid>", methods=["POST"])
@login_required
def bid(rid):
    price = request.form.get("price", "").strip()
    if price.isdigit():
        db = get_db()
        db.execute(
            "INSERT INTO bids (req_id, bidder_uid, price, note, created) VALUES (?,?,?,?,?)",
            (rid, session["uid"], int(price), request.form.get("note", "").strip(),
             datetime.now().strftime("%Y-%m-%d %H:%M")))
        db.commit()
    return redirect(url_for("requirements"))


# ---------- Sell ----------
@app.route("/sell", methods=["GET", "POST"])
@login_required
def sell():
    u = current_user()
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        category = request.form.get("category", "").strip()
        price = request.form.get("price", "").strip()
        if title and category and price.isdigit():
            db = get_db()
            db.execute(
                "INSERT INTO listings (title, category, price, detail, seller, phone, city, seller_uid, created)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (title, category, int(price), request.form.get("detail", "").strip(),
                 u["name"], u["phone"],
                 request.form.get("city", "").strip() or u["city"],
                 u["id"], datetime.now().strftime("%Y-%m-%d %H:%M")))
            db.commit()
            snapshot_prices()
            return redirect(url_for("index", posted=1))
        return render_template("sell.html", categories=CATEGORIES, error=True)
    return render_template("sell.html", categories=CATEGORIES)


# ---------- In-app chat ----------
@app.route("/chat")
@login_required
def chat_list():
    db = get_db()
    uid = session["uid"]
    rows = db.execute(
        """SELECT m.*, u.name AS uname,
             CASE WHEN m.sender_uid=? THEN m.receiver_uid ELSE m.sender_uid END AS other_uid
           FROM messages m JOIN users u ON u.id=CASE WHEN m.sender_uid=? THEN m.receiver_uid ELSE m.sender_uid END
           WHERE m.sender_uid=? OR m.receiver_uid=?
           GROUP BY other_uid ORDER BY MAX(m.id) DESC""",
        (uid, uid, uid, uid)).fetchall()
    unreads = {r["other_uid"]: r["c"] for r in db.execute(
        "SELECT sender_uid AS other_uid, COUNT(*) c FROM messages"
        " WHERE receiver_uid=? AND seen=0 GROUP BY sender_uid", (uid,)).fetchall()}
    return render_template("chat_list.html", rows=rows, unreads=unreads, uid=uid)


@app.route("/chat/<int:other_uid>", methods=["GET", "POST"])
@login_required
def chat_thread(other_uid):
    db = get_db()
    uid = session["uid"]
    other = db.execute("SELECT * FROM users WHERE id=?", (other_uid,)).fetchone()
    if not other or other_uid == uid:
        return redirect(url_for("chat_list"))
    if request.method == "POST":
        text = request.form.get("text", "").strip()
        if text:
            db.execute(
                "INSERT INTO messages (sender_uid, receiver_uid, lid, text, created)"
                " VALUES (?,?,?,?,?)",
                (uid, other_uid, request.args.get("lid") or None, text,
                 datetime.now().strftime("%Y-%m-%d %H:%M")))
            db.commit()
            return redirect(url_for("chat_thread", other_uid=other_uid))
    db.execute("UPDATE messages SET seen=1 WHERE sender_uid=? AND receiver_uid=?",
               (other_uid, uid))
    db.commit()
    msgs = db.execute(
        "SELECT * FROM messages WHERE (sender_uid=? AND receiver_uid=?)"
        " OR (sender_uid=? AND receiver_uid=?) ORDER BY id ASC",
        (uid, other_uid, other_uid, uid)).fetchall()
    return render_template("chat_thread.html", msgs=msgs, other=other, uid=uid)


@app.route("/chat/start/<int:lid>")
@login_required
def chat_start(lid):
    db = get_db()
    item = db.execute("SELECT seller_uid FROM listings WHERE id=?", (lid,)).fetchone()
    if not item or not item["seller_uid"] or item["seller_uid"] == session["uid"]:
        return redirect(url_for("listing_detail", lid=lid))
    return redirect(url_for("chat_thread", other_uid=item["seller_uid"], lid=lid))


# ---------- Deals (escrow v1: record only, no money movement) ----------
@app.route("/deal/propose/<int:lid>", methods=["POST"])
@login_required
def deal_propose(lid):
    db = get_db()
    item = db.execute("SELECT * FROM listings WHERE id=?", (lid,)).fetchone()
    if not item or not item["seller_uid"] or item["seller_uid"] == session["uid"]:
        return redirect(url_for("listing_detail", lid=lid))
    price = request.form.get("price", "").strip()
    db.execute(
        "INSERT INTO deals (lid, buyer_uid, seller_uid, price, created) VALUES (?,?,?,?,?)",
        (lid, session["uid"], item["seller_uid"],
         int(price) if price.isdigit() else item["price"],
         datetime.now().strftime("%Y-%m-%d %H:%M")))
    db.commit()
    return redirect(url_for("deals"))


# ---------- Seller profiles + reviews ----------
@app.route("/seller/<int:uid>")
def seller_profile(uid):
    db = get_db()
    u = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not u:
        return redirect(url_for("index"))
    listings = db.execute("SELECT * FROM listings WHERE seller_uid=? ORDER BY id DESC",
                           (uid,)).fetchall()
    revs = db.execute(
        """SELECT r.*, u.name AS rname FROM reviews r JOIN users u ON u.id=r.reviewer_uid
           WHERE r.reviewee_uid=? ORDER BY r.id DESC""", (uid,)).fetchall()
    st = seller_stats(uid)
    return render_template("seller.html", u=u, listings=listings, revs=revs,
                           st=st, stars=stars_html)


@app.route("/deal/<int:did>/review", methods=["POST"])
@login_required
def deal_review(did):
    db = get_db()
    d = db.execute("SELECT * FROM deals WHERE id=?", (did,)).fetchone()
    uid = session["uid"]
    if not d or d["status"] != "completed" or uid not in (d["buyer_uid"], d["seller_uid"]):
        return redirect(url_for("deals"))
    other = d["seller_uid"] if uid == d["buyer_uid"] else d["buyer_uid"]
    rating = request.form.get("rating", "5")
    rating = int(rating) if rating.isdigit() and 1 <= int(rating) <= 5 else 5
    try:
        db.execute(
            "INSERT INTO reviews (deal_id, reviewer_uid, reviewee_uid, rating, comment, created)"
            " VALUES (?,?,?,?,?,?)",
            (did, uid, other, rating, request.form.get("comment", "").strip()[:300],
             datetime.now().strftime("%Y-%m-%d %H:%M")))
        db.commit()
    except sqlite3.IntegrityError:
        pass
    return redirect(url_for("deals"))


# ---------- Brand directory ----------
@app.route("/brands")
def brands():
    db = get_db()
    rows = db.execute("SELECT id, title, category, price FROM listings").fetchall()
    info = {}
    for r in rows:
        b = brand_of(r["title"])
        e = info.setdefault(b, {"count": 0, "floor": None, "cats": set(), "sample": r["title"]})
        e["count"] += 1
        e["cats"].add(r["category"])
        if e["floor"] is None or r["price"] < e["floor"]:
            e["floor"] = r["price"]
    brands = sorted(info.items(), key=lambda kv: -kv[1]["count"])
    return render_template("brands.html", brands=brands)


# ---------- Voice messages ----------
AUDIO_DIR = os.path.join(os.path.dirname(__file__), "static", "audio")


@app.route("/chat/<int:other_uid>/audio", methods=["POST"])
@login_required
def chat_audio(other_uid):
    db = get_db()
    uid = session["uid"]
    other = db.execute("SELECT id FROM users WHERE id=?", (other_uid,)).fetchone()
    f = request.files.get("audio")
    if not other or other_uid == uid or not f:
        return redirect(url_for("chat_thread", other_uid=other_uid))
    os.makedirs(AUDIO_DIR, exist_ok=True)
    fname = secure_filename(f"v_{uid}_{uuid.uuid4().hex[:8]}.webm")
    f.save(os.path.join(AUDIO_DIR, fname))
    db.execute(
        "INSERT INTO messages (sender_uid, receiver_uid, lid, text, audio, created)"
        " VALUES (?,?,?,?,?,?)",
        (uid, other_uid, request.args.get("lid") or None, "",
         fname, datetime.now().strftime("%Y-%m-%d %H:%M")))
    db.commit()
    return redirect(url_for("chat_thread", other_uid=other_uid))


@app.route("/deals")
@login_required
def deals():
    db = get_db()
    uid = session["uid"]
    rows = db.execute(
        """SELECT d.*, l.title, bu.name AS bname, su.name AS sname,
             (SELECT COUNT(*) FROM reviews r WHERE r.deal_id=d.id AND r.reviewer_uid=? ) AS myrev
           FROM deals d JOIN listings l ON l.id=d.lid
           JOIN users bu ON bu.id=d.buyer_uid JOIN users su ON su.id=d.seller_uid
           WHERE d.buyer_uid=? OR d.seller_uid=? ORDER BY d.id DESC""",
        (uid, uid, uid)).fetchall()
    return render_template("deals.html", rows=rows, uid=uid)


@app.route("/deal/<int:did>/<action>", methods=["POST"])
@login_required
def deal_action(did, action):
    db = get_db()
    d = db.execute("SELECT * FROM deals WHERE id=?", (did,)).fetchone()
    if not d:
        return redirect(url_for("deals"))
    uid = session["uid"]
    if action == "accept" and uid == d["seller_uid"] and d["status"] == "proposed":
        db.execute("UPDATE deals SET status='accepted' WHERE id=?", (did,))
    elif action == "decline" and uid == d["seller_uid"] and d["status"] == "proposed":
        db.execute("UPDATE deals SET status='declined' WHERE id=?", (did,))
    elif action == "complete" and d["status"] == "accepted" and uid in (d["buyer_uid"], d["seller_uid"]):
        db.execute("UPDATE deals SET status='completed' WHERE id=?", (did,))
    elif action == "cancel" and d["status"] in ("proposed", "accepted") and uid in (d["buyer_uid"], d["seller_uid"]):
        db.execute("UPDATE deals SET status='cancelled' WHERE id=?", (did,))
    db.commit()
    return redirect(url_for("deals"))


# ---------- Events ----------
@app.route("/events")
def events():
    evs = get_db().execute("SELECT * FROM events ORDER BY id DESC").fetchall()
    return render_template("events.html", events=evs)


# ---------- About / admin ----------
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
    evs = db.execute("SELECT * FROM events ORDER BY id DESC").fetchall()
    users = db.execute("SELECT id, name, phone, city, created FROM users ORDER BY id DESC").fetchall()
    deals = db.execute(
        "SELECT d.*, l.title FROM deals d JOIN listings l ON l.id=d.lid ORDER BY d.id DESC LIMIT 20").fetchall()
    return render_template("admin.html", listings=listings, reqs=reqs,
                           events=evs, users=users, deals=deals)


@app.route("/admin/event", methods=["POST"])
def admin_event():
    if not session.get("admin"):
        return redirect(url_for("admin"))
    title = request.form.get("title", "").strip()
    if title:
        db = get_db()
        db.execute("INSERT INTO events (title, body, created) VALUES (?,?,?)",
                   (title, request.form.get("body", "").strip(),
                    datetime.now().strftime("%Y-%m-%d %H:%M")))
        db.commit()
    return redirect(url_for("admin"))


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


@app.route("/admin/delete-event/<int:eid>", methods=["POST"])
def admin_delete_event(eid):
    if not session.get("admin"):
        return redirect(url_for("admin"))
    db = get_db()
    db.execute("DELETE FROM events WHERE id=?", (eid,))
    db.commit()
    return redirect(url_for("admin"))


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin", None)
    return redirect(url_for("index"))


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
