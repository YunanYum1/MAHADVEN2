# cogs/inventory_cog.py

import discord
from discord.ext import commands
import math
from collections import Counter
import json
import traceback 

# Impor fungsi-fungsi yang dibutuhkan dari database
from database import (
    get_player_inventory,
    get_player_data,
    update_player_data,
    get_player_equipment
)
from ._utils import BotColors

# ===================================================================================
# --- KELAS VIEW INVENTARIS ---
# ===================================================================================

class InventoryView(discord.ui.View):
    # [BARU] Konfigurasi harga dasar fix jika item tidak memiliki harga di database/json
    RARITY_BASE_PRICES = {
        "Common": 100,
        "Rare": 200,
        "Epic": 400,
        "Legendary": 700
    }

    def __init__(self, author: discord.User, cog: commands.Cog):
        super().__init__(timeout=300.0)
        self.author = author
        self.cog = cog
        self.bot = cog.bot
        self.message: discord.Message = None

        # State Management
        self.current_state = "viewing" # viewing | selling | confirming
        self.page_index = 0
        self.selected_item_id_to_sell = None
        self.sell_price = 0
        
        # Konstanta
        self.ITEMS_PER_PAGE = 5
        self.SELL_RATIO = 0.9 

    # [BARU] Fungsi Helper untuk menghitung harga jual
    def calculate_sell_price(self, item_data: dict) -> int:
        """
        Menghitung harga jual. 
        Prioritas 1: Harga yang tertera di item (price).
        Prioritas 2: Harga fix berdasarkan Rarity (jika price 0 atau tidak ada).
        """
        base_price = item_data.get('price', 0)
        
        # Jika tidak ada harga (misal Artefak), gunakan harga fix berdasarkan rarity
        if base_price <= 0:
            rarity = item_data.get('rarity', 'Common')
            base_price = self.RARITY_BASE_PRICES.get(rarity, 100) # Default 50 jika rarity aneh

        # Terapkan rasio jual (misal 90% dari harga asli)
        return int(base_price * self.SELL_RATIO)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("Ini bukan sesi inventaris milikmu!", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        if self.message:
            try:
                for item in self.children:
                    item.disabled = True
                await self.message.edit(view=self)
            except discord.NotFound:
                pass

    async def start(self, ctx: commands.Context):
        # [AUTO-CLEAN] Bersihkan item sampah
        await self._clean_inventory_data(ctx.author.id)
        
        embed = await self._build_embed()
        await self._build_components()
        self.message = await ctx.send(embed=embed, view=self)

    async def _clean_inventory_data(self, user_id: int):
        inventory_list = await get_player_inventory(self.bot.db, user_id)
        if not inventory_list: return

        cleaned_list = [
            item_id for item_id in inventory_list 
            if self.bot.get_item_by_id(item_id) is not None and item_id != 0
        ]

        if len(cleaned_list) != len(inventory_list):
            await update_player_data(self.bot.db, user_id, inventory=json.dumps(cleaned_list))

    async def _update_view(self, interaction: discord.Interaction = None):
        try:
            embed = await self._build_embed()
            await self._build_components()
            
            if interaction:
                if interaction.response.is_done():
                    await interaction.edit_original_response(embed=embed, view=self)
                else:
                    await interaction.response.edit_message(embed=embed, view=self)
            else:
                await self.message.edit(embed=embed, view=self)
        except Exception as e:
            print(f"Error di _update_view: {e}")
            traceback.print_exc()

    async def _build_embed(self) -> discord.Embed:
        if self.current_state == "viewing":
            return await self._build_viewing_embed()
        elif self.current_state == "selling":
            return self._build_selling_embed()
        elif self.current_state == "confirming":
            return self._build_confirming_embed()

    async def _build_components(self):
        self.clear_items()
        
        if self.current_state == "viewing":
            inventory_list = await get_player_inventory(self.bot.db, self.author.id) or []
            total_items = len(Counter(inventory_list))
            total_pages = math.ceil(total_items / self.ITEMS_PER_PAGE) or 1
            
            if self.page_index >= total_pages:
                self.page_index = max(0, total_pages - 1)
            
            self.add_item(PageButton(direction=-1, disabled=(self.page_index == 0)))
            self.add_item(discord.ui.Button(label=f"Hal {self.page_index + 1}/{total_pages}", style=discord.ButtonStyle.secondary, disabled=True))
            self.add_item(PageButton(direction=1, disabled=(self.page_index >= total_pages - 1)))
            self.add_item(ToggleSellModeButton(row=1))

        elif self.current_state == "selling":
            self.add_item(BackButton(row=0))
            sell_select = SellItemSelect(parent_view=self, row=1)
            await sell_select._populate_options()
            self.add_item(sell_select)
        
        elif self.current_state == "confirming":
            self.add_item(ConfirmSellButton(self.sell_price))
            self.add_item(BackButton(label="Batal"))

    async def _build_viewing_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title=f"🎒 Inventaris - {self.author.display_name}",
            description="Berikut adalah item yang kamu miliki.",
            color=BotColors.DEFAULT
        ).set_thumbnail(url=self.author.display_avatar.url)

        equipped_items = await get_player_equipment(self.bot.db, self.author.id)
        if equipped_items:
            equipped_text = []
            slots_order = ['helm', 'armor', 'pants', 'shoes', 'artifact']
            
            for slot in slots_order:
                item_id = equipped_items.get(slot)
                if item_id:
                    item_data = self.bot.get_item_by_id(item_id)
                    if item_data:
                        equipped_text.append(f"**{slot.capitalize()}:** {item_data['name']}")
            
            if equipped_text:
                embed.add_field(
                    name="🛡️ Sedang Dipakai (Tidak bisa dijual)",
                    value="\n".join(equipped_text),
                    inline=False
                )
                embed.add_field(name="📦 Isi Tas", value="-------------------", inline=False)

        inventory_list = await get_player_inventory(self.bot.db, self.author.id) or []
        
        if not inventory_list:
            embed.add_field(name="Isi Tas", value="*Tas kosong...*", inline=False)
            return embed

        item_counts = Counter(inventory_list)
        
        def get_item_name(item_id):
            item = self.bot.get_item_by_id(item_id)
            return item.get('name', 'Unknown') if item else 'Unknown'

        unique_items = sorted(item_counts.items(), key=lambda x: get_item_name(x[0]))
        
        start_index = self.page_index * self.ITEMS_PER_PAGE
        end_index = start_index + self.ITEMS_PER_PAGE
        items_on_page = unique_items[start_index:end_index]

        if not items_on_page:
             embed.description += "\n\n*Tidak ada item di halaman ini.*"
        
        for item_id, count in items_on_page:
            item_data = self.bot.get_item_by_id(item_id)
            if item_data:
                rarity = item_data.get('rarity', 'Common')
                item_type = item_data.get('type', 'Item').capitalize()
                
                # [MODIFIKASI] Tampilkan estimasi harga jual di view utama juga (Opsional, tapi membantu)
                sell_est = self.calculate_sell_price(item_data)
                
                stats = []
                for k, v in item_data.get('stat_boost', {}).items():
                    stats.append(f"{k.upper()}: {v}")
                stats_str = f" | {', '.join(stats)}" if stats else ""

                embed.add_field(
                    name=f"{item_data.get('name', '???')} (x{count})",
                    value=f"`{rarity} | {item_type}`\nHarga Jual: {sell_est}💎\n{stats_str}",
                    inline=False
                )
                
        return embed

    def _build_selling_embed(self) -> discord.Embed:
        return discord.Embed(
            title="💰 Mode Menjual",
            description="**Pilih item dari dropdown di bawah ini untuk dijual.**\nItem tanpa harga toko akan dihargai berdasarkan kelangkaannya.\nTekan 'Kembali' untuk melihat tas.",
            color=BotColors.LEGENDARY
        )

    def _build_confirming_embed(self) -> discord.Embed:
        item_data = self.bot.get_item_by_id(self.selected_item_id_to_sell)
        item_name = item_data.get('name', 'Unknown Item') if item_data else 'Unknown Item'
        
        return discord.Embed(
            title="❓ Konfirmasi Penjualan",
            description=f"Kamu akan menjual **1x {item_name}**.\n\n"
                        f"Harga Jual: **{self.sell_price}** Prisma.\n\n"
                        "Apakah kamu yakin?",
            color=BotColors.WARNING
        )

# --- Komponen UI ---

class PageButton(discord.ui.Button):
    def __init__(self, direction: int, **kwargs):
        super().__init__(label="◀️" if direction == -1 else "▶️", style=discord.ButtonStyle.secondary, **kwargs)
        self.direction = direction

    async def callback(self, interaction: discord.Interaction):
        view: InventoryView = self.view
        await interaction.response.defer()
        view.page_index += self.direction
        await view._update_view(interaction)

class ToggleSellModeButton(discord.ui.Button):
    def __init__(self, **kwargs):
        super().__init__(label="Jual Item", emoji="💰", style=discord.ButtonStyle.danger, **kwargs)

    async def callback(self, interaction: discord.Interaction):
        view: InventoryView = self.view
        await interaction.response.defer()
        view.current_state = "selling"
        await view._update_view(interaction)

class BackButton(discord.ui.Button):
    def __init__(self, label="Kembali", **kwargs):
        super().__init__(label=label, emoji="⬅️", style=discord.ButtonStyle.secondary, **kwargs)

    async def callback(self, interaction: discord.Interaction):
        view: InventoryView = self.view
        await interaction.response.defer()
        view.current_state = "viewing"
        view.selected_item_id_to_sell = None
        view.sell_price = 0
        await view._update_view(interaction)

class SellItemSelect(discord.ui.Select):
    def __init__(self, parent_view: InventoryView, **kwargs):
        super().__init__(placeholder="Pilih item untuk dijual...", **kwargs)
        self.parent_view = parent_view

    async def _populate_options(self):
        inventory_list = await get_player_inventory(self.parent_view.bot.db, self.parent_view.author.id) or []
        item_counts = Counter(inventory_list)
        options = []
        
        for item_id, count in item_counts.items():
            item_data = self.parent_view.bot.get_item_by_id(item_id)
            
            if item_data:
                # [MODIFIKASI] Menggunakan fungsi helper untuk menghitung harga
                # Ini akan otomatis menghandle item tanpa 'price' menggunakan harga rarity
                sell_price = self.parent_view.calculate_sell_price(item_data)
                
                if sell_price > 0:
                    options.append(discord.SelectOption(
                        label=f"{item_data.get('name', '???')} (x{count})",
                        value=str(item_id),
                        description=f"Jual: {sell_price} Prisma/pcs",
                        emoji="💎"
                    ))
        
        if not options:
            self.disabled = True
            self.placeholder = "Tidak ada item yang bisa dijual."
            self.options = [discord.SelectOption(label="Kosong", value="dummy", description="Tidak ada item", default=False)]
        else:
            # Batasi opsi maks 25 agar tidak error Discord limit
            self.options = sorted(options, key=lambda o: o.label)[:25]
            self.disabled = False
    
    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "dummy":
            await interaction.response.defer()
            return

        view: InventoryView = self.view
        await interaction.response.defer()
        
        try:
            selected_id = int(self.values[0])
            item_data = self.view.bot.get_item_by_id(selected_id)
            
            if not item_data:
                await interaction.followup.send("Data item tidak valid.", ephemeral=True)
                return

            view.selected_item_id_to_sell = selected_id
            # [MODIFIKASI] Gunakan helper calculation yang sama
            view.sell_price = view.calculate_sell_price(item_data)
            view.current_state = "confirming"
            
            await view._update_view(interaction)
        except (ValueError, IndexError):
            await interaction.followup.send("Terjadi kesalahan saat memilih item.", ephemeral=True)

class ConfirmSellButton(discord.ui.Button):
    def __init__(self, sell_price: int):
        super().__init__(label=f"Jual ({sell_price} Prisma)", style=discord.ButtonStyle.success, emoji="✅")
        self.sell_price = sell_price

    async def callback(self, interaction: discord.Interaction):
        view: InventoryView = self.view
        await interaction.response.defer()

        inventory_list = await get_player_inventory(view.bot.db, view.author.id)
        player_data = await get_player_data(view.bot.db, view.author.id)
        current_prisma = player_data.get('prisma', 0)

        if view.selected_item_id_to_sell in inventory_list:
            inventory_list.remove(view.selected_item_id_to_sell)
            
            await update_player_data(
                view.bot.db, 
                view.author.id, 
                inventory=json.dumps(inventory_list), 
                prisma=current_prisma + self.sell_price
            )
            
            item_data = view.bot.get_item_by_id(view.selected_item_id_to_sell)
            item_name = item_data.get('name', 'Unknown Item') if item_data else 'Unknown Item'
            
            await interaction.followup.send(
                f"✅ Berhasil menjual **1x {item_name}** seharga **{self.sell_price} Prisma**!",
                ephemeral=True
            )
        else:
            await interaction.followup.send(
                "❌ Item tidak ditemukan (mungkin sudah terjual).",
                ephemeral=True
            )

        view.current_state = "viewing"
        view.selected_item_id_to_sell = None
        view.sell_price = 0
        await view._update_view(interaction)

# ===================================================================================
# --- COG UTAMA ---
# ===================================================================================

class InventoryCog(commands.Cog, name="Inventaris"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="inventory", aliases=["inv"])
    async def inventory_command(self, ctx: commands.Context):
        """Membuka panel inventory"""
        player_data = await get_player_data(self.bot.db, ctx.author.id)
        if not player_data:
             return 

        view = InventoryView(author=ctx.author, cog=self)
        await view.start(ctx)

async def setup(bot: commands.Bot):
    await bot.add_cog(InventoryCog(bot))