with open("frontend/src/app/admin/orders/page.tsx", "r", encoding="utf-8") as f:
    code = f.read()

old_th = """                    <th className="p-4">Số lượng</th>
                    <th className="p-4">Giá tiền</th>
                    <th className="p-4">Tiến độ (Bắt đầu / Còn lại)</th>"""

# Match both possible encodings of header
old_block = """                    <th className="p-4">Sá»‘ lÆ°á»£ng</th>
                    <th className="p-4">GiÃ¡ tiá» n</th>
                    <th className="p-4">Tiáº¿n Ä‘á»™ (Báº¯t Ä‘áº§u / CÃ²n láº¡i)</th>"""

new_block = """                    <th className="p-4">Số lượng</th>
                    <th className="p-4">Doanh thu</th>
                    <th className="p-4">Gốc / Lãi</th>
                    <th className="p-4">Tiến độ (Bắt đầu / Còn lại)</th>"""

if old_block in code:
    code = code.replace(old_block, new_block)
    print("Replaced table header (encoded)")
elif old_th in code:
    code = code.replace(old_th, new_block)
    print("Replaced table header (utf8)")

old_td = """                      <td className="p-4 font-mono font-semibold">{o.quantity.toLocaleString('vi-VN')}</td>
                      <td className="p-4 font-mono text-emerald-400 font-semibold whitespace-nowrap">{o.price.toLocaleString('vi-VN')}?</td>
                      <td className="p-4 font-mono text-slate-400 text-[11px] whitespace-nowrap">"""

# Let's inspect the actual row in the file
import re
match = re.search(r'<td className="p-4 font-mono font-semibold">\{o\.quantity.*?<td className="p-4 font-mono text-slate-400 text-\[11px\] whitespace-nowrap">', code, re.DOTALL)
if match:
    replacement = """<td className="p-4 font-mono font-semibold">{o.quantity.toLocaleString('vi-VN')}</td>
                      <td className="p-4 font-mono text-emerald-400 font-semibold whitespace-nowrap">
                        {Number(o.price || 0).toLocaleString('vi-VN')}đ
                      </td>
                      <td className="p-4 font-mono whitespace-nowrap text-[11px]">
                        <div className="text-slate-400">Gốc: {Number(o.provider_cost || 0).toLocaleString('vi-VN')}đ</div>
                        <div className="text-amber-400 font-bold">Lãi: +{Number(o.profit || 0).toLocaleString('vi-VN')}đ</div>
                      </td>
                      <td className="p-4 font-mono text-slate-400 text-[11px] whitespace-nowrap">"""
    code = code[:match.start()] + replacement + code[match.end():]
    print("Replaced order row TD!")

with open("frontend/src/app/admin/orders/page.tsx", "w", encoding="utf-8") as f:
    f.write(code)

print("Saved frontend/src/app/admin/orders/page.tsx")