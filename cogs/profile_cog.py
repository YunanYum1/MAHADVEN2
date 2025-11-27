import discord
from discord.ext import commands
import math
from collections import Counter
import os # Diperlukan untuk memeriksa path gambar

# Impor fungsi-fungsi yang dibutuhkan dari database
from database import (
    get_player_data,
    get_player_titles,
    set_equipped_title,
    get_player_equipment,
    get_player_inventory,
    update_player_equipment_and_inventory,
    get_player_upgrades
)
from ._utils import BotColors

# ===================================================================================
# --- KELAS-KELAS VIEW (UI INTERAKTIF) ---
# ===================================================================================

class ProfileView(discord.ui.View):
    """
    View interaktif yang telah diupgrade, mengelola semua state UI
    dan callback dari tombol/dropdown dengan alur equipment yang baru.
    """
    def __init__(self, author: discord.User, target: discord.User, cog: commands.Cog):
        super().__init__(timeout=300.0)
        self.author = author
        self.target = target
        self.cog = cog
        self.bot = cog.bot
        self.message: discord.Message = None

        # State Management
        self.current_page = "main"    # Halaman utama: 'main', 'equipment', 'title'
        self.title_page_index = 0     # Halaman untuk paginasi Title
        self.item_page_index = 0      # Halaman untuk paginasi Item
        self.selected_slot = None     # [KUNCI] Menyimpan slot equipment yg dipilih ('helm', 'armor', etc.)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("Ini bukan sesi profil milikmu!", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        if self.message:
            try:
                for item in self.children: item.disabled = True
                await self.message.edit(view=self)
            except discord.NotFound: pass

    async def _build_and_get_embed(self) -> discord.Embed:
        """Helper untuk mendapatkan embed yang sesuai dengan state saat ini."""
        player_data = await get_player_data(self.bot.db, self.target.id)
        if self.current_page == "main":
            return await self.cog._create_main_profile_embed(self.target, player_data)
        elif self.current_page == "equipment":
             return await self.cog._create_equipment_embed(self.target, player_data)
        elif self.current_page == "title":
            # Halaman title bisa punya gambar, jadi kita handle terpisah di _update_view
            return (await self.cog._create_title_detail_display(self.target, player_data))[0]

    async def _build_components(self):
        """Membangun ulang semua komponen UI berdasarkan state saat ini."""
        self.clear_items()

        # Tombol Navigasi Utama (selalu ada)
        self.add_item(NavButton(target_page="main", label="Profil", emoji="👤", style=self._get_button_style("main")))
        self.add_item(NavButton(target_page="equipment", label="Equipment", emoji="⚔️", style=self._get_button_style("equipment")))
        self.add_item(NavButton(target_page="title", label="Titles", emoji="👑", style=self._get_button_style("title")))

        # --- Logika Kondisional untuk Halaman Spesifik ---
        if self.current_page == "title":
            player_titles = await get_player_titles(self.bot.db, self.target.id) or []
            total_pages = math.ceil(len(player_titles) / 25) if player_titles else 1
            
            title_select = TitleSelect(self)
            await title_select.populate_options()
            self.add_item(title_select)
            
            self.add_item(PageButton(page_type='title', direction=-1, disabled=(self.title_page_index == 0), row=2))
            self.add_item(discord.ui.Button(label=f"Hal {self.title_page_index + 1}/{total_pages}", style=discord.ButtonStyle.secondary, disabled=True, row=2))
            self.add_item(PageButton(page_type='title', direction=1, disabled=(self.title_page_index >= total_pages - 1), row=2))

        elif self.current_page == "equipment":
            # [LOGIKA DIPERBAIKI] Atur ulang komponen ke baris yang berbeda untuk menghindari konflik
            if self.selected_slot is None:
                # Tampilkan dropdown untuk memilih slot di baris 1
                self.add_item(EquipSlotSelect(self)) 
            else:
                # --- Tampilan setelah slot dipilih ---

                # Tombol kembali di baris 1
                self.add_item(BackButton(row=1))

                # Dropdown untuk memilih item, sekarang kita tempatkan di baris 2
                equip_item_select = EquipItemSelect(self, row=2) # Berikan argumen row=2
                await equip_item_select.populate_options()
                self.add_item(equip_item_select)

                # Logika Paginasi untuk Item, kita pindahkan ke baris 3
                inventory_ids = await get_player_inventory(self.bot.db, self.target.id) or []
                matching_items = [item_id for item_id in set(inventory_ids) if self.cog.bot.get_item_by_id(item_id) and self.cog.bot.get_item_by_id(item_id).get('type') == self.selected_slot]
                total_pages = math.ceil(len(matching_items) / 24) or 1

                # Pindahkan semua tombol paginasi ke baris 3 (row=3)
                self.add_item(PageButton(page_type='item', direction=-1, disabled=(self.item_page_index == 0), row=3))
                self.add_item(discord.ui.Button(label=f"Hal {self.item_page_index + 1}/{total_pages}", style=discord.ButtonStyle.secondary, disabled=True, row=3))
                self.add_item(PageButton(page_type='item', direction=1, disabled=(self.item_page_index >= total_pages - 1), row=3))

    def _get_button_style(self, page_name: str) -> discord.ButtonStyle:
        return discord.ButtonStyle.primary if self.current_page == page_name else discord.ButtonStyle.secondary

    async def _update_view(self):
        """Fungsi terpusat untuk menggambar ulang seluruh panel."""
        await self._build_components()
        player_data = await get_player_data(self.bot.db, self.target.id)

        kwargs = {'view': self, 'attachments': []}
        if self.current_page == 'title':
            embed, file = await self.cog._create_title_detail_display(self.target, player_data)
            if file:
                kwargs['attachments'].append(file)
            kwargs['embed'] = embed
        else:
            embed = await self._build_and_get_embed()
            kwargs['embed'] = embed
        
        await self.message.edit(**kwargs)

# --- Komponen UI (Tombol & Dropdown) ---
class NavButton(discord.ui.Button):
    def __init__(self, target_page: str, **kwargs):
        super().__init__(**kwargs)
        self.target_page = target_page

    async def callback(self, interaction: discord.Interaction):
        view: ProfileView = self.view
        if view.current_page == self.target_page:
            return await interaction.response.defer()
        
        await interaction.response.defer()
        
        view.current_page = self.target_page
        view.title_page_index, view.selected_slot, view.item_page_index = 0, None, 0
        await view._update_view()

class PageButton(discord.ui.Button):
    def __init__(self, page_type: str, direction: int, **kwargs):
        super().__init__(label="◀️" if direction == -1 else "▶️", style=discord.ButtonStyle.secondary, **kwargs)
        self.page_type = page_type
        self.direction = direction

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        view: ProfileView = self.view
        if self.page_type == 'title': view.title_page_index += self.direction
        elif self.page_type == 'item': view.item_page_index += self.direction
        await view._update_view()

class BackButton(discord.ui.Button):
    """Tombol untuk kembali dari pemilihan item ke pemilihan slot."""
    def __init__(self, **kwargs):
        super().__init__(label="Kembali", emoji="⬅️", style=discord.ButtonStyle.danger, **kwargs)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        view: ProfileView = self.view
        view.selected_slot, view.item_page_index = None, 0
        await view._update_view()

class TitleSelect(discord.ui.Select):
    def __init__(self, parent_view: ProfileView):
        super().__init__(placeholder="Ganti & equip Title dari daftar...", row=1)
        self.parent_view = parent_view

    async def populate_options(self):
        player_data = await get_player_data(self.parent_view.cog.bot.db, self.parent_view.target.id)
        owned_titles_ids = await get_player_titles(self.parent_view.cog.bot.db, self.parent_view.target.id) or []
        current_title_id = player_data.get('equipped_title_id')
        page = self.parent_view.title_page_index
        titles_on_page_ids = owned_titles_ids[page * 25 : (page + 1) * 25]

        options = []
        for title_id in titles_on_page_ids:
            if title := self.parent_view.cog.bot.get_title_by_id(title_id):
                options.append(discord.SelectOption(
                    label=title['name'],
                    value=str(title['id']),
                    description=f"Rarity: {title.get('rarity', 'Common')}",
                    default=(title['id'] == current_title_id)
                ))
        if not options:
            self.options = [discord.SelectOption(label="Tidak ada title di halaman ini.", value="disabled")]
            self.disabled = True
        else:
            self.options = options
            self.disabled = False

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == 'disabled': return await interaction.response.defer()
        await interaction.response.defer()
        selected_title_id = int(self.values[0])
        await set_equipped_title(self.parent_view.cog.bot.db, interaction.user.id, selected_title_id)
        await self.parent_view._update_view()
        await interaction.followup.send(f"✅ Title berhasil diganti!", ephemeral=True, delete_after=5)

class EquipSlotSelect(discord.ui.Select):
    """[DIPERBAIKI] Dropdown untuk memilih slot equipment dengan penanganan callback yang lebih kokoh."""
    def __init__(self, parent_view: ProfileView):
        self.parent_view = parent_view
        options = [
            discord.SelectOption(label="Helm", value="helm", emoji="🧢"),
            discord.SelectOption(label="Armor", value="armor", emoji="👕"),
            discord.SelectOption(label="Celana", value="pants", emoji="👖"),
            discord.SelectOption(label="Sepatu", value="shoes", emoji="👢"),
            discord.SelectOption(label="Artefak", value="artifact", emoji="🔮"),
        ]
        super().__init__(placeholder="Pilih slot equipment untuk diganti...", options=options, row=1)

    async def callback(self, interaction: discord.Interaction):
        """
        Callback ini sekarang secara eksplisit menangani respons interaksi
        sebelum memperbarui view. Ini mencegah kondisi race dan timeout.
        """
        await interaction.response.defer()

        selected_slot_value = self.values[0]
        self.parent_view.selected_slot = selected_slot_value
        self.parent_view.item_page_index = 0 # Selalu reset halaman item saat ganti slot.
        # Langkah 3: Panggil fungsi utama untuk menggambar ulang seluruh tampilan.
        await self.parent_view._update_view()

class EquipItemSelect(discord.ui.Select):
    """Dropdown untuk memilih item dari inventory."""
    def __init__(self, parent_view: ProfileView, row: int): # Tambahkan 'row' sebagai argumen
        # Hapus 'row=1' dari sini agar bisa diatur secara dinamis
        super().__init__(placeholder=f"Pilih item untuk slot {parent_view.selected_slot.title()}...", row=row)
        self.parent_view = parent_view

    async def populate_options(self):
        """Mengisi dropdown dengan item yang cocok dari inventory."""
        inventory_ids = await get_player_inventory(self.parent_view.cog.bot.db, self.parent_view.target.id) or []
        item_counts = Counter(inventory_ids)

        # [PENYEMPURNAAN] Logika filter dibuat lebih eksplisit dan efisien.
        matching_item_ids = []
        for item_id in item_counts:
            # Panggil get_item_by_id sekali saja per item.
            if item := self.parent_view.cog.bot.get_item_by_id(item_id):
                # Cek apakah tipe item cocok dengan slot yang dipilih.
                if item.get('type') == self.parent_view.selected_slot:
                    matching_item_ids.append(item_id)
        
        # Logika paginasi
        page = self.parent_view.item_page_index
        items_on_page_ids = matching_item_ids[page * 24 : (page + 1) * 24]

        # Selalu tambahkan opsi untuk melepas item
        options = [discord.SelectOption(label="[ Lepas Item ]", value="unequip", emoji="❌")]
        for item_id in items_on_page_ids:
            if item := self.parent_view.cog.bot.get_item_by_id(item_id):
                stats_parts = [f"{k.upper()} {v:+}" for k, v in item.get('stat_boost', {}).items()]
                options.append(discord.SelectOption(label=f"{item['name']} (x{item_counts[item_id]})", value=str(item_id), description=' | '.join(stats_parts) or "Item kosmetik"))

        self.options = options
        if len(options) <= 1 and not items_on_page_ids:
            self.placeholder = f"Tidak ada {self.parent_view.selected_slot} di inventory."
            # Biarkan opsi "Lepas Item" tetap bisa diklik
        else:
            self.placeholder = f"Pilih item untuk slot {self.parent_view.selected_slot.title()}..."


    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        selected_value = self.values[0]
        slot_id = self.parent_view.selected_slot
        
        if selected_value == "unequip":
            # Melepas item dari slot
            await update_player_equipment_and_inventory(self.parent_view.cog.bot.db, interaction.user.id, slot_id, None)
            message_content = f"✅ Slot **{slot_id.capitalize()}** berhasil dikosongkan."
        else:
            # Memakai item baru
            new_item_id = int(selected_value)
            await update_player_equipment_and_inventory(self.parent_view.cog.bot.db, interaction.user.id, slot_id, new_item_id)
            item_data = self.parent_view.cog.bot.get_item_by_id(new_item_id)
            message_content = f"✅ Berhasil memakai **{item_data['name']}**!"

        # Reset state untuk kembali ke tampilan pemilihan slot
        self.parent_view.selected_slot = None
        self.parent_view.item_page_index = 0
        await self.parent_view._update_view()
        await interaction.followup.send(message_content, ephemeral=True, delete_after=5)


# ===================================================================================
# --- COG UTAMA ---
# ===================================================================================
class ProfileCog(commands.Cog, name="Profil"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
    
    # --- FUNGSI HELPER INTERNAL ---

    def _get_level_progress(self, total_exp: int):
        if total_exp < 0: total_exp = 0
        
        # 1. Hitung Level saat ini
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

    def _create_progress_bar(self, current: int, total: int, bar_length: int = 10) -> str:
        if total == 0: total = 1
        progress = round((current / total) * bar_length)
        return f"[{'▰' * progress}{'▱' * (bar_length - progress)}]"
    
    def _calculate_evasion_from_spd(self, player_spd: int) -> float:
        EVA_SCALING_FACTOR = 200
        MAX_EVA_FROM_SPD = 0.70
        return (player_spd / (player_spd + EVA_SCALING_FACTOR)) * MAX_EVA_FROM_SPD

    async def _get_total_and_bonus_stats(self, user_id: int, player_data: dict) -> tuple[dict, dict]:
        # Stat dasar dari database
        base_stats = {
            'hp': player_data.get('base_hp', 100), 'atk': player_data.get('base_atk', 10), 
            'def': player_data.get('base_def', 5), 'spd': player_data.get('base_spd', 10), 
            'crit_rate': 0.05, 'crit_dmg': 1.5 
        }
        bonus_stats = {key: 0 for key in base_stats}

        # Stat dari Title
        if title_id := player_data.get('equipped_title_id'):
            if title_data := self.bot.get_title_by_id(title_id):
                for stat, value in title_data.get('stat_boost', {}).items():
                    stat_key = 'crit_dmg' if stat == 'crit_damage' else stat
                    if stat_key in bonus_stats: bonus_stats[stat_key] += value

        # Stat dari Equipment + Upgrades
        player_equipment = await get_player_equipment(self.bot.db, user_id)
        player_upgrades = await get_player_upgrades(self.bot.db, user_id) 

        if player_equipment:
            for slot, item_id in player_equipment.items():
                if item_id and (item_data := self.bot.get_item_by_id(item_id)):
                    upgrade_data = player_upgrades.get(slot, {})
                    level = upgrade_data.get('level', 0)
                    
                    # Hitung Stat Dasar Item (Dengan Scaling)
                    for stat, value in item_data.get('stat_boost', {}).items():
                        stat_key = 'crit_dmg' if stat == 'crit_damage' else stat
                        final_value = value
                        
                        if value > 0:
                            # Scaling: 5% untuk Crit, 10% untuk Stat Biasa
                            if stat_key in ['crit_rate', 'crit_dmg']:
                                multiplier = 1 + (level * 0.05)
                                final_value = value * multiplier
                            else:
                                multiplier = 1 + (level * 0.10)
                                final_value = int(value * multiplier)
                        elif value < 0 and stat_key not in ['crit_rate', 'crit_dmg']:
                             # Stat minus membaik tiap 3 level
                             penalty_reduction = level // 3
                             final_value = min(0, value + penalty_reduction)
                        
                        if stat_key in bonus_stats:
                            bonus_stats[stat_key] += final_value

                    # Hitung Bonus Stat (Sub-stats dari Upgrade)
                    for stat, val in upgrade_data.get('bonus_stats', {}).items():
                        if stat in bonus_stats:
                            bonus_stats[stat] += val

        total_stats = {key: base_stats[key] + bonus_stats[key] for key in base_stats}
        return total_stats, bonus_stats

    def _format_stats_for_title(self, stats_dict: dict) -> str:
        parts = []
        stat_order = ['hp', 'atk', 'def', 'spd', 'crit_rate', 'crit_dmg']
        for stat in stat_order:
            # [PERBAIKAN] Cek 'crit_damage' dan 'crit_dmg' untuk fleksibilitas
            value = stats_dict.get(stat) or stats_dict.get('crit_damage' if stat == 'crit_dmg' else '')
            if value is not None and value != 0:
                if stat == 'hp': parts.append(f"❤️ HP `+{value}`")
                elif stat == 'atk': parts.append(f"⚔️ ATK `+{value}`")
                elif stat == 'def': parts.append(f"🛡️ DEF `+{value}`")
                elif stat == 'spd': parts.append(f"💨 SPD `+{value}`")
                elif stat == 'crit_rate': parts.append(f"🎯 Crit Rate `+{value*100:.0f}%`")
                elif stat == 'crit_dmg': parts.append(f"💥 Crit DMG `+{value*100:.0f}%`")
        return ' | '.join(parts) if parts else "Tidak ada bonus stat."

    # --- FUNGSI PEMBUAT EMBED ---

    async def _create_main_profile_embed(self, user: discord.Member, player_data: dict) -> discord.Embed:
        total_stats, bonus_stats = await self._get_total_and_bonus_stats(user.id, player_data)
        level, current_exp, needed_exp = self._get_level_progress(player_data.get('exp', 0))
        title_data = self.bot.get_title_by_id(player_data.get('equipped_title_id'))

        rarity = title_data.get("rarity", "Common").capitalize()
        rarity_colors = {
            "Common": BotColors.COMMON, "Rare": BotColors.RARE,
            "Epic": BotColors.EPIC, "Legendary": BotColors.LEGENDARY
        }
        embed_color = rarity_colors.get(rarity, BotColors.DEFAULT)

        embed = discord.Embed(title=f"Statistik Utama - {user.display_name}", color=embed_color).set_thumbnail(url=user.display_avatar.url)
        embed.add_field(name=f"👑 Title: {title_data['name'] if title_data else 'Tidak ada'}", value=f"**Level {level}**\n{self._create_progress_bar(current_exp, needed_exp)} `{current_exp:,} / {needed_exp:,}` EXP", inline=False)

        def format_line(stat_name, emoji, total, bonus, is_percent=False, is_multiplier=False):
            if is_percent:
                bonus_str = f" (+{bonus:.1%})" if bonus != 0 else ""
                total_str = f"{total:.1%}"
            elif is_multiplier:
                bonus_str = f" (+{bonus:.2f})" if bonus != 0 else ""
                total_str = f"x{total:.2f}"
            else:
                bonus_str = f" (+{bonus:,})" if bonus != 0 else ""
                total_str = f"{total:,}"
            
            return f"{emoji} **{stat_name}:** `{total_str}`{bonus_str}"
        
        stats_display = "\n".join([format_line("HP", "❤️", total_stats['hp'], bonus_stats['hp']), format_line("ATK", "⚔️", total_stats['atk'], bonus_stats['atk']), format_line("DEF", "🛡️", total_stats['def'], bonus_stats['def']), format_line("SPD", "💨", total_stats['spd'], bonus_stats['spd'])])
        embed.add_field(name="📊 Statistik Utama", value=stats_display, inline=True)
        
        evasion_from_spd = self._calculate_evasion_from_spd(total_stats['spd'])
        sub_stats_display = "\n".join([
            format_line("Crit Rate", "🎯", total_stats['crit_rate'], bonus_stats['crit_rate'], is_percent=True),
            # [PERUBAHAN] Crit DMG sekarang menggunakan format persentase (is_percent=True)
            format_line("Crit DMG", "💥", total_stats['crit_dmg'], bonus_stats['crit_dmg'], is_percent=True),
            f"🍃 **Evasion:** `{evasion_from_spd:.1%}`"
        ])
        embed.add_field(name="⚔️ Sub-Statistik", value=sub_stats_display, inline=True)
        
        # [IMPLEMENTASI BARU] Menambahkan informasi Agensi ke profil
        agency_id = player_data.get('agency_id')
        agency_display = "`Belum bergabung`"
        if agency_id and (agency_data := self.bot.get_agency_by_id(agency_id)):
            agency_display = f"{agency_data.get('emoji', '')} **{agency_data.get('name', 'N/A')}**"

        career_value = (
            f"🏢 **Agensi:** {agency_display}\n"
            f"👥 **Subscribers:** `{player_data.get('subscribers', 0):,}`\n"
            f"💎 **Prisma:** `{player_data.get('prisma', 0):,}`"
        )
        embed.add_field(name="🎙️ Karier", value=career_value, inline=False)
        
        embed.set_footer(text="Gunakan tombol di bawah untuk melihat detail lain.")
        return embed

    async def _create_equipment_embed(self, user: discord.Member, player_data: dict) -> discord.Embed:
        slot_map = {"helm": "🧢 Helm", "armor": "👕 Armor", "pants": "👖 Celana", "shoes": "👢 Sepatu", "artifact": "🔮 Artefak"}
        embed = discord.Embed(title=f"⚔️ Equipment & Artefak - {user.display_name}", description="Gunakan dropdown di bawah untuk memilih slot dan mengganti item.", color=BotColors.DEFAULT).set_thumbnail(url=user.display_avatar.url)
        
        equipment = await get_player_equipment(self.bot.db, user.id)
        upgrades = await get_player_upgrades(self.bot.db, user.id)

        for slot_id, display_name in slot_map.items():
            item_id = equipment.get(slot_id)
            if item_id and (item := self.bot.get_item_by_id(item_id)):
                
                # Ambil info level
                slot_upgrade = upgrades.get(slot_id, {})
                level = slot_upgrade.get('level', 0)
                level_str = f" **(+{level})**" if level > 0 else ""
                
                # Hitung stat display
                multiplier = 1 + (level * 0.1)
                stats_parts = []
                
                # Stat Utama
                for k, v in item.get('stat_boost', {}).items():
                    key_name = k.replace('_', ' ').replace('damage', 'DMG').upper()
                    
                    val_display = v
                    if k not in ['crit_rate', 'crit_damage']:
                        # Logika Stat Positif/Negatif untuk Display
                        if v > 0:
                            val_display = int(v * multiplier)
                        elif v < 0:
                            penalty_reduction = level // 3
                            val_display = min(0, v + penalty_reduction)
                    
                    # [PERBAIKAN UTAMA] Menggunakan format {val:+0}
                    # Ini akan otomatis menampilkan + jika positif dan - jika negatif
                    # dan menghilangkan masalah "+-5"
                    if isinstance(val_display, float):
                        formatted_val = f"{val_display:+.0%}" # Persentase
                    else:
                        formatted_val = f"{val_display:+}" # Angka biasa (+5, -3)

                    stats_parts.append(f"{key_name} `{formatted_val}`")

                # Stat Bonus (Extra)
                extra_stats = slot_upgrade.get('bonus_stats', {})
                if extra_stats:
                    stats_parts.append("\n*Sub-stat:*")
                    for k, v in extra_stats.items():
                        stats_parts.append(f"{k.upper()} `+{v}`") # Substat selalu positif

                stats_str = ' | '.join(stats_parts)
                embed.add_field(name=f"**{display_name}:** {item['name']}{level_str} `[{item.get('rarity', 'N/A')}]`", value=stats_str, inline=False)
            else:
                embed.add_field(name=f"**{display_name}:** Kosong", value="*Tidak ada item terpasang.*", inline=False)
        embed.set_footer(text="Item yang dilepas akan kembali ke inventory.")
        return embed

    async def _create_title_detail_display(self, user: discord.Member, player_data: dict) -> tuple[discord.Embed, discord.File | None]:
        """
        [DIPERBAIKI] Fungsi yang menampilkan detail Title yang sedang dipakai.
        Gambar title sekarang akan selalu ditampilkan sebagai thumbnail di pojok kanan atas.
        """
        equipped_title_id = player_data.get('equipped_title_id')
        title_data = self.bot.get_title_by_id(equipped_title_id)

        if not title_data:
            embed = discord.Embed(title="Error", description="Title yang dipakai tidak ditemukan.", color=BotColors.ERROR)
            return embed, None

        rarity = title_data.get("rarity", "Common").capitalize()
        rarity_colors = {
            "Common": BotColors.COMMON, "Rare": BotColors.RARE,
            "Epic": BotColors.EPIC, "Legendary": BotColors.LEGENDARY
        }
        embed_color = rarity_colors.get(rarity, BotColors.DEFAULT)
        
        embed = discord.Embed(
            title=f"👑 Detail Title: {title_data['name']}",
            description=f"_{title_data.get('description', '...')}_",
            color=embed_color
        )

        file = None
        # [LOGIKA BARU] Cek apakah title punya gambar dan apakah file-nya ada
        if image_filename := title_data.get("image_file"):
            image_path = f"assets/images/{image_filename}"
            if os.path.exists(image_path):
                file = discord.File(image_path, filename=image_filename)
                # [PERUBAHAN KUNCI] Gunakan set_thumbnail alih-alih set_image
                embed.set_thumbnail(url=f"attachment://{image_filename}")

        # Jika tidak ada file yang berhasil dibuat (baik karena tidak ada di JSON atau file tidak ditemukan)
        # maka gunakan avatar pengguna sebagai fallback.
        if file is None:
            embed.set_thumbnail(url=user.display_avatar.url)

        # Bagian selanjutnya dari fungsi ini tetap sama
        if title_data.get('stat_boost'):
            stats_text = self._format_stats_for_title(title_data['stat_boost'])
            embed.add_field(name="Bonus Stat Pasif", value=stats_text, inline=False)
            
        for skill in title_data.get('skills', []):
            skill_type = "Aktif" if skill.get('type') == 'active' else "Pasif"
            cooldown_text = f" (Cooldown: {skill.get('cooldown', 'N/A')} giliran)" if skill.get('type') == 'active' else ""
            
            embed.add_field(
                name=f"**{skill.get('name', '???')}** `[{skill_type}]`",
                value=f"*{skill.get('description', '...')}*{cooldown_text}\n"
                      f"**Efek:** {skill.get('damage_or_effect', 'N/A')}",
                inline=False
            )
        
        embed.set_footer(text="Gunakan menu dropdown di bawah untuk mengganti Title.")
        return embed, file

    # --- COMMAND UTAMA ---

    @commands.command(name="profil", aliases=["profile", "p"])
    async def view_profile(self, ctx: commands.Context, member: discord.Member = None):
        """Membuka panel profile player."""
        target_user = member or ctx.author
        player_data = await get_player_data(self.bot.db, target_user.id)

        if not player_data or not player_data.get('equipped_title_id'):
            await ctx.send(f"{target_user.display_name} belum memulai debutnya. Gunakan `{self.bot.command_prefix}debut` terlebih dahulu.")
            return

        view = ProfileView(author=ctx.author, target=target_user, cog=self)
        
        await view._build_components()
        initial_embed = await view._build_and_get_embed()
        
        message = await ctx.send(embed=initial_embed, view=view)
        view.message = message

async def setup(bot: commands.Bot):
    await bot.add_cog(ProfileCog(bot))