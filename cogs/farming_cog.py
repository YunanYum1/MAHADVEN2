import discord
from discord.ext import commands
import json
import os
import time
import datetime
import pytz 
import traceback
import random
from collections import Counter
from database import get_player_data, update_player_data, get_player_inventory
from ._utils import BotColors

# Zona Waktu WIB
WIB = pytz.timezone('Asia/Jakarta')

# Konfigurasi Upgrade
UPGRADE_BASE_COST = 5000
MAX_SLOTS_LIMIT = 12

# ===================================================================================
# VIEW: TOKO TANI (FARM SHOP)
# ===================================================================================
class FarmShopView(discord.ui.View):
    def __init__(self, user, cog, farm_data):
        super().__init__(timeout=180)
        self.user = user 
        self.cog = cog
        self.farm_data = farm_data
        self.build_menu()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user.id:
            await interaction.response.send_message("Ini bukan tokomu!", ephemeral=True)
            return False
        return True

    def build_menu(self):
        self.clear_items()
        options = []
        for plant_id, plant in self.cog.plants.items():
            label = f"{plant['name']} ({plant['price_buy']} 💎)"
            desc = f"⏱️ {int(plant['growth_time']/60)}m | ✨ Exp: {plant['xp_reward']}"
            options.append(discord.SelectOption(
                label=label, 
                value=plant_id, 
                description=desc,
                emoji=plant['emoji_stages'][-1]
            ))

        select = discord.ui.Select(placeholder="🛒 Beli Bibit...", options=options[:25], row=0)
        select.callback = self.buy_callback
        self.add_item(select)

        back_btn = discord.ui.Button(label="Kembali ke Ladang", style=discord.ButtonStyle.secondary, emoji="🏡", row=1)
        back_btn.callback = self.back_to_farm
        self.add_item(back_btn)

    async def buy_callback(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer()
            plant_id = interaction.data['values'][0]
            plant = self.cog.plants[plant_id]
            price = plant['price_buy']
            
            player_data = await get_player_data(self.cog.bot.db, self.user.id)
            if player_data.get('prisma', 0) < price:
                return await interaction.followup.send(f"❌ Prisma kurang! Butuh **{price} 💎**.", ephemeral=True)

            inventory = await get_player_inventory(self.cog.bot.db, self.user.id)
            new_prisma = player_data['prisma'] - price
            inventory.append(plant_id)
            
            await update_player_data(self.cog.bot.db, self.user.id, prisma=new_prisma, inventory=json.dumps(inventory))
            
            embed = self.cog.create_shop_embed(self.user, new_prisma)
            await interaction.edit_original_response(embed=embed, view=self)
            await interaction.followup.send(f"✅ Membeli **{plant['name']}**.", ephemeral=True)
        except Exception as e:
            print(f"Error Buy: {e}")
            await interaction.followup.send("Terjadi kesalahan sistem.", ephemeral=True)

    async def back_to_farm(self, interaction: discord.Interaction):
        await interaction.response.defer()
        await self.cog.refresh_farm_ui(interaction)

# ===================================================================================
# VIEW: UTAMA LADANG
# ===================================================================================
class FarmingView(discord.ui.View):
    def __init__(self, user, cog, farm_data, inventory):
        super().__init__(timeout=300) 
        self.user = user 
        self.cog = cog
        self.farm_data = farm_data
        self.inventory = inventory if inventory is not None else []
        self.update_components()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user.id:
            await interaction.response.send_message("Ini ladang milik orang lain!", ephemeral=True)
            return False
        return True

    def update_components(self):
        self.clear_items()
        
        # --- ROW 0: DROPDOWN TANAM ---
        owned_seeds = [item for item in self.inventory if item in self.cog.plants]
        seed_counts = Counter(owned_seeds)
        
        plant_options = []
        for seed_id, count in seed_counts.items():
            plant_info = self.cog.plants[seed_id]
            plant_options.append(discord.SelectOption(
                label=f"{plant_info['name']} (x{count})",
                value=seed_id,
                description=f"Panen: {int(plant_info['growth_time']/60)} mnt",
                emoji="🌱"
            ))
        
        if plant_options:
            select_plant = discord.ui.Select(placeholder="🌱 Pilih bibit untuk ditanam...", options=plant_options[:25], row=0, custom_id="select_plant")
            select_plant.callback = self.plant_seed_callback
            self.add_item(select_plant)
        else:
            select_plant = discord.ui.Select(placeholder="❌ Tas bibit kosong", options=[discord.SelectOption(label="Kosong", value="empty")], disabled=True, row=0)
            self.add_item(select_plant)

        # --- ROW 1: ACTION BUTTONS ---
        btn_water = discord.ui.Button(label="Siram", style=discord.ButtonStyle.primary, emoji="💧", custom_id="water_all", row=1)
        btn_water.callback = self.general_action_callback
        self.add_item(btn_water)
        
        has_ready = any(self.cog.check_is_ready(slot) for slot in self.farm_data['slots'])
        btn_style = discord.ButtonStyle.success if has_ready else discord.ButtonStyle.secondary
        
        btn_harvest = discord.ui.Button(label="Panen", style=btn_style, emoji="🌾", disabled=not has_ready, custom_id="harvest", row=1)
        btn_harvest.callback = self.general_action_callback
        self.add_item(btn_harvest)
        
        btn_refresh = discord.ui.Button(label="Refresh", style=discord.ButtonStyle.secondary, emoji="🔄", custom_id="refresh", row=1)
        btn_refresh.callback = self.general_action_callback
        self.add_item(btn_refresh)

        # --- ROW 2: MANAGEMENT BUTTONS ---
        btn_shop = discord.ui.Button(label="Toko Tani", style=discord.ButtonStyle.secondary, emoji="🛒", custom_id="open_shop", row=2)
        btn_shop.callback = self.general_action_callback
        self.add_item(btn_shop)
        
        current_slots = self.farm_data.get('max_slots', 6)
        if current_slots < MAX_SLOTS_LIMIT:
            # --- RUMUS HARGA BARU ---
            # Slot 6 (awal) -> Upgrade ke 7: (6-5) * 5000 = 5000
            # Slot 7 -> Upgrade ke 8: (7-5) * 5000 = 10000
            multiplier = max(1, current_slots - 5)
            cost = multiplier * UPGRADE_BASE_COST
            
            btn_upgrade = discord.ui.Button(
                label=f"+1 Petak ({cost}💎)", 
                style=discord.ButtonStyle.primary, 
                emoji="🔨", 
                custom_id="upgrade", 
                row=2
            )
            btn_upgrade.callback = self.general_action_callback
            self.add_item(btn_upgrade)
        else:
            btn_max = discord.ui.Button(label="Max Slot", style=discord.ButtonStyle.secondary, disabled=True, row=2)
            self.add_item(btn_max)

    # --- CALLBACKS ---

    async def plant_seed_callback(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer()
            seed_id = interaction.data['values'][0]
            
            empty_idx = -1
            limit = self.farm_data.get('max_slots', 6)
            
            for i in range(limit):
                if i < len(self.farm_data['slots']):
                    if self.farm_data['slots'][i]['status'] == 'empty':
                        empty_idx = i
                        break
            
            if empty_idx == -1:
                return await interaction.followup.send("❌ Ladang penuh! Panen dulu atau Beli Petak Baru.", ephemeral=True)

            current_inv = await get_player_inventory(self.cog.bot.db, self.user.id)
            if seed_id in current_inv:
                current_inv.remove(seed_id)
                self.farm_data['slots'][empty_idx] = {
                    "status": "growing",
                    "plant_id": seed_id,
                    "planted_at": time.time(),
                    "last_watered": time.time(),
                    "accumulated_growth": 0
                }
                await update_player_data(self.cog.bot.db, self.user.id, inventory=json.dumps(current_inv), farm_data=json.dumps(self.farm_data))
                await self.cog.refresh_farm_ui(interaction, message=f"🌱 Menanam **{self.cog.plants[seed_id]['name']}** di Petak #{empty_idx+1}.")
            else:
                await interaction.followup.send("❌ Bibit tidak ditemukan.", ephemeral=True)
        except Exception as e:
            print(f"Error Planting: {e}")
            traceback.print_exc()
            await interaction.followup.send("Terjadi kesalahan saat menanam.", ephemeral=True)

    async def general_action_callback(self, interaction: discord.Interaction):
        try:
            cid = interaction.data['custom_id']
            
            if cid == "water_all":
                await self.cog.water_all(interaction)
            elif cid == "harvest":
                await self.cog.harvest_all(interaction)
            elif cid == "refresh":
                await self.cog.refresh_farm_ui(interaction)
            elif cid == "open_shop":
                await interaction.response.defer()
                p_data = await get_player_data(self.cog.bot.db, self.user.id)
                embed = self.cog.create_shop_embed(self.user, p_data.get('prisma', 0))
                view = FarmShopView(self.user, self.cog, self.farm_data)
                await interaction.edit_original_response(embed=embed, view=view)
            elif cid == "upgrade":
                await self.cog.upgrade_farm(interaction)
        except Exception as e:
            print(f"Error General Action: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message("Gagal memproses aksi.", ephemeral=True)

# ===================================================================================
# COG UTAMA
# ===================================================================================
class FarmingCog(commands.Cog, name="Pertanian"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.plants = {}
        self._load_plants()

    def _load_plants(self):
        try:
            path = os.path.join(os.path.dirname(__file__), '..', 'data', 'plants.json')
            if not os.path.exists(path): path = 'data/plants.json'
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                self.plants = {p['id']: p for p in data}
        except Exception as e: print(f"Error plants.json: {e}")

    # --- SYSTEM: CUACA & WAKTU ---
    def get_weather(self):
        now = datetime.datetime.now(WIB)
        hour = now.hour
        weather_seed = (now.day * 100) + hour
        random.seed(weather_seed)
        chance = random.random()
        
        status = "Cerah ☀️"
        is_raining = False
        
        if 14 <= hour <= 20: 
            if chance > 0.4: status, is_raining = "Hujan 🌧️", True
        elif 0 <= hour <= 5: 
            if chance > 0.7: status, is_raining = "Gerimis 🌦️", True
        else: 
            if chance > 0.85: status, is_raining = "Hujan 🌧️", True
                
        return status, is_raining

    # --- LOGIC PERHITUNGAN ---
    def get_display_growth(self, slot):
        if slot['status'] == 'empty': return 0
        plant = self.plants.get(slot['plant_id'])
        if not plant: return 0

        now = time.time()
        time_since_water = now - slot['last_watered']
        added_growth = min(time_since_water, plant['water_interval'])
        total = slot['accumulated_growth'] + added_growth
        return min(total, plant['growth_time'])

    def is_dry(self, slot):
        if slot['status'] == 'empty': return False
        plant = self.plants.get(slot['plant_id'])
        if not plant: return False
        
        _, is_raining = self.get_weather()
        if is_raining: return False
        
        return (time.time() - slot['last_watered']) > plant['water_interval']

    def check_is_ready(self, slot):
        if slot['status'] == 'empty': return False
        plant = self.plants.get(slot['plant_id'])
        if not plant: return False
        growth = self.get_display_growth(slot)
        return growth >= plant['growth_time']

    # --- ACTIONS ---
    @commands.command(name="farm", aliases=["ladang", "kebun", "plant", "tanam"])
    async def farm_menu(self, ctx):
        player_data = await get_player_data(self.bot.db, ctx.author.id)
        if not player_data: return await ctx.send("Daftar dulu dengan `!debut`!")

        farm_data = json.loads(player_data.get('farm_data') or '{}')
        if 'max_slots' not in farm_data: farm_data['max_slots'] = 6
        if 'slots' not in farm_data: farm_data['slots'] = []
        while len(farm_data['slots']) < farm_data['max_slots']:
             farm_data['slots'].append({"status": "empty", "plant_id": None, "planted_at": 0, "last_watered": 0, "accumulated_growth": 0})
        
        await update_player_data(self.bot.db, ctx.author.id, farm_data=json.dumps(farm_data))
        
        inventory = await get_player_inventory(self.bot.db, ctx.author.id)
        
        embed = self.create_farm_embed(ctx.author, farm_data)
        view = FarmingView(ctx.author, self, farm_data, inventory)
        await ctx.send(embed=embed, view=view)

    async def upgrade_farm(self, interaction):
        await interaction.response.defer()
        player_data = await get_player_data(self.bot.db, interaction.user.id)
        farm_data = json.loads(player_data.get('farm_data'))
        
        current_slots = farm_data.get('max_slots', 6)
        if current_slots >= MAX_SLOTS_LIMIT:
            return await interaction.followup.send("Ladang sudah level maksimal!", ephemeral=True)
            
        # --- RUMUS HARGA UPGRADE PER 1 SLOT ---
        # Slot ke-7 (saat ini 6) -> (6 - 5) * 5000 = 5000
        # Slot ke-8 (saat ini 7) -> (7 - 5) * 5000 = 10000
        multiplier = max(1, current_slots - 5)
        cost = multiplier * UPGRADE_BASE_COST
        
        if player_data.get('prisma', 0) < cost:
            return await interaction.followup.send(f"❌ Uang kurang! Butuh **{cost} 💎** untuk menambah 1 petak.", ephemeral=True)
            
        new_prisma = player_data['prisma'] - cost
        farm_data['max_slots'] += 1
        farm_data['slots'].append({"status": "empty", "plant_id": None, "planted_at": 0, "last_watered": 0, "accumulated_growth": 0})
        
        await update_player_data(self.bot.db, interaction.user.id, prisma=new_prisma, farm_data=json.dumps(farm_data))
        await self.refresh_farm_ui(interaction, message=f"🔨 Ladang diperluas! Slot sekarang: **{farm_data['max_slots']}**.")

    async def water_all(self, interaction):
        await interaction.response.defer()
        user_id = interaction.user.id
        player_data = await get_player_data(self.bot.db, user_id)
        farm_data = json.loads(player_data.get('farm_data'))

        watered_count = 0
        now = time.time()

        for slot in farm_data['slots']:
            if slot['status'] != 'empty':
                plant = self.plants.get(slot['plant_id'])
                if not plant: continue

                time_diff = now - slot['last_watered']
                valid_growth = min(time_diff, plant['water_interval'])
                
                if slot['accumulated_growth'] < plant['growth_time']:
                     slot['accumulated_growth'] += valid_growth
                
                slot['last_watered'] = now
                watered_count += 1
        
        if watered_count > 0:
            await update_player_data(self.bot.db, user_id, farm_data=json.dumps(farm_data))
            await self.refresh_farm_ui(interaction, message=f"💧 {watered_count} tanaman disiram!")
        else:
            await interaction.followup.send("Tidak ada tanaman.", ephemeral=True)

    async def harvest_all(self, interaction):
        await interaction.response.defer()
        user_id = interaction.user.id
        player_data = await get_player_data(self.bot.db, user_id)
        farm_data = json.loads(player_data.get('farm_data'))
        inventory = await get_player_inventory(self.bot.db, user_id)

        harvested = []
        total_xp = 0

        for i, slot in enumerate(farm_data['slots']):
            if slot['status'] != 'empty':
                if self.check_is_ready(slot):
                    plant = self.plants.get(slot['plant_id'])
                    crop_id = slot['plant_id'].replace("seed_", "")
                    
                    inventory.append(crop_id)
                    harvested.append(f"{plant['name']}")
                    total_xp += plant['xp_reward']
                    farm_data['slots'][i] = {"status": "empty", "plant_id": None, "planted_at": 0, "last_watered": 0, "accumulated_growth": 0}

        if harvested:
            new_exp = player_data['exp'] + total_xp
            await update_player_data(self.bot.db, user_id, farm_data=json.dumps(farm_data), inventory=json.dumps(inventory), exp=new_exp)
            
            counts = Counter(harvested)
            display_list = [f"{name} x{cnt}" for name, cnt in counts.items()]
            await self.refresh_farm_ui(interaction, message=f"🌾 Panen: **{', '.join(display_list)}** | XP +{total_xp}")
        else:
            await interaction.followup.send("Belum ada yang siap panen.", ephemeral=True)

    async def refresh_farm_ui(self, interaction, message=None):
        try:
            player_data = await get_player_data(self.bot.db, interaction.user.id)
            farm_data = json.loads(player_data.get('farm_data'))
            inventory = await get_player_inventory(self.bot.db, interaction.user.id)
            
            embed = self.create_farm_embed(interaction.user, farm_data)
            view = FarmingView(interaction.user, self, farm_data, inventory)
            
            if interaction.response.is_done():
                await interaction.edit_original_response(embed=embed, view=view)
            else:
                await interaction.response.edit_message(embed=embed, view=view)
                
            if message: 
                await interaction.followup.send(message, ephemeral=True)
        except Exception as e:
            print(f"Refresh Error: {e}")
            traceback.print_exc()

    # --- VISUAL (EMBED BUILDER) ---
    def create_farm_embed(self, user, farm_data):
        now_wib = datetime.datetime.now(WIB)
        time_str = now_wib.strftime("%H:%M")
        weather_status, is_raining = self.get_weather()
        weather_icon = "🌧️" if is_raining else "☀️"
        
        desc = (
            f"> 🕒 **Waktu:** `{time_str} WIB`\n"
            f"> {weather_icon} **Cuaca:** `{weather_status}`\n"
            f"> 🚜 **Luas Tanah:** `{len(farm_data['slots'])} Petak`\n\n"
            f"*{'🌧️ Hujan turun! Tanah otomatis basah.' if is_raining else '☀️ Cuaca cerah, jangan lupa menyiram tanaman!' }*"
        )
        
        embed = discord.Embed(title=f"🏡 Ladang Milik {user.display_name}", description=desc, color=BotColors.SUCCESS)
        
        grid_visual = ""
        slots = farm_data['slots']
        display_limit = 12
        
        for i in range(0, min(len(slots), display_limit), 3):
            row_nums = ""
            row_icons = ""
            for j in range(3):
                idx = i + j
                if idx >= len(slots): break
                
                slot = slots[idx]
                icon = "🟫"
                if slot['status'] != 'empty':
                    plant = self.plants.get(slot['plant_id'])
                    if plant:
                        current_growth = self.get_display_growth(slot)
                        max_growth = plant['growth_time']
                        percent = min(100, int((current_growth / max_growth) * 100))
                        
                        if percent >= 100: icon = "✨" 
                        else:
                            stage = 0
                            if percent >= 50: stage = 1
                            icon = plant['emoji_stages'][min(stage, len(plant['emoji_stages'])-2)]
                
                row_nums += f"`[{idx+1:02}]` "
                row_icons += f" {icon}   "
                
            grid_visual += f"{row_nums}\n{row_icons}\n"

        if len(slots) > display_limit:
            grid_visual += f"\n*...dan {len(slots) - display_limit} petak lainnya.*"

        embed.add_field(name="🗺️ Peta Ladang", value=grid_visual, inline=False)
        
        active_plants_text = ""
        active_count = 0
        
        for i, slot in enumerate(slots):
            if slot['status'] != 'empty':
                plant = self.plants.get(slot['plant_id'])
                if not plant: continue
                
                active_count += 1
                current_growth = self.get_display_growth(slot)
                max_growth = plant['growth_time']
                percent = min(100, int((current_growth / max_growth) * 100))
                
                is_dry = self.is_dry(slot)
                if is_raining: is_dry = False
                
                bar_len = 6
                filled = int((percent / 100) * bar_len)
                bar = "🟩" * filled + "⬛" * (bar_len - filled)
                
                status_txt = "🌵 **KERING**" if is_dry else "💧 **BASAH**"
                if percent >= 100: status_txt = "✅ **SIAP PANEN**"
                
                active_plants_text += f"`{i+1}.` **{plant['name']}**\n{bar} `{percent}%` | {status_txt}\n"

        if active_count == 0:
            active_plants_text = "*Belum ada tanaman. Pilih bibit di menu bawah untuk menanam!*"
        
        embed.add_field(name="📊 Status Tanaman", value=active_plants_text, inline=False)
        return embed

    def create_shop_embed(self, user, prisma):
        embed = discord.Embed(
            title="🛒 Toko Tani (Farm Shop)",
            description=f"Saldo: `{prisma:,} 💎`\nBeli bibit berkualitas untuk ladangmu.",
            color=BotColors.DEFAULT
        )
        list_str = ""
        for pid, p in self.plants.items():
            growth_min = int(p['growth_time']/60)
            list_str += f"• **{p['name']}**: `{p['price_buy']}💎` ({growth_min}m)\n"
        
        embed.add_field(name="📋 Katalog Bibit", value=list_str, inline=False)
        return embed

async def setup(bot: commands.Bot):
    await bot.add_cog(FarmingCog(bot))