import discord
from discord.ext import commands
import asyncio
import random
import json
import copy
import math
from typing import Optional, List

# Mengimpor modul skill dan fungsi database
from . import skills as skill_handler
from database import get_player_data, update_player_data, get_player_equipment, get_player_upgrades 

# Variabel global untuk melacak pertarungan aktif
active_players = set()

# --- Helper Functions ---

def _get_level_from_exp(total_exp: int):
    if total_exp < 0: total_exp = 0
    return int(math.sqrt(total_exp / 100)) + 1

def _calculate_evasion(spd):
    # Rumus Evasion: SPD / (SPD + 200) * 0.70 (Max 70% chance)
    return (spd / (spd + 200)) * 0.70

def _calculate_damage(attacker, defender, skill_multiplier=1.0):
    # Defense Factor: Pengurangan damage berdasarkan DEF musuh
    defense_factor = 100 / (100 + max(0, defender['stats']['def']))
    
    # Base Damage Calculation
    raw_damage = attacker['stats']['atk'] * skill_multiplier
    damage = raw_damage * defense_factor
    
    is_crit = False
    # Cek Critical Hit
    if random.random() < attacker['stats']['crit_rate']:
        damage *= attacker['stats']['crit_damage']
        is_crit = True
        
    # RNG Variasi Damage (+/- 5%)
    damage *= random.uniform(0.95, 1.05)
    
    return int(max(1, damage)), is_crit

class CombatSession:
    def __init__(self, bot: commands.Bot, channel: discord.TextChannel, p1_entity, p2_entity, on_finish_callback=None, is_tourney_match=False):
        self.bot = bot
        self.channel = channel
        self.is_pve = not isinstance(p2_entity, discord.Member) and not is_tourney_match
        self.round_count = 1
        self.has_moved_in_round = set()
        self.log = []
        self.game_over = False
        self.message: Optional[discord.Message] = None
        self.view = None
        self.on_finish_callback = on_finish_callback

        if is_tourney_match:
            self.setup_task = asyncio.create_task(self._async_setup_from_data(p1_entity, p2_entity))
        else:
            self.setup_task = asyncio.create_task(self._async_setup_from_users(p1_entity, p2_entity))

    async def _async_setup_from_data(self, p1_data: dict, p2_data: dict):
        """Setup pertarungan menggunakan data partisipan yang sudah lengkap (untuk turnamen)."""
        self.p1 = p1_data
        self.p2 = p2_data
        active_players.add(self.p1['id'])
        active_players.add(self.p2['id'])

        await self._apply_initial_passives()
        self.log.append(f"⚔️ {self.p1['name']} vs {self.p2['name']}!")

        if self.p1['stats']['spd'] >= self.p2['stats']['spd']:
            self.current_turn_participant = self.p1
            self.turn_order = [self.p1, self.p2]
        else:
            self.current_turn_participant = self.p2
            self.turn_order = [self.p2, self.p1]

        self.log.append(f"💨 {self.current_turn_participant['name']} lebih cepat dan mendapat giliran pertama!")
        self.log.append(f"--- Putaran #{self.round_count} ---")

        await self._apply_post_speed_check_passives()

    async def _async_setup_from_users(self, p1_user, p2_entity):
        """Setup pertarungan dengan mengambil data dari awal (untuk PvP/PvE biasa)."""
        self.p1 = await self._create_participant(p1_user, is_player=True)
        self.p2 = await self._create_participant(p2_entity, is_player=not self.is_pve)

        active_players.add(self.p1['id'])
        if not self.is_pve and self.p2.get('id'):
            active_players.add(self.p2.get('id'))

        await self._apply_initial_passives()
        self.log.append(f"⚔️ {self.p1['name']} vs {self.p2['name']}!")

        if self.p1['stats']['spd'] >= self.p2['stats']['spd']:
            self.current_turn_participant = self.p1
            self.turn_order = [self.p1, self.p2]
        else:
            self.current_turn_participant = self.p2
            self.turn_order = [self.p2, self.p1]
            
        self.log.append(f"💨 {self.current_turn_participant['name']} lebih cepat dan mendapat giliran pertama!")
        self.log.append(f"--- Putaran #{self.round_count} ---")
        
        await self._apply_post_speed_check_passives()

    async def _apply_initial_passives(self):
        """Menerapkan semua pasif yang aktif di awal pertarungan."""
        for p in [self.p1, self.p2]:
            opponent = self.get_opponent(p)
            for skill in p.get('skills', []):
                if skill.get('type') == 'passive':
                    passive_func = skill_handler.passive_implementations.get(skill['name'])
                    if not passive_func: continue
                    
                    if skill['name'] in ["Ancestors Sight"]:
                        passive_func(self, p, opponent) 
                    elif skill['name'] in ["Haunting Presence", "Firewall Protocol"]:
                        await passive_func(self, p)
                    elif skill['name'] in ["Forests Embrace"]:
                        pass

    async def _apply_post_speed_check_passives(self):
        """Menerapkan pasif yang bergantung pada siapa yang lebih cepat."""
        if skill_handler._has_passive(self.p1, "Master Tactician") and self.p1['stats']['spd'] > self.p2['stats']['spd']:
            await skill_handler.passive_implementations['Master Tactician'](self, self.p1, self.p2)
        elif skill_handler._has_passive(self.p2, "Master Tactician") and self.p2['stats']['spd'] > self.p1['stats']['spd']:
            await skill_handler.passive_implementations['Master Tactician'](self, self.p2, self.p1)

    async def _create_participant(self, entity, is_player: bool) -> dict:
        """Membuat data snapshot partisipan untuk battle."""
        participant = {}
        
        # --- 1. Base Stats ---
        if is_player:
            player_data = await get_player_data(self.bot.db, entity.id)
            title_id = player_data.get('equipped_title_id')
            # Mengambil data Title (Helper ada di main.py)
            title_data = self.bot.get_title_by_id(title_id) or {}
            
            base_stats = {
                'hp': player_data.get('base_hp', 100), 'atk': player_data.get('base_atk', 10),
                'def': player_data.get('base_def', 5), 'spd': player_data.get('base_spd', 10),
                'crit_rate': 0.05, 'crit_damage': 1.5
            }
            
            participant = { 
                "id": entity.id, "name": entity.display_name, "avatar_url": entity.display_avatar.url, 
                "is_player": True, "agency_id": player_data.get('agency_id')
            }
            
            # Bonus Agensi
            if agency_id := player_data.get('agency_id'):
                if agency_id == "mahavirtual": base_stats['atk'] += 5
                elif agency_id == "prism_project": base_stats['def'] += 4
                elif agency_id == "meisoncafe": 
                    base_stats['hp'] += 20; base_stats['atk'] += 2
                    base_stats['def'] += 2; base_stats['spd'] += 1
                elif agency_id == "ateliernova": 
                    base_stats['spd'] += 3; base_stats['hp'] = int(base_stats['hp'] * 0.9)
                elif agency_id == "react_entertainment": 
                    base_stats['def'] = int(base_stats['def'] * 0.85)
        else:
            # Monster Logic
            title_id = entity.get('monster_title_id')
            title_data = self.bot.get_monster_title_by_id(title_id) or {}
            
            base_stats = {
                'hp': entity.get('hp', 100), 'atk': entity.get('atk', 10),
                'def': entity.get('def', 5), 'spd': entity.get('spd', 10),
                'crit_rate': entity.get('crit_rate', 0.05), 'crit_damage': entity.get('crit_damage', 1.5)
            }
            participant = { 
                "id": None, "name": entity.get('name', 'Monster'), 
                "avatar_url": self.bot.user.display_avatar.url, 
                "is_player": False, "agency_id": None,
                "rewards": {"exp": entity.get('exp_reward', 0), "prisma": entity.get('money_reward', 0)} 
            }

        # --- 2. Hitung Total Stats (Base + Title + Equip + Upgrade) ---
        current_stats = base_stats.copy()
        
        # A. Tambah Title Boost
        for k, v in title_data.get('stat_boost', {}).items():
            key = 'crit_damage' if k == 'crit_dmg' else k
            if key in current_stats: current_stats[key] += v
        
        # B. Tambah Equipment & Upgrade Boost (Hanya Player)
        if is_player:
            equipment = await get_player_equipment(self.bot.db, entity.id)
            upgrades = await get_player_upgrades(self.bot.db, entity.id)

            for slot, item_id in equipment.items():
                if item_id and (item := self.bot.get_item_by_id(item_id)):
                    # Data Upgrade
                    slot_upgrade = upgrades.get(slot, {})
                    level = slot_upgrade.get('level', 0)
                    
                    # B1. Stat Utama Item (Dengan Scaling Upgrade)
                    for k, v in item.get('stat_boost', {}).items():
                        key = 'crit_damage' if k == 'crit_dmg' else k
                        final_val = v
                        
                        # LOGIKA SCALING (Sama dengan ProfileCog & UpgradeCog)
                        if v > 0:
                            if key in ['crit_rate', 'crit_damage']:
                                # 5% per level untuk Crit
                                multiplier = 1 + (level * 0.05)
                                final_val = v * multiplier
                            else:
                                # 10% per level untuk Stat Biasa
                                multiplier = 1 + (level * 0.10)
                                final_val = int(v * multiplier)
                        elif v < 0 and key not in ['crit_rate', 'crit_damage']:
                            # Stat minus membaik
                            reduction = level // 3
                            final_val = min(0, v + reduction)
                        
                        if key in current_stats: current_stats[key] += final_val

                    # B2. Sub-stat (Bonus Stats)
                    for k, v in slot_upgrade.get('bonus_stats', {}).items():
                        if k in current_stats: current_stats[k] += v

        participant.update({
            "stats": current_stats,          # Stat Dinamis (bisa di-debuff)
            "base_stats": current_stats.copy(), # Stat Awal Battle (untuk acuan buff/debuff)
            "hp": current_stats['hp'], 
            "max_hp": current_stats['hp'],
            "skills": title_data.get('skills', []),
            "raw_title_data": title_data,
            "status_effects": [],
            "skill_cooldowns": {s['name']: 0 for s in title_data.get('skills', []) if s.get('type') == 'active'}
        })
        return participant

    def get_opponent(self, participant: dict) -> dict:
        return self.p2 if participant.get('id') == self.p1.get('id') else self.p1

    def get_skill_cooldown(self, participant: dict, skill_name: str) -> int:
        for skill_data in participant.get('skills', []):
            if skill_data.get('name') == skill_name:
                # Hapus '+ 1' di sini agar cooldown sesuai dengan data json/database
                return skill_data.get('cooldown', 3)
        return 0
    
    async def _apply_turn_start_effects_and_check_skip(self, participant: dict) -> bool:
        """
        Memproses semua efek awal giliran (cooldown, DoT, HoT, pasif)
        dan mengurangi durasi status SATU KALI.
        Mengembalikan True jika giliran harus dilewati (mis. karena stun).
        """
        if self.game_over: return True

        # 1. Kurangi cooldown skill
        for name in participant['skill_cooldowns']:
            if participant['skill_cooldowns'][name] > 0:
                participant['skill_cooldowns'][name] -= 1

        # 2. Terapkan pasif awal giliran
        if skill_handler._has_passive(participant, "Oceans Lullaby"): await skill_handler.passive_implementations["Oceans Lullaby"](self, participant)
        if skill_handler._has_passive(participant, "Perfect Confection"): await skill_handler.passive_implementations["Perfect Confection"](self, participant)
        if skill_handler._has_passive(participant, "Sanguine Pact"): await skill_handler.passive_implementations["Sanguine Pact"](self, participant)
        if skill_handler._has_passive(participant, "Whims of Fortune"): await skill_handler.passive_implementations["Whims of Fortune"](self, participant)
        if skill_handler._has_passive(participant, "Grave Pact"): await skill_handler.passive_implementations["Grave Pact"](self, participant)
        if skill_handler._has_passive(participant, "Forests Breath"): await skill_handler.passive_implementations["Forests Breath"](self, participant)
        if skill_handler._has_passive(participant, "Dark Honor"): await skill_handler.passive_implementations["Dark Honor"](self, participant)
        if skill_handler._has_passive(participant, "Immortal Blade"): await skill_handler.passive_implementations["Immortal Blade"](self, participant)
        if skill_handler._has_passive(participant, "Eternal Power"): await skill_handler.passive_implementations["Eternal Power"](self, participant)

        # 3. Terapkan efek DoT, HoT, dan efek berbasis giliran lainnya
        opponent = self.get_opponent(participant)
        if next((e for e in participant.get('status_effects', []) if e.get('name') == 'Blossom Strike'), None):
            damage, _ = await skill_handler._apply_damage(self, participant, opponent, 1.0)
            self.log.append(f"💮 **{participant['name']}** muncul dari kelopak bunga, memberikan **{damage}** kerusakan!")
        if next((e for e in participant.get('status_effects', []) if e.get('name') == 'Summoned Skeleton'), None):
            damage, _ = await skill_handler._apply_damage(self, participant, opponent, 0.40)
            self.log.append(f"💀 Tengkorak **{participant['name']}** menyerang, memberikan **{damage}** kerusakan!")

        for effect in list(participant.get('status_effects', [])):
            if effect.get('type') == 'dot':
                dot_damage = effect.get('damage', 0)
                caster = self.p1 if self.p1.get('id') == effect.get('caster_id') else self.p2
                if caster and skill_handler._has_passive(caster, "Lingering Malice"):
                    dot_damage = int(dot_damage * 1.25)
                participant['hp'] = max(0, participant['hp'] - dot_damage)
                self.log.append(f"🔥 **{participant['name']}** menerima **{dot_damage}** kerusakan dari **{effect['name']}**!")
            elif effect.get('type') == 'hot':
                heal_amount = effect.get('heal_amount', 0)
                if not any(e.get('type') == 'heal_block' for e in participant.get('status_effects', [])):
                    if participant.get('agency_id') == 'projectabyssal':
                        heal_amount = int(heal_amount * 0.9)
                    participant['hp'] = min(participant['max_hp'], participant['hp'] + heal_amount)
                    self.log.append(f"💖 **{participant['name']}** memulihkan **{heal_amount}** HP dari **{effect['name']}**!")
        
        if await self._check_and_handle_game_over():
            return True

        # 4. Cek efek yang melumpuhkan (stun, freeze, paralyze)
        if any(e.get('type') in ['stun', 'freeze'] for e in participant.get('status_effects', [])):
            self.log.append(f"😵 **{participant['name']}** tidak bisa bergerak karena pingsan!")
            self._countdown_effects(participant) # Durasi tetap berkurang
            return True
        if any(e.get('type') == 'paralyze' for e in participant.get('status_effects', [])) and random.random() < 0.5:
            self.log.append(f"⚡ **{participant['name']}** lumpuh dan gagal bergerak!")
            self._countdown_effects(participant) # Durasi tetap berkurang
            return True

        # 5. [PENTING] Kurangi durasi semua efek yang tersisa
        self._countdown_effects(participant)
        
        return False # Giliran tidak dilewati

    # ===================================================================================
    # [FUNGSI YANG DIPERBAIKI]
    # ===================================================================================
    def _countdown_effects(self, participant: dict):
        """
        [DIPERBAIKI] Mengurangi durasi dan menghapus efek yang sudah habis dengan aman.
        Fungsi ini menggunakan pendekatan membuat list baru untuk menghindari bug 
        modifikasi list saat iterasi, yang merupakan penyebab umum pertarungan macet.
        """
        if not participant.get('status_effects'):
            return

        next_turn_effects = []

        # Iterasi melalui semua efek yang ada
        for effect in participant['status_effects']:
            effect['duration'] -= 1
            
            # Jika durasi masih tersisa, simpan efeknya untuk ronde berikutnya
            if effect['duration'] > 0:
                next_turn_effects.append(effect)
            else:
                # Jika durasi habis, proses penghapusan dan kembalikan status
                self.log.append(f"✨ Efek **{effect['name']}** pada **{participant['name']}** telah berakhir.")
                
                # Kembalikan stat normal jika efek ini mengubah stat
                if 'stat' in effect and 'amount_abs' in effect:
                    stat_key = effect['stat']
                    if stat_key in participant['stats']:
                        participant['stats'][stat_key] = max(0, participant['stats'][stat_key] - effect.get('amount_abs', 0))

                # Logika pembersihan khusus untuk Stat Swap
                if effect.get('name') == "Stat Swap (Self)":
                    caster = participant
                    target = self.get_opponent(caster)

                    # Temukan efek yang sesuai pada target
                    target_effect = next((e for e in target.get('status_effects', []) if e.get('name') == "Stat Swap (Target)"), None)
                    
                    if 'original_atk' in effect and target_effect:
                        # Kembalikan stat kedua pemain ke nilai asli
                        caster['stats']['atk'] = effect['original_atk']
                        caster['stats']['def'] = effect['original_def']
                        target['stats']['atk'] = target_effect.get('original_atk', target['stats']['atk'])
                        target['stats']['def'] = target_effect.get('original_def', target['stats']['def'])
                        
                        # Hapus juga efek pada target agar tidak terjadi desinkronisasi
                        target['status_effects'] = [e for e in target['status_effects'] if e is not target_effect]

        # Ganti daftar efek lama dengan yang baru yang sudah difilter
        participant['status_effects'] = next_turn_effects
    # ===================================================================================

    async def switch_turn(self):
        if self.game_over: return

        if skill_handler._has_passive(self.current_turn_participant, "Perfect Symmetry"):
            skill_handler.passive_implementations['Perfect Symmetry'](self, self.current_turn_participant)

        current_id = self.current_turn_participant.get('id') if self.current_turn_participant.get('is_player') else 'monster'
        self.has_moved_in_round.add(current_id)
        
        # Ganti giliran ke lawan
        self.current_turn_participant = self.get_opponent(self.current_turn_participant)
        
        # Jika semua pemain sudah bergerak, mulai ronde baru
        if len(self.has_moved_in_round) >= 2:
            self.round_count += 1
            self.has_moved_in_round.clear()
            self.log.append(f"--- Putaran #{self.round_count} ---")
            # Kembali ke urutan awal
            self.current_turn_participant = self.turn_order[0]
    
    async def process_turn_action(self, user_id: int, action: str, **kwargs):
        if self.game_over: return
        attacker = self.current_turn_participant
        if user_id is not None and user_id != attacker.get('id'): return
        
        defender = self.get_opponent(attacker)
        if any(e.get('name') == 'Untargetable' for e in defender.get('status_effects', [])):
            self.log.append(f"💨 Serangan **{attacker['name']}** gagal karena **{defender['name']}** tidak dapat ditargetkan!")
            await self.switch_turn()
            return

        # Logika aksi dipindahkan ke sini
        log_message = ""
        if action == 'attack':
            if any(e.get('name') == 'Disarmed' for e in attacker.get('status_effects', [])):
                self.log.append(f"🚫 **{attacker['name']}** tidak bisa menyerang!")
            else:
                stun_guarantee = next((e for e in attacker.get('status_effects', []) if e.get('name') == 'Guaranteed Stun'), None)
                damage, is_crit = await skill_handler._apply_damage(self, attacker, defender)

                if stun_guarantee:
                    await skill_handler._apply_status(self, attacker, defender, "Glacial Stun", 2, 'stun')
                    log_message += f"\n> 🧊 Serangan berikutnya memberikan **Stun**!"
                    attacker['status_effects'].remove(stun_guarantee)

                if damage == 0 and not any(e.get('type') == 'invincibility' for e in defender.get('status_effects', [])):
                    log_message = f"🍃 **{attacker['name']}** menyerang, namun **{defender['name']}** berhasil menghindar!"
                else:
                    crit_text = "✨ **KRITIKAL!** " if is_crit else ""
                    log_message = f"💥 {crit_text}**{attacker['name']}** menyerang, memberikan **{damage}** kerusakan!" + log_message
                    if skill_handler._has_passive(attacker, "Twins Harmony"):
                        log_message += "\n" + await skill_handler.passive_implementations["Twins Harmony"](self, attacker, defender)
                    if skill_handler._has_passive(attacker, "Eager Heart"):
                        log_message += "\n" + skill_handler.passive_implementations["Eager Heart"](self, attacker)
                    if skill_handler._has_passive(attacker, "Winters Embrace"):
                        await skill_handler.passive_implementations["Winters Embrace"](self, attacker, defender)
                        
        elif action == 'skill':
            log_message = await skill_handler.apply_skill(self, attacker, defender, **kwargs)
            if skill_handler._has_passive(attacker, "Arcane Echo"):
                log_message += "\n" + skill_handler.passive_implementations["Arcane Echo"](self, attacker)
            if skill_handler._has_passive(attacker, "Feathered Sonnet"):
                await skill_handler.passive_implementations["Feathered Sonnet"](self, attacker)
            if skill_handler._has_passive(defender, "Spellthiefs Gleam"):
                await skill_handler.passive_implementations["Spellthiefs Gleam"](self, attacker, defender)
            if skill_handler._has_passive(attacker, "Dance of a Thousand Cuts"):
                await skill_handler.passive_implementations["Dance of a Thousand Cuts"](self, attacker, defender)

        if log_message.strip(): self.log.append(log_message.strip())
        if await self._check_and_handle_game_over(): return
        
        # Pindah giliran setelah aksi selesai
        await self.switch_turn()


    async def _check_and_handle_game_over(self) -> bool:
        """
        [DIPERBAIKI] Sekarang meneruskan hasil boolean dari _handle_game_over.
        """
        if self.p1['hp'] <= 0 or self.p2['hp'] <= 0:
            # Panggil dan kembalikan hasilnya. Jika ada revive, ini akan mengembalikan False.
            is_truly_over = await self._handle_game_over()
            return is_truly_over
        return False

    async def run_ai_turn(self):
        if self.game_over or self.current_turn_participant.get('is_player'): return
        
        ai = self.current_turn_participant
        
        # 1. Proses efek awal giliran
        is_skipped = await self._apply_turn_start_effects_and_check_skip(ai)
        await self.view.update_message(None) # Update UI untuk menunjukkan DoT/HoT

        if is_skipped:
            if not await self._check_and_handle_game_over():
                await self.switch_turn() # Langsung ganti giliran jika stun
            return

        # Jeda singkat sebelum AI "berpikir"
        await asyncio.sleep(1.0)

        # 2. Pilih dan lakukan aksi
        opponent = self.get_opponent(ai)
        
        taunt_effect = next((e for e in ai.get('status_effects', []) if e.get('name') == 'Taunt'), None)
        if taunt_effect and (taunter_id := taunt_effect.get('caster_id')):
            if opponent.get('id') != taunter_id:
                opponent = self.p1 if self.p1.get('id') == taunter_id else self.p2

        is_silenced = any(e.get('type') == 'silence' for e in ai.get('status_effects', []))
        usable_skills = [s['name'] for s in ai.get('skills', []) if s.get('type') == 'active' and ai['skill_cooldowns'].get(s['name'], 0) == 0]
        
        action = 'attack'
        kwargs = {}
        if usable_skills and not is_silenced and random.random() < 0.7:
            action = 'skill'
            kwargs['skill_name'] = random.choice(usable_skills)
        
        # Eksekusi aksi yang dipilih
        await self.process_turn_action(None, action, **kwargs)


    async def handle_surrender(self, user_id: int):
        if self.game_over: return
        self.game_over = True
        loser = self.p1 if user_id == self.p1.get('id') else self.p2
        winner = self.get_opponent(loser)
        await self._end_fight(winner, loser, reason="surrender")

    async def _handle_game_over(self) -> bool:
        """
        [DIPERBAIKI] Sekarang mengembalikan boolean.
        True jika game benar-benar berakhir, False jika ada yang hidup kembali.
        """
        if self.game_over: return True

        revived_this_turn = False
        for p in [self.p1, self.p2]:
            if p['hp'] <= 0:
                revived = False
                if skill_handler._has_passive(p, "Raging Phoenix"): 
                    revived = await skill_handler.passive_implementations["Raging Phoenix"](self, p)
                if not revived and skill_handler._has_passive(p, "Extra Life"): 
                    revived = await skill_handler.passive_implementations["Extra Life"](self, p)
                if not revived and skill_handler._has_passive(p, "Unbroken Threads"): 
                    revived = await skill_handler.passive_implementations["Unbroken Threads"](self, p)
                if revived:
                    revived_this_turn = True

        if revived_this_turn:
            # Jika ada yang hidup kembali, laporkan bahwa game BELUM berakhir.
            return False

        p1_dead, p2_dead = self.p1['hp'] <= 0, self.p2['hp'] <= 0
        winner, loser = None, None
        
        if p1_dead and not p2_dead: loser, winner = self.p1, self.p2
        elif p2_dead and not p1_dead: loser, winner = self.p2, self.p1
        elif p1_dead and p2_dead:
            self.game_over = True
            await self._end_fight(None, None, reason="draw")
            return True # Game berakhir
        if not (winner and loser):
            # Kondisi ini seharusnya tidak terjadi jika tidak ada revive, tapi sebagai pengaman
            return True

        if skill_handler._has_passive(loser, "Encore of Shadows"): 
            await skill_handler.passive_implementations["Encore of Shadows"](self, loser, winner)
        if skill_handler._has_passive(loser, "Final Vengeance"): 
            await skill_handler.passive_implementations["Final Vengeance"](self, loser, winner)
            
        if winner['hp'] <= 0:
            self.game_over = True
            await self._end_fight(None, None, reason="draw_after_passive")
            return True # Game berakhir
            
        self.game_over = True
        await self._end_fight(winner, loser, reason="knockout")
        return True # Game berakhir

    async def _end_fight(self, winner: dict, loser: dict, reason: str):
        # Fungsi ini sudah benar, tidak ada perubahan
        result_embed = discord.Embed(title="⚔️ Pertarungan Selesai! ⚔️", color=discord.Color.gold())
        description = f"🏆 **{winner['name']}** adalah pemenangnya!"
        if reason == "draw" or reason == "draw_after_passive":
            result_embed.description = "🔥 Pertarungan berakhir dengan **SERI!** Kedua petarung tumbang bersamaan."
            result_embed.color = discord.Color.greyple()
        elif reason == "surrender":
            result_embed.description = f"🏳️ **{loser['name']}** telah menyerah!\n🏆 **{winner['name']}** adalah pemenangnya!"
            result_embed.color = discord.Color.gold()
            if winner.get('avatar_url'):
                result_embed.set_thumbnail(url=winner['avatar_url'])
        else:
            result_embed.description = f"🏆 **{winner['name']}** adalah pemenangnya!"
            result_embed.color = discord.Color.gold()
            if winner.get('avatar_url'):
                result_embed.set_thumbnail(url=winner['avatar_url'])
        
        result_embed.description = description
        if winner.get('avatar_url'):
            result_embed.set_thumbnail(url=winner['avatar_url'])

        if winner and loser:
            if not self.is_pve and winner.get('is_player'):
                winner_data = await get_player_data(self.bot.db, winner['id'])
                current_wins = winner_data.get('pvp_wins', 0)
                await update_player_data(self.bot.db, winner['id'], pvp_wins=current_wins + 1)
                result_embed.set_footer(text=f"Total Kemenangan PvP: {current_wins + 1} 🏆")
        
        if self.is_pve and winner.get('is_player'):
            player_data = await get_player_data(self.bot.db, winner['id'])
            agency_id = player_data.get('agency_id')
            
            exp_reward_range = loser.get("rewards", {}).get("exp", [0, 0])
            prisma_reward_range = loser.get("rewards", {}).get("prisma", [0, 0])
            exp_gain = random.randint(*exp_reward_range) if isinstance(exp_reward_range, list) and len(exp_reward_range) == 2 else 0
            prisma_gain = random.randint(*prisma_reward_range) if isinstance(prisma_reward_range, list) and len(prisma_reward_range) == 2 else 0
            
            agency_bonus_text = ""
            if agency_id == "mahavirtual":
                exp_gain = int(exp_gain * 1.15); prisma_gain = int(prisma_gain * 0.85)
                agency_bonus_text = " (+15% EXP, -15% Prisma)"
            elif agency_id == "prism_project":
                exp_gain = int(exp_gain * 0.80); prisma_gain = int(prisma_gain * 1.20)
                agency_bonus_text = " (-20% EXP, +20% Prisma)"

            if exp_gain > 0 or prisma_gain > 0:
                old_level = player_data.get('level', 1)
                new_exp = player_data.get('exp', 0) + exp_gain
                new_prisma = player_data.get('prisma', 0) + prisma_gain
                new_level = _get_level_from_exp(new_exp)
                
                result_embed.add_field(name="🎁 Hadiah Diterima", value=f"✨ `{exp_gain}` EXP\n💰 `{prisma_gain}` Prisma", inline=False)
                if agency_bonus_text: result_embed.set_footer(text=f"Bonus Agensi diterapkan:{agency_bonus_text}")

                db_updates = {'exp': new_exp, 'prisma': new_prisma}

                if new_level > old_level:
                    levels_gained = new_level - old_level
                    hp_gain, atk_gain, def_gain, spd_gain = 15*levels_gained, 3*levels_gained, 2*levels_gained, 1*levels_gained
                    
                    db_updates.update({
                        'level': new_level,
                        'base_hp': player_data.get('base_hp', 100) + hp_gain,
                        'base_atk': player_data.get('base_atk', 10) + atk_gain,
                        'base_def': player_data.get('base_def', 5) + def_gain,
                        'base_spd': player_data.get('base_spd', 10) + spd_gain
                    })
                    
                    result_embed.title = "🎉 LEVEL UP! 🎉"
                    result_embed.description = f"Selamat **{winner['name']}**, kamu telah mencapai **Level {new_level}**!"
                    stat_increase_text = (f"❤️ HP `+{hp_gain}`\n⚔️ ATK `+{atk_gain}`\n🛡️ DEF `+{def_gain}`\n💨 SPD `+{spd_gain}`")
                    result_embed.add_field(name="📈 Peningkatan Stat Dasar", value=stat_increase_text, inline=False)
                
                await update_player_data(self.bot.db, winner['id'], **db_updates)

        await self.channel.send(embed=result_embed)

        quest_cog = self.bot.get_cog("Misi")
        if quest_cog:
            # Update untuk Pemenang
            if winner and winner.get('is_player'):
                quest_type_win = 'PVE_WIN' if self.is_pve else 'PVP_WIN'
                await quest_cog.update_quest_progress(winner['id'], quest_type_win)

                if self.is_pve:
                    exp_gain = locals().get('exp_gain', 0)
                    prisma_gain = locals().get('prisma_gain', 0)
                    if exp_gain > 0:
                        await quest_cog.update_quest_progress(winner['id'], 'STREAM_EXP', exp_gain)
                    if prisma_gain > 0:
                        await quest_cog.update_quest_progress(winner['id'], 'EARN_PRISMA', prisma_gain)
            
            # Update untuk Partisipasi PvP (untuk kedua pemain)
            if not self.is_pve:
                if winner and winner.get('is_player'):
                    await quest_cog.update_quest_progress(winner['id'], 'PVP_PARTICIPATE')
                if loser and loser.get('is_player'):
                    await quest_cog.update_quest_progress(loser['id'], 'PVP_PARTICIPATE')

        if self.on_finish_callback:
            await self.on_finish_callback(winner, loser)

        if winner.get('id'): active_players.discard(winner['id'])
        if loser.get('id'): active_players.discard(loser['id'])