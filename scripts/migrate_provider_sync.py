import sqlite3

conn = sqlite3.connect("tanglike.db")
cursor = conn.cursor()

# 1. Add dealer_price to services if missing
cursor.execute("PRAGMA table_info(services);")
cols = [r[1] for r in cursor.fetchall()]
if "dealer_price" not in cols:
    cursor.execute("ALTER TABLE services ADD COLUMN dealer_price FLOAT DEFAULT 0.0 NOT NULL;")
    print("Added dealer_price column to services table")

# 2. Create provider_service_mappings if missing
cursor.execute("""
CREATE TABLE IF NOT EXISTS provider_service_mappings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_id INTEGER NOT NULL,
    service_id INTEGER,
    external_service_id VARCHAR(100) NOT NULL,
    external_name VARCHAR(255),
    external_category VARCHAR(255),
    original_rate FLOAT DEFAULT 0.0 NOT NULL,
    markup_type VARCHAR(20) DEFAULT 'PERCENT' NOT NULL,
    markup_value FLOAT DEFAULT 30.0 NOT NULL,
    dealer_markup_type VARCHAR(20) DEFAULT 'PERCENT' NOT NULL,
    dealer_markup_value FLOAT DEFAULT 15.0 NOT NULL,
    auto_round BOOLEAN DEFAULT 1 NOT NULL,
    round_type VARCHAR(20) DEFAULT 'nearest' NOT NULL,
    round_unit FLOAT DEFAULT 10.0 NOT NULL,
    selling_price FLOAT DEFAULT 0.0 NOT NULL,
    dealer_price FLOAT DEFAULT 0.0 NOT NULL,
    min_quantity INTEGER DEFAULT 50 NOT NULL,
    max_quantity INTEGER DEFAULT 10000 NOT NULL,
    refill_enabled BOOLEAN DEFAULT 0 NOT NULL,
    cancel_enabled BOOLEAN DEFAULT 0 NOT NULL,
    sync_status VARCHAR(30) DEFAULT 'ACTIVE' NOT NULL,
    last_synced_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
    FOREIGN KEY(provider_id) REFERENCES providers(id),
    FOREIGN KEY(service_id) REFERENCES services(id)
);
""")
cursor.execute("CREATE INDEX IF NOT EXISTS ix_provider_service_mappings_id ON provider_service_mappings(id);")
cursor.execute("CREATE INDEX IF NOT EXISTS ix_provider_service_mappings_provider_id ON provider_service_mappings(provider_id);")
cursor.execute("CREATE INDEX IF NOT EXISTS ix_provider_service_mappings_service_id ON provider_service_mappings(service_id);")
cursor.execute("CREATE INDEX IF NOT EXISTS ix_provider_service_mappings_external_service_id ON provider_service_mappings(external_service_id);")

# 3. Create price_sync_logs if missing
cursor.execute("""
CREATE TABLE IF NOT EXISTS price_sync_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_id INTEGER NOT NULL,
    service_id INTEGER,
    external_service_id VARCHAR(100) NOT NULL,
    service_name VARCHAR(255) NOT NULL,
    old_rate FLOAT DEFAULT 0.0 NOT NULL,
    new_rate FLOAT DEFAULT 0.0 NOT NULL,
    old_price FLOAT DEFAULT 0.0 NOT NULL,
    new_price FLOAT DEFAULT 0.0 NOT NULL,
    change_percent FLOAT DEFAULT 0.0 NOT NULL,
    alert_triggered BOOLEAN DEFAULT 0 NOT NULL,
    status VARCHAR(50) DEFAULT 'UPDATED' NOT NULL,
    notes TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
    FOREIGN KEY(provider_id) REFERENCES providers(id),
    FOREIGN KEY(service_id) REFERENCES services(id)
);
""")
cursor.execute("CREATE INDEX IF NOT EXISTS ix_price_sync_logs_id ON price_sync_logs(id);")
cursor.execute("CREATE INDEX IF NOT EXISTS ix_price_sync_logs_provider_id ON price_sync_logs(provider_id);")
cursor.execute("CREATE INDEX IF NOT EXISTS ix_price_sync_logs_service_id ON price_sync_logs(service_id);")
cursor.execute("CREATE INDEX IF NOT EXISTS ix_price_sync_logs_created_at ON price_sync_logs(created_at);")

conn.commit()
conn.close()
print("Database migration completed successfully!")
