import discord
from discord.ui import Select, View
from discord.ext import commands
import datetime
import math  # <--- [PENTING] Wajib ada agar tidak error

# Impor fungsi dan variabel yang diperlukan
# Pastikan path import ini sesuai dengan struktur foldermu
from database import get_all_player_data
from ._utils import BotColors

# --- Helper Function Static (RUMUS BARU) ---
def _get_level_from_exp_static(total_exp: int) -> int:
    """
    Versi statis dari helper level untuk digunakan di luar kelas Cog.
    Menggunakan rumus Polynomial: Level = sqrt(EXP / 100) + 1
    """
    if total_exp < 0: total_exp = 0
    # Rumus ini hanya mengembalikan SATU angka (Level)
    return int(math.sqrt(total_exp / 100)) + 1

# --- Helper Function untuk Membuat Embed ---
async def create_leaderboard_embed(bot: commands.Bot, author: discord.User, all_players_data: list, category: str) -> discord.Embed:
    title_map = {
        "level": "🏆 Papan Peringkat - Level Tertinggi",
        "subs": "👥 Papan Peringkat - Subscriber Terbanyak",
        "prisma": "💎 Papan Peringkat - Prisma Terbanyak",
        "pvp_wins": "⚔️ Papan Peringkat - Juara Arena (PvP Wins)",
        "titles": "📚 Papan Peringkat - Kolektor Title",
        "power": "⚡ Papan Peringkat - Power Score"
    }
    
    # --- Logika Sorting ---
    if category == "power":
        # Dapatkan ProfileCog untuk menggunakan fungsi kalkulasi statnya.
        # Pastikan nama Cog di main.py atau setup kamu adalah "Profil"
        profile_cog = bot.get_cog("Profil")
        
        # Fallback jika ProfileCog belum siap/error, agar lb tidak crash total
        if not profile_cog:
            # Kita hitung kasar saja jika cog tidak ketemu
            for player in all_players_data:
                # Rumus kasar tanpa bonus item/title jika cog error
                base_power = (player.get('base_hp', 100) / 5) + (player.get('base_atk', 10) * 2)
                player['power_score'] = base_power
        else:
            # Hitung Power Score Detail
            for player in all_players_data:
                user_id = player.get('user_id')
                if not user_id:
                    player['power_score'] = 0
                    continue

                # Ambil total stat dari ProfileCog
                try:
                    total_stats, _ = await profile_cog._get_total_and_bonus_stats(user_id, player)
                    power_score = (
                        (total_stats.get('hp', 100) / 5) +             
                        (total_stats.get('atk', 10) * 2) +             
                        (total_stats.get('def', 5) * 1.5) +            
                        (total_stats.get('spd', 10) * 1.2) +           
                        (total_stats.get('crit_rate', 0.05) * 200) +   
                        ((total_stats.get('crit_dmg', 1.5) - 1.5) * 100) 
                    )
                    player['power_score'] = power_score
                except Exception as e:
                    print(f"Error calculating power for {user_id}: {e}")
                    player['power_score'] = 0
                    
        sort_key = "power_score"

    elif category == "titles":
        sort_key = "title_count"
    else:
        sort_key_map = {"level": "exp", "subs": "subscribers", "prisma": "prisma", "pvp_wins": "pvp_wins"}
        sort_key = sort_key_map.get(category, "exp") # Default ke exp jika key tidak ada

    # Lakukan Sorting
    sorted_players = sorted(all_players_data, key=lambda p: p.get(sort_key, 0), reverse=True)

    embed = discord.Embed(title=title_map.get(category, "Leaderboard"), color=BotColors.SUCCESS, timestamp=datetime.datetime.utcnow())

    description_lines = []
    rank_emojis = ["🥇", "🥈", "🥉"]

    # Loop Top 10
    for rank, player_data in enumerate(sorted_players[:10], start=1):
        user = bot.get_user(player_data.get('user_id'))
        # Jika user tidak ditemukan di cache bot (misal sudah keluar server), pakai nama placeholder atau skip
        display_name = user.display_name if user else f"User#{player_data.get('user_id')}"

        rank_display = rank_emojis[rank - 1] if rank <= 3 else f"**`#{rank: <2}`**"
        
        # Logika Tampilan
        if category == "level":
            total_exp = player_data.get('exp', 0)
            
            # [PERBAIKAN UTAMA DI SINI]
            # Kode lama: level, _, _ = ... (Crash karena fungsi baru cuma return 1 nilai)
            # Kode baru: level = ...
            level = _get_level_from_exp_static(total_exp)
            
            description_lines.append(f"{rank_display} {display_name} - **Level {level}** ({total_exp:,} EXP)")
        elif category == "subs":
            description_lines.append(f"{rank_display} {display_name} - **{player_data.get('subscribers', 0):,}** Subscribers")
        elif category == "prisma":
            description_lines.append(f"{rank_display} {display_name} - **{player_data.get('prisma', 0):,}** 💎")
        elif category == "pvp_wins":
            description_lines.append(f"{rank_display} {display_name} - **{player_data.get('pvp_wins', 0):,}** Kemenangan")
        elif category == "titles":
            description_lines.append(f"{rank_display} {display_name} - **{player_data.get('title_count', 0)}** Title")
        elif category == "power":
            description_lines.append(f"{rank_display} {display_name} - **{int(player_data.get('power_score', 0)):,}** Power")

    embed.description = "\n".join(description_lines) or "Papan peringkat ini kosong."

    # Menampilkan peringkat pengguna sendiri jika di luar top 10
    try:
        user_rank_index = next(i for i, p in enumerate(sorted_players) if p.get('user_id') == author.id)
        if user_rank_index >= 10:
            user_rank = user_rank_index + 1
            user_data = sorted_players[user_rank_index]
            value = ""
            if category == "level":
                # [PERBAIKAN DI SINI JUGA]
                level = _get_level_from_exp_static(user_data.get('exp', 0))
                value = f"Peringkat Anda: **#{user_rank}** dengan **Level {level}**"
            elif category == "subs":
                value = f"Peringkat Anda: **#{user_rank}** dengan **{user_data.get('subscribers', 0):,}** Subscribers"
            elif category == "prisma":
                value = f"Peringkat Anda: **#{user_rank}** dengan **{user_data.get('prisma', 0):,}** 💎"
            elif category == "pvp_wins":
                value = f"Peringkat Anda: **#{user_rank}** dengan **{user_data.get('pvp_wins', 0):,}** Kemenangan"
            elif category == "titles":
                value = f"Peringkat Anda: **#{user_rank}** dengan **{user_data.get('title_count', 0)}** Title"
            elif category == "power":
                 value = f"Peringkat Anda: **#{user_rank}** dengan **{int(user_data.get('power_score', 0)):,}** Power"

            if value:
                embed.add_field(name="Posisi Anda", value=value, inline=False)
    except StopIteration:
        pass # User tidak ada di database

    embed.set_footer(text=f"Diminta oleh {author.display_name}", icon_url=author.display_avatar.url)
    return embed

# --- Kelas View untuk Dropdown ---
class LeaderboardView(View):
    def __init__(self, bot: commands.Bot, author: discord.User, all_data: list):
        super().__init__(timeout=180.0)
        self.bot = bot
        self.author = author
        self.all_data = all_data
        self.message = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("Anda tidak bisa mengontrol papan peringkat ini.", ephemeral=True)
            return False
        return True

    async def on_timeout(self) -> None:
        if self.message:
            for item in self.children:
                item.disabled = True
            try:
                await self.message.edit(view=self)
            except discord.NotFound:
                pass

    @discord.ui.select(
        placeholder="Pilih kategori papan peringkat...",
        options=[
            discord.SelectOption(label="Level Tertinggi", value="level", description="Peringkat berdasarkan EXP dan Level.", emoji="🏆"),
            discord.SelectOption(label="Subscriber Terbanyak", value="subs", description="Peringkat berdasarkan jumlah subscriber.", emoji="👥"),
            discord.SelectOption(label="Prisma Terbanyak", value="prisma", description="Peringkat berdasarkan jumlah Prisma.", emoji="💎"),
            discord.SelectOption(label="Juara Arena (PvP Wins)", value="pvp_wins", description="Peringkat berdasarkan memenangkan pvp.",emoji="⚔️"),
            discord.SelectOption(label="Kolektor Title", value="titles", description="Peringkat berdasarkan jumlah title yang dimiliki.",emoji="📚"),
            discord.SelectOption(label="Power Score", value="power", description="Peringkat berdasarkan jumlah Power score player.",emoji="⚡"),
        ]
    )
    async def select_callback(self, interaction: discord.Interaction, select: Select):
        category = select.values[0]
        # Defer untuk mencegah 'Interaction Failed' jika kalkulasi lambat
        await interaction.response.defer()
        
        new_embed = await create_leaderboard_embed(self.bot, self.author, self.all_data, category)
        await interaction.edit_original_response(embed=new_embed, view=self)


# --- Kelas Cog Utama ---
class Leaderboard(commands.Cog, name="Papan Peringkat"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="leaderboard", aliases=["lb", "top"])
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def leaderboard(self, ctx: commands.Context):
        async with ctx.typing():
            try:
                all_players_data = await get_all_player_data(self.bot.db)

                if not all_players_data:
                    await ctx.send("Belum ada pemain di papan peringkat.")
                    return
                
                view = LeaderboardView(bot=self.bot, author=ctx.author, all_data=all_players_data)
                # Default view: Level
                initial_embed = await create_leaderboard_embed(self.bot, ctx.author, all_players_data, "level")
                
                message = await ctx.send(embed=initial_embed, view=view)
                view.message = message
            except Exception as e:
                # Tangkap error agar tidak silent fail
                await ctx.send(f"⚠️ Terjadi kesalahan saat memuat leaderboard: `{e}`")
                print(f"Error LB: {e}")

async def setup(bot: commands.Bot):
    await bot.add_cog(Leaderboard(bot))