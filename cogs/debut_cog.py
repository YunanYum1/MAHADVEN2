import discord
from discord.ext import commands
import random
import asyncio
import os
import math

from database import get_player_data, add_title_to_player, set_equipped_title, reset_player_progress
from ._utils import BotColors

# ===================================================================================
# --- KELAS VIEW INTERAKTIF ---
# ===================================================================================

class TutorialView(discord.ui.View):
    """View untuk pesan tutorial dengan satu tombol konfirmasi."""
    def __init__(self, author: discord.User):
        super().__init__(timeout=600)
        self.author = author
        self.message: discord.Message = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("Ini bukan sesi tutorialmu!", ephemeral=True)
            return False
        return True
    
    @discord.ui.button(label="Mulai Petualangan!", style=discord.ButtonStyle.success, emoji="🚀")
    async def start_adventure_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        
        final_embed = self.message.embeds[0]
        final_embed.set_footer(text="Selamat bermain! Gunakan !help untuk membuka panel bantuan.")
        await interaction.response.edit_message(embed=final_embed, view=self)
        self.stop()

    async def on_timeout(self):
        if self.message:
            for item in self.children:
                item.disabled = True
            try:
                timeout_embed = self.message.embeds[0]
                timeout_embed.set_footer(text="Sesi tutorial berakhir. Gunakan !help untuk melihat perintah.")
                await self.message.edit(embed=timeout_embed, view=self)
            except discord.NotFound:
                pass

class RerollView(discord.ui.View):
    """View untuk gacha debut dengan sistem reroll yang telah disempurnakan."""
    def __init__(self, ctx: commands.Context):
        super().__init__(timeout=300)
        self.ctx = ctx
        self.bot = ctx.bot
        self.cog = ctx.cog
        self.current_title = None
        self.rerolls_left = 10
        self.message: discord.Message = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("Ini bukan sesi gacha milikmu!", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Debut dengan Title Ini", style=discord.ButtonStyle.primary, custom_id="accept")
    async def accept_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        
        user_id = self.ctx.author.id
        title_id = self.current_title['id']

        await get_player_data(self.bot.db, user_id)
        await add_title_to_player(self.bot.db, user_id, title_id)
        await set_equipped_title(self.bot.db, user_id, title_id)

        final_embed, file = self.cog._create_title_display(self.current_title)
        final_embed.title = "🎉 Selamat Datang di Dunia MAHADVEN! 🎉"
        final_embed.set_footer(text="Karaktermu telah dibuat! Lihat panduan di bawah ini.")
        
        kwargs = {'view': self, 'attachments': []}
        if file:
            kwargs['attachments'].append(file)
        
        await interaction.response.edit_message(
            content=f"Selamat, {self.ctx.author.mention}! Kamu telah debut sebagai **{self.current_title['name']}**.", 
            embed=final_embed, 
            **kwargs
        )
        
        await self.cog.send_tutorial(self.ctx)
        self.stop()

    @discord.ui.button(label="Reroll (Sisa: 10)", style=discord.ButtonStyle.secondary, custom_id="reroll")
    async def reroll_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.rerolls_left <= 0:
            button.disabled = True
            await interaction.response.defer()
            return

        self.rerolls_left -= 1
        await interaction.response.defer()
        
        self.current_title = self.cog._get_weighted_random_title()
        new_embed, file = self.cog._create_title_display(self.current_title)
        
        button.label = f"Reroll (Sisa: {self.rerolls_left})"
        if self.rerolls_left == 0:
            button.disabled = True
        
        kwargs = {'view': self, 'attachments': []}
        if file:
            kwargs['attachments'].append(file)
            
        await interaction.edit_original_response(embed=new_embed, **kwargs)
        
    async def on_timeout(self):
        if self.message:
            for child in self.children:
                child.disabled = True
            try:
                await self.message.edit(content="Waktu memilih Title habis. Silakan gunakan `!debut` lagi.", view=self, embed=None, attachments=[])
            except discord.NotFound:
                pass

# [BARU] View untuk Konfirmasi Graduate
class GraduateConfirmationView(discord.ui.View):
    def __init__(self, ctx: commands.Context):
        super().__init__(timeout=60.0)
        self.ctx = ctx
        self.bot = ctx.bot

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("Ini bukan sesi konfirmasi milikmu!", ephemeral=True)
            return False
        return True
    
    @discord.ui.button(label="Ya, Reset Akun Saya", style=discord.ButtonStyle.danger)
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Nonaktifkan semua tombol
        for item in self.children:
            item.disabled = True
        
        await interaction.response.edit_message(content="🔄 Mereset progres akun...", view=self, embed=None)
        
        # Panggil fungsi reset dari database
        await reset_player_progress(self.bot.db, self.ctx.author.id)
        
        await asyncio.sleep(2)
        
        await interaction.edit_original_response(
            content=f"✅ **Kelulusan Berhasil!**\n{self.ctx.author.mention}, semua progres akunmu telah direset. Kamu sekarang bisa memulai petualangan baru dengan `{self.bot.command_prefix}debut`!"
        )
        self.stop()

    @discord.ui.button(label="Batal", style=discord.ButtonStyle.secondary)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="Aksi dibatalkan. Progres akunmu aman.", view=self, embed=None)
        self.stop()

    async def on_timeout(self):
        try:
            for item in self.children:
                item.disabled = True
            await self.ctx.message.edit(content="Waktu konfirmasi habis. Aksi dibatalkan.", view=self, embed=None)
        except (discord.NotFound, AttributeError):
            pass

# ===================================================================================
# --- COG UTAMA ---
# ===================================================================================

class DebutCog(commands.Cog, name="Debut"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
    
    # [BARU] Fungsi helper untuk menghitung level
    def _get_level_from_exp(self, total_exp: int) -> int:
        if total_exp < 0: total_exp = 0
        return int(math.sqrt(total_exp / 100)) + 1

    def _create_tutorial_embed(self, user: discord.User) -> discord.Embed:
        """
        [DIPERBARUI] Membuat embed tutorial yang lebih visual, informatif, dan estetik.
        """
        prefix = self.bot.command_prefix
        
        embed = discord.Embed(
            title=f"🌟 Selamat Datang di Dunia Virtual, {user.display_name}! 🌟",
            description=(
                "Debutmu telah sukses besar! Kini saatnya meniti karir menjadi legenda.\n"
                "Gunakan panduan di bawah ini untuk memulai perjalananmu."
            ),
            color=discord.Color.gold()
        )
        embed.set_thumbnail(url=user.display_avatar.url)

        # Field 1: Core Loop (Aktivitas Utama)
        embed.add_field(
            name="🔥 Aktivitas Utama (Grinding)",
            value=(
                f"> **`{prefix}latih`**\n"
                f"Lawan monster untuk mendapatkan **EXP** & **Prisma**.\n"
                f"> **`{prefix}stream`**\n"
                f"Lakukan siaran untuk mencari **Subscribers** & Uang.\n"
                f"> **`{prefix}misi`**\n"
                f"Selesaikan tugas harian untuk hadiah spesial."
            ),
            inline=True
        )

        # Field 2: Ekonomi & Power Up
        embed.add_field(
            name="💎 Ekonomi & Kekuatan",
            value=(
                f"> **`{prefix}gacha`**\n"
                f"Dapatkan **Title** (Job/Class) baru yang lebih kuat.\n"
                f"> **`{prefix}toko`**\n"
                f"Beli Equipment (Armor/Senjata) untuk bertarung.\n"
                f"> **`{prefix}tas`** / **`{prefix}inv`**\n"
                f"Kelola item dan pasang equipment-mu."
            ),
            inline=True
        )

        # Field 3: Side Activities (Fishing & PvP)
        embed.add_field(
            name="🎣 Hobi & Kompetisi",
            value=(
                f"• **`{prefix}fish`**: Memancing ikan untuk dijual atau dikoleksi di Aquarium.\n"
                f"• **`{prefix}tantang @user`**: Ajak pemain lain duel PvP.\n"
                f"• **`{prefix}turnamen`**: Ikuti kompetisi bracket (jika ada)."
            ),
            inline=False
        )

        # Field 4: Sosial & Info
        embed.add_field(
            name="🌐 Sosial & Info",
            value=(
                f"• **`{prefix}agensi`**: Gabung organisasi untuk buff permanen (Lv.10+).\n"
                f"• **`{prefix}profil`**: Cek status karaktermu.\n"
                f"• **`{prefix}lb`**: Cek Peringkat Global."
            ),
            inline=False
        )
        
        # Footer Tips
        embed.set_footer(text=f"💡 Tips: Bingung? Ketik {prefix}help untuk daftar perintah lengkap.")
        return embed

    async def send_tutorial(self, ctx: commands.Context):
        tutorial_embed = self._create_tutorial_embed(ctx.author)
        tutorial_view = TutorialView(ctx.author)
        message = await ctx.send(embed=tutorial_embed, view=tutorial_view)
        tutorial_view.message = message

    def _get_weighted_random_title(self) -> dict:
        """Mengambil satu title berdasarkan probabilitas berbobot."""
        all_titles = self.bot.titles
        weights = {"Common": 40, "Rare": 30, "Epic": 20, "Legendary": 1.5}
        
        title_population = [t for t in all_titles if t.get("rarity") in weights]
        title_weights = [weights[t.get("rarity")] for t in title_population]
        
        return random.choices(population=title_population, weights=title_weights, k=1)[0] if title_population else random.choice(all_titles)

    def _format_stats_for_debut(self, stats_dict: dict) -> str:
        """Memformat stat untuk ditampilkan di embed, hanya menampilkan stat non-nol."""
        parts = []
        if hp := stats_dict.get('hp', 0): parts.append(f"❤️HP:`{hp:+}`")
        if atk := stats_dict.get('atk', 0): parts.append(f"⚔️ATK:`{atk:+}`")
        if d := stats_dict.get('def', 0): parts.append(f"🛡️DEF:`{d:+}`")
        if spd := stats_dict.get('spd', 0): parts.append(f"💨SPD:`{spd:+}`")
        if cr := stats_dict.get('crit_rate', 0): parts.append(f"🎯Crit:`{cr*100:+.0f}%`")
        if cd := stats_dict.get('crit_damage', 0): parts.append(f"💥CDMG:`{cd*100:+.0f}%`")
        return ' | '.join(parts) or "Tidak ada bonus stat."

    def _create_title_display(self, title: dict) -> tuple[discord.Embed, discord.File | None]:
        """Membuat embed dan file gambar untuk Title."""
        rarity = title.get("rarity", "Common").capitalize()
        rarity_colors = {"Common": BotColors.COMMON, "Rare": BotColors.RARE, "Epic": BotColors.EPIC, "Legendary": BotColors.LEGENDARY}
        
        embed = discord.Embed(
            title="✨ Debut Title Terpilih! ✨",
            description=f"Takdir memilihkanmu Title:\n\n# **{title['name']}**\n_{title.get('description', '...')}_",
            color=rarity_colors.get(rarity, BotColors.DEFAULT)
        )

        file = None
        if image_filename := title.get("image_file"):
            image_path = f"assets/images/{image_filename}"
            if os.path.exists(image_path):
                file = discord.File(image_path, filename=image_filename)
                embed.set_thumbnail(url=f"attachment://{image_filename}")
        
        embed.add_field(name="Rarity", value=f"**{rarity}**", inline=False)
        
        if stats := title.get('stat_boost'):
            embed.add_field(name="📈 Bonus Status Awal", value=self._format_stats_for_debut(stats), inline=False)

        for skill in title.get('skills', []):
            skill_type = "Pasif 🧘" if skill.get('type') == 'passive' else "Aktif ⚡"
            embed.add_field(
                name=f"`{skill_type}`: {skill.get('name', '???')}",
                value=f"_{skill.get('description', '...')}_",
                inline=True
            )
            
        return embed, file

    # --- COMMAND UTAMA ---

    @commands.command(name="debut")
    @commands.cooldown(1, 900, commands.BucketType.user)
    async def debut(self, ctx: commands.Context):
        """Memulai permainan dan memilih title pertama."""
        player_data = await get_player_data(self.bot.db, ctx.author.id)

        if player_data.get("equipped_title_id") is not None:
            self.debut.reset_cooldown(ctx)
            return await ctx.send(f"Kamu sudah melakukan debut, {ctx.author.mention}! Tidak bisa debut dua kali.")

        if not self.bot.titles:
            return await ctx.send("Maaf, data titles sedang tidak tersedia. Hubungi developer.", ephemeral=True)

        msg = await ctx.send(embed=discord.Embed(title="🔮 Mencari takdirmu...", description="Menyiapkan panggung debut...", color=BotColors.DEFAULT))
        
        view = RerollView(ctx=ctx)
        first_title = self._get_weighted_random_title()
        view.current_title = first_title
        
        initial_embed, file = self._create_title_display(first_title)
        
        kwargs = {'content': None, 'embed': initial_embed, 'view': view, 'attachments': []}
        if file:
            kwargs['attachments'].append(file)

        view.message = await msg.edit(**kwargs)

    @debut.error
    async def debut_error(self, ctx, error):
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.send(f"Sabar dulu! Kamu bisa mencoba debut lagi dalam **{error.retry_after:.0f} detik**.", delete_after=10)

    # [BARU] Perintah Graduate
    @commands.command(name="graduate", aliases=["gradu"])
    @commands.cooldown(1, 60, commands.BucketType.user) # Cooldown 1 menit
    async def graduate(self, ctx: commands.Context):
        """Mereset progres akun untuk memulai dari awal (membutuhkan Level 7)."""
        player_data = await get_player_data(self.bot.db, ctx.author.id)

        # Cek 1: Apakah pemain sudah pernah debut?
        if player_data.get("equipped_title_id") is None:
            return await ctx.send(f"Kamu belum pernah debut, {ctx.author.mention}. Gunakan `{self.bot.command_prefix}debut` untuk memulai.")

        # Cek 2: Apakah level pemain sudah mencukupi?
        current_level = self._get_level_from_exp(player_data.get('exp', 0))
        required_level = 7
        if current_level < required_level:
            return await ctx.send(f"Kamu belum siap untuk 'lulus', {ctx.author.mention}. Kamu harus mencapai **Level {required_level}** terlebih dahulu. (Levelmu saat ini: **{current_level}**)")

        # Jika semua syarat terpenuhi, kirim pesan konfirmasi
        embed = discord.Embed(
            title="🎓 Konfirmasi Kelulusan 🎓",
            description=(
                "**PERINGATAN! INI AKAN MENGHAPUS SEMUA PROGRES AKUNMU!**\n\n"
                "Aksi ini akan mereset akunmu ke kondisi awal. Semua progres di bawah ini akan **HILANG PERMANEN**:\n"
                "• Level, EXP, dan Stat Dasar\n"
                "• Semua Title yang sudah didapat\n"
                "• Semua Equipment dan Item di Inventory\n"
                "• Semua Prisma dan Subscribers\n"
                "• Keanggotaan Agensi\n"
                "• Kemenangan PvP\n\n"
                "Apakah kamu benar-benar yakin ingin melanjutkan?"
            ),
            color=BotColors.ERROR
        )
        embed.set_footer(text="Aksi ini tidak dapat dibatalkan.")
        
        view = GraduateConfirmationView(ctx)
        await ctx.send(embed=embed, view=view)


async def setup(bot):
    await bot.add_cog(DebutCog(bot))