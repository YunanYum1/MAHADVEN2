# cogs/shop_cog.py

import discord
from discord.ext import commands
import json
import math
import os

# Impor fungsi dan kelas dari proyek Anda
from database import get_player_data, update_player_data
from ._utils import BotColors

# ===================================================================================
# --- KELAS-KELAS VIEW (UI INTERAKTIF) ---
# ===================================================================================

class ShopView(discord.ui.View):
    PAGE_SIZE = 4

    def __init__(self, author: discord.User, cog: commands.Cog):
        super().__init__(timeout=300)
        self.author = author
        self.cog = cog
        self.bot = cog.bot
        self.message: discord.Message = None
        
        self.current_page_index = 0
        self.selected_category = None
        self.all_items = cog.shop_items
        
        # Panggil `build_components` di awal untuk membuat semua komponen
        self.build_components()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("Ini bukan sesi toko milikmu!", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        if self.message:
            for item in self.children:
                item.disabled = True
            try:
                # Opsi: Edit pesan saat timeout untuk memberitahu pengguna
                await self.message.edit(content="Sesi toko telah berakhir.", view=self, embed=None)
            except discord.NotFound:
                pass

    async def update_view(self, interaction: discord.Interaction):
        if not interaction.response.is_done():
            await interaction.response.defer()

        self.build_components()
        embed = await self._create_embed()
        
        await interaction.edit_original_response(embed=embed, view=self)

    def build_components(self):
        self.clear_items()

        if self.selected_category is None:
            self.add_item(CategorySelect(self))
        else:
            filtered_items = self._get_filtered_items()
            total_pages = math.ceil(len(filtered_items) / self.PAGE_SIZE) if filtered_items else 1
            
            # Mengatur status disabled tombol langsung di sini
            self.back_button.disabled = False
            self.prev_button.disabled = self.current_page_index == 0
            self.next_button.disabled = self.current_page_index >= total_pages - 1
            
            # Menambahkan tombol yang sudah didefinisikan dengan decorator
            self.add_item(self.back_button)
            self.add_item(self.prev_button)
            self.add_item(discord.ui.Button(label=f"Hal {self.current_page_index + 1}/{total_pages}", style=discord.ButtonStyle.secondary, disabled=True, row=0))
            self.add_item(self.next_button)

            self.add_item(BuyItemSelect(self))

    async def _create_embed(self) -> discord.Embed:
        player_data = await get_player_data(self.bot.db, self.author.id)
        player_prisma = player_data.get('prisma', 0)

        if self.selected_category is None:
            embed = discord.Embed(
                title="🛍️ Selamat Datang di Toko MAHADVEN",
                description="Silakan pilih kategori equipment yang ingin kamu lihat dari menu di bawah.",
                color=BotColors.DEFAULT
            )
            embed.set_footer(text=f"Prismamu saat ini: {player_prisma:,} 💎")
            return embed

        filtered_items = self._get_filtered_items()
        start = self.current_page_index * self.PAGE_SIZE
        end = start + self.PAGE_SIZE
        items_on_page = filtered_items[start:end]
        
        embed = discord.Embed(
            title=f"🛍️ Toko - Kategori: {self.selected_category.title()}",
            description="Perkuat dirimu dengan membeli equipment terbaik!",
            color=BotColors.RARE
        )

        if not items_on_page:
            embed.description = "Sayangnya, tidak ada item di kategori ini."
        else:
            for item in items_on_page:
                stats = item.get('stat_boost', {})
                stats_parts = []
                if hp := stats.get('hp', 0): stats_parts.append(f"❤️HP`{hp:+}`")
                if atk := stats.get('atk', 0): stats_parts.append(f"⚔️ATK`{atk:+}`")
                if d := stats.get('def', 0): stats_parts.append(f"🛡️DEF`{d:+}`")
                if spd := stats.get('spd', 0): stats_parts.append(f"💨SPD`{spd:+}`")
                if cr := stats.get('crit_rate', 0): stats_parts.append(f"🎯Crit`{cr*100:+.0f}%`")
                if cd := stats.get('crit_damage', 0): stats_parts.append(f"💥CDMG`{cd*100:+.0f}%`")
                
                stats_text = ' | '.join(stats_parts) if stats_parts else "Tidak ada bonus stat."
                
                embed.add_field(
                    name=f"{item['name']} `[{item.get('rarity', 'Common')}]`",
                    value=f"**Bonus:** {stats_text}\n**Harga:** `{item['price']:,}` 💎",
                    inline=False
                )
        
        total_pages = math.ceil(len(filtered_items) / self.PAGE_SIZE) if filtered_items else 1
        embed.set_footer(text=f"Halaman {self.current_page_index + 1}/{total_pages} | Prismamu: {player_prisma:,} 💎")
        return embed

    def _get_filtered_items(self) -> list:
        if not self.selected_category:
            return []
        return [item for item in self.all_items if item.get('type', '').lower() == self.selected_category]

    @discord.ui.button(label="Kembali ke Kategori", emoji="⬅️", style=discord.ButtonStyle.danger, row=0)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.selected_category = None
        self.current_page_index = 0
        await self.update_view(interaction)
    
    @discord.ui.button(label="◀️", row=0)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page_index > 0:
            self.current_page_index -= 1
        await self.update_view(interaction)

    @discord.ui.button(label="▶️", row=0)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        filtered_items = self._get_filtered_items()
        total_pages = math.ceil(len(filtered_items) / self.PAGE_SIZE)
        if self.current_page_index < total_pages - 1:
            self.current_page_index += 1
        await self.update_view(interaction)


class CategorySelect(discord.ui.Select):
    def __init__(self, parent_view: ShopView):
        self.parent_view = parent_view
        options = [
            discord.SelectOption(label="Helm", value="helm", emoji="👑"),
            discord.SelectOption(label="Armor", value="armor", emoji="👕"),
            discord.SelectOption(label="Pants", value="pants", emoji="👖"),
            discord.SelectOption(label="Boots", value="shoes", emoji="👢"),
        ]
        super().__init__(placeholder="Pilih kategori equipment...", options=options, row=0)
        
    async def callback(self, interaction: discord.Interaction):
        self.parent_view.selected_category = self.values[0]
        self.parent_view.current_page_index = 0
        await self.parent_view.update_view(interaction)


class BuyItemSelect(discord.ui.Select):
    def __init__(self, parent_view: ShopView):
        self.parent_view = parent_view
        super().__init__(placeholder="Pilih item untuk dibeli...", row=1)
        self.populate_options()

    def populate_options(self):
        start = self.parent_view.current_page_index * self.parent_view.PAGE_SIZE
        end = start + self.parent_view.PAGE_SIZE
        items_on_page = self.parent_view._get_filtered_items()[start:end]

        options = []
        for item in items_on_page:
            options.append(discord.SelectOption(
                label=item['name'], 
                value=str(item['id']), 
                description=f"{item['price']:,} 💎"
            ))
        
        if not options:
            options.append(discord.SelectOption(label="Tidak ada item di halaman ini.", value="disabled"))
            self.disabled = True
        else:
            self.disabled = False
        
        self.options = options
        
    async def callback(self, interaction: discord.Interaction):
        selected_value = self.values[0]
        if selected_value == 'disabled':
            return await interaction.response.defer()
        
        selected_item_id = int(selected_value)
        item_to_buy = next((item for item in self.parent_view.all_items if item['id'] == selected_item_id), None)
        
        if not item_to_buy:
            return await interaction.response.send_message("Item tidak ditemukan!", ephemeral=True)

        player_data = await get_player_data(self.parent_view.bot.db, interaction.user.id)
        
        if player_data.get('prisma', 0) < item_to_buy['price']:
            return await interaction.response.send_message(f"Prismamu tidak cukup untuk membeli **{item_to_buy['name']}**!", ephemeral=True, delete_after=10)

        new_prisma = player_data['prisma'] - item_to_buy['price']
        inventory_list = json.loads(player_data.get('inventory', '[]'))
        inventory_list.append(item_to_buy['id'])
        new_inventory_json = json.dumps(inventory_list)

        await update_player_data(self.parent_view.bot.db, interaction.user.id, 
                                 prisma=new_prisma, inventory=new_inventory_json)
        
        await interaction.response.send_message(f"✅ Kamu berhasil membeli **{item_to_buy['name']}**!", ephemeral=True, delete_after=10)
        
        await self.parent_view.update_view(interaction)

# ===================================================================================
# --- COG UTAMA ---
# ===================================================================================

class ShopCog(commands.Cog, name="Toko"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.shop_items = []
        
        file_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'items.json')

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                all_items = json.load(f)
                
                if not isinstance(all_items, list):
                    print("KESALAHAN: data/items.json tidak berisi sebuah list/array.")
                    return

                valid_types = {"helm", "armor", "pants", "shoes"}
                
                self.shop_items = [
                    item for item in all_items 
                    if 'price' in item and item['price'] > 0 and item.get('type', '').lower() in valid_types
                ]

            self.shop_items.sort(key=lambda x: (x.get('type').lower(), x['price']))
            
            if self.shop_items:
                print(f"Berhasil memuat {len(self.shop_items)} item untuk toko.")
            else:
                print("PERINGATAN: Tidak ada item yang dimuat ke toko. Periksa 'data/items.json'.")

        except FileNotFoundError:
            print(f"KESALAHAN: File toko tidak ditemukan di path: {file_path}")
        except json.JSONDecodeError as e:
            print(f"KESALAHAN: Gagal membaca 'data/items.json'. Periksa format JSON. Error: {e}")

    @commands.command(name="toko", aliases=["shop"])
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def shop(self, ctx: commands.Context):
        """Membuka panel shop untuk membeli equipment."""
        player_data = await get_player_data(self.bot.db, ctx.author.id)
        if player_data.get("equipped_title_id") is None:
            prefix = self.bot.command_prefix
            return await ctx.send(f"Kamu harus debut dulu dengan `{prefix}debut` sebelum bisa mengakses toko.", ephemeral=True, delete_after=10)
        
        if not self.shop_items:
            return await ctx.send("Maaf, toko sedang kosong atau gagal memuat item. Hubungi admin.", ephemeral=True, delete_after=10)
            
        view = ShopView(ctx.author, self)
        initial_embed = await view._create_embed()
        
        message = await ctx.send(embed=initial_embed, view=view)
        view.message = message

    @shop.error
    async def shop_error(self, ctx, error):
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.send(f"Toko masih disiapkan! Silakan kembali lagi dalam **{error.retry_after:.1f} detik**.", delete_after=5, ephemeral=True)

async def setup(bot):
    await bot.add_cog(ShopCog(bot))