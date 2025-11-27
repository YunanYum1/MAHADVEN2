import discord
from discord.ext import commands
from discord import ui
import asyncio
from typing import Literal, Optional, List

from database import initialize_database, get_player_data, update_player_data

# --- Modal untuk meminta input nama Cog saat reload ---
class ReloadCogModal(ui.Modal, title="Reload Cog"):
    cog_name = ui.TextInput(
        label="Nama Cog",
        placeholder="Contoh: profile_cog",
        style=discord.TextStyle.short,
        required=True
    )

    def __init__(self, bot: commands.Bot):
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction):
        cog_full_name = f"cogs.{self.cog_name.value}"
        try:
            await self.bot.reload_extension(cog_full_name)
            await interaction.response.send_message(f"✅ Cog `{self.cog_name.value}` berhasil dimuat ulang.", ephemeral=True)
        except commands.ExtensionNotFound:
            await interaction.response.send_message(f"❌ Cog `{self.cog_name.value}` tidak ditemukan.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Terjadi kesalahan saat memuat ulang cog:\n```py\n{e}\n```", ephemeral=True)


# --- View untuk Konfirmasi Tindakan Berbahaya (Format DB) ---
class ConfirmFormatView(ui.View):
    def __init__(self, bot: commands.Bot, original_interaction: discord.Interaction):
        super().__init__(timeout=30)
        self.bot = bot
        self.original_interaction = original_interaction
        self.value = None

    @ui.button(label="KONFIRMASI HAPUS", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: ui.Button):
        # Hanya user asli yang bisa menekan tombol ini
        if interaction.user.id != self.original_interaction.user.id:
            await interaction.response.send_message("Anda tidak bisa melakukan ini!", ephemeral=True)
            return
            
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            # Nonaktifkan semua tombol setelah ditekan
            for item in self.children:
                item.disabled = True
            await self.original_interaction.edit_original_response(view=self)

            # Proses format database
            cursor = await self.bot.db.cursor()
            await cursor.execute("DROP TABLE IF EXISTS players")
            await cursor.execute("DROP TABLE IF EXISTS player_titles")
            await self.bot.db.commit()
            await cursor.close()
            
            await initialize_database()
            
            await interaction.followup.send("✅ Database berhasil diformat dan tabel baru telah dibuat.", ephemeral=True)
            self.value = True

        except Exception as e:
            await interaction.followup.send(f"❌ **Terjadi kesalahan fatal saat format database:**\n`{e}`", ephemeral=True)
            self.value = False
        
        self.stop()

    @ui.button(label="Batal", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.original_interaction.user.id:
            await interaction.response.send_message("Anda tidak bisa melakukan ini!", ephemeral=True)
            return

        for item in self.children:
            item.disabled = True
        await self.original_interaction.edit_original_response(view=self)
        await interaction.response.send_message("⚠️ Proses format database dibatalkan.", ephemeral=True)
        self.value = False
        self.stop()

    async def on_timeout(self):
        # Jika waktu habis, nonaktifkan tombol
        for item in self.children:
            item.disabled = True
        await self.original_interaction.edit_original_response(content="*Waktu konfirmasi habis...*", view=self)


# --- View Utama untuk Panel Developer ---
class DevPanelView(ui.View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=300) # Panel akan nonaktif setelah 5 menit
        self.bot = bot

    # Cek apakah pengguna yang berinteraksi adalah pemilik bot
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.bot.owner_id:
            await interaction.response.send_message("⛔ Panel ini bukan untukmu!", ephemeral=True)
            return False
        return True

    @ui.button(label="Format Database", style=discord.ButtonStyle.danger, emoji="⚠️", row=0)
    async def format_database_button(self, interaction: discord.Interaction, button: ui.Button):
        embed = discord.Embed(
            title="Konfirmasi Format Database",
            description="Ini akan **MENGHAPUS SEMUA DATA PEMAIN SECARA PERMANEN**. Tindakan ini tidak dapat dibatalkan.",
            color=discord.Color.red()
        )
        view = ConfirmFormatView(self.bot, interaction)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @ui.button(label="Reload Cog", style=discord.ButtonStyle.primary, emoji="🔄", row=0)
    async def reload_cog_button(self, interaction: discord.Interaction, button: ui.Button):
        modal = ReloadCogModal(self.bot)
        await interaction.response.send_modal(modal)

    @ui.button(label="Reload Game Data", style=discord.ButtonStyle.success, emoji="📄", row=1)
    async def reload_data_button(self, interaction: discord.Interaction, button: ui.Button):
        try:
            self.bot.load_all_game_data()
            await interaction.response.send_message("✅ **Berhasil!** Semua data game dari file JSON telah dimuat ulang.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ **Gagal!** Terjadi kesalahan saat memuat ulang data:\n```py\n{e}\n```", ephemeral=True)

    @ui.button(label="Shutdown Bot", style=discord.ButtonStyle.danger, emoji="🔌", row=1)
    async def shutdown_button(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_message("Bot sedang dimatikan...", ephemeral=True)
        await self.bot.close()


class EditValueModal(ui.Modal):
    """Modal untuk memasukkan nilai yang akan di-set, add, atau reduce."""
    value_input = ui.TextInput(label="Jumlah", placeholder="Masukkan angka...", style=discord.TextStyle.short, required=True)

    def __init__(self, title: str):
        super().__init__(title=title)

    async def on_submit(self, interaction: discord.Interaction):
        # Validasi bahwa input adalah angka
        if not self.value_input.value.isdigit():
            await interaction.response.send_message("Input harus berupa angka!", ephemeral=True)
            return
        
        # Simpan nilai dan tutup modal
        self.value = int(self.value_input.value)
        await interaction.response.defer() # Tutup modal tanpa pesan
        self.stop()

class EditPlayerView(ui.View):
    """View interaktif utama untuk mengedit data pemain."""

    def __init__(self, bot: commands.Bot, ctx: commands.Context, target: discord.Member):
        super().__init__(timeout=300)
        self.bot = bot
        self.ctx = ctx
        self.target = target
        
        self.selected_stat: Optional[str] = None
        self.stat_map = {
            'Prisma': 'prisma', 'Subscribers': 'subscribers',
            'EXP': 'exp', 'Level': 'level',
            'Base HP': 'base_hp', 'Base ATK': 'base_atk', 'Base DEF': 'base_def', 'Base SPD': 'base_spd'
        }
        
        # Panggil `build_components` untuk pertama kali
        self.build_components()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("Panel ini bukan untukmu!", ephemeral=True)
            return False
        return True
        
    async def update_panel(self, interaction: discord.Interaction):
        """Menggambar ulang seluruh panel."""
        # Defer ini berfungsi sebagai pengaman, tetapi defer utama ada di callback.
        if not interaction.response.is_done():
            await interaction.response.defer()

        self.build_components()
        embed = await self.create_embed()
        await interaction.edit_original_response(embed=embed, view=self)

    def build_components(self):
        """Membangun ulang komponen berdasarkan state."""
        self.clear_items()
        
        # Selalu tampilkan dropdown untuk memilih stat
        self.add_item(StatSelect(self.stat_map.keys(), self.selected_stat))
        
        # Hanya tampilkan tombol aksi jika stat sudah dipilih
        if self.selected_stat:
            self.add_item(ActionButton(label="Set", style=discord.ButtonStyle.primary, emoji="✏️"))
            self.add_item(ActionButton(label="Add", style=discord.ButtonStyle.success, emoji="➕"))
            self.add_item(ActionButton(label="Reduce", style=discord.ButtonStyle.danger, emoji="➖"))

    async def create_embed(self) -> discord.Embed:
        """Membuat embed yang menampilkan status saat ini."""
        player_data = await get_player_data(self.bot.db, self.target.id)
        if not player_data:
            return discord.Embed(title=f"❌ Error", description=f"{self.target.mention} belum melakukan debut.", color=discord.Color.red())

        embed = discord.Embed(title=f"📝 Mengedit Data: {self.target.display_name}", color=discord.Color.orange())
        embed.set_thumbnail(url=self.target.display_avatar.url)
        
        if self.selected_stat:
            db_stat = self.stat_map[self.selected_stat]
            current_value = player_data.get(db_stat, 0)
            embed.description = f"Mengedit stat **{self.selected_stat}**.\nNilai saat ini: `{current_value}`\n\nPilih aksi di bawah (Set, Add, atau Reduce)."
        else:
            embed.description = "Pilih stat yang ingin diubah dari menu di bawah ini."
            
        embed.set_footer(text="Panel ini akan nonaktif setelah 5 menit tidak ada aktivitas.")
        return embed

class StatSelect(ui.Select):
    """Dropdown untuk memilih stat yang akan diedit."""
    def __init__(self, stat_options: List[str], current_selection: Optional[str]):
        options = [discord.SelectOption(label=stat) for stat in stat_options]
        super().__init__(placeholder="Pilih stat untuk diedit...", options=options)
        self.current_selection = current_selection

    # [PERBAIKAN KUNCI DI SINI]
    async def callback(self, interaction: discord.Interaction):
        """
        Callback yang diperbaiki untuk merespons interaksi secepat mungkin.
        """
        # Langkah 1: SEGERA defer interaksi untuk menghindari timeout.
        await interaction.response.defer()
        
        # Langkah 2: Lakukan semua logika seperti biasa.
        view: EditPlayerView = self.view
        view.selected_stat = self.values[0]
        
        # Langkah 3: Panggil fungsi update, tetapi sekarang ia akan menggunakan
        # `interaction.edit_original_response` pada interaksi yang sudah di-defer.
        await view.update_panel(interaction)

class ActionButton(ui.Button):
    """Tombol untuk memilih aksi (Set, Add, Reduce)."""
    async def callback(self, interaction: discord.Interaction):
        view: EditPlayerView = self.view
        action_mode = self.label.lower()
        
        # Buka modal untuk meminta input nilai
        modal_title = f"{self.label} Stat: {view.selected_stat}"
        modal = EditValueModal(title=modal_title)
        await interaction.response.send_modal(modal)
        await modal.wait() # Tunggu sampai modal ditutup atau disubmit

        # Jika modal disubmit dengan nilai valid
        if hasattr(modal, 'value'):
            value = modal.value
            db_stat = view.stat_map[view.selected_stat]
            
            player_data = await get_player_data(view.bot.db, view.target.id)
            current_value = player_data.get(db_stat, 0)
            
            new_value = 0
            if action_mode == 'set': new_value = value
            elif action_mode == 'add': new_value = current_value + value
            elif action_mode == 'reduce': new_value = current_value - value
            
            new_value = max(0, new_value) # Pastikan tidak negatif

            await update_player_data(view.bot.db, view.target.id, **{db_stat: new_value})

            # Kirim pesan konfirmasi di channel
            await view.ctx.send(f"✅ Berhasil! Stat **{db_stat}** untuk **{view.target.mention}** telah diubah dari `{current_value}` menjadi `{new_value}`.")
            
            # Reset panel untuk pengeditan selanjutnya
            view.selected_stat = None
            await view.update_panel(modal.interaction) # Gunakan interaksi dari modal untuk mengedit pesan panel

# --- Cog Utama untuk Perintah Developer ---
class DevCog(commands.Cog, name="Developer"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="dev")
    @commands.is_owner()
    async def developer_panel(self, ctx: commands.Context):
        """Menampilkan panel kontrol khusus untuk developer bot."""
        embed = discord.Embed(title="Developer Control Panel", description="Gunakan tombol di bawah untuk mengelola bot.", color=discord.Color.blue())
        view = DevPanelView(self.bot)
        await ctx.send(embed=embed, view=view, ephemeral=True)

    # [IMPLEMENTASI BARU] Perintah edit interaktif
    @commands.command(name="edit", aliases=["editplayer"])
    @commands.is_owner()
    async def edit_player_interactive(self, ctx: commands.Context, target: discord.Member):
        """Membuka panel interaktif untuk mengedit data pemain."""
        view = EditPlayerView(self.bot, ctx, target)
        embed = await view.create_embed()
        
        if "Error" in embed.title:
            await ctx.send(embed=embed)
        else:
            await ctx.send(embed=embed, view=view)

    @commands.command(name="force_give")
    @commands.is_owner() # Hanya owner bot yang bisa pakai
    async def force_give_cmd(self, ctx, target: discord.Member, type: str, item_id: int):
        """
        Mengembalikan item/title yang hilang.
        Cara pakai: !force_give @Player title 5001
        Cara pakai: !force_give @Player item 101
        """
        from database import add_title_to_player, get_player_inventory, update_player_data
        import json

        if type.lower() == "title":
            # Kembalikan Title
            await add_title_to_player(self.bot.db, target.id, item_id)
            await ctx.send(f"✅ Berhasil memaksa masuk **Title ID {item_id}** ke akun **{target.display_name}**.")
        
        elif type.lower() in ["item", "fish", "rod"]:
            # Kembalikan Item/Ikan/Joran (semua masuk inventory)
            inventory = await get_player_inventory(self.bot.db, target.id)
            inventory.append(item_id)
            await update_player_data(self.bot.db, target.id, inventory=json.dumps(inventory))
            await ctx.send(f"✅ Berhasil memaksa masuk **Item ID {item_id}** ke inventory **{target.display_name}**.")
        
        else:
            await ctx.send("❌ Tipe salah! Gunakan: `title` atau `item`.")

async def setup(bot: commands.Bot):
    await bot.add_cog(DevCog(bot))