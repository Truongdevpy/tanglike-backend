with open("frontend/src/lib/api.ts", "r", encoding="utf-8") as f:
    code = f.read()

target = "  changeUserStatus: (id: number, status: string) =>\n    apiRequest(`/admin/users/${id}/status?status_val=${status}`, { method: 'PUT' }),"

replacement = """  changeUserStatus: (id: number, status: string) =>
    apiRequest(`/admin/users/${id}/status?status_val=${status}`, { method: 'PUT' }),
  changeUserRole: (id: number, role: string) =>
    apiRequest(`/admin/users/${id}/role?role_val=${encodeURIComponent(role)}`, { method: 'PUT' }),
  toggleUserPro: (id: number, isPro: boolean) =>
    apiRequest(`/admin/users/${id}/pro?is_pro=${isPro}`, { method: 'PUT' }),
  upgradeToPro: () =>
    apiRequest('/users/upgrade-pro', { method: 'POST' }),"""

code = code.replace(target, replacement)

with open("frontend/src/lib/api.ts", "w", encoding="utf-8") as f:
    f.write(code)

print("Updated frontend/src/lib/api.ts with pro/role methods!")