import asyncio
from sqlalchemy import select
from app.database.session import AsyncSessionLocal
from app.models import Service

async def main():
    out = []
    async with AsyncSessionLocal() as session:
        for keyword in ["chia sẻ live", "share live", "theo dõi youtube", "sub youtube", "subscriber"]:
            result = await session.execute(select(Service).filter(Service.name.ilike(f"%{keyword}%")))
            items = result.scalars().all()
            out.append(f"Keyword '{keyword}': found {len(items)}")
            for it in items:
                out.append(f"  ID={it.id}, ExtID={it.external_service_id}, Name={it.name}, Provider={it.provider_id}, Price={it.price}")
    with open("services_found.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(out))

asyncio.run(main())
