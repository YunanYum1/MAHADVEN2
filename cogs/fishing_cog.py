import discord
from discord.ext import commands
import json
import random
import asyncio
import os
import time
from collections import Counter
import traceback

from database import get_player_data, update_player_data, get_player_inventory
# Import BotColors dari utils
from ._utils import BotColors

# Konstanta Panah
ARROWS = {"UP": "⬆️", "DOWN": "⬇️", "LEFT": "⬅️", "RIGHT": "➡️"}
ARROW_KEYS = list(ARROWS.keys())

# ===================================================================================
# VIEW PERMAINAN (DIFFICULTY SCALING - BLIND MODE)
# ===================================================================================

class FishingGameView(discord.ui.View):
    def __init__(self, ctx, cog, fish_data, bonuses):
        # LOGIKA WAKTU (PER TOMBOL):
        base_time = 2.5 
        
        # Ditambah bonus dari Charm
        self.step_timeout = base_time + bonuses.get('time_bonus', 0.0)
        
        super().__init__(timeout=self.step_timeout)
        
        self.ctx = ctx
        self.cog = cog 
        self.fish = fish_data
        self.bonuses = bonuses
        self.sequence = []      
        self.current_step = 0   
        self.is_game_over = False
        self.message = None

        # Generate urutan panah berdasarkan difficulty
        length = self.fish['difficulty']
        for _ in range(length):
            self.sequence.append(random.choice(ARROW_KEYS))
        
        self._scramble_buttons()

    def _scramble_buttons(self):
        self.clear_items() 
        buttons = [
            discord.ui.Button(emoji="⬆️", custom_id="UP", style=discord.ButtonStyle.secondary),
            discord.ui.Button(emoji="⬇️", custom_id="DOWN", style=discord.ButtonStyle.secondary),
            discord.ui.Button(emoji="⬅️", custom_id="LEFT", style=discord.ButtonStyle.secondary),
            discord.ui.Button(emoji="➡️", custom_id="RIGHT", style=discord.ButtonStyle.secondary)
        ]
        random.shuffle(buttons)
        for btn in buttons:
            btn.callback = self.button_callback
            self.add_item(btn)

    async def button_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.ctx.author.id:
            return await interaction.response.send_message("Ini bukan pancinganmu!", ephemeral=True)
        if self.is_game_over: return

        pressed_dir = interaction.data['custom_id']
        target_dir = self.sequence[self.current_step]

        if pressed_dir == target_dir:
            self.current_step += 1
            if self.current_step >= len(self.sequence):
                self.is_game_over = True
                await self.handle_win(interaction)
            else:
                # Jika benar, acak tombol lagi.
                self._scramble_buttons()
                await self.update_embed(interaction)
        else:
            self.is_game_over = True
            await self.handle_loss(interaction, f"Salah tombol! Targetnya adalah {ARROWS[target_dir]}!")

    def get_embed(self):
        target_dir = self.sequence[self.current_step]
        target_emoji = ARROWS[target_dir]
        
        # BLIND MODE: Tanpa progress bar
        embed = discord.Embed(
            title="🎣 TARIK SEKUAT TENAGA!",
            description=(
                f"# TEKAN: {target_emoji}\n\n"
                f"*Pertahankan fokusmu! Jangan sampai lepas!*"
            ),
            # Menggunakan warna khusus saat ikan menyambar (Merah)
            color=BotColors.FISH_HOOKED 
        )
        embed.set_footer(text=f"Batas Waktu: {self.step_timeout:.1f} detik / tombol")
        return embed

    async def update_embed(self, interaction: discord.Interaction):
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

    async def handle_win(self, interaction: discord.Interaction):
        self.stop()
        player_inventory = await get_player_inventory(self.cog.bot.db, self.ctx.author.id)
        player_inventory.append(self.fish['id'])
        await update_player_data(self.cog.bot.db, self.ctx.author.id, inventory=json.dumps(player_inventory))

        # Menggunakan palet warna dari BotColors
        rarity_colors = {
            "Common": BotColors.COMMON,
            "Rare": BotColors.RARE,
            "Epic": BotColors.EPIC,
            "Legendary": BotColors.LEGENDARY,
            "Mitos": BotColors.MYTHIC,
            "Godly": BotColors.ARTIFACT # Menggunakan Cyan/Artifact untuk Godly
        }
        color = rarity_colors.get(self.fish['rarity'], BotColors.SUCCESS)

        embed = discord.Embed(title="✨ TANGKAPAN BERHASIL!", color=color)
        embed.add_field(name="Hasil", value=f"{self.fish['emoji']} {self.fish['name']}")
        embed.add_field(name="Info", value=f"Rarity: **{self.fish['rarity']}**\nHarga: `{self.fish['price']} 💎`")
        embed.set_thumbnail(url=self.ctx.author.display_avatar.url)
        embed.set_footer(text="Kembali ke menu dalam 3 detik...")
        
        for child in self.children: child.disabled = True
        await interaction.response.edit_message(embed=embed, view=None)
        await self._return_to_menu(interaction)

    async def handle_loss(self, interaction: discord.Interaction, reason: str):
        self.stop()
        # Menggunakan warna khusus saat ikan lepas (Abu-abu/Blue Grey)
        embed = discord.Embed(
            title="💦 IKAN LEPAS!", 
            description=f"{reason}\n\nIkan terlalu lincah untukmu!", 
            color=BotColors.FISH_ESCAPED
        )
        embed.set_footer(text="Kembali ke menu dalam 3 detik...")
        for child in self.children: child.disabled = True
        await interaction.response.edit_message(embed=embed, view=None)
        await self._return_to_menu(interaction)

    async def on_timeout(self):
        if not self.is_game_over:
            self.is_game_over = True
            try:
                # Warna ikan lepas
                embed = discord.Embed(
                    title="⏰ TALI PANCING PUTUS!", 
                    description="Waktu habis! Kamu terlalu lambat menekan tombol.", 
                    color=BotColors.FISH_ESCAPED
                )
                if self.message: await self.message.edit(embed=embed, view=None)
                await asyncio.sleep(3)
                await self._restore_main_panel_manual()
            except: pass

    async def _return_to_menu(self, interaction: discord.Interaction):
        await asyncio.sleep(3)
        embed, view = await self.cog._get_main_panel(self.ctx)
        try:
            await interaction.edit_original_response(embed=embed, view=view)
            view.message = await interaction.original_response()
        except:
            if self.message:
                await self.message.edit(embed=embed, view=view)
                view.message = self.message

    async def _restore_main_panel_manual(self):
        embed, view = await self.cog._get_main_panel(self.ctx)
        if self.message:
            await self.message.edit(embed=embed, view=view)
            view.message = self.message

# ===================================================================================
# VIEW: TAS IKAN (INVENTORY VIEWER & DEPOSIT)
# ===================================================================================

class FishBagView(discord.ui.View):
    def __init__(self, ctx, cog, inventory_list):
        super().__init__(timeout=120)
        self.ctx = ctx
        self.cog = cog
        self.inventory_list = inventory_list
        self.build_components()

    def build_components(self):
        self.clear_items()
        
        # Hitung jumlah ikan
        fish_counts = Counter([i for i in self.inventory_list if self.cog.get_fish_data(i)])
        
        # --- ROW 0: DEPOSIT KE AQUARIUM ---
        deposit_options = []
        for fish_id, count in fish_counts.items():
            fish = self.cog.get_fish_data(fish_id)
            if fish:
                deposit_options.append(discord.SelectOption(
                    label=f"Simpan: {fish['name']} (x{count})",
                    value=str(fish_id),
                    description="Pindahkan ke Aquarium (Aman)",
                    emoji="📥"
                ))
        
        if deposit_options:
            deposit_select = discord.ui.Select(
                placeholder="📥 Pilih ikan untuk DISIMPAN ke Aquarium...", 
                options=deposit_options[:25], 
                row=0,
                custom_id="deposit_select"
            )
            deposit_select.callback = self.deposit_callback
            self.add_item(deposit_select)
        else:
            self.add_item(discord.ui.Button(label="Tas Kosong (Tidak ada ikan)", disabled=True, row=0))

        # --- ROW 1: JUAL SATUAN ---
        sell_options = []
        for fish_id, count in fish_counts.items():
            fish = self.cog.get_fish_data(fish_id)
            if fish:
                sell_options.append(discord.SelectOption(
                    label=f"Jual: {fish['name']} (x{count})",
                    value=str(fish_id),
                    description=f"Harga: {fish['price']} Prisma per ekor",
                    emoji="💰"
                ))

        if sell_options:
            sell_select = discord.ui.Select(
                placeholder="💰 Pilih ikan untuk DIJUAL Satuan...", 
                options=sell_options[:25], 
                row=1,
                custom_id="sell_select"
            )
            sell_select.callback = self.sell_single_callback
            self.add_item(sell_select)

        # --- ROW 2: TOMBOL AKSI ---
        
        # Tombol Jual Semua
        sell_all_btn = discord.ui.Button(
            label="Jual SEMUA Ikan", 
            style=discord.ButtonStyle.danger, 
            emoji="💸", 
            row=2,
            disabled=(not fish_counts) # Disable jika tidak ada ikan
        )
        sell_all_btn.callback = self.sell_all_callback
        self.add_item(sell_all_btn)

        # Tombol Kembali
        back_btn = discord.ui.Button(label="Kembali", style=discord.ButtonStyle.secondary, row=2)
        back_btn.callback = self.back_callback
        self.add_item(back_btn)

    async def deposit_callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        fish_id = int(interaction.data['values'][0])
        await self._move_item(interaction, fish_id, to_aquarium=True)

    async def sell_single_callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            fish_id = int(interaction.data['values'][0])
            inventory = await get_player_inventory(self.cog.bot.db, self.ctx.author.id)
            player_data = await get_player_data(self.cog.bot.db, self.ctx.author.id)
            
            if fish_id in inventory:
                fish_data = self.cog.get_fish_data(fish_id)
                inventory.remove(fish_id)
                
                new_prisma = player_data.get('prisma', 0) + fish_data['price']
                await update_player_data(self.cog.bot.db, self.ctx.author.id, inventory=json.dumps(inventory), prisma=new_prisma)
                
                self.inventory_list = inventory
                self.build_components()
                embed = self.cog._create_bag_embed(self.ctx.author, inventory)
                
                await interaction.edit_original_response(embed=embed, view=self)
                await interaction.followup.send(f"💰 Berhasil menjual **{fish_data['name']}** seharga **{fish_data['price']}** Prisma.", ephemeral=True)
            else:
                await interaction.followup.send("Gagal: Ikan sudah tidak ada.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"Error: {e}", ephemeral=True)

    async def sell_all_callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            inventory = await get_player_inventory(self.cog.bot.db, self.ctx.author.id)
            player_data = await get_player_data(self.cog.bot.db, self.ctx.author.id)
            
            total_earnings = 0
            sold_count = 0
            new_inventory = []
            fish_ids = [f['id'] for f in self.cog.fishes]

            for item_id in inventory:
                if item_id in fish_ids:
                    fish_data = self.cog.get_fish_data(item_id)
                    if fish_data:
                        total_earnings += fish_data['price']
                        sold_count += 1
                else:
                    new_inventory.append(item_id)

            if sold_count > 0:
                new_prisma = player_data.get('prisma', 0) + total_earnings
                await update_player_data(self.cog.bot.db, self.ctx.author.id, inventory=json.dumps(new_inventory), prisma=new_prisma)
                
                self.inventory_list = new_inventory
                self.build_components()
                embed = self.cog._create_bag_embed(self.ctx.author, new_inventory)
                
                await interaction.edit_original_response(embed=embed, view=self)
                await interaction.followup.send(f"💸 **PANEN RAYA!**\nTerjual: {sold_count} Ekor\nTotal: **+{total_earnings} Prisma**", ephemeral=True)
            else:
                await interaction.followup.send("Tidak ada ikan untuk dijual.", ephemeral=True)

        except Exception as e:
            await interaction.followup.send(f"Error: {e}", ephemeral=True)

    async def _move_item(self, interaction, fish_id, to_aquarium):
        inventory = await get_player_inventory(self.cog.bot.db, self.ctx.author.id)
        player_data = await get_player_data(self.cog.bot.db, self.ctx.author.id)
        
        fishing_data = json.loads(player_data.get('fishing_data') or '{"aquarium": []}')
        aquarium = fishing_data.get('aquarium', [])

        if fish_id in inventory:
            inventory.remove(fish_id)
            aquarium.append(fish_id)
            fishing_data['aquarium'] = aquarium
            
            await update_player_data(self.cog.bot.db, self.ctx.author.id, inventory=json.dumps(inventory), fishing_data=json.dumps(fishing_data))
            
            self.inventory_list = inventory
            self.build_components()
            embed = self.cog._create_bag_embed(self.ctx.author, inventory)
            await interaction.edit_original_response(embed=embed, view=self)
            await interaction.followup.send(f"📥 Ikan disimpan ke Aquarium.", ephemeral=True)
        else:
            await interaction.followup.send("Gagal memindahkan item.", ephemeral=True)

    async def back_callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        embed, view = await self.cog._get_main_panel(self.ctx)
        await interaction.edit_original_response(embed=embed, view=view)

# ===================================================================================
# VIEW: AQUARIUM (LUXURY EDITION)
# ===================================================================================

class AquariumView(discord.ui.View):
    def __init__(self, ctx, cog, aquarium_list):
        super().__init__(timeout=120)
        self.ctx = ctx
        self.cog = cog
        self.aquarium_list = aquarium_list
        self.build_components()

    def build_components(self):
        self.clear_items()
        fish_counts = Counter(self.aquarium_list)
        
        rarity_weight = {"Godly": 6, "Mitos": 5, "Legendary": 4, "Epic": 3, "Rare": 2, "Common": 1}
        
        sorted_options = []
        for fish_id, count in fish_counts.items():
            fish = self.cog.get_fish_data(fish_id)
            if fish:
                r_icon = "✨" if fish['rarity'] == 'Godly' else "🐉" if fish['rarity'] == 'Mitos' else "🌟" if fish['rarity'] == 'Legendary' else "🐟"
                sorted_options.append({
                    "label": f"{fish['name']} (x{count})",
                    "value": str(fish_id),
                    "desc": f"[{fish['rarity']}] Ambil dari galeri.",
                    "emoji": r_icon,
                    "weight": rarity_weight.get(fish['rarity'], 0)
                })
        
        sorted_options.sort(key=lambda x: x['weight'], reverse=True)

        final_options = [
            discord.SelectOption(label=o['label'], value=o['value'], description=o['desc'], emoji=o['emoji'])
            for o in sorted_options
        ]
        
        if final_options:
            select = discord.ui.Select(placeholder="🏛️ Pilih Spesimen untuk Diambil...", options=final_options[:25], row=0)
            select.callback = self.withdraw_callback
            self.add_item(select)
        else:
            self.add_item(discord.ui.Button(label="Galeri Kosong", disabled=True, emoji="🕸️", row=0))

        back_btn = discord.ui.Button(label="Tutup Galeri", style=discord.ButtonStyle.secondary, row=1)
        back_btn.callback = self.back_callback
        self.add_item(back_btn)

    async def withdraw_callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            fish_id = int(interaction.data['values'][0])
            
            inventory = await get_player_inventory(self.cog.bot.db, self.ctx.author.id)
            player_data = await get_player_data(self.cog.bot.db, self.ctx.author.id)
            
            fishing_data = json.loads(player_data.get('fishing_data') or '{"aquarium": []}')
            aquarium = fishing_data.get('aquarium', [])

            if fish_id in aquarium:
                aquarium.remove(fish_id)
                inventory.append(fish_id)
                fishing_data['aquarium'] = aquarium
                
                await update_player_data(self.cog.bot.db, self.ctx.author.id, inventory=json.dumps(inventory), fishing_data=json.dumps(fishing_data))
                
                self.aquarium_list = aquarium
                self.build_components()
                
                embed = self.cog._create_aquarium_embed(self.ctx.author, aquarium)
                await interaction.edit_original_response(embed=embed, view=self)
                
                fish_name = self.cog.get_fish_data(fish_id)['name']
                await interaction.followup.send(f"✅ **{fish_name}** telah dipindahkan dari Galeri ke Tas.", ephemeral=True)
            else:
                await interaction.followup.send("Gagal: Spesimen tidak ditemukan.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"Error: {e}", ephemeral=True)

    async def back_callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        embed, view = await self.cog._get_main_panel(self.ctx)
        await interaction.edit_original_response(embed=embed, view=view)

# ===================================================================================
# VIEW: ITEM PREVIEW & CONFIRMATION (BARU)
# ===================================================================================

class FishingItemPreviewView(discord.ui.View):
    def __init__(self, ctx, cog, item_id, item_data):
        super().__init__(timeout=60)
        self.ctx = ctx
        self.cog = cog
        self.item_id = item_id
        self.item = item_data

    @discord.ui.button(label="✅ Konfirmasi Beli", style=discord.ButtonStyle.success)
    async def buy_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        
        player_data = await get_player_data(self.cog.bot.db, self.ctx.author.id)
        raw = player_data.get('fishing_data')
        fishing_data = json.loads(raw) if raw else {"inventory": ["rod_basic"]}
        if "inventory" not in fishing_data: fishing_data["inventory"] = ["rod_basic"]

        # Cek Kepemilikan
        if self.item_id in fishing_data['inventory']:
            return await interaction.followup.send("❌ Kamu sudah memiliki item ini!", ephemeral=True)
        
        # Cek Uang
        if player_data.get('prisma', 0) < self.item['price']:
            return await interaction.followup.send("❌ Saldo Prisma tidak mencukupi!", ephemeral=True)

        # Proses Transaksi
        new_prisma = player_data.get('prisma', 0) - self.item['price']
        fishing_data['inventory'].append(self.item_id)
        
        await update_player_data(self.cog.bot.db, self.ctx.author.id, prisma=new_prisma, fishing_data=json.dumps(fishing_data))
        
        # Feedback Sukses
        embed = discord.Embed(
            title="🎉 Pembelian Berhasil!", 
            description=f"Kamu telah membeli **{self.item['name']}** seharga `{self.item['price']}` Prisma.",
            color=BotColors.SUCCESS
        )
        # Kembali ke panel utama agar user bisa langsung equip
        main_embed, main_view = await self.cog._get_main_panel(self.ctx)
        
        await interaction.edit_original_response(embed=embed, view=None)
        await interaction.followup.send(embed=main_embed, view=main_view)

    @discord.ui.button(label="🔙 Kembali ke Toko", style=discord.ButtonStyle.secondary)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        # Kembali ke view Toko menggunakan helper
        await self.cog.open_shop_ui(self.ctx, interaction)

# ===================================================================================
# VIEW: SHOP
# ===================================================================================
class FishingShopView(discord.ui.View):
    def __init__(self, ctx, cog):
        super().__init__(timeout=120)
        self.ctx = ctx
        self.cog = cog
        self.rod_items = [item for item in self.cog.fishing_items.values() if item['type'] == 'rod']
        self.charm_items = [item for item in self.cog.fishing_items.values() if item['type'] == 'charm']
        self.current_category = 'rod' 
        self.update_components()

    def update_components(self):
        """Membangun ulang komponen View berdasarkan kategori yang aktif."""
        self.clear_items()
        
        items_to_display = self.rod_items if self.current_category == 'rod' else self.charm_items
        options = []
        for item in items_to_display:
            icon = "🎣" if item['type'] == 'rod' else "📿"
            options.append(discord.SelectOption(
                label=f"{item['name']} ({item['price']:,} 💎)", 
                value=item['id'], 
                description=f"{item['description'][:90]}...", 
                emoji=icon
            ))

        # --- ROW 0: KATEGORI & KEMBALI BUTTONS ---
        
        rod_btn = discord.ui.Button(
            label="Joran", 
            style=discord.ButtonStyle.success if self.current_category == 'rod' else discord.ButtonStyle.secondary, 
            emoji="🎣", 
            row=0, 
            disabled=(self.current_category == 'rod')
        )
        rod_btn.callback = self.rod_button_callback
        self.add_item(rod_btn)

        charm_btn = discord.ui.Button(
            label="Charm", 
            style=discord.ButtonStyle.success if self.current_category == 'charm' else discord.ButtonStyle.secondary, 
            emoji="📿", 
            row=0, 
            disabled=(self.current_category == 'charm')
        )
        charm_btn.callback = self.charm_button_callback
        self.add_item(charm_btn)

        # Tombol Kembali (Fixed Callback)
        back_btn = discord.ui.Button(label="Kembali", style=discord.ButtonStyle.danger, row=0)
        back_btn.callback = self.back_button_callback # Ubah nama fungsi callback
        self.add_item(back_btn)

        # --- ROW 1: SELECT MENU ---
        placeholder_text = f"Pilih {self.current_category.capitalize()} untuk melihat detail..."
            
        if not options: options.append(discord.SelectOption(label="Toko Kosong", value="empty"))
        
        select = discord.ui.Select(placeholder=placeholder_text, options=options, row=1)
        select.callback = self.select_callback
        self.add_item(select)


    async def rod_button_callback(self, interaction: discord.Interaction):
        if self.current_category == 'rod': return await interaction.response.defer()
        await interaction.response.defer()
        self.current_category = 'rod'
        self.update_components()
        await self.cog.open_shop_ui(self.ctx, interaction, self.current_category, self)

    async def charm_button_callback(self, interaction: discord.Interaction):
        if self.current_category == 'charm': return await interaction.response.defer()
        await interaction.response.defer()
        self.current_category = 'charm'
        self.update_components()
        await self.cog.open_shop_ui(self.ctx, interaction, self.current_category, self)
    
    async def back_button_callback(self, interaction: discord.Interaction):
        # Callback Kembali (hanya butuh interaction)
        await interaction.response.defer()
        embed, view = await self.cog._get_main_panel(self.ctx)
        self.stop() # Hentikan view Toko
        await interaction.edit_original_response(embed=embed, view=view)

    async def select_callback(self, interaction: discord.Interaction):
        item_id = interaction.data['values'][0]
        if item_id == "empty": return await interaction.response.defer()
        
        await interaction.response.defer()
        item = self.cog.fishing_items.get(item_id)
        
        if not item:
            return await interaction.followup.send("Item tidak ditemukan data.", ephemeral=True)

        # Buat Embed Detail / Spek Item (LOGIKA SAMA)
        tipe_item = "🎣 Joran (Rod)" if item['type'] == 'rod' else "📿 Jimat (Charm)"
        stats_desc = []
        
        if item.get('luck', 0) > 0:
            stats_desc.append(f"🍀 **Luck:** +{item['luck']}")
        if item.get('time_bonus', 0.0) > 0:
            stats_desc.append(f"⏰ **Waktu:** +{item['time_bonus']} detik")
        
        stats_text = "\n".join(stats_desc) if stats_desc else "Tidak ada efek khusus."

        embed = discord.Embed(title=f"🔍 Detail: {item['name']}", color=BotColors.INFO)
        embed.description = f"_{item['description']}_"
        embed.add_field(name="🏷️ Tipe", value=tipe_item, inline=True)
        embed.add_field(name="💎 Harga", value=f"`{item['price']:,}` Prisma", inline=True)
        embed.add_field(name="✨ Statistik / Efek", value=stats_text, inline=False)
        embed.set_footer(text="Klik tombol di bawah untuk membeli.")

        # Ganti view ke Preview/Confirmation View
        view = FishingItemPreviewView(self.ctx, self.cog, item_id, item)
        await interaction.edit_original_response(embed=embed, view=view)

# ===================================================================================
# VIEW: LOADOUT
# ===================================================================================

class FishingLoadoutView(discord.ui.View):
    def __init__(self, ctx, cog, fishing_data):
        super().__init__(timeout=120)
        self.ctx = ctx
        self.cog = cog
        self.fishing_data = fishing_data
        self.build_menus()

    def build_menus(self):
        inventory = self.fishing_data.get('inventory', [])
        rod_opts = [discord.SelectOption(label=self.cog.fishing_items[i]['name'], value=i, default=(i == self.fishing_data.get('equipped', {}).get('rod')), emoji="🎣") for i in inventory if self.cog.fishing_items.get(i, {}).get('type') == 'rod']
        if rod_opts:
            s1 = discord.ui.Select(placeholder="Ganti Joran...", options=rod_opts, row=0, custom_id="rod_select")
            s1.callback = self.equip_callback
            self.add_item(s1)
        
        charm_opts = [discord.SelectOption(label="Lepas Charm", value="unequip")]
        for i in inventory:
            if self.cog.fishing_items.get(i, {}).get('type') == 'charm':
                charm_opts.append(discord.SelectOption(label=self.cog.fishing_items[i]['name'], value=i, default=(i == self.fishing_data.get('equipped', {}).get('charm')), emoji="📿"))
        
        s2 = discord.ui.Select(placeholder="Ganti Charm...", options=charm_opts, row=1, custom_id="charm_select")
        s2.callback = self.equip_callback
        self.add_item(s2)

    @discord.ui.button(label="Kembali", style=discord.ButtonStyle.secondary, row=2)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        embed, view = await self.cog._get_main_panel(self.ctx)
        await interaction.edit_original_response(embed=embed, view=view)

    async def equip_callback(self, interaction: discord.Interaction):
        val = interaction.data['values'][0]
        cid = interaction.data['custom_id']
        if 'equipped' not in self.fishing_data: self.fishing_data['equipped'] = {}
        
        msg = ""
        if cid == "rod_select":
            self.fishing_data['equipped']['rod'] = val
            msg = f"🎣 Joran diganti."
        else:
            if val == "unequip": self.fishing_data['equipped']['charm'] = None; msg = "📿 Charm dilepas."
            else: self.fishing_data['equipped']['charm'] = val; msg = f"📿 Charm diganti."

        await update_player_data(self.cog.bot.db, self.ctx.author.id, fishing_data=json.dumps(self.fishing_data))
        self.clear_items(); self.build_menus(); self.add_item(self.back_button)
        
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(msg, ephemeral=True)

# ===================================================================================
# VIEW PANEL UTAMA
# ===================================================================================

class FishingPanelView(discord.ui.View):
    def __init__(self, ctx, cog):
        super().__init__(timeout=180)
        self.ctx = ctx
        self.cog = cog
        # Hapus tombol Auto Fish dari `self.children` agar bisa dibuat ulang dengan custom_id
        # dan memastikan _update_auto_button_state bisa berjalan.
        
    def _update_auto_button_state(self):
        """Mengubah warna dan teks tombol berdasarkan status Auto Fishing user."""
        # Cari tombol dengan custom_id 'btn_auto_fish'
        for child in self.children:
            if isinstance(child, discord.ui.Button) and child.custom_id == "btn_auto_fish":
                
                # Cek apakah ID user ada di daftar session auto fishing
                if self.ctx.author.id in self.cog.autofish_sessions:
                    # STATUS: SEDANG AUTO -> Tombol Stop
                    child.label = "Selesai Auto"
                    child.style = discord.ButtonStyle.danger  # Warna Merah
                    child.emoji = "🛑"
                else:
                    # STATUS: TIDAK AUTO -> Tombol Start
                    child.label = "Auto Fishing"
                    child.style = discord.ButtonStyle.secondary # Warna Biru
                    child.emoji = "🤖"
                break
        
    # PENTING: Tombol-tombol harus didefinisikan ulang di sini
    
    # ROW 0
    
    @discord.ui.button(label="Mulai Memancing", style=discord.ButtonStyle.primary, emoji="🎣", row=0)
    async def start_fishing(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(embed=discord.Embed(title="🌊 Menunggu...", description="Melemparkan kail ke kedalaman...", color=BotColors.FISH_WATER), view=None)
        
        user_id = self.ctx.author.id
        
        # --- KODE BARU: CEK DAN HENTIKAN AUTO FISHING ---
        if user_id in self.cog.autofish_sessions:
            await self.cog.stop_auto_fishing_logic(user_id)
            await interaction.followup.send("🛑 Auto Fishing dihentikan untuk memulai memancing manual.", ephemeral=True)
            
            # PENTING: Karena Auto Fishing dihentikan, kita harus mendapatkan VIEW yang baru
            # dengan tombol Auto Fish yang sudah ter-update (menjadi 'Auto Fishing' lagi).
            # Kita tidak bisa langsung lanjut, harus memuat ulang panel utama dulu.
            
            # Panggil lagi _get_main_panel, yang akan membuat view baru
            embed, new_view = await self.cog._get_main_panel(self.ctx)
            # Edit pesan dengan view yang benar (tombol 'Auto Fishing' aktif)
            await interaction.edit_original_response(embed=embed, view=new_view)
            
            # Beri sedikit delay sebelum mengulang aksi (agar tidak spam)
            await asyncio.sleep(2) 
            
            # Setelah menghentikan auto dan me-load ulang panel, 
            # player harus menekan tombol 'Mulai Memancing' lagi.
            return
        # --- AKHIR KODE BARU ---
        
        # Lanjutkan Logika Manual Fishing jika tidak ada sesi Auto yang berjalan
        
        player_data = await get_player_data(self.cog.bot.db, user_id)
        fishing_data = json.loads(player_data.get('fishing_data') or '{}')
        equipped = fishing_data.get('equipped', {'rod': 'rod_basic'})
        
        rod_item = self.cog.fishing_items.get(equipped.get('rod'), self.cog.fishing_items.get('rod_basic'))
        charm_item = self.cog.fishing_items.get(equipped.get('charm'), {'luck': 0, 'time_bonus': 0.0})
        if not rod_item: rod_item = {'luck': 0, 'time_bonus': 0.0}

        total_luck = rod_item.get('luck', 0) + charm_item.get('luck', 0)
        total_time_bonus = rod_item.get('time_bonus', 0.0) + charm_item.get('time_bonus', 0.0)
        
        target_fish = self.cog._calculate_catch_result(total_luck, is_auto=False)

        await asyncio.sleep(random.uniform(2.0, 4.5))
        
        game_view = FishingGameView(self.ctx, self.cog, target_fish, {'time_bonus': total_time_bonus})
        try:
            # Lanjutkan dengan QTE seperti biasa
            await interaction.edit_original_response(embed=game_view.get_embed(), view=game_view)
            game_view.message = await interaction.original_response()
        except: 
            pass

  

    # ROW 0 (AUTO FISHING)
    @discord.ui.button(label="Auto Fishing", style=discord.ButtonStyle.primary, emoji="🤖", row=0, custom_id="btn_auto_fish")
    async def toggle_auto_fish(self, interaction: discord.Interaction, button: discord.ui.Button):
        # 1. Acknowledge the interaction immediately
        await interaction.response.defer()
        
        user_id = self.ctx.author.id
        
        # Logika Toggle
        if user_id in self.cog.autofish_sessions:
            # Jika sedang aktif -> MATIKAN
            await self.cog.stop_auto_fishing_logic(user_id)
            msg = "🛑 **Auto Fishing Dimatikan.**"
        else:
            # Jika mati -> NYALAKAN
            success, error_msg = await self.cog.start_auto_fishing_logic(self.ctx)
            if success:
                msg = "✅ **Auto Fishing Dimulai!**\nPastikan berinteraksi dengan bot minimal setiap 120 detik (lewat command lain) agar tidak dihentikan."
            else:
                return await interaction.followup.send(error_msg, ephemeral=True)
                
        # 2. Hentikan view yang lama
        self.stop()
        
        # 3. Dapatkan EMBED dan VIEW BARU yang telah diupdate
        # _get_main_panel akan membuat FishingPanelView baru dan memanggil _update_auto_button_state()
        embed, new_view = await self.cog._get_main_panel(self.ctx)
        
        # 4. Edit pesan panel utama dengan VIEW YANG BARU
        await interaction.edit_original_response(embed=embed, view=new_view)
        new_view.message = interaction.message # Simpan referensi pesan baru

        # 5. Kirim notifikasi status (Ephemeral=True supaya tidak nyampah)
        await interaction.followup.send(msg, ephemeral=True)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("Buka panel memancingmu sendiri!", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Toko Pancing", style=discord.ButtonStyle.primary, emoji="🛒", row=0)
    async def open_shop(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer() 
        await self.cog.open_shop_ui(self.ctx, interaction)

    @discord.ui.button(label="Equipment", style=discord.ButtonStyle.success, emoji="🎒", row=1)
    async def open_equipment(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer() 
        player_data = await get_player_data(self.cog.bot.db, self.ctx.author.id)
        fishing_data = json.loads(player_data.get('fishing_data') or '{}')
        view = FishingLoadoutView(self.ctx, self.cog, fishing_data)
        await interaction.edit_original_response(embed=discord.Embed(title="🎒 Tas Pancing", description="Ganti peralatan memancingmu.", color=BotColors.DEFAULT), view=view)

    # ROW 1
    @discord.ui.button(label="Tas Ikan & Jual", style=discord.ButtonStyle.success, emoji="👜", row=1)
    async def open_bag(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer() 
        inventory = await get_player_inventory(self.cog.bot.db, self.ctx.author.id)
        embed = self.cog._create_bag_embed(self.ctx.author, inventory)
        view = FishBagView(self.ctx, self.cog, inventory)
        await interaction.edit_original_response(embed=embed, view=view)

    # ROW 2
    @discord.ui.button(label="Aquarium", style=discord.ButtonStyle.primary, emoji="🐠", row=2)
    async def open_aquarium(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer() 
        try:
            player_data = await get_player_data(self.cog.bot.db, self.ctx.author.id)
            raw_data = player_data.get('fishing_data')
            if not raw_data:
                fishing_data = {"inventory": ["rod_basic"], "equipped": {"rod": "rod_basic", "charm": None}, "aquarium": []}
                await update_player_data(self.cog.bot.db, self.ctx.author.id, fishing_data=json.dumps(fishing_data))
            else:
                fishing_data = json.loads(raw_data)
                
            aquarium = fishing_data.get('aquarium', [])
            
            embed = self.cog._create_aquarium_embed(self.ctx.author, aquarium)
            view = AquariumView(self.ctx, self.cog, aquarium)
            await interaction.edit_original_response(embed=embed, view=view)
        except Exception as e:
            await interaction.followup.send(f"Terjadi kesalahan saat membuka Aquarium: {e}", ephemeral=True)

# ===================================================================================
# COG UTAMA
# ===================================================================================

class FishingCog(commands.Cog, name="Memancing"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.fishes = []
        self.fishing_items = {} 
        self._load_fishes()
        self._load_fishing_items()
        
        # Dictionary untuk menyimpan sesi auto-fishing
        # Format: {user_id: {'task': asyncio.Task, 'last_active': float}}
        self.autofish_sessions = {}

    def _load_fishes(self):
        try:
            path = os.path.join(os.path.dirname(__file__), '..', 'data', 'fishes.json')
            if not os.path.exists(path): path = 'data/fishes.json'
            with open(path, 'r', encoding='utf-8') as f: self.fishes = json.load(f)
        except Exception as e: print(f"Error loading fishes.json: {e}")

    def _load_fishing_items(self):
        try:
            path = os.path.join(os.path.dirname(__file__), '..', 'data', 'fishing_items.json')
            if not os.path.exists(path): path = 'data/fishing_items.json'
            with open(path, 'r', encoding='utf-8') as f:
                items_list = json.load(f)
                for item in items_list: self.fishing_items[item['id']] = item
        except Exception as e: print(f"Error loading fishing_items.json: {e}")

    def get_fish_data(self, fish_id):
        return next((f for f in self.fishes if f['id'] == fish_id), None)
    
    # --- LOGIKA RNG TERPUSAT (DIPERLUKAN UNTUK MANUAL DAN AUTO) ---
    def _calculate_catch_result(self, total_luck, is_auto=False):
        """
        Menghitung ikan yang didapat dengan tingkat kesulitan EKSTREM untuk Mitos/Godly.
        """
        # 1. Hitung Berat Awal Berdasarkan Luck
        
        # Common: Tetap ada tapi berkurang drastis seiring luck
        w_common = max(70.0, 90.0 - (total_luck * 0.8)) 
        
        # Rare & Epic: Menjadi tangkapan utama di level tinggi
        w_rare = 50.0 + (total_luck * 0.2)             
        w_epic = 6.0 + (total_luck * 0.1)              
        
        # Legendary: Mulai sulit
        w_legendary = 0.0
        if total_luck >= 150:
             w_legendary = 1.0 + ((total_luck - 150) * 0.005)
        
        # --- ZONA HARDCORE (MITOS & GODLY) ---
        
        # MITOS: Hampir Mustahil
        # Syarat: Butuh Luck 300++ (Hampir Max Gear)
        w_mitos = 0.0
        if total_luck >= 300: 
            # Penambahan peluang sangat tipis (0.002 per luck)
            # Contoh Luck 500: (500-300) * 0.002 = Berat 0.4 (Sangat kecil dibanding Common yg 100.0)
            w_mitos = (total_luck - 300) * 0.001

        # GODLY: Mustahil
        # Syarat: Butuh Luck 450++ (Harus pakai 'The Creator's Pen' + Charm Bagus)
        w_godly = 0.0
        if total_luck >= 450: 
            # Penambahan peluang mikroskopis (0.0005 per luck)
            # Contoh Luck 500: (500-450) * 0.0005 = Berat 0.025
            w_godly = (total_luck - 450) * 0.0005 

        # 2. LOGIKA NERF AUTO FISHING (Dipadukan jadi makin mustahil)
        if is_auto:
            w_common *= 2.0   # Auto lebih banyak sampah
            w_rare *= 0.5
            w_epic *= 0.1
            w_legendary *= 0.001
            w_mitos *= 0.0001 # Di auto hampir 0 absolut
            w_godly *= 0.00001

        # 3. Masukkan ke Dictionary
        weights_dict = {
            "Common": w_common,
            "Rare": w_rare,
            "Epic": w_epic,
            "Legendary": w_legendary,
            "Mitos": w_mitos,
            "Godly": w_godly
        }

        # 4. Pilih Rarity menggunakan Weighted Random
        # Kita menggunakan random.choices (Python 3.6+) yang mendukung float weights
        rarity_choice = random.choices(
            list(weights_dict.keys()), 
            weights=list(weights_dict.values()), 
            k=1
        )[0]

        # 5. Ambil ikan spesifik dari rarity terpilih
        potential_fishes = [f for f in self.fishes if f['rarity'] == rarity_choice]
        
        # Fallback jika list kosong (misal luck rendah, legendary belum terbuka)
        if not potential_fishes: 
            potential_fishes = [f for f in self.fishes if f['rarity'] == "Common"]
            
        return random.choice(potential_fishes)
    
    # --- LOGIKA AUTO FISHING ---
    async def start_auto_fishing_logic(self, ctx):
        """Memulai sesi auto fishing. Mengembalikan (Success: bool, Message: str)."""
        user_id = ctx.author.id
        if user_id in self.autofish_sessions:
            return False, "Auto fishing sudah berjalan!"

        player_data = await get_player_data(self.bot.db, user_id)
        if not player_data:
            return False, "Kamu belum terdaftar di database."

        # PENTING: Message ID panel utama akan disimpan saat 'fish_menu' dipanggil
        # Kita tidak bisa memulainya di sini, karena 'ctx' di sini berasal dari 'toggle_auto_fish'
        # dan bukan dari 'fish_menu'. Kita akan panggil kembali fish_menu untuk mendapatkan pesan.

        # Mulai Task
        self.autofish_sessions[user_id] = {
            'last_active': time.time(),
            'task': self.bot.loop.create_task(self._auto_fish_task(ctx))
        }
        return True, "Auto fishing dimulai."

    async def stop_auto_fishing_logic(self, user_id):
        """Menghentikan sesi auto fishing secara manual."""
        if user_id in self.autofish_sessions:
            try:
                self.autofish_sessions[user_id]['task'].cancel()
            except: pass
            # Penghapusan dari dict akan dilakukan di `CancelledError` task
            return True
        return False
    
    @commands.Cog.listener()
    async def on_command(self, ctx):
        """Reset timer idle saat player menggunakan command lain."""
        if ctx.author.id in self.autofish_sessions:
            self.autofish_sessions[ctx.author.id]['last_active'] = time.time()
        
    def _get_auto_visual_embed(self, state, context_data=None):
        """
        Membuat Embed visual untuk simulasi auto fishing.
        state: 'waiting', 'hooked', 'success'
        """
        # (Fungsi ini sama dengan yang Anda berikan, saya biarkan tetap di Cog)
        if state == 'waiting':
            return discord.Embed(
                title="🌊 Auto Fishing...",
                description="*Menunggu ikan menyambar umpan...*\n\n(Kamu bisa mengetik command lain sambil menunggu)",
                color=BotColors.FISH_WATER
            )
        
        elif state == 'hooked':
            # Simulasi tampilan panah mini-game
            arrows = context_data.get('arrows', "⬆️ ⬇️ ⬅️ ➡️")
            return discord.Embed(
                title="🎣 TARIK SEKUAT TENAGA! (AUTO)",
                description=(
                    f"# Mendapatkan Ikan: {arrows}\n\n"
                    f"*Bot sedang menarik pancinganmu...*"
                ),
                color=BotColors.FISH_HOOKED # Merah
            )
        
        elif state == 'success':
            fish = context_data.get('fish')
            user = context_data.get('user')
            
            # Tentukan warna berdasarkan rarity
            rarity_colors = {
                "Common": BotColors.COMMON, "Rare": BotColors.RARE, 
                "Epic": BotColors.EPIC, "Legendary": BotColors.LEGENDARY,
                "Mitos": BotColors.MYTHIC, "Godly": BotColors.ARTIFACT
            }
            color = rarity_colors.get(fish['rarity'], BotColors.SUCCESS)
            
            embed = discord.Embed(title="✨ TANGKAPAN BERHASIL! (AUTO)", color=color)
            embed.add_field(name="Hasil", value=f"{fish['emoji']} **{fish['name']}**")
            embed.add_field(name="Info", value=f"Rarity: `{fish['rarity']}` | Harga: `{fish['price']:,} 💎`")
            if user: embed.set_thumbnail(url=user.display_avatar.url)
            embed.set_footer(text="Memancing lagi dalam beberapa detik...")
            return embed

    async def _auto_fish_task(self, ctx):
        user_id = ctx.author.id
        
        # Setup Data Awal (Sama dengan yang Anda berikan)
        player_data = await get_player_data(self.bot.db, user_id)
        fishing_data = json.loads(player_data.get('fishing_data') or '{}')
        equipped = fishing_data.get('equipped', {'rod': 'rod_basic'})
        
        rod_item = self.fishing_items.get(equipped.get('rod'), {'luck': 0})
        charm_item = self.fishing_items.get(equipped.get('charm'), {'luck': 0})
        total_luck = rod_item.get('luck', 0) + charm_item.get('luck', 0)
        
        # Kirim pesan visual awal
        message = await ctx.send(embed=self._get_auto_visual_embed('waiting'))
        
        try:
            while user_id in self.autofish_sessions:
                
                # 1. CEK IDLE (120 Detik)
                if time.time() - self.autofish_sessions[user_id]['last_active'] > 120:
                    del self.autofish_sessions[user_id]
                    try: 
                        await message.edit(content=f"💤 {ctx.author.mention}, Auto Fishing berhenti karena idle (tidak ada command selama 120 detik).", embed=None, view=None)
                    except: 
                        pass
                    break # Keluar dari loop karena idle

                # 2. FASE MENUNGGU (Waiting)
                try: 
                    # Edit pesan menggunakan message ID jika context channel sama
                    await message.edit(embed=self._get_auto_visual_embed('waiting'), view=None)
                except discord.NotFound: 
                    message = await ctx.send(embed=self._get_auto_visual_embed('waiting'))
                except:
                    pass 
                
                # Tunggu ikan menyambar (3 - 5 detik)
                await asyncio.sleep(random.uniform(5.0, 7.0))
                if user_id not in self.autofish_sessions: break

                # 3. FASE TARIK (Hooked - ANIMASI SATU PER SATU)
                seq_len = random.randint(5, 7)
                full_sequence = random.choices(["⬆️", "⬇️", "⬅️", "➡️"], k=seq_len)
                
                current_display_arrows = ""
                for arrow in full_sequence:
                    if user_id not in self.autofish_sessions: break
                    current_display_arrows += arrow + " "
                    
                    try:
                        await message.edit(embed=self._get_auto_visual_embed('hooked', {'arrows': current_display_arrows}))
                    except: pass
                    
                    await asyncio.sleep(random.uniform(1.0, 1.5)) # Kecepatan bot

                if user_id not in self.autofish_sessions: break

                # 4. FASE HASIL (Success & RNG Nerf)
                caught_fish = self._calculate_catch_result(total_luck, is_auto=True)
                
                # Simpan ke Database
                curr_inv = await get_player_inventory(self.bot.db, user_id)
                curr_inv.append(caught_fish['id'])
                await update_player_data(self.bot.db, user_id, inventory=json.dumps(curr_inv))

                # Tampilkan Hasil
                try: await message.edit(embed=self._get_auto_visual_embed('success', {'fish': caught_fish, 'user': ctx.author}))
                except: pass

                await asyncio.sleep(4.5)

        except asyncio.CancelledError:
            # CLEANUP: Dijalankan ketika task dibatalkan (manual stop)
            if user_id in self.autofish_sessions: del self.autofish_sessions[user_id]
            try:
                await message.edit(content=f"🎣 {ctx.author.mention}, Auto Fishing Dimatikan.", embed=None, view=None)
            except:
                pass 

        except Exception as e:
            # CLEANUP: Dijalankan ketika terjadi error tak terduga (crash)
            print(f"Auto Fish Task Critical Error: {e}")
            traceback.print_exc()
            
            if user_id in self.autofish_sessions: del self.autofish_sessions[user_id]
            try:
                await message.edit(content=f"❌ {ctx.author.mention}, Auto Fishing berhenti karena Error tak terduga.", embed=None, view=None)
            except:
                pass

    # Command Text alternatif untuk Auto Fishing
    @commands.command(name="autofish")
    async def auto_fish_cmd(self, ctx, action: str = "start"):
        """Command Text alternatif untuk Auto Fishing."""
        if ctx.channel.id != ctx.author.id: # Tidak mengizinkan di DM
            await ctx.message.delete()
        
        if action.lower() == "start":
            success, msg = await self.start_auto_fishing_logic(ctx)
            await ctx.send(msg, delete_after=5)
        elif action.lower() == "stop":
            if await self.stop_auto_fishing_logic(ctx.author.id):
                await ctx.send("🛑 Auto fishing dimatikan.", delete_after=5)
            else:
                await ctx.send("Kamu tidak sedang auto fishing.", delete_after=5)
        else:
            await ctx.send("Perintah tidak valid. Gunakan `!autofish start` atau `!autofish stop`", delete_after=5)

    async def open_shop_ui(self, ctx, interaction, active_category='rod', current_view=None):
        """Helper untuk membuka UI Toko. (DIKEMBALIKAN KE VERSI DENGAN KATEGORI)"""
        player_data = await get_player_data(self.bot.db, ctx.author.id)
        current_prisma = player_data.get('prisma', 0)
        
        fishing_data = json.loads(player_data.get('fishing_data') or '{}')
        equipped = fishing_data.get('equipped', {})
        
        rod_id = equipped.get('rod', 'rod_basic')
        charm_id = equipped.get('charm')
        
        rod_item = self.fishing_items.get(rod_id, {'name': 'Unknown Rod'})
        charm_item = self.fishing_items.get(charm_id, {'name': '-'}) if charm_id else {'name': 'Tidak Ada'}
        
        category_name = "Peralatan Memancing (Joran)" if active_category == 'rod' else "Peralatan Memancing (Charm)"
        
        desc = (
            f"**💳 Budget Anda:** `{current_prisma:,} Prisma`\n\n"
            f"**🛠️ Sedang Dipakai:**\n"
            f"🎣 **Joran:** {rod_item['name']}\n"
            f"📿 **Charm:** {charm_item['name']}\n\n"
            f"**-- Kategori Aktif: {category_name} --**\n"
            "Gunakan tombol di atas untuk mengganti kategori. Pilih item di menu bawah untuk melihat detail & membeli:"
        )

        embed = discord.Embed(title="🏪 Toko Peralatan Pancing", description=desc, color=BotColors.DEFAULT)
        
        view = current_view if current_view else FishingShopView(ctx, self)
        
        await interaction.edit_original_response(embed=embed, view=view)

    def _create_bag_embed(self, user, inventory_list):
        embed = discord.Embed(
            title=f"👜 Tas Ikan - {user.display_name}", 
            description="Ikan yang ada di sini bisa **DIJUAL**.\nSimpan ke Aquarium jika ingin dikoleksi.", 
            color=BotColors.DEFAULT
        )
        
        # Hitung ikan
        fish_counts = Counter([i for i in inventory_list if self.get_fish_data(i)])
        
        if fish_counts:
            # Urutkan ikan berdasarkan jumlah terbanyak agar lebih rapi
            sorted_items = sorted(fish_counts.items(), key=lambda x: x[1], reverse=True)
            
            current_field_text = ""
            field_count = 0
            
            for fid, count in sorted_items:
                fish = self.get_fish_data(fid)
                # Buat baris teks untuk ikan ini
                line = f"{fish.get('emoji','🐟')} **{fish['name']}** x{count} `Harga: {fish['price']}`\n"
                
                # Cek apakah jika baris ini ditambahkan akan melebihi batas 1024 karakter?
                if len(current_field_text) + len(line) > 1000:
                    # Jika ya, tambahkan field yang sudah ada ke embed
                    field_name = "Isi Tas:" if field_count == 0 else "Isi Tas (Lanjutan):"
                    embed.add_field(name=field_name, value=current_field_text, inline=False)
                    
                    # Reset text untuk field berikutnya
                    current_field_text = ""
                    field_count += 1
                    
                    # Batasan total ukuran Embed (6000 char). Jika sudah terlalu banyak field, stop.
                    if field_count >= 5: 
                        current_field_text = "*...dan masih banyak lagi (Tas Penuh). Jual sebagian untuk melihat sisanya.*"
                        break
                
                # Tambahkan baris ke text saat ini
                current_field_text += line
            
            # Tambahkan sisa text yang belum masuk ke field
            if current_field_text:
                field_name = "Isi Tas:" if field_count == 0 else "Isi Tas (Lanjutan):"
                embed.add_field(name=field_name, value=current_field_text, inline=False)
                
        else:
            embed.description += "\n\n*Tas kosong...*"
            
        return embed

    def _create_aquarium_embed(self, user, aquarium_list):
        total_asset_value = 0
        fish_counts = Counter(aquarium_list)
        
        grouped_fish = {
            "Godly": [],
            "Mitos": [],
            "Legendary": [],
            "Epic": [],
            "Rare": [],
            "Common": []
        }
        
        for fid, count in fish_counts.items():
            fish = self.get_fish_data(fid)
            if fish:
                total_asset_value += (fish['price'] * count)
                if fish['rarity'] in grouped_fish:
                    grouped_fish[fish['rarity']].append((fish, count))

        embed = discord.Embed(
            title=f"🏛️ Royal Aquarium — {user.display_name}", 
            description="*\"Koleksi spesimen laut terbaik yang dikurasi dengan cita rasa tinggi.\"*",
            # Menggunakan warna Emas/Legendary untuk kemewahan
            color=BotColors.LEGENDARY
        )
        
        embed.set_thumbnail(url=user.display_avatar.url)

        stats_text = (
            f"💎 **Nilai Aset:** `{total_asset_value:,} Prisma`\n"
            f"🐟 **Total Spesimen:** `{len(aquarium_list)} Ekor`\n"
            f"🏆 **Top Tier:** `{len(grouped_fish['Godly']) + len(grouped_fish['Mitos'])} Godly/Mitos`"
        )
        embed.add_field(name="📊 Statistik Kurator", value=stats_text, inline=False)

        # --- GODLY ---
        if grouped_fish["Godly"]:
            lines = []
            for fish, count in grouped_fish["Godly"]:
                lines.append(f"✨ **{fish['name']}** `x{count}`")
            embed.add_field(name="♾️ THE DIVINE ENTITIES (Godly)", value=" > " + "\n> ".join(lines), inline=False)

        # --- MITOS ---
        if grouped_fish["Mitos"]:
            lines = []
            for fish, count in grouped_fish["Mitos"]:
                lines.append(f"🐉 **{fish['name']}** `x{count}`")
            embed.add_field(name="🔥 MYTHICAL BEASTS (Mitos)", value=" > " + "\n> ".join(lines), inline=False)

        # --- LEGENDARY ---
        if grouped_fish["Legendary"]:
            lines = []
            for fish, count in grouped_fish["Legendary"]:
                lines.append(f"🌟 **{fish['name']}** `x{count}`")
            embed.add_field(name="👑 THE CROWN JEWELS (Legendary)", value="\n".join(lines), inline=False)

        # --- EPIC ---
        if grouped_fish["Epic"]:
            lines = []
            for fish, count in grouped_fish["Epic"]:
                lines.append(f"{fish.get('emoji', '🟣')} **{fish['name']}** `x{count}`")
            embed.add_field(name="🟣 EXOTIC COLLECTION (Epic)", value="\n".join(lines), inline=True)

        # --- RARE ---
        if grouped_fish["Rare"]:
            lines = []
            for fish, count in grouped_fish["Rare"]:
                lines.append(f"{fish.get('emoji', '🔵')} {fish['name']} `x{count}`")
            if len(lines) > 5: lines = lines[:5] + [f"*...dan {len(lines)-5} lainnya.*"]
            embed.add_field(name="🔵 RARE FINDS", value="\n".join(lines), inline=True)

        # --- COMMON ---
        if grouped_fish["Common"]:
            total_common = sum(count for _, count in grouped_fish["Common"])
            unique_common = len(grouped_fish["Common"])
            embed.add_field(name="⚪ STANDARD TANK", value=f"Berisi **{total_common}** ikan umum dari **{unique_common}** spesies berbeda.", inline=False)

        if not aquarium_list:
            embed.description = "*Galeri ini masih kosong. Tangkap ikan langka dan simpan di sini untuk dipamerkan.*"
            # Warna Dark Grey / Offline
            embed.color = BotColors.STREAM_END

        embed.set_footer(text="🛡️ Ikan di dalam Aquarium aman dari penjualan tidak sengaja.")
        return embed

    async def _get_main_panel(self, ctx):
        player_data = await get_player_data(self.bot.db, ctx.author.id)
        
        fishing_data_raw = player_data.get('fishing_data')
        if not fishing_data_raw:
            default_data = {"inventory": ["rod_basic"], "equipped": {"rod": "rod_basic", "charm": None}, "aquarium": []}
            await update_player_data(self.bot.db, ctx.author.id, fishing_data=json.dumps(default_data))
            fishing_data = default_data
        else:
            fishing_data = json.loads(fishing_data_raw)

        equipped = fishing_data.get('equipped', {})
        rod_id = equipped.get('rod')
        charm_id = equipped.get('charm')

        rod_item = self.fishing_items.get(rod_id, self.fishing_items.get('rod_basic'))
        charm_item = self.fishing_items.get(charm_id)

        # Ambil Data Stat
        r_luck = rod_item.get('luck', 0)
        r_time = rod_item.get('time_bonus', 0.0)
        
        c_luck = charm_item.get('luck', 0) if charm_item else 0
        c_time = charm_item.get('time_bonus', 0.0) if charm_item else 0.0

        total_luck = r_luck + c_luck
        total_time = r_time + c_time

        rod_name = rod_item['name'] if rod_item else "Unknown Rod"
        charm_name = charm_item['name'] if charm_item else "Kosong"

        embed = discord.Embed(
            title="🎣 Danau Pemancingan",
            description=(
                f"**📊 Statistik Memancing:**\n"
                f"🍀 **Luck:** `{total_luck}` | ⏰ **Time Bonus:** `+{total_time:.1f}s`\n\n"
                f"**🛠️ Equipment Saat Ini:**\n"
                f"🎣 **Joran:** {rod_name}\n"
                f"📿 **Charm:** {charm_name}\n\n"
                "**Menu Utama:**\n"
                "🎣 **Mulai:** Mulai Memancing.\n"
                "🛒 **Toko:** Beli Joran & Charm baru.\n"
                "🎒 **Equipment:** Ganti alat pancing.\n"
                "👜 **Tas Ikan & Jual:** Lihat hasil untuk dijual atau simpan ke Aquarium.\n"
                "🐠 **Aquarium:** Koleksi aman (Anti-Jual)."
            ),
            color=BotColors.FISH_WATER
        )
        
        # Buat View Baru
        view = FishingPanelView(ctx, self)
        # PENTING: Update status tombol Auto Fish pada view baru
        view._update_auto_button_state() 
        return embed, view

    @commands.command(name="fish", aliases=["mancing"])
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def fish_menu(self, ctx: commands.Context):
        """Menampilkan panel memancing."""
        player_data = await get_player_data(self.bot.db, ctx.author.id)
        if not player_data.get('equipped_title_id'):
            return await ctx.send("Kamu harus debut dulu sebelum bisa memancing!")

        embed, view = await self._get_main_panel(ctx)
        msg = await ctx.send(embed=embed, view=view)
        view.message = msg

async def setup(bot: commands.Bot):
    await bot.add_cog(FishingCog(bot))