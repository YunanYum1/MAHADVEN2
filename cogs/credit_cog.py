import discord
from discord.ext import commands
import json
import math

# Impor BotColors dari file _utils.py untuk konsistensi
from ._utils import BotColors

# ===================================================================================
# KELAS KOMPONEN: DROPDOWN NAVIGASI (PAGINATION JUMP)
# ===================================================================================

class JumpSelect(discord.ui.Select):
    """Dropdown untuk melompat ke halaman tertentu."""
    def __init__(self, parent_view, total_pages, current_page, data_list, items_per_page, data_type):
        self.parent_view = parent_view
        
        options = []
        # Batasi max 25 halaman untuk dropdown (Limit Discord)
        max_opts = 25
        start_page = 0
        end_page = min(total_pages, 25)

        # Logika "Sliding Window" jika halaman > 25
        if current_page > 20:
            start_page = current_page - 10
            end_page = min(total_pages, start_page + 25)

        for i in range(start_page, end_page):
            start_idx = i * items_per_page
            
            # Ambil item pertama di halaman itu sebagai penanda label
            first_item = data_list[start_idx]
            name_label = first_item.get('name', f"Item {start_idx+1}")[:20]
            
            # Logika Label Dropdown
            if items_per_page == 1:
                # Mode Detail (1 item per halaman) -> Langsung nama item
                label = f"{i+1}. {name_label}"
                desc = f"Lihat detail {name_label}"
            else:
                # Mode List (Banyak item per halaman) -> Range halaman
                end_idx = min((i + 1) * items_per_page, len(data_list))
                label = f"Hal {i+1}: {name_label}..."
                desc = f"Index {start_idx+1} - {end_idx}"

            # Tentukan Emoji Dropdown
            emoji = "📄"
            if data_type == "fishes": emoji = "🐟"
            elif data_type == "fishing_items": emoji = "🎒"
            elif data_type == "monsters": emoji = "😈"
            elif data_type == "titles": emoji = "📜"

            options.append(discord.SelectOption(
                label=label,
                value=str(i),
                description=desc,
                emoji=emoji,
                default=(i == current_page)
            ))

        super().__init__(placeholder=f"🔍 Lompat ke Halaman... ({start_page+1}-{end_page})", min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        new_page = int(self.values[0])
        self.parent_view.current_page = new_page
        await self.parent_view.update_view(interaction)

# ===================================================================================
# KELAS VIEW: PAGINATOR DATA (MULTI-ITEM & LINKED DATA SUPPORT)
# ===================================================================================

class DataPaginator(discord.ui.View):
    def __init__(self, ctx, data_list, data_type, main_view):
        super().__init__(timeout=300)
        self.ctx = ctx
        self.data_list = data_list
        self.data_type = data_type
        self.main_view = main_view 
        self.current_page = 0
        
        # --- KONFIGURASI ITEM PER HALAMAN ---
        # Ikan & Item: Info singkat -> Muat banyak (6)
        # Monster, Title, Agensi: Info Padat -> Muat 1 per halaman (Detail View)
        
        if self.data_type in ["fishes", "fishing_items"]:
            self.items_per_page = 6
        else:
            # Monsters masuk ke sini sekarang (1 per page)
            self.items_per_page = 1
            
        self.total_pages = math.ceil(len(data_list) / self.items_per_page)
        self.rebuild_components()

    def rebuild_components(self):
        self.clear_items()

        # 1. Dropdown Navigasi
        self.add_item(JumpSelect(self, self.total_pages, self.current_page, self.data_list, self.items_per_page, self.data_type))

        # 2. Tombol Navigasi
        prev_btn = discord.ui.Button(label="◀️", style=discord.ButtonStyle.primary, disabled=(self.current_page == 0), row=1)
        prev_btn.callback = self.prev_callback
        self.add_item(prev_btn)

        count_btn = discord.ui.Button(label=f"Hal {self.current_page + 1} / {self.total_pages}", style=discord.ButtonStyle.secondary, disabled=True, row=1)
        self.add_item(count_btn)

        next_btn = discord.ui.Button(label="▶️", style=discord.ButtonStyle.primary, disabled=(self.current_page >= self.total_pages - 1), row=1)
        next_btn.callback = self.next_callback
        self.add_item(next_btn)

        # 3. Tombol Kembali
        back_btn = discord.ui.Button(label="Kembali ke Menu Utama", style=discord.ButtonStyle.danger, row=2)
        back_btn.callback = self.back_callback
        self.add_item(back_btn)

    async def update_view(self, interaction: discord.Interaction):
        self.rebuild_components()
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

    async def prev_callback(self, interaction: discord.Interaction):
        self.current_page -= 1
        await self.update_view(interaction)

    async def next_callback(self, interaction: discord.Interaction):
        self.current_page += 1
        await self.update_view(interaction)

    async def back_callback(self, interaction: discord.Interaction):
        # Saat kembali, tampilkan lagi tombol Link (Manual & Trakteer)
        # Kita perlu me-reset view ke CreditView awal
        await interaction.response.edit_message(embed=self.main_view.initial_embed, view=self.main_view)

    def _format_stats(self, stats):
        if not stats: return "Tidak ada."
        return " | ".join([f"{k.upper().replace('_', ' ')}: `{v}`" for k, v in stats.items()])

    def get_embed(self):
        start_idx = self.current_page * self.items_per_page
        end_idx = start_idx + self.items_per_page
        
        # Ambil potongan data (Batch)
        batch_items = self.data_list[start_idx:end_idx]
        
        embed = discord.Embed(color=BotColors.DEFAULT)
        # Footer universal
        embed.set_footer(text=f"Halaman {self.current_page + 1} dari {self.total_pages} | Index ID: {batch_items[0].get('id', 'N/A')}")

        # ========================================================
        # MODE 1: FISHING / ITEMS LIST (COMPACT - 6 ITEM)
        # ========================================================
        if self.data_type == "fishes":
            embed.title = "🐟 Ensiklopedia Ikan"
            embed.description = "Daftar ikan yang bisa ditangkap."
            
            for item in batch_items:
                rarity = item.get('rarity', 'Common')
                emoji = item.get('emoji', '🐟')
                r_icon = "⚪" if rarity == "Common" else "🔵" if rarity == "Rare" else "🟣" if rarity == "Epic" else "🌟" if rarity == "Legendary" else "🔥"
                
                details = f"🏷️ **{rarity}** {r_icon} | 💰 `{item['price']} 💎` | 🕹️ `{item['difficulty']} tombol`"
                embed.add_field(name=f"{emoji} {item['name']}", value=details, inline=True)

        elif self.data_type == "fishing_items":
            embed.title = "🎒 Peralatan Pancing"
            embed.description = "Joran & Charm."
            
            for item in batch_items:
                tipe = "🎣 Rod" if item['type'] == 'rod' else "📿 Charm"
                stats = []
                if item.get('luck') > 0: stats.append(f"Luck +{item['luck']}")
                if item.get('time_bonus') > 0: stats.append(f"Time +{item['time_bonus']}s")
                stats_str = " | ".join(stats) if stats else "No Effect"

                details = f"🏷️ {tipe} | 💎 `{item['price']}`\n✨ {stats_str}"
                embed.add_field(name=f"{item['name']}", value=details, inline=True)

        # ========================================================
        # MODE 2: DETAIL VIEW (MONSTER, TITLE, AGENCY - 1 ITEM)
        # ========================================================
        else:
            # Karena items_per_page = 1, batch_items pasti cuma isi 1 elemen
            item = batch_items[0]

            if self.data_type == "monsters":
                embed.title = f"😈 {item['name']}"
                embed.description = f"_{item['description']}_"
                
                # Format Stat
                stats_str = (
                    f"❤️ **HP:** `{item['hp']}` | ⚔️ **ATK:** `{item['atk']}`\n"
                    f"🛡️ **DEF:** `{item['def']}` | 💨 **SPD:** `{item['spd']}`"
                )
                embed.add_field(name="📊 Status Dasar", value=stats_str, inline=False)
                
                # Format Rewards
                drops_str = f"💎 Money: `{item['money_reward'][0]}-{item['money_reward'][1]}`\n✨ EXP: `{item['exp_reward'][0]}-{item['exp_reward'][1]}`"
                embed.add_field(name="🎁 Drop Reward", value=drops_str, inline=True)

                # Ambil Skill dari Titles
                # Akses database Titles dari main_view -> cog -> bot -> titles
                all_titles = getattr(self.main_view.cog.bot, 'titles', [])
                related_title = next((t for t in all_titles if t['id'] == item.get('monster_title_id')), None)

                skill_desc_list = []
                if related_title and 'skills' in related_title:
                    for s in related_title['skills']:
                        icon = "🔴" if s['type'] == 'active' else "🟢"
                        skill_desc_list.append(f"{icon} **{s['name']}**\n> {s['description']}")
                
                skill_text = "\n\n".join(skill_desc_list) if skill_desc_list else "*Tidak ada skill khusus.*"
                embed.add_field(name="⚡ Kemampuan (Skill)", value=skill_text, inline=False)

            
            elif self.data_type == "titles":
                rarity = item.get('rarity', 'Common')
                r_icon = "🌟" if rarity == "Legendary" else "🟣" if rarity == "Epic" else "🔵" if rarity == "Rare" else "⚪"
                
                embed.title = f"{r_icon} {item['name']} [{rarity}]"
                embed.description = f"_{item.get('description', '-')}_"
                
                if stats := item.get('stat_boost'):
                    embed.add_field(name="📈 Bonus Status", value=self._format_stats(stats), inline=False)
                
                skills = item.get('skills', [])
                if skills:
                    skill_desc = ""
                    for s in skills:
                        tipe = "🔴 Aktif" if s['type'] == 'active' else "🟢 Pasif"
                        skill_desc += f"**{s['name']}** ({tipe})\n> {s['description']}\n"
                    embed.add_field(name="⚔️ Skill", value=skill_desc, inline=False)

            elif self.data_type == "agencies":
                embed.title = f"{item.get('emoji', '🏢')} {item['name']}"
                embed.description = item.get('description', '-')
                
                if benefits := item.get('benefits'):
                    embed.add_field(name="✅ Keuntungan", value="\n".join([f"• {b}" for b in benefits]), inline=False)
                
                if drawbacks := item.get('drawbacks'):
                    embed.add_field(name="❌ Kekurangan", value="\n".join([f"• {d}" for d in drawbacks]), inline=False)

        return embed

# ===================================================================================
# KELAS VIEW: MENU UTAMA (DENGAN TOMBOL DONASI)
# ===================================================================================

class CreditView(discord.ui.View):
    def __init__(self, ctx, cog, initial_embed):
        super().__init__(timeout=180)
        self.ctx = ctx
        self.cog = cog
        self.initial_embed = initial_embed
        
        # URL Donasi & Manual
        self.url_trakteer = "https://trakteer.id/Popoooooooooo"
        self.url_manual = "https://docs.google.com/document/d/1BtvHCo6ORo1gZNVcFFOgkoQZ-tFKyraSGB6yqoWIBts/edit?usp=sharing"

        # Menambahkan Tombol Link
        self.add_item(discord.ui.Button(label="📚 Baca Panduan Lengkap", url=self.url_manual, style=discord.ButtonStyle.link, row=1))
        self.add_item(discord.ui.Button(label="🎁 Traktir Dev (Support)", url=self.url_trakteer, style=discord.ButtonStyle.link, row=1))

    @discord.ui.select(
        placeholder="📚 Pilih Data Wiki untuk Dilihat...",
        options=[
            discord.SelectOption(label="Ensiklopedia Monster", value="monsters", description="Statistik, Drops, & Skill Musuh", emoji="😈"),
            discord.SelectOption(label="Daftar Title (Job)", value="titles", description="Lihat status & skill Class Pemain", emoji="📜"),
            discord.SelectOption(label="Daftar Ikan", value="fishes", description="Data ikan, harga, & rarity", emoji="🐟"),
            discord.SelectOption(label="Peralatan Pancing", value="fishing_items", description="Joran & Charm", emoji="🎒"),
            discord.SelectOption(label="Daftar Agensi", value="agencies", description="Bonus & efek agensi", emoji="🏢"),
        ],
        row=0
    )
    async def select_callback(self, interaction: discord.Interaction, select: discord.ui.Select):
        choice = select.values[0]
        data_source = []

        # Load data dari bot attributes (pastikan data sudah di-load di main.py)
        if choice == "titles": data_source = getattr(self.cog.bot, 'titles', [])
        elif choice == "monsters": data_source = getattr(self.cog.bot, 'monsters', [])
        elif choice == "fishes": data_source = getattr(self.cog.bot, 'fishes', [])
        elif choice == "fishing_items": 
            fishing_items_dict = getattr(self.cog.bot, 'fishing_items', {})
            data_source = list(fishing_items_dict.values())
        elif choice == "agencies": data_source = getattr(self.cog.bot, 'agencies', [])

        # Fallback jika data kosong (untuk testing lokal tanpa main.py penuh)
        if not data_source and choice == "fishing_items":
             try:
                 with open('data/fishing_items.json', 'r') as f: data_source = json.load(f)
             except: pass

        if not data_source:
            return await interaction.response.send_message(f"❌ Data {choice} tidak ditemukan atau kosong.", ephemeral=True)

        paginator = DataPaginator(self.ctx, data_source, choice, self)
        await interaction.response.edit_message(embed=paginator.get_embed(), view=paginator)

# ===================================================================================
# COG UTAMA
# ===================================================================================

class CreditCog(commands.Cog, name="Informasi"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="credit", aliases=["credits", "info", "wiki"])
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def credit(self, ctx: commands.Context):
        """Menampilkan informasi bot, wiki, dan dukungan donasi."""

        developer_name = "CoffeeCyno"
        trakteer_link = "https://trakteer.id/Popoooooooooo"

        embed = discord.Embed(
            title="🌟 MAHADVEN: Markas Informasi 🌟",
            description=(
                "Yo! Selamat datang di **MAHADVEN**, tempat di mana mimpi jadi Virtual Legend dimulai! 🚀\n\n"
                "**Cara Main Singkat:**\n"
                "1️⃣ **Debut** dulu biar punya status.\n"
                "2️⃣ **Grinding** lawan monster atau **Mancing** santai.\n"
                "3️⃣ Kumpulin duit buat **Gacha** Title OP.\n"
                "4️⃣ Pamer ke temen satu server!\n\n"
                "Bingung soal drop item atau skill musuh? Pilih menu **Wiki** di bawah! 👇"
            ),
            color=BotColors.DEFAULT
        )
        embed.set_thumbnail(url=self.bot.user.display_avatar.url)

        # Rate Gacha
        embed.add_field(name="🎰 Rate Gacha Title", value="• Legendary: `5%`\n• Epic: `10%`\n• Rare: `25%`\n• Common: `65%`", inline=True)
        
        # Rate Fishing
        embed.add_field(
            name="🎣 Rate Memancing", 
            value=(
                "Dipengaruhi **Total Luck**:\n"
                "• **Legendary:** Unlock @ `150 Luck`\n"
                "• **Mitos:** Unlock @ `300 Luck`\n"
                "• **Godly:** Unlock @ `450 Luck`"
            ), 
            inline=True
        )

        # Rate Blacksmith
        embed.add_field(name="⚒️ Rate Tempa", value="• Lv 0-1: `100%`\n• Lv 5-6: `50%`\n• Lv 10+: `7%` ke bawah", inline=True)
        
        # Dukungan / Donasi (NEW)
        embed.add_field(
            name="💖 Dukung Pengembangan",
            value=(
                f"Suka dengan bot ini? Bantu dev beli kopi biar update terus!\n"
                f"👉 **[Klik di sini untuk Trakteer]({trakteer_link})**"
            ),
            inline=False
        )

        embed.add_field(name="💡 Shortcut", value=f"`{self.bot.command_prefix}debut` | `{self.bot.command_prefix}help`", inline=True)
        embed.add_field(name="👨‍💻 Developer", value=developer_name, inline=True)

        view = CreditView(ctx, self, embed)
        await ctx.send(embed=embed, view=view)

async def setup(bot: commands.Bot):
    await bot.add_cog(CreditCog(bot))