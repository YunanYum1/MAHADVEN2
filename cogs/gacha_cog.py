import discord
from discord.ext import commands
import random
from collections import defaultdict
import asyncio
import os
import json
from typing import Dict, Any

# [PERBAIKAN] Impor fungsi baru untuk menyimpan artefak
from database import get_player_data, update_player_data, add_title_to_player, get_player_titles, add_artifact_to_player
from ._utils import BotColors

# --- KONFIGURASI GACHA BARU ---
GACHA_COST_SINGLE = 300
GACHA_COST_MULTI = 3000
PITY_THRESHOLD_LEGENDARY = 90 # Jaminan Legendary setelah 90 tarikan

RARITY_RATES = {
    "Common": 0.68,    # NAIK dari 0.65
    "Rare": 0.245,     # TURUN dari 0.25
    "Epic": 0.05,      # TURUN drastis dari 0.10
    "Legendary": 0.025 # TURUN setengahnya dari 0.05 (Lebih Sulit)
}
DUPLICATE_COMPENSATION = {
    "Common": 30,     
    "Rare": 50,       
    "Epic": 150,     
    "Legendary": 300   
}
RARITY_VISUALS = {
    "Common": {"color": BotColors.COMMON, "emoji": "⬜"},
    "Rare": {"color": BotColors.RARE, "emoji": "🟦"},
    "Epic": {"color": BotColors.EPIC, "emoji": "🟪"},
    "Legendary": {"color": BotColors.LEGENDARY, "emoji": "🟨"}
}
BANNER_IMAGES = {
    "titles": "assets/banners/gacha_banner_titles.png",
    "artifacts": "assets/banners/gacha_banner_artifacts.png"
}

# ===================================================================================
# --- KELAS-KELAS VIEW (UI INTERAKTIF) ---
# ===================================================================================

class GachaBannerView(discord.ui.View):
    """View untuk berinteraksi dengan banner gacha yang spesifik (Title atau Artefak)."""
    def __init__(self, author: discord.User, cog: commands.Cog, banner_type: str):
        super().__init__(timeout=300)
        self.author = author
        self.cog = cog
        self.banner_type = banner_type
        self.message: discord.Message = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("Ini bukan sesi gacha milikmu!", ephemeral=True)
            return False
        return True

    def disable_all_buttons(self):
        for item in self.children:
            item.disabled = True

    @discord.ui.button(label=f"Tarik 1x ({GACHA_COST_SINGLE} Prisma)", style=discord.ButtonStyle.primary, emoji="✨", row=0)
    async def pull_single(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.disable_all_buttons()
        await interaction.response.edit_message(view=self)
        await self.cog.execute_pulls(interaction, self, 1)

    @discord.ui.button(label=f"Tarik 10x ({GACHA_COST_MULTI} Prisma)", style=discord.ButtonStyle.success, emoji="🌟", row=0)
    async def pull_multi(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.disable_all_buttons()
        await interaction.response.edit_message(view=self)
        await self.cog.execute_pulls(interaction, self, 10)

    @discord.ui.button(label="Kembali", style=discord.ButtonStyle.secondary, emoji="⬅️", row=1)
    async def go_back(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        main_view = GachaMainView(self.author, self.cog)
        embed = await self.cog.create_main_embed(self.author)
        await interaction.edit_original_response(embed=embed, view=main_view, attachments=[])
        main_view.message = self.message

class GachaMainView(discord.ui.View):
    """View utama untuk memilih banner gacha."""
    def __init__(self, author: discord.User, cog: commands.Cog):
        super().__init__(timeout=300)
        self.author = author
        self.cog = cog
        self.message: discord.Message = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("Ini bukan sesi gacha milikmu!", ephemeral=True)
            return False
        return True
    
    async def on_timeout(self):
        if self.message:
            for item in self.children: item.disabled = True
            try:
                await self.message.edit(view=self)
            except discord.NotFound:
                pass

    @discord.ui.button(label="Gacha Title", style=discord.ButtonStyle.primary, emoji="👑", row=0)
    async def select_title_banner(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.show_banner(interaction, "titles")

    @discord.ui.button(label="Gacha Artefak", style=discord.ButtonStyle.primary, emoji="🔮", row=0)
    async def select_artifact_banner(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.show_banner(interaction, "artifacts")

# ===================================================================================
# --- COG UTAMA ---
# ===================================================================================

class GachaCog(commands.Cog, name="Gacha"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.pools = { "titles": defaultdict(list), "artifacts": defaultdict(list) }
        self.rarities = list(RARITY_RATES.keys())
        self.weights = list(RARITY_RATES.values())

        for title in self.bot.titles:
            self.pools["titles"][title.get('rarity')].append(title)
        for artifact in self.bot.artifacts:
            self.pools["artifacts"][artifact.get('rarity')].append(artifact)

    async def create_main_embed(self, user: discord.User) -> discord.Embed:
        player_data = await get_player_data(self.bot.db, user.id)
        embed = discord.Embed(title="Pusat Gacha MAHADVEN", description="Pilih banner di bawah untuk menggunakan Prismamu!", color=BotColors.DEFAULT)
        embed.set_author(name=user.display_name, icon_url=user.display_avatar.url)
        embed.add_field(name="👑 Banner Title", value=f"Pity Legendary: `{player_data.get('title_pity', 0)}/{PITY_THRESHOLD_LEGENDARY}`", inline=True)
        embed.add_field(name="🔮 Banner Artefak", value=f"Pity Legendary: `{player_data.get('artifact_pity', 0)}/{PITY_THRESHOLD_LEGENDARY}`", inline=True)
        embed.set_footer(text=f"Prismamu saat ini: {player_data.get('prisma', 0):,} 💎")
        return embed

    async def create_banner_embed(self, user: discord.User, banner_type: str) -> discord.Embed:
        player_data = await get_player_data(self.bot.db, user.id)
        pity_key = f"{banner_type.rstrip('s')}_pity"
        banner_name = "Title" if banner_type == "titles" else "Artefak"
        embed = discord.Embed(title=f"Banner Gacha - {banner_name}", description="Gunakan tombol di bawah untuk melakukan tarikan.\nJaminan 1 item Epic atau lebih tinggi setiap 10 tarikan!", color=BotColors.RARE)
        embed.set_footer(text=f"Pity Legendary: {player_data.get(pity_key, 0)}/{PITY_THRESHOLD_LEGENDARY} | Prismamu: {player_data.get('prisma', 0):,} 💎")
        return embed

    def _perform_pull(self, banner_type: str, force_rarity: str = None) -> Dict[str, Any]:
        """Melakukan satu tarikan gacha dan mengembalikan data item."""
        rarity_pool = self.pools[banner_type]
        if force_rarity:
            chosen_rarity = force_rarity
        else:
            chosen_rarity = random.choices(self.rarities, weights=self.weights, k=1)[0]
        
        pool = rarity_pool.get(chosen_rarity, [])
        if not pool:
            # Fallback aman: jika pool rarity yang dipilih kosong, ambil dari Common.
            pool = rarity_pool.get("Common", [])
            # Jika Common juga kosong, kembalikan item error agar bot tidak crash.
            if not pool:
                return {"id": 0, "name": "Error Item (Pool Kosong)", "rarity": "Common"}
        
        return random.choice(pool)

    async def execute_pulls(self, interaction: discord.Interaction, view: GachaBannerView, amount: int):
        cost = GACHA_COST_MULTI if amount == 10 else GACHA_COST_SINGLE
        player_data = await get_player_data(self.bot.db, interaction.user.id)
        
        if player_data.get('prisma', 0) < cost:
            await interaction.followup.send(f"Prismamu tidak cukup! Butuh {cost}, kamu punya {player_data.get('prisma', 0)}.", ephemeral=True)
            for item in view.children: item.disabled = False
            await interaction.edit_original_response(view=view)
            return

        pity_key = f"{view.banner_type.rstrip('s')}_pity"
        current_pity = player_data.get(pity_key, 0)
        owned_title_ids = set(await get_player_titles(self.bot.db, interaction.user.id))

        results = []
        has_high_rarity_in_multi = False
        reset_pity = False
        for i in range(amount):
            current_pity += 1
            pull = self._perform_pull(view.banner_type, "Legendary") if current_pity >= PITY_THRESHOLD_LEGENDARY else self._perform_pull(view.banner_type)
            # [PERBAIKAN] Gunakan .get() untuk akses yang aman
            if pull.get('rarity') == "Legendary": reset_pity = True
            if pull.get('rarity') in ["Epic", "Legendary"]: has_high_rarity_in_multi = True
            results.append(pull)

        if amount == 10 and not has_high_rarity_in_multi:
            results[-1] = self._perform_pull(view.banner_type, force_rarity="Epic")

        processed_results, total_compensation = [], 0
        newly_acquired_title_ids, newly_acquired_artifact_ids = [], []

        for item in results:
            is_duplicate = False
            item_id = item.get('id')
            if item_id is None: continue # Lewati item yang rusak/error

            if view.banner_type == "titles":
                if item_id in owned_title_ids:
                    is_duplicate = True
                    total_compensation += DUPLICATE_COMPENSATION.get(item.get('rarity', 'Common'), 0)
                else:
                    newly_acquired_title_ids.append(item_id)
                    owned_title_ids.add(item_id)
            elif view.banner_type == "artifacts":
                newly_acquired_artifact_ids.append(item_id)
            
            processed_results.append({"item": item, "is_duplicate": is_duplicate})
        
        # [IMPLEMENTASI BARU] Terapkan bonus/malus Prisma dari Agensi ke kompensasi
        agency_id = player_data.get('agency_id')
        if agency_id == "mahavirtual":
            total_compensation = int(total_compensation * 0.85)
        elif agency_id == "prism_project":
            total_compensation = int(total_compensation * 1.20)
        
        final_prisma_total = player_data.get('prisma', 0) - cost + total_compensation
        await self._run_pull_animation(interaction, processed_results, final_prisma_total, total_compensation, view.banner_type)

        if view.banner_type == "titles":
            for title_id in newly_acquired_title_ids: await add_title_to_player(self.bot.db, interaction.user.id, title_id)
        elif view.banner_type == "artifacts":
            for artifact_id in newly_acquired_artifact_ids: await add_artifact_to_player(self.bot.db, interaction.user.id, artifact_id)
        
        new_pity = 0 if reset_pity else current_pity
        await update_player_data(self.bot.db, interaction.user.id, **{pity_key: new_pity, 'prisma': final_prisma_total})
        
        await asyncio.sleep(3)
        await self.show_banner(interaction, view.banner_type, is_refresh=True)

    # [PERBAIKAN] Fungsi format dibuat lebih aman dari data yang hilang
    def _format_item_line(self, item: Dict[str, Any], is_duplicate: bool) -> str:
        """Helper untuk memformat satu baris hasil item dengan aman."""
        rarity = item.get('rarity', 'Common')
        name = item.get('name', 'Unknown Item')
        
        visuals = RARITY_VISUALS.get(rarity, RARITY_VISUALS['Common'])
        is_highlight = rarity in ['Epic', 'Legendary']
        duplicate_marker = " `(Duplikat)`" if is_duplicate else ""
        
        return f"{'**' if is_highlight else ''}{visuals['emoji']} {name} `({rarity})`{duplicate_marker}{'**' if is_highlight else ''}"

    def _create_summary_embed(self, user: discord.User, processed_results: list, new_prisma_total: int, total_compensation: int, banner_type: str) -> discord.Embed:
        rarity_order = ["Legendary", "Epic", "Rare", "Common"]
        # [PERBAIKAN] Pengurutan dan pengambilan data dibuat lebih aman
        processed_results.sort(key=lambda r: rarity_order.index(r['item'].get('rarity', 'Common')))
        description_lines = [self._format_item_line(r['item'], r['is_duplicate']) for r in processed_results]
        top_item_rarity = processed_results[0]['item'].get('rarity', 'Common')
        embed_color = RARITY_VISUALS.get(top_item_rarity, RARITY_VISUALS['Common'])['color']
        
        banner_name = "Title" if banner_type == "titles" else "Artefak"
        embed = discord.Embed(title=f"Hasil Gacha {banner_name} {len(processed_results)}x", description="\n".join(description_lines), color=embed_color)
        embed.set_author(name=user.display_name, icon_url=user.display_avatar.url)
        if total_compensation > 0:
            embed.add_field(name="💎 Kompensasi Duplikat", value=f"Anda mendapatkan kembali **+{total_compensation:,}** Prisma!", inline=False)
        embed.set_footer(text=f"Sisa Prisma: {new_prisma_total:,} | Akan kembali dalam 3 detik...")
        return embed

    async def _run_pull_animation(self, interaction: discord.Interaction, processed_results: list, new_prisma_total: int, total_compensation: int, banner_type: str):
        amount = len(processed_results)
        user = interaction.user
        
        if amount == 1:
            result_data = processed_results[0] if processed_results else {"item": {}, "is_duplicate": False}
            item, is_duplicate = result_data['item'], result_data['is_duplicate']
            visuals = RARITY_VISUALS.get(item.get('rarity', 'Common'))
            
            start_embed = discord.Embed(title="✨ Menarik...", description="Semoga beruntung!", color=BotColors.DEFAULT)
            await interaction.edit_original_response(embed=start_embed, view=None, attachments=[])
            await asyncio.sleep(2.0)
            
            result_embed = discord.Embed(title="Kamu Mendapatkan:", description=self._format_item_line(item, is_duplicate), color=visuals['color'])
            if is_duplicate:
                compensation = DUPLICATE_COMPENSATION.get(item.get('rarity', 'Common'), 0)
                
                # [IMPLEMENTASI BARU] Terapkan bonus agensi pada pesan hasil single pull
                player_data = await get_player_data(self.bot.db, interaction.user.id)
                agency_id = player_data.get('agency_id')
                if agency_id == "mahavirtual": compensation = int(compensation * 0.85)
                elif agency_id == "prism_project": compensation = int(compensation * 1.20)
                
                result_embed.add_field(name="💎 Kompensasi Duplikat", value=f"+{compensation:,} Prisma")
            
            result_embed.set_author(name=user.display_name, icon_url=user.display_avatar.url)
            result_embed.set_footer(text=f"Sisa Prisma: {new_prisma_total:,} | Akan kembali dalam 3 detik...")
            await interaction.edit_original_response(embed=result_embed)
            return

        placeholders = ["❓ `(Belum terungkap)`"] * amount
        start_embed = discord.Embed(title=f"🌟 Menarik {amount}x...", description="\n".join(placeholders), color=BotColors.DEFAULT).set_author(name=user.display_name, icon_url=user.display_avatar.url)
        await interaction.edit_original_response(embed=start_embed, view=None, attachments=[])
        await asyncio.sleep(1.5)

        revealed_lines = []
        for i, result_data in enumerate(processed_results):
            item = result_data['item']
            visuals = RARITY_VISUALS.get(item.get('rarity', 'Common'))
            revealed_lines.append(self._format_item_line(item, result_data['is_duplicate']))
            current_display = revealed_lines + placeholders[i+1:]
            
            animation_embed = discord.Embed(title=f"🌟 Mengungkap Hasil... ({i+1}/{amount})", description="\n".join(current_display), color=visuals['color']).set_author(name=user.display_name, icon_url=user.display_avatar.url)
            await interaction.edit_original_response(embed=animation_embed)
            
            # [PERBAIKAN] Jeda animasi dibuat lebih aman
            await asyncio.sleep(0.75 if item.get('rarity') not in ['Epic', 'Legendary'] else 2.0)

        summary_embed = self._create_summary_embed(user, processed_results, new_prisma_total, total_compensation, banner_type)
        await interaction.edit_original_response(embed=summary_embed)

    async def show_banner(self, interaction: discord.Interaction, banner_type: str, is_refresh: bool = False):
        if not is_refresh:
            await interaction.response.defer()
        
        view = GachaBannerView(interaction.user, self, banner_type)
        embed = await self.create_banner_embed(interaction.user, banner_type)
        
        attachments = []
        banner_path = BANNER_IMAGES.get(banner_type)
        if banner_path and os.path.exists(banner_path):
            banner_file = discord.File(banner_path, filename=os.path.basename(banner_path))
            attachments.append(banner_file)
            embed.set_image(url=f"attachment://{os.path.basename(banner_path)}")

        await interaction.edit_original_response(embed=embed, view=view, attachments=attachments)
        view.message = await interaction.original_response()

    @commands.command(name="gacha")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def gacha(self, ctx: commands.Context):
        """Membuka panel gacha Title Dan Artefact"""
        view = GachaMainView(ctx.author, self)
        embed = await self.create_main_embed(ctx.author)
        message = await ctx.send(embed=embed, view=view)
        view.message = message

async def setup(bot: commands.Bot):
    await bot.add_cog(GachaCog(bot))