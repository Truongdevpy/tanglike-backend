/**
 * run_wasm.js  — Zero-dependency WASM runner for MBBank login encryption.
 *
 * This script embeds the full Go/WASM runtime (from LoadWasm.ts) inline.
 * It requires NO npm packages — only built-in Node.js modules.
 *
 * Usage (called by mb_login.py via subprocess):
 *   node run_wasm.js <wasm_file_path> <payload_json> [arg1="0"]
 *
 * Outputs one JSON line to stdout:
 *   {"ok":true,"dataEnc":"..."}   on success
 *   {"ok":false,"error":"..."}    on failure
 */

'use strict';

const fs = require('fs');

if (!globalThis.crypto) {
    const crypto = require('crypto');
    globalThis.crypto = crypto.webcrypto || {
        getRandomValues: function(buffer) {
            return crypto.randomFillSync(buffer);
        }
    };
}

if (!globalThis.performance) {
    globalThis.performance = require('perf_hooks').performance;
}

// ── polyfills needed by Go WASM runtime ──────────────────────────────────────
// TextEncoder / TextDecoder are built-in since Node 11

const window = { globalThis, document: { welovemb: true } };
globalThis.window   = window;
try {
    globalThis.location = new URL('https://online.mbbank.com.vn/pl/login');
} catch (_) {
    globalThis.location = { href: 'https://online.mbbank.com.vn/pl/login' };
}

// ── minimal fs shim for Go runtime ───────────────────────────────────────────
if (!globalThis.fs) {
    let data = '';
    globalThis.fs = {
        constants: { O_WRONLY: -1, O_RDWR: -1, O_CREAT: -1, O_TRUNC: -1, O_APPEND: -1, O_EXCL: -1 },
        writeSync(fd, buffer) {
            data += new TextDecoder('utf-8').decode(buffer);
            const idx = data.lastIndexOf('\n');
            if (idx !== -1) { data = data.substring(idx + 1); }
            return buffer.length;
        },
        write(fd, buffer, offset, length, position, callback) {
            if (offset === 0 && length === buffer.length && position === null)
                callback(null, this.writeSync(fd, buffer));
            else callback(new Error('not implemented'));
        },
        fsync(fd, callback) { callback(null); },
    };
}

// ── processAsync helper (from LoadWasm.ts) ────────────────────────────────────
const processAsync = (p1, p2, gen) =>
    new Promise((resolve, reject) => {
        const ok  = res => { try { step(gen.next(res)); } catch (e) { reject(e); } };
        const err = e   => { try { step(gen.throw(e)); } catch (e2) { reject(e2); } };
        const step = s  => s.done ? resolve(s.value) : Promise.resolve(s.value).then(ok, err);
        step((gen = gen.apply(p1, p2)).next());
    });

// ── Go runtime (ported from LoadWasm.ts, no external deps) ───────────────────
const TextEncoderU = new TextEncoder();
const TextDecoderU = new TextDecoder('utf-8');

globalThis.Go = class {
    constructor() {
        this.argv = ['js'];
        this.env  = {};
        this.exit = code => { if (code !== 0) console.warn('exit code:', code); };
        this._exitPromise = new Promise(r => (this._resolveExitPromise = r));
        this._pendingEvent = null;
        this._scheduledTimeouts = new Map();
        this._nextCallbackTimeoutID = 1;

        const setMem = (addr, v) => {
            this.mem.setUint32(addr,     v,                                true);
            this.mem.setUint32(addr + 4, Math.floor(v / 0x100000000),     true);
        };
        const getVal = addr => {
            const f = this.mem.getFloat64(addr, true);
            if (f === 0) return undefined;
            if (!isNaN(f)) return f;
            return this._values[this.mem.getUint32(addr, true)];
        };
        const setVal = (addr, v) => {
            if (typeof v === 'number' && v !== 0) {
                if (isNaN(v)) { this.mem.setUint32(addr+4,0x7ff80000,true); this.mem.setUint32(addr,0,true); }
                else this.mem.setFloat64(addr, v, true);
                return;
            }
            if (v === undefined) { this.mem.setFloat64(addr, 0, true); return; }
            let id = this._ids.get(v);
            if (id === undefined) {
                id = this._idPool.pop() ?? this._values.length;
                this._values[id] = v; this._goRefCounts[id] = 0; this._ids.set(v, id);
            }
            this._goRefCounts[id]++;
            let flag = 0;
            if (typeof v === 'object' && v !== null) flag = 1;
            else if (typeof v === 'string') flag = 2;
            else if (typeof v === 'symbol') flag = 3;
            else if (typeof v === 'function') flag = 4;
            this.mem.setUint32(addr+4, 0x7ff80000|flag, true);
            this.mem.setUint32(addr, id, true);
        };
        const getBytes = addr => {
            const start = this.mem.getUint32(addr,true)   + 0x100000000*this.mem.getInt32(addr+4,true);
            const len   = this.mem.getUint32(addr+8,true) + 0x100000000*this.mem.getInt32(addr+12,true);
            return new Uint8Array(this._inst.exports.mem.buffer, start, len);
        };
        const getArr = addr => {
            const start = this.mem.getUint32(addr,true)   + 0x100000000*this.mem.getInt32(addr+4,true);
            const len   = this.mem.getUint32(addr+8,true) + 0x100000000*this.mem.getInt32(addr+12,true);
            const arr = new Array(len);
            for (let i = 0; i < len; i++) arr[i] = getVal(start + 8*i);
            return arr;
        };
        const getStr = addr => {
            const start = this.mem.getUint32(addr,true)   + 0x100000000*this.mem.getInt32(addr+4,true);
            const len   = this.mem.getUint32(addr+8,true) + 0x100000000*this.mem.getInt32(addr+12,true);
            return TextDecoderU.decode(new DataView(this._inst.exports.mem.buffer, start, len));
        };

        const td = Date.now() - performance.now();

        this.importObject = {
            _gotest: { add: (a,b) => a+b },
            gojs: {
                'runtime.wasmExit': sp => {
                    const code = this.mem.getInt32(8+(sp>>>=0), true);
                    this.exited = true;
                    delete this._inst; delete this._values; delete this._goRefCounts;
                    delete this._ids; delete this._idPool;
                    this.exit(code);
                },
                'runtime.wasmWrite': addr => {
                    addr >>>=0;
                    const fd   = this.mem.getUint32(8+addr,true) + 0x100000000*this.mem.getInt32(8+addr+4,true);
                    const dat  = this.mem.getUint32(addr+16,true) + 0x100000000*this.mem.getInt32(addr+20,true);
                    const size = this.mem.getInt32(addr+24, true);
                    fs.writeSync(fd, new Uint8Array(this._inst.exports.mem.buffer, dat, size));
                },
                'runtime.resetMemoryDataView': () => {
                    this.mem = new DataView(this._inst.exports.mem.buffer);
                },
                'runtime.nanotime1': sp => { setMem(8+(sp>>>=0), 1e6*(td+performance.now())); },
                'runtime.walltime': sp => {
                    sp >>>=0;
                    const ms = Date.now();
                    setMem(sp+8, ms/1e3);
                    this.mem.setInt32(sp+16, (ms%1000)*1e6, true);
                },
                'runtime.scheduleTimeoutEvent': sp => {
                    sp>>>=0;
                    const id = this._nextCallbackTimeoutID++;
                    this._scheduledTimeouts.set(id, setTimeout(() => {
                        for (this._resume(); this._scheduledTimeouts.has(id);) this._resume();
                    }, this.mem.getUint32(sp+8,true) + 0x100000000*this.mem.getInt32(sp+12,true)));
                    this.mem.setInt32(sp+16, id, true);
                },
                'runtime.clearTimeoutEvent': sp => {
                    clearTimeout(this._scheduledTimeouts.get(this.mem.getInt32(8+(sp>>>=0),true)));
                    this._scheduledTimeouts.delete(this.mem.getInt32(8+sp,true));
                },
                'runtime.getRandomData': sp => { globalThis.crypto.getRandomValues(getBytes(8+(sp>>>=0))); },
                'syscall/js.finalizeRef': sp => {
                    const id = this.mem.getUint32(8+(sp>>>=0),true);
                    if (--this._goRefCounts[id] === 0) {
                        const v = this._values[id];
                        this._values[id] = null; this._ids.delete(v); this._idPool.push(id);
                    }
                },
                'syscall/js.stringVal': sp => { setVal(24+(sp>>>=0), getStr(sp+8)); },
                'syscall/js.valueGet': sp => {
                    sp>>>=0;
                    const r = Reflect.get(getVal(sp+8), getStr(sp+16));
                    sp = this._inst.exports.getsp()>>>0;
                    setVal(sp+32, r);
                },
                'syscall/js.valueSet': sp => {
                    sp>>>=0; Reflect.set(getVal(sp+8), getStr(sp+16), getVal(sp+32));
                },
                'syscall/js.valueDelete': sp => {
                    sp>>>=0; Reflect.deleteProperty(getVal(sp+8), getStr(sp+16));
                },
                'syscall/js.valueIndex': sp => {
                    setVal(24+(sp>>>=0), Reflect.get(getVal(sp+8), this.mem.getUint32(sp+16,true)+0x100000000*this.mem.getInt32(sp+20,true)));
                },
                'syscall/js.valueSetIndex': sp => {
                    sp>>>=0; Reflect.set(getVal(sp+8), this.mem.getUint32(sp+16,true)+0x100000000*this.mem.getInt32(sp+20,true), getVal(sp+24));
                },
                'syscall/js.valueCall': sp => {
                    sp>>>=0;
                    try {
                        const obj  = getVal(sp+8);
                        const fn   = Reflect.get(obj, getStr(sp+16));
                        const args = getArr(sp+32);
                        const ret  = Reflect.apply(fn, obj, args);
                        sp = this._inst.exports.getsp()>>>0;
                        setVal(sp+56, ret); this.mem.setUint8(sp+64, 1);
                    } catch(e) {
                        sp = this._inst.exports.getsp()>>>0;
                        setVal(sp+56, e); this.mem.setUint8(sp+64, 0);
                    }
                },
                'syscall/js.valueInvoke': sp => {
                    sp>>>=0;
                    try {
                        const fn   = getVal(sp+8);
                        const args = getArr(sp+16);
                        const ret  = Reflect.apply(fn, undefined, args);
                        sp = this._inst.exports.getsp()>>>0;
                        setVal(sp+40, ret); this.mem.setUint8(sp+48, 1);
                    } catch(e) {
                        sp = this._inst.exports.getsp()>>>0;
                        setVal(sp+40, e); this.mem.setUint8(sp+48, 0);
                    }
                },
                'syscall/js.valueNew': sp => {
                    sp>>>=0;
                    try {
                        const ctor = getVal(sp+8);
                        const args = getArr(sp+16);
                        const obj  = Reflect.construct(ctor, args);
                        sp = this._inst.exports.getsp()>>>0;
                        setVal(sp+40, obj); this.mem.setUint8(sp+48, 1);
                    } catch(e) {
                        sp = this._inst.exports.getsp()>>>0;
                        setVal(sp+40, e); this.mem.setUint8(sp+48, 0);
                    }
                },
                'syscall/js.valueLength': sp => { setMem(16+(sp>>>=0), parseInt(getVal(sp+8).length)); },
                'syscall/js.valuePrepareString': sp => {
                    sp>>>=0;
                    const enc = TextEncoderU.encode(String(getVal(sp+8)));
                    setVal(sp+16, enc); setMem(sp+24, enc.length);
                },
                'syscall/js.valueLoadString': sp => {
                    getBytes(16+(sp>>>=0)).set(getVal(8+sp));
                },
                'syscall/js.valueInstanceOf': sp => {
                    this.mem.setUint8(24+(sp>>>=0), getVal(sp+8) instanceof getVal(sp+16) ? 1 : 0);
                },
                'syscall/js.copyBytesToGo': sp => {
                    const dst = getBytes(8+(sp>>>=0));
                    const src = getVal(sp+32);
                    if (!(src instanceof Uint8Array||src instanceof Uint8ClampedArray)) { this.mem.setUint8(sp+48,0); return; }
                    const n = src.subarray(0, dst.length);
                    dst.set(n); setMem(sp+40, n.length); this.mem.setUint8(sp+48,1);
                },
                'syscall/js.copyBytesToJS': sp => {
                    const dst = getVal(8+(sp>>>=0));
                    const src = getBytes(sp+16);
                    if (!(dst instanceof Uint8Array||dst instanceof Uint8ClampedArray)) { this.mem.setUint8(sp+48,0); return; }
                    const n = src.subarray(0, dst.length);
                    dst.set(n); setMem(sp+40, n.length); this.mem.setUint8(sp+48,1);
                },
                debug: v => console.log(v),
            },
        };
    }

    run(inst) {
        return processAsync(this, null, function*() {
            this._inst = inst;
            this.mem = new DataView(this._inst.exports.mem.buffer);
            this._values = [NaN, 0, null, true, false, globalThis, this];
            this._goRefCounts = new Array(this._values.length).fill(Infinity);
            this._ids = new Map([[0,1],[null,2],[true,3],[false,4],[globalThis,5],[this,6]]);
            this._idPool = [];
            this.exited = false;

            let offset = 0x1000;
            const strPtr = s => {
                const p = offset;
                const enc = TextEncoderU.encode(s + '\0');
                new Uint8Array(this.mem.buffer, offset, enc.length).set(enc);
                offset += enc.length;
                if (offset % 8) offset += 8 - (offset % 8);
                return p;
            };

            const argc  = this.argv.length;
            const ptrs  = [];
            this.argv.forEach(a => ptrs.push(strPtr(a)));
            ptrs.push(0);
            Object.keys(this.env).sort().forEach(k => ptrs.push(strPtr(k+'='+this.env[k])));
            ptrs.push(0);

            const argvPtr = offset;
            ptrs.forEach(p => {
                this.mem.setUint32(offset,p,true); this.mem.setUint32(offset+4,0,true); offset+=8;
            });

            this._inst.exports.run(argc, argvPtr);
            if (this.exited) this._resolveExitPromise();
            yield this._exitPromise;
        });
    }

    _resume() {
        if (this.exited) throw new Error('Go program has already exited');
        this._inst.exports.resume();
        if (this.exited) this._resolveExitPromise();
    }

    _makeFuncWrapper(id) {
        const self = this;
        return function() {
            const ev = { id, this: this, args: arguments };
            self._pendingEvent = ev;
            self._resume();
            return ev.result;
        };
    }
};

// ── main ──────────────────────────────────────────────────────────────────────
(async () => {
    const wasmPath   = process.argv[2];
    const payloadStr = process.argv[3];
    const arg1       = process.argv[4] || '0';

    if (!wasmPath || !payloadStr) {
        process.stdout.write(JSON.stringify({ ok: false, error: 'Usage: node run_wasm.js <wasm_path> <payload_json> [arg1]' }));
        process.exit(1);
    }

    try {
        const wasmBytes = fs.readFileSync(wasmPath);
        const payload   = JSON.parse(payloadStr);

        const go = new Go();
        const { instance } = await WebAssembly.instantiate(wasmBytes, go.importObject);
        go.run(instance); // Don't await — bder() is sync once the module is running

        // Give the Go runtime a tick to register globalThis.bder
        await new Promise(r => setTimeout(r, 50));

        if (typeof globalThis.bder !== 'function') {
            process.stdout.write(JSON.stringify({ ok: false, error: 'globalThis.bder not found after WASM init' }));
            process.exit(1);
        }

        const dataEnc = globalThis.bder(JSON.stringify(payload), arg1);
        process.stdout.write(JSON.stringify({ ok: true, dataEnc }));
    } catch(e) {
        process.stdout.write(JSON.stringify({ ok: false, error: e.message || String(e) }));
        process.exit(1);
    }
})();
