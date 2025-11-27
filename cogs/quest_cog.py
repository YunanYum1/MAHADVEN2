import discord
from discord.ext import commands, tasks
import json
from datetime import datetime, time, timedelta
import pytz
import asyncio
import os
import math # [PENTING] Diperlukan untuk rumus level

# Impor dari proyek Anda
from database import get_player_data, update_player_data
from ._utils import BotColors

# ===================================================================================
# UI CLASSES (TAMPILAN TOMBOL)
# ===================================================================================

class ClaimButton(discord.ui.Button):
    def __init__(self, quest_def: dict):
        super().__init__(
            label=f"Klaim Hadiah",
            emoji="🎁",
            style=discord.ButtonStyle.success,
            custom_id=f"claim_{quest_def['id']}",
            row=1
        )
        self.quest_def = quest_def

class QuestView(discord.ui.View):
    def __init__(self, author: discord.User, cog: 'QuestCog', active_filter: str):
        super().__init__(timeout=180.0)
        self.author = author
        self.cog = cog
        self.active_filter = active_filter
        self.message: discord.Message = None

        for child in self.children:
            if isinstance(child, discord.ui.Button):
                if (child.label == "Harian" and active_filter == 'daily') or \
                   (child.label == "Mingguan" and active_filter == 'weekly'):
                    child.disabled = True
                    child.style = discord.ButtonStyle.primary
                elif child.label in ["Harian", "Mingguan"]:
                    child.style = discord.ButtonStyle.secondary

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("🚫 Ini bukan panel misimu!", ephemeral=True)
            return False
        return True

    async def _update_view(self, interaction: discord.Interaction, quest_type: str):
        await interaction.response.defer()
        new_embed, new_view = await self.cog._build_quest_interface(interaction.user.id, quest_type)
        if new_view: new_view.message = interaction.message
        await interaction.edit_original_response(embed=new_embed, view=new_view)

    @discord.ui.button(label="Harian", emoji="📅", style=discord.ButtonStyle.secondary, row=0)
    async def daily_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._update_view(interaction, 'daily')

    @discord.ui.button(label="Mingguan", emoji="🗓️", style=discord.ButtonStyle.secondary, row=0)
    async def weekly_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._update_view(interaction, 'weekly')
    
    @discord.ui.button(label="Tutup", emoji="✖️", style=discord.ButtonStyle.danger, row=2)
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.message.delete()
        self.stop()
    
    async def on_timeout(self):
        if self.message:
            try:
                for item in self.children: item.disabled = True
                await self.message.edit(view=self)
            except discord.NotFound: pass

    async def _handle_claim(self, interaction: discord.Interaction, button: ClaimButton):
        user_id = interaction.user.id
        quest_id = button.quest_def['id']

        # Cek status klaim di DB
        async with self.cog.bot.db.execute("SELECT completed, claimed FROM player_quests WHERE user_id = ? AND quest_id = ?", (user_id, quest_id)) as cursor:
            quest_status = await cursor.fetchone()

        if not (quest_status and quest_status[0] and not quest_status[1]):
            await interaction.response.send_message("❌ Misi ini tidak bisa diklaim atau sudah diklaim.", ephemeral=True)
            await self._update_view(interaction, self.active_filter)
            return

        # Tandai sebagai claimed
        await self.cog.bot.db.execute("UPDATE player_quests SET claimed = 1 WHERE user_id = ? AND quest_id = ?", (user_id, quest_id))
        
        # --- LOGIKA REWARD & LEVEL UP ---
        rewards = button.quest_def['rewards']
        exp_gain = rewards.get('exp', 0)
        prisma_gain = rewards.get('prisma', 0)

        player_data = await get_player_data(self.cog.bot.db, user_id)
        
        current_exp = player_data.get('exp', 0)
        current_prisma = player_data.get('prisma', 0)
        old_level = player_data.get('level', 1)

        new_exp = current_exp + exp_gain
        new_prisma = current_prisma + prisma_gain
        
        # 1. Dapatkan Level Lama & Baru
        old_level = self.cog._get_level_from_exp(current_exp)
        new_level = self.cog._get_level_from_exp(new_exp)
        
        db_updates = {
            "exp": new_exp,
            "prisma": new_prisma
        }

        embed_desc = f"🎉 **HADIAH DITERIMA!**\n\n` +{exp_gain} EXP `\n` +{prisma_gain} Prisma `"
        
        # 2. Cek Level Up & Tambahkan Stat
        if new_level > old_level:
            levels_gained = new_level - old_level
            
            # Stat Gain per Level (Konsisten dengan StreamSession: 15/3/2/1)
            hp_gain = 15 * levels_gained
            atk_gain = 3 * levels_gained
            def_gain = 2 * levels_gained
            spd_gain = 1 * levels_gained
            
            # Update Stat
            db_updates.update({
                "level": new_level,
                "base_hp": player_data.get('base_hp', 100) + hp_gain,
                "base_atk": player_data.get('base_atk', 10) + atk_gain,
                "base_def": player_data.get('base_def', 5) + def_gain,
                "base_spd": player_data.get('base_spd', 10) + spd_gain
            })
            
            embed_desc += f"\n\n🆙 **LEVEL UP!**\nKamu naik ke **Level {new_level}**!\n"
            embed_desc += f"❤️HP `+{hp_gain}` ⚔️ATK `+{atk_gain}` 🛡️DEF `+{def_gain}` 💨SPD `+{spd_gain}`"

        # Simpan ke DB
        await update_player_data(self.cog.bot.db, user_id, **db_updates)
        await self.cog.bot.db.commit()

        embed_success = discord.Embed(description=embed_desc, color=discord.Color.green())
        await interaction.response.send_message(embed=embed_success, ephemeral=True)
        await self._update_view(interaction, self.active_filter)

# ===================================================================================
# QUEST COG
# ===================================================================================
class QuestCog(commands.Cog, name="Misi"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.WIB = pytz.timezone('Asia/Jakarta')
        self.RESET_TIME = time(5, 0)
        self.last_daily_reset_check = None
        
        self.quests_data = self._load_quests()
        self.all_quest_defs = {q['id']: q for q_list in self.quests_data.values() for q in q_list}
        
        self.quest_reset_loop.start()

    def cog_unload(self):
        self.quest_reset_loop.cancel()
    
    # [HELPER BARU] Rumus Level (Polynomial)
    def _get_level_from_exp(self, total_exp: int) -> int:
        """Menggunakan formula Level EXP berbasis kuadratik (100 * level^2)"""
        if total_exp < 0: total_exp = 0
        # Formula yang benar: EXP_Total = 100 * (Level - 1)^2. Level = sqrt(EXP/100) + 1
        return int(math.sqrt(total_exp / 100)) + 1 

    async def _ensure_daily_columns(self):
        """Memastikan kolom daily_streak dan last_daily_claim ada di tabel players."""
        try:
            async with self.bot.db.execute("PRAGMA table_info(players)") as cursor:
                columns = [row[1] for row in await cursor.fetchall()]
            
            if 'daily_streak' not in columns:
                await self.bot.db.execute("ALTER TABLE players ADD COLUMN daily_streak INTEGER DEFAULT 0")
            if 'last_daily_claim' not in columns:
                await self.bot.db.execute("ALTER TABLE players ADD COLUMN last_daily_claim INTEGER DEFAULT 0")
            
            await self.bot.db.commit()
        except Exception as e:
            print(f"❌ Gagal update kolom daily: {e}")

    def _load_quests(self):
        try:
            base_path = os.path.dirname(os.path.abspath(__file__))
            json_path = os.path.join(base_path, '..', 'data', 'quests.json')
            if not os.path.exists(json_path): json_path = 'data/quests.json'
            with open(json_path, 'r', encoding='utf-8') as f: return json.load(f)
        except Exception as e:
            print(f"ERROR LOAD QUESTS: {e}")
            return {"daily": [], "weekly": []}

    def _create_progress_bar(self, current, total, length=8):
        if total <= 0: return f"▱" * length
        percentage = min(current / total, 1.0)
        progress = int(percentage * length)
        return f"{'▰' * progress}{'▱' * (length - progress)}"

    def _get_quest_icon(self, quest_type):
        icons = {"PVE_WIN": "⚔️", "PVP_WIN": "🤺", "PVP_PARTICIPATE": "🛡️", "USE_SKILL": "🔥", "COMPLETE_STREAM": "🎥", "LAND_CRIT": "💥", "EARN_PRISMA": "💰", "WIN_TOURNAMENT_MATCH": "🏆", "BECOME_CHAMPION": "👑"}
        return icons.get(quest_type, "📜")

    def get_quest_date_info(self):
        now_wib = datetime.now(self.WIB)
        if now_wib.time() < self.RESET_TIME:
            quest_date = now_wib.date() - timedelta(days=1)
        else:
            quest_date = now_wib.date()
        return quest_date.strftime("%Y-%m-%d"), f"{quest_date.isocalendar().year}-{quest_date.isocalendar().week}"

    async def _ensure_quests_for_user(self, user_id: int, quest_type: str):
        daily_period, weekly_period = self.get_quest_date_info()
        current_period = daily_period if quest_type == 'daily' else weekly_period
        target_quests = self.quests_data.get(quest_type, [])
        
        if not target_quests: return

        pattern = 'DAILY_%' if quest_type == 'daily' else 'WEEKLY_%'
        await self.bot.db.execute("DELETE FROM player_quests WHERE user_id = ? AND quest_id LIKE ? AND assigned_period != ?", (user_id, pattern, current_period))
        
        quests_to_insert = [(user_id, q['id'], current_period) for q in target_quests]
        if quests_to_insert:
            await self.bot.db.executemany("INSERT OR IGNORE INTO player_quests (user_id, quest_id, assigned_period) VALUES (?, ?, ?)", quests_to_insert)
            await self.bot.db.commit()

    @tasks.loop(minutes=30)
    async def quest_reset_loop(self):
        daily_period, _ = self.get_quest_date_info()
        if self.last_daily_reset_check is None: self.last_daily_reset_check = daily_period
        if daily_period > self.last_daily_reset_check: self.last_daily_reset_check = daily_period

    @quest_reset_loop.before_loop
    async def before_loop(self):
        await self.bot.wait_until_ready()
        await self._ensure_daily_columns()

    async def update_quest_progress(self, user_id, quest_type, amount=1):
        if not self.bot.db: return
        await self._ensure_quests_for_user(user_id, 'daily')
        await self._ensure_quests_for_user(user_id, 'weekly')

        async with self.bot.db.execute("SELECT quest_id, progress FROM player_quests WHERE user_id = ? AND completed = 0", (user_id,)) as cursor:
            active_quests = await cursor.fetchall()
        
        if not active_quests: return

        updates, completions = [], []
        for quest_id, current_progress in active_quests:
            quest_def = self.all_quest_defs.get(quest_id)
            if not quest_def: continue
            
            if quest_def['type'] == quest_type:
                target = quest_def['target']
                new_prog = min(current_progress + amount, target)
                if new_prog > current_progress:
                    updates.append((new_prog, user_id, quest_id))
                    if new_prog >= target: completions.append((user_id, quest_id))

        if updates: await self.bot.db.executemany("UPDATE player_quests SET progress = ? WHERE user_id = ? AND quest_id = ?", updates)
        if completions: await self.bot.db.executemany("UPDATE player_quests SET completed = 1 WHERE user_id = ? AND quest_id = ?", completions)
        if updates or completions: await self.bot.db.commit()

    async def _build_quest_interface(self, user_id, active_filter):
        await self._ensure_quests_for_user(user_id, active_filter)

        async with self.bot.db.execute("SELECT quest_id, progress, completed, claimed FROM player_quests WHERE user_id = ?", (user_id,)) as cursor:
            player_quests_db = await cursor.fetchall()
        
        player_data = await get_player_data(self.bot.db, user_id)
        has_unfinished = any(not q[2] for q in player_quests_db if q[0].lower().startswith(active_filter))
        embed_color = discord.Color.gold() if not has_unfinished and player_quests_db else discord.Color.from_rgb(59, 130, 246)
        title_text = "📅 Misi Harian" if active_filter == 'daily' else "🗓️ Misi Mingguan"
        
        embed = discord.Embed(title=f"{title_text}", description=f"*Reset pada pukul {self.RESET_TIME.strftime('%H:%M')} WIB*", color=embed_color)
        if player_data:
            user = self.bot.get_user(user_id)
            if user: embed.set_thumbnail(url=user.display_avatar.url)
            embed.description += f"\n\n💳 **Saldo Anda:** `{player_data.get('prisma', 0):,} Prisma`"

        view = QuestView(self.bot.get_user(user_id), self, active_filter)
        found_quests = False
        sorted_quests = sorted(player_quests_db, key=lambda x: (x[3], -x[2]))

        for quest_row in sorted_quests:
            quest_id, progress, completed, claimed = quest_row
            if not quest_id.lower().startswith(active_filter) or claimed: continue

            quest_def = self.all_quest_defs.get(quest_id)
            if not quest_def: continue

            found_quests = True
            target = quest_def['target']
            icon = self._get_quest_icon(quest_def['type'])
            p_bar = self._create_progress_bar(progress, target)
            percent = int((progress / target) * 100)
            rewards_txt = f"` +{quest_def['rewards']['exp']} EXP ` ` +{quest_def['rewards']['prisma']} 💎 `"
            
            if completed:
                status_txt, progress_txt, border_color = f"✅ **SIAP KLAIM!**", f"**MAKSIMAL**", "🟩"
            else:
                status_txt, progress_txt, border_color = f"{p_bar} `{percent}%`", f"**{progress}/{target}**", "🟦"

            field_value = f"> {quest_def['description']}\n> {status_txt}\n> 🎁 **Hadiah:** {rewards_txt}"
            embed.add_field(name=f"{border_color} {icon} {quest_def['title']} {progress_txt}", value=field_value, inline=False)

            if completed and not claimed:
                btn = ClaimButton(quest_def)
                async def cb(i, b=btn): await view._handle_claim(i, b)
                btn.callback = cb
                view.add_item(btn)
        
        if not found_quests:
            has_finished_all = any(q[0].lower().startswith(active_filter) and q[3] for q in player_quests_db)
            if has_finished_all:
                embed.description += "\n\n🌟 **LUAR BIASA!**\nSemua misi untuk periode ini telah selesai. Kembalilah nanti!"
                embed.set_image(url="https://cdn.discordapp.com/attachments/1417401606353195018/1441707295372345405/Salinan_dari_Salinan_dari_SPONSOR_PREVIEW.png?ex=6922c60c&is=6921748c&hm=fe2cdd415cdc41d7fc81942f11b746c40ddc6f0232fe396927b65851fdacb7be&")
            else:
                embed.description += "\n\n💤 **Tidak ada misi aktif.**\n(Pastikan database telah diperbaiki)"

        return embed, view

    @commands.hybrid_command(name="misi", aliases=["quest", "quests"])
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def quest_command(self, ctx: commands.Context):
        await get_player_data(self.bot.db, ctx.author.id)
        embed, view = await self._build_quest_interface(ctx.author.id, 'daily')
        msg = await ctx.send(embed=embed, view=view)
        if view: view.message = msg

    @commands.command(name="daily")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def daily(self, ctx):
        user_id = ctx.author.id
        player_data = await get_player_data(self.bot.db, user_id)
        if not player_data.get('equipped_title_id'): return await ctx.send("Kamu harus debut dulu dengan `!debut`!")

        now_wib = datetime.now(self.WIB)
        today_reset_time = now_wib.replace(hour=self.RESET_TIME.hour, minute=self.RESET_TIME.minute, second=0, microsecond=0)

        if now_wib < today_reset_time:
            active_reset = today_reset_time - timedelta(days=1)
            next_reset = today_reset_time
        else:
            active_reset = today_reset_time
            next_reset = today_reset_time + timedelta(days=1)

        active_reset_ts = int(active_reset.timestamp())
        next_reset_ts = int(next_reset.timestamp())
        last_claim_ts = player_data.get('last_daily_claim', 0) or 0

        if last_claim_ts >= active_reset_ts:
            return await ctx.send(f"⏳ **Kamu sudah absen hari ini!**\nReset harian: **{self.RESET_TIME.strftime('%H:%M')} WIB**.\nKembali lagi: <t:{next_reset_ts}:R>")
        
        streak = player_data.get('daily_streak', 0) or 0
        previous_cycle_start_ts = int((active_reset - timedelta(days=1)).timestamp())

        if last_claim_ts < previous_cycle_start_ts and last_claim_ts != 0:
            streak = 0
        
        streak += 1
        base_prisma = 300
        bonus_prisma = min(streak * 50, 500)
        total_prisma = base_prisma + bonus_prisma
        
        # [BARU] Tambahkan sedikit EXP ke Daily (100 EXP)
        exp_gain = 100 

        # --- LOGIKA LEVEL UP ---
        current_exp = player_data.get('exp', 0)
        
        # 1. Dapatkan Level Lama & Baru (Menggunakan formula yang benar)
        old_level = self._get_level_from_exp(current_exp)
        
        new_exp = current_exp + exp_gain
        new_level = self._get_level_from_exp(new_exp)
        new_prisma = player_data.get('prisma', 0) + total_prisma
        current_ts = int(now_wib.timestamp())

        # Update Daily Specific Columns + Resource (EXP dan Prisma baru)
        await self.bot.db.execute(
            "UPDATE players SET prisma = ?, exp = ?, last_daily_claim = ?, daily_streak = ? WHERE user_id = ?", 
            (new_prisma, new_exp, current_ts, streak, user_id)
        )
        
        # Update Level & Stats jika naik level
        embed_extra = ""
        if new_level > old_level:
            levels_gained = new_level - old_level
            
            # Stat Gain per Level (Konsisten: 15/3/2/1)
            hp_gain = 15 * levels_gained
            atk_gain = 3 * levels_gained
            def_gain = 2 * levels_gained
            spd_gain = 1 * levels_gained
            
            await update_player_data(
                self.bot.db, user_id,
                level=new_level,
                base_hp=player_data.get('base_hp', 100) + hp_gain,
                base_atk=player_data.get('base_atk', 10) + atk_gain,
                base_def=player_data.get('base_def', 5) + def_gain,
                base_spd=player_data.get('base_spd', 10) + spd_gain
            )
            embed_extra = f"\n\n🆙 **LEVEL UP!** (Lv.{new_level})\nStatus naik: ❤️+{hp_gain} ⚔️+{atk_gain} 🛡️+{def_gain} 💨+{spd_gain}"
        
        await self.bot.db.commit()

        embed = discord.Embed(title="📅 Absensi Harian Berhasil!", color=BotColors.SUCCESS)
        embed.description = f"Reset harian pukul **{self.RESET_TIME.strftime('%H:%M')} WIB**.{embed_extra}"
        embed.add_field(name="💰 Hadiah", value=f"**+{total_prisma}** Prisma\n**+{exp_gain}** EXP", inline=True)
        embed.add_field(name="🔥 Streak", value=f"**{streak}** Hari", inline=True)
        
        if streak > 1: embed.set_footer(text=f"Bonus Streak aktif: +{bonus_prisma} Prisma")
        else: embed.set_footer(text="Selamat datang! Jangan lupa absen besok.")
            
        await ctx.send(embed=embed)

async def setup(bot: commands.Bot):
    await bot.add_cog(QuestCog(bot))