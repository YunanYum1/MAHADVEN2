import os
import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv
import asyncio
import json
import random
import sys
from datetime import datetime

# Impor database dan handler error
from database import initialize_database
from handlers.error_handler import setup_error_handler

# Memuat variabel dari file .env
load_dotenv()
TOKEN = os.getenv('BOT_TOKEN')
DEV_ID = int(os.getenv('DEV_ID'))
PREFIX = os.getenv('PREFIX')
STATUS_CHANNEL_ID = os.getenv('STATUS_LOG_CHANNEL_ID')

# Menentukan intents
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True 

# ===================================================================================
# CLASS VIEW KHUSUS ADMIN
# ===================================================================================
class AdminControlView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != DEV_ID:
            await interaction.response.send_message("⛔ Akses Ditolak.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Update", style=discord.ButtonStyle.primary, emoji="🔄", custom_id="admin_reload")
    async def reload_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        log_text, error_count = [], 0
        for filename in os.listdir('./cogs'):
            if filename.endswith('.py') and not filename.startswith('_'):
                try:
                    await self.bot.reload_extension(f'cogs.{filename[:-3]}')
                    log_text.append(f"✅ `{filename}`")
                except Exception as e:
                    log_text.append(f"❌ `{filename}`: {e}")
                    error_count += 1
        status_color = discord.Color.green() if error_count == 0 else discord.Color.orange()
        embed = discord.Embed(title="🔄 Reload Selesai", description="\n".join(log_text), color=status_color)
        await interaction.followup.send(embed=embed, ephemeral=True)
        
        # Trigger update panel manual agar timestamp terupdate
        if hasattr(self.bot, 'update_status_panel'):
            await self.bot.update_status_panel()

    @discord.ui.button(label="Ping", style=discord.ButtonStyle.secondary, emoji="📶", custom_id="admin_ping")
    async def ping_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Update manual panel status
        if hasattr(self.bot, 'update_status_panel'):
            await self.bot.update_status_panel()
        
        lat = self.bot.latency
        if lat == float('inf') or lat is None:
            latency_display = "N/A"
        else:
            latency_display = f"{round(lat * 1000)}ms"
            
        await interaction.response.send_message(f"🏓 Panel Diperbarui! Latensi saat ini: **{latency_display}**", ephemeral=True)

    @discord.ui.button(label="Shutdown", style=discord.ButtonStyle.danger, emoji="🔌", custom_id="admin_shutdown")
    async def shutdown_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("🔌 Mematikan sistem...", ephemeral=True)
        
        # Update panel jadi Merah sebelum mati
        if STATUS_CHANNEL_ID:
            try:
                channel = self.bot.get_channel(int(STATUS_CHANNEL_ID))
                if channel:
                    # Cari pesan terakhir bot untuk diedit
                    target_msg = None
                    async for msg in channel.history(limit=5):
                        if msg.author == self.bot.user:
                            target_msg = msg
                            break
                    
                    embed = discord.Embed(
                        title="🔴 SISTEM OFFLINE", 
                        description="Bot telah dimatikan secara manual oleh Developer.", 
                        color=discord.Color.red(), 
                        timestamp=datetime.now()
                    )
                    embed.set_footer(text="Shutting down...")
                    
                    # Disable semua tombol
                    for child in self.children: child.disabled = True
                    
                    if target_msg:
                        await target_msg.edit(embed=embed, view=self)
                    else:
                        await channel.send(embed=embed, view=self)
            except: pass

        await self.bot.close()

# ===================================================================================
# BOT UTAMA
# ===================================================================================

class MAHADVEN(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix=PREFIX, intents=intents, owner_id=DEV_ID)
        self.db = None
        # Data Game
        self.titles = []
        self.monsters = []
        self.monster_titles = []
        self.items = []
        self.artifacts = []
        self.agencies = [] 
        self.quests = {}
        self.stream_data = {}
        self.fishes = [] 
        
        # Status Message Cache
        self.status_message = None

    def _load_json_data(self, file_path, default):
        try:
            with open(file_path, 'r', encoding='utf-8') as f: return json.load(f)
        except: return default

    def load_all_game_data(self):
        print("--- MEMUAT DATA GAME ---")
        self.titles = self._load_json_data('./data/titles.json', [])
        self.monsters = self._load_json_data('./data/monsters.json', [])
        self.monster_titles = self._load_json_data('./data/monsters_titles.json', [])
        self.items = self._load_json_data('./data/items.json', [])
        self.artifacts = self._load_json_data('./data/artifacts.json', [])
        self.agencies = self._load_json_data('./data/agencies.json', [])
        self.quests = self._load_json_data('./data/quests.json', {"daily": [], "weekly": []})
        self.fishes = self._load_json_data('./data/fishes.json', [])
        self.fishing_items = self._load_json_data('./data/fishing_items.json', [])
        print("--- SELESAI MEMUAT DATA ---")

    # --- Helper Get Data ---
    def get_agency_by_id(self, agency_id: str):
        return next((a for a in self.agencies if a.get('id') == agency_id), None)

    def get_title_by_id(self, title_id: int):
        return next((t for t in self.titles if t.get('id') == title_id), None)
    
    def get_monster_title_by_id(self, title_id: int):
        return next((t for t in self.monster_titles if t.get('id') == title_id), None)

    def get_item_by_id(self, item_id: int):
        if item_id is None: return None
        for i in self.items + self.artifacts:
            if i.get('id') == item_id: return i
        return None

    def get_skill_details(self, participant_data: dict, skill_name: str) -> dict:
        if not (participant_data and skill_name): return None
        equipped_title_id = participant_data.get('equipped_title_id')
        title_data = participant_data.get('raw_title_data') or self.get_title_by_id(equipped_title_id)
        if title_data:
            return next((skill for skill in title_data.get('skills', []) if skill.get('name') == skill_name), None)
        return None

    async def _load_all_cogs(self):
        print("--- MEMUAT COGS ---")
        for filename in os.listdir('./cogs'):
            if filename.endswith('.py') and not filename.startswith('_'):
                try: await self.load_extension(f'cogs.{filename[:-3]}'); print(f"⚙️ {filename}")
                except Exception as e: print(f"❌ {filename}: {e}")

    async def setup_hook(self):
        self.db = await initialize_database() 
        setup_error_handler(self)
        self.load_all_game_data()
        await self._load_all_cogs()
        
        # Mulai loop
        self.change_status.start()
        self.status_panel_loop.start()

    # --- TASK 1: ROTASI STATUS DISCORD ---
    @tasks.loop(minutes=5)
    async def change_status(self):
        # Variasi status agar tidak membosankan
        activities = [
            discord.Game(name=f"{PREFIX}help | RPG"),
            discord.Game(name="Fishing 🐟"),
            discord.Game(name="PvP Arena ⚔️"),
            discord.Activity(type=discord.ActivityType.listening, name="Lagu Tavern 🎵"),
            discord.Activity(type=discord.ActivityType.watching, name=f"{len(self.guilds)} Server"),
            discord.Activity(type=discord.ActivityType.competing, name="Turnamen Akbar 🏆")
        ]
        await self.change_presence(activity=random.choice(activities))

    @change_status.before_loop
    async def before_change_status(self): await self.wait_until_ready()

    # --- TASK 2: UPDATE PANEL STATUS REALTIME ---
    @tasks.loop(seconds=30) # Update setiap 30 detik
    async def status_panel_loop(self):
        await self.update_status_panel()

    @status_panel_loop.before_loop
    async def before_status_panel_loop(self):
        await self.wait_until_ready()

    async def update_status_panel(self):
        """Fungsi inti untuk memperbarui embed status"""
        if not STATUS_CHANNEL_ID: return

        try:
            channel = self.get_channel(int(STATUS_CHANNEL_ID))
            if not channel: return

            # --- FIX ERROR INFINITY FLOAT ---
            # Jika latency masih infinity (saat baru nyala), set ke nilai dummy atau handle khusus
            if self.latency is None or self.latency == float('inf'):
                latency_ms = 0 
                is_starting_up = True
            else:
                latency_ms = round(self.latency * 1000)
                is_starting_up = False
            
            # Tentukan Warna & Icon
            if is_starting_up:
                status_color = discord.Color.light_grey()
                signal_icon = "🔄 **Memulai...**"
                display_ping = "N/A"
            elif latency_ms < 100:
                status_color = discord.Color.green()
                signal_icon = "🟢 **Sangat Bagus**"
                display_ping = f"`{latency_ms}ms`"
            elif latency_ms < 300:
                status_color = discord.Color.gold()
                signal_icon = "🟡 **Sedang**"
                display_ping = f"`{latency_ms}ms`"
            else:
                status_color = discord.Color.red()
                signal_icon = "🔴 **Buruk (Lag)**"
                display_ping = f"`{latency_ms}ms`"

            # Build Embed
            embed = discord.Embed(
                title="🖥️ MONITOR PANEL", 
                description=f"**Status Bot:** Online ✅\n**Uptime:** Sejak {datetime.now().strftime('%H:%M')}",
                color=status_color,
                timestamp=datetime.now()
            )
            embed.set_thumbnail(url=self.user.display_avatar.url)
            
            embed.add_field(name="📡 Koneksi (Ping)", value=display_ping, inline=True)
            embed.add_field(name="📶 Kualitas Sinyal", value=signal_icon, inline=True)
            embed.add_field(name="🌐 Server", value=f"`{len(self.guilds)} Guilds`", inline=True)
            
            embed.set_footer(text="Auto-update setiap 30 detik")

            # Cari pesan lama untuk diedit (biar nggak spam)
            view = AdminControlView(self)
            
            # Jika kita sudah punya referensi pesan, pakai itu
            if self.status_message:
                try:
                    await self.status_message.edit(embed=embed, view=view)
                    return
                except discord.NotFound:
                    self.status_message = None # Pesan dihapus user, reset

            # Jika belum punya, cari di history
            if not self.status_message:
                found = False
                async for msg in channel.history(limit=5):
                    if msg.author == self.user:
                        self.status_message = msg
                        await msg.edit(embed=embed, view=view)
                        found = True
                        break
                
                # Jika benar-benar tidak ada, kirim baru
                if not found:
                    self.status_message = await channel.send(embed=embed, view=view)

        except Exception as e:
            print(f"Gagal update status panel: {e}")

    async def on_ready(self):
        print(f'🟢 ONLINE: {self.user}')
        # Trigger update pertama kali saat nyala
        # Kita beri delay sedikit agar latency sempat terhitung
        await asyncio.sleep(5)
        await self.update_status_panel()

async def main():
    bot = MAHADVEN()
    try: await bot.start(TOKEN)
    except KeyboardInterrupt: await bot.close()

if __name__ == "__main__":
    asyncio.run(main())