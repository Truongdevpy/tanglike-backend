with open("frontend/src/lib/api.ts", "r", encoding="utf-8") as f:
    text = f.read()

target = """  testSavedProviderConnection: (id: number) =>
    apiRequest(`/admin/providers/${id}/test-connection`, { method: 'POST' }),"""

replacement = """  testSavedProviderConnection: (id: number) =>
    apiRequest(`/admin/providers/${id}/test-connection`, { method: 'POST' }),
  checkAllProviderBalances: () =>
    apiRequest('/admin/providers/check-all-balances', { method: 'POST' }),
  getProviderOrderStatus: (id: number, data: { order_id?: string; order_ids?: string[]; action?: string }) =>
    apiRequest(`/admin/providers/${id}/order-status`, { method: 'POST', body: JSON.stringify(data) }),"""

if target in text:
    text = text.replace(target, replacement, 1)
    with open("frontend/src/lib/api.ts", "w", encoding="utf-8") as f:
        f.write(text)
    print("Updated api.ts successfully!")
else:
    print("Target not found in api.ts")
