with open("backend/app/schemas/__init__.py", "r", encoding="utf-8") as f:
    text = f.read()

new_exports = [
    "ProviderTestConnectionRequest",
    "ProviderTestConnectionResponse",
    "ProviderCatalogItem",
    "ImportServiceItem",
    "ImportServicesRequest",
    "BulkMarkupPreviewRequest",
    "ProviderMappingResponse",
    "UpdateMappingRequest",
    "PriceSyncLogResponse",
]

for item in new_exports:
    if f'"{item}"' not in text:
        text = text.replace(
            '    "DashboardStats",',
            f'    "DashboardStats",\n    "{item}",'
        )

with open("backend/app/schemas/__init__.py", "w", encoding="utf-8") as f:
    f.write(text)

print("Updated backend/app/schemas/__init__.py successfully!")
