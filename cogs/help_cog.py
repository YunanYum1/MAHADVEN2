import discord
from discord.ext import commands
from typing import Optional, List, Dict

# Impor BotColors dari file _utils.py untuk konsistensi
from ._utils import BotColors 

# ===================================================================================
# --- KELAS-KELAS VIEW (UI INTERAKTIF BARU) ---
# ===================================================================================

class HelpView(discord.ui.View):
    """
    View interaktif utama untuk panel bantuan.
    Mengelola state (halaman mana yang ditampilkan) dan semua komponen UI.
    """
    def __init__(self, bot: commands.Bot, ctx: commands.Context, mapping: Dict[Optional[commands.Cog], List[commands.Command]]):
        super().__init__(timeout=300)
        self.bot = bot
        self.ctx = ctx
        self.mapping = mapping
        self.current_cog: Optional[commands.Cog] = None
        self.message: Optional[discord.Message] = None
        
        # --- PENTING: GANTI URL DI BAWAH INI ---
        self.docs_url = "https://docs.google.com/document/d/1d0Lml3tQDrDQBLddOxX8DcsVHLgY5Xfs0l9a_zuOdGg/edit?usp=sharing"
        # -----------------------------------------
        
        # Panggil fungsi untuk membuat komponen awal
        self.rebuild_components()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Memastikan hanya pengguna asli yang dapat berinteraksi."""
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("Ini bukan sesi bantuan milikmu!", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        """Menonaktifkan semua komponen saat view berakhir."""
        if self.message:
            for item in self.children:
                item.disabled = True
            try:
                await self.message.edit(view=self)
            except discord.NotFound:
                pass
    
    def get_visible_cogs(self) -> List[commands.Cog]:
        """Menyaring dan mengembalikan daftar cog yang memiliki perintah yang bisa dilihat oleh pengguna."""
        visible_cogs = []
        for cog, cog_commands in self.mapping.items():
            if cog and any(not c.hidden for c in cog_commands):
                visible_cogs.append(cog)
        # Urutkan berdasarkan nama untuk konsistensi
        return sorted(visible_cogs, key=lambda c: c.qualified_name)

    def rebuild_components(self):
        """Membangun ulang semua komponen (dropdown, tombol) berdasarkan state saat ini."""
        self.clear_items()
        
        if self.current_cog is None:
            # Tampilan utama: Tampilkan dropdown
            self.add_item(HelpCategorySelect(self.get_visible_cogs()))
            self.add_item(discord.ui.Button(label="Panduan Dasar", emoji="📚", url=self.docs_url, row=1))
        else:
            # Tampilan kategori: Tampilkan tombol kembali
            self.add_item(GoBackButton())
            self.add_item(discord.ui.Button(label="Panduan Dasar", emoji="📚", url=self.docs_url, row=0))

    async def update_view(self, interaction: discord.Interaction):
        """Fungsi terpusat untuk menggambar ulang seluruh panel."""
        self.rebuild_components()
        embed = self.create_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    def create_embed(self) -> discord.Embed:
        """Membuat embed yang sesuai dengan state (utama atau kategori)."""
        prefix = self.ctx.clean_prefix
        
        if self.current_cog is None:
            # Embed Halaman Utama
            embed = discord.Embed(
                title="📚 Pusat Bantuan MAHADVEN",
                description=(
                    "Selamat datang di panggung virtual para bintang! 🌟\n\n"
                    "Pilih salah satu kategori perintah dari menu di bawah untuk melihat daftar perintah yang tersedia di dalamnya. "
                    f"Anda juga bisa mendapatkan info detail tentang perintah spesifik dengan mengetik `{prefix}help [nama_perintah]`."
                ),
                color=BotColors.DEFAULT
            )
            embed.set_thumbnail(url=self.bot.user.display_avatar.url)
            embed.set_footer(text="Pilih kategori untuk memulai.")
            return embed
        
        # Embed Halaman Kategori
        emoji_map = { "Profil": "👤", "Debut": "🔰", "Streaming": "🔴", "Pertarungan": "⚔️", "Agensi": "🏢", "Gacha": "🎰", "Toko": "🛒", "Transaksi":"⚖️", "TopUp": "💰", "Pertanian": "🌱",
                     "Informasi": "ℹ️", "Bantuan": "❓", "Turnamen": "🏆" , "Developer": "☕", "Inventaris": "👜", "Papan Peringkat": "🏆", "Misi": "📜", "Upgrade": "⚒️", "Memancing": "🎣"}
        emoji = emoji_map.get(self.current_cog.qualified_name, "⚙️")
        
        embed = discord.Embed(
            title=f"{emoji} Kategori: {self.current_cog.qualified_name}",
            description=self.current_cog.description or "Tidak ada deskripsi untuk kategori ini.",
            color=BotColors.RARE
        )
        
        # Filter perintah yang tidak tersembunyi
        visible_commands = [c for c in self.current_cog.get_commands() if not c.hidden]
        for command in sorted(visible_commands, key=lambda c: c.name):
            signature = f"`{prefix}{command.name} {command.signature}`"
            doc = command.short_doc or "Tidak ada deskripsi singkat."
            embed.add_field(name=signature, value=doc, inline=False)
            
        embed.set_footer(text=f"Gunakan `{prefix}help [nama_perintah]` untuk info lebih detail.")
        return embed

# --- Komponen UI Spesifik ---

class HelpCategorySelect(discord.ui.Select):
    """Dropdown untuk memilih kategori perintah."""
    def __init__(self, cogs: List[commands.Cog]):
        emoji_map = { "Profil": "👤", "Debut": "🔰", "Streaming": "🔴", "Pertarungan": "⚔️", "Agensi": "🏢", "Gacha": "🎰", "Toko": "🛒", "Transaksi":"⚖️", "TopUp": "💰", "Pertanian": "🌱",
                     "Informasi": "ℹ️", "Bantuan": "❓", "Turnamen": "🏆" , "Developer": "☕", "Inventaris": "👜", "Papan Peringkat": "🏆", "Misi": "📜", "Upgrade": "⚒️", "Memancing": "🎣"}
        options = [
            discord.SelectOption(
                label=cog.qualified_name,
                value=cog.qualified_name,
                emoji=emoji_map.get(cog.qualified_name, "⚙️")
            ) for cog in cogs
        ]
        super().__init__(placeholder="Pilih kategori untuk dilihat...", options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        view: HelpView = self.view
        selected_cog_name = self.values[0]
        # Cari objek cog yang sesuai dengan nama yang dipilih
        view.current_cog = view.bot.get_cog(selected_cog_name)
        await view.update_view(interaction)

class GoBackButton(discord.ui.Button):
    """Tombol untuk kembali ke halaman utama bantuan."""
    def __init__(self):
        super().__init__(label="Kembali ke Daftar Kategori", emoji="⬅️", style=discord.ButtonStyle.secondary, row=0)

    async def callback(self, interaction: discord.Interaction):
        view: HelpView = self.view
        view.current_cog = None
        await view.update_view(interaction)

# ===================================================================================
# --- COG DAN PERINTAH HELP UTAMA ---
# ===================================================================================

class HelpCog(commands.Cog, name="Bantuan"):
    """
    Menampilkan pesan bantuan yang informatif, dinamis, dan mudah digunakan.
    """
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._original_help_command = bot.help_command
        bot.help_command = MyHelpCommand()
        bot.help_command.cog = self

    def cog_unload(self):
        self.bot.help_command = self._original_help_command

class MyHelpCommand(commands.HelpCommand):
    
    # Perintah !help utama
    async def send_bot_help(self, mapping):
        view = HelpView(self.context.bot, self.context, mapping)
        initial_embed = view.create_embed()
        message = await self.get_destination().send(embed=initial_embed, view=view)
        view.message = message

    # Perintah !help <command>
    async def send_command_help(self, command: commands.Command):
        prefix = self.context.clean_prefix
        help_text = command.help or "Tidak ada deskripsi detail."
        description_lines, example_lines = [], []
        
        for line in help_text.splitlines():
            clean_line = line.strip()
            if clean_line.lower().startswith("contoh:"):
                example_lines.append(clean_line[7:].strip())
            else:
                description_lines.append(clean_line)
        
        description = "\n".join(description_lines)
        
        embed = discord.Embed(title=f"Bantuan Perintah: `{prefix}{command.name}`", description=description, color=BotColors.SUCCESS)
        if command.cog_name: embed.add_field(name="Kategori", value=f"`{command.cog_name}`", inline=True)
        if command.aliases: embed.add_field(name="Alias", value=", ".join(f"`{a}`" for a in command.aliases), inline=True)
        embed.add_field(name="Format Penggunaan", value=f"`{prefix}{command.qualified_name} {command.signature}`", inline=False)
        if example_lines: embed.add_field(name="Contoh", value="\n".join(f"`{ex.replace('{prefix}', prefix)}`" for ex in example_lines), inline=False)
        if command.cooldown: embed.add_field(name="Cooldown", value=f"{command.cooldown.rate}x setiap {command.cooldown.per:.0f} detik.", inline=False)
        
        await self.get_destination().send(embed=embed)

    # Perintah !help <cog> sekarang akan menampilkan panel interaktif
    async def send_cog_help(self, cog: commands.Cog):
        # Alihkan ke panel utama, tapi langsung pilih cog yang diminta
        mapping = {c: await self.filter_commands(c.get_commands(), sort=True) for c in self.context.bot.cogs.values()}
        view = HelpView(self.context.bot, self.context, mapping)
        view.current_cog = cog # Langsung set cog yang dipilih
        view.rebuild_components() # Pastikan komponen yang benar (tombol kembali) dibuat
        embed = view.create_embed()
        message = await self.get_destination().send(embed=embed, view=view)
        view.message = message

    async def send_error_message(self, error):
        embed = discord.Embed(title="❌ Bantuan Tidak Ditemukan", description=str(error).replace("`", ""), color=BotColors.ERROR)
        await self.context.send(embed=embed, delete_after=15)

async def setup(bot: commands.Bot):
    await bot.add_cog(HelpCog(bot))