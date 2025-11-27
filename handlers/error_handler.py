import discord
import traceback
import sys
from discord.ext import commands
import datetime

# Impor kelas-kelas error yang lebih spesifik
from discord.ext.commands import (
    CommandNotFound,
    CommandOnCooldown,
    MissingRequiredArgument,
    CheckFailure,
    BadArgument,
    MemberNotFound,
    UserNotFound, # [PENINGKATAN] Impor UserNotFound
    BadUnionArgument, # [PENINGKATAN] Untuk menangani error konversi
    CommandInvokeError
)

# Impor BotColors dengan cara yang aman
try:
    from ..cogs._utils import BotColors
except ImportError:
    class BotColors:
        WARNING = 0xf0b86c
        ERROR = 0xff6961
        DEFAULT = 0x2b2d31

def setup_error_handler(bot: commands.Bot):
    """
    Fungsi untuk mendaftarkan event on_command_error ke instance bot.
    """
    @bot.event
    async def on_command_error(ctx: commands.Context, error: commands.CommandError):
        """
        Listener global yang dipanggil setiap kali sebuah perintah menghasilkan error.
        """
        if hasattr(ctx.command, 'on_error'):
            return

        if isinstance(error, CommandNotFound):
            return

        original_error = getattr(error, 'original', error)

        # === Menangani Error Cooldown ===
        if isinstance(error, CommandOnCooldown):
            waktu_sisa = f"{error.retry_after:.1f} detik"
            embed = discord.Embed(
                title="⏳ Perintah Sedang Cooldown ⏳",
                description=f"Sabar dulu! Kamu bisa mencoba perintah `{ctx.command.name}` lagi dalam **{waktu_sisa}**.",
                color=BotColors.WARNING
            )
            await ctx.send(embed=embed, ephemeral=True, delete_after=int(error.retry_after))
            return

        # === Menangani Error Argumen Kurang ===
        elif isinstance(error, MissingRequiredArgument):
            argumen_hilang = error.param.name
            prefix = bot.command_prefix
            penggunaan_benar = f"`{prefix}{ctx.command.qualified_name} {ctx.command.signature}`"
            
            embed = discord.Embed(
                title="❌ Argumen Tidak Lengkap",
                description=f"Kamu lupa memasukkan argumen yang dibutuhkan: `{argumen_hilang}`.",
                color=BotColors.ERROR
            )
            embed.add_field(name="Penggunaan yang Benar", value=penggunaan_benar)
            await ctx.send(embed=embed, ephemeral=True)
            return

        # === Menangani Error Member/User Tidak Ditemukan ===
        elif isinstance(original_error, (MemberNotFound, UserNotFound)):
            embed = discord.Embed(
                title="👤 Pengguna Tidak Ditemukan",
                description=f"Aku tidak dapat menemukan pengguna yang kamu sebutkan. Pastikan ejaannya benar dan (jika perlu) mereka ada di server ini.",
                color=BotColors.ERROR
            )
            await ctx.send(embed=embed, ephemeral=True, delete_after=10)
            return

        # === [PENINGKATAN] Menangani Error Konversi Argumen yang Gagal ===
        elif isinstance(error, (BadArgument, BadUnionArgument)):
            embed = discord.Embed(
                title="🤔 Argumen Tidak Valid",
                description="Tipe argumen yang kamu berikan sepertinya salah. Misalnya, kamu memasukkan teks padahal yang diminta adalah angka.",
                color=BotColors.ERROR
            )
            prefix = bot.command_prefix
            penggunaan_benar = f"`{prefix}{ctx.command.qualified_name} {ctx.command.signature}`"
            embed.add_field(name="Contoh Penggunaan", value=penggunaan_benar)
            await ctx.send(embed=embed, ephemeral=True, delete_after=10)
            return
            
        # === Menangani Error Izin (CheckFailure) ===
        elif isinstance(error, CheckFailure):
            embed = discord.Embed(
                title="🚫 Akses Ditolak",
                description="Kamu tidak memiliki izin untuk menggunakan perintah ini.",
                color=BotColors.ERROR
            )
            await ctx.send(embed=embed, ephemeral=True, delete_after=10)
            return

        # === [PENINGKATAN] Menangani SEMUA ERROR LAINNYA dengan Notifikasi ke Developer ===
        elif isinstance(error, CommandInvokeError):
            # Kirim pesan umum ke pengguna
            embed_user = discord.Embed(
                title="💥 Terjadi Kesalahan Internal",
                description="Maaf, terjadi kesalahan tak terduga saat menjalankan perintah. Developer telah diberitahu secara otomatis.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed_user, ephemeral=True)
            
            # Siapkan pesan detail untuk developer
            tb_str = "".join(traceback.format_exception(type(original_error), original_error, original_error.__traceback__))
            
            embed_dev = discord.Embed(
                title=f"🚨 Error Ditemukan di Perintah: `{ctx.command.name}`",
                color=discord.Color.dark_red(),
                timestamp=datetime.now()
            )
            embed_dev.add_field(name="Server", value=f"`{ctx.guild.name}` (ID: `{ctx.guild.id}`)", inline=False)
            embed_dev.add_field(name="Channel", value=f"`#{ctx.channel.name}` (ID: `{ctx.channel.id}`)", inline=False)
            embed_dev.add_field(name="Pengguna", value=f"`{ctx.author}` (ID: `{ctx.author.id}`)", inline=False)
            
            # Batasi panjang traceback agar muat di embed
            if len(tb_str) > 4000:
                tb_str = tb_str[:4000] + "\n... (traceback dipotong)"

            embed_dev.description = f"```py\n{tb_str}\n```"

            # Kirim DM ke owner bot
            try:
                owner = await bot.fetch_user(bot.owner_id)
                await owner.send(embed=embed_dev)
            except Exception as e:
                print(f"GAGAL MENGIRIM DM ERROR KE DEVELOPER: {e}", file=sys.stderr)

            # Tetap cetak di konsol sebagai cadangan
            print(f'Error pada perintah {ctx.command} oleh {ctx.author}:', file=sys.stderr)
            traceback.print_exception(type(original_error), original_error, original_error.__traceback__, file=sys.stderr)
            
        else:
            # Jika ada error lain yang tidak tertangkap, cetak saja
            print(f'Ignoring exception in command {ctx.command}:', file=sys.stderr)
            traceback.print_exception(type(error), error, error.__traceback__, file=sys.stderr)