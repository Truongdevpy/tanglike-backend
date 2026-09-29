with open("frontend/src/lib/api.ts", "r", encoding="utf-8") as f:
    text = f.read()

new_methods = """  testProviderConnection: (data: any) =>
    apiRequest('/admin/providers/test-connection', { method: 'POST', body: JSON.stringify(data) }),
  testSavedProviderConnection: (id: number) =>
    apiRequest(`/admin/providers/${id}/test-connection`, { method: 'POST' }),
  getProviderCatalog: (id: number) =>
    apiRequest(`/admin/providers/${id}/catalog`),
  previewBulkMarkup: (data: any) =>
    apiRequest('/admin/providers/preview-markup', { method: 'POST', body: JSON.stringify(data) }),
  importProviderServices: (id: number, data: any) =>
    apiRequest(`/admin/providers/${id}/import-services`, { method: 'POST', body: JSON.stringify(data) }),
  getAdminMappings: (params?: { provider_id?: number; search?: string; sync_status?: string }) => {
    const q = new URLSearchParams();
    if (params?.provider_id) q.set('provider_id', params.provider_id.toString());
    if (params?.search) q.set('search', params.search);
    if (params?.sync_status) q.set('sync_status', params.sync_status);
    const qs = q.toString();
    return apiRequest(`/admin/mappings${qs ? `?${qs}` : ''}`);
  },
  updateAdminMapping: (id: number, data: any) =>
    apiRequest(`/admin/mappings/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteAdminMapping: (id: number, unlinkService: boolean = true) =>
    apiRequest(`/admin/mappings/${id}?unlink_service=${unlinkService}`, { method: 'DELETE' }),
  syncProviderMappings: (id: number, alertThreshold: number = 15) =>
    apiRequest(`/admin/providers/${id}/sync-mappings?alert_threshold_percent=${alertThreshold}`, { method: 'POST' }),
  getPriceSyncLogs: (params?: { provider_id?: number; alert_only?: boolean; limit?: number }) => {
    const q = new URLSearchParams();
    if (params?.provider_id) q.set('provider_id', params.provider_id.toString());
    if (params?.alert_only) q.set('alert_only', 'true');
    if (params?.limit) q.set('limit', params.limit.toString());
    const qs = q.toString();
    return apiRequest(`/admin/price-sync-logs${qs ? `?${qs}` : ''}`);
  },
  syncAdminOrderStatus: (id: number) =>
    apiRequest(`/admin/orders/${id}/sync-status`, { method: 'POST' }),
  retryAdminOrderProvider: (id: number) =>
    apiRequest(`/admin/orders/${id}/retry-provider`, { method: 'POST' }),
  refillAdminOrderAction: (id: number) =>
    apiRequest(`/admin/orders/${id}/refill-action`, { method: 'POST' }),
  cancelAdminOrderAction: (id: number) =>
    apiRequest(`/admin/orders/${id}/cancel-action`, { method: 'POST' }),"""

target = "syncAdminProvider: (id: number) => apiRequest(`/admin/providers/${id}/sync`, { method: 'POST' }),"

if "testProviderConnection:" not in text:
    text = text.replace(target, target + "\n" + new_methods)
    with open("frontend/src/lib/api.ts", "w", encoding="utf-8") as f:
        f.write(text)
    print("Added API methods to frontend/src/lib/api.ts")
else:
    print("API methods already present")