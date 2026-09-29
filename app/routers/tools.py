import urllib.parse
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException
from app.schemas.all import ApiResponse

router = APIRouter(prefix="/tools", tags=["Free Tools"])

class UTMRequest(BaseModel):
    url: str
    source: str
    medium: str
    campaign: str

class TextStatRequest(BaseModel):
    text: str

@router.post("/utm-builder", response_model=ApiResponse[dict])
async def generate_utm(payload: UTMRequest):
    clean_url = payload.url.strip()
    if not (clean_url.startswith("http://") or clean_url.startswith("https://")):
        raise HTTPException(status_code=400, detail="URL phải bắt đầu bằng http:// hoặc https://")
    params = {
        "utm_source": payload.source,
        "utm_medium": payload.medium,
        "utm_campaign": payload.campaign
    }
    encoded = urllib.parse.urlencode(params)
    separator = "&" if "?" in clean_url else "?"
    final_url = f"{clean_url}{separator}{encoded}"
    return ApiResponse(data={"final_url": final_url})

@router.post("/text-stats", response_model=ApiResponse[dict])
async def analyze_text(payload: TextStatRequest):
    text = payload.text
    char_count = len(text)
    char_no_spaces = len(text.replace(" ", "").replace("\n", "").replace("\r", ""))
    word_count = len(text.split())
    line_count = len(text.splitlines()) if text else 0
    return ApiResponse(data={
        "characters": char_count,
        "characters_no_spaces": char_no_spaces,
        "words": word_count,
        "lines": line_count
    })
