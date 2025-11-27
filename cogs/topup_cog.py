import discord
from discord.ext import commands
import os
import asyncio
from dotenv import load_dotenv

# Import database functions
from database import get_player_data, update_player_data
# Pastikan _utils.py ada di folder cogs
from ._utils import BotColors 

load_dotenv()

# --- KONFIGURASI PAKET TOP UP (BALANCED + 6% BONUS) ---
TOPUP_PACKAGES = [
    # TIER 1: Entry Level (100k)
    {
        "id": "maha1", 
        "label": "Kopi Santai", 
        "prisma": 1200,   # Sebelumnya 600
        "price": 100000, 
        "price_str": "100k Owo", 
        "emoji": "☕"
    },
    # TIER 2: Mid Level (500k)
    {
        "id": "maha2", 
        "label": "Jajan Nia", 
        "prisma": 5000,  # Sebelumnya 3.200
        "price": 500000, 
        "price_str": "500k Owo", 
        "emoji": "🎮" 
    },
    # TIER 3: High Level (1 Juta)
    {
        "id": "maha3", 
        "label": "Konser Akang", 
        "prisma": 10000,  # Sebelumnya 7.000 (Pas buat 1 Mythic)
        "price": 1000000, 
        "price_str": "1m Owo", 
        "emoji": "🎸"
    },
    # TIER 4: Whale Start (2.5 Juta)
    {
        "id": "maha4", 
        "label": "Galaxy Lumi", 
        "prisma": 25000, # Sebelumnya 18.000 (1 Godly + Sisa 4k buat gacha)
        "price": 2500000, 
        "price_str": "2.5m Owo", 
        "emoji": "🌌"
    },
    # TIER 5: Sultan (5 Juta)
    {
        "id": "maha5", 
        "label": "Black Card Zen", 
        "prisma": 50000, # Sebelumnya 37.000 (2 Godly + Sisa 10k)
        "price": 5000000, 
        "price_str": "5m Owo", 
        "emoji": "💳"
    },
    # TIER 6: Leviathan (7.5 Juta)
    {
        "id": "maha6", 
        "label": "Harta Karun Moca", 
        "prisma": 750000, # Sebelumnya 56.000 (Pas 4 Godly)
        "price": 7500000, 
        "price_str": "7.5m Owo", 
        "emoji": "💎"
    },
    # TIER 7: MAHA INVESTOR (10 Juta)
    {
        "id": "maha7", 
        "label": "MAHA5 Investor", 
        "prisma": 100000, # Sebelumnya 75.000 (5 Godly + Sisa 5k)
        "price": 10000000, 
        "price_str": "10m Owo", 
        "emoji": "👑"
    }
]

# ===================================================================================
# --- VIEW 1: ADMIN APPROVAL (FIXED) ---
# ===================================================================================

class TransactionReviewView(discord.ui.View):
    def __init__(self, bot, buyer_id: int, package: dict):
        super().__init__(timeout=None) # Timeout None agar tombol tidak mati sendiri
        self.bot = bot
        self.buyer_id = buyer_id # Simpan ID saja, nanti di-fetch
        self.package = package
        self.is_processed = False

    async def update_admin_embed(self, interaction, status_text, color, processed_by):
        """Update tampilan embed admin dengan aman."""
        try:
            embed = interaction.message.embeds[0]
            embed.color = color
            
            # Hapus field "Status" lama jika ada, lalu tambahkan yang baru
            # Kita rebuild fields untuk menghindari error index
            new_fields = []
            for field in embed.fields:
                if field.name != "Status":
                    new_fields.append(field)
            
            embed.clear_fields()
            for field in new_fields:
                embed.add_field(name=field.name, value=field.value, inline=field.inline)
            
            # Tambahkan Status Baru
            embed.add_field(name="Status", value=f"{status_text} oleh **{processed_by.display_name}**", inline=False)

            # Matikan tombol dan update pesan
            for child in self.children:
                child.disabled = True
                
            await interaction.message.edit(embed=embed, view=self)
        except Exception as e:
            print(f"ERROR update_admin_embed: {e}")

    async def get_buyer_user(self):
        """Helper untuk mendapatkan object User yang valid."""
        try:
            return await self.bot.fetch_user(self.buyer_id)
        except:
            return None

    @discord.ui.button(label="✅ Terima (Kirim Prisma)", style=discord.ButtonStyle.success)
    async def approve_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.is_processed:
            return await interaction.response.send_message("❌ Transaksi ini sudah selesai.", ephemeral=True)

        self.is_processed = True
        await interaction.response.defer()

        print(f"Processing TopUp Approval for User ID: {self.buyer_id}")

        try:
            # 1. Update Database
            player_data = await get_player_data(self.bot.db, self.buyer_id)
            current_prisma = player_data.get('prisma', 0)
            new_prisma = current_prisma + self.package['prisma']
            await update_player_data(self.bot.db, self.buyer_id, prisma=new_prisma)

            # 2. Update Embed Admin
            await self.update_admin_embed(interaction, "✅ **DISETUJUI**", discord.Color.green(), interaction.user)

            # 3. Kirim DM
            buyer = await self.get_buyer_user()
            notif_status = "Gagal (User tidak ditemukan/DM Tutup)"
            
            if buyer:
                try:
                    receipt_embed = discord.Embed(
                        title="💎 Top Up Berhasil!",
                        description=f"Hore! Top up paket **{self.package['label']}** disetujui.\n"
                                    f"**{self.package['prisma']:,} Prisma** telah masuk ke akunmu.",
                        color=discord.Color.green()
                    )
                    receipt_embed.set_footer(text="Terima kasih telah support server ini!")
                    await buyer.send(embed=receipt_embed)
                    notif_status = "Terkirim ke DM User"
                except discord.Forbidden:
                    notif_status = "Gagal (DM User Tertutup)"

            await interaction.followup.send(f"✅ Sukses! Prisma ditambahkan. ({notif_status})", ephemeral=True)

        except Exception as e:
            print(f"ERROR approve_button: {e}")
            await interaction.followup.send("❌ Terjadi error internal saat memproses. Cek terminal.", ephemeral=True)

    @discord.ui.button(label="❌ Tolak (Bukti Salah)", style=discord.ButtonStyle.danger)
    async def reject_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.is_processed:
            return await interaction.response.send_message("❌ Transaksi ini sudah selesai.", ephemeral=True)

        self.is_processed = True
        await interaction.response.defer()

        try:
            # 1. Update Embed Admin
            await self.update_admin_embed(interaction, "❌ **DITOLAK**", discord.Color.red(), interaction.user)

            # 2. Kirim DM
            buyer = await self.get_buyer_user()
            notif_status = "Gagal (DM Tutup)"
            
            if buyer:
                try:
                    reject_embed = discord.Embed(
                        title="⚠️ Top Up Ditolak",
                        description=f"Top up paket **{self.package['label']}** ditolak oleh Admin.",
                        color=discord.Color.red()
                    )
                    reject_embed.add_field(name="Alasan", value="Bukti transfer tidak valid, buram, atau dana belum masuk.")
                    await buyer.send(embed=reject_embed)
                    notif_status = "Terkirim ke DM User"
                except discord.Forbidden:
                    pass

            await interaction.followup.send(f"❌ Transaksi ditolak. ({notif_status})", ephemeral=True)

        except Exception as e:
            print(f"ERROR reject_button: {e}")
            await interaction.followup.send("❌ Error saat menolak transaksi.", ephemeral=True)

# ===================================================================================
# --- VIEW 2: TOMBOL UPLOAD (Dengan Private DM) ---
# ===================================================================================

class UploadEvidenceView(discord.ui.View):
    def __init__(self, cog, package):
        super().__init__(timeout=300) 
        self.cog = cog
        self.package = package
        self.is_uploading = False # Flag untuk mencegah double klik

    @discord.ui.button(label="📸 Kirim Bukti (via DM)", style=discord.ButtonStyle.primary, emoji="📩")
    async def upload_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.is_uploading:
            return await interaction.response.send_message("Sedang memproses, mohon tunggu...", ephemeral=True)
        
        self.is_uploading = True
        await interaction.response.defer(ephemeral=True)

        try:
            # 1. Coba buka DM
            try:
                dm_channel = await interaction.user.create_dm()
                await dm_channel.send(
                    f"👋 Halo **{interaction.user.name}**!\n"
                    f"Silakan kirim **Screenshot Bukti Transfer** untuk paket **{self.package['label']}** di sini.\n"
                    "Bot akan menunggu selama 2 menit..."
                )
            except discord.Forbidden:
                self.is_uploading = False
                return await interaction.followup.send(
                    "❌ **Gagal mengirim DM!** Buka pengaturan privasi server (Allow Direct Messages) agar bot bisa chat kamu.",
                    ephemeral=True
                )

            # 2. Disable tombol di server agar tidak diklik 2x
            button.disabled = True
            button.label = "Cek DM Kamu"
            try:
                await interaction.edit_original_response(view=self)
            except: pass
            
            await interaction.followup.send("📩 **Cek DM Kamu sekarang!** Kirim gambarnya di sana.", ephemeral=True)

            # 3. Tunggu Gambar di DM
            def check(m):
                return m.author.id == interaction.user.id and m.guild is None and m.attachments

            msg = await self.cog.bot.wait_for('message', check=check, timeout=120.0)
            
            attachment = msg.attachments[0]
            if not attachment.content_type.startswith('image/'):
                await msg.reply("⚠️ Itu bukan gambar! Ulangi dari awal di server jika ingin mencoba lagi.")
                return 

            # 4. Proses Gambar
            await msg.channel.send("🔄 Mengupload bukti ke Admin...")
            evidence_file = await attachment.to_file()

            # 5. Kirim ke Admin
            channel_id = os.getenv("TOPUP_CHANNEL_ID")
            if not channel_id:
                await msg.reply("❌ Error Config: TOPUP_CHANNEL_ID missing.")
                return

            topup_channel = self.cog.bot.get_channel(int(channel_id))
            if not topup_channel:
                await msg.reply("❌ Error: Channel Admin tidak ditemukan.")
                return

            # Embed Admin
            admin_embed = discord.Embed(
                title="🧾 Verifikasi Pembayaran Baru",
                description=f"User: **{interaction.user.name}** ({interaction.user.mention})\n"
                            f"Paket: **{self.package['label']}**\n"
                            f"Tagihan: **{self.package['price_str']}**",
                color=discord.Color.gold(),
                timestamp=interaction.created_at
            )
            admin_embed.set_image(url=f"attachment://{evidence_file.filename}")
            admin_embed.set_thumbnail(url=interaction.user.display_avatar.url)
            admin_embed.add_field(name="Status", value="⏳ **Menunggu Konfirmasi Admin**", inline=False)

            # Pass ID user, bukan object user, untuk keamanan data saat view direkonstruksi
            await topup_channel.send(
                content=f"Request Topup dari {interaction.user.mention}", 
                embed=admin_embed, 
                file=evidence_file, 
                view=TransactionReviewView(self.cog.bot, interaction.user.id, self.package)
            )

            await msg.reply("✅ **Bukti Terkirim!** Tunggu konfirmasi admin ya.")

        except asyncio.TimeoutError:
            try: await interaction.user.send("⚠️ Waktu habis. Silakan ulangi request di server.")
            except: pass
        except Exception as e:
            print(f"ERROR Upload Flow: {e}")
            try: await interaction.user.send("❌ Terjadi kesalahan sistem.")
            except: pass

# ===================================================================================
# --- VIEW 3: SELECT MENU ---
# ===================================================================================

class TopUpSelect(discord.ui.Select):
    def __init__(self, cog):
        self.cog = cog
        options = [
            discord.SelectOption(
                label=f"{pkg['label']} - {pkg['price_str']}",
                value=pkg['id'],
                description=f"Dapat {pkg['prisma']:,} Prisma",
                emoji=pkg['emoji']
            ) for pkg in TOPUP_PACKAGES
        ]
        super().__init__(placeholder="Pilih paket OwoCash...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        selected_id = self.values[0]
        package = next((p for p in TOPUP_PACKAGES if p["id"] == selected_id), None)
        
        receiver_id = os.getenv("OWO_RECEIVER_ID")
        if not receiver_id:
            return await interaction.response.send_message("Config Error: OWO_RECEIVER_ID not set.", ephemeral=True)

        payment_command = f"owo give {receiver_id} {package['price']}"

        embed = discord.Embed(
            title=f"🛒 Checkout: {package['label']}",
            description="Ikuti langkah di bawah ini:",
            color=BotColors.MAHA5_PURPLE
        )
        embed.add_field(
            name="1️⃣ Lakukan Transfer",
            value=f"Salin & kirim:\n```\n{payment_command}\n```",
            inline=False
        )
        embed.add_field(
            name="2️⃣ Screenshot & Kirim",
            value="Screenshot bukti sukses, lalu klik tombol di bawah.",
            inline=False
        )

        await interaction.response.send_message(
            embed=embed, 
            view=UploadEvidenceView(self.cog, package), 
            ephemeral=True
        )

class TopUpView(discord.ui.View):
    def __init__(self, cog):
        super().__init__()
        self.add_item(TopUpSelect(cog))

# ===================================================================================
# --- COG UTAMA ---
# ===================================================================================

class TopUpCog(commands.Cog, name="TopUp"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="topup")
    async def topup_menu(self, ctx: commands.Context):
        embed = discord.Embed(
            title="💎 MAHA5 Store: Premium Top Up",
            description="Tukarkan **OwoCash** dengan Prisma.\nRate Prisma telah disesuaikan untuk menjaga nilai eksklusif Item Godly.",
            color=BotColors.MAHA5_PURPLE 
        )
        embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        
        list_str = ""
        for pkg in TOPUP_PACKAGES:
            list_str += f"{pkg['emoji']} **{pkg['label']}**\n" \
                        f"╰ `💸 {pkg['price_str']}` ➔ `💎 {pkg['prisma']:,}`\n"
        
        embed.add_field(name="📦 Daftar Paket", value=list_str, inline=False)
        embed.set_footer(text="Gunakan command 'owo give' untuk pembayaran.")

        view = TopUpView(self)
        await ctx.send(embed=embed, view=view)

async def setup(bot: commands.Bot):
    await bot.add_cog(TopUpCog(bot))