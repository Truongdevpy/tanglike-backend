import sys

with open("frontend/src/app/admin/providers/page.tsx", "r", encoding="utf-8") as f:
    code = f.read()

tab5_marker = "{/* ======================================================== */}\n        {/* TAB 5: KIỂM TRA SỐ DƯ, SỐ XU & TRẠNG THÁI ĐƠN HÀNG API */}"

if tab5_marker not in code:
    pos = code.find("{showProviderModal &&")
    if pos != -1:
        # Search backwards for the comment block start
        comment_pos = code.rfind("{/*", 0, pos)
        insert_pos = comment_pos if comment_pos != -1 else pos

        tab5_content = """
        {/* ======================================================== */}
        {/* TAB 5: KIỂM TRA SỐ DƯ, SỐ XU & TRẠNG THÁI ĐƠN HÀNG API */}
        {/* ======================================================== */}
        {activeTab === 'status' && (
          <div className="space-y-6">
            {/* Top overview & check all */}
            <div className="p-6 rounded-3xl bg-[#0D131F] border border-slate-800 shadow-xl space-y-4">
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
                <div>
                  <h3 className="text-base font-bold text-white flex items-center space-x-2">
                    <Activity className="w-5 h-5 text-emerald-400" />
                    <span>Trạng Thái & Số Dư Khả Dụng Các Cổng API (Live)</span>
                  </h3>
                  <p className="text-xs text-slate-400 mt-1">
                    Gửi yêu cầu kiểm tra (action=balance) trực tiếp đến máy chủ THMXH, TuongTacCheo để lấy số dư và đo độ trễ mạng thực tế.
                  </p>
                </div>

                <button
                  type="button"
                  onClick={handleCheckAllBalances}
                  disabled={checkingAllBalances}
                  className="flex items-center space-x-2 px-5 py-2.5 rounded-xl bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white font-bold text-xs shadow-lg shadow-emerald-600/20 transition-all active:scale-95"
                >
                  <RefreshCw className={`w-4 h-4 ${checkingAllBalances ? 'animate-spin' : ''}`} />
                  <span>{checkingAllBalances ? 'Đang kiểm tra tất cả API...' : 'Kiểm Tra Tất Cả Số Dư API'}</span>
                </button>
              </div>

              {/* Providers live cards */}
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 pt-2">
                {providers.map((p) => {
                  const isTtc = (p.provider_type || '').toLowerCase() === 'tuongtaccheo' || (p.name || '').toLowerCase().includes('ttc');
                  const curr = p.currency || (isTtc ? 'XU' : (p.provider_type === 'thmxh' ? 'USD' : 'VND'));
                  const balDisplay = p.balance != null ? (curr === 'USD' ? `$${Number(p.balance).toFixed(4)} USD` : `${Number(p.balance).toLocaleString('vi-VN')} ${curr}`) : 'Chưa cập nhật';

                  return (
                    <div key={p.id} className="p-4 rounded-2xl bg-slate-900/80 border border-slate-800 space-y-3">
                      <div className="flex items-center justify-between">
                        <div className="flex items-center space-x-2">
                          <div className={`w-3 h-3 rounded-full ${p.status === 'ACTIVE' ? 'bg-emerald-400 animate-pulse' : 'bg-slate-500'}`} />
                          <h4 className="font-bold text-sm text-white">{p.name}</h4>
                        </div>
                        <span className="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-slate-800 text-slate-300 border border-slate-700">
                          {p.provider_type?.toUpperCase()}
                        </span>
                      </div>

                      <div className="p-3 rounded-xl bg-slate-950/70 border border-slate-800/80 space-y-1">
                        <div className="text-[11px] text-slate-400">Số dư hiện tại:</div>
                        <div className="text-xl font-black text-amber-400 font-mono tracking-tight">
                          {balDisplay}
                        </div>
                        <div className="text-[10px] text-slate-500 flex items-center justify-between pt-1">
                          <span>URL: {p.base_url}</span>
                          <span className="text-emerald-400 font-semibold">{p.status}</span>
                        </div>
                      </div>

                      <button
                        type="button"
                        onClick={() => handleCheckSingleBalance(p)}
                        disabled={checkingProviderId === p.id}
                        className="w-full flex items-center justify-center space-x-1.5 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-semibold transition-all disabled:opacity-50"
                      >
                        {checkingProviderId === p.id ? (
                          <Loader2 className="w-3.5 h-3.5 animate-spin text-amber-400" />
                        ) : (
                          <Activity className="w-3.5 h-3.5 text-emerald-400" />
                        )}
                        <span>{checkingProviderId === p.id ? 'Đang ping API...' : (p.provider_type === 'thmxh' ? 'Kiểm tra số dư USD ($)' : (p.provider_type === 'tuongtaccheo' ? 'Kiểm tra số xu (XU)' : 'Kiểm tra số dư'))}</span>
                      </button>
                    </div>
                  );
                })}
              </div>
            </div>

            {/* Live Order & Refill Status Inspector */}
            <div className="p-6 rounded-3xl bg-[#0D131F] border border-slate-800 shadow-xl space-y-5">
              <div>
                <h3 className="text-base font-bold text-white flex items-center space-x-2">
                  <Search className="w-5 h-5 text-amber-400" />
                  <span>Tra Cứu Trực Tiếp Đơn Hàng & Refill Từ API (Order Status Inspector)</span>
                </h3>
                <p className="text-xs text-slate-400 mt-1">
                  Nhập mã đơn hàng bên phía nhà cung cấp (Order ID) để kiểm tra trạng thái thực tế, số lượng còn lại (remains), số lượng ban đầu (start_count) hoặc gửi yêu cầu hủy/refill.
                </p>
              </div>

              <form onSubmit={handleQueryOrderStatus} className="space-y-4">
                <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                  <div>
                    <label className="block text-xs font-semibold text-slate-300 mb-1.5">
                      Chọn Nhà Cung Cấp API
                    </label>
                    <select
                      value={orderStatusProviderId || providers[0]?.id || ''}
                      onChange={(e) => setOrderStatusProviderId(Number(e.target.value))}
                      className="w-full px-3.5 py-2.5 rounded-xl bg-slate-900 border border-slate-700 text-white text-xs font-semibold focus:outline-none focus:border-amber-400"
                    >
                      {providers.map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.name} ({p.provider_type})
                        </option>
                      ))}
                    </select>
                  </div>

                  <div>
                    <label className="block text-xs font-semibold text-slate-300 mb-1.5">
                      Hành Động Cần Tra Cứu (Action)
                    </label>
                    <select
                      value={orderStatusAction}
                      onChange={(e: any) => setOrderStatusAction(e.target.value)}
                      className="w-full px-3.5 py-2.5 rounded-xl bg-slate-900 border border-slate-700 text-white text-xs font-semibold focus:outline-none focus:border-amber-400"
                    >
                      <option value="status">Tra cứu trạng thái đơn (action=status)</option>
                      <option value="refill_status">Tra cứu trạng thái Refill (action=refill_status)</option>
                      <option value="cancel">Gửi yêu cầu hủy đơn (action=cancel)</option>
                    </select>
                  </div>

                  <div>
                    <label className="block text-xs font-semibold text-slate-300 mb-1.5">
                      Mã Đơn Hàng (Order ID hoặc nhiều ID cách nhau bằng dấu phẩy)
                    </label>
                    <input
                      type="text"
                      required
                      placeholder="VD: 319301 hoặc 319301, 23501, 100"
                      value={orderStatusInput}
                      onChange={(e) => setOrderStatusInput(e.target.value)}
                      className="w-full px-3.5 py-2.5 rounded-xl bg-slate-900 border border-slate-700 text-white text-xs font-mono placeholder-slate-500 focus:outline-none focus:border-amber-400"
                    />
                  </div>
                </div>

                <div className="flex items-center justify-end">
                  <button
                    type="submit"
                    disabled={checkingOrderStatus}
                    className="flex items-center space-x-2 px-6 py-2.5 rounded-xl bg-amber-500 hover:bg-amber-400 disabled:opacity-50 text-slate-950 font-bold text-xs shadow-lg shadow-amber-500/20 transition-all active:scale-95"
                  >
                    {checkingOrderStatus ? (
                      <>
                        <Loader2 className="w-4 h-4 animate-spin" />
                        <span>Đang gọi API Provider...</span>
                      </>
                    ) : (
                      <>
                        <Search className="w-4 h-4" />
                        <span>Tra Cứu Từ API Ngay</span>
                      </>
                    )}
                  </button>
                </div>
              </form>

              {/* Display Result */}
              {orderStatusResult && (
                <div className="p-5 rounded-2xl bg-slate-900 border border-slate-800 space-y-3">
                  <div className="flex items-center justify-between border-b border-slate-800 pb-3">
                    <span className="font-bold text-xs text-white uppercase tracking-wider flex items-center space-x-2">
                      <CheckCircle2 className="w-4 h-4 text-emerald-400" />
                      <span>Kết Quả Trả Về Từ API Provider</span>
                    </span>
                    <span className="text-[11px] text-slate-400 font-mono">
                      Mã đơn kiểm tra: <strong className="text-amber-400">#{orderStatusInput}</strong>
                    </span>
                  </div>

                  {orderStatusResult.error ? (
                    <div className="p-3.5 rounded-xl bg-rose-500/10 border border-rose-500/20 text-rose-400 text-xs">
                      Lỗi: {orderStatusResult.error}
                    </div>
                  ) : typeof orderStatusResult === 'object' && orderStatusResult.status ? (
                    <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
                      <div className="p-3 rounded-xl bg-slate-950/80 border border-slate-800">
                        <span className="text-[11px] text-slate-400 block mb-1">Trạng thái:</span>
                        <span className={`px-2 py-0.5 rounded text-[11px] font-bold ${
                          orderStatusResult.status === 'COMPLETED' ? 'bg-emerald-500/10 text-emerald-400' :
                          orderStatusResult.status === 'PROCESSING' || orderStatusResult.status === 'In progress' ? 'bg-blue-500/10 text-blue-400' :
                          orderStatusResult.status === 'PENDING' ? 'bg-amber-500/10 text-amber-400' :
                          'bg-slate-800 text-slate-300'
                        }`}>
                          {orderStatusResult.status}
                        </span>
                      </div>

                      <div className="p-3 rounded-xl bg-slate-950/80 border border-slate-800">
                        <span className="text-[11px] text-slate-400 block mb-1">Số lượng ban đầu:</span>
                        <span className="font-mono font-bold text-white text-sm">
                          {orderStatusResult.start_count ?? '—'}
                        </span>
                      </div>

                      <div className="p-3 rounded-xl bg-slate-950/80 border border-slate-800">
                        <span className="text-[11px] text-slate-400 block mb-1">Số lượng còn lại:</span>
                        <span className="font-mono font-bold text-amber-400 text-sm">
                          {orderStatusResult.remains ?? '—'}
                        </span>
                      </div>

                      <div className="p-3 rounded-xl bg-slate-950/80 border border-slate-800">
                        <span className="text-[11px] text-slate-400 block mb-1">Tiền tệ:</span>
                        <span className="font-mono font-bold text-emerald-400 text-sm">
                          {orderStatusResult.currency || 'USD'}
                        </span>
                      </div>
                    </div>
                  ) : null}

                  <div className="pt-2">
                    <span className="text-[10px] text-slate-500 block mb-1">Dữ liệu thô (Raw JSON):</span>
                    <pre className="p-3 rounded-xl bg-slate-950 border border-slate-800 font-mono text-[11px] text-emerald-400 overflow-x-auto">
                      {JSON.stringify(orderStatusResult, null, 2)}
                    </pre>
                  </div>
                </div>
              )}
            </div>
          </div>
        )}
"""
        code = code[:insert_pos] + tab5_content + "\n        " + code[insert_pos:]
        with open("frontend/src/app/admin/providers/page.tsx", "w", encoding="utf-8") as f:
            f.write(code)
        print("Tab 5 added successfully!")
    else:
        print("showProviderModal not found")
else:
    print("Tab 5 already in file")
