import urllib.request, urllib.parse, http.cookiejar, json, ssl, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj), urllib.request.HTTPSHandler(context=ctx))

login_url = 'https://tuongtaccheo.com/logintoken.php'
login_data = urllib.parse.urlencode({'username': 'truongdvmmo8', 'password': 'Xuantruong@1412'}).encode('utf-8')
headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)', 'Referer': 'https://tuongtaccheo.com/'}

req = urllib.request.Request(login_url, data=login_data, headers=headers)
try:
    with opener.open(req) as resp:
        content = resp.read().decode('utf-8', errors='ignore')
        print('Login response:', content[:300])
except Exception as e:
    print('Login error:', e)

for path in ['home.php', 'menu.php', 'api/v2']:
    url = f'https://tuongtaccheo.com/{path}'
    try:
        req = urllib.request.Request(url, headers=headers)
        with opener.open(req) as resp:
            text = resp.read().decode('utf-8', errors='ignore')
            print(f'=== {path} ===')
            print(text[:300])
    except Exception as e:
        print(f'{path} error: {e}')
