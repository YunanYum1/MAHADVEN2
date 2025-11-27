import discord
from discord.ext import commands
import asyncio
import math
from typing import Optional, List

# Mengimpor CombatSession yang baru dan daftar pertarungan aktif
from game_logic.combat_logic import CombatSession, active_players, get_player_data

# ===================================================================================
# [BARU] PEMETAAN STATUS KE EMOJI & FUNGSI HELPER
# ===================================================================================

STATUS_EMOTE_MAP = {
    # === BUFFS (Peningkatan) ===
    "ATK Up": "⚔️",
    "Defense Up": "🛡️", "DEF Up": "🛡️", "Ironclad": "🛡️", "Steady Guard": "🛡️", "Iron Resolve": "💪",
    "Speed Up": "💨", "SPD Up": "💨", "Wind Step": "💨",
    "Crit Up": "🎯", "Crit Rate": "🎯", "Crit Dmg": "💥", "Focus": "🎯",
    "Evasion": "🍃",
    "Regeneration": "❤️‍🩹", "Hallowed Ground": "❤️‍🩹", "Natures Blessing": "❤️‍🩹", "Unyielding Heart": "❤️‍🩹",
    "Immunity": "✅",
    "Invincibility": "✨",
    "Shield": "🛡️",
    "Counter": "🔄",
    "Reflect": "🔄",

    # === DEBUFFS (Penurunan) & KONDISI ===
    "Stun": "😵", "Stunned": "😵",
    "Freeze": "🥶",
    "Sleep": "😴",
    "Root": "🌲", "Rooted": "🌲",
    "Charmed": "💕",
    "Paralyze": "⚡", "Paralyzed": "⚡",
    "Silence": "🔇",
    "Heal Block": "💔",
    "Blind": "😵‍💫", "Blinded": "😵‍💫",
    "Fear": "😨",
    "Branded": "🎯",
    "Taunt": "🗣️",
    "Disarmed": "🚫",
    "Attack Down": "📉", "ATK Down": "📉", "Weaken": "📉",
    "Defense Down": "📉", "DEF Down": "📉",
    "Speed Down": "🐌", "SPD Down": "🐌", "Slow": "🐌", "Slowed": "🐌",

    # === DAMAGE OVER TIME (DoT) ===
    "Burn": "🔥", "Burned": "🔥",
    "Poison": "☠️", "Poisoned": "☠️",
    "Bleed": "🩸", "Bleeding": "🩸",

    # === EFEK SPESIFIK DARI SKILL (LENGKAP) ===
    # Buffs
    "Tidal Defense": "🌊",
    "Solar ATK Up": "☀️",
    "Flowing Evasion": "🍃",
    "Flowing Attack": "⚔️",
    "Rocs ATK Up": "🐦",
    "Rocs Crit Up": "🎯",
    "Static Resonance": "⚡", # SPD Up
    "Cometfall Focus": "☄️", # Crit Rate Up
    "Wind Rider": "🏇", # SPD & ATK Up
    "Orchestrated": "🎼", # Crit Rate & Dmg Up
    "Flourish": "🌹", # Crit Rate Up
    "Ephemeral Grace": "🌸", # SPD Up
    "Blade Dance": "💃", # SPD Up
    "Demonic Pact": "😈", # ATK Up
    "Lunar Blessing": "🌙", # ATK Up
    "Wardens Grace": "⛓️", # SPD Up
    "Spirit of the Pack": "🐺", # SPD Up Stacking
    "Feathered Sonnet": "🕊️", # SPD Up Stacking
    "Resolute Guardian": "💪", # DEF Up
    "Grave Pact": "🪦", # DEF Up
    "Sanguine Pact": "💔", # ATK Up
    "Eager Heart": "❤️", # ATK Up

    # Debuffs
    "Duskfall": "🌇", # Blind & Slow
    "Sorrowful": "🎶", # ATK & DEF Down
    "Weeping Wound": "💧", # ATK Down
    "Quake Weaken": "⛰️", # DEF Down
    "Tacticians Ploy": "🧠", # ATK Down
    "Volatile Encryption": "🔒", # ATK Down
    "Caramelized": "🍮", # Slow
    "Winters Chill": "❄️",
    "Lunar Curse": "🌘", # SPD Down
    "Chained": "⛓️", # SPD Down
    "Deep Sea Curse": "⚓", # ATK Down
    "Draining Note": "🎵", # ATK Down
    "Fading Curse": "📉", # ATK Down
    
    # DoT Spesial
    "Neurotoxin": "☣️",
    "Wilted Rose Curse": "🥀",

    # Efek Spesial / Lainnya
    "Link": "🔗",
    "Untargetable": "💨",
    "Perfect Confection": "🍬",
    "Summon": "💀",
    "Stat Swap": "↔️",
    "Guaranteed Stun": "⏳",
    "Blossom Strike": "💮",
}

def get_status_emote(effect_name: str) -> str:
    """Mencari emoji yang sesuai untuk sebuah efek status berdasarkan kata kunci."""
    # Iterasi melalui map untuk menemukan kata kunci yang cocok
    for key, emote in STATUS_EMOTE_MAP.items():
        if key.lower() in effect_name.lower():
            return emote
    # Emoji default jika tidak ada yang cocok sama sekali
    return "⭐"

def _calculate_evasion_from_spd(player_spd: int) -> float:
    """Menghitung persentase evasion berdasarkan stat SPD."""
    EVA_SCALING_FACTOR = 200
    MAX_EVA_FROM_SPD = 0.70
    if player_spd <= 0:
        return 0.0
    return (player_spd / (player_spd + EVA_SCALING_FACTOR)) * MAX_EVA_FROM_SPD


# ===================================================================================
# KELAS-KELAS VIEW (UI INTERAKTIF BARU)
# ===================================================================================

class SurrenderConfirmationView(discord.ui.View):
    """View ephemeral untuk konfirmasi menyerah."""
    def __init__(self, combat_view: 'CombatView', surrenderer_id: int):
        super().__init__(timeout=30.0)
        self.combat_view = combat_view
        self.surrenderer_id = surrenderer_id

    @discord.ui.button(label="Ya, Saya Yakin", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Nonaktifkan tombol di view konfirmasi
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="Anda telah menyerah.", view=self)

        # [FIXED] Panggil fungsi handle_surrender dari sesi pertarungan
        if self.combat_view.session and not self.combat_view.session.game_over:
            await self.combat_view.session.handle_surrender(self.surrenderer_id)
        
            # Perbarui pesan pertarungan utama untuk menampilkan hasil akhir
            await self.combat_view.update_message()
        
        self.stop()

    @discord.ui.button(label="Batal", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="Aksi dibatalkan. Aksi dibatalkan.", view=self)
        self.stop()

class MonsterSelect(discord.ui.Select):
    """Dropdown untuk memilih monster dari halaman saat ini."""
    def __init__(self, parent_view: 'PaginatedMonsterView'):
        self.parent_view = parent_view
        self.cog = parent_view.cog
        
        options = []
        monsters_on_page = self.parent_view.get_monsters_for_current_page()
        for monster in monsters_on_page:
            stats_desc = f"❤️{monster['hp']} ⚔️{monster['atk']} 🛡️{monster['def']}"
            options.append(discord.SelectOption(
                label=monster['name'], value=monster['name'], description=stats_desc
            ))
        
        super().__init__(placeholder="Pilih monster untuk dilawan...", options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        monster_name = self.values[0]
        monster_data = next((m for m in self.cog.bot.monsters if m['name'] == monster_name), None)
        
        if not monster_data:
            await interaction.response.send_message("Monster tidak ditemukan.", ephemeral=True)
            return
            
        await interaction.response.edit_message(
            content=f"Seekor **{monster_data['name']}** liar muncul! Mempersiapkan arena...", 
            view=None, embed=None
        )
        await self.cog.start_fight_session(interaction, interaction.user, monster_data)

class PaginatedMonsterView(discord.ui.View):
    """View dengan paginasi untuk menampilkan daftar monster."""
    def __init__(self, author: discord.User, cog: commands.Cog):
        super().__init__(timeout=180.0)
        self.author = author
        self.cog = cog
        self.current_page = 0
        self.items_per_page = 4
        self.total_pages = math.ceil(len(self.cog.bot.monsters) / self.items_per_page)
        self.message: Optional[discord.Message] = None
        self.update_components()

    def get_monsters_for_current_page(self) -> List[dict]:
        start = self.current_page * self.items_per_page
        end = start + self.items_per_page
        return self.cog.bot.monsters[start:end]

    def create_pve_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="⚔️ Arena Latihan ⚔️",
            description=f"Pilih lawan untuk memulai pertarungan.\n\n**Halaman {self.current_page + 1}/{self.total_pages}**",
            color=0x2b2d31
        )
        monsters_on_page = self.get_monsters_for_current_page()
        for m in monsters_on_page:
            stats = f"❤️`{m.get('hp', 'N/A')}` ⚔️`{m.get('atk', 'N/A')}` 🛡️`{m.get('def', 'N/A')}` 💨`{m.get('spd', 'N/A')}`"
            
            # --- [LOGIKA BARU] Format tampilan hadiah ---
            exp_reward = m.get('exp_reward', 0)
            exp_text = f"{exp_reward[0]}~{exp_reward[1]}" if isinstance(exp_reward, list) else str(exp_reward)
            
            money_reward = m.get('money_reward', 0)
            money_text = f"{money_reward[0]}~{money_reward[1]}" if isinstance(money_reward, list) else str(money_reward)

            rewards = f"✨`{exp_text}` EXP ▪️ 💰`{money_text}` Prisma"
            # --- Akhir Logika Baru ---

            embed.add_field(name=f"```{m['name']}```", value=f"{stats}\n{rewards}", inline=False)
            
        embed.set_footer(text="Lawan yang lebih kuat memberikan hadiah yang lebih besar.")
        return embed

    def update_components(self):
        self.clear_items()
        self.add_item(MonsterSelect(self))

        prev_button = discord.ui.Button(label="◀️", style=discord.ButtonStyle.primary, disabled=(self.current_page == 0), row=1)
        prev_button.callback = self.prev_page

        next_button = discord.ui.Button(label="▶️", style=discord.ButtonStyle.primary, disabled=(self.current_page >= self.total_pages - 1), row=1)
        next_button.callback = self.next_page

        self.add_item(prev_button)
        self.add_item(next_button)

    async def update_message(self, interaction: discord.Interaction):
        self.update_components()
        await interaction.response.edit_message(embed=self.create_pve_embed(), view=self)

    async def prev_page(self, interaction: discord.Interaction):
        if self.current_page > 0: self.current_page -= 1
        await self.update_message(interaction)
        
    async def next_page(self, interaction: discord.Interaction):
        if self.current_page < self.total_pages - 1: self.current_page += 1
        await self.update_message(interaction)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("Ini bukan panel latihan milikmu!", ephemeral=True)
            return False
        return True
    
    async def on_timeout(self):
        if self.message:
            for item in self.children: item.disabled = True
            try: await self.message.edit(view=self)
            except discord.NotFound: pass

class CombatView(discord.ui.View):
    """View dinamis yang dirombak untuk alur pertarungan yang lebih baik."""
    def __init__(self, session: CombatSession):
        super().__init__(timeout=300.0)
        self.session = session
        self.message: Optional[discord.Message] = None
        self.update_components()

    def update_components(self):
        self.clear_items()
        active_participant = self.session.current_turn_participant
        is_player_turn = active_participant.get('is_player', False) and not self.session.game_over
        is_silenced = any(e.get('type') == 'silence' for e in active_participant.get('status_effects', []))

        attack_button = discord.ui.Button(label="Serang", style=discord.ButtonStyle.danger, emoji="⚔️", disabled=not is_player_turn, row=0)
        attack_button.callback = self.attack_callback
        self.add_item(attack_button)

        active_skills = [s for s in active_participant.get('skills', []) if s.get('type') == 'active']
        
        for i, skill in enumerate(active_skills[:4]):
            cooldown_turns = active_participant['skill_cooldowns'].get(skill['name'], 0)
            on_cooldown = cooldown_turns > 0
            label = f"{skill['name']} ({cooldown_turns})" if on_cooldown else skill['name']
            
            skill_button = discord.ui.Button(label=label, style=discord.ButtonStyle.primary, disabled=not is_player_turn or on_cooldown or is_silenced, row=1)
            skill_button.callback = (lambda s=skill: lambda i: self.skill_callback(i, s['name']))()
            self.add_item(skill_button)

        surrender_button = discord.ui.Button(label="Menyerah", style=discord.ButtonStyle.secondary, emoji="🏳️", row=2, disabled=self.session.game_over)
        surrender_button.callback = self.surrender_callback
        self.add_item(surrender_button)

    async def update_message(self, interaction: Optional[discord.Interaction] = None):
        # Defer ini tetap di sini sebagai pengaman jika update_message dipanggil dari tempat lain
        if interaction and not interaction.response.is_done():
            await interaction.response.defer()
        
        self.update_components()
        if self.session.game_over:
            for item in self.children: item.disabled = True
            self.stop()

        # Gunakan create_fight_embed versi terbaru yang minimalis
        embed = create_fight_embed(self.session) 
        
        # Gunakan edit_original_response jika ada interaksi, jika tidak, gunakan edit biasa
        edit_method = interaction.edit_original_response if interaction else self.message.edit
        
        try:
            await edit_method(embed=embed, view=self)
        except (discord.NotFound, discord.InteractionResponded):
            pass
    
    async def _handle_turn_flow(self, interaction: discord.Interaction, action: str, **kwargs):
        """
        [DIPERBAIKI] Alur utama yang sekarang memanggil efek awal giliran untuk pemain.
        """
        if self.session.game_over or self.session.current_turn_participant['id'] != interaction.user.id:
            try: await interaction.response.send_message("Ini bukan giliranmu atau pertarungan telah berakhir!", ephemeral=True)
            except discord.InteractionResponded: pass
            return

        await interaction.response.defer()

        # [PERBAIKAN KUNCI] Jalankan efek awal giliran (termasuk pengurangan cooldown) UNTUK PEMAIN
        participant = self.session.current_turn_participant
        is_skipped = await self.session._apply_turn_start_effects_and_check_skip(participant)
        
        # Perbarui UI untuk menunjukkan efek DoT/HoT dan pengurangan cooldown yang baru saja terjadi
        await self.update_message(interaction)

        # Jika pemain terkena stun/freeze/paralyze, lewati giliran mereka
        if is_skipped:
            if self.session.game_over:
                return

            # Langsung ganti ke giliran berikutnya
            await self.session.switch_turn()
            await self.update_message(None) # Update lagi untuk menunjukkan giliran telah berganti

            # Jika sekarang giliran AI, jalankan
            await asyncio.sleep(1.5)
            if self.session.is_pve and not self.session.current_turn_participant.get('is_player'):
                await self.session.run_ai_turn()
                await self.update_message(None)
            return # Hentikan fungsi di sini karena giliran pemain sudah selesai (dilewati)

        # --- Jika giliran tidak dilewati, lanjutkan untuk memproses aksi pemain ---
        await self.session.process_turn_action(interaction.user.id, action, **kwargs)
        
        await self.update_message(interaction)
        
        if self.session.game_over:
            return

        await asyncio.sleep(1.5)

        if self.session.is_pve and not self.session.current_turn_participant.get('is_player'):
            await self.session.run_ai_turn()
            await self.update_message(None)
    
    async def attack_callback(self, interaction: discord.Interaction):
        await self._handle_turn_flow(interaction, 'attack')
            
    async def skill_callback(self, interaction: discord.Interaction, skill_name: str):
        await self._handle_turn_flow(interaction, 'skill', skill_name=skill_name)

    async def surrender_callback(self, interaction: discord.Interaction):
        is_participant = interaction.user.id in [self.session.p1.get('id'), self.session.p2.get('id')]
        if not is_participant or self.session.game_over:
            return await interaction.response.send_message("Anda tidak bisa melakukan ini.", ephemeral=True)
        
        view = SurrenderConfirmationView(self, interaction.user.id)
        await interaction.response.send_message("Anda yakin ingin menyerah?", view=view, ephemeral=True)

    async def on_timeout(self):
        if not self.session.game_over and self.message:
            self.session.game_over = True
            if self.session.p1.get('id'): active_players.discard(self.session.p1['id'])
            if self.session.p2.get('id'): active_players.discard(self.session.p2['id'])

            timeout_embed = discord.Embed(title="⚔️ Pertarungan Selesai ⚔️", description="⌛ Pertarungan dihentikan karena tidak ada aktivitas.", color=discord.Color.greyple())
            await self.message.channel.send(embed=timeout_embed)
            await self.update_message(None)

# ===================================================================================
# [FUNGSI YANG DIPERBAIKI]
# ===================================================================================
def create_fight_embed(session: CombatSession) -> discord.Embed:
    """
    [PERBAIKAN] Versi embed yang sekarang membatasi panjang log agar lebih ringkas.
    """
    p1, p2 = session.p1, session.p2
    
    # --- Bagian 1: Logika Embed Berdasarkan Status Pertarungan ---
    if session.game_over:
        winner = p1 if p1.get('hp', 0) > 0 else p2
        winner_name = winner.get('name', 'Tidak ada') if winner else 'Tidak ada'
        winner_color = discord.Color.gold() if winner and winner.get('is_player') else 0x992d22
        
        embed = discord.Embed(
            title="⚔️ Pertarungan Telah Selesai ⚔️",
            description=f"**{winner_name}** telah memenangkan pertarungan!\nHasil lengkap dikirim dalam pesan terpisah.",
            color=winner_color
        )
        if winner and winner.get('avatar_url'):
            embed.set_thumbnail(url=winner['avatar_url'])
        footer_text = "Gunakan !!tantang atau !!latih untuk memulai pertarungan baru."
    else:
        active_char = session.current_turn_participant
        
        embed_color = discord.Color.blurple() # Warna default
        if active_agency_id := active_char.get('agency_id'):
            if agency_data := session.bot.get_agency_by_id(active_agency_id):
                if agency_color := agency_data.get('color'):
                    embed_color = discord.Color(int(agency_color, 16))

        embed = discord.Embed(
            title=f"⏳ Putaran #{session.round_count} - Giliran {active_char.get('name')}",
            color=embed_color
        )
        embed.set_thumbnail(url=active_char.get('avatar_url'))
        footer_text = "Pilih aksimu dari tombol di bawah."

    # --- Bagian 2: Fungsi Internal untuk Memformat Tampilan Pemain ---
    def format_participant_display(p_data: dict) -> tuple[str, str]:
        agency_emoji = ""
        if p_data.get('is_player') and (agency_id := p_data.get('agency_id')):
            if agency := session.bot.get_agency_by_id(agency_id):
                agency_emoji = f"{agency['emoji']} "
        field_name = f"{agency_emoji}{p_data['name']}"

        value_lines = []

        hp, max_hp = max(0, int(p_data.get('hp', 0))), int(p_data.get('max_hp', 1))
        hp_percent = hp / max_hp if max_hp > 0 else 0

        bar_length = 10
        filled_blocks = round(hp_percent * bar_length)
        hp_bar = '🟩' * filled_blocks + '🟥' * (bar_length - filled_blocks)
        value_lines.append(f"**HP:** `{hp}/{max_hp}`\n{hp_bar} `{hp_percent:.0%}`")
        
        # Ambil stat saat ini dan stat dasar (acuan netral)
        stats = p_data.get('stats', {})
        base_stats = p_data.get('base_stats', {})

        # [LOGIKA INDIKATOR EMOTE]
        # 🔼 = Buff (Stat sekarang > Stat Awal Battle)
        # 🔽 = Debuff (Stat sekarang < Stat Awal Battle)
        # Kosong = Netral
        def stat_indicator(current, base):
            if current > base: return "🔼" 
            if current < base: return "🔽"
            return "" 
        
        # Format Stat dengan Indikator
        atk = f"⚔️`{stats.get('atk', 0)}`{stat_indicator(stats.get('atk', 0), base_stats.get('atk', 0))}"
        defs = f"🛡️`{stats.get('def', 0)}`{stat_indicator(stats.get('def', 0), base_stats.get('def', 0))}"
        spd = f"💨`{stats.get('spd', 0)}`{stat_indicator(stats.get('spd', 0), base_stats.get('spd', 0))}"

        crit_rate = f"🎯`{stats.get('crit_rate', 0.05):.1%}`"
        # [MODIFIKASI] Ubah format Crit Dmg menjadi persentase
        crit_dmg = f"💥`{stats.get('crit_damage', 1.5):.0%}`"

        value_lines.append(f"**Stat:** {atk} | {defs} | {spd}")
        value_lines.append(f"**Kritikal:** {crit_rate} | {crit_dmg}")
        
        status_effects = p_data.get('status_effects', [])
        if not status_effects: 
            status_display = "✅ *Normal*"
        else: 
            # Menggunakan get_status_emote global yang sudah didefinisikan di awal file
            status_display = " ".join([f"{get_status_emote(e['name'])}`{e['duration']}`" for e in status_effects])

        value_lines.append(f"**Status:** {status_display}")
        return field_name, "\n".join(value_lines)

    # --- Bagian 3: Menambahkan Field Pemain ke Embed ---
    p1_name, p1_value = format_participant_display(p1)
    p2_name, p2_value = format_participant_display(p2)
    embed.add_field(name=p1_name, value=p1_value, inline=True)
    embed.add_field(name=p2_name, value=p2_value, inline=True)
    
    # --- Bagian 4: Memformat dan Menambahkan Log Pertarungan ---
    # [DIUBAH] Logika pemotongan log diubah menjadi berbasis jumlah baris.
    MAX_LOG_LINES = 10
    
    log_slice = session.log[-MAX_LOG_LINES:] if session.log else ["Pertarungan akan segera dimulai..."]
    
    # Tambahkan indikator '...' jika log lebih panjang dari batas
    if len(session.log) > MAX_LOG_LINES:
        log_slice.insert(0, "...")

    formatted_lines = []
    for line in log_slice:
        if line == "...":
            formatted_lines.append("...")
            continue
            
        clean_line = line.replace("**", "")
        
        prefix = "🔹" # Default
        line_lower = clean_line.lower()
        if "kerusakan" in line_lower: prefix = "💥"
        elif "meleset" in line_lower or "menghindar" in line_lower: prefix = "💨"
        elif "memulihkan" in line_lower: prefix = "💚"
        elif "perisai" in line_lower: prefix = "🛡️"
        elif "terkena efek" in line_lower or "menjadi lebih" in line_lower: prefix = "✨"
        elif "gagal bergerak" in line_lower or "pingsan" in line_lower or "lumpuh" in line_lower: prefix = "❌"
        elif "--- Putaran" in line: prefix = "➡️"
            
        formatted_lines.append(f"{prefix} {clean_line}")

    log_text = "\n".join(formatted_lines)
    
    field_name = "📜 Riwayat Pertarungan Terakhir" if session.game_over else "📜 Riwayat Pertarungan"
    embed.add_field(name=field_name, value=f"```md\n{log_text}\n```", inline=False)
    
    embed.set_footer(text=footer_text)
    return embed
# ===================================================================================

class FightCog(commands.Cog, name="Pertarungan"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        
    async def start_fight_session(self, ctx_or_channel, p1_entity, p2_entity, on_finish_callback=None, is_tourney_match=False):
        """
        [DIPERBAIKI] Sekarang menerima flag is_tourney_match untuk menangani data yang sudah jadi.
        """
        # --- Langkah 1: Tentukan Channel & Metode Pengiriman ---
        channel = None
        send_method = None
        
        if isinstance(ctx_or_channel, (commands.Context, discord.Interaction)):
            channel = ctx_or_channel.channel
            send_method = ctx_or_channel.followup.send if isinstance(ctx_or_channel, discord.Interaction) else channel.send
        elif isinstance(ctx_or_channel, discord.TextChannel):
            channel = ctx_or_channel
            send_method = channel.send
        else:
            print(f"FATAL ERROR in start_fight_session: Tipe input tidak valid: {type(ctx_or_channel)}")
            return

        # --- [PERBAIKAN] Validasi data hanya untuk pertarungan non-turnamen ---
        if not is_tourney_match:
            try:
                p1_user = p1_entity # Ganti nama variabel agar lebih jelas
                p1_data = await get_player_data(self.bot.db, p1_user.id)
                if not p1_data or not p1_data.get('equipped_title_id'):
                    await channel.send(f"❌ Pertarungan tidak bisa dimulai karena **{p1_user.display_name}** belum melakukan debut atau datanya tidak ditemukan.")
                    return

                if isinstance(p2_entity, discord.Member):
                    p2_data = await get_player_data(self.bot.db, p2_entity.id)
                    if not p2_data or not p2_data.get('equipped_title_id'):
                        await channel.send(f"❌ Pertarungan tidak bisa dimulai karena **{p2_entity.display_name}** belum melakukan debut atau datanya tidak ditemukan.")
                        return
            except Exception as e:
                await channel.send(f"❌ Terjadi kesalahan saat mengambil data pemain: `{e}`")
                print(f"ERROR fetching player data for fight: {e}")
                return

        # --- Langkah 3: Buat Sesi Pertarungan ---
        try:
            # [MODIFIKASI] Teruskan flag is_tourney_match ke CombatSession
            session = CombatSession(self.bot, channel, p1_entity, p2_entity, on_finish_callback=on_finish_callback, is_tourney_match=is_tourney_match)
            await session.setup_task # Tunggu setup selesai
        except Exception as e:
            await channel.send(f"❌ Gagal membuat sesi pertarungan: `{e}`")
            print(f"FATAL ERROR in CombatSession creation: {e}")
            
            # Pastikan pemain tidak terjebak dalam status 'bertarung'
            p1_id = p1_entity['id'] if is_tourney_match else p1_entity.id
            p2_id = p2_entity['id'] if is_tourney_match else (p2_entity.id if isinstance(p2_entity, discord.Member) else None)
            active_players.discard(p1_id)
            if p2_id: active_players.discard(p2_id)
            return

        # --- Sisa fungsi ini tetap sama ---
        view = CombatView(session)
        session.view = view
        
        fight_msg = await send_method(embed=create_fight_embed(session), view=view)
        view.message = fight_msg
        session.message = fight_msg
        
        if not session.current_turn_participant.get('is_player'):
            await view.update_message()
            await asyncio.sleep(1.5)
            await session.run_ai_turn()
            await view.update_message()

    @commands.hybrid_command(name="tantang", aliases=["pvp"], description="Tantang pemain lain untuk bertarung (PvP).")
    @commands.cooldown(1, 15, commands.BucketType.user)
    async def challenge(self, ctx: commands.Context, lawan: discord.Member):
        """
        Melakukan tantangan pvp dengan player lain.
        """
        # [IMPLEMENTASI BARU] Pemeriksaan debut untuk kedua pemain
        player_data = await self.bot.db.execute("SELECT equipped_title_id FROM players WHERE user_id = ?", (ctx.author.id,))
        if not await player_data.fetchone():
            return await ctx.send(f"Kamu harus melakukan debut terlebih dahulu dengan `{self.bot.command_prefix}debut` sebelum bisa bertarung!", ephemeral=True)

        opponent_data = await self.bot.db.execute("SELECT equipped_title_id FROM players WHERE user_id = ?", (lawan.id,))
        if not await opponent_data.fetchone():
            return await ctx.send(f"**{lawan.display_name}** belum melakukan debut dan tidak bisa ditantang.", ephemeral=True)
        # --- Akhir Implementasi Baru ---

        if lawan.bot or lawan == ctx.author:
            return await ctx.send("Anda tidak bisa menantang target ini.", ephemeral=True)
        
        if ctx.author.id in active_players:
            return await ctx.send("Kamu sudah berada dalam pertarungan! Selesaikan dulu pertarunganmu.", ephemeral=True)
        if lawan.id in active_players:
            return await ctx.send(f"**{lawan.display_name}** sedang berada dalam pertarungan lain saat ini.", ephemeral=True)

        view = discord.ui.View(timeout=60.0)
        async def accept_callback(interaction: discord.Interaction):
            if interaction.user != lawan: return await interaction.response.send_message("Hanya yang ditantang yang bisa merespon!", ephemeral=True)
            for item in view.children: item.disabled = True
            await interaction.response.edit_message(content=f"Tantangan diterima! Mempersiapkan arena...", view=view)
            await self.start_fight_session(ctx, ctx.author, lawan)
            view.stop()
        async def reject_callback(interaction: discord.Interaction):
            if interaction.user != lawan: return
            for item in view.children: item.disabled = True
            await interaction.response.edit_message(content=f"**{lawan.display_name}** menolak tantangan.", view=view)
            view.stop()
            
        accept_btn = discord.ui.Button(label="Terima", style=discord.ButtonStyle.success)
        reject_btn = discord.ui.Button(label="Tolak", style=discord.ButtonStyle.danger)
        accept_btn.callback = accept_callback
        reject_btn.callback = reject_callback
        view.add_item(accept_btn)
        view.add_item(reject_btn)
        await ctx.send(f"⚔️ **{ctx.author.display_name}** menantang **{lawan.mention}**!", view=view)

    @commands.hybrid_command(name="latih", aliases=["pve"], description="Buka panel untuk memilih monster (PvE).")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def train(self, ctx: commands.Context):
        """
        Melakukan pertarungan dengan monsters.
        """
        # [IMPLEMENTASI BARU] Pemeriksaan debut
        player_data = await self.bot.db.execute("SELECT equipped_title_id FROM players WHERE user_id = ?", (ctx.author.id,))
        if not await player_data.fetchone():
            return await ctx.send(f"Kamu harus melakukan debut terlebih dahulu dengan `{self.bot.command_prefix}debut` sebelum bisa berlatih!", ephemeral=True)
        # --- Akhir Implementasi Baru ---

        if ctx.author.id in active_players:
            return await ctx.send("Kamu sudah berada dalam pertarungan! Selesaikan dulu sebelum memulai yang baru.", ephemeral=True)
        
        if not self.bot.monsters:
            return await ctx.send("Data monster tidak tersedia. Hubungi admin.", ephemeral=True)
        
        view = PaginatedMonsterView(author=ctx.author, cog=self)
        embed = view.create_pve_embed()
        message = await ctx.send(embed=embed, view=view)
        view.message = message

async def setup(bot: commands.Bot):
    await bot.add_cog(FightCog(bot))