import argparse
import base64
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import requests

BASE_URL = "https://online.mbbank.com.vn"
WASM_URL = f"{BASE_URL}/assets/wasm/main.wasm"
SCRIPT_DIR = Path(__file__).parent
WASM_CACHE = SCRIPT_DIR / "main.wasm"
DEVICE_CACHE = SCRIPT_DIR / "mb_device.json"
OCR_MODEL_PATH = Path(os.getenv("MB_CAPTCHA_MODEL_PATH", SCRIPT_DIR / "model.onnx"))
RUN_WASM_JS_PATH = SCRIPT_DIR / "run_wasm.js"
AUTHORIZATION = "Basic RU1CUkVUQUlMV0VCOlNEMjM0ZGZnMzQlI0BGR0AzNHNmc2RmNDU4NDNm"
DEFAULT_TIMEOUT = 30
FPR = "c7a1beebb9400375bb187daa33de9659"
OCR_CHARSET = sorted(
    [str(i) for i in range(10)]
    + [chr(i) for i in range(97, 123)]
    + [chr(i) for i in range(65, 91)]
)
_OCR_SESSION = None

DEFAULT_HEADERS = {
    "Cache-Control": "max-age=0",
    "Accept": "application/json, text/plain, */*",
    "Authorization": AUTHORIZATION,
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/134.0.0.0 Safari/537.36"
    ),
    "Origin": BASE_URL,
    "Referer": f"{BASE_URL}/pl/login?returnUrl=%2F",
    "Content-Type": "application/json; charset=UTF-8",
    "app": "MB_WEB",
    "elastic-apm-traceparent": "00-55b950e3fcabc785fa6db4d7deb5ef73-8dbd60b04eda2f34-01",
    "Sec-Ch-Ua": '"Not.A/Brand";v="8", "Chromium";v="134", "Google Chrome";v="134"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}


def _new_session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    return session


def _default_from_date() -> str:
    return (datetime.now() - timedelta(days=7)).strftime("%d/%m/%Y")


def _default_to_date() -> str:
    return datetime.now().strftime("%d/%m/%Y")


def _time_now_short() -> str:
    now = datetime.now()
    millis = str(now.microsecond // 1000)
    return now.strftime("%Y%m%d%H%M%S") + millis[:-1]


def _time_now_long() -> str:
    now = datetime.now()
    millis = str(now.microsecond // 1000).zfill(3)
    return now.strftime("%Y%m%d%H%M%S") + millis


def _generate_device_id() -> str:
    return "s1rmi184-mbib-0000-0000-" + _time_now_short()


def _md5(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


def _load_device_id(username: str) -> str:
    if DEVICE_CACHE.exists():
        try:
            data = json.loads(DEVICE_CACHE.read_text(encoding="utf-8"))
            if username in data:
                device_id = data[username]
                print(f"[*] Using saved deviceId: {device_id}")
                return device_id
        except Exception:
            pass

    device_id = _generate_device_id()
    _save_device_id(username, device_id)
    print(f"[*] Generated new deviceId: {device_id}")
    return device_id


def _save_device_id(username: str, device_id: str) -> None:
    data: dict[str, str] = {}
    if DEVICE_CACHE.exists():
        try:
            data = json.loads(DEVICE_CACHE.read_text(encoding="utf-8"))
        except Exception:
            pass
    data[username] = device_id
    DEVICE_CACHE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _download_wasm() -> bytes:
    if WASM_CACHE.exists():
        print(f"[*] Using cached WASM from {WASM_CACHE}")
        return WASM_CACHE.read_bytes()

    print("[*] Downloading main.wasm from MBBank...")
    with _new_session() as session:
        response = session.get(WASM_URL, headers=DEFAULT_HEADERS, timeout=30)
    response.raise_for_status()
    WASM_CACHE.write_bytes(response.content)
    print(f"[*] WASM saved to {WASM_CACHE}")
    return response.content


def _get_node_bin() -> str:
    node_bin = os.getenv("NODE_BIN")
    if node_bin and os.path.exists(node_bin):
        return node_bin
    
    nvm_dir = Path.home() / ".nvm" / "versions" / "node"
    if nvm_dir.exists():
        versions = sorted([d for d in nvm_dir.iterdir() if d.is_dir()], reverse=True)
        if versions:
            nvm_node = versions[0] / "bin" / "node"
            if nvm_node.exists():
                return str(nvm_node)
    
    return "node"


def _wasm_encrypt_via_node(wasm_bytes: bytes, payload: dict, arg1: str = "0") -> str:
    if not RUN_WASM_JS_PATH.exists():
        raise FileNotFoundError(f"Missing {RUN_WASM_JS_PATH}")

    payload_json = json.dumps(payload, ensure_ascii=False)

    with tempfile.NamedTemporaryFile(
        suffix=".wasm",
        delete=False,
        dir=str(SCRIPT_DIR),
    ) as wasm_file:
        wasm_file.write(wasm_bytes)
        wasm_tmp = wasm_file.name

    try:
        node_bin = _get_node_bin()
        result = subprocess.run(
            [node_bin, str(RUN_WASM_JS_PATH), wasm_tmp, payload_json, arg1],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(SCRIPT_DIR),
        )
        lines = [line for line in result.stdout.strip().splitlines() if line.strip()]
        if not lines:
            stderr = result.stderr.strip()
            raise RuntimeError(f"node run_wasm.js produced no output. stderr: {stderr}")

        data = json.loads(lines[-1])
        if not data.get("ok"):
            raise RuntimeError(f"WASM encryption failed: {data.get('error')}")
        return data["dataEnc"]
    finally:
        try:
            os.unlink(wasm_tmp)
        except OSError:
            pass


def _get_captcha(session: requests.Session, device_id: str) -> Tuple[str, str]:
    ref_no = _time_now_short()
    body = {
        "sessionId": "",
        "refNo": ref_no,
        "deviceIdCommon": device_id,
    }
    headers = dict(DEFAULT_HEADERS)
    headers["X-Request-Id"] = ref_no
    headers["Deviceid"] = device_id
    headers["Refno"] = ref_no

    response = session.post(
        f"{BASE_URL}/api/retail-internetbankingms/getCaptchaImage",
        headers=headers,
        json=body,
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()

    image_b64 = payload.get("imageString", "")
    captcha_path = SCRIPT_DIR / "captcha.png"
    captcha_path.write_bytes(base64.b64decode(image_b64))
    print(f"[*] Captcha saved to {captcha_path}")
    return image_b64, ref_no


def _get_ocr_session():
    global _OCR_SESSION
    if _OCR_SESSION is not None:
        return _OCR_SESSION

    if not OCR_MODEL_PATH.exists():
        return None

    try:
        import onnxruntime as ort
    except ImportError:
        return None

    _OCR_SESSION = ort.InferenceSession(str(OCR_MODEL_PATH))
    return _OCR_SESSION


def _solve_captcha_onnx(image_b64: str) -> str | None:
    session = _get_ocr_session()
    if session is None:
        return None

    try:
        import io

        import numpy as np
        from PIL import Image

        image = Image.open(io.BytesIO(base64.b64decode(image_b64))).convert("L")
        if hasattr(Image, "Resampling"):
            image = image.resize((160, 50), Image.Resampling.LANCZOS)
        else:
            image = image.resize((160, 50))

        pixels = np.asarray(image, dtype=np.float32) / 255.0
        tensor = pixels.reshape(1, 1, 50, 160)

        input_name = session.get_inputs()[0].name
        output = session.run(None, {input_name: tensor})[0]
        logits = np.asarray(output)
        if logits.ndim != 3 or logits.shape[0] != 1:
            return None

        token_ids = np.argmax(logits[0], axis=1)
        text = "".join(
            OCR_CHARSET[int(token_id)]
            for token_id in token_ids
            if 0 <= int(token_id) < len(OCR_CHARSET)
        )
        text = "".join(char for char in text if char.isalnum())
        if len(text) == 6:
            return text
    except Exception:
        return None

    return None


def _solve_captcha_auto(image_b64: str) -> str | None:
    onnx_text = _solve_captcha_onnx(image_b64)
    if onnx_text:
        return onnx_text

    try:
        import io

        from PIL import Image
        import pytesseract

        image = Image.open(io.BytesIO(base64.b64decode(image_b64))).convert("L")
        text = pytesseract.image_to_string(
            image,
            config="--psm 8 -c tessedit_char_whitelist=0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ",
        ).strip().upper()
        text = "".join(char for char in text if char.isalnum())
        if len(text) == 6:
            return text
    except Exception:
        pass
    return None


def _solve_captcha(image_b64: str) -> str:
    auto = _solve_captcha_auto(image_b64)
    if auto:
        print(f"[*] Auto-solved captcha: {auto}")
        return auto

    env_captcha = os.getenv("MB_CAPTCHA", "").strip().upper()
    if env_captcha:
        print("[*] Using captcha from MB_CAPTCHA")
        return env_captcha

    captcha_path = SCRIPT_DIR / "captcha.png"
    try:
        from PIL import Image

        Image.open(captcha_path).show()
    except Exception:
        pass

    if not sys.stdin or not sys.stdin.isatty():
        raise RuntimeError(
            f"Cannot read captcha interactively in this environment. "
            f"Open {captcha_path}, then set MB_CAPTCHA or run the script in a terminal."
        )

    try:
        return input("[?] Enter captcha (6 chars): ").strip().upper()
    except EOFError as exc:
        raise RuntimeError(
            "Could not read captcha from stdin. Run the script in a terminal or set MB_CAPTCHA."
        ) from exc


def mb_login(username: str, password: str, max_retries: int = 5) -> Tuple[str, str]:
    wasm_bytes = _download_wasm()
    device_id = _load_device_id(username)
    session = _new_session()
    manual_captcha = bool(os.getenv("MB_CAPTCHA", "").strip())

    for attempt in range(1, max_retries + 1):
        print(f"\n[*] Login attempt #{attempt}...")

        image_b64, _ = _get_captcha(session, device_id)
        captcha = _solve_captcha(image_b64)
        if not captcha or len(captcha) != 6:
            print("[-] Invalid captcha, retrying...")
            continue

        ref_no = f"{username}-{_time_now_short()}"
        request_data = {
            "userId": username,
            "password": _md5(password),
            "captcha": captcha,
            "ibAuthen2faString": FPR,
            "sessionId": None,
            "refNo": ref_no,
            "deviceIdCommon": device_id,
        }

        print("[*] Encrypting login payload via MB WASM...")
        data_enc = _wasm_encrypt_via_node(wasm_bytes, request_data, "0")

        headers = dict(DEFAULT_HEADERS)
        headers["X-Request-Id"] = ref_no
        headers["Deviceid"] = device_id
        headers["Refno"] = ref_no

        response = session.post(
            f"{BASE_URL}/api/retail_web/internetbanking/v2.0/doLogin",
            headers=headers,
            json={"dataEnc": data_enc},
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()

        if not payload.get("result"):
            raise RuntimeError(f"Login response missing result: {payload}")

        result = payload["result"]
        if result.get("ok"):
            session_id = payload.get("sessionId")
            print(f"[+] Login successful. sessionId={session_id}")
            return session_id, device_id

        code = result.get("responseCode", "")
        message = result.get("message", "")
        if code == "GW283":
            if manual_captcha:
                raise RuntimeError(
                    "Captcha from --captcha/MB_CAPTCHA was rejected. "
                    "Open captcha.png, read the latest captcha, then run again."
                )
            print("[-] Captcha rejected by MB, retrying...")
            continue

        if code == "GW266":
            print("[!] MB requires OTP confirmation for this device.")
            if not sys.stdin or not sys.stdin.isatty():
                raise RuntimeError("Tài khoản MBBank yêu cầu xác thực OTP thiết bị mới (GW266). Vui lòng xác thực trên ứng dụng MBBank.")
            try:
                otp = input("[?] Enter OTP: ").strip()
            except Exception:
                raise RuntimeError("Tài khoản MBBank yêu cầu xác thực OTP thiết bị mới (GW266).")
            if not otp:
                raise RuntimeError("OTP was not provided.")

            otp_ref = f"{username}-{_time_now_short()}"
            otp_headers = dict(headers)
            otp_headers["X-Request-Id"] = otp_ref
            otp_headers["Refno"] = otp_ref

            for otp_path in [
                "/api/retail_web/internetbanking/v2.0/auth/authen-transaction",
                "/api/retail-internetbankingms/internetbanking/otp/activateDevice",
            ]:
                try:
                    otp_response = session.post(
                        BASE_URL + otp_path,
                        headers=otp_headers,
                        json={
                            "sessionId": payload.get("sessionId", ""),
                            "refNo": otp_ref,
                            "deviceIdCommon": device_id,
                            "otpTransaction": otp,
                            "transactionId": payload.get("transactionId", ""),
                        },
                        timeout=30,
                    )
                    otp_payload = otp_response.json()
                    if otp_payload.get("result", {}).get("ok"):
                        print("[+] Device confirmation succeeded.")
                        _save_device_id(username, device_id)
                        break
                except Exception:
                    continue
            else:
                print("[-] OTP confirmation failed. Regenerating deviceId and retrying...")
                device_id = _generate_device_id()
                _save_device_id(username, device_id)
            continue

        raise RuntimeError(f"Login failed ({code}): {message}")

    raise RuntimeError(f"Login failed after {max_retries} attempts.")


class MBBankService:
    def __init__(self, username: str, session_id: str, device_id: str, timeout: int = DEFAULT_TIMEOUT):
        self.username = username
        self.session_id = session_id
        self.device_id = device_id
        self.timeout = timeout
        self.session = _new_session()

    @classmethod
    def auto_login(cls, username: str, password: str, timeout: int = DEFAULT_TIMEOUT) -> "MBBankService":
        session_id, device_id = mb_login(username, password)
        return cls(username=username, session_id=session_id, device_id=device_id, timeout=timeout)

    def _headers(self, request_id: str) -> dict:
        headers = dict(DEFAULT_HEADERS)
        headers["X-Request-Id"] = request_id
        headers["Deviceid"] = self.device_id
        headers["Refno"] = request_id
        return headers

    def _request_id(self) -> str:
        return f"{self.username}-{_time_now_long()}"

    def _post_json(self, path: str, body: dict, request_id: str) -> Any:
        response = self.session.post(
            BASE_URL + path,
            headers=self._headers(request_id),
            json=body,
            timeout=self.timeout,
        )
        try:
            payload = response.json()
        except ValueError:
            payload = response.text

        if not response.ok:
            raise RuntimeError(f"MBBank API Error - HTTP {response.status_code} for {path}: {payload}")
        return payload

    def auth_post(self, path: str, extra_body: Optional[Dict[str, Any]] = None) -> Any:
        request_id = self._request_id()
        body = {
            "sessionId": self.session_id,
            "refNo": request_id,
            "deviceIdCommon": self.device_id,
        }
        if extra_body:
            body.update(extra_body)
        return self._post_json(path, body, request_id)

    def verify_biometric(self) -> Any:
        return self.auth_post("/api/retail-go-ekycms/v1.0/verify-biometric-nfc-transaction")

    def get_balance(self) -> Any:
        return self.auth_post("/api/retail-accountms/accountms/getBalance")

    def get_transactions(self, account_no: str, from_date: str, to_date: str) -> Any:
        return self.auth_post(
            "/api/retail-transactionms/transactionms/get-account-transaction-history",
            {
                "accountNo": account_no,
                "fromDate": from_date,
                "toDate": to_date,
            },
        )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Standalone MBBank login/balance/transaction flow.")
    parser.add_argument("--username", default=os.getenv("MB_USERNAME"))
    parser.add_argument("--password", default=os.getenv("MB_PASSWORD"))
    parser.add_argument("--account-no", default=os.getenv("MB_ACCOUNT_NO"))
    parser.add_argument("--from-date", default=os.getenv("MB_FROM_DATE", _default_from_date()))
    parser.add_argument("--to-date", default=os.getenv("MB_TO_DATE", _default_to_date()))
    parser.add_argument("--captcha", help="Manual captcha value for a single run.")
    parser.add_argument("--skip-balance", action="store_true", help="Skip balance API and only fetch transactions.")
    return parser


def _configure_stdio() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _pretty_print(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    _configure_stdio()
    args = _build_parser().parse_args()

    if args.captcha:
        os.environ["MB_CAPTCHA"] = args.captcha.strip().upper()

    if args.username == "YOUR_MB_PHONE_NUMBER":
        print("[-] Vui long mo file test_mb.py va dien USERNAME, PASSWORD truoc khi chay.")
        raise SystemExit(1)

    print(f"[...] Dang thuc thi dang nhap MBBank voi tai khoan {args.username}...")
    try:
        service = MBBankService.auto_login(username=args.username, password=args.password)
        print("[OK] Dang nhap thanh cong!")
        print(f"    Session ID: {service.session_id}")
        print(f"    Device ID: {service.device_id}")

        if not args.skip_balance:
            print("\n[...] Dang lay thong tin so du...")
            _pretty_print(service.get_balance())

        print(
            f"\n[...] Dang lay lich su giao dich cho tai khoan {args.account_no} "
            f"tu {args.from_date} den {args.to_date}..."
        )
        _pretty_print(
            service.get_transactions(
                account_no=args.account_no,
                from_date=args.from_date,
                to_date=args.to_date,
            )
        )
    except Exception as exc:
        print(f"[ERR] Loi: {exc}")
