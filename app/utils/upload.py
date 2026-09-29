import os
import re
import uuid
import zipfile
import io
from typing import Tuple, Set

# Whitelist allowed image extensions and MIME types
ALLOWED_EXTENSIONS: Set[str] = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

ALLOWED_MIME_TYPES: Set[str] = {
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/gif"
}

DANGEROUS_EXTENSIONS: Set[str] = {
    ".php", ".phtml", ".php3", ".php4", ".php5", ".phps", ".phar",
    ".exe", ".dll", ".bat", ".cmd", ".sh", ".bash", ".py", ".pyw",
    ".js", ".jsp", ".jspx", ".asp", ".aspx", ".asa", ".asax",
    ".cgi", ".pl", ".jar", ".war", ".svg", ".svgz", ".html", ".htm",
    ".xhtml", ".shtml", ".htaccess", ".config", ".env", ".vbs"
}

MAGIC_BYTES = {
    ".png": b"\x89PNG\r\n\x1a\n",
    ".jpg": b"\xff\xd8\xff",
    ".jpeg": b"\xff\xd8\xff",
    ".gif": (b"GIF87a", b"GIF89a"),
}

DISALLOWED_PAYLOAD_SIGNATURES = [
    b"<?php",
    b"<?=",
    b"<script",
    b"<svg",
    b"<!doctype",
    b"<!entity",
    b"eval(",
    b"system(",
    b"passthru(",
    b"shell_exec(",
    b"base64_decode(",
    b"javascript:",
]

MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024  # 5MB


def sanitize_filename(filename: str) -> str:
    """Validates filename against null bytes, directory traversal, and dangerous characters."""
    if not filename or not isinstance(filename, str):
        raise ValueError("Tên tập tin không hợp lệ.")

    # Reject null bytes (raw and URL-encoded)
    if "\x00" in filename or "%00" in filename.lower():
        raise ValueError("Tên tập tin chứa ký tự null byte không hợp lệ.")

    # Reject path traversal patterns
    if ".." in filename or "/" in filename or "\\" in filename:
        raise ValueError("Phát hiện hành vi path traversal trong tên tập tin.")

    # Strip control characters
    clean = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", filename).strip()
    if not clean:
        raise ValueError("Tên tập tin rỗng sau khi làm sạch.")

    return clean


def validate_uploaded_image(
    filename: str,
    content_type: str,
    file_bytes: bytes,
    max_size_bytes: int = MAX_FILE_SIZE_BYTES
) -> str:
    """
    Validates uploaded image against extension, MIME, size, magic signature,
    embedded script polyglots, and path traversal. Returns a randomized, safe storage filename.
    """
    clean_name = sanitize_filename(filename)

    # 1. Size check
    if not file_bytes or len(file_bytes) == 0:
        raise ValueError("Tập tin tải lên rỗng.")

    if len(file_bytes) > max_size_bytes:
        raise ValueError(f"Dung lượng tập tin ({len(file_bytes)} bytes) vượt quá giới hạn cho phép ({max_size_bytes} bytes).")

    # 2. Extension check
    _, ext = os.path.splitext(clean_name)
    ext = ext.lower()

    if ext in DANGEROUS_EXTENSIONS:
        raise ValueError(f"Phần mở rộng tập tin '{ext}' bị từ chối vì lý do bảo mật.")

    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f"Định dạng tập tin '{ext}' không được hỗ trợ. Chỉ chấp nhận: PNG, JPG, JPEG, WEBP, GIF.")

    # 3. MIME type check
    mime = (content_type or "").lower().split(";")[0].strip()
    if mime not in ALLOWED_MIME_TYPES:
        raise ValueError(f"Kiểu nội dung (MIME) '{mime}' không hợp lệ.")

    # 4. File signature (Magic bytes) check
    if ext == ".png":
        if not file_bytes.startswith(MAGIC_BYTES[".png"]):
            raise ValueError("Chữ ký tệp (magic bytes) không khớp với định dạng PNG.")
    elif ext in (".jpg", ".jpeg"):
        if not file_bytes.startswith(MAGIC_BYTES[".jpg"]):
            raise ValueError("Chữ ký tệp (magic bytes) không khớp với định dạng JPEG.")
    elif ext == ".gif":
        if not (file_bytes.startswith(MAGIC_BYTES[".gif"][0]) or file_bytes.startswith(MAGIC_BYTES[".gif"][1])):
            raise ValueError("Chữ ký tệp (magic bytes) không khớp với định dạng GIF.")
    elif ext == ".webp":
        if not (file_bytes.startswith(b"RIFF") and len(file_bytes) >= 12 and file_bytes[8:12] == b"WEBP"):
            raise ValueError("Chữ ký tệp (magic bytes) không khớp với định dạng WEBP.")

    # 5. Polyglot and malicious embedded script check
    lower_bytes = file_bytes.lower()
    for sig in DISALLOWED_PAYLOAD_SIGNATURES:
        if sig in lower_bytes:
            raise ValueError("Nội dung tệp chứa mã thực thi nguy hiểm hoặc payload polyglot bị từ chối.")

    # 6. Generate safe randomized UUID filename
    safe_stored_filename = f"{uuid.uuid4().hex}{ext}"
    return safe_stored_filename


def get_safe_destination_path(base_dir: str, safe_filename: str) -> str:
    """Ensures the destination path stays strictly inside base_dir."""
    canonical_base = os.path.realpath(base_dir)
    target_path = os.path.realpath(os.path.join(canonical_base, safe_filename))

    if not target_path.startswith(canonical_base + os.sep) and target_path != canonical_base:
        raise ValueError("Phát hiện đường dẫn lưu trữ không an toàn (path traversal).")

    return target_path


def validate_zip_archive(
    zip_bytes: bytes,
    max_uncompressed_bytes: int = 20 * 1024 * 1024,
    max_ratio: float = 10.0
) -> None:
    """Guards against ZIP bombs, recursion, and traversal in compressed uploads."""
    if not zip_bytes or len(zip_bytes) == 0:
        raise ValueError("Tập tin nén rỗng.")

    compressed_size = len(zip_bytes)
    total_uncompressed = 0

    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            for info in zf.infolist():
                name = info.filename
                # Traversal check
                if ".." in name or name.startswith("/") or name.startswith("\\"):
                    raise ValueError("Tập tin nén chứa đường dẫn path traversal nguy hiểm.")

                total_uncompressed += info.file_size
                if total_uncompressed > max_uncompressed_bytes:
                    raise ValueError("Dung lượng giải nén vượt quá giới hạn an toàn (ZIP bomb protection).")

                if compressed_size > 0:
                    ratio = total_uncompressed / compressed_size
                    if ratio > max_ratio and total_uncompressed > 1024 * 1024:
                        raise ValueError(f"Tỷ lệ nén ({ratio:.1f}x) bất thường, nghi ngờ ZIP bomb.")
    except zipfile.BadZipFile:
        raise ValueError("Tập tin không phải định dạng ZIP hợp lệ.")