# cogs/tournament_cog.py

import discord
from discord.ext import commands
import asyncio
import random
from typing import List, Dict, Optional
import os
from collections import Counter

from ._utils import BotColors
from database import get_player_data, get_player_titles, get_player_inventory, get_player_equipment

# ===================================================================================
# --- KELAS-KELAS VIEW (UI INTERAKTIF UNTUK FASE TURNAMEN) ---
# ===================================================================================

class RegistrationView(discord.ui.View):
    def __init__(self, cog: 'TournamentCog'):
        super().__init__(timeout=None)
        self.cog = cog

    async def update_embed(self, interaction: discord.Interaction):
        tournament = self.cog.tournament_state
        embed = interaction.message.embeds[0]
        embed.clear_fields()
        
        participant_list = "Belum ada yang bergabung."
        if tournament['participants']:
            # [CATATAN] Menggunakan fetch_user agar nama selalu update, tapi bisa lebih lambat. Mention lebih cepat.
            mentions = [f"<@{p_id}>" for p_id in tournament['participants']]
            participant_list = "\n".join(mentions)

        embed.add_field(
            name=f"👥 Peserta Terdaftar ({len(tournament['participants'])})",
            value=participant_list,
            inline=False
        )
        # [CATATAN] Menggunakan edit_original_response karena interaksi di-defer.
        await interaction.edit_original_response(embed=embed, view=self)

    @discord.ui.button(label="Ikuti Turnamen", style=discord.ButtonStyle.success, emoji="⚔️", custom_id="tourney_join")
    async def join_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = interaction.user.id
        tournament = self.cog.tournament_state

        if not tournament or tournament['state'] != 'registration':
            return await interaction.response.send_message("Pendaftaran turnamen sudah ditutup.", ephemeral=True)

        if user_id in tournament['participants']:
            return await interaction.response.send_message("Kamu sudah terdaftar!", ephemeral=True)
            
        tournament['participants'].add(user_id)
        await interaction.response.defer() # Defer sebelum update embed
        await self.update_embed(interaction)

    @discord.ui.button(label="Mulai Turnamen", style=discord.ButtonStyle.primary, emoji="▶️", custom_id="tourney_start")
    async def start_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        tournament = self.cog.tournament_state
        # Pengecekan host
        if not tournament or interaction.user.id != tournament['host_id']:
            return await interaction.response.send_message("Hanya penyelenggara yang bisa memulai turnamen.", ephemeral=True)
        
        # Pengecekan jumlah peserta
        if len(tournament['participants']) < 2:
            return await interaction.response.send_message("Turnamen membutuhkan minimal 2 peserta untuk dimulai.", ephemeral=True)
            
        await interaction.response.defer()
        await self.cog.start_tournament()

    @discord.ui.button(label="Batalkan Turnamen", style=discord.ButtonStyle.danger, emoji="✖️", custom_id="tourney_cancel")
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        tournament = self.cog.tournament_state
        if not tournament or interaction.user.id != tournament['host_id']:
            return await interaction.response.send_message("Hanya penyelenggara yang bisa membatalkan turnamen.", ephemeral=True)

        await interaction.response.defer()
        await self.cog.end_tournament(cancelled=True)


class ChannelPhaseView(discord.ui.View):
    def __init__(self, handler: 'MatchHandler'):
        super().__init__(timeout=900.0) # Timeout total 15 menit untuk semua fase
        self.handler = handler
        self.p1_id = handler.p1_id
        self.p2_id = handler.p2_id
        
        # --- State Management ---
        self.phase = 'ban'  # alur: 'ban' -> 'pick' -> 'setup'
        self.current_player_id = self.p1_id
        
        # --- State untuk Fase Setup ---
        self.p1_equipment = {}
        self.p2_equipment = {}
        self.p1_inventory = []
        self.p2_inventory = []
        self.setup_selected_slot = None

    async def initialize_data(self):
        # --- Player 1 ---
        # Ambil inventory asli dan item yang sedang dipakai
        p1_inv = await get_player_inventory(self.handler.bot.db, self.p1_id)
        p1_equipped_items = await get_player_equipment(self.handler.bot.db, self.p1_id)
        self.p1_inventory = p1_inv + list(p1_equipped_items.values())
        self.p1_equipment = {}

        # --- Player 2 ---
        # Lakukan hal yang sama untuk pemain kedua
        p2_inv = await get_player_inventory(self.handler.bot.db, self.p2_id)
        p2_equipped_items = await get_player_equipment(self.handler.bot.db, self.p2_id)
        self.p2_inventory = p2_inv + list(p2_equipped_items.values())
        self.p2_equipment = {}

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id not in [self.p1_id, self.p2_id]:
             await interaction.response.send_message("Kamu bukan peserta pertandingan ini.", ephemeral=True)
             return False
        if interaction.user.id != self.current_player_id:
            await interaction.response.send_message("Harap tunggu, ini bukan giliranmu!", ephemeral=True)
            return False
        return True

    def build_components(self):
        """Membangun ulang komponen UI secara dinamis berdasarkan fase saat ini."""
        self.clear_items()
        
        # --- Fase Banning ---
        if self.phase == 'ban':
            bannable = [t for t in self.handler.bot.titles if t.get('rarity') in ['Epic', 'Legendary']]
            options = [discord.SelectOption(label=t['name'], value=str(t['id'])) for t in bannable[:25]]
            if not options: options.append(discord.SelectOption(label="Tidak ada title untuk di-ban", value="disabled"))
            select = discord.ui.Select(placeholder="Pilih Title untuk di-BAN...", options=options)
            select.callback = self.phase_select_callback
            self.add_item(select)
            
        # --- Fase Picking ---
        elif self.phase == 'pick':
            banned_ids = list(self.handler.state['bans'].values())
            owned_ids = self.handler.state['owned_titles'][self.current_player_id]
            pickable = [self.handler.bot.get_title_by_id(tid) for tid in owned_ids if tid not in banned_ids and self.handler.bot.get_title_by_id(tid)]
            options = [discord.SelectOption(label=t['name'], value=str(t['id'])) for t in pickable[:25]]
            if not options: options.append(discord.SelectOption(label="Tidak ada title yang bisa dipilih", value="disabled"))
            select = discord.ui.Select(placeholder="Pilih Title yang akan digunakan...", options=options)
            select.callback = self.phase_select_callback
            self.add_item(select)
            
        # --- Fase Setup Equipment ---
        elif self.phase == 'setup':
            # Jika belum memilih slot (tampilan awal setup)
            if self.setup_selected_slot is None:
                slots = ["helm", "armor", "pants", "shoes", "artifact"]
                options = [discord.SelectOption(label=s.capitalize(), value=s) for s in slots]
                select = discord.ui.Select(placeholder="Pilih slot equipment untuk diubah...", options=options)
                select.callback = self.setup_slot_callback
                self.add_item(select)
            # Jika sudah memilih slot (tampilan pemilihan item)
            else:
                inventory = self.p1_inventory if self.current_player_id == self.p1_id else self.p2_inventory
                item_counts = Counter(inventory)
                options = [discord.SelectOption(label="[ Lepas Item ]", value="unequip")]
                for item_id in set(inventory):
                    item = self.handler.bot.get_item_by_id(item_id)
                    if item and item.get('type') == self.setup_selected_slot:
                        options.append(discord.SelectOption(label=f"{item['name']} (x{item_counts[item_id]})", value=str(item_id)))
                
                select = discord.ui.Select(placeholder=f"Pilih item untuk slot {self.setup_selected_slot}...", options=options)
                select.callback = self.setup_item_callback
                self.add_item(select)
                
                back_button = discord.ui.Button(label="Kembali", style=discord.ButtonStyle.secondary)
                back_button.callback = self.setup_back_callback
                self.add_item(back_button)

            # Tombol ini selalu ada di fase setup
            lock_button = discord.ui.Button(label="Kunci Pilihan", style=discord.ButtonStyle.success, emoji="✅")
            lock_button.callback = self.setup_lock_callback
            self.add_item(lock_button)
    
    def create_embed(self) -> discord.Embed:
        """
        Membuat embed yang sesuai dengan fase saat ini,
        dengan logika "Blind Pick" untuk fase pick dan setup.
        """
        p1_user, p2_user = self.handler.p1_user, self.handler.p2_user
        turn_user = self.handler.bot.get_user(self.current_player_id)
        
        # --- FASE BANNING (Tampilan tetap sama, karena ban bersifat publik) ---
        if self.phase == 'ban':
            embed = discord.Embed(title="⚔️ Fase Banning ⚔️", description=f"Giliran **{turn_user.mention}** memilih Title untuk dilarang.", color=BotColors.ERROR)
            embed.set_thumbnail(url=turn_user.display_avatar.url)
            p1_ban_text = f"`{self.handler.get_title_name(self.handler.state['bans'][self.p1_id])}`"
            p2_ban_text = f"`{self.handler.get_title_name(self.handler.state['bans'][self.p2_id])}`"
            embed.add_field(name=f"{p1_user.display_name} Ban", value=p1_ban_text, inline=True)
            embed.add_field(name=f"{p2_user.display_name} Ban", value=p2_ban_text, inline=True)
            return embed
            
        # --- FASE PICKING (Tampilan Baru dengan Blind Pick) ---
        elif self.phase == 'pick':
            embed = discord.Embed(title="👑 Fase Picking Rahasia 👑", description=f"Giliran **{turn_user.mention}** untuk memilih Title. Pilihanmu akan disembunyikan dari lawan.", color=BotColors.RARE)
            embed.set_thumbnail(url=turn_user.display_avatar.url)

            # Logika Tampilan untuk Player 1
            p1_pick_id = self.handler.state['picks'][self.p1_id]
            if p1_pick_id:
                p1_status_emoji, p1_status_text = "✅", "Pilihan Terkunci"
                p1_pick_text = "`✅ Pilihan Terkunci`" # Sembunyikan pilihan
            elif self.current_player_id == self.p1_id:
                p1_status_emoji, p1_status_text = "🤔", "Memilih..."
                p1_pick_text = "*Belum ada*"
            else: # Menunggu
                p1_status_emoji, p1_status_text = "⏳", "Menunggu..."
                p1_pick_text = "*Belum ada*"
            
            p1_value = f"{p1_status_emoji} **Status:** {p1_status_text}\n👑 **Pick:** {p1_pick_text}"
            embed.add_field(name=p1_user.display_name, value=p1_value, inline=False)

            # Logika Tampilan untuk Player 2
            p2_pick_id = self.handler.state['picks'][self.p2_id]
            if p2_pick_id:
                p2_status_emoji, p2_status_text = "✅", "Pilihan Terkunci"
                p2_pick_text = "`✅ Pilihan Terkunci`" # Sembunyikan pilihan
            elif self.current_player_id == self.p2_id:
                p2_status_emoji, p2_status_text = "🤔", "Memilih..."
                p2_pick_text = "*Belum ada*"
            else: # Menunggu
                p2_status_emoji, p2_status_text = "⏳", "Menunggu..."
                p2_pick_text = "*Belum ada*"

            p2_value = f"{p2_status_emoji} **Status:** {p2_status_text}\n👑 **Pick:** {p2_pick_text}"
            embed.add_field(name=p2_user.display_name, value=p2_value, inline=False)

            banned_names = [f"`{self.handler.get_title_name(tid)}`" for tid in self.handler.state['bans'].values() if tid]
            embed.set_footer(text=f"Title dilarang: {', '.join(banned_names) or 'Tidak ada'}")
            return embed
            
        # --- FASE SETUP (Tampilan Baru dengan Reveal Title & Blind Setup) ---
        elif self.phase == 'setup':
            embed = discord.Embed(title="🛠️ Fase Setup Equipment 🛠️", description=f"Pilihan Title terungkap! Sekarang giliran **{turn_user.mention}** mengatur equipment secara rahasia.", color=BotColors.EPIC)
            embed.set_thumbnail(url=turn_user.display_avatar.url)
            
            # 1. Bagian Reveal Title
            p1_pick_name = f"`{self.handler.get_title_name(self.handler.state['picks'][self.p1_id])}`"
            p2_pick_name = f"`{self.handler.get_title_name(self.handler.state['picks'][self.p2_id])}`"
            reveal_text = f"**{p1_user.display_name}:** {p1_pick_name}\n**{p2_user.display_name}:** {p2_pick_name}"
            embed.add_field(name="📜 Pilihan Title Terungkap!", value=reveal_text, inline=False)

            # 2. Bagian Setup Equipment (Hanya untuk pemain saat ini)
            current_equipment = self.p1_equipment if self.current_player_id == self.p1_id else self.p2_equipment
            slot_map = {"helm": "🧢", "armor": "👕", "pants": "👖", "shoes": "👢", "artifact": "🔮"}
            
            equipment_lines = []
            for slot, emoji in slot_map.items():
                item_name = "Kosong"
                if item_id := current_equipment.get(slot):
                    if item_data := self.handler.bot.get_item_by_id(item_id):
                        item_name = item_data['name']
                equipment_lines.append(f"{emoji} **{slot.capitalize()}:** `{item_name}`")
            
            equipment_display = "\n".join(equipment_lines)
            embed.add_field(name=f"Pengaturan Equipment - {turn_user.display_name}", value=equipment_display, inline=False)
            
            # 3. Footer Status
            p1_status = "✅ Terkunci" if self.handler.state['ready'][self.p1_id] else "Menyiapkan..."
            p2_status = "✅ Terkunci" if self.handler.state['ready'][self.p2_id] else ("Menyiapkan..." if self.current_player_id == self.p2_id else "Menunggu")
            embed.set_footer(text=f"Status: {p1_user.display_name} [{p1_status}] | {p2_user.display_name} [{p2_status}]")
            return embed
            
    async def _update_view(self, interaction: discord.Interaction):
        """Fungsi sentral untuk memperbarui pesan setelah setiap aksi."""
        embed = self.create_embed()
        self.build_components()
        await interaction.edit_original_response(embed=embed, view=self)

    # --- CALLBACKS DENGAN ERROR HANDLING ---
    
    async def phase_select_callback(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer()
            if interaction.data['values'][0] == "disabled": return
            
            selected_id = int(interaction.data['values'][0])
            
            # [PERBAIKAN 1] Gunakan kunci yang benar ('bans' dan 'picks')
            current_phase_key = f"{self.phase}s" # Mengubah 'ban' -> 'bans', 'pick' -> 'picks'

            if self.phase == 'ban': 
                self.handler.state['bans'][self.current_player_id] = selected_id
            elif self.phase == 'pick': 
                self.handler.state['picks'][self.current_player_id] = selected_id

            # Ganti giliran pemain
            self.current_player_id = self.p2_id if self.current_player_id == self.p1_id else self.p1_id

            # [PERBAIKAN 2] Gunakan kunci yang benar ('bans' dan 'picks') saat mengecek
            if all(self.handler.state[current_phase_key].values()):
                if self.phase == 'ban': 
                    self.phase = 'pick'
                    self.current_player_id = self.p1_id # Mulai lagi dari P1 untuk picking
                elif self.phase == 'pick': 
                    self.phase = 'setup'
                    self.current_player_id = self.p1_id # Mulai lagi dari P1 untuk setup
            
            # Panggil fungsi update
            await self._update_view(interaction)

        except Exception as e:
            print(f"!! TERJADI ERROR DI phase_select_callback: {e}")
            import traceback
            traceback.print_exc()
            # Gunakan followup jika sudah di-defer
            await interaction.followup.send("Terjadi error internal. Mohon hubungi admin dan periksa konsol.", ephemeral=True)


    async def setup_slot_callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self.setup_selected_slot = interaction.data['values'][0]
        await self._update_view(interaction)

    async def setup_item_callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        selected_value = interaction.data['values'][0]
        equipment = self.p1_equipment if self.current_player_id == self.p1_id else self.p2_equipment
        inventory = self.p1_inventory if self.current_player_id == self.p1_id else self.p2_inventory

        if equipped_id := equipment.get(self.setup_selected_slot): 
            inventory.append(equipped_id)
            
        if selected_value == "unequip": 
            equipment.pop(self.setup_selected_slot, None)
        else:
            new_item_id = int(selected_value)
            equipment[self.setup_selected_slot] = new_item_id
            if new_item_id in inventory:
                inventory.remove(new_item_id)
        
        self.setup_selected_slot = None
        await self._update_view(interaction)
        
    async def setup_back_callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self.setup_selected_slot = None
        await self._update_view(interaction)

    async def setup_lock_callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        if self.current_player_id == self.p1_id:
            self.handler.register_setup(self.p1_id, self.p1_equipment)
            self.current_player_id = self.p2_id
            await self._update_view(interaction)
        else:
            self.handler.register_setup(self.p2_id, self.p2_equipment)
            for item in self.children: item.disabled = True
            # Jangan panggil _update_view lagi, cukup edit view-nya saja
            await interaction.edit_original_response(view=self) 
            await self.handler.prepare_for_fight()

    async def on_timeout(self):
        await self.handler.handle_timeout(self.current_player_id)

# ===================================================================================
# --- KELAS PENGELOLA PERTANDINGAN ---
# ===================================================================================

class MatchHandler:
    def __init__(self, bot: commands.Bot, cog: 'TournamentCog', p1_id: int, p2_id: int):
        self.bot = bot
        self.cog = cog
        self.p1_id, self.p2_id = p1_id, p2_id
        self.p1_user, self.p2_user = bot.get_user(p1_id), bot.get_user(p2_id)
        self.message: Optional[discord.Message] = None
        self.state = {
            'phase': 'ban', 'bans': {p1_id: None, p2_id: None}, 'picks': {p1_id: None, p2_id: None},
            'setups': {p1_id: None, p2_id: None}, 'ready': {p1_id: False, p2_id: False},
            'owned_titles': {p1_id: [], p2_id: []}, 'forfeit_winner': None
        }

    def get_title_name(self, title_id: int) -> str:
        """Helper untuk mendapatkan nama title dari ID."""
        if not title_id: return "Belum Memilih"
        title = self.bot.get_title_by_id(title_id)
        return title['name'] if title else "ID Tidak Valid"

    async def start_match_flow(self):
        """Memulai seluruh alur persiapan sebelum pertarungan."""
        # Ambil data title milik pemain
        self.state['owned_titles'][self.p1_id] = await get_player_titles(self.bot.db, self.p1_id)
        self.state['owned_titles'][self.p2_id] = await get_player_titles(self.bot.db, self.p2_id)

        view = ChannelPhaseView(self)
        await view.initialize_data()
        
        embed = view.create_embed()
        view.build_components()

        self.message = await self.cog.get_stage_channel().send(
            content=f"**Persiapan Pertandingan:** {self.p1_user.mention} vs {self.p2_user.mention}",
            embed=embed, view=view
        )
        
    def register_setup(self, player_id: int, equipment: dict):
        """Mencatat equipment yang dipilih pemain dan menandai mereka sebagai siap."""
        self.state['setups'][player_id] = equipment
        self.state['ready'][player_id] = True

    async def prepare_for_fight(self):
        """Membangun data pemain dan menyerahkannya ke fight_cog."""
        await self.message.edit(content="Semua persiapan selesai! Memulai pertarungan...", embed=None, view=None)
        p1_data = await self.build_participant_data(self.p1_id)
        p2_data = await self.build_participant_data(self.p2_id)
        
        if p1_data and p2_data:
            await self.cog.initiate_fight(p1_data, p2_data)
        else:
            await self.cog.get_stage_channel().send("Terjadi kesalahan saat mempersiapkan data pertarungan. Pertandingan dibatalkan.")
            # Melaporkan hasil default agar turnamen bisa lanjut
            winner_mock = {'id': self.p2_id, 'name': self.p2_user.display_name} if not p1_data else {'id': self.p1_id, 'name': self.p1_user.display_name}
            loser_mock = {'id': self.p1_id, 'name': self.p1_user.display_name} if not p1_data else {'id': self.p2_id, 'name': self.p2_user.display_name}
            await self.cog.report_match_result(winner_mock, loser_mock)

    async def build_participant_data(self, player_id: int) -> Optional[Dict]:
        """
        [DIPERBARUI] Membuat 'snapshot' data pemain untuk pertarungan dengan
        base stat yang disetarakan ke Level 80.
        """
        try:
            player_user = self.bot.get_user(player_id)
            # Data asli tetap diambil untuk info non-stat seperti agensi
            player_data = await get_player_data(self.bot.db, player_id) 
            title_id = self.state['picks'][player_id]
            equipment = self.state['setups'][player_id]
            title_data = self.bot.get_title_by_id(title_id)

            # [TURNAMEN ADIL] Alih-alih mengambil dari database, gunakan stat Level 80 yang sudah di-hardcode.
            # Base stat dasar (crit, dll) tetap dipertahankan.
            base_stats = {
                'hp': 1285, 'atk': 247, 'def': 163, 'spd': 89,
                'crit_rate': 0.05, 'crit_damage': 1.5, 'lifesteal': 0.0
            }
            
            # Tambahkan bonus agensi (tetap berlaku untuk identitas pemain)
            agency_id = player_data.get('agency_id')
            if agency_id == "ateliernova": base_stats['crit_rate'] += 0.03
            elif agency_id == "projectabyssal": base_stats['lifesteal'] += 0.05
            elif agency_id == "react_entertainment": base_stats['crit_damage'] += 0.15
            
            final_stats = base_stats.copy()
            
            # Tambahkan stat dari title
            for stat, value in title_data.get('stat_boost', {}).items():
                if stat in final_stats: final_stats[stat] += value
                
            # Tambahkan stat dari equipment yang dipilih di fase setup
            for item_id in equipment.values():
                if item_id and (item_data := self.bot.get_item_by_id(item_id)):
                    for stat, value in item_data.get('stat_boost', {}).items():
                        stat_key = 'crit_damage' if stat == 'crit_damage' else stat
                        if stat_key in final_stats: final_stats[stat_key] += value
            
            return {
                "id": player_id, "name": player_user.display_name, "avatar_url": player_user.display_avatar.url,
                "is_player": True, "agency_id": agency_id,
                "stats": final_stats, "base_stats": base_stats, # base_stats sekarang adalah stat Lv 80
                "skills": title_data.get('skills', []), "raw_title_data": title_data,
                "hp": final_stats['hp'], "max_hp": final_stats['hp'],
                "status_effects": [], "passive_flags": {},
                "skill_cooldowns": {s['name']: 0 for s in title_data.get('skills', []) if s.get('type') == 'active'}
            }
        except Exception as e:
            print(f"Error building participant data for {player_id}: {e}")
            return None

    async def handle_timeout(self, timed_out_player_id: int):
        """Menangani jika pemain AFK selama fase persiapan."""
        if self.state['forfeit_winner']: return # Mencegah double trigger
        
        winner_id = self.p2_id if timed_out_player_id == self.p1_id else self.p1_id
        self.state['forfeit_winner'] = winner_id
        
        winner_user = self.bot.get_user(winner_id)
        loser_user = self.bot.get_user(timed_out_player_id)
        
        # [PERBAIKAN KUNCI] Bungkus dalam try-except untuk menangani pesan yang hilang
        try:
            # Coba edit pesan yang ada seperti biasa
            if self.message:
                await self.message.edit(
                    content=f"Pemain {loser_user.mention} tidak menyelesaikan fase **{self.state['phase']}** tepat waktu. **{winner_user.mention}** memenangkan pertandingan secara default!",
                    embed=None, view=None
                )
        except discord.NotFound:
            # Jika pesan tidak ditemukan, kirim pesan baru sebagai gantinya
            # agar pengumuman tetap ada.
            await self.cog.get_stage_channel().send(
                f"Pemain {loser_user.mention} tidak merespons tepat waktu. **{winner_user.mention}** memenangkan pertandingan secara default!"
            )
        except Exception as e:
            # Menangkap error tak terduga lainnya untuk debugging
            print(f"Error saat mengedit pesan timeout: {e}")
        
        # Buat data palsu untuk melaporkan hasil
        winner_mock = {'id': winner_id, 'name': winner_user.display_name}
        loser_mock = {'id': timed_out_player_id, 'name': loser_user.display_name}
        
        # Logika ini sekarang akan selalu berjalan, bahkan jika pesan aslinya hilang
        await self.cog.report_match_result(winner_mock, loser_mock)

# ===================================================================================
# --- COG UTAMA ---
# ===================================================================================

class TournamentCog(commands.Cog, name="Turnamen"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.tournament_state: Optional[Dict] = None
        self.announcement_channel_id = int(os.getenv("TOURNAMENT_ANNOUNCEMENT_CHANNEL_ID", 0))
        self.stage_channel_id = int(os.getenv("TOURNAMENT_STAGE_CHANNEL_ID", 0))

    def get_announcement_channel(self) -> Optional[discord.TextChannel]:
        return self.bot.get_channel(self.announcement_channel_id)

    def get_stage_channel(self) -> Optional[discord.TextChannel]:
        return self.bot.get_channel(self.stage_channel_id)
    
    async def _create_match_analysis_embed(self, winner: dict, loser: dict) -> discord.Embed:
        """Membuat embed analisis yang detail setelah pertandingan selesai."""
        try:
            winner_user = await self.bot.fetch_user(winner['id'])
            loser_user = await self.bot.fetch_user(loser['id'])

            # Tentukan seberapa sengit pertarungannya
            hp_percentage = winner.get('hp', 0) / winner.get('max_hp', 1)
            if hp_percentage < 0.15:
                match_summary = f"Sebuah pertarungan yang sangat sengit hingga napas terakhir!"
            elif hp_percentage < 0.5:
                match_summary = f"Kemenangan yang diraih dengan susah payah."
            else:
                match_summary = f"Sebuah kemenangan yang dominan!"

            embed = discord.Embed(
                title="📊 Analisis Pertandingan Turnamen 📊",
                description=f"**{winner_user.mention}** berhasil mengalahkan **{loser_user.mention}**!\n_{match_summary}_",
                color=discord.Color.gold()
            )
            embed.set_thumbnail(url=winner_user.display_avatar.url)

            # --- Data Pemenang ---
            winner_title = winner.get('raw_title_data', {}).get('name', 'Tidak Diketahui')
            winner_stats = winner.get('stats', {})
            winner_stats_str = (
                f"**Title:** `{winner_title}`\n"
                f"**Sisa HP:** `{winner.get('hp', 0)}/{winner.get('max_hp', 1)}`\n"
                f"**Stat Akhir:** ⚔️`{winner_stats.get('atk',0)}` 🛡️`{winner_stats.get('def',0)}` 💨`{winner_stats.get('spd',0)}`"
            )
            embed.add_field(name=f"🏆 {winner['name']}", value=winner_stats_str, inline=True)

            # --- Data yang Kalah ---
            loser_title = loser.get('raw_title_data', {}).get('name', 'Tidak Diketahui')
            loser_stats = loser.get('stats', {})
            loser_stats_str = (
                f"**Title:** `{loser_title}`\n"
                f"**Sisa HP:** `0/{loser.get('max_hp', 1)}`\n"
                f"**Stat Akhir:** ⚔️`{loser_stats.get('atk',0)}` 🛡️`{loser_stats.get('def',0)}` 💨`{loser_stats.get('spd',0)}`"
            )
            embed.add_field(name=f"☠️ {loser['name']}", value=loser_stats_str, inline=True)

            embed.set_footer(text="Pertandingan berikutnya akan segera dimulai...")
            return embed
        except Exception as e:
            print(f"Error saat membuat embed analisis: {e}")
            return discord.Embed(title="Error", description="Gagal membuat analisis pertandingan.")

    async def create_bracket(self):
        """Mengacak peserta dan membuat bracket awal."""
        participants = list(self.tournament_state['participants'])
        random.shuffle(participants)
        
        bye_player = None
        if len(participants) % 2 != 0:
            bye_player = participants.pop()
            
        matches = [{'p1': participants[i], 'p2': participants[i+1], 'winner': None} for i in range(0, len(participants), 2)]
        self.tournament_state['bracket'].append(matches)
        
        if bye_player:
            self.tournament_state['bye_player'] = bye_player

    def format_bracket(self, round_num: int) -> str:
        """Memformat data bracket menjadi string yang bisa dibaca."""
        if not self.tournament_state or not self.tournament_state['bracket']: return "Bracket belum dibuat."
        
        round_matches = self.tournament_state['bracket'][round_num]
        lines = [f"**--- Babak {round_num + 1} ---**"]
        for match in round_matches:
            lines.append(f"• <@{match['p1']}> vs <@{match['p2']}>")
            
        if self.tournament_state.get('bye_player') and round_num == 0:
            lines.append(f"• <@{self.tournament_state['bye_player']}> mendapatkan *bye*!")
            
        return "\n".join(lines)

    async def start_tournament(self):
        self.tournament_state['state'] = 'in_progress'
        announcement_channel = self.get_announcement_channel()
        stage_channel = self.get_stage_channel()
        
        if msg_id := self.tournament_state.get('message_id'):
            try:
                msg = await announcement_channel.fetch_message(msg_id)
                await msg.delete() 
            except (discord.NotFound, AttributeError): 
                pass

        await self.create_bracket()
        bracket_embed = discord.Embed(title="⚔️ Bracket Turnamen Telah Dibuat! ⚔️", description=self.format_bracket(0), color=BotColors.RARE)
        
        await announcement_channel.send(embed=bracket_embed)
        await stage_channel.send(f"**Turnamen Dimulai!**", embed=bracket_embed)
        
        await asyncio.sleep(3)
        await self.start_next_match()

    async def start_next_match(self):
        # [FINAL BO3] Cek apakah kita sedang dalam seri final
        if self.tournament_state.get('final_series', {}).get('is_active'):
            series = self.tournament_state['final_series']
            series['match_count'] += 1
            p1_id, p2_id = series['finalists']

            match_announcement = f"**GRAND FINAL: Pertandingan #{series['match_count']}**"
            if series['match_count'] == 3:
                match_announcement = f"**GRAND FINAL: Pertandingan Penentuan!**"

            await self.get_stage_channel().send(f"🔥 {match_announcement} 🔥")
            
            handler = MatchHandler(self.bot, self, p1_id, p2_id)
            await handler.start_match_flow()
            return

        # Logika untuk babak-babak biasa
        current_round_index = len(self.tournament_state['bracket']) - 1
        current_round = self.tournament_state['bracket'][current_round_index]
        
        next_match_data = next((match for match in current_round if match['winner'] is None), None)
        
        if next_match_data:
            self.tournament_state['current_match'] = next_match_data
            handler = MatchHandler(self.bot, self, next_match_data['p1'], next_match_data['p2'])
            await handler.start_match_flow()
        else:
            await self.advance_to_next_round()

    async def initiate_fight(self, p1_data: dict, p2_data: dict):
        stage_channel = self.get_stage_channel()
        await stage_channel.send(f"Pertarungan antara **{p1_data['name']}** dan **{p2_data['name']}** akan segera dimulai!")
        
        fight_cog = self.bot.get_cog("Pertarungan")
        if fight_cog:
            await fight_cog.start_fight_session(
                stage_channel, 
                p1_data, 
                p2_data, 
                on_finish_callback=self.report_match_result, 
                is_tourney_match=True
            )
        else:
            await stage_channel.send("Error: Cog pertarungan tidak ditemukan.")

    async def report_match_result(self, winner: dict, loser: dict):
        if not self.tournament_state or self.tournament_state.get('state') != 'in_progress':
            print("INFO: report_match_result dipanggil, tetapi tidak ada turnamen aktif. Mengabaikan.")
            return

        announcement_channel = self.get_announcement_channel()
        stage_channel = self.get_stage_channel()
        winner_id = winner['id']
        
        # [FINAL BO3] Logika baru jika sedang dalam seri final
        if self.tournament_state.get('final_series', {}).get('is_active'):
            series = self.tournament_state['final_series']
            series['score'][winner_id] += 1
            
            p1_id, p2_id = series['finalists']
            score_text = f"Skor saat ini: <@{p1_id}> **{series['score'][p1_id]}** - **{series['score'][p2_id]}** <@{p2_id}>"

            # Kirim analisis ke channel pengumuman
            if announcement_channel:
                analysis_embed = await self._create_match_analysis_embed(winner, loser)
                analysis_embed.title = f"📊 Analisis Grand Final - Match {series['match_count']} 📊"
                analysis_embed.add_field(name="Skor Seri", value=score_text, inline=False)
                await announcement_channel.send(embed=analysis_embed)

            # Cek apakah seri sudah berakhir
            if series['score'][winner_id] == 2:
                await stage_channel.send(f"🎉 **{winner['name']}** memenangkan seri Grand Final dengan skor **2-{series['score'][loser['id']]}**!")
                await self.end_tournament(champion_id=winner_id)
                return

            # Jika belum berakhir, umumkan skor dan lanjut
            await stage_channel.send(f"**<@{winner_id}>** memenangkan pertandingan #{series['match_count']}!\n{score_text}")
            
            # Jika skor 1-1, beri pengumuman khusus
            if series['match_count'] == 2 and series['score'][p1_id] == 1:
                await stage_channel.send("Skor seimbang 1-1! Pertandingan berikutnya adalah penentuan juara!")
            
            await asyncio.sleep(5)
            await self.start_next_match()
            return
            
        # Logika lama untuk babak-babak biasa
        current_match = self.tournament_state.get('current_match')
        if not current_match:
             print("WARNING: report_match_result dipanggil, tapi current_match kosong.")
             return

        current_match['winner'] = winner_id
        
        if announcement_channel:
            analysis_embed = await self._create_match_analysis_embed(winner, loser)
            await announcement_channel.send(embed=analysis_embed)

        quest_cog = self.bot.get_cog("Misi")
        if quest_cog:
            await quest_cog.update_quest_progress(winner_id, 'WIN_TOURNAMENT_MATCH')

        await stage_channel.send(f"Selamat <@{winner_id}>, kamu memenangkan pertandingan ini dan maju ke babak selanjutnya!")
        
        self.tournament_state['current_match'] = None
        
        await asyncio.sleep(5)
        await self.start_next_match()

    async def advance_to_next_round(self):
        announcement_channel, stage_channel = self.get_announcement_channel(), self.get_stage_channel()
        last_round_index = len(self.tournament_state['bracket']) - 1
        
        winners = [match['winner'] for match in self.tournament_state['bracket'][last_round_index]]
        
        if bye_player := self.tournament_state.pop('bye_player', None):
            winners.append(bye_player)
            
        # [FINAL BO3] Logika baru untuk mendeteksi babak final
        if len(winners) == 2:
            p1_id, p2_id = winners[0], winners[1]
            self.tournament_state['final_series'] = {
                'is_active': True,
                'finalists': [p1_id, p2_id],
                'score': {p1_id: 0, p2_id: 0},
                'match_count': 0
            }
            
            final_embed = discord.Embed(
                title="🔥 GRAND FINAL TELAH TIBA! 🔥",
                description=f"Dua penantang terakhir akan berhadapan dalam seri **Best of 3** untuk memperebutkan gelar juara!\n\n<@{p1_id}> vs <@{p2_id}>",
                color=discord.Color.red()
            )
            await announcement_channel.send(embed=final_embed)
            await stage_channel.send(embed=final_embed)
            
            await asyncio.sleep(5)
            await self.start_next_match()
            return
            
        # Cek jika turnamen selesai (seharusnya tidak terjadi jika ada minimal 2 peserta)
        if len(winners) == 1:
            await self.end_tournament(champion_id=winners[0])
            return
            
        random.shuffle(winners)
        
        bye_player = winners.pop() if len(winners) % 2 != 0 else None
        
        next_round_matches = [{'p1': winners[i], 'p2': winners[i+1], 'winner': None} for i in range(0, len(winners), 2)]
        self.tournament_state['bracket'].append(next_round_matches)
        
        if bye_player:
            self.tournament_state['bye_player'] = bye_player
            
        new_round_num = len(self.tournament_state['bracket'])
        bracket_embed = discord.Embed(
            title=f"⚔️ Babak {new_round_num} Telah Dimulai! ⚔️", 
            description=self.format_bracket(new_round_num - 1), 
            color=BotColors.SUCCESS
        )
        
        await announcement_channel.send(embed=bracket_embed)
        await stage_channel.send(embed=bracket_embed)
        
        await asyncio.sleep(5)
        await self.start_next_match()

    async def end_tournament(self, champion_id: Optional[int] = None, cancelled: bool = False):
        announcement_channel, stage_channel = self.get_announcement_channel(), self.get_stage_channel()
        
        if cancelled:
            msg_text = "Turnamen telah dibatalkan oleh penyelenggara."
            if announcement_channel: await announcement_channel.send(msg_text)
            if stage_channel: await stage_channel.send(msg_text)
            
        elif champion_id:
            champion_embed = discord.Embed(
                title="🏆 JUARA TURNAMEN! 🏆", 
                description=f"Selamat kepada <@{champion_id}> yang telah menjadi juara!", 
                color=discord.Color.gold()
            )
            champion_user = await self.bot.fetch_user(champion_id)
            champion_embed.set_thumbnail(url=champion_user.display_avatar.url)
            
            if announcement_channel: await announcement_channel.send(embed=champion_embed)
            if stage_channel: await stage_channel.send(embed=champion_embed)
            
            quest_cog = self.bot.get_cog("Misi")
            if quest_cog:
                await quest_cog.update_quest_progress(champion_id, 'BECOME_CHAMPION')
            
        self.tournament_state = None

    @commands.command(name="turnamen")
    @commands.is_owner()
    async def tournament(self, ctx: commands.Context):
        """Turnamen PvP yang hanya bisa di selenggarakan oleh Developer."""
        if self.tournament_state:
            return await ctx.send("Sudah ada turnamen yang sedang berlangsung.")
            
        announcement_channel = self.get_announcement_channel()
        if not announcement_channel:
            return await ctx.send("Channel pengumuman turnamen belum diatur. Mohon hubungi developer.")

        # [FINAL BO3] Tambahkan state 'final_series'
        self.tournament_state = {
            'host_id': ctx.author.id, 'state': 'registration', 'participants': set(),
            'bracket': [], 'current_match': None, 'bye_player': None, 'message_id': None,
            'final_series': {'is_active': False}
        }
        
        embed = discord.Embed(title="🏆 Pendaftaran Turnamen Dibuka! 🏆", description="Tekan tombol di bawah untuk mendaftar.", color=BotColors.DEFAULT)
        embed.add_field(name="👥 Peserta Terdaftar (0)", value="Belum ada yang bergabung.", inline=False)
        embed.set_footer(text="Penyelenggara akan memulai turnamen setelah pendaftaran ditutup.")
        
        view = RegistrationView(self)
        msg = await announcement_channel.send(embed=embed, view=view)
        self.tournament_state['message_id'] = msg.id

async def setup(bot: commands.Bot):
    await bot.add_cog(TournamentCog(bot))