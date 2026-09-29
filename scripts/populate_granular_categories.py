import os
import shutil
import sqlite3
import unicodedata
import re
from datetime import datetime

def detect_platform(cat_raw: str, name_raw: str = "") -> str:
    c = (cat_raw or "").lower().strip()
    n = (name_raw or "").lower().strip()
    # Check category first
    if "shopee" in c:
        return "Shopee"
    if "traffic" in c or "website" in c or "webtraffic" in c:
        return "Webtraffic"
    if "threads" in c:
        return "Threads"
    if "tiktok" in c:
        return "TikTok"
    if "facebook" in c or "fb" in c:
        return "Facebook"
    if "instagram" in c or "ig" in c:
        return "Instagram"
    if "telegram" in c:
        return "Telegram"
    if "twitter" in c or " x " in c:
        return "Twitter"
    if "youtube" in c or "yt" in c:
        return "YouTube"
    if "google" in c:
        return "Google"

    # Fallback to service name
    if "shopee" in n:
        return "Shopee"
    if "traffic" in n or "website" in n or "webtraffic" in n:
        return "Webtraffic"
    if "threads" in n:
        return "Threads"
    if "tiktok" in n:
        return "TikTok"
    if "facebook" in n or "fb" in n:
        return "Facebook"
    if "instagram" in n or "ig" in n:
        return "Instagram"
    if "telegram" in n:
        return "Telegram"
    if "twitter" in n or " x " in n:
        return "Twitter"
    if "youtube" in n or "yt" in n:
        return "YouTube"
    if "google" in n:
        return "Google"

    return "Facebook"

def get_platform_sort_order(platform: str) -> int:
    orders = {
        "TikTok": 1,
        "Facebook": 2,
        "Instagram": 3,
        "Telegram": 4,
        "Twitter": 5,
        "YouTube": 6,
        "Webtraffic": 7,
        "Threads": 8,
        "Shopee": 9,
        "Google": 10
    }
    return orders.get(platform, 50)

def get_platform_icon(platform: str) -> str:
    icons = {
        "Facebook": "Facebook",
        "TikTok": "Video",
        "Instagram": "Instagram",
        "YouTube": "Youtube",
        "Telegram": "Send",
        "Twitter": "Twitter",
        "Google": "Globe",
        "Shopee": "ShoppingBag",
        "Webtraffic": "Globe",
        "Threads": "AtSign",
    }
    return icons.get(platform, "Layers")

def make_slug(name: str, platform: str, existing_slugs: set) -> str:
    clean = unicodedata.normalize("NFKD", f"{platform}-{name}").encode("ascii", "ignore").decode("utf-8").lower()
    base = re.sub(r"[^a-z0-9]+", "-", clean).strip("-")
    if not base:
        base = f"cat-{platform.lower()}"
    base = base[:55].strip("-")
    slug = base
    suf = 1
    while slug in existing_slugs:
        slug = f"{base}-{suf}"
        suf += 1
    existing_slugs.add(slug)
    return slug

def migrate_database(db_path: str):
    if not os.path.exists(db_path):
        print(f"Skipping non-existent db: {db_path}")
        return

    print(f"\n--- Migrating {db_path} ---")
    bak_path = f"{db_path}.bak_gran"
    shutil.copy2(db_path, bak_path)
    print(f"Backed up to {bak_path}")

    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    # Load existing categories
    c.execute("SELECT id, name, slug, platform FROM categories")
    cat_rows = c.fetchall()
    cat_map = {(r[3].lower(), r[1].lower().strip()): r[0] for r in cat_rows}
    existing_slugs = set(r[2] for r in cat_rows)

    # 1. Process provider_service_mappings (THMXH & mapped services)
    c.execute("SELECT service_id, external_category, external_name FROM provider_service_mappings WHERE service_id IS NOT NULL")
    mappings = c.fetchall()

    created_cats_count = 0
    updated_services_count = 0

    for sid, ext_cat, ext_name in mappings:
        ext_cat = (ext_cat or "Mạng Xã Hội").strip()
        if ext_cat == "General" and ("youtube" in (ext_name or "").lower() or "livestream" in (ext_name or "").lower()):
            ext_cat = "YouTube | Livestream Views 👁‍🗨"

        plat = detect_platform(ext_cat, ext_name)
        key = (plat.lower(), ext_cat.lower())

        cat_id = cat_map.get(key)
        if not cat_id:
            slug = make_slug(ext_cat, plat, existing_slugs)
            icon = get_platform_icon(plat)
            sort_order = get_platform_sort_order(plat)
            now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

            c.execute(
                """INSERT INTO categories (name, slug, platform, icon, description, status, sort_order, created_at, is_deleted)
                   VALUES (?, ?, ?, ?, ?, 'ACTIVE', ?, ?, 0)""",
                (ext_cat, slug, plat, icon, f"Danh mục dịch vụ {plat} - {ext_cat}", sort_order, now)
            )
            cat_id = c.lastrowid
            cat_map[key] = cat_id
            created_cats_count += 1

        # Update service
        c.execute("UPDATE services SET category_id = ?, platform = ? WHERE id = ?", (cat_id, plat, sid))
        updated_services_count += 1

    # 2. Process unmapped services (TuongTacCheo & standard services)
    c.execute("SELECT id, name, platform, category_id, provider_id FROM services WHERE id NOT IN (SELECT service_id FROM provider_service_mappings WHERE service_id IS NOT NULL)")
    unmapped = c.fetchall()

    for sid, name, plat, old_cat_id, provider_id in unmapped:
        plat = detect_platform("", name) if (not plat or plat == "Facebook") else plat
        name_lower = name.lower()

        # Find best matching category name
        matched_cat_name = None
        if plat == "Facebook":
            if "viplike" in name_lower:
                matched_cat_name = "Facebook VIP Like | Vietnam🇻🇳 ⭐⭐"
            elif "bình luận" in name_lower or "comment" in name_lower:
                matched_cat_name = "Facebook | Tăng Comment Bài Viết"
            elif "theo dõi" in name_lower or "follow" in name_lower or "sub" in name_lower:
                matched_cat_name = "Facebook Page Follow"
            elif "fanpage" in name_lower:
                matched_cat_name = "Facebook Page Follow"
            elif "nhóm" in name_lower or "group" in name_lower:
                matched_cat_name = "Facebook - Group Members"
            elif "cảm xúc" in name_lower or any(r in name_lower for r in ["love", "care", "haha", "wow", "sad", "angry"]):
                matched_cat_name = "Facebook | Like Post Cảm Xúc Mix👍❤️🤗 "
            else:
                matched_cat_name = "Facebook | Tăng Like Post"
        elif plat == "TikTok":
            if "follow" in name_lower:
                matched_cat_name = "TikTok Follower VietNam 📈"
            elif "tym" in name_lower or "like" in name_lower:
                matched_cat_name = "TikTok Like| Real | Vietnamese 🇻🇳 ❤️"
            elif "view" in name_lower:
                matched_cat_name = "Tiktok Video Views |👁‍🗨✴️"
            elif "share" in name_lower:
                matched_cat_name = "TikTok Share ➦"
            elif "save" in name_lower or "yêu thích" in name_lower:
                matched_cat_name = "TikTok Sav"
            elif "bình luận" in name_lower:
                matched_cat_name = "Tiktok Comments 💬"
            else:
                matched_cat_name = "TikTok Growth"
        elif plat == "YouTube":
            if "sub" in name_lower:
                matched_cat_name = "YouTube | Subscribe"
            elif "bình luận" in name_lower:
                matched_cat_name = "Youtube Comment"
            elif "short" in name_lower:
                matched_cat_name = "YouTube | Shorts Videos"
            else:
                matched_cat_name = "YouTube | Views | Watchtime"
        elif plat == "Instagram":
            if "follow" in name_lower:
                matched_cat_name = "Instagram Followers [Guaranteed] "
            else:
                matched_cat_name = "Instagram Likes"
        elif plat == "Google":
            matched_cat_name = "Google Đánh Giá 5 Sao"

        if matched_cat_name:
            key = (plat.lower(), matched_cat_name.lower())
            cat_id = cat_map.get(key)
            if not cat_id:
                slug = make_slug(matched_cat_name, plat, existing_slugs)
                icon = get_platform_icon(plat)
                sort_order = get_platform_sort_order(plat)
                now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                c.execute(
                    """INSERT INTO categories (name, slug, platform, icon, description, status, sort_order, created_at, is_deleted)
                       VALUES (?, ?, ?, ?, ?, 'ACTIVE', ?, ?, 0)""",
                    (matched_cat_name, slug, plat, icon, f"Danh mục dịch vụ {plat} - {matched_cat_name}", sort_order, now)
                )
                cat_id = c.lastrowid
                cat_map[key] = cat_id
                created_cats_count += 1
            c.execute("UPDATE services SET category_id = ?, platform = ? WHERE id = ?", (cat_id, plat, sid))
            updated_services_count += 1

    conn.commit()

    # Verify counts
    c.execute("SELECT COUNT(*) FROM categories WHERE is_deleted = 0")
    total_cats = c.fetchone()[0]
    print(f"Total active categories now: {total_cats} (Created {created_cats_count} new)")

    c.execute("SELECT platform, COUNT(*) FROM categories WHERE is_deleted = 0 GROUP BY platform ORDER BY platform")
    print("Categories per platform:")
    for row in c.fetchall():
        print(f"  - {row[0]}: {row[1]} categories")

    c.execute("SELECT platform, COUNT(*) FROM services WHERE is_deleted = 0 AND status = 'ACTIVE' GROUP BY platform ORDER BY platform")
    print("Active services per platform:")
    for row in c.fetchall():
        print(f"  - {row[0]}: {row[1]} services")

    conn.close()

if __name__ == "__main__":
    migrate_database("tanglike.db")
    if os.path.exists("backend/tanglike.db"):
        migrate_database("backend/tanglike.db")
