# cogs/transaction_cog.py

import discord
from discord.ext import commands
import json
import os
import asyncio
import math
import traceback
from collections import Counter

# Import database functions
# PASTIKAN SEMUA FUNGSI INI ADA DI database.py
from database import (
    get_player_data, 
    update_player_data, 
    get_player_inventory, 
    get_player_titles, 
    remove_player_title, 
    add_title_to_player,
    has_title
)
from ._utils import BotColors

# ===================================================================================
# VIEW: PANEL UTAMA TRANSAKSI
# ===================================================================================

class TransactionMainView(discord.ui.View):
    def __init__(self, ctx, cog, target_user):
        super().__init__(timeout=180) # Timeout diperpanjang jadi 3 menit
        self.ctx = ctx
        self.cog = cog
        self.target = target_user

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("Ini bukan sesi transaksimu.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Kirim Uang (Pay)", style=discord.ButtonStyle.success, emoji="💸")
    async def pay_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(PayModal(self.ctx, self.target, self.cog))

    @discord.ui.button(label="Beri Barang/Title", style=discord.ButtonStyle.primary, emoji="🎁")
    async def give_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        inventory = await get_player_inventory(self.cog.bot.db, self.ctx.author.id)
        titles = await get_player_titles(self.cog.bot.db, self.ctx.author.id)
        
        if not inventory and not titles:
            return await interaction.followup.send("Tasmu kosong (tidak ada item/title)!", ephemeral=True)
            
        view = ItemSelectView(self.ctx, self.target, self.cog, inventory, titles, mode="give")
        await interaction.edit_original_response(embed=view.get_embed(), view=view)

    @discord.ui.button(label="Jual di Market", style=discord.ButtonStyle.secondary, emoji="⚖️")
    async def market_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        inventory = await get_player_inventory(self.cog.bot.db, self.ctx.author.id)
        titles = await get_player_titles(self.cog.bot.db, self.ctx.author.id)
        
        if not inventory and not titles:
            return await interaction.followup.send("Tasmu kosong!", ephemeral=True)

        view = ItemSelectView(self.ctx, self.target, self.cog, inventory, titles, mode="sell")
        await interaction.edit_original_response(embed=view.get_embed(), view=view)

# ===================================================================================
# MODAL: INPUT JUMLAH UANG
# ===================================================================================

class PayModal(discord.ui.Modal):
    def __init__(self, ctx, target, cog):
        super().__init__(title="💸 Transfer Prisma")
        self.ctx = ctx
        self.target = target
        self.cog = cog
        self.amount = discord.ui.TextInput(label="Jumlah Prisma", placeholder="1000", min_length=1, max_length=10)
        self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            amount = int(self.amount.value)
            if amount <= 0: raise ValueError
        except: return await interaction.response.send_message("❌ Jumlah tidak valid.", ephemeral=True)

        sender_data = await get_player_data(self.cog.bot.db, self.ctx.author.id)
        if sender_data.get('prisma', 0) < amount:
            return await interaction.response.send_message("❌ Saldo tidak cukup.", ephemeral=True)

        target_data = await get_player_data(self.cog.bot.db, self.target.id)
        await update_player_data(self.cog.bot.db, self.ctx.author.id, prisma=sender_data['prisma'] - amount)
        await update_player_data(self.cog.bot.db, self.target.id, prisma=target_data.get('prisma', 0) + amount)

        embed = discord.Embed(title="💸 Transfer Berhasil", description=f"**{self.ctx.author.display_name}** mengirim `{amount:,}` 💎 ke **{self.target.display_name}**.", color=BotColors.SUCCESS)
        await interaction.response.edit_message(embed=embed, view=None)

# ===================================================================================
# VIEW: PILIH ITEM / TITLE
# ===================================================================================

class ItemSelectView(discord.ui.View):
    def __init__(self, ctx, target, cog, inventory, titles, mode="give"):
        super().__init__(timeout=180)
        self.ctx = ctx
        self.target = target
        self.cog = cog
        self.inventory = inventory
        self.titles = titles
        self.mode = mode 
        
        self.current_page = 0
        self.items_per_page = 25
        self.all_options = self._generate_all_options()
        self.total_pages = math.ceil(len(self.all_options) / self.items_per_page) or 1
        
        self.update_components()

    def _generate_all_options(self):
        options = []
        
        # 1. LIST TITLES
        for title_id in self.titles:
            title_data = self.cog.bot.get_title_by_id(title_id)
            if title_data:
                options.append(discord.SelectOption(
                    label=f"Title: {title_data['name']}",
                    value=f"title:{title_id}",
                    description=f"Rarity: {title_data.get('rarity', 'Common')}",
                    emoji="👑"
                ))

        # 2. LIST ITEMS
        item_counts = Counter(self.inventory)
        for item_id, count in item_counts.items():
            if item_data := self.cog.item_map.get(item_id):
                emoji = "🔮" if item_data.get('type') == 'artifact' else "📦"
                options.append(discord.SelectOption(
                    label=f"{item_data['name']} (x{count})",
                    value=f"item:{item_id}", 
                    description=f"[{'Artefak' if emoji=='🔮' else 'Item'}]",
                    emoji=emoji
                ))
            elif fish_data := self.cog.fish_map.get(item_id):
                options.append(discord.SelectOption(
                    label=f"{fish_data['name']} (x{count})",
                    value=f"item:{item_id}", 
                    description=f"[Ikan] {fish_data['rarity']}",
                    emoji="🐟"
                ))
            elif gear_data := self.cog.fishing_gear_map.get(item_id):
                options.append(discord.SelectOption(
                    label=f"{gear_data['name']} (x{count})",
                    value=f"item:{item_id}",
                    description=f"[Pancing]",
                    emoji="🎣"
                ))

        options.sort(key=lambda x: ("0" if x.value.startswith("title") else "1") + x.label)
        return options

    def update_components(self):
        self.clear_items()
        
        if not self.all_options:
            self.add_item(discord.ui.Button(label="Tas Kosong", disabled=True))
            back_btn = discord.ui.Button(label="Kembali", style=discord.ButtonStyle.danger, row=1)
            back_btn.callback = self.back_button
            self.add_item(back_btn)
            return

        start_idx = self.current_page * self.items_per_page
        end_idx = start_idx + self.items_per_page
        page_options = self.all_options[start_idx:end_idx]

        select = discord.ui.Select(
            placeholder=f"Pilih Item/Title (Hal {self.current_page + 1}/{self.total_pages})...", 
            options=page_options,
            row=0
        )
        select.callback = self.select_callback
        self.add_item(select)

        if self.total_pages > 1:
            prev_btn = discord.ui.Button(label="◀️", style=discord.ButtonStyle.primary, disabled=(self.current_page == 0), row=1)
            prev_btn.callback = self.prev_page
            self.add_item(prev_btn)
            indicator = discord.ui.Button(label=f"{self.current_page + 1}/{self.total_pages}", style=discord.ButtonStyle.secondary, disabled=True, row=1)
            self.add_item(indicator)
            next_btn = discord.ui.Button(label="▶️", style=discord.ButtonStyle.primary, disabled=(self.current_page >= self.total_pages - 1), row=1)
            next_btn.callback = self.next_page
            self.add_item(next_btn)

        row_back = 2 if self.total_pages > 1 else 1
        back_btn = discord.ui.Button(label="Kembali", style=discord.ButtonStyle.danger, row=row_back)
        back_btn.callback = self.back_button
        self.add_item(back_btn)

    async def prev_page(self, interaction):
        self.current_page -= 1
        self.update_components()
        await interaction.response.edit_message(view=self)

    async def next_page(self, interaction):
        self.current_page += 1
        self.update_components()
        await interaction.response.edit_message(view=self)

    async def select_callback(self, interaction: discord.Interaction):
        data_split = interaction.data['values'][0].split(":")
        data_type = data_split[0]
        data_id = int(data_split[1])
        
        if self.mode == "give":
            await self.process_give(interaction, data_type, data_id)
        else:
            await interaction.response.send_modal(
                SellPriceModal(self.ctx, self.target, self.cog, data_type, data_id)
            )

    async def process_give(self, interaction, data_type, data_id):
        if data_type == "title":
            if await has_title(self.cog.bot.db, self.target.id, data_id):
                return await interaction.response.send_message(f"❌ **{self.target.display_name}** sudah memiliki title tersebut!", ephemeral=True)

        try:
            success, obj_name = await self.cog.transfer_object(
                self.ctx.author.id, self.target.id, data_type, data_id
            )
            
            if success:
                embed = discord.Embed(title="🎁 Berhasil Terkirim!", description=f"**{obj_name}** berhasil dikirim ke **{self.target.display_name}**.", color=BotColors.INFO)
                await interaction.response.edit_message(embed=embed, view=None)
            else:
                await interaction.response.send_message("❌ Gagal mengirim. (Item dipakai/hilang/gagal database).", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error Sistem: {e}", ephemeral=True)
            print(f"Transfer Error: {e}")

    def get_embed(self):
        title = "🎁 Pilih Barang/Title" if self.mode == "give" else "⚖️ Pilih Barang/Title untuk Dijual"
        desc = "Pilih Item, Artefak, Ikan, atau Title dari menu di bawah.\n**Catatan:** Title/Item yang sedang dipakai tidak bisa dipilih."
        return discord.Embed(title=title, description=desc, color=BotColors.DEFAULT)

    async def back_button(self, interaction):
        embed = discord.Embed(title="🤝 Panel Transaksi", description=f"Transaksi dengan **{self.target.display_name}**.", color=BotColors.DEFAULT)
        view = TransactionMainView(self.ctx, self.cog, self.target)
        await interaction.response.edit_message(embed=embed, view=view)

# ===================================================================================
# MODAL & VIEW: MARKET
# ===================================================================================

class SellPriceModal(discord.ui.Modal):
    def __init__(self, ctx, target, cog, data_type, data_id):
        super().__init__(title="🏷️ Tentukan Harga Jual")
        self.ctx = ctx
        self.target = target
        self.cog = cog
        self.data_type = data_type
        self.data_id = data_id
        self.price = discord.ui.TextInput(label="Harga (Prisma)", placeholder="5000")
        self.add_item(self.price)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            price = int(self.price.value)
            if price <= 0: raise ValueError
        except: return await interaction.response.send_message("❌ Harga tidak valid.", ephemeral=True)

        if self.data_type == "title":
            if await has_title(self.cog.bot.db, self.target.id, self.data_id):
                return await interaction.response.send_message(f"❌ Batal: **{self.target.display_name}** sudah punya Title ini!", ephemeral=True)

        name = "Unknown"
        if self.data_type == "title":
            t = self.cog.bot.get_title_by_id(self.data_id)
            if t: name = t['name']
        else:
            if self.data_id in self.cog.item_map: name = self.cog.item_map[self.data_id]['name']
            elif self.data_id in self.cog.fish_map: name = self.cog.fish_map[self.data_id]['name']
            elif self.data_id in self.cog.fishing_gear_map: name = self.cog.fishing_gear_map[self.data_id]['name']

        # Kunci Item/Title
        locked = await self.cog.lock_object(self.ctx.author.id, self.data_type, self.data_id)
        if not locked:
            return await interaction.response.send_message("❌ Gagal mengunci barang (sedang dipakai/hilang).", ephemeral=True)

        offer_view = MarketOfferView(self.ctx, self.target, self.cog, self.data_type, self.data_id, price, name)
        embed = discord.Embed(
            title="⚖️ Penawaran Pasar",
            description=f"**Penjual:** {self.ctx.author.mention}\n**Pembeli:** {self.target.mention}\n\n📦 **Barang/Title:** {name}\n💰 **Harga:** `{price:,}` Prisma",
            color=BotColors.WARNING
        )
        await interaction.response.edit_message(content=self.target.mention, embed=embed, view=offer_view)
        offer_view.message = await interaction.original_response()

class MarketOfferView(discord.ui.View):
    def __init__(self, ctx, buyer, cog, data_type, data_id, price, obj_name):
        super().__init__(timeout=180) # Timeout 3 menit
        self.ctx = ctx
        self.buyer = buyer
        self.cog = cog
        self.data_type = data_type
        self.data_id = data_id
        self.price = price
        self.obj_name = obj_name
        self.is_finished = False

    async def on_timeout(self):
        if not self.is_finished: await self.cancel("Waktu habis.")

    async def cancel(self, reason):
        self.is_finished = True
        await self.cog.unlock_object(self.ctx.author.id, self.data_type, self.data_id)
        embed = discord.Embed(title="❌ Transaksi Batal", description=f"Alasan: {reason}\nBarang dikembalikan ke penjual.", color=BotColors.ERROR)
        for c in self.children: c.disabled = True
        try: await self.message.edit(embed=embed, view=self)
        except: pass

    @discord.ui.button(label="Beli Sekarang", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        # 1. Cek User
        if interaction.user.id != self.buyer.id:
            return await interaction.response.send_message("Hanya pembeli yang bisa menerima!", ephemeral=True)
            
        try:
            # 2. Cek Uang Pembeli
            buyer_data = await get_player_data(self.cog.bot.db, self.buyer.id)
            if buyer_data.get('prisma', 0) < self.price:
                return await interaction.response.send_message("❌ Uang tidak cukup!", ephemeral=True)

            self.is_finished = True
            
            # 3. Transfer Uang
            await update_player_data(self.cog.bot.db, self.buyer.id, prisma=buyer_data['prisma'] - self.price)
            seller_data = await get_player_data(self.cog.bot.db, self.ctx.author.id)
            await update_player_data(self.cog.bot.db, self.ctx.author.id, prisma=seller_data.get('prisma', 0) + self.price)

            # 4. Transfer Barang (Tambahkan ke pembeli)
            # Ini bagian paling krusial yang mungkin error sebelumnya
            await self.cog.unlock_object(self.buyer.id, self.data_type, self.data_id)
            
            embed = discord.Embed(title="✅ Transaksi Sukses!", description=f"**{self.obj_name}** terjual seharga `{self.price:,}` Prisma.", color=BotColors.SUCCESS)
            for c in self.children: c.disabled = True
            await interaction.response.edit_message(embed=embed, view=self)

        except Exception as e:
            # Jika error, kembalikan barang ke penjual dan lapor
            await self.cog.unlock_object(self.ctx.author.id, self.data_type, self.data_id)
            await interaction.response.send_message(f"❌ Terjadi kesalahan fatal: {e}\nBarang dikembalikan ke penjual.", ephemeral=True)
            print(f"Transaction Error: {e}")
            traceback.print_exc()

    @discord.ui.button(label="Tolak", style=discord.ButtonStyle.danger)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id in [self.buyer.id, self.ctx.author.id]:
            await self.cancel("Dibatalkan pengguna.")
            await interaction.response.defer()
        else: await interaction.response.send_message("Bukan urusanmu!", ephemeral=True)

# ===================================================================================
# COG UTAMA
# ===================================================================================

class TransactionCog(commands.Cog, name="Transaksi"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.item_map = {}
        self.fish_map = {}
        self.fishing_gear_map = {}
        self._load_data()

    def _load_data(self):
        try:
            with open('data/items.json', 'r', encoding='utf-8') as f:
                self.item_map = {i['id']: i for i in json.load(f)}
        except: pass
        try:
            with open('data/artifacts.json', 'r', encoding='utf-8') as f:
                for a in json.load(f):
                    if 'type' not in a: a['type'] = 'artifact'
                    self.item_map[a['id']] = a
        except: pass
        try:
            with open('data/fishes.json', 'r', encoding='utf-8') as f:
                self.fish_map = {i['id']: i for i in json.load(f)}
        except: pass
        try:
            with open('data/fishing_items.json', 'r', encoding='utf-8') as f:
                self.fishing_gear_map = {i['id']: i for i in json.load(f)}
        except: pass

    async def transfer_object(self, sender_id, target_id, data_type, data_id):
        if not await self.lock_object(sender_id, data_type, data_id):
            return False, None
        
        await self.unlock_object(target_id, data_type, data_id)
        
        name = "Unknown"
        if data_type == "title":
            t = self.bot.get_title_by_id(data_id)
            if t: name = t['name']
        else:
            if data_id in self.item_map: name = self.item_map[data_id]['name']
            elif data_id in self.fish_map: name = self.fish_map[data_id]['name']
            elif data_id in self.fishing_gear_map: name = self.fishing_gear_map[data_id]['name']
            
        return True, name

    async def lock_object(self, user_id, data_type, data_id):
        """Menghapus Item/Title dari user."""
        p_data = await get_player_data(self.bot.db, user_id)
        
        if data_type == "title":
            if p_data.get('equipped_title_id') == data_id: return False 
            titles = await get_player_titles(self.bot.db, user_id)
            if data_id in titles:
                await remove_player_title(self.bot.db, user_id, data_id)
                return True
            return False
        else:
            # Cek Equip
            equipped = json.loads(p_data.get('equipment') or '{}')
            if data_id in equipped.values(): return False
            fishing = json.loads(p_data.get('fishing_data') or '{}')
            f_equipped = fishing.get('equipped', {})
            if data_id == f_equipped.get('rod') or data_id == f_equipped.get('charm'): return False

            inv = await get_player_inventory(self.bot.db, user_id)
            if data_id in inv:
                inv.remove(data_id)
                await update_player_data(self.bot.db, user_id, inventory=json.dumps(inv))
                return True
            return False

    async def unlock_object(self, user_id, data_type, data_id):
        """Menambahkan Item/Title ke user."""
        if data_type == "title":
            # Pastikan fungsi ini ada di database.py
            await add_title_to_player(self.bot.db, user_id, data_id)
        else:
            inv = await get_player_inventory(self.bot.db, user_id)
            inv.append(data_id)
            await update_player_data(self.bot.db, user_id, inventory=json.dumps(inv))

    @commands.command(name="transaksi", aliases=["trade", "trx"])
    async def transaction_panel(self, ctx, target: discord.Member):
        if target.bot or target.id == ctx.author.id: return await ctx.send("❌ Target tidak valid.")
        
        p1 = await get_player_data(self.bot.db, ctx.author.id)
        if not p1: return await ctx.send("Kamu belum debut!")
        p2 = await get_player_data(self.bot.db, target.id)
        if not p2: return await ctx.send(f"**{target.display_name}** belum debut!")

        embed = discord.Embed(
            title="🤝 Panel Transaksi",
            description=f"Transaksi dengan **{target.display_name}**.\nPilih jenis transaksi di bawah:",
            color=BotColors.DEFAULT
        )
        view = TransactionMainView(ctx, self, target)
        await ctx.send(embed=embed, view=view)

async def setup(bot: commands.Bot):
    await bot.add_cog(TransactionCog(bot))