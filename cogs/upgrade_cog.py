import discord
from discord.ext import commands
import random
import json
import math

from database import (
    get_player_data, 
    get_player_equipment, 
    get_player_upgrades, 
    update_player_upgrades, 
    update_player_data
)
from ._utils import BotColors

class UpgradeView(discord.ui.View):
    def __init__(self, ctx, cog):
        super().__init__(timeout=120)
        self.ctx = ctx
        self.cog = cog
        self.bot = cog.bot
        self.selected_slot = None
        self.message: discord.Message = None

    async def on_timeout(self):
        if self.message:
            for child in self.children:
                child.disabled = True
            try:
                await self.message.edit(view=self)
            except:
                pass

    @discord.ui.select(
        placeholder="Pilih Equipment untuk di-Upgrade...",
        options=[
            discord.SelectOption(label="Helm", value="helm", emoji="🧢"),
            discord.SelectOption(label="Armor", value="armor", emoji="👕"),
            discord.SelectOption(label="Celana", value="pants", emoji="👖"),
            discord.SelectOption(label="Sepatu", value="shoes", emoji="👢")
        ]
    )
    async def select_slot(self, interaction: discord.Interaction, select: discord.ui.Select):
        if interaction.user.id != self.ctx.author.id:
            return await interaction.response.send_message("Ini bukan sesi milikmu.", ephemeral=True)

        self.selected_slot = select.values[0]
        await self.update_embed(interaction)

    @discord.ui.button(label="Tempa (Upgrade)", style=discord.ButtonStyle.success, emoji="🔨", disabled=True)
    async def upgrade_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.ctx.author.id: return
        await self.cog.process_upgrade(interaction, self)

    async def update_embed(self, interaction: discord.Interaction):
        self.children[1].disabled = False 
        
        equipment = await get_player_equipment(self.bot.db, self.ctx.author.id)
        item_id = equipment.get(self.selected_slot)
        
        if not item_id:
            embed = discord.Embed(title="Slot Kosong", description="Kamu tidak memakai equipment di slot ini.", color=BotColors.ERROR)
            self.children[1].disabled = True
            if interaction.response.is_done():
                return await self.message.edit(embed=embed, view=self)
            else:
                return await interaction.response.edit_message(embed=embed, view=self)

        item_data = self.bot.get_item_by_id(item_id)
        upgrades = await get_player_upgrades(self.bot.db, self.ctx.author.id)
        slot_upgrade = upgrades.get(self.selected_slot, {'level': 0, 'bonus_stats': {}})
        current_level = slot_upgrade.get('level', 0)

        if current_level >= 12:
            embed = discord.Embed(title="Maksimal Level", description=f"**{item_data['name']}** sudah mencapai level maksimal (Lv.12)!", color=BotColors.LEGENDARY)
            self.children[1].disabled = True
            if interaction.response.is_done():
                return await self.message.edit(embed=embed, view=self)
            else:
                return await interaction.response.edit_message(embed=embed, view=self)

        # Kalkulasi baru (Fixed Price)
        success_rate = self.cog.calculate_success_rate(current_level)
        cost = self.cog.calculate_cost(current_level)
        
        stats_preview = self.cog.get_stats_preview(item_data, current_level)
        
        embed = discord.Embed(
            title=f"⚒️ Blacksmith - Upgrade {item_data['name']}",
            description="Tekan tombol **Tempa** untuk menaikkan level item.",
            color=BotColors.DEFAULT
        )
        embed.add_field(name="Level Saat Ini", value=f"**+{current_level}** ➡️ **+{current_level+1}**", inline=True)
        embed.add_field(name="Biaya Tetap", value=f"💰 **{cost:,}** Prisma", inline=True)
        embed.add_field(name="Peluang Sukses", value=f"🎲 **{success_rate}%**", inline=True)
        
        embed.add_field(name="Efek Stat Utama", value=stats_preview, inline=False)
        
        # Tampilkan Sub-stat yang SUDAH didapat sebelumnya
        if extra_stats := slot_upgrade.get('bonus_stats', {}):
            extra_text_list = []
            for k, v in extra_stats.items():
                # [PERUBAHAN] Format tampilan persen untuk Crit
                if k in ['crit_rate', 'crit_damage']:
                    extra_text_list.append(f"✨ **{k.replace('_', ' ').upper()} +{v:.1%}**")
                else:
                    extra_text_list.append(f"✨ **{k.upper()} +{v}**")
            extra_text = "\n".join(extra_text_list)
            embed.add_field(name="Stat Tambahan Aktif", value=extra_text, inline=False)
        else:
            embed.add_field(name="Stat Tambahan", value="❓ *Upgrade berpeluang membuka potensi tersembunyi...*", inline=False)

        embed.set_footer(text="Biaya upgrade tetap sama untuk semua jenis item.")
        
        if interaction.response.is_done():
            await self.message.edit(embed=embed, view=self)
        else:
            await interaction.response.edit_message(embed=embed, view=self)

class UpgradeCog(commands.Cog, name="Upgrade"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def calculate_success_rate(self, current_level: int) -> int:
        """Rate Hardcore."""
        if current_level == 0: return 100
        if current_level == 1: return 90
        if current_level == 2: return 80
        if current_level == 3: return 70
        if current_level == 4: return 60
        if current_level == 5: return 50
        if current_level == 6: return 40
        if current_level == 7: return 30
        if current_level == 8: return 20
        if current_level == 9: return 15
        if current_level == 10: return 7
        return 3 # Lv 11 ke 12

    def calculate_cost(self, current_level: int) -> int:
        """HARGA FIX TIAP LEVEL."""
        fixed_costs = {
            0: 500, 1: 1000, 2: 1500, 3: 2000, 4: 2500, 5: 3000,
            6: 3500, 7: 4000, 8: 5000, 9: 6000, 10: 7500, 11: 9000
        }
        return fixed_costs.get(current_level, 99999)

    def calculate_scaled_stat(self, stat_name: str, base_value: float, level: int) -> float:
        """
        [PERUBAHAN] Logika scaling stat utama.
        - Stat Biasa (ATK/HP/DEF/SPD): Naik 10% per level.
        - Stat Crit (Rate/DMG): Naik 5% per level (agar tidak broken).
        """
        if base_value == 0: return 0
        
        # Multiplier dasar
        multiplier_per_level = 0.10 # 10%
        
        # Crit stat scalingnya lebih kecil
        if stat_name in ['crit_rate', 'crit_damage']:
            multiplier_per_level = 0.05 # 5%
            
        if base_value > 0:
            multiplier = 1 + (level * multiplier_per_level)
            result = base_value * multiplier
            # Jika stat biasa, bulatkan ke int. Jika crit, biarkan float.
            return int(result) if stat_name not in ['crit_rate', 'crit_damage'] else result
        else:
            # Stat minus membaik tiap 3 level (hanya untuk stat biasa)
            penalty_reduction = level // 3
            new_value = base_value + penalty_reduction
            return min(0, new_value)

    def get_stats_preview(self, item_data: dict, current_level: int) -> str:
        base_stats = item_data.get('stat_boost', {})
        lines = []
        
        for stat, val in base_stats.items():
            # Hitung nilai saat ini dan selanjutnya
            val_now = self.calculate_scaled_stat(stat, val, current_level)
            val_next = self.calculate_scaled_stat(stat, val, current_level + 1)
            
            # Format tampilan (Persen vs Angka Biasa)
            if stat in ['crit_rate', 'crit_damage']:
                # Format persen (0.05 -> 5.0%)
                str_now = f"{val_now:.1%}"
                str_next = f"**{val_next:.1%}**"
            else:
                str_now = f"{val_now}"
                str_next = f"**{val_next}**"

            arrow = "➡️" 
            lines.append(f"{stat.replace('_',' ').upper()}: {str_now} {arrow} {str_next}")
        
        if not lines:
            return "Stat dasar item ini tidak berubah."
            
        return "\n".join(lines)

    async def process_upgrade(self, interaction: discord.Interaction, view: UpgradeView):
        await interaction.response.defer()
        
        user_id = interaction.user.id
        slot = view.selected_slot
        
        player_data = await get_player_data(self.bot.db, user_id)
        equipment = await get_player_equipment(self.bot.db, user_id)
        
        upgrades = await get_player_upgrades(self.bot.db, user_id)
        slot_upgrade = upgrades.get(slot, {'level': 0, 'bonus_stats': {}})
        current_level = slot_upgrade.get('level', 0)
        
        cost = self.calculate_cost(current_level)
        
        if player_data.get('prisma', 0) < cost:
            return await interaction.followup.send(f"❌ Prisma tidak cukup! Butuh **{cost:,}**.", ephemeral=True)

        new_prisma = player_data.get('prisma', 0) - cost
        await update_player_data(self.bot.db, user_id, prisma=new_prisma)

        success_rate = self.calculate_success_rate(current_level)
        rng = random.randint(1, 100)

        # --- GAGAL ---
        if rng > success_rate:
            msg_fail = "Prisma telah menjadi debu."
            if current_level >= 8: msg_fail = "Sakit, tapi tak berdarah."
            
            fail_embed = discord.Embed(title="💥 Tempa Gagal!", description=f"Upgrade ke Level **{current_level+1}** gagal.\n{msg_fail}", color=BotColors.ERROR)
            fail_embed.set_footer(text=f"Roll: {rng} (Butuh <= {success_rate})")
            await interaction.followup.send(embed=fail_embed, ephemeral=True)
            await view.update_embed(interaction)
            return

        # --- SUKSES ---
        new_level = current_level + 1
        bonus_stats = slot_upgrade.get('bonus_stats', {}).copy()
        
        msg_extra = ""
        
        # [PERUBAHAN] LOGIKA SUB-STAT RAHASIA
        # Hanya di-roll saat sukses, 15% chance
        if random.random() < 0.15:
            # Pool stat termasuk crit sekarang
            possible_stats = ['atk', 'def', 'hp', 'spd', 'crit_rate', 'crit_damage']
            new_stat = random.choice(possible_stats)
            
            gain = 0
            gain_text = ""

            # Tentukan nilai gain berdasarkan tipe stat
            if new_stat == 'crit_rate':
                # Tambah 0.5% - 1.5%
                gain = random.uniform(0.05, 0.25)
                gain_text = f"+{gain:.1%}"
            elif new_stat == 'crit_damage':
                # Tambah 2% - 5%
                gain = random.uniform(0.1, 0.25)
                gain_text = f"+{gain:.1%}"
            else:
                # Stat biasa: 5 - 15
                gain = random.randint(5, 25)
                gain_text = f"+{gain}"
            
            # Simpan ke bonus_stats (akumulasi)
            current_val = bonus_stats.get(new_stat, 0)
            bonus_stats[new_stat] = current_val + gain
            
            msg_extra = f"\n\n🎉 **KEJUTAN!**\nSaat menempa, kamu menemukan stat tersembunyi:\n✨ **{new_stat.replace('_',' ').upper()} {gain_text}**"

        await update_player_upgrades(self.bot.db, user_id, slot, new_level, bonus_stats)

        success_embed = discord.Embed(
            title="✨ SUKSES MENEMPA!",
            description=f"Equipment berhasil naik ke **Level {new_level}**!{msg_extra}",
            color=BotColors.SUCCESS
        )
        if msg_extra:
             success_embed.set_footer(text="Cek detail stat barumu di panel utama!")

        await interaction.followup.send(embed=success_embed, ephemeral=True)
        await view.update_embed(interaction)

    @commands.command(name="upgrade", aliases=["forge", "tempa"])
    async def upgrade_cmd(self, ctx):
        """Menempa Equipment menjaid lebih kuat"""
        view = UpgradeView(ctx, self)
        embed = discord.Embed(
            title="⚒️ The Blacksmith",
            description="Selamat datang.\nBiaya tempa di sini **tetap (Flat Rate)**, tidak peduli seberapa langka barangmu.\n\nApakah kamu cukup beruntung untuk membuka **Potensi Tersembunyi**?",
            color=discord.Color.dark_grey()
        )
        view.message = await ctx.send(embed=embed, view=view)

async def setup(bot: commands.Bot):
    await bot.add_cog(UpgradeCog(bot))