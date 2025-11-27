# cogs/admin_tools.py
import discord
from discord.ext import commands
import math
import json
from database import get_player_data, update_player_data

class AdminTools(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def _calculate_new_level(self, exp):
        return int(math.sqrt(exp / 100)) + 1

    @commands.command(name="migrate_levels")
    @commands.is_owner() # Hanya owner yang bisa pakai
    async def migrate_levels(self, ctx):
        """
        [BAHAYA] Mereset Level & Base Stats semua player berdasarkan EXP mereka saat ini
        menggunakan rumus baru (Kuadratik).
        """
        msg = await ctx.send("🔄 Memulai migrasi database ke sistem Level Baru...")
        
        # 1. Ambil semua player
        async with self.bot.db.execute("SELECT user_id, exp, agency_id FROM players") as cursor:
            all_players = await cursor.fetchall()

        count = 0
        for row in all_players:
            user_id, exp, agency_id = row
            
            # 2. Hitung Level Baru
            new_level = self._calculate_new_level(exp)
            
            # 3. Hitung Ulang Base Stat (Supaya Fair)
            # Rumus Stat: Base + ((Level - 1) * Multiplier)
            # Default: HP 100, ATK 10, DEF 5, SPD 10
            # Gain per Lv: HP+15, ATK+3, DEF+2, SPD+1
            
            new_base_hp = 100 + ((new_level - 1) * 15)
            new_base_atk = 10 + ((new_level - 1) * 3)
            new_base_def = 5 + ((new_level - 1) * 2)
            new_base_spd = 10 + ((new_level - 1) * 1)
            
            # 4. Tambahkan Bonus Agensi Kembali (Karena kita reset stat base)
            # (Pastikan logic ini sama dengan di agency_cog.py)
            if agency_id == "mahavirtual":
                new_base_atk += 5
            elif agency_id == "prism_project":
                new_base_def += 4
            elif agency_id == "meisoncafe":
                new_base_hp += 20; new_base_atk += 2; new_base_def += 2; new_base_spd += 1
            elif agency_id == "ateliernova":
                new_base_spd += 3; new_base_hp = int(new_base_hp * 0.9)
            elif agency_id == "react_entertainment":
                new_base_def = int(new_base_def * 0.85)

            # 5. Simpan ke Database
            await update_player_data(
                self.bot.db, user_id,
                level=new_level,
                base_hp=new_base_hp,
                base_atk=new_base_atk,
                base_def=new_base_def,
                base_spd=new_base_spd
            )
            count += 1
            
            if count % 10 == 0:
                await msg.edit(content=f"🔄 Memproses... ({count}/{len(all_players)} Player)")

        await msg.edit(content=f"✅ **Migrasi Selesai!**\nTotal {count} player telah di-update ke sistem Level baru.\nLevel dan Stat mereka telah disesuaikan dengan EXP saat ini.")

async def setup(bot):
    await bot.add_cog(AdminTools(bot))