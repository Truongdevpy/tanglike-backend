import urllib.request, urllib.parse, json, ssl, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

data = urllib.parse.urlencode({'key': 'fd179048690db78a4fdc3db450c12d6e', 'action': 'services'}).encode('utf-8')
req = urllib.request.Request('https://thmxh.com/api/v2', data=data, headers={'User-Agent': 'Mozilla/5.0'})
with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
    res = json.loads(resp.read().decode('utf-8'))

print('Total services returned by THMXH API:', len(res))

print('--- FACEBOOK SAMPLES ---')
fb_services = [s for s in res if 'facebook' in s.get('category', '').lower() or 'facebook' in s.get('name', '').lower()]
for s in fb_services[:10]:
    sid = s.get('service')
    cat = s.get('category')
    name = s.get('name')
    rate = s.get('rate')
    min_q = s.get('min')
    max_q = s.get('max')
    print(f'ID: {sid} | Cat: {cat} | Name: {name} | Rate: {rate} | Min: {min_q} | Max: {max_q}')

print('--- TIKTOK SAMPLES ---')
tt_services = [s for s in res if 'tiktok' in s.get('category', '').lower() or 'tiktok' in s.get('name', '').lower()]
for s in tt_services[:10]:
    sid = s.get('service')
    cat = s.get('category')
    name = s.get('name')
    rate = s.get('rate')
    min_q = s.get('min')
    max_q = s.get('max')
    print(f'ID: {sid} | Cat: {cat} | Name: {name} | Rate: {rate} | Min: {min_q} | Max: {max_q}')