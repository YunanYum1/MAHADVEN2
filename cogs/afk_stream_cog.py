import discord
from discord.ext import commands, tasks
import os
import asyncio
import random
import math
from datetime import datetime, timedelta

from database import get_player_data, update_player_data
from ._utils import BotColors

class AFKStreamCog(commands.Cog, name="AFK Stream"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        
        # PERBAIKAN 1: Konsisten menggunakan self.afk_vc_ids (plural/list)
        self.afk_vc_ids = [] 
        self.afk_log_id = None
        self.dashboard_message_id = None 
        
        try:
            # PERBAIKAN 2: Baca ID Channel AFK (Anggap ENV bisa berisi 1 ID saja, atau ubah ENV loading jika perlu banyak)
            single_vc_id = int(os.getenv("AFK_VOICE_CHANNEL_ID", 0))
            if single_vc_id:
                self.afk_vc_ids.append(single_vc_id)
                
            # PERBAIKAN 3: Pastikan membaca AFK_LOG_CHANNEL_ID, bukan AFK_VOICE_CHANNEL_ID lagi
            self.afk_log_id = int(os.getenv("AFK_LOG_CHANNEL_ID", 0))
        except ValueError:
            # Jika gagal konversi ke int (misal ENV tidak di-set), nilainya tetap None/0
            pass

        # --- KONFIGURASI REWARD ---
        self.EXP_PER_TICK = 30 
        self.PRISMA_PER_TICK = 20
        self.INTERVAL_MINUTES = 15

        # Flavor text untuk suasana
        self.flavor_texts = [
            "🎵 Memutar: Lofi Hip Hop Beats to Relax/Grind to...",
            "💤 Ssst... Jangan berisik, mereka sedang farming dalam tidur...",
            "🌙 Mengumpulkan energi kosmik dari keheningan...",
            "✨ Grinding tanpa henti, bahkan dalam mimpi...",
            "🎧 Mode Fokus: Aktif. Gangguan: Minimal.",
            "🌊 Suara ombak menemanimu berlatih..."
        ]

        self.afk_farming_loop.start()

    def cog_unload(self):
        self.afk_farming_loop.cancel()

    # --- HELPER: MENGHITUNG LEVEL ---
    def _get_level_from_exp(self, total_exp: int) -> int:
        if total_exp < 0: total_exp = 0
        return int(math.sqrt(total_exp / 100)) + 1

    # --- HELPER BARU: SCAN STATUS MEMBER DI VC ---
    async def _get_vc_status(self):
        # PERBAIKAN 4: Menggunakan self.afk_vc_ids
        if not self.afk_vc_ids: return [], []
        
        active_farmers = [] # List of tuples: (Member Object, Display Name, Level, Player Data)
        disqualified_members = [] # List string description
        
        # Loop melalui semua channel AFK yang terdaftar
        for vc_id in self.afk_vc_ids:
            voice_channel = self.bot.get_channel(vc_id)
            if not voice_channel or not isinstance(voice_channel, discord.VoiceChannel): continue

            for member in voice_channel.members:
                if member.bot: continue

                # Cek Syarat Visual
                is_muted = member.voice.self_mute or member.voice.mute
                is_deafened = member.voice.self_deaf or member.voice.deaf
                
                reason = []
                if is_muted: reason.append("Mic Off 🔇")
                if is_deafened: reason.append("Deafen 🎧")

                # Ambil Data DB (Cukup cepat untuk realtime)
                player_data = await get_player_data(self.bot.db, member.id)
                
                # Syarat Debut (Harus memiliki title yang ter-equip)
                if not (player_data and player_data.get('equipped_title_id')):
                    reason.append("Belum Debut 👶")

                if not reason:
                    # Hitung level saat ini untuk ditampilkan
                    level = self._get_level_from_exp(player_data.get('exp', 0))
                    # Tambahkan ke list jika belum ada, untuk menghindari duplikasi jika user pindah antar VC AFK
                    if member.id not in [a[0].id for a in active_farmers]:
                        active_farmers.append((member, member.display_name, level, player_data))
                else:
                    # Tambahkan ke disqualified jika belum ada (agar tidak menumpuk)
                    desc = f"{member.display_name} ({', '.join(reason)})"
                    if desc not in disqualified_members:
                        disqualified_members.append(desc)
        
        return active_farmers, disqualified_members

    # --- EVENT LISTENER: REALTIME UPDATE ---
    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        """Mendeteksi perubahan di Voice Channel secara realtime."""
        if member.bot: return
        
        # Cek apakah event terjadi di salah satu channel AFK kita
        target_ids = self.afk_vc_ids
        is_relevant = False

        if before.channel and before.channel.id in target_ids: is_relevant = True
        if after.channel and after.channel.id in target_ids: is_relevant = True

        if is_relevant:
            # Tunggu sebentar agar DB/State stabil, lalu update dashboard
            await asyncio.sleep(1)
            
            active_farmers, disqualified_members = await self._get_vc_status()
            
            # Format data untuk _update_dashboard (Hanya butuh Nama & Level)
            active_list_fmt = [(name, lvl) for _, name, lvl, _ in active_farmers]
            
            await self._update_dashboard(active_list_fmt, disqualified_members)

    # --- LOOP UTAMA: DISTRIBUSI HADIAH (15 MENIT) ---
    @tasks.loop(minutes=15)
    async def afk_farming_loop(self):
        if not self.bot.is_ready(): return

        # 1. Ambil status terkini
        active_farmers_data, disqualified_members = await self._get_vc_status()
        
        log_channel = self.bot.get_channel(self.afk_log_id) if self.afk_log_id else None
        
        dashboard_active_list = []

        # 2. Proses Hadiah hanya untuk yang Active
        for member, name, old_level_calc, player_data in active_farmers_data:
            current_exp = player_data.get('exp', 0)
            
            # Hitung level dari DB (untuk memastikan akurasi reward)
            real_old_level = self._get_level_from_exp(current_exp)

            new_exp_val = current_exp + self.EXP_PER_TICK
            new_prisma_val = player_data.get('prisma', 0) + self.PRISMA_PER_TICK
            
            new_level = self._get_level_from_exp(new_exp_val)
            
            db_updates = {
                "exp": new_exp_val,
                "prisma": new_prisma_val
            }

            # Level Up Handling
            if new_level > real_old_level:
                levels_gained = new_level - real_old_level
                hp_gain, atk_gain, def_gain, spd_gain = 15*levels_gained, 3*levels_gained, 2*levels_gained, 1*levels_gained
                
                db_updates.update({
                    "base_hp": player_data.get('base_hp', 100) + hp_gain,
                    "base_atk": player_data.get('base_atk', 10) + atk_gain,
                    "base_def": player_data.get('base_def', 5) + def_gain,
                    "base_spd": player_data.get('base_spd', 10) + spd_gain,
                    "level": new_level
                })

                if log_channel:
                    lvl_embed = discord.Embed(
                        description=f"### 🆙 LEVEL UP!\nSelamat **{member.mention}**! Kamu naik ke **Level {new_level}** dalam tidurmu! 🛌💤",
                        color=discord.Color.gold()
                    )
                    lvl_embed.set_thumbnail(url=member.display_avatar.url)
                    lvl_embed.add_field(name="Stat Bonus", value=f"❤️HP `+{hp_gain}` ⚔️ATK `+{atk_gain}` 🛡️DEF `+{def_gain}`", inline=True)
                    await log_channel.send(embed=lvl_embed, delete_after=60)

            await update_player_data(self.bot.db, member.id, **db_updates)
            
            # Masukkan ke list untuk update dashboard
            dashboard_active_list.append((name, new_level))

        # 3. Update Dashboard setelah pembagian hadiah
        await self._update_dashboard(dashboard_active_list, disqualified_members)

    async def _update_dashboard(self, active_list, disqualified_list):
        """Update pesan dashboard dengan tampilan yang lebih estetik."""
        if not self.afk_log_id: return
        log_channel = self.bot.get_channel(self.afk_log_id)
        if not log_channel: return

        # Cari pesan lama
        message = None
        if self.dashboard_message_id:
            try: message = await log_channel.fetch_message(self.dashboard_message_id)
            except discord.NotFound: pass
        
        if not message:
            async for msg in log_channel.history(limit=10):
                if msg.author == self.bot.user and msg.embeds and "🔴 LIVE" in (msg.embeds[0].title or ""):
                    message = msg; self.dashboard_message_id = msg.id; break

        # Hitung waktu drop berikutnya (Berdasarkan loop task)
        next_run = self.afk_farming_loop.next_iteration
        if next_run:
            timestamp_code = int(next_run.timestamp())
            time_str = f"<t:{timestamp_code}:R>"
        else:
            time_str = "Segera..."

        flavor = random.choice(self.flavor_texts)

        # PERBAIKAN 5: Menggunakan self.afk_vc_ids yang sudah diperbaiki
        if self.afk_vc_ids:
            channels_mention = ", ".join([f"<#{vid}>" for vid in self.afk_vc_ids])
        else:
            channels_mention = "Channel tidak set"

        embed = discord.Embed(
            title="🔴 LIVE: AFK Sleep Stream Station",
            description=f"*{flavor}*\n\nBergabunglah di {channels_mention} untuk mendapatkan Passive Income.",
            color=discord.Color.from_rgb(147, 112, 219), 
            timestamp=datetime.now()
        )
        embed.set_image(url="https://media.discordapp.net/attachments/123456789/banner_lofi_sleep.gif") 
        embed.set_thumbnail(url="https://cdn.discordapp.com/emojis/912644933537595423.png") 

        if active_list:
            active_str = "\n".join([f"💤 **{name}** `[Lv.{lvl}]`" for name, lvl in active_list])
            embed.add_field(name=f"🎧 Sedang Relaxing ({len(active_list)})", value=active_str, inline=False)
        else:
            embed.add_field(name="🎧 Sedang Relaxing", value="*Studio kosong...*", inline=False)

        if disqualified_list:
            dq_str = "\n".join([f"⚠️ **{entry}**" for entry in disqualified_list])
            embed.add_field(name="🔧 Perlu Perhatian (Syarat Gagal)", value=dq_str, inline=False)

        embed.add_field(
            name="🎁 Next Drop", 
            value=f"⏳ {time_str}\n`{self.EXP_PER_TICK} EXP` & `{self.PRISMA_PER_TICK} Prisma`", 
            inline=False
        )
        
        embed.set_footer(text=f"Interval: {self.INTERVAL_MINUTES} Menit | Syarat: Mic ON • Un-Deafen")

        if message: await message.edit(embed=embed)
        else:
            msg = await log_channel.send(embed=embed)
            self.dashboard_message_id = msg.id

    @afk_farming_loop.before_loop
    async def before_afk_loop(self):
        await self.bot.wait_until_ready()

    @commands.command(name="afkinfo")
    async def afk_info(self, ctx):
        # PERBAIKAN 6: Menggunakan self.afk_vc_ids untuk menampilkan channel
        if not self.afk_vc_ids: return await ctx.send("❌ Sistem AFK belum diatur (AFK Voice Channel ID belum diset).")
        
        vc_mentions = ", ".join([f"<#{vid}>" for vid in self.afk_vc_ids])
        
        embed = discord.Embed(title="🎧 Panduan AFK Stream", description=f"Dapatkan resource sambil istirahat di {vc_mentions}!", color=BotColors.DEFAULT)
        embed.set_thumbnail(url=ctx.author.display_avatar.url)
        
        embed.add_field(name="💰 Reward Pasif", value=f"Setiap **{self.INTERVAL_MINUTES} menit**, kamu mendapatkan:\n• `{self.EXP_PER_TICK}` EXP\n• `{self.PRISMA_PER_TICK}` Prisma", inline=False)
        embed.add_field(name="📜 Syarat Wajib", value="1. Sudah melakukan `!debut`\n2. Masuk salah satu Voice Channel di atas\n3. **Open Mic** (Tidak boleh Mute)\n4. **Open Headset** (Tidak boleh Deafen)", inline=False)
        embed.add_field(name="💡 Tips", value="Kamu bisa mematikan hardware mic kamu, tapi status di Discord harus tetap Open Mic.", inline=False)
        
        if self.afk_log_id: embed.add_field(name="📊 Dashboard", value=f"Cek status live di <#{self.afk_log_id}>")
        
        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(AFKStreamCog(bot))