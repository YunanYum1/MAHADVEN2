# database.py

import aiosqlite
import json
import time

DB_NAME = "mahadven.db"

async def initialize_database():
    """
    Menghubungkan ke database, membuat/memverifikasi skema tabel, dan
    mengembalikan objek koneksi database.
    """
    db = await aiosqlite.connect(DB_NAME)
    cursor = await db.cursor()

    # --- Tabel Players [DIPERBARUI LENGKAP] ---
    await cursor.execute("""
        CREATE TABLE IF NOT EXISTS players (
            user_id INTEGER PRIMARY KEY,
            exp INTEGER DEFAULT 0,
            subscribers INTEGER DEFAULT 0,
            prisma INTEGER DEFAULT 3000,
            equipped_title_id INTEGER DEFAULT NULL,
            inventory TEXT DEFAULT '[]',
            equipment TEXT DEFAULT '{}',
            level INTEGER DEFAULT 1,
            base_hp INTEGER DEFAULT 100,
            base_atk INTEGER DEFAULT 10,
            base_def INTEGER DEFAULT 5,
            base_spd INTEGER DEFAULT 10,
            title_pity INTEGER DEFAULT 0,
            artifact_pity INTEGER DEFAULT 0,
            agency_id TEXT DEFAULT NULL,
            pvp_wins INTEGER DEFAULT 0,
            agency_leave_timestamp INTEGER DEFAULT 0,
            equipment_upgrades TEXT DEFAULT '{}',
            fishing_data TEXT DEFAULT NULL,
            daily_streak INTEGER DEFAULT 0,
            last_daily_claim INTEGER DEFAULT 0
        )
    """)
    
    # --- Migrasi Skema (Update Kolom Baru Otomatis) ---
    player_table_info = await cursor.execute("PRAGMA table_info(players)")
    columns = [row[1] for row in await player_table_info.fetchall()]

    # 1. Kolom Equipment Upgrades
    if 'equipment_upgrades' not in columns:
        print("Migrasi: Menambahkan kolom 'equipment_upgrades'...")
        await cursor.execute("ALTER TABLE players ADD COLUMN equipment_upgrades TEXT DEFAULT '{}'")

    # 2. Kolom Fishing Data
    if 'fishing_data' not in columns:
        print("Migrasi: Menambahkan kolom 'fishing_data'...")
        default_fishing = json.dumps({
            "inventory": ["rod_basic"], 
            "equipped": {"rod": "rod_basic", "charm": None},
            "aquarium": []
        })
        await cursor.execute(f"ALTER TABLE players ADD COLUMN fishing_data TEXT DEFAULT '{default_fishing}'")
    
    # 3. Kolom Daily System [PENTING UNTUK !!daily]
    if 'daily_streak' not in columns:
        print("Migrasi: Menambahkan kolom 'daily_streak'...")
        await cursor.execute("ALTER TABLE players ADD COLUMN daily_streak INTEGER DEFAULT 0")
    
    if 'last_daily_claim' not in columns:
        print("Migrasi: Menambahkan kolom 'last_daily_claim'...")
        await cursor.execute("ALTER TABLE players ADD COLUMN last_daily_claim INTEGER DEFAULT 0")

    # ... (Migrasi kolom stat lama untuk kompatibilitas) ...
    if 'level' not in columns: await cursor.execute("ALTER TABLE players ADD COLUMN level INTEGER DEFAULT 1")
    if 'base_hp' not in columns: await cursor.execute("ALTER TABLE players ADD COLUMN base_hp INTEGER DEFAULT 100")
    if 'base_atk' not in columns: await cursor.execute("ALTER TABLE players ADD COLUMN base_atk INTEGER DEFAULT 10")
    if 'base_def' not in columns: await cursor.execute("ALTER TABLE players ADD COLUMN base_def INTEGER DEFAULT 5")
    if 'base_spd' not in columns: await cursor.execute("ALTER TABLE players ADD COLUMN base_spd INTEGER DEFAULT 10")
    if 'title_pity' not in columns: await cursor.execute("ALTER TABLE players ADD COLUMN title_pity INTEGER DEFAULT 0")
    if 'artifact_pity' not in columns: await cursor.execute("ALTER TABLE players ADD COLUMN artifact_pity INTEGER DEFAULT 0")
    if 'agency_id' not in columns: await cursor.execute("ALTER TABLE players ADD COLUMN agency_id TEXT DEFAULT NULL")
    if 'agency_leave_timestamp' not in columns: await cursor.execute("ALTER TABLE players ADD COLUMN agency_leave_timestamp INTEGER DEFAULT 0")

    # --- Tabel Player Titles ---
    await cursor.execute("""
        CREATE TABLE IF NOT EXISTS player_titles (
            user_id INTEGER NOT NULL,
            title_id INTEGER NOT NULL,
            FOREIGN KEY (user_id) REFERENCES players (user_id),
            PRIMARY KEY (user_id, title_id)
        )
    """)
    
    # --- Tabel Player Quests ---
    await cursor.execute("""
        CREATE TABLE IF NOT EXISTS player_quests (
            user_id INTEGER NOT NULL,
            quest_id TEXT NOT NULL,
            progress INTEGER DEFAULT 0,
            completed INTEGER DEFAULT 0,
            claimed INTEGER DEFAULT 0,
            assigned_period TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES players (user_id),
            PRIMARY KEY (user_id, quest_id)
        )
    """)

    # Migrasi Quest Period
    quest_table_info = await cursor.execute("PRAGMA table_info(player_quests)")
    q_columns = [row[1] for row in await quest_table_info.fetchall()]
    if 'assigned_period' not in q_columns:
        print("Migrasi: Menambahkan kolom 'assigned_period' ke 'player_quests'...")
        await cursor.execute("ALTER TABLE player_quests ADD COLUMN assigned_period TEXT DEFAULT ''")

    # 4. Kolom Farm Data (Sistem Pertanian)
    if 'farm_data' not in columns:
        print("Migrasi: Menambahkan kolom 'farm_data'...")
        # Struktur default: 3 Slot tanah kosong
        default_farm = json.dumps({
            "slots": [
                {"status": "empty", "plant_id": None, "planted_at": 0, "last_watered": 0, "accumulated_growth": 0},
                {"status": "empty", "plant_id": None, "planted_at": 0, "last_watered": 0, "accumulated_growth": 0},
                {"status": "empty", "plant_id": None, "planted_at": 0, "last_watered": 0, "accumulated_growth": 0}
            ]
        })
        await cursor.execute(f"ALTER TABLE players ADD COLUMN farm_data TEXT DEFAULT '{default_farm}'")

    await db.commit()
    await cursor.close()
    print("Database telah diperbarui dan siap digunakan.")
    return db

# =========================================================================
# FUNGSI CRUD (GET/UPDATE)
# =========================================================================

async def get_player_data(db: aiosqlite.Connection, user_id: int):
    """
    Mengambil data pemain atau membuat entri baru jika tidak ada.
    """
    db.row_factory = aiosqlite.Row 
    cursor = await db.cursor()
    
    await cursor.execute("SELECT * FROM players WHERE user_id = ?", (user_id,))
    player_row = await cursor.fetchone()
    
    if player_row is None:
        # Masukkan default value untuk fishing saat buat user baru
        default_fishing = json.dumps({
            "inventory": ["rod_basic"], 
            "equipped": {"rod": "rod_basic", "charm": None},
            "aquarium": []
        })
        await cursor.execute("INSERT INTO players (user_id, fishing_data) VALUES (?, ?)", (user_id, default_fishing))
        await db.commit()
        await cursor.execute("SELECT * FROM players WHERE user_id = ?", (user_id,))
        player_row = await cursor.fetchone()
        
    await cursor.close()
    return dict(player_row) if player_row else None

async def update_player_data(db: aiosqlite.Connection, user_id: int, **kwargs):
    """
    Memperbarui satu atau lebih kolom data untuk seorang pemain secara generik.
    """
    if not kwargs: return

    updates = ", ".join([f"{key} = ?" for key in kwargs])
    values = list(kwargs.values())
    values.append(user_id)
    
    query = f"UPDATE players SET {updates} WHERE user_id = ?"
    
    cursor = await db.cursor()
    await cursor.execute(query, tuple(values))
    await db.commit()
    await cursor.close()

# --- FUNGSI UTILITIES ---

async def add_title_to_player(db: aiosqlite.Connection, user_id: int, title_id: int):
    await db.execute("INSERT OR IGNORE INTO player_titles (user_id, title_id) VALUES (?, ?)", (user_id, title_id))
    await db.commit()

async def set_equipped_title(db: aiosqlite.Connection, user_id: int, title_id: int):
    await update_player_data(db, user_id, equipped_title_id=title_id)

async def get_player_titles(db: aiosqlite.Connection, user_id: int) -> list[int]:
    cursor = await db.cursor()
    await cursor.execute("SELECT title_id FROM player_titles WHERE user_id = ?", (user_id,))
    rows = await cursor.fetchall()
    await cursor.close()
    return [row[0] for row in rows]

async def get_player_inventory(db: aiosqlite.Connection, user_id: int) -> list:
    player_data = await get_player_data(db, user_id)
    try: return json.loads(player_data.get('inventory', '[]'))
    except: return []

async def get_player_equipment(db: aiosqlite.Connection, user_id: int) -> dict:
    player_data = await get_player_data(db, user_id)
    try: return json.loads(player_data.get('equipment', '{}'))
    except: return {}

async def update_player_equipment_and_inventory(db: aiosqlite.Connection, user_id: int, slot: str, new_item_id: int | None):
    player_data = await get_player_data(db, user_id)
    inventory = json.loads(player_data.get('inventory', '[]'))
    equipment = json.loads(player_data.get('equipment', '{}'))

    currently_equipped_id = equipment.get(slot)
    if currently_equipped_id:
        inventory.append(int(currently_equipped_id))

    if new_item_id:
        if new_item_id in inventory: inventory.remove(new_item_id)
        equipment[slot] = new_item_id
    else:
        if slot in equipment: del equipment[slot]

    await update_player_data(db, user_id, inventory=json.dumps(inventory), equipment=json.dumps(equipment))

async def get_all_player_data(db):
    async with db.execute("""
        SELECT 
            p.user_id, p.exp, p.subscribers, p.prisma, p.pvp_wins, p.base_hp, p.base_atk, p.base_def, p.base_spd,
            (SELECT COUNT(pt.title_id) FROM player_titles pt WHERE pt.user_id = p.user_id) as title_count
        FROM players p
    """) as cursor:
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    
async def add_artifact_to_player(db: aiosqlite.Connection, user_id: int, artifact_id: int):
    player_data = await get_player_data(db, user_id)
    inventory = json.loads(player_data.get('inventory', '[]'))
    inventory.append(artifact_id)
    await update_player_data(db, user_id, inventory=json.dumps(inventory))

async def get_all_players_in_agency(db, agency_id: str):
    async with db.cursor() as cursor:
        await cursor.execute("SELECT user_id, exp FROM players WHERE agency_id = ? ORDER BY exp DESC", (agency_id,))
        return await cursor.fetchall()
    
async def reset_player_progress(db: aiosqlite.Connection, user_id: int):
    """Mereset semua progres pemain ke nilai default (termasuk streak)."""
    default_fishing = json.dumps({"inventory": ["rod_basic"], "equipped": {"rod": "rod_basic", "charm": None}, "aquarium": []})
    
    async with db.cursor() as cursor:
        await cursor.execute("DELETE FROM player_titles WHERE user_id = ?", (user_id,))
        await cursor.execute("DELETE FROM player_quests WHERE user_id = ?", (user_id,))
        await cursor.execute("""
            UPDATE players 
            SET
                exp = 0, subscribers = 0, prisma = 0, equipped_title_id = NULL,
                inventory = '[]', equipment = '{}', equipment_upgrades = '{}',
                fishing_data = ?,
                level = 1, base_hp = 100, base_atk = 10, base_def = 5, base_spd = 10,
                title_pity = 0, artifact_pity = 0, 
                agency_id = NULL, pvp_wins = 0, agency_leave_timestamp = 0,
                daily_streak = 0, last_daily_claim = 0
            WHERE user_id = ?
        """, (default_fishing, user_id,))
    await db.commit()
    print(f"Player progress for user_id {user_id} has been fully reset.")

async def get_player_upgrades(db: aiosqlite.Connection, user_id: int) -> dict:
    player_data = await get_player_data(db, user_id)
    try: return json.loads(player_data.get('equipment_upgrades', '{}'))
    except: return {}

async def update_player_upgrades(db: aiosqlite.Connection, user_id: int, slot: str, level: int, bonus_stats: dict = None):
    upgrades = await get_player_upgrades(db, user_id)
    if slot not in upgrades: upgrades[slot] = {}
    upgrades[slot]['level'] = level
    if bonus_stats is not None: upgrades[slot]['bonus_stats'] = bonus_stats
    await update_player_data(db, user_id, equipment_upgrades=json.dumps(upgrades))

    
# --- Tambahan untuk Transaksi Title ---

async def get_player_titles(db, user_id: int) -> list:
    """Mengambil list ID title yang dimiliki player."""
    async with db.execute("SELECT title_id FROM player_titles WHERE user_id = ?", (user_id,)) as cursor:
        rows = await cursor.fetchall()
        return [row[0] for row in rows]

async def add_title_to_player(db, user_id: int, title_id: int):
    """Memberikan title ke player."""
    try:
        await db.execute("INSERT OR IGNORE INTO player_titles (user_id, title_id) VALUES (?, ?)", (user_id, title_id))
        await db.commit()
    except Exception as e:
        print(f"Error adding title: {e}")

async def remove_player_title(db, user_id: int, title_id: int):
    """Menghapus title dari player (saat dijual/diberikan)."""
    try:
        await db.execute("DELETE FROM player_titles WHERE user_id = ? AND title_id = ?", (user_id, title_id))
        await db.commit()
    except Exception as e:
        print(f"Error removing title: {e}")

async def has_title(db, user_id: int, title_id: int) -> bool:
    """Cek apakah player sudah punya title ini (untuk mencegah duplikat)."""
    async with db.execute("SELECT 1 FROM player_titles WHERE user_id = ? AND title_id = ?", (user_id, title_id)) as cursor:
        return await cursor.fetchone() is not None