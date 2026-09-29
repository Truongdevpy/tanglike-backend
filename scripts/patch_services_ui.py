with open("frontend/src/app/admin/services/page.tsx", "r", encoding="utf-8") as f:
    code = f.read()

import re

# Update table header
header_match = re.search(r'<thead.*?>.*?<tr.*?>.*?<th.*?>.*?Gi[aá] g[oố]c Provider.*?</tr>.*?</thead>', code, re.DOTALL)
if header_match:
    new_header = """<thead>
                  <tr className="border-b border-slate-800 text-slate-400 font-semibold uppercase text-[10px]">
                    <th className="pb-3 px-3">Mã</th>
                    <th className="pb-3 px-3">Nền tảng</th>
                    <th className="pb-3 px-3">Tên dịch vụ</th>
                    <th className="pb-3 px-3">Giá gốc Web NCC</th>
                    <th className="pb-3 px-3">Giá bán Khách</th>
                    <th className="pb-3 px-3">Lợi nhuận chênh lệch</th>
                    <th className="pb-3 px-3">Min / Max</th>
                    <th className="pb-3 px-3">Trạng thái</th>
                    <th className="pb-3 px-3 text-right">Thao tác</th>
                  </tr>
                </thead>"""
    code = code[:header_match.start()] + new_header + code[header_match.end():]
    print("Replaced Services table header!")

# Update table row cells
row_match = re.search(r'<td className="py-3 px-3 text-slate-400 font-mono">.*?</td>\s*<td className="py-3 px-3 font-black text-emerald-400 text-sm">.*?</td>', code, re.DOTALL)
if row_match:
    new_cells = """<td className="py-3 px-3 text-slate-400 font-mono whitespace-nowrap">
                        {(s.provider_price || 0).toLocaleString('vi-VN')}đ
                      </td>
                      <td className="py-3 px-3 font-black text-emerald-400 text-sm whitespace-nowrap">
                        {s.price.toLocaleString('vi-VN')}đ
                      </td>
                      <td className="py-3 px-3 font-mono whitespace-nowrap text-xs">
                        <span className="text-amber-400 font-bold">
                          +{Math.max(0, (s.price || 0) - (s.provider_price || 0)).toLocaleString('vi-VN')}đ
                        </span>
                        <span className="text-[10px] text-slate-400 ml-1">
                          ({s.provider_price > 0 ? Math.round(((s.price - s.provider_price) / s.provider_price) * 100) : 0}%)
                        </span>
                      </td>"""
    code = code[:row_match.start()] + new_cells + code[row_match.end():]
    print("Replaced Services table row cells!")

with open("frontend/src/app/admin/services/page.tsx", "w", encoding="utf-8") as f:
    f.write(code)

print("Saved frontend/src/app/admin/services/page.tsx")