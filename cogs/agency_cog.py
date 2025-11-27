# cogs/agency_cog.py

import discord
from discord.ext import commands
import copy
import math
import datetime # [BARU] Impor datetime untuk timestamp

from database import get_player_data, update_player_data, get_all_players_in_agency
from ._utils import BotColors

# Helper statis
def _get_level_from_exp(total_exp: int) -> int:
    if total_exp < 0: total_exp = 0
    return int(math.sqrt(total_exp / 100)) + 1

PERMANENT_STAT_MODS = {
    "mahavirtual": {"base_atk": 5}, "prism_project": {"base_def": 4},
    "meisoncafe": {"base_hp": 20, "base_atk": 2, "base_def": 2, "base_spd": 1},
    "ateliernova": {"base_spd": 3, "base_hp": -0.1}, "react_entertainment": {"base_def": -0.15}
}

AGENCY_LEAVE_COST = 5000
AGENCY_LEAVE_COOLDOWN = 86400 # Detik dalam 1 hari

# ===================================================================================
# --- KELAS-KELAS VIEW (UI INTERAKTIF) ---
# ===================================================================================

class LeaveConfirmationView(discord.ui.View):
    def __init__(self, author: discord.User, cog: commands.Cog, lobby_message: discord.Message):
        super().__init__(timeout=60.0)
        self.author = author; self.cog = cog; self.bot = cog.bot
        self.lobby_message = lobby_message # Pesan lobi yang akan di-update

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("Ini bukan sesi konfirmasi milikmu!", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Ya, Keluar dari Agensi", style=discord.ButtonStyle.danger)
    async def confirm_leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.disable_all()

        player_data = await get_player_data(self.bot.db, self.author.id)
        if player_data.get('prisma', 0) < AGENCY_LEAVE_COST:
            await interaction.edit_original_response(content=f"❌ Gagal! Kamu tidak memiliki cukup Prisma. Dibutuhkan {AGENCY_LEAVE_COST} 💎.", view=self)
            return

        old_agency_id = player_data.get("agency_id")
        if not old_agency_id:
            await interaction.edit_original_response(content="❌ Gagal! Kamu sudah tidak berada di agensi.", view=self)
            return

        db_updates = {
            "agency_id": None,
            "prisma": player_data.get('prisma', 0) - AGENCY_LEAVE_COST,
            "agency_leave_timestamp": int(datetime.datetime.now().timestamp()) # [BARU] Set timestamp cooldown
        }
        current_stats = {'base_hp': player_data.get('base_hp', 100), 'base_atk': player_data.get('base_atk', 10),'base_def': player_data.get('base_def', 5), 'base_spd': player_data.get('base_spd', 10)}

        if old_agency_id in PERMANENT_STAT_MODS:
            for stat, value in PERMANENT_STAT_MODS[old_agency_id].items():
                if isinstance(value, float): current_stats[stat] = int(current_stats[stat] / (1 + value))
                else: current_stats[stat] -= value
        
        db_updates.update(current_stats)
        await update_player_data(self.bot.db, self.author.id, **db_updates)

        await interaction.edit_original_response(
            content=f"✅ Kamu telah berhasil keluar dari agensi. Stat dasarmu telah dikembalikan. Biaya {AGENCY_LEAVE_COST} Prisma telah dibayarkan.", view=self
        )
        # Hapus pesan lobi yang lama karena sudah tidak relevan
        await self.lobby_message.delete()
        self.stop()

    @discord.ui.button(label="Batal", style=discord.ButtonStyle.secondary)
    async def cancel_leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.disable_all()
        await interaction.response.edit_message(content="Aksi dibatalkan. Kamu tetap di agensimu.", view=self)
        self.stop()

    def disable_all(self):
        for item in self.children: item.disabled = True

class AgencyView(discord.ui.View):
    def __init__(self, author: discord.User, cog: commands.Cog):
        super().__init__(timeout=300); self.author = author; self.cog = cog; self.bot = cog.bot
        self.message: discord.Message = None; self.selected_agency_id = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("Ini bukan sesi agensi milikmu!", ephemeral=True); return False
        return True
        
    async def on_timeout(self):
        if self.message:
            for item in self.children: item.disabled = True
            try: await self.message.edit(content="Sesi panel agensi telah berakhir.", view=self, embed=None)
            except discord.NotFound: pass

    async def update_view(self, interaction: discord.Interaction):
        if not interaction.response.is_done(): await interaction.response.defer()
        player_data = await get_player_data(self.bot.db, self.author.id)
        current_agency = self.bot.get_agency_by_id(player_data.get("agency_id"))
        embed = await self._create_embed(current_agency)
        # [PERUBAHAN] Kirim status agensi pemain ke build_components
        self.build_components(has_agency=bool(current_agency))
        await interaction.edit_original_response(embed=embed, view=self, content=None)
    
    async def refresh_after_action(self, interaction: discord.Interaction):
        self.selected_agency_id = None
        player_data = await get_player_data(self.bot.db, self.author.id)
        current_agency = self.bot.get_agency_by_id(player_data.get("agency_id"))
        embed = await self._create_embed(current_agency)
        # [PERUBAHAN] Kirim status agensi pemain ke build_components
        self.build_components(has_agency=bool(current_agency))
        await self.message.edit(embed=embed, view=self, content=None)

    def build_components(self, has_agency: bool):
        self.clear_items()
        if self.selected_agency_id is None:
            self.add_item(AgencySelect(self))
        else:
            # [PERUBAHAN] Tombol gabung akan nonaktif jika pemain sudah punya agensi
            join_button = discord.ui.Button(
                label="Gabung Agensi Ini", 
                style=discord.ButtonStyle.success, 
                emoji="✅",
                disabled=has_agency # Tombol dinonaktifkan jika has_agency adalah True
            )
            join_button.callback = self.join_agency_callback
            
            back_button = discord.ui.Button(label="Kembali", style=discord.ButtonStyle.secondary, emoji="⬅️")
            back_button.callback = self.back_callback
            
            self.add_item(join_button)
            self.add_item(back_button)

    async def _create_embed(self, current_agency: dict = None) -> discord.Embed:
        if self.selected_agency_id is None:
            # [PERUBAHAN] Deskripsi diubah untuk memperjelas aturan baru
            description = (
                "Pilih agensi dari menu untuk melihat detailnya.\n\n"
                "**PERHATIAN:** Kamu hanya bisa bergabung jika belum memiliki agensi. "
                f"Jika sudah bergabung, kamu harus keluar terlebih dahulu melalui `{self.bot.command_prefix}lobby`."
            )
            embed = discord.Embed(title="🏢 Papan Rekrutmen Agensi", description=description, color=BotColors.DEFAULT)
            if current_agency: embed.set_footer(text=f"Agensimu Saat Ini: {current_agency['name']}", icon_url=self.author.display_avatar.url)
            else: embed.set_footer(text="Kamu belum bergabung dengan agensi.", icon_url=self.author.display_avatar.url)
            return embed
        else:
            agency = self.bot.get_agency_by_id(self.selected_agency_id)
            embed = discord.Embed(title=f"{agency['emoji']} Selamat Datang di {agency['name']}", description=f"_{agency['description']}_", color=BotColors.RARE)
            embed.add_field(name="✅ Keuntungan", value="\n".join([f"• {b}" for b in agency['benefits']]), inline=False)
            embed.add_field(name="⚠️ Kerugian", value="\n".join([f"• {d}" for d in agency['drawbacks']]), inline=False)
            if current_agency: 
                footer_text = "Kamu sudah menjadi anggota agensi lain. Keluar dulu untuk bergabung."
                if current_agency['id'] == agency['id']:
                    footer_text = "Kamu sudah menjadi anggota agensi ini."
                embed.set_footer(text=footer_text)
            else: 
                embed.set_footer(text="Pikirkan baik-baik sebelum bergabung!")
            return embed

    async def join_agency_callback(self, interaction: discord.Interaction):
        player_data = await get_player_data(self.bot.db, self.author.id)

        # 1. Cek Utama: Apakah pemain sudah punya agensi?
        if player_data.get("agency_id"):
            await interaction.response.send_message(
                f"❌ Gagal! Kamu sudah bergabung di agensi lain. Gunakan `{self.bot.command_prefix}lobby` untuk keluar terlebih dahulu.", 
                ephemeral=True
            )
            return

        # 2. Jika lolos, lanjutkan logika bergabung seperti biasa
        agency_to_join = self.bot.get_agency_by_id(self.selected_agency_id)
        db_updates = {"agency_id": self.selected_agency_id}
        current_stats = {'base_hp': player_data.get('base_hp', 100), 'base_atk': player_data.get('base_atk', 10), 'base_def': player_data.get('base_def', 5), 'base_spd': player_data.get('base_spd', 10)}
        
        # [LOGIKA LAMA DIHAPUS] Tidak perlu lagi menghapus stat agensi lama, karena pemain pasti tidak punya agensi.

        # Terapkan stat dari agensi baru yang akan dimasuki
        if new_agency_id := self.selected_agency_id:
            if new_agency_id in PERMANENT_STAT_MODS:
                for stat, value in PERMANENT_STAT_MODS[new_agency_id].items():
                    if isinstance(value, float): current_stats[stat] = int(current_stats[stat] * (1 + value))
                    else: current_stats[stat] += value

        db_updates.update(current_stats)
        await update_player_data(self.bot.db, self.author.id, **db_updates)
        
        await interaction.response.send_message(embed=discord.Embed(title="🎉 Selamat Bergabung!", description=f"Kamu sekarang anggota **{agency_to_join['name']}**!", color=BotColors.SUCCESS), ephemeral=True)
        await self.refresh_after_action(interaction)

    async def back_callback(self, interaction: discord.Interaction):
        self.selected_agency_id = None
        await self.update_view(interaction)

class AgencySelect(discord.ui.Select):
    def __init__(self, parent_view: AgencyView):
        self.parent_view = parent_view; options = [discord.SelectOption(label=a['name'], value=a['id'], emoji=a['emoji']) for a in self.parent_view.bot.agencies]
        super().__init__(placeholder="Lihat detail agensi...", options=options, row=0)
    async def callback(self, interaction: discord.Interaction):
        self.parent_view.selected_agency_id = self.values[0]
        await self.parent_view.update_view(interaction)

# [BARU] View untuk panel lobi
class LobbyView(discord.ui.View):
    def __init__(self, author: discord.User, cog: commands.Cog):
        super().__init__(timeout=300.0)
        self.author = author
        self.cog = cog
        self.bot = cog.bot
        self.message: discord.Message = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("Ini bukan sesi lobi milikmu!", ephemeral=True); return False
        return True

    @discord.ui.button(label="Keluar Agensi", style=discord.ButtonStyle.danger, emoji="🚪")
    async def leave_agency(self, interaction: discord.Interaction, button: discord.ui.Button):
        player_data = await get_player_data(self.bot.db, self.author.id)
        
        # Cek Cooldown
        last_leave_ts = player_data.get('agency_leave_timestamp', 0)
        current_ts = int(datetime.datetime.now().timestamp())
        
        if (current_ts - last_leave_ts) < AGENCY_LEAVE_COOLDOWN:
            cooldown_ends = last_leave_ts + AGENCY_LEAVE_COOLDOWN
            await interaction.response.send_message(f"❌ Kamu sedang dalam masa cooldown! Kamu bisa keluar agensi lagi <t:{cooldown_ends}:R>.", ephemeral=True)
            return

        # Cek Prisma
        if player_data.get('prisma', 0) < AGENCY_LEAVE_COST:
            await interaction.response.send_message(f"❌ Prismamu tidak cukup! Dibutuhkan **{AGENCY_LEAVE_COST} Prisma** untuk keluar.", ephemeral=True)
            return

        # Tampilkan konfirmasi
        embed = discord.Embed(title="❓ Konfirmasi Keluar Agensi", description=f"Apakah kamu yakin ingin keluar? Ini akan dikenakan biaya **{AGENCY_LEAVE_COST} Prisma** dan kamu akan kehilangan semua bonus stat permanen.", color=BotColors.WARNING)
        confirm_view = LeaveConfirmationView(self.author, self.cog, self.message)
        await interaction.response.send_message(embed=embed, view=confirm_view, ephemeral=True)

    async def on_timeout(self):
        if self.message:
            for item in self.children: item.disabled = True
            try: await self.message.edit(view=self)
            except discord.NotFound: pass

# ===================================================================================
# --- COG UTAMA ---
# ===================================================================================

class AgencyCog(commands.Cog, name="Agensi"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="agensi")
    @commands.cooldown(1, 15, commands.BucketType.user)
    async def agensi(self, ctx: commands.Context):
        """Menampilkan panel Agensi untuk memberikan bonus tambahan pada player"""
        player_data = await get_player_data(self.bot.db, ctx.author.id)
        if not player_data.get('equipped_title_id'): return await ctx.send(f"Kamu harus debut dulu!", ephemeral=True)
        player_level = _get_level_from_exp(player_data.get('exp', 0))

        if player_level < 10: return await ctx.send(f"Kamu harus Level 10. Levelmu saat ini: {player_level}.", ephemeral=True)
        
        view = AgencyView(ctx.author, self)
        current_agency = self.bot.get_agency_by_id(player_data.get("agency_id"))

        # [PERUBAHAN] Kirim status agensi saat pertama kali membuat komponen
        view.build_components(has_agency=bool(current_agency)) 
        
        initial_embed = await view._create_embed(current_agency)
        message = await ctx.send(embed=initial_embed, view=view)
        view.message = message
        
    @commands.command(name="lobby", aliases=["lobi"])
    @commands.cooldown(1, 20, commands.BucketType.user)
    async def lobby(self, ctx: commands.Context):
        player_data = await get_player_data(self.bot.db, ctx.author.id)
        agency_id = player_data.get('agency_id')

        if not agency_id: return await ctx.send(f"Kamu belum bergabung dengan agensi. Gunakan `{self.bot.command_prefix}agensi`.")
        agency_data = self.bot.get_agency_by_id(agency_id)

        if not agency_data: return await ctx.send("Data agensimu tidak ditemukan.")
        all_members_data = await get_all_players_in_agency(self.bot.db, agency_id)

        member_lines = []
        for i, (user_id, user_exp) in enumerate(all_members_data[:10]):
            if member := ctx.guild.get_member(user_id):
                level, rank_emoji = _get_level_from_exp(user_exp), "👑 " if i == 0 else ""
                member_lines.append(f"`{i+1}.` {rank_emoji}{member.display_name} (Level {level})")

        member_list_str = "\n".join(member_lines) if member_lines else "Kamu satu-satunya anggota."
        if len(all_members_data) > 10: member_list_str += f"\n... dan {len(all_members_data) - 10} anggota lainnya."

        embed = discord.Embed(title=f"{agency_data['emoji']} Lobi Agensi: {agency_data['name']}", description=f"_{agency_data['description']}_", color=BotColors.RARE)
        embed.set_thumbnail(url=ctx.author.display_avatar.url)
        embed.add_field(name=f"🏆 Anggota Teratas (Total: {len(all_members_data)})", value=member_list_str, inline=False)
        embed.add_field(name="✅ Keuntungan", value="\n".join([f"• {b}" for b in agency_data['benefits']]), inline=True)
        embed.add_field(name="⚠️ Kerugian", value="\n".join([f"• {d}" for d in agency_data['drawbacks']]), inline=True)
        embed.set_footer(text=f"Selamat datang di lobi, {ctx.author.display_name}!")
        
        # [PERBAIKAN] Kirim embed bersama dengan LobbyView
        view = LobbyView(ctx.author, self)
        message = await ctx.send(embed=embed, view=view)
        view.message = message

async def setup(bot: commands.Bot):
    await bot.add_cog(AgencyCog(bot))