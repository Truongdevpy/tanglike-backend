with open("backend/app/providers/tuongtaccheo.py", "r", encoding="utf-8") as f:
    content = f.read()

target = """            async with httpx.AsyncClient(timeout=25.0) as client:
                payload = {
                    "key": self.api_key,
                    "action": "add",
                    "service": sid_str,
                    "link": link,
                    "quantity": int(quantity)
                }"""

replacement = """            # Handle web-only services: TikTok Share Live and YouTube Subscribers
            if sid_str in ["ttc_tiktok_share_live", "ttc_youtube_sub"]:
                endpoint = "https://tuongtaccheo.com/tiktok/tangsharelive/themvip.php" if sid_str == "ttc_tiktok_share_live" else "https://tuongtaccheo.com/youtube/tangsub/themvip.php"
                async with httpx.AsyncClient(timeout=25.0, follow_redirects=True) as web_client:
                    await web_client.post("https://tuongtaccheo.com/login.php", data={
                        "username": "truongdvmmo8",
                        "password": "Xuantruong@1412",
                        "submit": "Đăng nhập"
                    })
                    order_res = await web_client.post(endpoint, data={
                        "maghinho": "tanglike_auto",
                        "link": link.strip(),
                        "sl": int(quantity),
                        "dateTime": ""
                    })
                    text = order_res.text.strip()
                    if "thành công" in text.lower() or "thanh cong" in text.lower() or "true" in text.lower():
                        import time
                        return {"order_id": f"TTC-WEB-{int(time.time())}"}
                    else:
                        try:
                            j = order_res.json()
                            if "mess" in j:
                                raise Exception(j["mess"])
                        except Exception:
                            pass
                        raise Exception(f"TuongTacCheo phản hồi: {text[:120]}")

            # Smart clean Facebook post link to numeric ID if needed
            clean_link = str(link).strip()
            fb_post_services = {"1","2","3","4","5","6","7","8","9","10","11","12","13","14","18","32","33","34","35","36","37","38","39","40","41","42","43","44","45","46","49","50","51","52","53","54","55"}
            if sid_str in fb_post_services and ("facebook.com" in clean_link or "fb.watch" in clean_link):
                import re
                m_post = re.search(r"/(?:posts|videos|reel|reels|photos)/(?:[\w\.]+/)?(\d+)", clean_link)
                if m_post:
                    clean_link = m_post.group(1)
                else:
                    m_fbid = re.search(r"(?:story_fbid|fbid)=(\d+)", clean_link)
                    if m_fbid:
                        m_id = re.search(r"[?&]id=(\d+)", clean_link)
                        clean_link = f"{m_id.group(1)}_{m_fbid.group(1)}" if m_id else m_fbid.group(1)
                    else:
                        m_id = re.search(r"[?&]id=(\d+)", clean_link)
                        if m_id:
                            clean_link = m_id.group(1)

            async with httpx.AsyncClient(timeout=25.0) as client:
                payload = {
                    "key": self.api_key,
                    "action": "add",
                    "service": sid_str,
                    "link": clean_link,
                    "quantity": int(quantity)
                }"""

if target in content:
    content = content.replace(target, replacement, 1)
    with open("backend/app/providers/tuongtaccheo.py", "w", encoding="utf-8") as f:
        f.write(content)
    print("Patched tuongtaccheo.py successfully!")
else:
    print("Target block not found in tuongtaccheo.py")