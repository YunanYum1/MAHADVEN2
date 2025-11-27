import discord
from discord.abc import Messageable

# ===================================================================================
# KELAS UNTUK PATH ASET
# ===================================================================================

class AssetPaths:
    """
    Kelas terpusat untuk menyimpan path ke file aset lokal.
    Memudahkan pengelolaan jika path atau nama file berubah.
    """
    LOGO_MAHA5 = "assets/logos/maha5_logo.png"

# ===================================================================================
# KELAS UNTUK WARNA
# ===================================================================================

class BotColors:
    """
    Kelas terpusat untuk menyimpan objek discord.Color agar konsisten di seluruh bot.
    """
    # --- Warna Tema Utama (MAHA5 & Default) ---
    MAHA5_PURPLE = discord.Color(0x5c2d91) # Warna ungu khas MAHA5
    DEFAULT      = MAHA5_PURPLE            # Warna embed default
    
    # --- Tingkat Kelangkaan (Rarity) ---
    COMMON    = discord.Color(0xa8a8a8)      # Abu-abu (Biasa)
    UNCOMMON  = discord.Color(0x5fde96)      # Hijau Muda (Tidak Biasa)
    RARE      = discord.Color(0x3498db)      # Biru Cerah (Langka)
    EPIC      = MAHA5_PURPLE                 # Ungu (Epik - Brand Color)
    LEGENDARY = discord.Color(0xf1c40f)      # Emas (Legendaris)
    MYTHIC    = discord.Color(0xff0055)      # Merah Neon (Mitologi/Sangat Langka)
    ARTIFACT  = discord.Color(0x00ffff)      # Cyan Terang (Artefak Kuno/Divine)

    # --- Elemen / Tipe Serangan (RPG Combat) ---
    ELEMENT_FIRE  = discord.Color(0xff4500)  # Merah Oranye
    ELEMENT_WATER = discord.Color(0x1e90ff)  # Biru Laut
    ELEMENT_WIND  = discord.Color(0x2ecc71)  # Hijau Angin
    ELEMENT_EARTH = discord.Color(0x8b4513)  # Coklat Tanah
    ELEMENT_LIGHT = discord.Color(0xffffca)  # Kuning Pucat
    ELEMENT_DARK  = discord.Color(0x2c3e50)  # Biru Gelap/Hitam
    ELEMENT_VOID  = discord.Color(0x9b59b6)  # Ungu Misterius

    # --- Tema Agensi / Faksi (Lore) ---
    # Warna ini bisa digunakan saat menampilkan profil member dari agensi tertentu
    AGENCY_MAHAVIRTUAL = MAHA5_PURPLE
    AGENCY_PRISM       = discord.Color(0x00bfff)  # Deep Sky Blue (Futuristik)
    AGENCY_MAISON      = discord.Color(0xffb6c1)  # Light Pink (Cafe/Maid)
    AGENCY_ATELIER     = discord.Color(0xba55d3)  # Medium Orchid (Magic/Crafting)
    AGENCY_REACT       = discord.Color(0xff6347)  # Tomato Red (Energetic)
    AGENCY_ABYSSAL     = discord.Color(0x191970)  # Midnight Blue (Villain/Dark)

    # --- Status UI & Notifikasi ---
    SUCCESS = discord.Color(0x2ecc71)     # Hijau (Berhasil)
    ERROR   = discord.Color(0xe74c3c)     # Merah (Gagal/Error)
    WARNING = discord.Color(0xe67e22)     # Oranye (Peringatan)
    INFO    = discord.Color(0x5865F2)     # Blurple (Info Netral Discord)
    LOADING = discord.Color(0x95a5a6)     # Abu-abu Pucat (Proses)
    
    # --- Fitur Spesifik: Stream ---
    STREAM_LIVE     = discord.Color.from_rgb(255, 82, 82)     # Merah LIVE
    STREAM_POSITIVE = discord.Color.from_rgb(88, 255, 88)     # Hijau (Donasi/Sub)
    STREAM_NEGATIVE = discord.Color.from_rgb(255, 137, 56)    # Oranye (Haters/Lag)
    STREAM_SPIKE    = discord.Color.from_rgb(255, 231, 71)    # Emas (Viral)
    STREAM_CRASH    = discord.Color.dark_red()                # Merah Gelap (Disconnect)
    STREAM_END      = discord.Color.dark_grey()               # Abu Gelap (Offline)

    # --- Fitur Spesifik: Fishing ---
    FISH_WATER      = discord.Color(0x0099cc)  # Biru Air
    FISH_HOOKED     = discord.Color(0xff4444)  # Merah (Ikan menyambar!)
    FISH_ESCAPED    = discord.Color(0x607d8b)  # Abu-abu (Ikan lepas)

# ===================================================================================
# FUNGSI BANTUAN (HELPER FUNCTIONS)
# ===================================================================================

async def send_embed_with_local_image(
    destination: Messageable,
    embed: discord.Embed,
    file_path: str,
    attachment_name: str,
    content: str = None
):
    """
    Fungsi bantuan untuk mengirim embed yang icon/gambarnya menggunakan file lokal.
    
    - destination: Tempat mengirim pesan (misal: ctx, channel).
    - embed: Embed yang sudah disiapkan.
    - file_path: Path lengkap ke file gambar lokal (misal: "assets/logos/maha5_logo.png").
    - attachment_name: Nama file yang akan digunakan di Discord (misal: "maha5_logo.png").
    - content: Teks pesan tambahan (opsional).
    """
    try:
        local_file = discord.File(file_path, filename=attachment_name)
        await destination.send(content=content, file=local_file, embed=embed)
    except FileNotFoundError:
        print(f"KESALAHAN: File aset lokal tidak ditemukan di '{file_path}'. Mengirim embed tanpa gambar.")
        await destination.send(content=content, embed=embed) # Fallback
    except Exception as e:
        print(f"Terjadi kesalahan saat mengirim embed dengan gambar lokal: {e}")