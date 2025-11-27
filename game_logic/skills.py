# game_logic/skills.py

import random
from typing import TYPE_CHECKING, Tuple

# Mencegah circular import dengan type checking
if TYPE_CHECKING:
    from .combat_logic import CombatSession

# ===================================================================================
# [BARU] KONSTANTA UNTUK MEKANIK EVASION BERBASIS SPD
# ===================================================================================
EVA_CAP = 0.80
EVA_SCALING_FACTOR = 200
MAX_EVA_FROM_SPD = 0.70

# ===================================================================================
# --- FUNGSI HELPER (ALAT BANTU) ---
# ===================================================================================

def _has_passive(participant: dict, passive_name: str) -> bool:
    """Fungsi helper untuk mengecek apakah partisipan memiliki skill pasif tertentu."""
    if passive_name == "Extra Life" and participant.get('passive_flags', {}).get('extra_life_used'):
        return False
    return any(skill.get('name') == passive_name for skill in participant.get('raw_title_data', {}).get('skills', []))

async def _apply_damage(session: "CombatSession", attacker: dict, defender: dict, multiplier: float = 1.0, fixed_damage: int = None, bonus_crit_rate: float = 0.0, bonus_crit_dmg: float = 0.0, ignores_def_percent: float = 0.0, force_crit: bool = False, is_counter_attack: bool = False, bypass_evasion: bool = False) -> Tuple[int, bool]:
    """
    Fungsi terpusat untuk menghitung dan menerapkan damage.
    [PERUBAHAN] Dibuat async untuk bisa memanggil pasif yang async.
    """
    blind_effect = next((e for e in attacker.get('status_effects', []) if e.get('name') in ["Duskfall Blind", "Blind", "Whispers of Fear"]), None)
    if blind_effect and random.random() < blind_effect.get('miss_chance', 0.0):
        session.log.append(f"😵 Serangan **{attacker['name']}** meleset karena efek negatif!")
        return 0, False

    if any(e.get('type') == 'invincibility' for e in defender.get('status_effects', [])):
        session.log.append(f"🛡️ Serangan terhadap **{defender['name']}** tidak mempan karena kebal!")
        return 0, False
    
    if not bypass_evasion:
        defender_spd = defender['stats'].get('spd', 0)
        eva_from_spd = (defender_spd / (defender_spd + EVA_SCALING_FACTOR)) * MAX_EVA_FROM_SPD
        passive_eva_bonus = 0.10 if _has_passive(defender, "Keen Senses") else 0.0
        flowing_evasion_effect = next((e for e in defender.get('status_effects', []) if e.get('name') == "Flowing Evasion"), None)
        buff_eva_bonus = flowing_evasion_effect.get('evasion_boost', 0.0) if flowing_evasion_effect else 0.0
        total_evasion_chance = min(eva_from_spd + passive_eva_bonus + buff_eva_bonus, EVA_CAP)
        if random.random() < total_evasion_chance:
            session.log.append(f"💨 **{defender['name']}** dengan gesit menghindari serangan!")
            return 0, False

    confection_buff = next((e for e in attacker.get('status_effects', []) if e.get('name') == 'Perfect Confection Ready'), None)
    if confection_buff:
        bonus_crit_dmg += 0.50
        attacker['status_effects'].remove(confection_buff)
        session.log.append(f"🍬 **Perfect Confection**! Serangan **{attacker['name']}** menjadi jauh lebih kuat!")

    actual_damage = 0
    is_crit = False

    if fixed_damage is not None:
        actual_damage = fixed_damage
    else:
        total_crit_rate = attacker['stats'].get('crit_rate', 0.05) + bonus_crit_rate
        is_crit = force_crit or random.random() < total_crit_rate
        base_damage = attacker['stats'].get('atk', 10) * multiplier
        final_damage = base_damage * (attacker['stats'].get('crit_damage', 1.5) + bonus_crit_dmg) if is_crit else base_damage
        brand_effect = next((e for e in defender.get('status_effects', []) if e.get('name') == "Branded"), None)
        if brand_effect:
            final_damage *= (1 + brand_effect.get('vulnerability', 0.0))
            session.log.append(f"🎯 Tanda **Inferno Brand** membuat serangan ini lebih menyakitkan!")
        target_def = defender['stats'].get('def', 0) * (1 - ignores_def_percent)
        reduced_damage = final_damage - target_def
        actual_damage = max(1, int(reduced_damage))

    damage_to_hp = actual_damage
    for effect in list(defender['status_effects']):
        if effect.get('type') == 'shield':
            shield_hp = effect.get('shield_hp', 0)
            absorbed = min(damage_to_hp, shield_hp)
            effect['shield_hp'] -= absorbed
            damage_to_hp -= absorbed
            session.log.append(f"🛡️ Perisai **{defender['name']}** menyerap **{absorbed}** kerusakan!")
            if effect['shield_hp'] <= 0:
                defender['status_effects'].remove(effect)
                session.log.append(f"🛡️ Perisai **{defender['name']}** hancur!")
            if damage_to_hp <= 0: break

    lifesteal_percent = attacker['stats'].get('lifesteal', 0.0)
    is_heal_blocked = any(e.get('type') == 'heal_block' for e in attacker.get('status_effects', []))
    if lifesteal_percent > 0 and not is_heal_blocked:
        healed_amount = int(actual_damage * lifesteal_percent)
        attacker['hp'] = min(attacker['max_hp'], attacker['hp'] + healed_amount)
        session.log.append(f"🩸 **{attacker['name']}** memulihkan **{healed_amount}** HP dari serangan.")
    defender['hp'] = max(0, defender['hp'] - damage_to_hp)

    counter_effect = next((e for e in defender.get('status_effects', []) if e.get('type') == 'counter'), None)
    if counter_effect and actual_damage > 0 and not is_counter_attack:
        session.log.append(f"🔄 **{defender['name']}** membalas serangan karena efek **{counter_effect['name']}**!")
        # [PERBAIKAN] Tambahkan await di sini
        counter_damage, is_counter_crit = await _apply_damage(session, attacker=defender, defender=attacker, multiplier=0.75, is_counter_attack=True)
        crit_text = "✨ **KRITIKAL!** " if is_counter_crit else ""
        session.log.append(f"> {crit_text}Serangan balasan memberikan **{counter_damage}** kerusakan!")

    # [PERBAIKAN] Tambahkan await untuk memanggil pasif
    if is_crit and _has_passive(attacker, "Static Resonance"):
        await passive_implementations["Static Resonance"](session, attacker)

    if _has_passive(attacker, "Soul Siphon"):
        await passive_implementations["Soul Siphon"](session, attacker, actual_damage)

    # [TAMBAHKAN INI] Hook untuk misi serangan kritikal
    if is_crit and attacker.get('is_player'):
        quest_cog = session.bot.get_cog("Misi")
        if quest_cog:
            await quest_cog.update_quest_progress(attacker['id'], 'LAND_CRIT')

    return actual_damage, is_crit
    
    
async def _apply_status(session: "CombatSession", caster: dict, target: dict, name: str, duration: int, effect_type: str, **kwargs):
    """Fungsi helper untuk menambahkan efek status dengan logika agensi dan interaksi Heal Block."""
    caster_agency_id = caster.get('agency_id')
    target_agency_id = target.get('agency_id')
    
    is_debuff = effect_type in ['debuff', 'stun', 'silence', 'paralyze', 'heal_block', 'dot']
    is_buff = effect_type in ['buff', 'shield', 'hot', 'counter', 'reflect', 'invincibility', 'immunity']

    # --- LOGIKA HEAL BLOCK (BARU) ---
    # Cek apakah target memiliki Heal Block
    has_heal_block = any(e.get('type') == 'heal_block' for e in target.get('status_effects', []))

    # 1. Jika mencoba memberikan HoT tapi target kena Heal Block -> Gagal
    if effect_type == 'hot' and has_heal_block:
        return f"🚫 Efek **{name}** gagal diterapkan pada **{target['name']}** karena Heal Block!"

    # 2. Jika efek yang diberikan adalah Heal Block -> Hapus semua HoT yang ada
    if effect_type == 'heal_block':
        original_count = len(target['status_effects'])
        # Hapus efek tipe 'hot'
        target['status_effects'] = [e for e in target['status_effects'] if e.get('type') != 'hot']
        
        if len(target['status_effects']) < original_count:
            session.log.append(f"🚫 **Heal Block** menghapus efek regenerasi yang ada pada **{target['name']}**!")
    # --------------------------------

    if is_debuff and caster_agency_id == 'projectabyssal':
        duration += 1
    if is_buff and target_agency_id == 'projectabyssal':
        duration = max(2, int(duration * 0.9))

    # Update durasi jika efek sudah ada (kecuali shield/hot/dot yang biasanya menumpuk/terpisah)
    if effect_type not in ['shield', 'hot', 'dot'] and any(e['name'] == name for e in target['status_effects']):
        for effect in target['status_effects']:
            if effect['name'] == name:
                effect['duration'] = max(effect['duration'], duration)
        return f"Durasi efek **{name}** pada **{target['name']}** diperbarui."

    effect = {'name': name, 'duration': duration, 'type': effect_type, 'caster_id': caster.get('id')}
    effect.update(kwargs)
    
    if 'stat' in kwargs and 'amount' in kwargs:
        stat_name, amount = kwargs['stat'], kwargs['amount']
        if isinstance(amount, str) and '%' in amount:
            percentage = float(amount.strip('%')) / 100
            if is_buff and target_agency_id == 'projectabyssal':
                percentage *= 0.9
            actual_amount = int(target['base_stats'][stat_name] * percentage)
            effect['amount_abs'] = actual_amount
        else:
            actual_amount = amount
            effect['amount_abs'] = actual_amount
        target['stats'][stat_name] = max(0, target['stats'].get(stat_name, 0) + actual_amount)
    
    target['status_effects'].append(effect)
        
    return f"✨ **{target['name']}** terkena efek **{name}**!"

# ===================================================================================
# --- IMPLEMENTASI SKILL AKTIF ---
# ===================================================================================

# --- Twilight Guardian of the Archipelago ---
async def tidal_bulwark(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    shield_amount = int(caster['max_hp'] * 0.50)
    await _apply_status(session, caster, caster, "Tidal Shield", 3, 'shield', shield_hp=shield_amount)
    await _apply_status(session, caster, caster, "Tidal Counter", 3, 'counter')
    await _apply_status(session, caster, caster, "Tidal Defense", 3, 'buff', stat='def', amount='+30%')
    return f"🌊 **{caster['name']}** memanggil **Tidal Bulwark**, menciptakan perisai **{shield_amount}** HP dan bersiap membalas serangan!"

async def duskfall_strike(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    damage, is_crit = await _apply_damage(session, caster, target, 1.50)
    await _apply_status(session, caster, target, "Duskfall Blind", 3, 'debuff', miss_chance=0.35)
    await _apply_status(session, caster, target, "Duskfall Slow", 3, 'debuff', stat='spd', amount='-25%')
    crit_text = "✨ **KRITIKAL!** " if is_crit else ""
    return f"🌇 {crit_text}**{caster['name']}** melancarkan **Duskfall Strike**, memberikan **{damage}** kerusakan dan mengaburkan pandangan lawan!"

# --- Shadowborne Diva of Sorrow ---
async def sorrowful_aria(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    await _apply_status(session, caster, target, "Sorrowful ATK Down", 4, 'debuff', stat='atk', amount='-30%')
    await _apply_status(session, caster, target, "Sorrowful DEF Down", 4, 'debuff', stat='def', amount='-30%')
    log_message = f"🎶 **{caster['name']}** menyanyikan **Sorrowful Aria**, meremukkan semangat juang **{target['name']}**!"
    if random.random() < 0.40:
        await _apply_status(session, caster, target, "Aria Silence", 2, 'silence')
        log_message += f"\n> 🔇 Suara merdunya membungkam **{target['name']}**!"
    return log_message

async def phantom_crescendo(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    damage, is_crit = await _apply_damage(session, caster, target, 1.60, ignores_def_percent=0.30)
    
    # Cek Heal Block
    is_heal_blocked = any(e.get('type') == 'heal_block' for e in caster.get('status_effects', []))
    heal_msg = ""
    
    if not is_heal_blocked:
        heal_amount = int(damage * 0.25)
        caster['hp'] = min(caster['max_hp'], caster['hp'] + heal_amount)
        heal_msg = f" dan menyerap **{heal_amount}** HP!"
    else:
        heal_msg = " (Penyembuhan gagal karena Heal Block!)"

    crit_text = "✨ **KRITIKAL!** " if is_crit else ""
    return f"👻 {crit_text}**{caster['name']}** mencapai **Phantom Crescendo**, memberikan **{damage}** kerusakan{heal_msg}"

# --- Sun of Dual Symphonies ---
async def solar_overture(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    heal_per_turn = int(caster['max_hp'] * 0.15)
    await _apply_status(session, caster, caster, "Solar Regeneration", 4, 'hot', heal_amount=heal_per_turn)
    
    # [PERBAIKAN] Cek flag dari pasif untuk buff tambahan
    if caster.get('passive_flags', {}).get('enhance_support', False):
        await _apply_status(session, caster, caster, "Solar ATK Up (Enhanced)", 4, 'buff', stat='atk', amount='+45%')
        caster['passive_flags']['enhance_support'] = False # Hapus flag setelah digunakan
        log_message = f"☀️ **{caster['name']}** memulai **Solar Overture** yang diperkuat, memulihkan diri dan memperkuat serangan secara masif!"
    else:
        await _apply_status(session, caster, caster, "Solar ATK Up", 4, 'buff', stat='atk', amount='+30%')
        log_message = f"☀️ **{caster['name']}** memulai **Solar Overture**, memulihkan diri dan memperkuat serangan!"

    # Panggil pasif untuk mengaktifkan bagian cooldown reduction
    passive_implementations['Harmonious Resonance'](session, caster, skill_type='support')
    return log_message

async def blazing_finale(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    multiplier = 1.40
    has_buff = any(e.get('type') == 'buff' for e in caster.get('status_effects', []))
    if has_buff:
        # [PERBAIKAN] Menggunakan perkalian untuk "peningkatan 50%"
        multiplier *= 1.50
    
    damage, is_crit = await _apply_damage(session, caster, target, multiplier)
    crit_text = "✨ **KRITIKAL!** " if is_crit else ""
    log_message = f"🔥 {crit_text}**{caster['name']}** menutup simfoni dengan **Blazing Finale**, memberikan **{damage}** kerusakan!"
    if has_buff:
        log_message += "\n> Kekuatan buff meningkatkan ledakan secara drastis!"
        
    # Panggil pasif untuk mengaktifkan bagian enhance support
    passive_implementations['Harmonious Resonance'](session, caster, skill_type='damage')
    return log_message

# --- Flame Within the Crimson Soul ---
async def inferno_brand(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    damage, is_crit = await _apply_damage(session, caster, target, 1.20)
    burn_damage = int(target['max_hp'] * 0.10)
    await _apply_status(session, caster, target, "Inferno Burn", 3, 'dot', damage=burn_damage)
    await _apply_status(session, caster, target, "Branded", 3, 'debuff', vulnerability=0.25) # Custom key
    crit_text = "✨ **KRITIKAL!** " if is_crit else ""
    return f"🔥 **{caster['name']}** meninggalkan **Inferno Brand**, memberikan **{damage}** kerusakan dan membuat target rentan!"

async def soul_combustion(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    hp_percent = caster['hp'] / caster['max_hp']
    # Peningkatan damage berbanding terbalik dengan sisa HP
    # Saat HP 100%, bonus = 0. Saat HP 1%, bonus = 1.5
    damage_bonus = (1 - hp_percent) * 1.5
    multiplier = 1.0 + damage_bonus
    damage, is_crit = await _apply_damage(session, caster, target, multiplier)
    crit_text = "✨ **KRITIKAL!** " if is_crit else ""
    return f"💥 {crit_text}**{caster['name']}** meledakkan jiwanya dengan **Soul Combustion**, memberikan **{damage}** kerusakan!"

# --- Monk of Ancestral Echoes ---
async def hundred_spirits_palm(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    total_damage = 0
    debuffs_applied = []
    debuff_options = [
        {'name': "Weakened Spirit", 'duration': 2, 'type': 'debuff', 'stat': 'atk', 'amount': '-15%'},
        {'name': "Broken Spirit", 'duration': 2, 'type': 'debuff', 'stat': 'def', 'amount': '-15%'},
        {'name': "Slowed Spirit", 'duration': 2, 'type': 'debuff', 'stat': 'spd', 'amount': '-15%'}
    ]
    # [PERBAIKAN 1] Ubah variabel loop dari 'i' menjadi '_' untuk menghindari konflik
    for _ in range(2):
        # [PERBAIKAN 2] Perbaiki pengganda damage agar sesuai dengan deskripsi (50% per serangan)
        damage, _ = await _apply_damage(session, caster, target, 1.00)
        total_damage += damage
        if random.random() < 0.50:
            debuff = random.choice(debuff_options)
            await _apply_status(session, caster, target, **debuff)
            debuffs_applied.append(debuff['name'].split(" ")[0])
    
    log_message = f"👻 **{caster['name']}** menyerang dengan **Hundred Spirits Palm**, memberikan 2 serangan dengan total **{total_damage}** kerusakan!"
    if debuffs_applied:
        log_message += f"\n> Arwah leluhur memberikan efek: {', '.join(debuffs_applied)}!"
    return log_message

async def flowing_mantra(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    # Fungsi ini sudah benar
    caster['status_effects'] = [e for e in caster['status_effects'] if e.get('type') not in ['debuff', 'dot', 'stun', 'silence', 'paralyze', 'heal_block']]
    await _apply_status(session, caster, caster, "Flowing Evasion", 3, 'buff', evasion_boost=0.35)
    await _apply_status(session, caster, caster, "Flowing Attack", 3, 'buff', stat='atk', amount='+15%')
    return f"🧘 **{caster['name']}** menggunakan **Flowing Mantra**, membersihkan diri dan meningkatkan Evasion serta Buff ATK!"

# --- Last Dream of Falling Snow ---
async def absolute_zero(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    damage, is_crit = await _apply_damage(session, caster, target, 1.30)
    crit_text = "✨ **KRITIKAL!** " if is_crit else ""
    log_message = f"❄️ {crit_text}**{caster['name']}** menciptakan **Absolute Zero**, memberikan **{damage}** kerusakan!"
    if random.random() < 0.50:
        await _apply_status(session, caster, target, "Freeze", 2, 'stun') # Treat Freeze as a regular stun for implementation
        log_message += f"\n> 🥶 **{target['name']}** membeku di tempat!"
    return log_message

async def snowflake_dance(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    is_slowed = any(e.get('stat') == 'spd' and e.get('amount') < 0 for e in target.get('status_effects', []))
    total_damage = 0
    damage1, _ = await _apply_damage(session, caster, target, 0.70, force_crit=is_slowed)
    damage2, _ = await _apply_damage(session, caster, target, 0.70, force_crit=is_slowed)
    total_damage = damage1 + damage2
    log_message = f"❄️ **{caster['name']}** menari seperti kepingan salju, memberikan total **{total_damage}** kerusakan!"
    if is_slowed:
        log_message += "\n> Gerakan lambat musuh menjadi celah yang fatal!"
    return log_message

# --- Architect of Fantastical Harmony ---
async def reality_s_blueprint(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    await _apply_status(session, caster, caster, "Stat Swap (Self)", 3, 'stat_swap', target_id=target['id'])
    await _apply_status(session, caster, target, "Stat Swap (Target)", 3, 'stat_swap', target_id=caster['id'])
    
    # Logic for swapping will be handled by a passive hook
    passive_implementations['Stat Swap Logic'](session) # Call the logic immediately
    return f"📐 **{caster['name']}** menggunakan **Realitys Blueprint**, menukar ATK dan DEF dengan **{target['name']}**!"

    
async def harmonic_convergence(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    # Cek Heal Block
    is_heal_blocked = any(e.get('type') == 'heal_block' for e in caster.get('status_effects', []))
    
    caster_hp_percent = caster['hp'] / caster['max_hp']
    target_hp_percent = target['hp'] / target['max_hp']
    avg_percent = (caster_hp_percent + target_hp_percent) / 2
    
    # Penyeimbangan HP tetap terjadi (karena teknisnya bukan heal, tapi swap/balance logic)
    # Tapi bonus heal di akhir harus dicek
    caster['hp'] = int(caster['max_hp'] * avg_percent)
    target['hp'] = int(target['max_hp'] * avg_percent)
    
    heal_msg = ""
    if not is_heal_blocked:
        heal_amount = int(caster['max_hp'] * 0.10)
        caster['hp'] = min(caster['max_hp'], caster['hp'] + heal_amount)
        heal_msg = " dan memulihkan diri!"
    else:
        heal_msg = " (Bonus pemulihan gagal karena Heal Block!)"
    
    return f"🎶 **{caster['name']}** menciptakan **Harmonic Convergence**, menyeimbangkan HP{heal_msg}"

# --- Amethyst Witchfire Gleam ---
async def crystallize_mana(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    damage, is_crit = await _apply_damage(session, caster, target, 1.40)
    for skill_name in target['skill_cooldowns']:
        target['skill_cooldowns'][skill_name] += 2
    crit_text = "✨ **KRITIKAL!** " if is_crit else ""
    return f"💎 {crit_text}**{caster['name']}** menembakkan **Crystallize Mana**, memberikan **{damage}** kerusakan dan mengunci skill lawan!"

async def amethyst_purge(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    buffs_to_remove = [e for e in target['status_effects'] if e.get('type') == 'buff']
    buff_count = len(buffs_to_remove)
    
    for buff in buffs_to_remove:
        if 'stat' in buff: # Revert stat changes
            target['stats'][buff['stat']] -= buff['amount']
        target['status_effects'].remove(buff)

    multiplier = 1.0 + (0.20 * buff_count)
    damage, is_crit = await _apply_damage(session, caster, target, multiplier)
    crit_text = "✨ **KRITIKAL!** " if is_crit else ""
    log_message = f"🔥 {crit_text}**{caster['name']}** melepaskan **Amethyst Purge**, memberikan **{damage}** kerusakan!"
    if buff_count > 0:
        log_message += f"\n> **{buff_count}** buff dari **{target['name']}** telah dihapus!"
    return log_message

# --- Princess of the Imagined Sea ---
async def summon_leviathan_s_mirage(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    damage, is_crit = await _apply_damage(session, caster, target, 1.60)
    await _apply_status(session, caster, target, "Mirage DEF Down", 4, 'debuff', stat='def', amount='-30%')
    crit_text = "✨ **KRITIKAL!** " if is_crit else ""
    return f"🐉 {crit_text}**{caster['name']}** memanggil **Leviathans Mirage**, memberikan **{damage}** kerusakan dahsyat!"

async def dreamtide(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    await _apply_status(session, caster, target, "Dreamtide Stun", 2, 'stun')
    await _apply_status(session, caster, target, "Dreamtide Heal Block", 3, 'heal_block')
    return f"🌊 **{caster['name']}** menenggelamkan **{target['name']}** dalam **Dreamtide**, membuatnya tertidur!"

# --- Poet of Winged Mystery ---
async def verse_of_the_griffin(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    damage, is_crit = await _apply_damage(session, caster, target, 1.50, ignores_def_percent=0.50)
    crit_text = "✨ **KRITIKAL!** " if is_crit else ""
    return f"🦅 {crit_text}**{caster['name']}** mendeklamasikan **Verse of the Griffin**, memberikan **{damage}** kerusakan menembus!"

async def rhyme_of_the_roc(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    await _apply_status(session, caster, caster, "Rocs ATK Up", 3, 'buff', stat='atk', amount='+30%')
    await _apply_status(session, caster, caster, "Rocs Crit Up", 3, 'buff', stat='crit_rate', amount=0.50)
    return f"🐦 **{caster['name']}** membisikkan **Rhyme of the Roc**, mempersiapkan serangan mematikan!"

# --- Silken Marionette Gallery ---
async def puppet_s_vow(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    session.log.append(f"🧵 **{caster['name']}** menggunakan **Puppets Vow**, memaksa **{target['name']}** menyerang dirinya sendiri!")
    # Simulate self-attack
    damage, is_crit = await _apply_damage(session, target, target, 0.75)
    crit_text = "✨ **KRITIKAL!** " if is_crit else ""
    return f"> {crit_text}Serangan boneka memberikan **{damage}** kerusakan pada **{target['name']}**!"

async def strings_of_fate(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    await _apply_status(session, caster, caster, "Strings of Fate Link", 4, 'damage_link', target_id=target['id'])
    return f"🔗 **{caster['name']}** mengikat takdirnya dengan **{target['name']}** melalui **Strings of Fate**!"

# --- Violet Nocturne of Thunder ---
async def thunderclap_sonata(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    damage, _ = await _apply_damage(session, caster, target, 1.0, force_crit=True)
    recoil_damage = int(damage * 0.15)
    caster['hp'] = max(0, caster['hp'] - recoil_damage)
    return f"⚡ **{caster['name']}** memainkan **Thunderclap Sonata**, memberikan **{damage}** kerusakan kritikal dan menerima **{recoil_damage}** recoil!"

async def lightning_etude(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    total_damage = 0
    paralyze_count = 0
    for _ in range(3):
        damage, _ = await _apply_damage(session, caster, target, 0.50)
        total_damage += damage
        if random.random() < 0.20:
            await _apply_status(session, caster, target, "Etude Paralyze", 2, 'paralyze')
            paralyze_count += 1
    
    log_message = f"🌩️ **{caster['name']}** melancarkan **Lightning Etude**, memberikan total **{total_damage}** kerusakan!"
    if paralyze_count > 0:
        log_message += f"\n> Petir menyambar dan melumpuhkan **{target['name']}**!"
    return log_message

# --- Ballad of the Winterborn ---
async def glacial_prison(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    damage, is_crit = await _apply_damage(session, caster, target, 1.20)
    await _apply_status(session, caster, caster, "Guaranteed Stun", 2, 'internal_buff')
    crit_text = "✨ **KRITIKAL!** " if is_crit else ""
    return f"🧊 {crit_text}**{caster['name']}** menciptakan **Glacial Prison**, memberikan **{damage}** kerusakan dan mempersiapkan stun!"

async def winter_s_heart(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    # [PERBAIKAN] Gunakan session.round_count bukan session.turn_count
    turn_bonus_hp = int(caster['max_hp'] * (0.02 * session.round_count))
    turn_bonus_shield = int(caster['max_hp'] * (0.01 * session.round_count))
    
    heal_amount = int(caster['max_hp'] * 0.20) + turn_bonus_hp
    shield_amount = int(caster['max_hp'] * 0.15) + turn_bonus_shield
    
    caster['hp'] = min(caster['max_hp'], caster['hp'] + heal_amount)
    await _apply_status(session, caster, caster, "Winter Shield", 3, 'shield', shield_hp=shield_amount)
    
    return f"❤️‍🩹 **{caster['name']}** memanggil **Winters Heart**, memulihkan **{heal_amount}** HP dan mendapatkan perisai **{shield_amount}** HP!"

# --- Whispers of the Netopia Café ---
async def data_leak(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    # Hapus semua buff
    buffs_to_remove = [e for e in target['status_effects'] if e.get('type') == 'buff']
    for buff in buffs_to_remove:
        if 'stat' in buff: target['stats'][buff['stat']] -= buff['amount']
        target['status_effects'].remove(buff)

    await _apply_status(session, caster, target, "Data Leak DEF Down", 3, 'debuff', stat='def', amount='-50%')
    log_message = f"💻 **{caster['name']}** melakukan **Data Leak**!"
    if buffs_to_remove:
        log_message += f"\n> Semua buff **{target['name']}** dihapus dan pertahanannya hancur!"
    else:
        log_message += f"\n> Pertahanan **{target['name']}** hancur!"
    return log_message

async def system_crash(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    # Menggunakan fixed_damage untuk mengabaikan DEF secara total
    damage_amount = int(caster['stats']['atk'] * 0.80)
    damage, _ = await _apply_damage(session, caster, target, fixed_damage=damage_amount)
    await _apply_status(session, caster, target, "System Crash Heal Block", 3, 'heal_block')
    return f"💥 **{caster['name']}** menyebabkan **System Crash**, memberikan **{damage}** kerusakan dan mencegah pemulihan!"

# --- Soft Silence of Sakura ---
async def sakura_flash(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    bonus_crit_dmg = kwargs.get('bonus_crit_dmg', 0.0) + 0.50
    # [PERBAIKAN] Gunakan flag bypass_evasion
    damage, is_crit = await _apply_damage(session, caster, target, 1.50, bonus_crit_dmg=bonus_crit_dmg, bypass_evasion=True)
    crit_text = "✨ **KRITIKAL!** " if is_crit else ""
    return f"🌸 {crit_text}**{caster['name']}** menebas dengan **Sakura Flash**, memberikan **{damage}** kerusakan yang tak terhindarkan!"

async def falling_blossom(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    await _apply_status(session, caster, caster, "Untargetable", 2, 'ungettable') # Efek akan berlangsung 1 giliran
    await _apply_status(session, caster, caster, "Blossom Strike", 2, 'internal_buff') # Tanda untuk menyerang giliran berikutnya
    return f"💮 **{caster['name']}** menghilang dalam **Falling Blossom**, menjadi tidak dapat diserang!"

# --- The Adamant Colossus (BARU) ---
async def sundering_quake(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    bonus_crit_rate = kwargs.get('bonus_crit_rate', 0.0)
    bonus_crit_dmg = kwargs.get('bonus_crit_dmg', 0.0)
    damage, is_crit = await _apply_damage(session, caster, target, 1.30, bonus_crit_rate=bonus_crit_rate, bonus_crit_dmg=bonus_crit_dmg)
    crit_text = "✨ **KRITIKAL!** " if is_crit else ""
    log_message = f"⛰️ {crit_text}**{caster['name']}** menciptakan **Sundering Quake**, memberikan **{damage}** kerusakan!"

    if random.random() < 0.30:
        await _apply_status(session, caster, target, "Quake Weaken", 3, 'debuff', stat='def', amount='-20%')
        log_message += f"\n> 🛡️ Pertahanan **{target['name']}** retak!"
        
    return log_message

async def ironclad_resolve(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    # Memberikan 3 efek: Taunt (untuk logika AI), DEF buff, dan Disarm (mencegah serangan)
    await _apply_status(session, caster, caster, "Taunt", 2, 'taunt')
    await _apply_status(session, caster, caster, "Ironclad Defense", 2, 'buff', stat='def', amount='+60%')
    await _apply_status(session, caster, caster, "Disarmed", 2, 'disarm')
    return f"🛡️ **{caster['name']}** menggunakan **Ironclad Resolve**, memfokuskan semua serangan pada dirinya dan memperkuat pertahanan!"

# --- The Sentinels Vow (BARU) ---
async def barricade_of_thorns(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    shield_amount = int(caster['max_hp'] * 0.35)
    await _apply_status(session, caster, caster, "Thorns Shield", 3, 'shield', shield_hp=shield_amount)
    await _apply_status(session, caster, caster, "Thorns Reflect", 3, 'reflect', reflect_percent=0.20)
    return f"🛡️ **{caster['name']}** mendirikan **Barricade of Thorns**, menciptakan perisai **{shield_amount}** HP yang memantulkan serangan!"

async def retribution_bash(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    bonus_crit_rate = kwargs.get('bonus_crit_rate', 0.0)
    bonus_crit_dmg = kwargs.get('bonus_crit_dmg', 0.0)

    damage, is_crit = await _apply_damage(session, caster, target, 0.80,
                                    bonus_crit_rate=bonus_crit_rate,
                                    bonus_crit_dmg=bonus_crit_dmg)
    crit_text = "✨ **KRITIKAL!** " if is_crit else ""
    log_message = f"👊 {crit_text}**{caster['name']}** menghantam dengan **Retribution Bash**, memberikan **{damage}** kerusakan!"
    
    is_shield_active = any(e.get('type') == 'shield' for e in caster.get('status_effects', []))
    if is_shield_active:
        await _apply_status(session, caster, target, "Bash Stun", 2, 'stun')
        log_message += f"\n> 😵 Kekuatan perisai membuat **{target['name']}** pingsan!"
        
    return log_message

# --- Howling Gale (BARU) ---
async def lancer_s_cometfall(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    bonus_crit_rate = kwargs.get('bonus_crit_rate', 0.0)
    bonus_crit_dmg = kwargs.get('bonus_crit_dmg', 0.0)

    damage, is_crit = await _apply_damage(session, caster, target, 1.25,
                                    bonus_crit_rate=bonus_crit_rate,
                                    bonus_crit_dmg=bonus_crit_dmg)
    await _apply_status(session, caster, caster, "Cometfall Focus", 2, 'buff', stat='crit_rate', amount=0.30)
    crit_text = "✨ **KRITIKAL!** " if is_crit else ""
    return f"☄️ {crit_text}**{caster['name']}** melesat dengan **Lancers Cometfall**, memberikan **{damage}** kerusakan dan meningkatkan fokus kritikal!"

async def ride_the_wind(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    await _apply_status(session, caster, caster, "Wind Rider (SPD)", 3, 'buff', stat='spd', amount='+25%')
    await _apply_status(session, caster, caster, "Wind Rider (ATK)", 3, 'buff', stat='atk', amount='+15%')
    return f"🏇 **{caster['name']}** memacu tunggangannya dengan **Ride the Wind**, meningkatkan kecepatan dan kekuatan serangan!"

# --- The Weaver Commander (BARU) ---
async def foresights_gambit(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    await _apply_status(session, caster, target, "Blind", 3, 'debuff', miss_chance=0.25)
    return f"👁️ **{caster['name']}** menggunakan **Foresights Gambit**, mengaburkan pandangan **{target['name']}**!"

async def orchestrated_assault(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    await _apply_status(session, caster, caster, "Orchestrated Crit Rate", 4, 'buff', stat='crit_rate', amount=0.20)
    await _apply_status(session, caster, caster, "Orchestrated Crit Dmg", 4, 'buff', stat='crit_damage', amount=0.20)
    return f"🎼 **{caster['name']}** melakukan **Orchestrated Assault**, mempersiapkan serangan mematikan berikutnya!"

# --- Bulwark of the Dawns Light (BARU) ---
async def hallowed_ground(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    is_heal_blocked = any(e.get('type') == 'heal_block' for e in caster.get('status_effects', []))
    if is_heal_blocked:
        return f"❤️‍🩹 **{caster['name']}** mencoba menggunakan **Hallowed Ground**, tetapi gagal karena efek Heal Block!"
        
    heal_per_turn = int(caster['max_hp'] * 0.15)
    await _apply_status(session, caster, caster, "Hallowed Ground", 3, 'hot', heal_amount=heal_per_turn)
    return f"✨ **{caster['name']}** memberkati tanah dengan **Hallowed Ground**, memulihkan diri setiap giliran!"

async def sacred_intervention(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    await _apply_status(session, caster, caster, "Invincibility", 2, 'invincibility')
    return f"🛡️ **{caster['name']}** dilindungi oleh **Sacred Intervention**, menjadi kebal terhadap semua serangan!"

# --- Phantom in the Code (BARU) ---
async def cascading_logic_bomb(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    damage, is_crit = await _apply_damage(session, caster, target, 0.80)
    burn_damage = int(target['max_hp'] * 0.05)
    await _apply_status(session, caster, target, "Logic Bomb Burn", 4, 'dot', damage=burn_damage)
    crit_text = "✨ **KRITIKAL!** " if is_crit else ""
    return f"💻 {crit_text}**{caster['name']}** mengirim **Cascading Logic Bomb**, memberikan **{damage}** kerusakan dan menanam virus!"

async def protocol_override(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    await _apply_status(session, caster, target, "Heal Block", 3, 'heal_block')
    await _apply_status(session, caster, target, "Protocol Slow", 3, 'debuff', stat='spd', amount='-25%')
    return f"🚫 **{caster['name']}** menggunakan **Protocol Override**, merusak sistem **{target['name']}** dan mencegah pemulihan!"

# --- Venomous Koala (BARU) ---
async def neurotoxin_bloom(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    damage, is_crit = await _apply_damage(session, caster, target, 0.50)
    poison_damage = int(target['max_hp'] * 0.07)
    await _apply_status(session, caster, target, "Neurotoxin", 3, 'dot', damage=poison_damage)
    crit_text = "✨ **KRITIKAL!** " if is_crit else ""
    return f"🐨 {crit_text}**{caster['name']}** melepaskan **Neurotoxin Bloom**, memberikan **{damage}** kerusakan dan meracuni target!"

async def paralyzing_venom(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    damage, is_crit = await _apply_damage(session, caster, target, 0.70)
    crit_text = "✨ **KRITIKAL!** " if is_crit else ""
    log_message = f"🐍 {crit_text}**{caster['name']}** menyuntikkan **Paralyzing Venom**, memberikan **{damage}** kerusakan!"
    
    if random.random() < 0.40:
        await _apply_status(session, caster, target, "Paralyzed", 2, 'paralyze')
        log_message += f"\n> ⚡ **{target['name']}** merasakan sarafnya menegang!"
        
    return log_message

# --- Gilded Rose of Sunstone (BARU) ---
async def caramelized_shot(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    damage, is_crit = await _apply_damage(session, caster, target, 1.15)
    await _apply_status(session, caster, target, "Caramelized", 3, 'debuff', stat='spd', amount='-15%')
    crit_text = "✨ **KRITIKAL!** " if is_crit else ""
    return f"🍮 {crit_text}**{caster['name']}** menembakkan **Caramelized Shot**, memberikan **{damage}** kerusakan dan memperlambat target!"

async def flourish_and_fire(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    await _apply_status(session, caster, caster, "Flourish", 3, 'buff', stat='crit_rate', amount=0.25)
    
    bonus_crit_rate = kwargs.get('bonus_crit_rate', 0.0) + 0.25
    bonus_crit_dmg = kwargs.get('bonus_crit_dmg', 0.0)

    damage, is_crit = await _apply_damage(session, caster, target, 0.75,
                                    bonus_crit_rate=bonus_crit_rate,
                                    bonus_crit_dmg=bonus_crit_dmg)
    crit_text = "✨ **KRITIKAL!** " if is_crit else ""
    return f"🌹 **{caster['name']}** melakukan **Flourish and Fire**, meningkatkan Crit Rate dan langsung menembak, memberikan **{damage}** kerusakan!"

# --- Curse of a Broken Moon (BARU) ---
async def whispers_of_decay(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    await _apply_status(session, caster, target, "Attack Down (Decay)", 3, 'debuff', stat='atk', amount='-20%')
    await _apply_status(session, caster, target, "Defense Down (Decay)", 3, 'debuff', stat='def', amount='-20%')
    return f"🌙 **{caster['name']}** membisikkan **Whispers of Decay**, merapuhkan kekuatan dan pertahanan **{target['name']}**!"

async def blood_price_offering(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    hp_cost = int(caster['hp'] * 0.15)
    caster['hp'] = max(1, caster['hp'] - hp_cost)
    
    damage_amount = int(hp_cost * 2.0)
    damage_dealt, _ = await _apply_damage(session, caster, target, fixed_damage=damage_amount)
    
    bleed_damage = int(target['max_hp'] * 0.04) # Bleed standar
    await _apply_status(session, caster, target, "Blood Price Bleed", 3, 'dot', damage=bleed_damage)
    
    return f"💔 **{caster['name']}** menggunakan **Blood Price Offering**, mengorbankan **{hp_cost}** HP untuk memberikan **{damage_dealt}** kerusakan dan menyebabkan pendarahan!"

# --- Gambler of the Fickle Fate (BARU) ---
async def chaotic_roll(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    roll = random.random()
    if roll < 0.25: # 25% Stun
        await _apply_status(session, caster, target, "Chaotic Stun", 2, 'stun')
        return f"🎲 **Chaotic Roll**! **{caster['name']}** membuat **{target['name']}** pingsan!"
    elif roll < 0.50: # 25% Heal
        is_heal_blocked = any(e.get('type') == 'heal_block' for e in caster.get('status_effects', []))
        if is_heal_blocked:
            return f"🎲 **Chaotic Roll**! **{caster['name']}** mencoba memulihkan diri, tetapi gagal karena efek Heal Block!"
        
        heal_amount = int(caster['max_hp'] * 0.20)
        caster['hp'] = min(caster['max_hp'], caster['hp'] + heal_amount)
        return f"🎲 **Chaotic Roll**! **{caster['name']}** memulihkan **{heal_amount}** HP!"
    else: # 50% Damage
        bonus_crit_rate = kwargs.get('bonus_crit_rate', 0.0)
        bonus_crit_dmg = kwargs.get('bonus_crit_dmg', 0.0)

        damage, is_crit = await _apply_damage(session, caster, target, 1.50,
                                        bonus_crit_rate=bonus_crit_rate,
                                        bonus_crit_dmg=bonus_crit_dmg)
        crit_text = "✨ **KRITIKAL!** " if is_crit else ""
        return f"🎲 **Chaotic Roll**! {crit_text}**{caster['name']}** memberikan **{damage}** kerusakan besar!"

async def all_in(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    if random.random() < 0.50:
        # Sukses: Critical Hit dengan bonus 100% Crit Damage
        damage, _ = await _apply_damage(session, caster, target, 1.0, force_crit=True, bonus_crit_dmg=1.00) # bonus_crit_dmg +100%
        return f"💰 **ALL IN!** Serangan **{caster['name']}** mendarat sempurna, memberikan **{damage}** kerusakan kritikal yang masif!"
    else:
        # Gagal: Meleset
        return f"💸 **ALL IN!** Serangan **{caster['name']}** terlalu berisiko dan meleset sepenuhnya!"

# --- Crimson Lotus Dancer (BARU) ---
async def blade_of_ephemeral_grace(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    damage, is_crit = await _apply_damage(session, caster, target, 1.35)
    crit_text = "✨ **KRITIKAL!** " if is_crit else ""
    log_message = f"🌸 {crit_text}**{caster['name']}** menyerang dengan **Blade of Ephemeral Grace**, memberikan **{damage}** kerusakan!"
    
    if random.random() < 0.50:
        await _apply_status(session, caster, caster, "Ephemeral Grace", 3, 'buff', stat='spd', amount='+20%')
        log_message += f"\n> 💨 Gerakan **{caster['name']}** menjadi lebih cepat!"
        
    return log_message

async def arcane_silence(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    damage, is_crit = await _apply_damage(session, caster, target, 1.00)
    await _apply_status(session, caster, target, "Arcane Silence", 2, 'silence')
    crit_text = "✨ **KRITIKAL!** " if is_crit else ""
    return f"🤫 {crit_text}**{caster['name']}** menggunakan **Arcane Silence**, memberikan **{damage}** kerusakan dan membungkam sihir **{target['name']}**!"

# --- Whisper of the Gilded Cage (BARU) ---
async def golden_shackle(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    damage, is_crit = await _apply_damage(session, caster, target, 0.80)
    crit_text = "✨ **KRITIKAL!** " if is_crit else ""
    log_message = f"⛓️ {crit_text}**{caster['name']}** menggunakan **Golden Shackle**, memberikan **{damage}** kerusakan!"
    
    if random.random() < 0.40:
        await _apply_status(session, caster, target, "Silence", 2, 'silence') # Durasi 2 untuk 1 giliran efektif
        log_message += f"\n> 🔇 **{target['name']}** dibungkam dan tidak bisa menggunakan skill!"
        
    return log_message

async def gilded_prison(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    if random.random() < 0.50:
        await _apply_status(session, caster, target, "Paralyze", 3, 'paralyze') # Durasi 3 untuk 2 giliran efektif
        return f"🏛️ **{caster['name']}** menciptakan **Gilded Prison**, melumpuhkan **{target['name']}**!"
    else:
        return f"💨 **{caster['name']}** mencoba menciptakan **Gilded Prison**, tetapi **{target['name']}** berhasil lolos!"

# --- The Undying Taboo (BARU) ---
async def raise_dead(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    await _apply_status(session, caster, caster, "Summoned Skeleton", 3, 'summon') # Durasi 3 agar aktif selama 2 giliran
    return f"💀 **{caster['name']}** menggunakan **Raise Dead**, membangkitkan sesosok tengkorak dari tanah!"

async def soul_drain(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    bonus_crit_rate = kwargs.get('bonus_crit_rate', 0.0)
    bonus_crit_dmg = kwargs.get('bonus_crit_dmg', 0.0)

    damage, is_crit = await _apply_damage(session, caster, target, 0.70,
                                    bonus_crit_rate=bonus_crit_rate,
                                    bonus_crit_dmg=bonus_crit_dmg)
    
    is_heal_blocked = any(e.get('type') == 'heal_block' for e in caster.get('status_effects', []))
    log_message = ""
    if is_heal_blocked:
        log_message = "\n> ❤️‍🩹 Pemulihan HP gagal karena efek Heal Block!"
    else:
        heal_amount = int(damage * 0.20)
        caster['hp'] = min(caster['max_hp'], caster['hp'] + heal_amount)
        log_message = f" dan memulihkan **{heal_amount}** HP."

    crit_text = "✨ **KRITIKAL!** " if is_crit else ""
    return f"👻 {crit_text}**{caster['name']}** menggunakan **Soul Drain**, memberikan **{damage}** kerusakan{log_message}"

# --- Wail of the Mourning Moon ---
async def crescent_weep(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    damage, is_crit = await _apply_damage(session, caster, target, 0.80)
    crit_text = "✨ **KRITIKAL!** " if is_crit else ""
    log_message = f"🌙 {crit_text}**{caster['name']}** melepaskan **Crescent Weep**, memberikan **{damage}** kerusakan!"
    
    # Peluang 40% untuk memberikan debuff ATK Down
    if random.random() < 0.40:
        await _apply_status(session, caster, target, "Weeping Wound", 3, 'debuff', stat='atk', amount='-15%')
        log_message += f"\n> 💧 Serangan **{target['name']}** melemah!"
        
    return log_message

async def lunar_curse(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    await _apply_status(session, caster, caster, "Lunar Blessing", 3, 'buff', stat='atk', amount='+15%')
    log_message = f"诅 **{caster['name']}** merapal **Lunar Curse**, meningkatkan kekuatan serangannya!"

    # Peluang 75% untuk berhasil memberikan debuff SPD Down
    if random.random() < 0.75:
        await _apply_status(session, caster, target, "Lunar Curse", 4, 'debuff', stat='spd', amount='-20%')
        log_message += f"\n> 🐌 Gerakan **{target['name']}** juga menjadi lambat!"
    else:
        log_message += f"\n> Namun, kutukannya gagal memperlambat **{target['name']}**!"
        
    return log_message

# --- Canvas of the World Tree (BARU) ---
async def vine_lash(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    damage, is_crit = await _apply_damage(session, caster, target, 0.85)
    crit_text = "✨ **KRITIKAL!** " if is_crit else ""
    log_message = f"🌿 {crit_text}**{caster['name']}** menggunakan **Vine Lash**, memberikan **{damage}** kerusakan!"
    
    if random.random() < 0.20:
        await _apply_status(session, caster, target, "Stunned by Vines", 2, 'stun') # Durasi 2 untuk 1 giliran efektif
        log_message += f"\n> 🌱 **{target['name']}** terikat oleh akar dan tidak bisa bergerak!"
        
    return log_message

async def nature_s_blessing(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    heal_per_turn = int(caster['max_hp'] * 0.07)
    await _apply_status(session, caster, caster, "Natures Blessing", 4, 'hot', heal_amount=heal_per_turn) # Durasi 4 untuk 3 giliran efektif
    return f"🌳 **{caster['name']}** menerima **Natures Blessing**, memulihkan HP setiap giliran!"

# --- Smile from the Shadows (BARU) ---
async def shadow_bolt(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    damage, is_crit = await _apply_damage(session, caster, target, 0.95)
    crit_text = "✨ **KRITIKAL!** " if is_crit else ""
    return f"⚫ {crit_text}**{caster['name']}** menembakkan **Shadow Bolt**, memberikan **{damage}** kerusakan!"

async def whispers_of_fear(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    if random.random() < 0.50:
        await _apply_status(session, caster, target, "Fear", 3, 'debuff', miss_chance=0.25) # Durasi 3 untuk 2 giliran efektif
        return f"😨 **{caster['name']}** membisikkan **Whispers of Fear**, membuat **{target['name']}** diliputi rasa takut!"
    else:
        return f"💨 **{caster['name']}** membisikkan **Whispers of Fear**, tetapi **{target['name']}** berhasil menepisnya!"

# --- Fate of the Twin Blades (BARU) ---
async def cross_slash(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    damage1, is_crit1 = await _apply_damage(session, caster, target, 0.60)
    damage2, is_crit2 = await _apply_damage(session, caster, target, 0.60)
    total_damage = damage1 + damage2
    crit_count = (1 if is_crit1 else 0) + (1 if is_crit2 else 0)
    return f"⚔️ **{caster['name']}** menggunakan **Cross Slash**, memberikan 2 serangan dengan total **{total_damage}** kerusakan! ({crit_count} kritikal)"

async def blade_dance(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    await _apply_status(session, caster, caster, "Blade Dance", 3, 'buff', stat='spd', amount='+20%')
    return f"💃 **{caster['name']}** melakukan **Blade Dance**, meningkatkan SPD secara drastis!"

# --- Guardian of the Soul Gate (BARU) ---
async def fel_flame(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    damage, is_crit = await _apply_damage(session, caster, target, 0.75)
    burn_damage = int(caster['stats']['atk'] * 0.05)
    await _apply_status(session, caster, target, "Fel Flame Burn", 3, 'dot', damage=burn_damage) # Durasi 3 untuk 2 turn
    crit_text = "✨ **KRITIKAL!** " if is_crit else ""
    return f"🔥 {crit_text}**{caster['name']}** menembakkan **Fel Flame**, memberikan **{damage}** kerusakan dan membakar jiwa target!"

async def demonic_pact(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    hp_cost = int(caster['hp'] * 0.10)
    caster['hp'] = max(1, caster['hp'] - hp_cost)
    await _apply_status(session, caster, caster, "Demonic Pact", 2, 'buff', stat='atk', amount='+30%') # Durasi 2 untuk 1 turn efektif
    return f"😈 **{caster['name']}** membuat **Demonic Pact**, mengorbankan **{hp_cost}** HP untuk kekuatan yang lebih besar!"

# --- The Pixelated Prodigy (BARU) ---
async def button_mash(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    total_damage = 0
    crit_count = 0
    for _ in range(3):
        damage, is_crit = await _apply_damage(session, caster, target, 0.35)
        total_damage += damage
        if is_crit:
            crit_count += 1
    return f"👾 **{caster['name']}** melakukan **Button Mash**, memberikan 3 serangan dengan total **{total_damage}** kerusakan! ({crit_count} kritikal)"

async def rage_quit(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    if (caster['hp'] / caster['max_hp']) > 0.25:
        return f"🤬 **{caster['name']}** mencoba menggunakan **Rage Quit**, tetapi HP-nya masih di atas 25%!"

    damage, is_crit = await _apply_damage(session, caster, target, 1.50)
    await _apply_status(session, caster, caster, "Stunned (Rage Quit)", 2, 'stun') # Stun diri sendiri, durasi 2 agar efektif 1 giliran
    crit_text = "✨ **KRITIKAL!** " if is_crit else ""
    return f"🤬 {crit_text}**{caster['name']}** menggunakan **Rage Quit**, memberikan **{damage}** kerusakan besar tetapi akan terkena stun!"

# --- Blessing of the Food Goddess (BARU) ---
async def spicy_dish(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    damage, is_crit = await _apply_damage(session, caster, target, 0.80)
    # Efek burn akan memberikan damage setara 10% ATK caster setiap giliran
    burn_damage = int(caster['stats']['atk'] * 0.10)
    await _apply_status(session, caster, target, "Burn (Spicy)", 3, 'dot', damage=burn_damage) # Durasi 3 agar efektif 2 giliran
    crit_text = "✨ **KRITIKAL!** " if is_crit else ""
    return f"🌶️ {crit_text}**{caster['name']}** melempar **Spicy Dish**, memberikan **{damage}** kerusakan dan membakar target!"

async def hearty_meal(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    is_heal_blocked = any(e.get('type') == 'heal_block' for e in caster.get('status_effects', []))
    if is_heal_blocked:
        return f"❤️‍🩹 **{caster['name']}** mencoba memakan **Hearty Meal**, tetapi gagal karena efek Heal Block!"

    heal_amount = int(caster['max_hp'] * 0.25)

    # [IMPLEMENTASI BARU] Terapkan efek agensi sebelum pasif lain
    if caster.get('agency_id') == 'projectabyssal':
        heal_amount = int(heal_amount * 0.9)
    
    final_heal = heal_amount
    if _has_passive(caster, "Steadfast Faith"):
        final_heal = passive_implementations['Steadfast Faith'](session, caster, heal_amount)

    caster['hp'] = min(caster['max_hp'], caster['hp'] + final_heal)
    return f"🍲 **{caster['name']}** memakan **Hearty Meal**, memulihkan **{final_heal}** HP!"

# --- Nameless Blade ---
async def first_cut(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    bonus_crit_rate = kwargs.get('bonus_crit_rate', 0.0)
    bonus_crit_dmg = kwargs.get('bonus_crit_dmg', 0.0)
    
    damage, is_crit = await _apply_damage(session, caster, target, 1.10,
                                    bonus_crit_rate=bonus_crit_rate,
                                    bonus_crit_dmg=bonus_crit_dmg)
    crit_text = "✨ **KRITIKAL!** " if is_crit else ""
    return f"🔪 {crit_text}**{caster['name']}** menggunakan **First Cut**, memberikan **{damage}** kerusakan!"

    
async def steady_guard(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    await _apply_status(session, caster, caster, "Steady Guard", duration=2, effect_type='buff', stat='def', amount='+15%')
    return f"🛡️ **{caster['name']}** menggunakan **Steady Guard**, meningkatkan DEF!"

# --- Altars Whisper ---
async def mending_light(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    is_heal_blocked = any(e.get('type') == 'heal_block' for e in caster.get('status_effects', []))
    if is_heal_blocked:
        return f"❤️‍🩹 **{caster['name']}** mencoba menggunakan **Mending Light**, tetapi gagal karena efek Heal Block!"

    heal_amount = int(caster['max_hp'] * 0.15)
    
    # [IMPLEMENTASI BARU] Terapkan efek agensi sebelum pasif lain
    if caster.get('agency_id') == 'projectabyssal':
        heal_amount = int(heal_amount * 0.9)

    final_heal = heal_amount
    if _has_passive(caster, "Steadfast Faith"):
        final_heal = passive_implementations['Steadfast Faith'](session, caster, heal_amount)
    
    caster['hp'] = min(caster['max_hp'], caster['hp'] + final_heal)
    return f"💖 **{caster['name']}** menggunakan **Mending Light**, memulihkan **{final_heal}** HP!"

async def hallowed_ward(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    shield_amount = int(caster['max_hp'] * 0.10)
    await _apply_status(session, caster, caster, "Hallowed Ward", duration=99, effect_type='shield', shield_hp=shield_amount)
    return f"🌟 **{caster['name']}** menggunakan **Hallowed Ward**, menciptakan perisai sebesar **{shield_amount}** HP!"

# --- Leafs Shadow ---
async def swift_strike(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    bonus_crit_rate = kwargs.get('bonus_crit_rate', 0.0)
    bonus_crit_dmg = kwargs.get('bonus_crit_dmg', 0.0)

    damage, is_crit = await _apply_damage(session, caster, target, 1.05,
                                    bonus_crit_rate=bonus_crit_rate,
                                    bonus_crit_dmg=bonus_crit_dmg)
    if damage == 0:
        return f"🍃 **{target['name']}** berhasil menghindari **Swift Strike** dari **{caster['name']}**!"
    crit_text = "✨ **KRITIKAL!** " if is_crit else ""
    return f"💨 {crit_text}**{caster['name']}** menggunakan **Swift Strike**, memberikan **{damage}** kerusakan!"

    
async def wind_step(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    await _apply_status(session, caster, caster, "Wind Step", duration=2, effect_type='buff', stat='spd', amount='+20%')
    return f"🌬️ **{caster['name']}** menggunakan **Wind Step**, meningkatkan SPD!"

# --- First Spark ---
async def ember_cast(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    bonus_crit_rate = kwargs.get('bonus_crit_rate', 0.0)
    bonus_crit_dmg = kwargs.get('bonus_crit_dmg', 0.0)

    damage, is_crit = await _apply_damage(session, caster, target, 1.20,
                                    bonus_crit_rate=bonus_crit_rate,
                                    bonus_crit_dmg=bonus_crit_dmg)
    if damage == 0:
        return f"🍃 **{target['name']}** berhasil menghindari **Ember Cast** dari **{caster['name']}**!"
    crit_text = "✨ **KRITIKAL!** " if is_crit else ""
    return f"🔥 {crit_text}**{caster['name']}** merapal **Ember Cast**, memberikan **{damage}** kerusakan sihir!"

    
async def fading_curse(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    await _apply_status(session, caster, target, "Fading Curse", duration=2, effect_type='debuff', stat='atk', amount='-10%')
    return f"📉 **{caster['name']}** merapal **Fading Curse**, mengurangi ATK **{target['name']}**!"

# --- Concrete Will ---
async def heavy_blow(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    bonus_crit_rate = kwargs.get('bonus_crit_rate', 0.0)
    bonus_crit_dmg = kwargs.get('bonus_crit_dmg', 0.0)

    damage, is_crit = await _apply_damage(session, caster, target, 1.30,
                                    bonus_crit_rate=bonus_crit_rate,
                                    bonus_crit_dmg=bonus_crit_dmg)
    if damage == 0:
        return f"🍃 **{target['name']}** berhasil menghindari **Heavy Blow** dari **{caster['name']}**!"
    crit_text = "✨ **KRITIKAL!** " if is_crit else ""
    return f"👊 {crit_text}**{caster['name']}** melancarkan **Heavy Blow**, memberikan **{damage}** kerusakan!"

async def iron_resolve(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    await _apply_status(session, caster, caster, "Iron Resolve", duration=2, effect_type='buff', damage_reduction=0.30)
    return f"굳 **{caster['name']}** menggunakan **Iron Resolve**, mengeraskan diri untuk serangan berikutnya!"


# --- Enemy Skills (Baru) ---
# --- Wanderer ---
async def silent_strike(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    # Gabungkan bonus crit dari buff dengan bonus bawaan skill
    bonus_crit_rate = kwargs.get('bonus_crit_rate', 0.0) + 0.20
    bonus_crit_dmg = kwargs.get('bonus_crit_dmg', 0.0)
    
    damage, is_crit = await _apply_damage(session, caster, target, 1.50, 
                                    bonus_crit_rate=bonus_crit_rate, bonus_crit_dmg=bonus_crit_dmg)
    crit = "✨ **KRITIKAL!** " if is_crit else ""
    return f"💨 {crit}**{caster['name']}** menggunakan **Silent Strike**, memberikan **{damage}** kerusakan!"

async def flowing_blade(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    bonus_crit_rate = kwargs.get('bonus_crit_rate', 0.0)
    bonus_crit_dmg = kwargs.get('bonus_crit_dmg', 0.0)
    
    damage, is_crit = await _apply_damage(session, caster, target, 1.20,
                                    bonus_crit_rate=bonus_crit_rate, bonus_crit_dmg=bonus_crit_dmg)
    spd_reduction = -int(target['stats'].get('spd', 10) * 0.15)
    await _apply_status(session, caster, target, "Slowed", 2, 'debuff', stat='spd', amount=spd_reduction)
    crit = "✨ **KRITIKAL!** " if is_crit else ""
    return f"🌊 {crit}**{caster['name']}** menggunakan **Flowing Blade**, memberikan **{damage}** kerusakan dan memperlambat target!"

# --- Puppet Knight ---
async def mechanical_slash(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    bonus_crit_rate = kwargs.get('bonus_crit_rate', 0.0)
    bonus_crit_dmg = kwargs.get('bonus_crit_dmg', 0.0)

    damage, is_crit = await _apply_damage(session, caster, target, 1.60,
                                    bonus_crit_rate=bonus_crit_rate, bonus_crit_dmg=bonus_crit_dmg)
    crit = "✨ **KRITIKAL!** " if is_crit else ""
    return f"⚙️ {crit}**{caster['name']}** menggunakan **Mechanical Slash**, memberikan **{damage}** kerusakan!"

async def core_overload(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    bonus_crit_rate = kwargs.get('bonus_crit_rate', 0.0)
    bonus_crit_dmg = kwargs.get('bonus_crit_dmg', 0.0)

    recoil_damage = int(caster['max_hp'] * 0.15)
    caster['hp'] = max(0, caster['hp'] - recoil_damage)
    damage, is_crit = await _apply_damage(session, caster, target, 1.80,
                                    bonus_crit_rate=bonus_crit_rate, bonus_crit_dmg=bonus_crit_dmg)
    crit = "✨ **KRITIKAL!** " if is_crit else ""
    return f"💥 {crit}**{caster['name']}** menggunakan **Core Overload**, memberikan **{damage}** kerusakan dan menerima **{recoil_damage}** recoil!"

# --- Berserker ---
async def vengeful_fist(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    bonus_crit_rate = kwargs.get('bonus_crit_rate', 0.0)
    bonus_crit_dmg = kwargs.get('bonus_crit_dmg', 0.0)

    damage, is_crit = await _apply_damage(session, caster, target, 1.70, ignores_def_percent=0.15,
                                    bonus_crit_rate=bonus_crit_rate, bonus_crit_dmg=bonus_crit_dmg)
    crit = "✨ **KRITIKAL!** " if is_crit else ""
    return f"👊 {crit}**{caster['name']}** menggunakan **Vengeful Fist**, memberikan **{damage}** kerusakan!"

async def savage_rampage(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    bonus_crit_rate = kwargs.get('bonus_crit_rate', 0.0)
    bonus_crit_dmg = kwargs.get('bonus_crit_dmg', 0.0)

    recoil_damage = int(caster['max_hp'] * 0.10)
    caster['hp'] = max(0, caster['hp'] - recoil_damage)
    damage, is_crit = await _apply_damage(session, caster, target, 2.00,
                                    bonus_crit_rate=bonus_crit_rate, bonus_crit_dmg=bonus_crit_dmg)
    crit = "✨ **KRITIKAL!** " if is_crit else ""
    return f"😡 {crit}**{caster['name']}** mengamuk dengan **Savage Rampage**, memberikan **{damage}** kerusakan dan menerima **{recoil_damage}** recoil!"

# --- Forest Reaper ---
async def root_bind(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    bonus_crit_rate = kwargs.get('bonus_crit_rate', 0.0)
    bonus_crit_dmg = kwargs.get('bonus_crit_dmg', 0.0)

    damage, is_crit = await _apply_damage(session, caster, target, 1.20,
                                    bonus_crit_rate=bonus_crit_rate, bonus_crit_dmg=bonus_crit_dmg)
    await _apply_status(session, caster, target, "Rooted", 2, 'stun')
    crit = "✨ **KRITIKAL!** " if is_crit else ""
    return f"🌲 {crit}**{caster['name']}** menggunakan **Root Bind**, memberikan **{damage}** kerusakan dan mengikat target!"

async def concentrated_venom(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    bonus_crit_rate = kwargs.get('bonus_crit_rate', 0.0)
    bonus_crit_dmg = kwargs.get('bonus_crit_dmg', 0.0)

    damage, is_crit = await _apply_damage(session, caster, target, 0.90,
                                    bonus_crit_rate=bonus_crit_rate, bonus_crit_dmg=bonus_crit_dmg)
    dot_damage = int(target['max_hp'] * 0.05)
    await _apply_status(session, caster, target, "Poisoned", 3, 'dot', damage=dot_damage)
    crit = "✨ **KRITIKAL!** " if is_crit else ""
    return f"🧪 {crit}**{caster['name']}** menggunakan **Concentrated Venom**, memberikan **{damage}** kerusakan dan meracuni target!"

# --- Dark Champion ---
async def arenas_cleave(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    bonus_crit_rate = kwargs.get('bonus_crit_rate', 0.0)
    bonus_crit_dmg = kwargs.get('bonus_crit_dmg', 0.0)
    
    bonus_damage_from_def = int(caster['stats'].get('def', 0) * 0.10)
    base_damage, is_crit = await _apply_damage(session, caster, target, 1.70,
                                         bonus_crit_rate=bonus_crit_rate, bonus_crit_dmg=bonus_crit_dmg)
    final_damage = base_damage + bonus_damage_from_def
    target['hp'] = max(0, target['hp'] - bonus_damage_from_def)
    crit = "✨ **KRITIKAL!** " if is_crit else ""
    return f"⚔️ {crit}**{caster['name']}** menggunakan **Arenas Cleave**, memberikan total **{final_damage}** kerusakan!"
    
async def finishing_blow(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    bonus_crit_rate = kwargs.get('bonus_crit_rate', 0.0) + 0.25
    bonus_crit_dmg = kwargs.get('bonus_crit_dmg', 0.0)

    damage, is_crit = await _apply_damage(session, caster, target, 1.50, 
                                    bonus_crit_rate=bonus_crit_rate, bonus_crit_dmg=bonus_crit_dmg)
    crit = "✨ **KRITIKAL!** " if is_crit else ""
    return f"🎯 {crit}**{caster['name']}** menggunakan **Finishing Blow**, memberikan **{damage}** kerusakan!"

# --- Swordsman Phantom ---
async def shadow_slash(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    bonus_crit_rate = kwargs.get('bonus_crit_rate', 0.0)
    bonus_crit_dmg = kwargs.get('bonus_crit_dmg', 0.0)

    damage, is_crit = await _apply_damage(session, caster, target, 1.50,
                                    bonus_crit_rate=bonus_crit_rate, bonus_crit_dmg=bonus_crit_dmg)
    
    log_message = ""
    is_heal_blocked = any(e.get('type') == 'heal_block' for e in caster.get('status_effects', []))
    if is_heal_blocked:
        log_message = " tetapi pemulihan HP gagal karena Heal Block!"
    else:
        lifesteal_amount = int(damage * 0.20)
        caster['hp'] = min(caster['max_hp'], caster['hp'] + lifesteal_amount)
        log_message = f" dan memulihkan **{lifesteal_amount}** HP!"

    crit = "✨ **KRITIKAL!** " if is_crit else ""
    return f"👻 {crit}**{caster['name']}** menggunakan **Shadow Slash**, memberikan **{damage}** kerusakan{log_message}"

async def splitting_shadow(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    bonus_crit_rate = kwargs.get('bonus_crit_rate', 0.0)
    bonus_crit_dmg = kwargs.get('bonus_crit_dmg', 0.0)

    damage1, is_crit1 = await _apply_damage(session, caster, target, 0.80, bonus_crit_rate=bonus_crit_rate, bonus_crit_dmg=bonus_crit_dmg)
    damage2, is_crit2 = await _apply_damage(session, caster, target, 0.80, bonus_crit_rate=bonus_crit_rate, bonus_crit_dmg=bonus_crit_dmg)
    total_damage = damage1 + damage2
    crit_count = (1 if is_crit1 else 0) + (1 if is_crit2 else 0)
    return f"👥 **{caster['name']}** menggunakan **Splitting Shadow**, memberikan 2 serangan dengan total **{total_damage}** kerusakan! ({crit_count} kritikal)"

# --- Blade Dancer ---
async def crimson_edge(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    bonus_crit_rate = kwargs.get('bonus_crit_rate', 0.0)
    bonus_crit_dmg = kwargs.get('bonus_crit_dmg', 0.0)

    damage, is_crit = await _apply_damage(session, caster, target, 1.10,
                                    bonus_crit_rate=bonus_crit_rate, bonus_crit_dmg=bonus_crit_dmg)
    bleed_damage = int(target['max_hp'] * 0.04)
    await _apply_status(session, caster, target, "Bleeding", 3, 'dot', damage=bleed_damage)
    crit = "✨ **KRITIKAL!** " if is_crit else ""
    return f"🩸 {crit}**{caster['name']}** menggunakan **Crimson Edge**, memberikan **{damage}** kerusakan dan menyebabkan pendarahan!"

async def blade_fury(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    bonus_crit_rate = kwargs.get('bonus_crit_rate', 0.0) + 0.10
    bonus_crit_dmg = kwargs.get('bonus_crit_dmg', 0.0)

    total_damage = 0
    crit_count = 0
    for _ in range(3):
        damage, is_crit = await _apply_damage(session, caster, target, 0.60, 
                                        bonus_crit_rate=bonus_crit_rate, bonus_crit_dmg=bonus_crit_dmg)
        total_damage += damage
        if is_crit: crit_count += 1
    return f"💃 **{caster['name']}** menari dengan **Blade Fury**, memberikan 3 serangan dengan total **{total_damage}** kerusakan! ({crit_count} kritikal)"

# Corrupt Alchemist
async def poison_vial(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    bonus_crit_rate = kwargs.get('bonus_crit_rate', 0.0)
    bonus_crit_dmg = kwargs.get('bonus_crit_dmg', 0.0)

    damage, is_crit = await _apply_damage(session, caster, target, 0.90,
                                    bonus_crit_rate=bonus_crit_rate, bonus_crit_dmg=bonus_crit_dmg)
    poison_damage = int(target['max_hp'] * 0.10)
    await _apply_status(session, caster, target, "Strong Poison", 3, 'dot', damage=poison_damage)
    crit = "✨ **KRITIKAL!** " if is_crit else ""
    return f"☠️ {crit}**{caster['name']}** melempar **Poison Vial**, memberikan **{damage}** kerusakan dan meracuni target dengan kuat!"

async def acid_splash(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    bonus_crit_rate = kwargs.get('bonus_crit_rate', 0.0)
    bonus_crit_dmg = kwargs.get('bonus_crit_dmg', 0.0)
    
    damage, is_crit = await _apply_damage(session, caster, target, 1.20,
                                    bonus_crit_rate=bonus_crit_rate, bonus_crit_dmg=bonus_crit_dmg)
    def_reduction = -int(target['stats'].get('def', 5) * 0.15)
    await _apply_status(session, caster, target, "Defense Down", 3, 'debuff', stat='def', amount=def_reduction)
    crit = "✨ **KRITIKAL!** " if is_crit else ""
    return f"⚗️ {crit}**{caster['name']}** menyiram **Acid Splash**, memberikan **{damage}** kerusakan dan mengurangi DEF target!"

# Thorn Witch
async def binding_thorns(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    damage, is_crit = await _apply_damage(session, caster, target, 1.00)
    await _apply_status(session, caster, target, "Rooted", 2, 'stun')
    crit = "✨ **KRITIKAL!** " if is_crit else ""
    return f"🥀 {crit}**{caster['name']}** menggunakan **Binding Thorns**, memberikan **{damage}** kerusakan dan mengikat target!"

async def wilted_rose(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    damage, is_crit = await _apply_damage(session, caster, target, 0.80)
    poison_damage = int(target['max_hp'] * 0.10)
    await _apply_status(session, caster, target, "Wilted Rose Curse", 3, 'dot', damage=poison_damage)
    crit = "✨ **KRITIKAL!** " if is_crit else ""
    return f"🌹 {crit}**{caster['name']}** mengutuk dengan **Wilted Rose**, memberikan **{damage}** kerusakan dan meracuni target!"

# Chain Warden
async def prison_chain(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    bonus_damage = int(caster['stats'].get('def', 0) * 0.20)
    base_dmg, is_crit = await _apply_damage(session, caster, target, 1.0)
    total_damage = base_dmg + bonus_damage
    target['hp'] = max(0, target['hp'] - bonus_damage)
    
    spd_reduction = -int(target['stats'].get('spd', 10) * 0.25)
    await _apply_status(session, caster, target, "Chained", 3, 'debuff', stat='spd', amount=spd_reduction)
    crit = "✨ **KRITIKAL!** " if is_crit else ""
    return f"🔗 {crit}**{caster['name']}** melempar **Prison Chain**, memberikan **{total_damage}** kerusakan dan memperlambat target!"

async def drag_to_the_abyss(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    damage, is_crit = await _apply_damage(session, caster, target, 1.80)
    if random.random() < 0.25:
        await _apply_status(session, caster, target, "Stunned", 2, 'stun')
        session.log.append(f"🥶 **{target['name']}** pingsan!")
    crit = "✨ **KRITIKAL!** " if is_crit else ""
    return f"⛓️ {crit}**{caster['name']}** menggunakan **Drag to the Abyss**, memberikan **{damage}** kerusakan!"

# Sun Sentinel
async def burning_light(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    damage, is_crit = await _apply_damage(session, caster, target, 1.60)
    burn_damage = int(target['max_hp'] * 0.05)
    await _apply_status(session, caster, target, "Burned", 3, 'dot', damage=burn_damage)
    crit = "✨ **KRITIKAL!** " if is_crit else ""
    return f"☀️ {crit}**{caster['name']}** menembakkan **Burning Light**, memberikan **{damage}** kerusakan dan membakar target!"

async def solar_spear(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    bonus_damage = int(caster['stats'].get('def', 0) * 0.10)
    base_dmg, is_crit = await _apply_damage(session, caster, target, 1.80)
    total_damage = base_dmg + bonus_damage
    target['hp'] = max(0, target['hp'] - bonus_damage)
    crit = "✨ **KRITIKAL!** " if is_crit else ""
    return f"🌞 {crit}**{caster['name']}** menghantam dengan **Solar Spear**, memberikan **{total_damage}** kerusakan!"

# Ghost Captain
async def cursed_cannonball(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    damage, is_crit = await _apply_damage(session, caster, target, 1.70, ignores_def_percent=0.15)
    crit = "✨ **KRITIKAL!** " if is_crit else ""
    return f"💣 {crit}**{caster['name']}** menembakkan **Cursed Cannonball**, menembus pertahanan dan memberikan **{damage}** kerusakan!"

async def deep_sea_curse(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    damage, is_crit = await _apply_damage(session, caster, target, 1.30)
    await _apply_status(session, caster, target, "Deep Sea Curse", 2, 'debuff', stat='atk', amount='-20%')
    crit = "✨ **KRITIKAL!** " if is_crit else ""
    return f"🌊 {crit}**{caster['name']}** melepaskan **Deep Sea Curse**, memberikan **{damage}** kerusakan dan melemahkan serangan target!"

# Doppelgänger
async def lifes_mirror(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    target_skills = [s for s in target.get('raw_title_data', {}).get('skills', []) if s['type'] == 'active']
    if not target_skills:
        return f"🎭 **{caster['name']}** mencoba meniru, tetapi **{target['name']}** tidak memiliki skill aktif untuk ditiru!"
    
    copied_skill = random.choice(target_skills)
    skill_func = skill_implementations.get(copied_skill['name'])

    if not skill_func:
         return f"🎭 **{caster['name']}** mencoba meniru **{copied_skill['name']}**, tetapi gagal!"

    # Simpan stat ATK asli, kurangi, lalu kembalikan
    original_atk = caster['stats']['atk']
    caster['stats']['atk'] = int(original_atk * 0.50)
    
    log_message = await skill_func(session, caster, target)
    
    caster['stats']['atk'] = original_atk # Kembalikan stat
    
    return f"🎭 **{caster['name']}** menggunakan **Lifes Mirror**, meniru **{copied_skill['name']}** dengan 50% kekuatan!\n> {log_message}"

async def dual_face(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    bonus_crit_rate = kwargs.get('bonus_crit_rate', 0.0)
    bonus_crit_dmg = kwargs.get('bonus_crit_dmg', 0.0)

    damage, is_crit = await _apply_damage(session, caster, target, 1.30,
                                    bonus_crit_rate=bonus_crit_rate, bonus_crit_dmg=bonus_crit_dmg)
    crit = "✨ **KRITIKAL!** " if is_crit else ""
    return f"🎭 {crit}**{caster['name']}** melancarkan serangan **Dual Face** yang tak terduga, memberikan **{damage}** kerusakan!"

# Flame Revenant
async def lava_burst(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    bonus_crit_rate = kwargs.get('bonus_crit_rate', 0.0)
    bonus_crit_dmg = kwargs.get('bonus_crit_dmg', 0.0)

    damage, is_crit = await _apply_damage(session, caster, target, 2.00,
                                    bonus_crit_rate=bonus_crit_rate, bonus_crit_dmg=bonus_crit_dmg)
    await _apply_status(session, caster, target, "Burn (Lava)", 2, 'dot', damage=int(caster['stats']['atk'] * 0.20))
    crit = "✨ **KRITIKAL!** " if is_crit else ""
    return f"🌋 {crit}**{caster['name']}** meledakkan **Lava Burst**, memberikan **{damage}** kerusakan dan menyebabkan luka bakar parah!"

async def incinerate(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    bonus_crit_rate = kwargs.get('bonus_crit_rate', 0.0)
    bonus_crit_dmg = kwargs.get('bonus_crit_dmg', 0.0)

    damage, is_crit = await _apply_damage(session, caster, target, 1.40,
                                    bonus_crit_rate=bonus_crit_rate, bonus_crit_dmg=bonus_crit_dmg)
    await _apply_status(session, caster, target, "Burn", 1, 'dot', damage=int(caster['stats']['atk'] * 0.15))
    crit = "✨ **KRITIKAL!** " if is_crit else ""
    return f"🔥 {crit}**{caster['name']}** menembakkan **Incinerate**, memberikan **{damage}** kerusakan dan membakar target!"

# Dark Bard
async def draining_note(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    bonus_crit_rate = kwargs.get('bonus_crit_rate', 0.0)
    bonus_crit_dmg = kwargs.get('bonus_crit_dmg', 0.0)

    damage, is_crit = await _apply_damage(session, caster, target, 1.00,
                                    bonus_crit_rate=bonus_crit_rate, bonus_crit_dmg=bonus_crit_dmg)
    await _apply_status(session, caster, target, "Draining Note", 2, 'debuff', stat='atk', amount='-20%')
    crit = "✨ **KRITIKAL!** " if is_crit else ""
    return f"🎵 {crit}**{caster['name']}** memainkan **Draining Note**, memberikan **{damage}** kerusakan dan melemahkan target!"

async def lullaby_of_nightmares(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    bonus_crit_rate = kwargs.get('bonus_crit_rate', 0.0)
    bonus_crit_dmg = kwargs.get('bonus_crit_dmg', 0.0)

    damage, is_crit = await _apply_damage(session, caster, target, 1.20,
                                    bonus_crit_rate=bonus_crit_rate, bonus_crit_dmg=bonus_crit_dmg)
    crit_text = "✨ **KRITIKAL!** " if is_crit else ""
    log = f"🎶 {crit_text}**{caster['name']}** menyanyikan **Lullaby of Nightmares**, memberikan **{damage}** kerusakan!"

    if random.random() < 0.30:
        await _apply_status(session, caster, target, "Sleep", 2, 'stun')
        log += f"\n> 😴 **{target['name']}** tertidur lelap!"
    return log

# Fog Lord
async def blinding_mist(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    bonus_crit_rate = kwargs.get('bonus_crit_rate', 0.0)
    bonus_crit_dmg = kwargs.get('bonus_crit_dmg', 0.0)

    damage, is_crit = await _apply_damage(session, caster, target, 0.90,
                                    bonus_crit_rate=bonus_crit_rate, bonus_crit_dmg=bonus_crit_dmg)
    await _apply_status(session, caster, target, "Blinded", 2, 'debuff', miss_chance=0.20)
    crit = "✨ **KRITIKAL!** " if is_crit else ""
    return f"🌫️ {crit}**{caster['name']}** meniupkan **Blinding Mist**, memberikan **{damage}** kerusakan dan mengganggu penglihatan target!"

async def hand_of_fog(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    bonus_crit_rate = kwargs.get('bonus_crit_rate', 0.0)
    bonus_crit_dmg = kwargs.get('bonus_crit_dmg', 0.0)

    damage, is_crit = await _apply_damage(session, caster, target, 1.50,
                                    bonus_crit_rate=bonus_crit_rate, bonus_crit_dmg=bonus_crit_dmg)
    await _apply_status(session, caster, target, "Slowed by Fog", 2, 'debuff', stat='spd', amount='-15%')
    crit = "✨ **KRITIKAL!** " if is_crit else ""
    return f"✋ {crit}**{caster['name']}** menggunakan **Hand of Fog**, memberikan **{damage}** kerusakan dan memperlambat target!"

# Shadow Dragon
async def dark_claw(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    bonus_crit_rate = kwargs.get('bonus_crit_rate', 0.0)
    # Gabungkan bonus crit damage dari buff dengan bonus bawaan skill
    bonus_crit_dmg = kwargs.get('bonus_crit_dmg', 0.0) + 0.15

    damage, is_crit = await _apply_damage(session, caster, target, 1.80, 
                                    bonus_crit_rate=bonus_crit_rate, bonus_crit_dmg=bonus_crit_dmg)
    crit = "✨ **KRITIKAL!** " if is_crit else ""
    return f"🐉 {crit}**{caster['name']}** mencakar dengan **Dark Claw**, memberikan **{damage}** kerusakan!"

async def shadow_breath(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    bonus_crit_rate = kwargs.get('bonus_crit_rate', 0.0)
    bonus_crit_dmg = kwargs.get('bonus_crit_dmg', 0.0)

    damage, is_crit = await _apply_damage(session, caster, target, 1.10,
                                    bonus_crit_rate=bonus_crit_rate, bonus_crit_dmg=bonus_crit_dmg)
    await _apply_status(session, caster, target, "Blinded", 2, 'debuff', miss_chance=0.20)
    crit = "✨ **KRITIKAL!** " if is_crit else ""
    return f"💨 {crit}**{caster['name']}** menghembuskan **Shadow Breath**, memberikan **{damage}** kerusakan dan membutakan target!"

# Fallen Emperor
async def imperial_sword(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    bonus_crit_rate = kwargs.get('bonus_crit_rate', 0.0)
    bonus_crit_dmg = kwargs.get('bonus_crit_dmg', 0.0)

    damage, is_crit = await _apply_damage(session, caster, target, 1.90,
                                    bonus_crit_rate=bonus_crit_rate, bonus_crit_dmg=bonus_crit_dmg)
    crit = "✨ **KRITIKAL!** " if is_crit else ""
    return f"👑 {crit}**{caster['name']}** menebas dengan **Imperial Sword**, memberikan **{damage}** kerusakan agung!"

async def decree_of_ruin(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    bonus_crit_rate = kwargs.get('bonus_crit_rate', 0.0)
    bonus_crit_dmg = kwargs.get('bonus_crit_dmg', 0.0)

    damage, is_crit = await _apply_damage(session, caster, target, 1.40,
                                    bonus_crit_rate=bonus_crit_rate, bonus_crit_dmg=bonus_crit_dmg)
    await _apply_status(session, caster, target, "Defense Broken", 2, 'debuff', stat='def', amount='-15%')
    crit = "✨ **KRITIKAL!** " if is_crit else ""
    return f"📜 {crit}**{caster['name']}** mengeluarkan **Decree of Ruin**, memberikan **{damage}** kerusakan dan meremukkan pertahanan!"

# Cult Priest
async def blood_offering(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    is_heal_blocked = any(e.get('type') == 'heal_block' for e in caster.get('status_effects', []))
    recoil = int(caster['max_hp'] * 0.15)
    caster['hp'] = max(0, caster['hp'] - recoil)
    
    if is_heal_blocked:
        return f"🩸 **{caster['name']}** menggunakan **Blood Offering**, mengorbankan **{recoil}** HP, tetapi pemulihan gagal karena Heal Block!"
    
    heal = int(caster['max_hp'] * 0.30)
    caster['hp'] = min(caster['max_hp'], caster['hp'] + heal)
    return f"🩸 **{caster['name']}** menggunakan **Blood Offering**, mengorbankan **{recoil}** HP untuk memulihkan **{heal}** HP!"

async def ivory_curse(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    bonus_crit_rate = kwargs.get('bonus_crit_rate', 0.0)
    bonus_crit_dmg = kwargs.get('bonus_crit_dmg', 0.0)

    damage, is_crit = await _apply_damage(session, caster, target, 1.30,
                                    bonus_crit_rate=bonus_crit_rate, bonus_crit_dmg=bonus_crit_dmg)
    await _apply_status(session, caster, target, "Defense Cursed", 2, 'debuff', stat='def', amount='-10%')
    crit = "✨ **KRITIKAL!** " if is_crit else ""
    return f"💀 {crit}**{caster['name']}** merapal **Ivory Curse**, memberikan **{damage}** kerusakan dan merapuhkan pertahanan!"

# Fate Weaver
async def thread_of_life(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    drain_amount = int(target['hp'] * 0.15)
    target['hp'] = max(0, target['hp'] - drain_amount)
    
    is_heal_blocked = any(e.get('type') == 'heal_block' for e in caster.get('status_effects', []))
    if is_heal_blocked:
        return f"🧵 **{caster['name']}** menarik **Thread of Life** dan memberikan **{drain_amount}** kerusakan, tetapi pemulihan gagal karena Heal Block!"
    
    caster['hp'] = min(caster['max_hp'], caster['hp'] + drain_amount)
    return f"🧵 **{caster['name']}** menarik **Thread of Life**, menyerap **{drain_amount}** HP dari **{target['name']}**!"


async def self_mending(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    is_heal_blocked = any(e.get('type') == 'heal_block' for e in caster.get('status_effects', []))
    if is_heal_blocked:
        return f"🧶 **{caster['name']}** mencoba menggunakan **Self Mending**, tetapi gagal karena efek Heal Block!"
        
    heal_amount = int(caster['max_hp'] * 0.25)
    caster['hp'] = min(caster['max_hp'], caster['hp'] + heal_amount)
    return f"🧶 **{caster['name']}** menggunakan **Self Mending**, memulihkan **{heal_amount}** HP."

# Abyss Guardian
async def abyss_strike(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    bonus_crit_rate = kwargs.get('bonus_crit_rate', 0.0)
    bonus_crit_dmg = kwargs.get('bonus_crit_dmg', 0.0)

    damage, is_crit = await _apply_damage(session, caster, target, 2.00, ignores_def_percent=0.15,
                                    bonus_crit_rate=bonus_crit_rate, bonus_crit_dmg=bonus_crit_dmg)
    crit = "✨ **KRITIKAL!** " if is_crit else ""
    return f"💥 {crit}**{caster['name']}** melancarkan **Abyss Strike**, memberikan **{damage}** kerusakan!"

async def hellfire_chains(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    bonus_crit_rate = kwargs.get('bonus_crit_rate', 0.0)
    bonus_crit_dmg = kwargs.get('bonus_crit_dmg', 0.0)

    damage, is_crit = await _apply_damage(session, caster, target, 1.20,
                                    bonus_crit_rate=bonus_crit_rate, bonus_crit_dmg=bonus_crit_dmg)
    await _apply_status(session, caster, target, "Burn (Hellfire)", 2, 'dot', damage=int(caster['stats']['atk'] * 0.10))
    await _apply_status(session, caster, target, "Poison (Hellfire)", 2, 'dot', damage=int(caster['stats']['atk'] * 0.10))
    crit = "✨ **KRITIKAL!** " if is_crit else ""
    return f"⛓️ {crit}**{caster['name']}** mengikat dengan **Hellfire Chains**, memberikan **{damage}** kerusakan, membakar, dan meracuni!"

# Mythic Kitsune
async def spirit_fireball(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    bonus_crit_rate = kwargs.get('bonus_crit_rate', 0.0)
    bonus_crit_dmg = kwargs.get('bonus_crit_dmg', 0.0)
    
    damage, is_crit = await _apply_damage(session, caster, target, 1.90,
                                    bonus_crit_rate=bonus_crit_rate, bonus_crit_dmg=bonus_crit_dmg)
    await _apply_status(session, caster, target, "Spirit Burn", 2, 'dot', damage=int(caster['stats']['atk'] * 0.25))
    crit = "✨ **KRITIKAL!** " if is_crit else ""
    return f"🦊 {crit}**{caster['name']}** menembakkan **Spirit Fireball**, memberikan **{damage}** kerusakan dan meninggalkan api roh!"

async def red_moon_charm(session: "CombatSession", caster: dict, target: dict, **kwargs) -> str:
    await _apply_status(session, caster, target, "Charmed", 2, 'stun')
    
    is_heal_blocked = any(e.get('type') == 'heal_block' for e in caster.get('status_effects', []))
    if is_heal_blocked:
        return f"🌕 **{caster['name']}** menggunakan **Red Moon Charm** dan memesona **{target['name']}**, tetapi pemulihan gagal karena Heal Block!"
        
    heal_amount = int(caster['max_hp'] * 0.10)
    caster['hp'] = min(caster['max_hp'], caster['hp'] + heal_amount)
    return f"🌕 **{caster['name']}** menggunakan **Red Moon Charm**, memesona **{target['name']}** dan memulihkan **{heal_amount}** HP!"

# ===================================================================================
# --- IMPLEMENTASI SKILL PASIF ---
# ===================================================================================

async def archipelagos_blessing(session: "CombatSession", participant: dict):
    """HOOK: Dipanggil dari _apply_damage saat 'participant' menerima damage."""
    if 'passive_flags' not in participant: participant['passive_flags'] = {}
    
    # Cek Heal Block
    if any(e.get('type') == 'heal_block' for e in participant.get('status_effects', [])):
        return

    used = participant['passive_flags'].get('archipelago_blessing_used', False)
    if (participant['hp'] / participant['max_hp']) < 0.50 and not used:
        heal_amount = int(participant['max_hp'] * 0.20)
        participant['hp'] = min(participant['max_hp'], participant['hp'] + heal_amount)
        # Cleanse debuffs
        participant['status_effects'] = [e for e in participant['status_effects'] if e.get('type') not in ['debuff', 'dot', 'stun', 'silence', 'paralyze', 'heal_block']]
        participant['passive_flags']['archipelago_blessing_used'] = True
        session.log.append(f"🏝️ **Archipelagos Blessing** aktif! **{participant['name']}** memulihkan **{heal_amount}** HP dan membersihkan diri!")

async def encore_of_shadows(session: "CombatSession", loser: dict, attacker: dict):
    """HOOK: Dipanggil dari _handle_game_over saat 'loser' (pemilik pasif) dikalahkan."""
    damage = int(loser['max_hp'] * 0.25)
    attacker['hp'] = max(0, attacker['hp'] - damage)
    session.log.append(f"🎤 **Encore of Shadows**! **{loser['name']}** melepaskan nada terakhir, memberikan **{damage}** kerusakan pada **{attacker['name']}**!")

def harmonious_resonance(session: "CombatSession", caster: dict, skill_type: str):
    """HOOK: Dipanggil secara manual dari skill aktif Sun of Dual Symphonies."""
    if 'passive_flags' not in caster: caster['passive_flags'] = {}

    if skill_type == 'damage':
        # Jika menggunakan skill damage, set flag untuk memperkuat skill support berikutnya
        caster['passive_flags']['enhance_support'] = True
    elif skill_type == 'support':
        # Jika menggunakan skill support, kurangi cooldown skill damage
        if caster['skill_cooldowns'].get("Blazing Finale", 0) > 0:
            caster['skill_cooldowns']["Blazing Finale"] -= 1
            session.log.append("🎶 **Harmonious Resonance** mengurangi cooldown **Blazing Finale**!")

async def raging_phoenix(session: "CombatSession", participant: dict) -> bool:
    """HOOK: Panggil saat HP <= 0. Mengembalikan True jika hidup kembali."""
    # [PERBAIKAN] Cek flag di awal untuk mencegah eksekusi lebih lanjut.
    if participant.get('passive_flags', {}).get('raging_phoenix_used', False):
        return False

    if _has_passive(participant, "Raging Phoenix"):
        participant['hp'] = 1
        await _apply_status(session, participant, participant, "Phoenix Invincibility", 2, 'invincibility')
        
        # Tandai pasif telah digunakan
        if 'passive_flags' not in participant: participant['passive_flags'] = {}
        participant['passive_flags']['raging_phoenix_used'] = True
        
        session.log.append(f"不死鳥 **Raging Phoenix**! **{participant['name']}** menolak kematian dan bangkit dengan api abadi!")
        return True
    return False

def ancestors_sight(session: "CombatSession", caster: dict, target: dict):
    """HOOK: Dipanggil dari combat_logic.py di awal pertarungan."""
    target['stats']['crit_rate'] = max(0, target['stats']['crit_rate'] - 0.15)
    target['stats']['crit_damage'] = max(0, target['stats']['crit_damage'] - 0.15)
    session.log.append(f"👁️ **Ancestors Sight** dari **{caster['name']}** melihat kelemahan **{target['name']}**!")

async def winters_embrace(session: "CombatSession", attacker: dict, target: dict):
    """HOOK: Dipanggil dari _apply_damage setelah 'attacker' berhasil mendaratkan serangan."""
    if random.random() < 0.25:
        await _apply_status(session, attacker, target, "Winters Chill", 3, 'debuff', stat='spd', amount='-15%')
        session.log.append(f"🥶 **Winters Embrace** dari **{attacker['name']}** memperlambat **{target['name']}**!")

def perfect_symmetry(session: "CombatSession", participant: dict):
    """HOOK: Panggil di akhir giliran (misal: di awal fungsi switch_turn)."""
    # Cleanse self
    debuff_to_remove = next((e for e in participant['status_effects'] if e.get('type') in ['debuff', 'dot']), None)
    if debuff_to_remove:
        participant['status_effects'].remove(debuff_to_remove)
        session.log.append(f"⚖️ **Perfect Symmetry** menghapus **{debuff_to_remove['name']}** dari **{participant['name']}**.")
    # Purge enemy
    opponent = session.get_opponent(participant)
    buff_to_remove = next((e for e in opponent['status_effects'] if e.get('type') == 'buff'), None)
    if buff_to_remove:
        if 'stat' in buff_to_remove: opponent['stats'][buff_to_remove['stat']] -= buff_to_remove['amount']
        opponent['status_effects'].remove(buff_to_remove)
        session.log.append(f"⚖️ **Perfect Symmetry** menghapus **{buff_to_remove['name']}** dari **{opponent['name']}**.")

async def spellthief_s_gleam(session: "CombatSession", skill_user: dict, self_participant: dict):
    """HOOK: Panggil setelah musuh ('skill_user') menggunakan skill."""
    if random.random() < 0.20:
        buffs_on_enemy = [e for e in skill_user['status_effects'] if e.get('type') == 'buff' and e.get('duration') > 1]
        if buffs_on_enemy:
            stolen_buff = random.choice(buffs_on_enemy)
            # Terapkan buff yang dicuri ke diri sendiri
            await _apply_status(session, self_participant, self_participant, f"Stolen: {stolen_buff['name']}", 3, **stolen_buff)
            session.log.append(f"✨ **Spellthiefs Gleam**! **{self_participant['name']}** mencuri efek **{stolen_buff['name']}**!")

async def oceans_lullaby(session: "CombatSession", participant: dict):
    """HOOK: Panggil di awal giliran 'participant'._process_turn_effects."""
    if random.random() < 0.25:
        debuff_to_remove = next((e for e in participant['status_effects'] if e.get('type') in ['debuff', 'dot']), None)
        if debuff_to_remove:
            participant['status_effects'].remove(debuff_to_remove)
            session.log.append(f"🌊 **Oceans Lullaby** menenangkan jiwa **{participant['name']}**, menghapus **{debuff_to_remove['name']}**.")

async def feathered_sonnet(session: "CombatSession", caster: dict):
    """HOOK: Panggil setelah 'caster' berhasil menggunakan skill."""
    if 'passive_flags' not in caster: caster['passive_flags'] = {}
    stacks = caster['passive_flags'].get('sonnet_stacks', 0)
    if stacks < 5:
        stacks += 1
        caster['passive_flags']['sonnet_stacks'] = stacks
        spd_boost = int(caster['base_stats']['spd'] * (stacks * 0.05))
        # Hapus buff lama dan terapkan yang baru untuk menumpuk
        caster['status_effects'] = [e for e in caster['status_effects'] if e['name'] != "Feathered Sonnet"]
        await _apply_status(session, caster, caster, "Feathered Sonnet", 99, 'buff', stat='spd', amount=spd_boost)
        session.log.append(f"🕊️ **Feathered Sonnet**! Kecepatan **{caster['name']}** meningkat permanen!")

async def master_of_puppets(session: "CombatSession", target: dict, debuff_name: str):
    """HOOK: Panggil setelah berhasil memberikan debuff Stun, Paralyze, atau Silence."""
    target_debuff = next((e for e in target['status_effects'] if e['name'] == debuff_name), None)
    if target_debuff:
        target_debuff['duration'] += 1
        session.log.append(f"MASTER OF PUPPETS MENAMBAH DURASI")

async def static_resonance(session: "CombatSession", attacker: dict):
    """HOOK: Dipanggil dari _apply_damage saat 'attacker' mendaratkan serangan kritikal."""
    await _apply_status(session, attacker, attacker, "Static Resonance", 3, 'buff', stat='spd', amount='+15%')
    session.log.append(f"⚡ **Static Resonance**! Serangan kritikal meningkatkan kecepatan **{attacker['name']}**!")

async def rimefrost_aura(session: "CombatSession", attacker: dict, defender: dict):
    """HOOK: Dipanggil dari _apply_damage saat 'defender' (pemilik pasif) diserang."""
    if random.random() < 0.30:
        _apply_status(session, defender, attacker, "Rimefrost Slow", 3, 'debuff', stat='spd', amount='-10%')
        session.log.append(f"❄️ **Rimefrost Aura** dari **{defender['name']}** memperlambat **{attacker['name']}**!")

async def firewall_protocol(session: "CombatSession", participant: dict):
    """HOOK: Dipanggil dari combat_logic.py di awal pertarungan."""
    await _apply_status(session, participant, participant, "Immunity", 3, 'immunity') # Durasi 3 untuk 2 giliran
    session.log.append(f"🛡️ **Firewall Protocol** aktif! **{participant['name']}** kebal terhadap debuff.")

async def blade_of_serenity(session: "CombatSession", attacker: dict, target: dict):
    """HOOK: Panggil sebelum serangan. Cek flag 'tidak menerima damage'."""
    if attacker.get('passive_flags', {}).get('serenity_active', False):
        bleed_damage = int(target['max_hp'] * 0.08)
        await _apply_status(session, attacker, target, "Serenity Bleed", 3, 'dot', damage=bleed_damage)
        session.log.append(f"🍃 **Blade of Serenity**! Tebasan **{attacker['name']}** menyebabkan pendarahan hebat!")
        attacker['passive_flags']['serenity_active'] = False # Reset flag

def stat_swap_logic(session: "CombatSession"):
    """Fungsi Logika Khusus. Dipanggil oleh skill dan saat efek berakhir."""
    # Fungsi ini kompleks dan perlu dipanggil saat efek aktif dan berakhir.
    # Implementasi sederhana:
    p1, p2 = session.p1, session.p2
    p1_swap = next((e for e in p1['status_effects'] if e['name'] == "Stat Swap (Self)"), None)
    p2_swap = next((e for e in p2['status_effects'] if e['name'] == "Stat Swap (Self)"), None)
    
    # Simpan stat asli jika belum disimpan
    if p1_swap and 'original_atk' not in p1_swap:
        p1_swap['original_atk'], p1_swap['original_def'] = p1['stats']['atk'], p1['stats']['def']
        p2_swap['original_atk'], p2_swap['original_def'] = p2['stats']['atk'], p2['stats']['def']
        
        # Lakukan penukaran
        p1['stats']['atk'], p1['stats']['def'] = p2_swap['original_atk'], p2_swap['original_def']
        p2['stats']['atk'], p2['stats']['def'] = p1_swap['original_atk'], p1_swap['original_def']

# --- The Adamant Colossus (BARU) ---
async def stonewill_resilience(session: "CombatSession", participant: dict):
    """
    HOOK: Dipanggil dari _apply_damage saat 'participant' menerima damage.
    """
    if random.random() < 0.20:
        # Shield setara 10% dari DEF saat ini
        shield_amount = int(participant['stats']['def'] * 0.10)
        await _apply_status(session, participant, participant, "Stonewill Shield", 2, 'shield', shield_hp=shield_amount)
        session.log.append(f"💎 **Stonewill Resilience** aktif! **{participant['name']}** mendapatkan perisai **{shield_amount}** HP!")

# --- The Sentinels Vow (BARU) ---
async def unyielding_heart(session: "CombatSession", participant: dict):
    """
    HOOK: Dipanggil dari _apply_damage saat 'participant' menerima damage.
    """
    if 'passive_flags' not in participant: participant['passive_flags'] = {}
    
    heart_triggered = participant['passive_flags'].get('unyielding_heart_triggered', False)
    is_low_hp = (participant['hp'] / participant['max_hp']) < 0.40
    
    if is_low_hp and not heart_triggered:
        heal_per_turn = int(participant['max_hp'] * 0.10)
        await _apply_status(session, participant, participant, "Unyielding Heart", 4, 'hot', heal_amount=heal_per_turn)
        session.log.append(f"❤️‍🔥 **Unyielding Heart**! **{participant['name']}** menolak untuk jatuh dan mulai memulihkan diri!")
        participant['passive_flags']['unyielding_heart_triggered'] = True

# --- Howling Gale (BARU) ---
async def spirit_of_the_pack(session: "CombatSession", attacker: dict):
    """
    HOOK: Dipanggil dari _apply_damage saat 'attacker' mendaratkan serangan kritikal.
    """
    if 'passive_flags' not in attacker: attacker['passive_flags'] = {}
    
    current_stacks = attacker['passive_flags'].get('spirit_of_pack_stacks', 0)
    current_stacks += 1
    attacker['passive_flags']['spirit_of_pack_stacks'] = current_stacks
    
    # Hapus buff lama (jika ada) untuk menumpuknya
    existing_buff = next((e for e in attacker['status_effects'] if e.get('name') == 'Spirit of the Pack'), None)
    if existing_buff:
        attacker['stats']['spd'] -= existing_buff['amount']
        attacker['status_effects'].remove(existing_buff)

    # Terapkan buff baru yang sudah ditumpuk
    total_spd_boost_percent = current_stacks * 5
    base_spd = attacker['base_stats']['spd']
    total_boost_amount = int(base_spd * (total_spd_boost_percent / 100))
    
    await _apply_status(session, attacker, attacker, "Spirit of the Pack", 99, 'buff', stat='spd', amount=total_boost_amount)
    session.log.append(f"🐺 **Spirit of the Pack**! Kecepatan **{attacker['name']}** meningkat secara permanen!")

# --- The Weaver Commander (BARU) ---
async def master_tactician(session: "CombatSession", caster: dict, target: dict):
    """
    HOOK: Dipanggil dari combat_logic.py di awal pertarungan.
    """
    await _apply_status(session, caster, target, "Tacticians Ploy", 3, 'debuff', stat='atk', amount='-15%')
    session.log.append(f"🧠 **Master Tactician**! Kecepatan **{caster['name']}** membuatnya bisa melemahkan **{target['name']}** di awal!")

# --- Bulwark of the Dawns Light (BARU) ---
async def resolute_guardian(session: "CombatSession", participant: dict):
    """
    HOOK: Dipanggil dari _apply_status saat 'participant' menerima debuff.
    """
    await _apply_status(session, participant, participant, "Resolute Guardian", 3, 'buff', stat='def', amount='+10%')
    session.log.append(f"💪 **Resolute Guardian**! **{participant['name']}** menjadi lebih kuat setelah menerima efek negatif!")

# --- Phantom in the Code (BARU) ---
async def volatile_encryption(session: "CombatSession", attacker: dict, defender: dict):
    """
    HOOK: Dipanggil dari _apply_damage saat 'defender' (pemilik pasif) menerima damage.
    """
    if random.random() < 0.15:
        await _apply_status(session, defender, attacker, "Volatile Encryption", 2, 'debuff', stat='atk', amount='-20%')
        session.log.append(f"🔒 **Volatile Encryption** dari **{defender['name']}** merusak data serangan **{attacker['name']}**!")

# --- Gilded Rose of Sunstone (BARU) ---
async def perfect_confection(session: "CombatSession", participant: dict):
    """
    HOOK: Panggil di awal giliran. Menghitung giliran untuk buff berikutnya.
    """
    if 'passive_flags' not in participant: participant['passive_flags'] = {}
    
    counter = participant['passive_flags'].get('confection_counter', 0) + 1
    
    if counter >= 4:
        participant['passive_flags']['confection_counter'] = 0
        # Terapkan buff internal yang akan dicek oleh _apply_damage
        await _apply_status(session, participant, participant, "Perfect Confection Ready", 2, 'internal_buff')
    else:
        participant['passive_flags']['confection_counter'] = counter

# --- Curse of a Broken Moon (BARU) ---
async def sanguine_pact(session: "CombatSession", participant: dict):
    """
    HOOK: Panggil di awal giliran. Mengecek kondisi HP untuk memberikan buff.
    """
    has_buff = any(e.get('name') == "Sanguine Pact" for e in participant.get('status_effects', []))
    is_low_hp = (participant['hp'] / participant['max_hp']) < 0.60
    
    # Cek apakah pasifnya pernah aktif sebelumnya (untuk mencegah re-trigger terus menerus)
    pact_triggered = participant.get('passive_flags', {}).get('sanguine_pact_triggered', False)

    if is_low_hp and not has_buff and not pact_triggered:
        await _apply_status(session, participant, participant, "Sanguine Pact", 3, 'buff', stat='atk', amount='+25%')
        session.log.append(f"🩸 **Sanguine Pact** aktif! Kekuatan **{participant['name']}** meningkat drastis!")
        if 'passive_flags' not in participant: participant['passive_flags'] = {}
        participant['passive_flags']['sanguine_pact_triggered'] = True
    elif not is_low_hp and pact_triggered:
        # Reset flag jika HP sudah pulih, agar bisa aktif lagi nanti
        participant['passive_flags']['sanguine_pact_triggered'] = False

# --- Gambler of the Fickle Fate (BARU) ---
async def whims_of_fortune(session: "CombatSession", participant: dict):
    """
    HOOK: Panggil di awal giliran.
    """
    if random.random() < 0.33:
        await _apply_status(session, participant, participant, "Counter-Attack", 2, 'counter')
        session.log.append(f"🍀 **Whims of Fortune** tersenyum! **{participant['name']}** siap untuk melakukan serangan balasan!")

# --- Crimson Lotus Dancer (BARU) ---
async def dance_of_a_thousand_cuts(session: "CombatSession", attacker: dict, target: dict):
    """
    HOOK: Panggil setelah attacker berhasil mendaratkan serangan.
    """
    if 'passive_flags' not in attacker: attacker['passive_flags'] = {}
    
    counter = attacker['passive_flags'].get('thousand_cuts_counter', 0) + 1
    
    if counter >= 3:
        attacker['passive_flags']['thousand_cuts_counter'] = 0
        bleed_damage = int(target['max_hp'] * 0.06)
        await _apply_status(session, attacker, target, "Thousand Cuts Bleed", 3, 'dot', damage=bleed_damage)
        session.log.append(f"🩸 **Dance of a Thousand Cuts**! **{target['name']}** menderita pendarahan parah!")
    else:
        attacker['passive_flags']['thousand_cuts_counter'] = counter

# --- Whisper of the Gilded Cage (BARU) ---
async def warden_s_grace(session: "CombatSession", caster: dict):
    """
    HOOK: Dipanggil dari _apply_status ketika caster berhasil memberikan debuff.
    """
    await _apply_status(session, caster, caster, "Wardens Grace", 3, 'buff', stat='spd', amount='+15%')
    session.log.append(f"✨ **Wardens Grace** aktif! SPD **{caster['name']}** meningkat!")

# --- The Undying Taboo (BARU) ---
async def grave_pact(session: "CombatSession", participant: dict):
    """
    HOOK: Panggil di awal giliran. Mengelola status buff DEF.
    """
    has_buff = any(e.get('name') == "Grave Pact" for e in participant.get('status_effects', []))
    is_low_hp = (participant['hp'] / participant['max_hp']) < 0.30

    if is_low_hp and not has_buff:
        # Terapkan buff
        await _apply_status(session, participant, participant, "Grave Pact", 99, 'buff', stat='def', amount='+25%')
        session.log.append(f"묘 **Grave Pact** aktif, **{participant['name']}** menjadi lebih tangguh!")
    elif not is_low_hp and has_buff:
        # Hapus buff jika HP sudah pulih
        effects_to_remove = [e for e in participant['status_effects'] if e.get('name') == "Grave Pact"]
        if effects_to_remove:
            effect = effects_to_remove[0]
            # Kembalikan stat secara manual
            original_amount = effect.get('amount', 0)
            participant['stats'][effect['stat']] -= original_amount
            participant['stats'][effect['stat']] = max(0, participant['stats'][effect['stat']])
            participant['status_effects'].remove(effect)
            session.log.append(f"묘 **Grave Pact** nonaktif.")

# --- Fate of the Twin Blades (BARU) ---
async def twin_s_harmony(session: "CombatSession", attacker: dict, defender: dict) -> str:
    """
    HOOK: Panggil fungsi ini di `combat_logic.py` setelah serangan dasar ('attack') berhasil.
    """
    if 'passive_flags' not in attacker: attacker['passive_flags'] = {}
    
    counter = attacker['passive_flags'].get('twin_harmony_counter', 0) + 1
    
    if counter >= 4:
        attacker['passive_flags']['twin_harmony_counter'] = 0
        damage, is_crit = await _apply_damage(session, attacker, defender, 0.50)
        crit_text = "✨ **KRITIKAL!** " if is_crit else ""
        return f"🎶 **Twins Harmony** aktif! {crit_text}**{attacker['name']}** melancarkan serangan bonus, memberikan **{damage}** kerusakan!"
    else:
        attacker['passive_flags']['twin_harmony_counter'] = counter
    return ""

# --- Guardian of the Soul Gate (BARU) ---
async def soul_siphon(session: "CombatSession", attacker: dict, damage_dealt: int):
    """
    HOOK: Panggil di dalam `_apply_damage` setelah damage dihitung.
    """
    # Cek Heal Block
    if any(e.get('type') == 'heal_block' for e in attacker.get('status_effects', [])):
        return

    if random.random() < 0.15 and damage_dealt > 0:
        healed_amount = int(damage_dealt * 0.25) # Menyerap 25% dari damage yang diberikan
        attacker['hp'] = min(attacker['max_hp'], attacker['hp'] + healed_amount)
        session.log.append(f"👻 **Soul Siphon** menyerap **{healed_amount}** HP untuk **{attacker['name']}**!")

# --- The Pixelated Prodigy (BARU) ---
async def extra_life(session: "CombatSession", participant: dict) -> bool:
    """HOOK: Panggil saat HP <= 0. Mengembalikan True jika hidup kembali."""
    # [PERBAIKAN] Cek flag di awal.
    if participant.get('passive_flags', {}).get('extra_life_used', False):
        return False

    if _has_passive(participant, "Extra Life"):
        revive_hp = int(participant['max_hp'] * 0.15)
        participant['hp'] = revive_hp
        
        # Tandai pasif telah digunakan
        if 'passive_flags' not in participant: participant['passive_flags'] = {}
        participant['passive_flags']['extra_life_used'] = True
        
        session.log.append(f"❤️‍🩹 **Extra Life**! **{participant['name']}** hidup kembali dengan **{revive_hp}** HP!")
        return True
    return False

# --- Nameless Blade ---
async def eager_heart(session: "CombatSession", attacker: dict) -> str:
    """
    HOOK: Panggil fungsi ini di `combat_logic.py` setelah serangan dasar (`'attack'`) berhasil.
    """
    if random.random() < 0.05:
        await _apply_status(session, attacker, attacker, "Eager Heart", duration=1, effect_type='buff', stat='atk', amount='+10%')
        return f"❤️ Semangat **Eager Heart** berkobar, meningkatkan ATK **{attacker['name']}**!"
    return ""

# --- Altars Whisper ---
def steadfast_faith(session: "CombatSession", target: dict, heal_amount: int) -> int:
    """
    HOOK: Panggil fungsi ini di dalam skill penyembuhan apa pun (seperti Mending Light)
    untuk memodifikasi nilai penyembuhan sebelum diterapkan.
    """
    # Cek apakah target memiliki pasif ini
    has_passive = any(skill.get('name') == "Steadfast Faith" for skill in target['raw_title_data'].get('skills', []))
    if has_passive:
        return int(heal_amount * 1.05)
    return heal_amount

# --- Leafs Shadow ---
def keen_senses(session: "CombatSession", defender: dict) -> bool:
    """
    HOOK: Panggil di awal fungsi `_apply_damage`. Jika mengembalikan True,
    batalkan damage dan kembalikan pesan menghindar.
    """
    has_passive = any(skill.get('name') == "Keen Senses" for skill in defender['raw_title_data'].get('skills', []))
    if has_passive and random.random() < 0.10:
        return True # Berhasil menghindar
    return False # Gagal menghindar

# --- First Spark ---
def arcane_echo(session: "CombatSession", caster: dict) -> str:
    """
    HOOK: Panggil fungsi ini di `combat_logic.py` setelah sebuah skill (`'skill'`) berhasil digunakan.
    """
    has_passive = any(skill.get('name') == "Arcane Echo" for skill in caster['raw_title_data'].get('skills', []))
    if has_passive and random.random() < 0.05:
        for skill_name, cd in caster['skill_cooldowns'].items():
            if cd > 0:
                caster['skill_cooldowns'][skill_name] -= 1
        return "🌀 **Arcane Echo** aktif, mengurangi cooldown semua skill!"
    return ""

# --- Concrete Will ---
def cornered_fury(session: "CombatSession", participant: dict, current_atk: int) -> int:
    """
    HOOK: Panggil di dalam `_apply_damage` sebelum menghitung `base_damage`
    untuk memodifikasi stat ATK secara sementara untuk serangan tersebut.
    """
    has_passive = any(skill.get('name') == "Cornered Fury" for skill in participant['raw_title_data'].get('skills', []))
    if has_passive and (participant['hp'] / participant['max_hp']) < 0.30:
        # Peningkatan 15% dari ATK saat ini untuk perhitungan damage ini
        return int(current_atk * 1.15)
    return current_atk

# --- Enemy Passives ---
async def unbroken_threads(session: "CombatSession", participant: dict) -> bool:
    """HOOK: Panggil saat HP <= 0. Mengembalikan True jika hidup kembali."""
    # [PERBAIKAN KUNCI] Tambahkan sistem flag yang sama untuk pasif monster.
    if participant.get('passive_flags', {}).get('unbroken_threads_used', False):
        return False

    if _has_passive(participant, "Unbroken Threads") and random.random() < 0.25:
        revive_hp = int(participant['max_hp'] * 0.20)
        participant['hp'] = revive_hp
        
        # Tandai pasif telah digunakan
        if 'passive_flags' not in participant: participant['passive_flags'] = {}
        participant['passive_flags']['unbroken_threads_used'] = True

        session.log.append(f"🧵 Benang tak putus! **{participant['name']}** hidup kembali dengan **{revive_hp}** HP!")
        return True
    return False

async def forests_breath(session: "CombatSession", participant):
    """HOOK: Panggil di awal giliran."""
    # Cek Heal Block
    if any(e.get('type') == 'heal_block' for e in participant.get('status_effects', [])):
        return

    if _has_passive(participant, "Forests Breath"):
        regen_hp = int(participant['max_hp'] * 0.05)
        participant['hp'] = min(participant['max_hp'], participant['hp'] + regen_hp)
        session.log.append(f"🌿 **Forests Breath** memulihkan **{regen_hp}** HP untuk **{participant['name']}**.")

async def dark_honor(session: "CombatSession", participant):
    """HOOK: Panggil di awal giliran, cek kondisi HP."""
    if _has_passive(participant, "Dark Honor") and (participant['hp'] / participant['max_hp']) < 0.40:
        # Cek apakah buff belum aktif
        if not any(e['name'] == 'Dark Honor' for e in participant['status_effects']):
            spd_boost = int(participant['stats']['spd'] * 0.20)
            await _apply_status(session, participant, participant, "Dark Honor", 99, 'buff', stat='spd', amount=spd_boost)
            session.log.append(f"🔥 **Dark Honor** aktif, meningkatkan SPD **{participant['name']}**!")

async def immortal_blade(session: "CombatSession", participant):
    """HOOK: Panggil di awal giliran, cek kondisi HP."""
    if _has_passive(participant, "Immortal Blade") and (participant['hp'] / participant['max_hp']) < 0.30:
        if not any(e['name'] == 'Immortal Blade' for e in participant['status_effects']):
            crit_boost = 0.20
            await _apply_status(session, participant, participant, "Immortal Blade", 3, 'buff', stat='crit_rate', amount=crit_boost)
            session.log.append(f"🗡️ **Immortal Blade** aktif, meningkatkan CRIT Rate **{participant['name']}**!")

async def blood_frenzy(session: "CombatSession", attacker):
    """HOOK: Panggil setelah serangan kritikal."""
    if _has_passive(attacker, "Blood Frenzy"):
        atk_boost = int(attacker['stats']['atk'] * 0.10)
        await _apply_status(session, attacker, attacker, "Blood Frenzy", 3, 'buff', stat='atk', amount=atk_boost)
        session.log.append(f"🩸 **Blood Frenzy** aktif karena kritikal, meningkatkan ATK **{attacker['name']}**!")

async def toxic_body(session: "CombatSession", attacker, defender):
    """HOOK: Panggil setelah defender menerima serangan."""
    if _has_passive(defender, "Toxic Body"):
        poison_damage = int(attacker['max_hp'] * 0.03)
        await _apply_status(session, defender, attacker, "Toxic Body", 2, 'dot', damage=poison_damage)
        session.log.append(f"☣️ **Toxic Body** dari **{defender['name']}** meracuni **{attacker['name']}**!")

async def thorny_garden(session: "CombatSession", attacker, defender, damage_dealt):
    """HOOK: Panggil setelah defender menerima serangan."""
    if _has_passive(defender, "Thorny Garden"):
        reflect_damage = int(damage_dealt * 0.10)
        attacker['hp'] = max(0, attacker['hp'] - reflect_damage)
        session.log.append(f"🌵 **Thorny Garden** memantulkan **{reflect_damage}** kerusakan ke **{attacker['name']}**!")

async def bound_soul(session: "CombatSession", caster):
    """HOOK: Panggil setelah caster berhasil mendaratkan debuff."""
    if _has_passive(caster, "Bound Soul"):
        def_boost = int(caster['stats']['def'] * 0.15)
        await _apply_status(session, caster, caster, "Bound Soul", 99, 'buff', stat='def', amount=def_boost)
        session.log.append(f"🔗 **Bound Soul** memperkuat DEF **{caster['name']}**!")
        
async def retribution_aura(session: "CombatSession", defender):
    """HOOK: Panggil setelah defender menerima serangan."""
    if _has_passive(defender, "Retribution Aura") and random.random() < 0.20:
        def_boost = int(defender['stats']['def'] * 0.15)
        await _apply_status(session, defender, defender, "Retribution Aura", 3, 'buff', stat='def', amount=def_boost)
        session.log.append(f"☀️ **Retribution Aura** meningkatkan DEF **{defender['name']}**!")

async def ghostly_rage(session: "CombatSession", attacker):
    """HOOK: Panggil sebelum `_apply_damage`. Mengembalikan multiplier damage tambahan."""
    if _has_passive(attacker, "Ghostly Rage"):
        # `turn_count` di sini merujuk pada giliran global, bukan giliran individual
        if (session.turn_count - 1) % 4 == 0 and session.current_turn_participant == attacker:
            session.log.append(f"👻 **Ghostly Rage** aktif!")
            # Ini akan diterapkan sebagai bonus di combat_logic
            return 1.50 
    return 1.0

async def body_of_fire(session: "CombatSession", attacker, defender):
    """HOOK: Panggil setelah defender (pemilik pasif) menerima damage."""
    await _apply_status(session, defender, attacker, "Body of Fire", 1, 'dot', damage=int(defender['stats']['atk'] * 0.10))
    session.log.append(f"🔥 Tubuh api **{defender['name']}** membakar **{attacker['name']}**!")
    
async def haunting_presence(session: "CombatSession", caster):
    """HOOK: Panggil di awal pertarungan. Menerapkan debuff aura."""
    if _has_passive(caster, "Haunting Presence"):
        opponent = session.get_opponent(caster)
        await _apply_status(session, caster, opponent, "Haunting Presence", 999, 'debuff', stat='spd', amount='-5%')
        session.log.append(f"🎶 Kehadiran **{caster['name']}** memperlambat semua lawan!")

async def final_prayer(session: "CombatSession", user):
    """HOOK: Panggil setelah `user` menerima damage."""
    # Cek Heal Block
    if any(e.get('type') == 'heal_block' for e in user.get('status_effects', [])):
        return

    hp_percent = user['hp'] / user['max_hp']
    if hp_percent < 0.30 and not user.get('passive_flags', {}).get('final_prayer_used'):
        heal_amount = int(user['max_hp'] * 0.25)
        user['hp'] = min(user['max_hp'], user['hp'] + heal_amount)
        # Tandai pasif sudah digunakan
        if 'passive_flags' not in user: user['passive_flags'] = {}
        user['passive_flags']['final_prayer_used'] = True
        session.log.append(f"🙏 **Final Prayer** aktif, memulihkan **{heal_amount}** HP untuk **{user['name']}**!")

async def written_fate(session: "CombatSession", attacker, defender):
    """HOOK: Panggil di awal giliran `attacker`."""
    if _has_passive(attacker, "Written Fate"):
        if (session.turn_count - 1) % 4 == 0 and random.random() < 0.40:
             await _apply_status(session, attacker, defender, "Stunned by Fate", 2, 'stun')
             session.log.append(f"📜 Takdir tertulis! **{attacker['name']}** membuat **{defender['name']}** pingsan!")

async def final_vengeance(session: "CombatSession", loser, winner):
    """HOOK: Panggil saat `loser` (pemilik pasif) dikalahkan."""
    if _has_passive(loser, "Final Vengeance"):
        damage = int(loser['stats']['atk'] * 1.20)
        winner['hp'] = max(0, winner['hp'] - damage)
        session.log.append(f"💥 Balas dendam terakhir! **{loser['name']}** meledak saat kalah, memberikan **{damage}** kerusakan pada **{winner['name']}**!")

async def eternal_power(session: "CombatSession", user):
    """HOOK: Panggil di awal giliran `user` (pemilik pasif)."""
    if 'passive_flags' not in user: user['passive_flags'] = {}
    
    stacks = user['passive_flags'].get('eternal_power_stacks', 0)
    
    if stacks < 10: # Maksimal 50% (10 tumpukan @ 5%)
        stacks += 1
        user['passive_flags']['eternal_power_stacks'] = stacks
        
        # Hapus buff lama jika ada, lalu terapkan yang baru
        user['status_effects'] = [e for e in user['status_effects'] if e['name'] != "Eternal Power"]
        
        atk_boost_percent = stacks * 5
        base_atk = user['base_stats']['atk']
        boost_amount = int(base_atk * (atk_boost_percent / 100))
        
        # Terapkan sebagai buff non-durasi yang di-refresh tiap giliran
        await _apply_status(session, user, user, "Eternal Power", 2, 'buff', stat='atk', amount=boost_amount)
        session.log.append(f"✨ Kekuatan **{user['name']}** bertambah! (ATK +{atk_boost_percent}%)")


# ===================================================================================
# --- PEMETAAN DAN FUNGSI UTAMA ---
# =================================================================================== 

# Daftarkan semua fungsi skill aktif Anda di sini
skill_implementations = {
    "Tidal Bulwark": tidal_bulwark,
    "Duskfall Strike": duskfall_strike,
    "Sorrowful Aria": sorrowful_aria,
    "Phantom Crescendo": phantom_crescendo,
    "Solar Overture": solar_overture,
    "Blazing Finale": blazing_finale,
    "Inferno Brand": inferno_brand,
    "Soul Combustion": soul_combustion,
    "Hundred Spirits Palm": hundred_spirits_palm,
    "Flowing Mantra": flowing_mantra,
    "Absolute Zero": absolute_zero,
    "Snowflake Dance": snowflake_dance,
    "Realitys Blueprint": reality_s_blueprint,
    "Harmonic Convergence": harmonic_convergence,
    "Crystallize Mana": crystallize_mana,
    "Amethyst Purge": amethyst_purge,
    "Summon: Leviathans Mirage": summon_leviathan_s_mirage,
    "Dreamtide": dreamtide,
    "Verse of the Griffin": verse_of_the_griffin,
    "Rhyme of the Roc": rhyme_of_the_roc,
    "Puppets Vow": puppet_s_vow,
    "Strings of Fate": strings_of_fate,
    "Thunderclap Sonata": thunderclap_sonata,
    "Lightning Etude": lightning_etude,
    "Glacial Prison": glacial_prison,
    "Winters Heart": winter_s_heart,
    "Data Leak": data_leak,
    "System Crash": system_crash,
    "Sakura Flash": sakura_flash,
    "Falling Blossom": falling_blossom,

    "Sundering Quake": sundering_quake,
    "Ironclad Resolve": ironclad_resolve,
    "Barricade of Thorns": barricade_of_thorns,
    "Retribution Bash": retribution_bash,
    "Lancers Cometfall": lancer_s_cometfall,
    "Ride the Wind": ride_the_wind,
    "Foresights Gambit": foresights_gambit,
    "Orchestrated Assault": orchestrated_assault,
    "Hallowed Ground": hallowed_ground,
    "Sacred Intervention": sacred_intervention,
    "Cascading Logic Bomb": cascading_logic_bomb,
    "Protocol Override": protocol_override,
    "Neurotoxin Bloom": neurotoxin_bloom,
    "Paralyzing Venom": paralyzing_venom,
    "Caramelized Shot": caramelized_shot,
    "Flourish and Fire": flourish_and_fire,
    "Whispers of Decay": whispers_of_decay,
    "Blood Price Offering": blood_price_offering,
    "Chaotic Roll": chaotic_roll,
    "All In": all_in,
    "Blade of Ephemeral Grace": blade_of_ephemeral_grace,
    "Arcane Silence": arcane_silence,
    "Golden Shackle": golden_shackle,
    "Gilded Prison": gilded_prison,
    "Raise Dead": raise_dead,
    "Soul Drain": soul_drain,
    "Crescent Weep": crescent_weep,
    "Lunar Curse": lunar_curse,
    "Vine Lash": vine_lash,
    "Natures Blessing": nature_s_blessing,
    "Shadow Bolt": shadow_bolt,
    "Whispers of Fear": whispers_of_fear,
    "Cross Slash": cross_slash,
    "Blade Dance": blade_dance,
    "Fel Flame": fel_flame,
    "Demonic Pact": demonic_pact,
    "Button Mash": button_mash,
    "Rage Quit": rage_quit,
    "Spicy Dish": spicy_dish,
    "Hearty Meal": hearty_meal,
    "First Cut": first_cut,
    "Steady Guard": steady_guard,
    "Mending Light": mending_light,
    "Hallowed Ward": hallowed_ward,
    "Swift Strike": swift_strike,
    "Wind Step": wind_step,
    "Ember Cast": ember_cast,
    "Fading Curse": fading_curse,
    "Heavy Blow": heavy_blow,
    "Iron Resolve": iron_resolve,

    # Enemy
    "Silent Strike": silent_strike, 
    "Flowing Blade": flowing_blade, 
    "Mechanical Slash": mechanical_slash,
    "Core Overload": core_overload, 
    "Vengeful Fist": vengeful_fist, 
    "Savage Rampage": savage_rampage,
    "Root Bind": root_bind, 
    "Concentrated Venom": concentrated_venom, 
    "Arenas Cleave": arenas_cleave,
    "Finishing Blow": finishing_blow, 
    "Shadow Slash": shadow_slash, 
    "Splitting Shadow": splitting_shadow,
    "Crimson Edge": crimson_edge, 
    "Blade Fury": blade_fury, 
    "Poison Vial": poison_vial,
    "Acid Splash": acid_splash, 
    "Binding Thorns": binding_thorns, 
    "Wilted Rose": wilted_rose,
    "Prison Chain": prison_chain, 
    "Drag to the Abyss": drag_to_the_abyss, 
    "Burning Light": burning_light,
    "Solar Spear": solar_spear,

    "Cursed Cannonball": cursed_cannonball, 
    "Deep Sea Curse": deep_sea_curse,
    "Lifes Mirror": lifes_mirror, 
    "Dual Face": dual_face,
    "Lava Burst": lava_burst, 
    "Incinerate": incinerate,
    "Draining Note": draining_note, 
    "Lullaby of Nightmares": lullaby_of_nightmares,
    "Blinding Mist": blinding_mist, 
    "Hand of Fog": hand_of_fog,
    "Dark Claw": dark_claw, 
    "Shadow Breath": shadow_breath,
    "Imperial Sword": imperial_sword, 
    "Decree of Ruin": decree_of_ruin,
    "Blood Offering": blood_offering, 
    "Ivory Curse": ivory_curse,
    "Thread of Life": thread_of_life, 
    "Self Mending": self_mending,
    "Abyss Strike": abyss_strike, 
    "Hellfire Chains": hellfire_chains,
    "Spirit Fireball": spirit_fireball, 
    "Red Moon Charm": red_moon_charm,
}

# Daftarkan semua fungsi skill pasif untuk referensi
passive_implementations = {
    "Archipelagos Blessing": archipelagos_blessing,
    "Encore of Shadows": encore_of_shadows,
    "Harmonious Resonance": harmonious_resonance, # Dipanggil manual
    "Raging Phoenix": raging_phoenix,
    "Ancestors Sight": ancestors_sight,
    "Winters Embrace": winters_embrace,
    "Perfect Symmetry": perfect_symmetry,
    "Spellthiefs Gleam": spellthief_s_gleam,
    "Oceans Lullaby": oceans_lullaby,
    "Feathered Sonnet": feathered_sonnet,
    "Master of Puppets": master_of_puppets,
    "Static Resonance": static_resonance,
    "Rimefrost Aura": rimefrost_aura,
    "Firewall Protocol": firewall_protocol,
    "Blade of Serenity": blade_of_serenity,
    "Stat Swap Logic": stat_swap_logic, # Fungsi internal

    "Stonewill Resilience": stonewill_resilience,
    "Unyielding Heart": unyielding_heart,
    "Spirit of the Pack": spirit_of_the_pack,
    "Master Tactician": master_tactician,
    "Resolute Guardian": resolute_guardian,
    "Volatile Encryption": volatile_encryption,
    "Lingering Malice": None, # Di-handle di _apply_status
    "Perfect Confection": perfect_confection,
    "Sanguine Pact": sanguine_pact,
    "Whims of Fortune": whims_of_fortune,
    "Dance of a Thousand Cuts": dance_of_a_thousand_cuts,
    "Wardens Grace": warden_s_grace,
    "Grave Pact": grave_pact,
    "Moons Scorn": None, # Di-handle di _apply_damage
    "Forests Embrace": None, # Di-handle di combat_logic
    "Cloak of Darkness": None, # Di-handle di _apply_damage
    "Twins Harmony": twin_s_harmony,
    "Soul Siphon": soul_siphon,
    "Extra Life": extra_life,
    "Eager Heart": eager_heart,
    "Steadfast Faith": steadfast_faith,
    "Keen Senses": keen_senses,
    "Arcane Echo": arcane_echo,
    "Cornered Fury": cornered_fury,

    # Enemy
    "Unbroken Threads": unbroken_threads, 
    "Forests Breath": forests_breath, 
    "Dark Honor": dark_honor,
    "Immortal Blade": immortal_blade, 
    "Blood Frenzy": blood_frenzy, 
    "Toxic Body": toxic_body,
    "Thorny Garden": thorny_garden, 
    "Bound Soul": bound_soul, 
    "Retribution Aura": retribution_aura,

    "Ghostly Rage": ghostly_rage,
    "Body of Fire": body_of_fire,
    "Haunting Presence": haunting_presence,
    "Final Prayer": final_prayer,
    "Written Fate": written_fate,
    "Final Vengeance": final_vengeance,
    "Eternal Power": eternal_power,
}

async def apply_skill(session: "CombatSession", caster: dict, target: dict, skill_name: str, **kwargs) -> str:
    skill_function = skill_implementations.get(skill_name)
    
    if skill_function:
        caster['skill_cooldowns'][skill_name] = session.get_skill_cooldown(caster, skill_name)
        
        # [TAMBAHKAN INI] Hook untuk misi penggunaan skill
        if caster.get('is_player'):
            quest_cog = session.bot.get_cog("Misi")
            if quest_cog:
                await quest_cog.update_quest_progress(caster['id'], 'USE_SKILL')
        
        log_message = await skill_function(session, caster, target, **kwargs)
        return log_message
    
    return f"KESALAHAN: Skill '{skill_name}' belum diimplementasikan."