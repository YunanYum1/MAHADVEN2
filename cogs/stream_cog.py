import discord
from discord.ext import commands
import random
import asyncio
import datetime
import math
import json
from collections import deque

# Impor dari proyek Anda
from database import get_player_data, update_player_data
from ._utils import BotColors

# ===================================================================================
# VIEW UNDANGAN COLLAB
# ===================================================================================

class CollabInviteView(discord.ui.View):
    def __init__(self, host: discord.User, guest: discord.User):
        super().__init__(timeout=30)
        self.host = host
        self.guest = guest
        self.value = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.guest.id:
            await interaction.response.send_message(f"Undangan ini khusus untuk {self.guest.mention}!", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Terima Collab", style=discord.ButtonStyle.success, emoji="🤝")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = True
        for item in self.children: item.disabled = True
        await interaction.response.edit_message(content=f"✅ **{self.guest.display_name}** menerima undangan! Menyiapkan stream...", view=self)
        self.stop()

    @discord.ui.button(label="Tolak", style=discord.ButtonStyle.danger, emoji="✖️")
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = False
        for item in self.children: item.disabled = True
        await interaction.response.edit_message(content=f"❌ **{self.guest.display_name}** menolak undangan.", view=self)
        self.stop()

# ===================================================================================
# KELAS PENGELOLA SESI STREAMING
# ===================================================================================

class StreamSession:
    def __init__(self, bot: commands.Bot, user: discord.User, channel: discord.TextChannel, stream_type: str, partner: discord.User = None):
        self.bot = bot
        self.user = user
        self.partner = partner # Player kedua (Opsional)
        self.channel = channel
        self.stream_type = stream_type
        
        # Judul Stream
        base_titles = self.bot.stream_data['stream_titles'].get(stream_type, ["Stream Santai"])
        self.stream_title = random.choice(base_titles)
        if self.partner:
            self.stream_title = f"COLLAB: {self.stream_title} ft. {self.partner.display_name}"

        self.message: discord.Message = None
        self.is_running = False
        self.end_time: datetime.datetime = None

        self.chat_message_queue = deque()
        self.chat_log_display = deque(maxlen=4)
        
        self.last_event: dict = None
        self.hype, self.current_viewers, self.peak_viewers = 50.0, 0, 0
        self.total_exp, self.total_prisma, self.total_subs_gain = 0, 0, 0
        
        # Status Agensi
        self.agency_synergy = False

    def _get_level_progress_static(self, total_exp: int) -> tuple[int, int, int]:
        """Versi statis dari _get_level_progress yang juga ada di ProfileCog."""
        if total_exp < 0: total_exp = 0
        
        # 1. Hitung Level saat ini (Menggunakan formula yang sama dengan ProfileCog)
        level = int(math.sqrt(total_exp / 100)) + 1
        
        # 2. Hitung Total EXP untuk mencapai level ini (Batas Bawah)
        current_level_base_exp = 100 * ((level - 1) ** 2)
        
        # 3. Hitung Total EXP untuk level berikutnya (Batas Atas)
        next_level_exp_total = 100 * (level ** 2)
        
        # 4. EXP yang "sedang berjalan" di level ini
        current_progress = total_exp - current_level_base_exp
        
        # 5. EXP yang dibutuhkan dari awal level ini ke level berikutnya
        needed_for_next = next_level_exp_total - current_level_base_exp
        
        return level, current_progress, needed_for_next

    def _create_progress_bar(self, current: float, total: float, length: int = 10) -> str:
        progress = int((current / total) * length)
        return f"[{'█' * progress}{'─' * (length - progress)}]"

    async def start(self):
        self.is_running = True
        # Durasi: 60 detik (Solo), 90 detik (Collab)
        duration_seconds = 90 if self.partner else 60
        
        self.end_time = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=duration_seconds)
        
        desc = f"Persiapan terakhir untuk **{self.stream_title}**.\n"
        if self.partner:
            desc += f"Menampilkan: {self.user.mention} & {self.partner.mention}\n"
        desc += "Hitung mundur dimulai!"

        embed = discord.Embed(
            title="SIARAN AKAN DIMULAI...",
            description=desc,
            color=BotColors.WARNING
        ).set_author(name=self.user.display_name, icon_url=self.user.display_avatar.url)
        
        self.message = await self.channel.send(embed=embed)
        await asyncio.sleep(4)
        
        while datetime.datetime.now(datetime.timezone.utc) < self.end_time and self.is_running:
            await self._process_tick()
            await self._render_tick_with_animation()
        
        await self._end_stream()

    def _generate_chat_queue(self, event_type: str):
        self.chat_message_queue.clear()
        chat_data = self.bot.stream_data['chat_data']
        usernames = chat_data.get("usernames", ["Viewer"])
        templates = chat_data.get("chat_templates", {}).get(event_type, ["..."])

        if self.partner:
            collab_templates = chat_data.get("chat_templates", {}).get("collab_reaction", [])
            # Gabungkan list (70% template event, 30% template collab)
            if collab_templates:
                templates = templates + collab_templates
        
        # Lebih banyak chat jika collab
        msg_range = (3, 5) if self.partner else ((2, 4) if "spike" in event_type else (1, 3))
        num_messages = random.randint(*msg_range)
        
        for _ in range(num_messages):
            user = random.choice(usernames)
            comment = random.choice(templates)
            self.chat_message_queue.append((user, comment))

    async def _process_tick(self):
        # Ambil data Host
        player_data = await get_player_data(self.bot.db, self.user.id)
        subscribers = player_data.get('subscribers', 1)
        level = player_data.get('level', 1)
        agency_id = player_data.get('agency_id')
        
        # Ambil data Partner (jika ada) & Hitung Rata-rata
        if self.partner:
            partner_data = await get_player_data(self.bot.db, self.partner.id)
            p_subs = partner_data.get('subscribers', 1)
            p_agency = partner_data.get('agency_id')
            
            # Gabungkan Subscribers (Host + 50% Partner) untuk perhitungan viewers
            subscribers = int(subscribers + (p_subs * 0.5))
            level = int((level + partner_data.get('level', 1)) / 2) # Rata-rata level
            
            # Cek Synergy Agensi
            if agency_id and p_agency and agency_id == p_agency:
                self.agency_synergy = True
        
        # --- LOGIKA HYPE & VIEWERS ---
        decay = 0.5
        if self.agency_synergy: decay = 0.2 # Hype lebih tahan lama jika satu agensi
        
        self.hype = max(0, self.hype - decay)
        hype_factor = (self.hype - 50) / 50.0
        
        # Viewers Calculation
        viewer_change = int((subscribers * 0.1) * hype_factor) + random.randint(-max(1, int(self.current_viewers * 0.02)), max(2, int(self.current_viewers * 0.02)))
        self.current_viewers = max(5, self.current_viewers + viewer_change)
        
        self.last_event = random.choice(self.bot.stream_data['stream_events'])
        self._generate_chat_queue(self.last_event['type'])
        
        hype_gain = self.last_event["hype_impact"]
        if self.agency_synergy: hype_gain *= 1.2 # Bonus hype gain
        self.hype = max(0, min(100, self.hype + hype_gain))
        
        if self.last_event['type'] == 'positive_spike': 
            self.current_viewers += random.randint(*self.last_event['viewer_spike'])
        elif self.last_event['type'] == 'negative_spike': 
            self.current_viewers -= int(self.current_viewers * self.last_event.get('viewer_drop_percentage', 0.5))
        
        self.peak_viewers = max(self.peak_viewers, self.current_viewers)
        
        # --- PERHITUNGAN EXP & PRISMA ---
        base_exp = self.last_event.get("exp_bonus", 0)
        base_prisma = self.last_event.get("prisma_bonus", 0)
        
        level_mult = 1 + (level / 50.0)
        hype_mult = 0.5 + (self.hype / 200.0)
        subscriber_mult = 1 + (math.log10(subscribers + 1) / 2.5)
        GLOBAL_SCALING_DIVISOR = 2.2

        raw_exp = (base_exp * level_mult * hype_mult * subscriber_mult) / GLOBAL_SCALING_DIVISOR
        raw_prisma = (base_prisma * level_mult * hype_mult * subscriber_mult) / GLOBAL_SCALING_DIVISOR

        exp_multiplier = 1.0
        prisma_multiplier = 1.0

        # Multiplier Tipe Stream
        if self.stream_type == "gaming":
            exp_multiplier *= 1.5; prisma_multiplier *= 0.6
        elif self.stream_type == "donathon":
            exp_multiplier *= 0.6; prisma_multiplier *= 1.5
        # PERBAIKAN UNTUK SOLO TALK (Agar tidak terlalu dominan)
        elif self.stream_type == "free_talk":
             exp_multiplier *= 0.8; prisma_multiplier *= 0.8
        
        # Multiplier Collab (DITINGKATKAN AGAR LEBIH DOMINAN)
        if self.partner:
            base_collab_mult = 2.5 # NAIK DARI 1.9
            if self.agency_synergy:
                # Bonus Spesial Satu Agensi
                exp_multiplier *= (base_collab_mult + 0.5) # NAIK DARI 2.3
                prisma_multiplier *= (base_collab_mult + 0.5) # NAIK DARI 2.3
            else:
                # Bonus Collab Biasa
                exp_multiplier *= base_collab_mult # NAIK DARI 1.9
                prisma_multiplier *= base_collab_mult # NAIK DARI 1.9

        # Multiplier Agensi Host (Disesuaikan sedikit karena collab sudah tinggi)
        if agency_id == "mahavirtual":
            exp_multiplier *= 1.10; prisma_multiplier *= 0.90 # Sedikit dikurangi
        elif agency_id == "prism_project":
            exp_multiplier *= 0.90; prisma_multiplier *= 1.10 # Sedikit dikurangi
            
        # PERBAIKAN: Jika Collab, hasil dibagi rata (50/50) setelah dikalikan
        if self.partner:
            self.total_exp += int(raw_exp * exp_multiplier)
            self.total_prisma += int(raw_prisma * prisma_multiplier)
        else:
            final_exp = int(raw_exp * exp_multiplier)
            final_prisma = int(raw_prisma * prisma_multiplier)
            self.total_exp += final_exp
            self.total_prisma += final_prisma

    def _create_live_embed(self) -> discord.Embed:
        color_map = { "positive_spike": BotColors.STREAM_SPIKE, "positive": BotColors.SUCCESS, "negative_spike": BotColors.STREAM_CRASH, "negative": BotColors.ERROR, "neutral": BotColors.DEFAULT }
        embed_color = color_map.get(self.last_event['type'], BotColors.DEFAULT)
        
        # Judul dan Header
        header_text = f"🔴 LIVE: {self.stream_title}"
        if self.agency_synergy:
            header_text += " | ✨ AGENCY SYNERGY ACTIVE!"
            embed_color = discord.Color.gold() # Warna spesial untuk synergy

        event_text = f"{self.last_event['emoji']} **{self.last_event['log_text']}**"
        
        embed = discord.Embed(
            title=header_text,
            description=f"**Kejadian Terbaru:** {event_text}",
            color=embed_color
        )
        embed.set_thumbnail(url=self.user.display_avatar.url)
        if self.partner:
            # Menampilkan footer spesial jika collab
            embed.set_footer(text=f"Streamers: {self.user.display_name} x {self.partner.display_name}")
        else:
            embed.set_footer(text=f"Streamer: {self.user.display_name}")

        stats_dashboard = (
            f"👀 **Penonton:** `{self.current_viewers:,}`\n"
            f"🔥 **Hype:** `{self.hype:.1f}/100` {self._create_progress_bar(self.hype, 100)}\n"
            f"⏳ **Berakhir:** <t:{int(self.end_time.timestamp())}:R>"
        )
        embed.add_field(name="📊 Dasbor Siaran", value=stats_dashboard, inline=False)
        
        chat_display = "\n".join(self.chat_log_display) or "_Menunggu pesan pertama..._"
        embed.add_field(name="💬 Live Chat", value=chat_display, inline=False)
        
        return embed

    async def _render_tick_with_animation(self):
        try:
            base_embed = self._create_live_embed()
            await self.message.edit(embed=base_embed)
            await asyncio.sleep(random.uniform(0.5, 0.8))

            total_delay = 0
            while self.chat_message_queue:
                user, comment = self.chat_message_queue.popleft()
                self.chat_log_display.append(f"**`{user}`**: {comment}")
                
                animated_embed = self._create_live_embed()
                await self.message.edit(embed=animated_embed)
                
                delay = random.uniform(0.9, 1.4)
                total_delay += delay
                await asyncio.sleep(delay)
                
            remaining_sleep = 5.0 - total_delay
            if remaining_sleep > 0:
                await asyncio.sleep(remaining_sleep)

        except (discord.DiscordServerError, discord.NotFound, Exception) as e:
            print(f"Stream Error: {e}")
            if isinstance(e, discord.NotFound): self.is_running = False

    async def _end_stream(self):
        finish_desc = "Terima kasih sudah menonton! Menyiapkan rekap..."
        if self.partner: finish_desc += f"\n🤝 Terima kasih kepada {self.partner.mention} atas kolaborasinya!"
        
        await self.message.edit(embed=discord.Embed(title="⚫ SIARAN SELESAI", description=finish_desc, color=BotColors.STREAM_END), view=None)
        await asyncio.sleep(4)
        
        # --- LOGIKA HADIAH HOST ---
        await self._distribute_rewards(self.user)
        
        # --- LOGIKA HADIAH PARTNER (Jika Collab) ---
        if self.partner:
            await self._distribute_rewards(self.partner, is_partner=True)

        # Kirim embed rekap
        await self._send_recap_embed()

    async def _distribute_rewards(self, user: discord.User, is_partner=False):
        """Fungsi pembantu untuk membagikan hadiah ke user (Host/Partner)"""
        player_data = await get_player_data(self.bot.db, user.id)

        # --- LOGIKA SUBSCRIBER (SAMA) ---
        sub_gain = 0
        current_subs = player_data.get('subscribers', 1)
        sub_based_roll_modifier = 1 + (math.log10(current_subs + 1) / 5.0)
        
        viewers_calc = self.peak_viewers
        if self.partner: viewers_calc = int(viewers_calc * 0.7)

        if self.stream_type == "free_talk":
            sub_gain = 1
            bonus_chance = ((viewers_calc / 300.0) * (self.hype / 100.0)) * sub_based_roll_modifier
            bonus_acquisition_chance = min(bonus_chance, 0.50)
            potential_bonus_rolls = math.ceil(viewers_calc / 120.0)
            for _ in range(potential_bonus_rolls):
                if random.random() < bonus_acquisition_chance: sub_gain += 1
            if player_data.get('agency_id') == "react_entertainment": 
                sub_gain = math.ceil(sub_gain * 1.30)
        else:
            sub_gain = 1 if self.partner else 0
            base_chance = ((viewers_calc / 750.0) * (self.hype / 150.0)) * sub_based_roll_modifier
            acquisition_chance = min(base_chance, 0.30)
            potential_rolls = math.ceil(viewers_calc / 150.0)
            for _ in range(potential_rolls):
                if random.random() < acquisition_chance: sub_gain += 1
        
        if not is_partner: self.total_subs_gain = sub_gain 

        # --- UPDATE DATA & LOGIKA LEVEL UP (KODE DIPERBAIKI) ---
        
        # 1. Dapatkan Level Lama menggunakan formula ProfileCog
        old_level, _, _ = self._get_level_progress_static(player_data.get('exp', 0))
        
        # PERBAIKAN PEMBAGIAN REWARD COLLAB
        if self.partner:
            # EXP & PRISMA DI BAGI RATA (50/50) dari total yang dikumpulkan (self.total_exp/prisma)
            # Host mendapat 100% dari total yang dikumpulkan
            # Partner mendapat 100% dari total yang dikumpulkan
            total_exp_gained = self.total_exp 
            total_prisma_gained = self.total_prisma
        else:
            # Solo Stream
            total_exp_gained = self.total_exp
            total_prisma_gained = self.total_prisma


        new_total_exp = player_data.get('exp', 0) + total_exp_gained
        
        # 3. Dapatkan Level Baru menggunakan formula ProfileCog
        new_level, _, _ = self._get_level_progress_static(new_total_exp)
        
        stat_updates = {}
        if new_level > old_level:
            levels_gained = new_level - old_level
            
            # Stat Gain per Level (Sama dengan yang ada di ProfileCog: 15/3/2/1)
            hp_gain, atk_gain, def_gain, spd_gain = 15*levels_gained, 3*levels_gained, 2*levels_gained, 1*levels_gained
            
            # Ambil Base Stat yang sudah ada
            current_base_hp = player_data.get('base_hp', 100)
            current_base_atk = player_data.get('base_atk', 10)
            current_base_def = player_data.get('base_def', 5)
            current_base_spd = player_data.get('base_spd', 10)
            
            # Tambahkan ke update
            stat_updates = {
                'level': new_level,
                'base_hp': current_base_hp + hp_gain,
                'base_atk': current_base_atk + atk_gain,
                'base_def': current_base_def + def_gain,
                'base_spd': current_base_spd + spd_gain
            }
            # Kirim notif level up personal
            try: 
                await user.send(f"🎉 Level Up ke **{new_level}** dari hasil {'Collab Stream' if is_partner else 'Stream Langsung'}!\nBonus Stat: HP +{hp_gain}, ATK +{atk_gain}, DEF +{def_gain}, SPD +{spd_gain}")
            except: 
                pass

        final_updates = {
            'subscribers': player_data.get('subscribers', 0) + sub_gain,
            'exp': new_total_exp,
            'prisma': player_data.get('prisma', 0) + total_prisma_gained, # Gunakan Prisma yang sudah disesuaikan
            **stat_updates
        }
        await update_player_data(self.bot.db, user.id, **final_updates)

        # Update Misi
        quest_cog = self.bot.get_cog("Misi")
        if quest_cog:
            await quest_cog.update_quest_progress(user.id, 'COMPLETE_STREAM')
            if total_exp_gained > 0: await quest_cog.update_quest_progress(user.id, 'STREAM_EXP', total_exp_gained)
            if total_prisma_gained > 0: await quest_cog.update_quest_progress(user.id, 'EARN_PRISMA', total_prisma_gained)

    async def _send_recap_embed(self, level_up_messages: list = []):
        desc = f"Berikut adalah rekap dari stream **{self.stream_title}**."
        if self.agency_synergy:
            desc += "\n✨ **AGENCY SYNERGY BONUS APPLIED!**"
        
        recap_embed = discord.Embed(title="🎉 Hasil Siaran Langsung!", description=desc, color=BotColors.SUCCESS)
        recap_embed.set_author(name=self.user.display_name, icon_url=self.user.display_avatar.url)
        
        recap_embed.add_field(name="📈 Performa", value=f"👀 **Puncak Penonton:** `{int(self.peak_viewers):,}`\n🔥 **Hype Score Akhir:** `{self.hype:.1f}`", inline=True)
        
        reward_val = f"👥 **Subscribers Baru:** `+{self.total_subs_gain:,}`\n✨ **EXP Didapat:** `{self.total_exp:,}`\n💎 **Prisma Didapat:** `{self.total_prisma:,}`"
        if self.partner:
            reward_val += "\n*(Partner juga mendapatkan EXP & Prisma yang sama)*"
            
        recap_embed.add_field(name="🎁 Hasil", value=reward_val, inline=True)
        recap_embed.set_footer(text="Terima kasih telah bermain! | MAHADVEN")
        
        content = f"Rekap untuk {self.user.mention}"
        if self.partner: content += f" dan {self.partner.mention}!"
        
        await self.channel.send(content=content, embed=recap_embed)

# ===================================================================================
# VIEW & COG UTAMA
# ===================================================================================
    
    
class StreamTypeSelectionView(discord.ui.View):
    def __init__(self, author: discord.User, cog: 'StreamCog'):
        super().__init__(timeout=70)
        self.author = author; self.cog = cog; self.message: discord.Message = None
    
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id: 
            await interaction.response.send_message("Ini bukan sesi milikmu!", ephemeral=True)
            return False
        return True

    async def start_stream_logic(self, interaction: discord.Interaction, stream_type: str, partner: discord.User = None):
        # Jika Collab, jalankan logika undangan dan Validasi
        if stream_type == "collab" and not partner:
            
            # --- [BARU] CEK COOLDOWN HOST ---
            current_time = datetime.datetime.now().timestamp()
            host_cooldown = self.cog.collab_cooldowns.get(interaction.user.id, 0)
            
            if current_time < host_cooldown:
                return await interaction.response.send_message(
                    f"⏳ **Cooldown Aktif!**\nKamu baru saja melakukan Collab.\nBisa collab lagi: <t:{int(host_cooldown)}:R>", 
                    ephemeral=True
                )
            # --------------------------------

            # Mode tombol: Minta user mention
            await interaction.response.send_message("Silakan mention user yang ingin diajak Collab (Contoh: @User).", ephemeral=True)
            
            def check(m):
                return m.author == interaction.user and m.channel == interaction.channel and m.mentions
            
            try:
                msg = await self.cog.bot.wait_for('message', check=check, timeout=30.0)
                partner = msg.mentions[0]
                
                # Validasi 1: Tidak boleh diri sendiri atau bot
                if partner.id == interaction.user.id or partner.bot:
                    return await interaction.followup.send("Kamu tidak bisa collab dengan diri sendiri atau bot!", ephemeral=True)
                
                # Validasi 2: Cek apakah partner sudah debut
                partner_data = await get_player_data(self.cog.bot.db, partner.id)
                if not partner_data or not partner_data.get('equipped_title_id'):
                    return await interaction.followup.send(
                        f"🚫 {partner.mention} belum melakukan **Debut**!", ephemeral=True
                    )

                # --- [BARU] CEK COOLDOWN PARTNER ---
                partner_cooldown = self.cog.collab_cooldowns.get(partner.id, 0)
                if current_time < partner_cooldown:
                    return await interaction.followup.send(
                        f"🚫 **Gagal Mengundang:**\n{partner.mention} sedang dalam cooldown Collab.\nMereka bisa diajak lagi: <t:{int(partner_cooldown)}:R>",
                        ephemeral=True
                    )
                # -----------------------------------

                # Kirim Undangan
                embed_invite = discord.Embed(
                    title="🤝 Undangan Collab Stream",
                    description=f"{interaction.user.mention} mengundang {partner.mention} untuk melakukan Collab Stream!\n\n**Keuntungan:**\n✅ Durasi Stream +30 detik\n✅ Hadiah EXP & Prisma Meningkat\n✅ Bonus Spesial jika Satu Agensi",
                    color=discord.Color.purple()
                )
                invite_view = CollabInviteView(interaction.user, partner)
                await interaction.channel.send(content=f"{partner.mention}", embed=embed_invite, view=invite_view)
                
                # Hapus UI Lama
                for item in self.children: item.disabled = True
                if self.message: await self.message.edit(view=self)

                # Tunggu Respon
                await invite_view.wait()
                
                if invite_view.value:
                    # Diterima
                    self.cog.active_streams.add(interaction.user.id)
                    self.cog.active_streams.add(partner.id)
                    
                    # --- [BARU] SET COOLDOWN UNTUK KEDUANYA (30 MENIT) ---
                    cooldown_end = datetime.datetime.now().timestamp() + (30 * 60)
                    self.cog.collab_cooldowns[interaction.user.id] = cooldown_end
                    self.cog.collab_cooldowns[partner.id] = cooldown_end
                    # -----------------------------------------------------
                    
                    real_type = random.choice(["gaming", "free_talk", "donathon"])
                    session = StreamSession(self.cog.bot, interaction.user, interaction.channel, real_type, partner=partner)
                    asyncio.create_task(self._run_session(session, [interaction.user.id, partner.id]))
                else:
                    return 

            except asyncio.TimeoutError:
                await interaction.followup.send("Waktu habis untuk memilih partner.", ephemeral=True)
            return

        # Logika Stream Solo Biasa
        for item in self.children: item.disabled = True
        await interaction.response.edit_message(content=f"Baik! Sesi **{stream_type.replace('_',' ').title()} Stream** akan segera dimulai...", view=self, embed=None)
        self.cog.active_streams.add(interaction.user.id)
        session = StreamSession(self.cog.bot, interaction.user, interaction.channel, stream_type)
        asyncio.create_task(self._run_session(session, [interaction.user.id]))

    async def _run_session(self, session: StreamSession, user_ids: list):
        try: await session.start()
        finally:
            for uid in user_ids: self.cog.active_streams.discard(uid)

    @discord.ui.button(label="Gaming", style=discord.ButtonStyle.primary, emoji="🎮", row=0)
    async def gaming_stream(self, interaction: discord.Interaction, button: discord.ui.Button): await self.start_stream_logic(interaction, "gaming")
    
    @discord.ui.button(label="Donathon", style=discord.ButtonStyle.success, emoji="💎", row=0)
    async def donathon_stream(self, interaction: discord.Interaction, button: discord.ui.Button): await self.start_stream_logic(interaction, "donathon")

    @discord.ui.button(label="Free Talk", style=discord.ButtonStyle.secondary, emoji="💬", row=0)
    async def freetalk_stream(self, interaction: discord.Interaction, button: discord.ui.Button): await self.start_stream_logic(interaction, "free_talk")
    
    @discord.ui.button(label="Collab Stream", style=discord.ButtonStyle.blurple, emoji="🤝", row=1)
    async def collab_stream(self, interaction: discord.Interaction, button: discord.ui.Button): 
        await self.start_stream_logic(interaction, "collab")
    
    async def on_timeout(self):
        if self.message:
            try:
                for item in self.children: item.disabled = True
                await self.message.edit(content="Waktu pemilihan tipe stream habis.", view=self, embed=None)
            except discord.NotFound: pass


class StreamCog(commands.Cog, name="Streaming"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.active_streams = set()
        # Dictionary untuk menyimpan waktu cooldown collab
        # Format: {user_id: timestamp_kapan_selesai}
        self.collab_cooldowns = {} 
        self._load_stream_data()

    def _load_stream_data(self):
        try:
            with open('data/stream_data.json', 'r', encoding='utf-8') as f:
                self.bot.stream_data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"Error loading stream_data.json: {e}")
            self.bot.stream_data = {"stream_titles": {}, "stream_events": [], "chat_data": {}}

    @commands.command(name="stream", aliases=["live"])
    @commands.cooldown(1, 75, commands.BucketType.user)
    async def stream(self, ctx: commands.Context):
        """Memulai streaming. Gunakan tombol Collab untuk mengajak teman!"""
        if ctx.author.id in self.active_streams: return await ctx.send("Kamu sudah sedang streaming!", ephemeral=True, delete_after=10)
        player_data = await get_player_data(self.bot.db, ctx.author.id)
        if not player_data.get('equipped_title_id'): return await ctx.send(f"Kamu harus debut dulu dengan `{self.bot.command_prefix}debut`!", ephemeral=True, delete_after=10)
        
        embed = discord.Embed(
            title="🎙️ Pilih Tipe Stream",
            description=(
                "Pilih konten streaming kamu hari ini.\n\n"
                "🎮 **Gaming**: Fokus EXP.\n"
                "💎 **Donathon**: Fokus Prisma.\n"
                "💬 **Free Talk**: Jaminan Subscriber.\n"
                "🤝 **Collab**: Ajak teman untuk durasi & hadiah lebih besar!\n*(Collab Cooldown: 30 Menit)*"
            ),
            color=BotColors.DEFAULT
        )
        view = StreamTypeSelectionView(ctx.author, self)
        view.message = await ctx.send(embed=embed, view=view)

    @stream.error
    async def stream_error(self, ctx, error):
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.send(f"Kamu butuh istirahat! Coba lagi dalam **{error.retry_after:.0f} detik**.", delete_after=10)

async def setup(bot):
    await bot.add_cog(StreamCog(bot))