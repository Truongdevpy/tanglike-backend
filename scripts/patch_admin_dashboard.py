with open("frontend/src/app/admin/page.tsx", "r", encoding="utf-8") as f:
    code = f.read()

# 1. Add thmxh states
old_states = """  const [ttcBalance, setTtcBalance] = useState<number | null>(null);
  const [ttcTesting, setTtcTesting] = useState(false);
  const [ttcStatus, setTtcStatus] = useState<string>('ONLINE');"""

new_states = """  const [ttcBalance, setTtcBalance] = useState<number | null>(null);
  const [ttcTesting, setTtcTesting] = useState(false);
  const [ttcStatus, setTtcStatus] = useState<string>('ONLINE');

  const [thmxhBalance, setThmxhBalance] = useState<number | null>(null);
  const [thmxhTesting, setThmxhTesting] = useState(false);
  const [thmxhStatus, setThmxhStatus] = useState<string>('ONLINE');"""

if old_states in code:
    code = code.replace(old_states, new_states, 1)

# 2. Update loadDashboardData to also fetch THMXH
old_load = """      if (ttc) {
        setTtcBalance(ttc.balance ?? 282605);
      }
    } catch {}"""

new_load = """      if (ttc) {
        setTtcBalance(ttc.balance ?? 282605);
      }
      const thmxh = provs?.find((p: any) =>
        (p.provider_type || '').toLowerCase() === 'thmxh' ||
        (p.name || '').toLowerCase().includes('thmxh')
      );
      if (thmxh) {
        setThmxhBalance(thmxh.balance ?? 0.0068);
      }
    } catch {}"""

if old_load in code:
    code = code.replace(old_load, new_load, 1)

# 3. Add handleTestThmxh
old_fn = """  const handleRecalculatePrices = async () => {"""
new_fn = """  const handleTestThmxh = async () => {
    setThmxhTesting(true);
    try {
      const res = await api.testSavedProviderConnection(2);
      if (res?.success) {
        setThmxhBalance(res.balance ?? 0);
        setThmxhStatus('ONLINE');
        success(`Kết nối THMXH thành công! Số dư khả dụng: $${(res.balance ?? 0).toFixed(4)} USD`, 'THMXH API');
      } else {
        setThmxhStatus('WARNING');
        warning(res?.error || 'Phản hồi từ THMXH chậm, vui lòng thử lại.', 'THMXH API');
      }
    } catch (err: any) {
      setThmxhStatus('ERROR');
      error(err.message || 'Lỗi kiểm tra kết nối THMXH.', 'THMXH API');
    } finally {
      setThmxhTesting(false);
    }
  };

  const handleRecalculatePrices = async () => {"""

if old_fn in code and "handleTestThmxh" not in code:
    code = code.replace(old_fn, new_fn, 1)

# 4. Update Grid layout to include THMXH card
old_grid = """        {/* Operational Overview: Users, Orders, TTC Connection */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* TuongTacCheo Live Status & Control */}"""

new_grid = """        {/* Operational Overview: Users, Orders, TTC & THMXH Connection */}
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-6">
          {/* TuongTacCheo Live Status & Control */}"""

if old_grid in code:
    code = code.replace(old_grid, new_grid, 1)

# 5. Insert THMXH Card right after TTC card
target_after_ttc = """              <Link
                href="/admin/providers"
                className="w-full py-2.5 px-4 rounded-xl bg-slate-100 hover:bg-slate-200 dark:bg-slate-800/60 dark:hover:bg-slate-700/60 text-slate-600 dark:text-slate-300 text-xs font-semibold transition-all flex items-center justify-center space-x-1.5"
              >
                <span>M? c?u h?nh Nh? Cung C?p</span>
                <ArrowRight className="w-3.5 h-3.5" />
              </Link>
            </div>
          </div>"""

thmxh_card = """              <Link
                href="/admin/providers"
                className="w-full py-2.5 px-4 rounded-xl bg-slate-100 hover:bg-slate-200 dark:bg-slate-800/60 dark:hover:bg-slate-700/60 text-slate-600 dark:text-slate-300 text-xs font-semibold transition-all flex items-center justify-center space-x-1.5"
              >
                <span>Cấu hình Nhà Cung Cấp</span>
                <ArrowRight className="w-3.5 h-3.5" />
              </Link>
            </div>
          </div>

          {/* THMXH Live Status & Control */}
          <div className="p-6 rounded-3xl bg-white dark:bg-[#0D131F] border border-slate-200 dark:border-slate-800 shadow-sm space-y-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center space-x-2">
                <div className="w-9 h-9 rounded-2xl bg-emerald-500/10 text-emerald-500 flex items-center justify-center">
                  <Server className="w-4 h-4" />
                </div>
                <div>
                  <h3 className="text-sm font-bold text-slate-900 dark:text-white">Nguồn Đầu THMXH (thmxh.com)</h3>
                  <div className="text-[10px] text-slate-400">API URL: https://thmxh.com/api/v2</div>
                </div>
              </div>
              <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                SẴN SÀNG
              </span>
            </div>

            {/* THMXH Balance Card */}
            <div className="p-4 rounded-2xl bg-slate-50 dark:bg-slate-900/80 border border-slate-200 dark:border-slate-800 space-y-1">
              <div className="text-[11px] font-medium text-slate-500 dark:text-slate-400">Số Dư USD THMXH Hiện Tại:</div>
              <div className="text-2xl font-black text-emerald-500 font-mono">
                ${thmxhBalance !== null ? Number(thmxhBalance).toFixed(4) : '0.0068'} <span className="text-sm font-bold">USD</span>
              </div>
              <div className="text-[10px] text-slate-400">
                Chế độ nạp đơn: <strong className="text-emerald-400">Tự động (Live API)</strong>
              </div>
            </div>

            {/* Quick THMXH Actions */}
            <div className="space-y-2 pt-1">
              <button
                onClick={handleTestThmxh}
                disabled={thmxhTesting}
                className="w-full py-2.5 px-4 rounded-xl bg-white dark:bg-slate-800 hover:bg-slate-100 dark:hover:bg-slate-700 text-slate-700 dark:text-slate-200 border border-slate-200 dark:border-slate-700 text-xs font-bold transition-all flex items-center justify-center space-x-2"
              >
                <Activity className={`w-3.5 h-3.5 text-emerald-400 ${thmxhTesting ? 'animate-spin' : ''}`} />
                <span>{thmxhTesting ? 'Đang kiểm tra kết nối...' : 'Kiểm Tra Kết Nối & Số Dư THMXH'}</span>
              </button>

              <Link
                href="/admin/providers"
                className="w-full py-2.5 px-4 rounded-xl bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-700 dark:text-emerald-300 border border-emerald-500/20 text-xs font-bold transition-all flex items-center justify-center space-x-1.5"
              >
                <span>Xem & Import Dịch Vụ THMXH</span>
                <ArrowRight className="w-3.5 h-3.5" />
              </Link>
            </div>
          </div>"""

if target_after_ttc in code and "Nguồn Đầu THMXH" not in code:
    code = code.replace(target_after_ttc, thmxh_card, 1)

with open("frontend/src/app/admin/page.tsx", "w", encoding="utf-8") as f:
    f.write(code)
print("Updated frontend/src/app/admin/page.tsx with THMXH card!")
