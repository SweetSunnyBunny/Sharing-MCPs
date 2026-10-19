
"""
Multi-World Quest Action System
Handles quest-related commands across different game worlds
"""

import random
import json
from pathlib import Path
from datetime import datetime
from typing import Dict, Optional
from .multi_world_player_manager import MultiWorldPlayerManager

class MultiWorldQuestHandler:
    """Handles quest actions across multiple worlds"""

    def __init__(self, player_manager: MultiWorldPlayerManager):
        self.player_manager = player_manager

        hunting_config_path = Path(__file__).parent / "hunting_config.json"
        if hunting_config_path.exists():
            with open(hunting_config_path, 'r', encoding='utf-8') as f:
                self.hunting_config = json.load(f)
        else:
            self.hunting_config = {'hunting_areas': {}, 'processing_recipes': {}}

        gathering_config_path = Path(__file__).parent / "gathering_config.json"
        if gathering_config_path.exists():
            with open(gathering_config_path, 'r', encoding='utf-8') as f:
                self.gathering_config = json.load(f)
        else:
            self.gathering_config = {'gathering_areas': {}, 'sell_prices': {}}

        mount_config_path = Path(__file__).parent / "mount_config.json"
        if mount_config_path.exists():
            with open(mount_config_path, 'r', encoding='utf-8') as f:
                self.mount_config = json.load(f)
        else:
            self.mount_config = {'mounts': {}, 'speed_messages': {}}

        self.cooldown_times = {
            'collect': 30,
            'hunt': 45,
            'search': 20,
            'gather': 30,
            'fish': 45,
        }

    def roll_damage_die(self, damage_die: str) -> int:
        """Parse and roll a damage die string like '1d6' or '1d8'"""
        if 'd' not in damage_die:
            return 0

        try:
            num_dice, die_size = damage_die.split('d')
            num_dice = int(num_dice)
            die_size = int(die_size)
            return sum(random.randint(1, die_size) for _ in range(num_dice))
        except (ValueError, TypeError):
            return random.randint(1, 6)  

    def get_combat_bonuses(self, player_id: str, world_id: str) -> Dict:
        """Calculate total combat bonuses from weapon + form + companion"""
        bonuses = {
            'attack': 3,  
            'damage_die': '1d6',  
            'damage_bonus': 0,
            'weapon': None,
            'form': None,
            'companion': None,
            'companion_loyal': True,
            'bonus_sources': {
                'base': 3
            }
        }

        weapon = self.player_manager.get_equipped_weapon(player_id, world_id)
        if weapon and weapon['weapon_id'] != 'fists':
            bonuses['attack'] += weapon['attack_bonus']
            bonuses['damage_die'] = weapon['damage_die']
            bonuses['damage_bonus'] += weapon['damage_bonus']
            bonuses['weapon'] = weapon
            bonuses['bonus_sources']['weapon'] = {
                'name': weapon['name'],
                'attack': weapon['attack_bonus'],
                'damage': weapon['damage_bonus']
            }
        elif weapon['weapon_id'] == 'fists':
            
            bonuses['damage_die'] = weapon['damage_die']
            bonuses['weapon'] = weapon

        form = self.player_manager.get_current_form(player_id)
        if form:
            bonuses['attack'] += form['bonuses']['attack']
            bonuses['damage_bonus'] += form['bonuses']['damage']
            bonuses['form'] = form
            bonuses['bonus_sources']['form'] = {
                'name': form['name'],
                'attack': form['bonuses']['attack'],
                'damage': form['bonuses']['damage']
            }

        companion_data = self.player_manager.get_companion_bonuses(player_id)
        companion = self.player_manager.get_companion(player_id)
        if companion_data['is_loyal']:
            
            comp_attack = 0
            comp_damage = 0
            for bonus_type, value in companion_data['bonuses'].items():
                if bonus_type == 'attack':
                    bonuses['attack'] += value
                    comp_attack += value
                elif bonus_type == 'hunting':
                    bonuses['attack'] += value
                    comp_attack += value
                elif bonus_type == 'damage':
                    bonuses['damage_bonus'] += value
                    comp_damage = value
            bonuses['companion'] = companion
            bonuses['companion_loyal'] = True
            if companion and (comp_attack != 0 or comp_damage != 0):
                bonuses['bonus_sources']['companion'] = {
                    'name': companion.get('custom_name') or companion['name'],
                    'attack': comp_attack,
                    'hunting': companion_data['bonuses'].get('hunting', 0),
                    'damage': comp_damage,
                    'loyal': True
                }
        else:
            
            comp_attack = 0
            comp_damage = 0
            for penalty_type, value in companion_data['penalties'].items():
                if penalty_type == 'attack':
                    bonuses['attack'] += value  
                    comp_attack = value
                elif penalty_type == 'damage':
                    bonuses['damage_bonus'] += value  
                    comp_damage = value
            bonuses['companion'] = companion
            bonuses['companion_loyal'] = False
            if companion and (comp_attack != 0 or comp_damage != 0):
                bonuses['bonus_sources']['companion'] = {
                    'name': f"{companion['emoji']} {companion['name']} (disloyal)",
                    'attack': comp_attack,
                    'damage': comp_damage,
                    'loyal': False
                }

        equipment_bonuses = self.player_manager.get_equipment_bonuses(player_id, world_id)
        if equipment_bonuses['attack'] != 0:
            bonuses['attack'] += equipment_bonuses['attack']
            bonuses['bonus_sources']['equipment_attack'] = equipment_bonuses['attack']

        hunting_bonus = equipment_bonuses.get('hunting', equipment_bonuses.get('hunting_bonus', 0))
        if hunting_bonus != 0:
            bonuses['attack'] += hunting_bonus
            bonuses['bonus_sources']['hunting_bonus'] = hunting_bonus

        ranged_bonus = equipment_bonuses.get('ranged_bonus', 0)
        if ranged_bonus != 0 and weapon and weapon.get('type') == 'ranged':
            bonuses['attack'] += ranged_bonus
            bonuses['bonus_sources']['ranged_bonus'] = ranged_bonus

        bonuses['special_effects'] = equipment_bonuses.get('special_effects', {})

        level_bonuses = self.player_manager.get_level_bonuses(player_id)
        if level_bonuses['attack_bonus'] > 0 or level_bonuses['damage_bonus'] > 0:
            bonuses['attack'] += level_bonuses['attack_bonus']
            bonuses['damage_bonus'] += level_bonuses['damage_bonus']
            bonuses['crit_chance'] = level_bonuses['crit_chance']
            bonuses['bonus_sources']['level'] = {
                'level': level_bonuses['level'],
                'attack': level_bonuses['attack_bonus'],
                'damage': level_bonuses['damage_bonus'],
                'crit': level_bonuses['crit_chance']
            }
        else:
            bonuses['crit_chance'] = 0

        return bonuses

    def get_mount_speed_bonus(self, player_id: str, world_id: str) -> Dict:
        """Check if player has a mount/vehicle that gives speed bonus"""
        result = {
            'has_mount': False,
            'speed_bonus': 0.0,
            'mount_name': None,
            'message': None
        }

        world_mounts = self.mount_config.get('mounts', {}).get(world_id, {})
        if not world_mounts:
            return result

        companions = self.player_manager.get_all_companions(player_id)
        for companion in companions:
            comp_id = companion.get('id', '').lower()
            comp_name = companion.get('name', '').lower()
            loyalty = companion.get('loyalty', 0)

            for mount_id, mount_data in world_mounts.items():
                if mount_id.lower() in comp_id or mount_id.lower() in comp_name:
                    min_loyalty = mount_data.get('min_loyalty', 50)
                    if loyalty >= min_loyalty:
                        result['has_mount'] = True
                        result['speed_bonus'] = mount_data.get('speed_bonus', 0.0)
                        result['mount_name'] = companion.get('name', mount_id)

                        messages = self.mount_config.get('speed_messages', {}).get(world_id, [])
                        if messages:
                            import random
                            result['message'] = random.choice(messages)
                        return result

        player_data = self.player_manager.get_player(player_id)
        if player_data:
            world_data = player_data.get('worlds', {}).get(world_id, {})

            crafted_items = world_data.get('crafted_items', [])
            for item in crafted_items:
                item_name = item.get('name', '') if isinstance(item, dict) else str(item)
                item_lower = item_name.lower()

                for mount_id, mount_data in world_mounts.items():
                    if mount_data.get('crafted') and mount_id.lower().replace('_', ' ') in item_lower:
                        result['has_mount'] = True
                        result['speed_bonus'] = mount_data.get('speed_bonus', 0.0)
                        result['mount_name'] = item_name

                        messages = self.mount_config.get('speed_messages', {}).get(world_id, [])
                        if messages:
                            import random
                            result['message'] = random.choice(messages)
                        return result

            inventory = world_data.get('inventory', [])
            for item in inventory:
                item_name = item.get('item', item) if isinstance(item, dict) else item
                item_lower = item_name.lower() if isinstance(item_name, str) else ''

                for mount_id, mount_data in world_mounts.items():
                    if mount_data.get('crafted') and mount_id.lower().replace('_', ' ') in item_lower:
                        result['has_mount'] = True
                        result['speed_bonus'] = mount_data.get('speed_bonus', 0.0)
                        result['mount_name'] = item_name

                        messages = self.mount_config.get('speed_messages', {}).get(world_id, [])
                        if messages:
                            import random
                            result['message'] = random.choice(messages)
                        return result

        return result

    def check_cooldown(self, player_id: str, world_id: str, action_type: str) -> Optional[int]:
        """Check if action is on cooldown in a specific world (persistent)

        Speed bonuses from mounts/vehicles reduce cooldown duration!
        """
        last_action_str = self.player_manager.get_cooldown(player_id, world_id, action_type)

        if not last_action_str:
            return None

        last_action = datetime.fromisoformat(last_action_str)
        cooldown_duration = self.cooldown_times.get(action_type, 30)

        mount_info = self.get_mount_speed_bonus(player_id, world_id)
        if mount_info['has_mount']:
            speed_bonus = mount_info['speed_bonus']
            cooldown_duration = cooldown_duration * (1 - speed_bonus)

        time_passed = (datetime.now() - last_action).total_seconds()

        if time_passed >= cooldown_duration:
            return None

        return int(cooldown_duration - time_passed)

    def set_cooldown(self, player_id: str, world_id: str, action_type: str):
        """Set cooldown for an action in a specific world (persistent)"""
        self.player_manager.set_cooldown(player_id, world_id, action_type)

    @staticmethod
    def _matches_objective(quest, action_types, target, location):
        if not quest or quest.get('action_type') not in action_types:
            return False
        required = quest.get('required_location', '')
        if required not in ('', 'any') and required != location:
            return False
        normalize = lambda value: str(value or '').casefold().replace('_', ' ').strip()
        wanted, actual = normalize(quest.get('target_item')), normalize(target)
        if quest.get('action_type') == 'hack':
            return wanted in ('any', 'system', 'hack success') or bool(wanted and actual and (wanted in actual or actual in wanted))
        return bool(wanted and wanted == actual)

    def _record_party_action(self, player_id, world_id, action_types, target, location, amount=1):
        """A resolved attempt contributes once, only to its actual party objective."""
        world = self.player_manager.get_world_data(player_id, world_id)
        if not world.get('on_party_quest'):
            return None
        quest = (self.player_manager.get_party(player_id) or {}).get('current_quest')
        if not quest or quest.get('world_id') != world_id or not self._matches_objective(quest, action_types, target, location):
            return None
        if quest.get('progress', 0) >= quest.get('target_amount', 0):
            return None
        return self.player_manager.contribute_to_party_quest(player_id, world_id, amount)

    def _record_objective(self, player_id, world_id, action_types, target, location, amount=1):
        world = self.player_manager.get_world_data(player_id, world_id)
        quest = world.get('current_quest')
        progress = None
        if self._matches_objective(quest, action_types, target, location):
            progress = self.player_manager.update_quest_progress(
                player_id, world_id, quest.get('progress', 0) + amount)
        party = self._record_party_action(player_id, world_id, action_types, target, location, amount)
        if not progress and not party:
            return {'success': False, 'has_quest': bool(quest), 'message': 'No matching quest objective.'}
        result = dict(progress or party, success=True, has_quest=True)
        if party:
            result['party_progress'] = party
        result['message'] = f"Quest progress: {result['progress']}/{result['target']} {target}."
        return result
    
    def handle_collect(
        self,
        player_id: str,
        player_name: str,
        world_id: str,
        item_name: str,
        current_location: str
    ) -> Dict:
        """Handle !collect command in a specific world"""
        
        cooldown = self.check_cooldown(player_id, world_id, 'collect')
        if cooldown:
            return {
                'success': False,
                'message': f"⏳ You're still catching your breath! Wait {cooldown} seconds."
            }

        world_data = self.player_manager.get_world_data(player_id, world_id, player_name)
        quest = world_data.get('current_quest')
        
        if not quest:
            return {
                'success': False,
                'message': "You don't have an active quest! Find a quest giver."
            }

        if quest.get('action_type') not in ('collect', 'gather'):
            return {
                'success': False,
                'message': f"Your current quest is: {quest['title']} (not a collection or gathering quest)"
            }

        if item_name.lower() != quest['target_item'].lower():
            return {
                'success': False,
                'message': f"You're looking for {quest['target_item']}, not {item_name}!"
            }

        required_location = quest.get('required_location', '')
        preferred_locations = quest.get('preferred_locations', [])

        if required_location == 'any':
            if current_location in preferred_locations:
                success_threshold = 8  
            else:
                success_threshold = 14  
        else:
            
            if required_location and current_location != required_location:
                return {
                    'success': False,
                    'message': f"You need to be in #{required_location} to collect {item_name}!"
                }
            success_threshold = 10  

        if quest.get('action_type') == 'gather':
            return self.handle_free_gather(player_id, player_name, world_id, item_name, current_location)

        setup = self.get_gathering_bonuses(player_id, world_id)
        gathering_bonus = setup['bonus']

        roll = random.randint(1, 20)
        modified_roll = roll + gathering_bonus

        if modified_roll >= success_threshold:
            
            amount_found = random.randint(1, 3)

            if gathering_bonus >= 3:
                amount_found += random.randint(1, 2)
            elif gathering_bonus >= 1:
                amount_found += 1 if random.random() < 0.5 else 0

            critical = False
            if roll == 20:
                amount_found *= 2
                critical = True

            new_progress = quest['progress'] + amount_found
            result = self.player_manager.update_quest_progress(player_id, world_id, new_progress)

            self.set_cooldown(player_id, world_id, 'collect')

            bonus_text = ""
            if gathering_bonus > 0:
                bonus_text = f" 🐱+{gathering_bonus}"

            party_msg = ""
            party_result = self._record_party_action(player_id, world_id, ('collect',), item_name, current_location, amount_found)
            if party_result and party_result.get('success'):
                party_msg = f"\n👥 Party: {party_result['progress']}/{party_result['target']}"

            return {
                'success': True,
                'roll': roll,
                'modified_roll': modified_roll,
                'gathering_bonus': gathering_bonus,
                'bonus_sources': setup['bonus_sources'],
                'difficulty': success_threshold,
                'amount_found': amount_found,
                'critical': critical,
                'progress': result['progress'],
                'target': result['target'],
                'completed': result['completed'],
                'message': f"Success! Found {amount_found} {item_name}!{bonus_text} ({result['progress']}/{result['target']}){party_msg}"
            }
        else:
            
            self.set_cooldown(player_id, world_id, 'collect')

            bonus_text = ""
            if gathering_bonus > 0:
                bonus_text = f" (Roll: {roll}+{gathering_bonus}={modified_roll})"

            return {
                'success': False,
                'roll': roll,
                'modified_roll': modified_roll,
                'gathering_bonus': gathering_bonus,
                'bonus_sources': setup['bonus_sources'],
                'difficulty': success_threshold,
                'message': f"You search but don't find any {item_name}.{bonus_text} Try again!"
            }
    
    def _finish_companion_combat(self, player_id, world_id, result):
        events=getattr(self.player_manager,'_combat_care_events',{}).pop(str(player_id),[])
        wear=[entry for event in events for entry in event.get('item_wear',[])]
        injuries=[event['companion_injury'] for event in events if event.get('companion_injury')]
        if result.get('combat'):
            wear_fn=getattr(self.player_manager,'wear_equipment',None)
            if callable(wear_fn):wear.extend(wear_fn(player_id,world_id,weapon=True,armor=False))
        if wear:
            result['item_wear']=wear
            for entry in wear:
                if entry.get('message'):result['message']+='\n'+entry['message']
        if injuries:result['companion_injuries']=injuries
        recovery = getattr(self.player_manager, 'companion_combat_recovery', None)
        if result.get('combat') and callable(recovery):
            healed = recovery(player_id, world_id)
            if healed and healed.get('healed'):
                result['companion_recovery'] = healed
                result['message'] += '\n'+healed['name']+' restores '+str(healed['healed'])+' HP.'
        return result

    def _run_hunting_combat(self, player_id, world_id, handler, *args):
        manager=self.player_manager
        if not hasattr(manager,'_hunting_actors'):manager._hunting_actors=set()
        if not hasattr(manager,'_combat_care_events'):manager._combat_care_events={}
        actor=str(player_id)
        manager._hunting_actors.add(actor)
        manager._combat_care_events[actor]=[]
        try:
            result=handler(*args)
        except Exception:
            manager._combat_care_events.pop(actor,None)
            raise
        finally:
            manager._hunting_actors.discard(actor)
        if result.get('success'):
            party = self._record_party_action(player_id, world_id, ('hunt',), args[3], args[4])
            if party:
                result['party_progress'] = party
                result['message'] += f"\n👥 Party: {party['progress']}/{party['target']}"
        return self._finish_companion_combat(player_id,world_id,result)

    def handle_hunt(self, player_id, player_name, world_id, creature_name, current_location, approach_bonus=0):
        return self._run_hunting_combat(player_id,world_id,self._handle_hunt,
            player_id,player_name,world_id,creature_name,current_location,approach_bonus)

    def _handle_hunt(
        self,
        player_id: str,
        player_name: str,
        world_id: str,
        creature_name: str,
        current_location: str,
        approach_bonus: int = 0
    ) -> Dict:
        """Handle !hunt command with combat in a specific world. approach_bonus is from roleplay descriptions."""
        
        cooldown = self.check_cooldown(player_id, world_id, 'hunt')
        if cooldown:
            return {
                'success': False,
                'message': f"⏳ You're still recovering! Wait {cooldown} seconds."
            }

        world_data = self.player_manager.get_world_data(player_id, world_id, player_name)
        quest = world_data.get('current_quest')
        
        if not quest:
            return {
                'success': False,
                'message': "You don't have an active quest! Find a quest giver."
            }

        if quest.get('action_type') != 'hunt':
            return {
                'success': False,
                'message': f"Your current quest is: {quest['title']} (not a hunting quest)"
            }

        if creature_name.lower() != quest['target_item'].lower():
            return {
                'success': False,
                'message': f"You're hunting {quest['target_item']}, not {creature_name}!"
            }

        required_location = quest.get('required_location', '')
        if required_location not in ('', 'any') and current_location != required_location:
            return {
                'success': False,
                'message': f"You need to be in #{required_location} to hunt {creature_name}!"
            }

        bonuses = self.get_combat_bonuses(player_id, world_id)

        if approach_bonus > 0:
            bonuses['attack'] += approach_bonus
            bonuses['bonus_sources']['approach'] = approach_bonus

        weapon = bonuses.get('weapon')
        ammo_used = None
        ammo_damage_bonus = 0
        if weapon and weapon.get('requires_ammo'):
            ammo_type = weapon.get('ammo_type', 'arrows')

            world_data = self.player_manager.get_world_data(player_id, world_id)
            inventory = world_data.get('inventory', [])

            def has_arrow_type(inv, arrow_type):
                arrow_lower = arrow_type.lower()
                for item in inv:
                    
                    item_name = item.get('name', '') if isinstance(item, dict) else item
                    item_lower = item_name.lower() if item_name else ''
                    
                    if item_lower == arrow_lower or item_lower == arrow_lower + 's':
                        return item_name
                    if item_lower.startswith(arrow_lower) and ('(' in item_lower or item_lower.endswith('s')):
                        return item_name
                return None

            bone_arrow = has_arrow_type(inventory, 'Bone Arrow')
            regular_arrow = has_arrow_type(inventory, 'Arrow')

            if bone_arrow:
                ammo_used = bone_arrow
                ammo_damage_bonus = 2  
            elif regular_arrow:
                ammo_used = regular_arrow
                ammo_damage_bonus = 0
            else:
                return {
                    'success': False,
                    'message': f"🏹 You need Arrows to use {weapon['name']}! Buy some from Victor or a merchant."
                }

            bonuses['damage_bonus'] += ammo_damage_bonus
            if ammo_damage_bonus > 0:
                bonuses['bonus_sources']['ammo'] = {
                    'name': ammo_used,
                    'damage': ammo_damage_bonus
                }

        hunting_areas = self.hunting_config.get('hunting_areas', {})
        world_areas = hunting_areas.get(world_id, {})
        animal_config = None

        for area_name, area_data in world_areas.items():
            for animal in area_data.get('animals', []):
                if animal['name'].lower() == creature_name.lower():
                    animal_config = animal
                    break
            if animal_config:
                break

        creature_ac = animal_config.get('ac', 12) if animal_config else 12
        creature_damage = animal_config.get('damage', 0) if animal_config else 0

        player_roll = random.randint(1, 20)
        player_attack = player_roll + bonuses['attack']

        combat_result = {
            'player_roll': player_roll,
            'player_attack': player_attack,
            'creature_ac': creature_ac,
            'critical': False,
            'fumble': False,
            'attack_bonus': bonuses['attack'],
            'damage_die': bonuses['damage_die'],
            'damage_bonus': bonuses['damage_bonus'],
            'bonus_sources': bonuses.get('bonus_sources', {}),
            'level_crit': False
        }

        crit_chance = bonuses.get('crit_chance', 0)
        is_crit = player_roll == 20
        if not is_crit and crit_chance > 0 and player_roll >= 15:
            
            if random.randint(1, 100) <= crit_chance:
                is_crit = True
                combat_result['level_crit'] = True

        if is_crit:
            combat_result['critical'] = True
            damage_roll_1 = self.roll_damage_die(bonuses['damage_die'])
            damage_roll_2 = self.roll_damage_die(bonuses['damage_die'])
            damage = damage_roll_1 + damage_roll_2 + bonuses['damage_bonus']
            combat_result['damage'] = damage
            combat_result['damage_rolls'] = [damage_roll_1, damage_roll_2]

            if weapon and weapon.get('requires_ammo') and ammo_used:
                self.player_manager.remove_item(player_id, world_id, ammo_used, 1)

            if damage >= 4:
                
                new_progress = quest['progress'] + 1
                result = self.player_manager.update_quest_progress(player_id, world_id, new_progress)

                loyalty_result = self.player_manager.modify_companion_loyalty(player_id, 5, "successful hunt")

                new_achievements = self.player_manager.record_hunt(player_id)

                self.set_cooldown(player_id, world_id, 'hunt')

                response_msg = f"Defeated {creature_name}! ({result['progress']}/{result['target']})"

                if loyalty_result.get('success'):
                    response_msg += f"\n💖 Loyalty: {loyalty_result['new_loyalty']}% (+5%)"
                    if loyalty_result.get('crossed_threshold') == 'gained':
                        response_msg += f"\n✨ **Your companion is now LOYAL!** Bonuses activated!"

                return {
                    'success': True,
                    'combat': True,
                    'rolls': combat_result,
                    'progress': result['progress'],
                    'target': result['target'],
                    'completed': result['completed'],
                    'new_achievements': new_achievements,
                    'message': response_msg
                }
            else:
                
                self.set_cooldown(player_id, world_id, 'hunt')

                return {
                    'success': False,
                    'combat': True,
                    'rolls': combat_result,
                    'message': f"Hit for {damage} damage, but {creature_name} escaped!"
                }

        elif player_roll == 1:
            combat_result['fumble'] = True
            if player_attack >= creature_ac:
                
                damage_roll_1 = self.roll_damage_die(bonuses['damage_die'])
                damage_roll_2 = self.roll_damage_die(bonuses['damage_die'])
                damage = min(damage_roll_1, damage_roll_2) + bonuses['damage_bonus']
                combat_result['damage'] = damage
                combat_result['damage_rolls'] = [damage_roll_1, damage_roll_2]

                if weapon and weapon.get('requires_ammo') and ammo_used:
                    self.player_manager.remove_item(player_id, world_id, ammo_used, 1)

                if damage >= 4:
                    
                    new_progress = quest['progress'] + 1
                    result = self.player_manager.update_quest_progress(player_id, world_id, new_progress)

                    new_achievements = self.player_manager.record_hunt(player_id)

                    self.set_cooldown(player_id, world_id, 'hunt')

                    return {
                        'success': True,
                        'combat': True,
                        'rolls': combat_result,
                        'progress': result['progress'],
                        'target': result['target'],
                        'completed': result['completed'],
                        'new_achievements': new_achievements,
                        'message': f"Barely defeated {creature_name}! ({result['progress']}/{result['target']})"
                    }
                else:
                    
                    self.set_cooldown(player_id, world_id, 'hunt')

                    return {
                        'success': False,
                        'combat': True,
                        'rolls': combat_result,
                        'message': f"Weak hit for {damage} damage, {creature_name} escaped!"
                    }
            else:
                
                self.set_cooldown(player_id, world_id, 'hunt')

                response_msg = f"Complete miss! {creature_name} got away!"

                if creature_damage > 0:
                    fumble_damage = creature_damage * 2
                    damage_result = self.player_manager.take_damage(player_id, world_id, fumble_damage, creature_name)
                    response_msg += f"\n💥 **FUMBLE!** The {creature_name} takes advantage! {damage_result['message']}"
                    if damage_result.get('knocked_out'):
                        response_msg += f"\n💀 You've been knocked out!"

                return {
                    'success': False,
                    'combat': True,
                    'rolls': combat_result,
                    'message': response_msg
                }

        elif player_attack >= creature_ac:
            damage = self.roll_damage_die(bonuses['damage_die']) + bonuses['damage_bonus']
            combat_result['damage'] = damage

            if weapon and weapon.get('requires_ammo') and ammo_used:
                self.player_manager.remove_item(player_id, world_id, ammo_used, 1)

            if damage >= 4:
                
                new_progress = quest['progress'] + 1
                result = self.player_manager.update_quest_progress(player_id, world_id, new_progress)

                loyalty_result = self.player_manager.modify_companion_loyalty(player_id, 5, "successful hunt")

                new_achievements = self.player_manager.record_hunt(player_id)

                self.set_cooldown(player_id, world_id, 'hunt')

                response_msg = f"Defeated {creature_name}! ({result['progress']}/{result['target']})"

                if loyalty_result.get('success'):
                    response_msg += f"\n💖 Loyalty: {loyalty_result['new_loyalty']}% (+5%)"
                    if loyalty_result.get('crossed_threshold') == 'gained':
                        response_msg += f"\n✨ **Your companion is now LOYAL!** Bonuses activated!"

                return {
                    'success': True,
                    'combat': True,
                    'rolls': combat_result,
                    'progress': result['progress'],
                    'target': result['target'],
                    'completed': result['completed'],
                    'new_achievements': new_achievements,
                    'message': response_msg
                }
            else:
                
                self.set_cooldown(player_id, world_id, 'hunt')

                return {
                    'success': False,
                    'combat': True,
                    'rolls': combat_result,
                    'message': f"Hit for {damage} damage, but {creature_name} escaped!"
                }
        else:

            loyalty_result = self.player_manager.modify_companion_loyalty(player_id, -2, "failed hunt")

            self.set_cooldown(player_id, world_id, 'hunt')

            response_msg = f"Missed! {creature_name} got away!"

            if creature_damage > 0:
                damage_result = self.player_manager.take_damage(player_id, world_id, creature_damage, creature_name)
                response_msg += f"\n⚔️ The {creature_name} strikes back! {damage_result['message']}"
                if damage_result.get('knocked_out'):
                    response_msg += f"\n💀 You've been knocked out and limp back to safety..."

            if loyalty_result.get('success'):
                response_msg += f"\n💔 Loyalty: {loyalty_result['new_loyalty']}% (-2%)"
                if loyalty_result.get('crossed_threshold') == 'lost':
                    response_msg += f"\n⚠️ **Your companion no longer trusts you!** Penalties active."

            return {
                'success': False,
                'combat': True,
                'rolls': combat_result,
                'message': response_msg
            }

    def handle_search(
        self,
        player_id: str,
        player_name: str,
        world_id: str,
        current_location: str
    ) -> Dict:
        """Handle !search command in a specific world"""
        
        cooldown = self.check_cooldown(player_id, world_id, 'search')
        if cooldown:
            return {
                'success': False,
                'message': f"⏳ You're still searching! Wait {cooldown} seconds."
            }

        world_data = self.player_manager.get_world_data(player_id, world_id, player_name)
        quest = world_data.get('current_quest')
        
        if not quest:
            return {
                'success': False,
                'message': "You search around but find nothing interesting..."
            }

        required_location = quest.get('required_location', '')
        if required_location and required_location != 'any' and current_location != required_location:
            return {
                'success': False,
                'message': f"You need to search in #{required_location} for quest items!"
            }

        roll = random.randint(1, 20)
        
        if roll >= 12:
            
            action_type = quest.get('action_type', 'unknown')
            target = quest['target_item']
            
            self.set_cooldown(player_id, world_id, 'search')
            
            hints = {
                'collect': f"You spot some {target} nearby! Use `!collect {target}` to gather them.",
                'hunt': f"You hear something... A {target} is near! Use `!hunt {target}` to pursue it.",
                'deliver': f"You check your quest log. Bring the {target} back to complete your quest!"
            }
            
            return {
                'success': True,
                'roll': roll,
                'message': hints.get(action_type, "You find something interesting...")
            }
        else:
            self.set_cooldown(player_id, world_id, 'search')

            return {
                'success': False,
                'roll': roll,
                'message': "You search carefully but don't find anything useful."
            }

    def record_tame(
        self,
        player_id: str,
        world_id: str,
        player_name: str,
        creature_name: str,
        current_location: str
    ) -> Dict:
        """Count a successful bond for the matching solo and party objectives."""
        return self._record_objective(player_id, world_id, ('tame',), creature_name, current_location)

    def record_hack(
        self,
        player_id: str,
        world_id: str,
        player_name: str,
        system_name: str,
        current_location: str
    ) -> Dict:
        """Count a successful breach for the matching solo and party objectives."""
        return self._record_objective(player_id, world_id, ('hack',), system_name, current_location)

    def format_combat_result(self, combat_data: Dict) -> str:
        """Format combat rolls for display"""
        lines = []

        attack_bonus = combat_data.get('attack_bonus', 3)
        damage_die = combat_data.get('damage_die', '1d6')
        damage_bonus = combat_data.get('damage_bonus', 0)

        lines.append("⚔️ **Combat!**")

        bonus_sources = combat_data.get('bonus_sources', {})
        if bonus_sources:
            breakdown_parts = []
            if bonus_sources.get('base'):
                breakdown_parts.append(f"Base +{bonus_sources['base']}")
            if bonus_sources.get('weapon'):
                weapon_info = bonus_sources['weapon']
                breakdown_parts.append(f"{weapon_info['name']} +{weapon_info['attack']}")
            if bonus_sources.get('form'):
                form_info = bonus_sources['form']
                breakdown_parts.append(f"{form_info['name']} +{form_info['attack']}")
            if bonus_sources.get('companion'):
                comp_info = bonus_sources['companion']
                sign = "+" if comp_info['attack'] >= 0 else ""
                breakdown_parts.append(f"{comp_info['name']} {sign}{comp_info['attack']}")
            if bonus_sources.get('ammo'):
                ammo_info = bonus_sources['ammo']
                breakdown_parts.append(f"{ammo_info['name']} +{ammo_info['damage']} dmg")

            if breakdown_parts:
                lines.append(f"📊 **Bonuses:** {', '.join(breakdown_parts)}")

        lines.append(f"🎲 Attack Roll: 1d20+{attack_bonus} = [{combat_data['player_roll']}]+{attack_bonus} = **{combat_data['player_attack']}**")
        lines.append(f"🛡️ Target AC: {combat_data['creature_ac']}")

        if combat_data.get('critical'):
            if 'damage_rolls' in combat_data:
                rolls = combat_data['damage_rolls']
                bonus_text = f"+{damage_bonus}" if damage_bonus > 0 else ""
                lines.append(f"💥 🎯 **CRITICAL HIT!** Damage: {damage_die}+{damage_die}{bonus_text} = [{rolls[0]}]+[{rolls[1]}]{bonus_text} = **{combat_data['damage']}**")
            else:
                lines.append(f"💥 🎯 **CRITICAL HIT!** Damage: **{combat_data['damage']}**")

        elif combat_data.get('fumble'):
            if combat_data['player_attack'] >= combat_data['creature_ac']:
                if 'damage_rolls' in combat_data:
                    rolls = combat_data['damage_rolls']
                    bonus_text = f"+{damage_bonus}" if damage_bonus > 0 else ""
                    lines.append(f"💥 **FUMBLE! (Disadvantage)** HIT! Damage: {damage_die} dis{bonus_text} = [{rolls[0]}, {rolls[1]}] (lower){bonus_text} = **{combat_data['damage']}**")
                else:
                    lines.append(f"💥 **FUMBLE!** But still hit! Damage: **{combat_data['damage']}**")
            else:
                lines.append("💥 **FUMBLE!** Complete miss!")

        elif combat_data['player_attack'] >= combat_data['creature_ac']:
            if 'damage' in combat_data:
                bonus_text = f"+{damage_bonus}" if damage_bonus > 0 else ""
                lines.append(f"💥 **HIT!** Damage: {damage_die}{bonus_text} = **{combat_data['damage']}**")
        else:
            lines.append("❌ **MISS!**")

        return '\n'.join(lines)

    def handle_free_hunt(self, player_id, player_name, world_id, creature_name, current_location, approach_bonus=0):
        return self._run_hunting_combat(player_id,world_id,self._handle_free_hunt,
            player_id,player_name,world_id,creature_name,current_location,approach_bonus)

    def _handle_free_hunt(
        self,
        player_id: str,
        player_name: str,
        world_id: str,
        creature_name: str,
        current_location: str,
        approach_bonus: int = 0
    ) -> Dict:
        """Handle free hunting (without a quest) in designated hunting areas. approach_bonus is from roleplay descriptions."""
        
        hunting_areas = self.hunting_config.get('hunting_areas', {})
        world_areas = hunting_areas.get(world_id, {})

        if current_location not in world_areas:
            return {
                'success': False,
                'message': f"You can't hunt in #{current_location}. Try designated hunting areas!"
            }

        available_animals = world_areas[current_location].get('animals', [])
        animal_config = None
        for animal in available_animals:
            if animal['name'].lower() == creature_name.lower():
                animal_config = animal
                break

        if not animal_config:
            available_names = [a['name'] for a in available_animals]
            if available_names:
                return {
                    'success': False,
                    'message': f"No {creature_name} here. Available: {', '.join(available_names)}"
                }
            else:
                return {
                    'success': False,
                    'message': f"This area has no huntable animals."
                }

        cooldown = self.check_cooldown(player_id, world_id, 'hunt')
        if cooldown:
            return {
                'success': False,
                'message': f"⏳ You're still recovering! Wait {cooldown} seconds."
            }

        bonuses = self.get_combat_bonuses(player_id, world_id)

        if approach_bonus > 0:
            bonuses['attack'] += approach_bonus
            bonuses['bonus_sources']['approach'] = approach_bonus

        weapon = bonuses.get('weapon')
        ammo_used = None
        ammo_damage_bonus = 0
        if weapon and weapon.get('requires_ammo'):
            ammo_type = weapon.get('ammo_type', 'arrows')

            world_data = self.player_manager.get_world_data(player_id, world_id)
            inventory = world_data.get('inventory', [])

            def has_arrow_type(inv, arrow_type):
                arrow_lower = arrow_type.lower()
                for item in inv:
                    
                    item_name = item.get('name', '') if isinstance(item, dict) else item
                    item_lower = item_name.lower() if item_name else ''
                    
                    if item_lower == arrow_lower or item_lower == arrow_lower + 's':
                        return item_name
                    if item_lower.startswith(arrow_lower) and ('(' in item_lower or item_lower.endswith('s')):
                        return item_name
                return None

            bone_arrow = has_arrow_type(inventory, 'Bone Arrow')
            regular_arrow = has_arrow_type(inventory, 'Arrow')

            if bone_arrow:
                ammo_used = bone_arrow
                ammo_damage_bonus = 2  
            elif regular_arrow:
                ammo_used = regular_arrow
                ammo_damage_bonus = 0
            else:
                return {
                    'success': False,
                    'message': f"🏹 You need Arrows to use {weapon['name']}! Buy some from Victor or a merchant."
                }

            bonuses['damage_bonus'] += ammo_damage_bonus
            if ammo_damage_bonus > 0:
                bonuses['bonus_sources']['ammo'] = {
                    'name': ammo_used,
                    'damage': ammo_damage_bonus
                }

        player_roll = random.randint(1, 20)
        player_attack = player_roll + bonuses['attack']
        creature_ac = animal_config['ac']

        combat_result = {
            'player_roll': player_roll,
            'player_attack': player_attack,
            'creature_ac': creature_ac,
            'critical': False,
            'fumble': False,
            'attack_bonus': bonuses['attack'],
            'damage_die': bonuses['damage_die'],
            'damage_bonus': bonuses['damage_bonus'],
            'bonus_sources': bonuses.get('bonus_sources', {}),
            'level_crit': False
        }

        crit_chance = bonuses.get('crit_chance', 0)
        is_crit = player_roll == 20
        if not is_crit and crit_chance > 0 and player_roll >= 15:
            
            if random.randint(1, 100) <= crit_chance:
                is_crit = True
                combat_result['level_crit'] = True

        if is_crit:
            combat_result['critical'] = True
            damage_roll_1 = self.roll_damage_die(bonuses['damage_die'])
            damage_roll_2 = self.roll_damage_die(bonuses['damage_die'])
            damage = damage_roll_1 + damage_roll_2 + bonuses['damage_bonus']
            combat_result['damage'] = damage
            combat_result['damage_rolls'] = [damage_roll_1, damage_roll_2]

            if weapon and weapon.get('requires_ammo') and ammo_used:
                self.player_manager.remove_item(player_id, world_id, ammo_used, 1)

            if damage >= animal_config['health']:

                spoil_days = self.hunting_config['processing_recipes'].get(
                    creature_name.lower(), {}
                ).get('spoil_days', 7)

                self.player_manager.add_perishable_item(
                    player_id, world_id, creature_name, spoil_days
                )

                loyalty_result = self.player_manager.modify_companion_loyalty(player_id, 5, "successful hunt")

                new_achievements = self.player_manager.record_hunt(player_id)

                self.set_cooldown(player_id, world_id, 'hunt')

                response_msg = f"Successfully hunted {creature_name}! Added to inventory.\n💡 Use `!process {creature_name}` within {spoil_days} days or it will spoil!"

                if loyalty_result.get('success'):
                    response_msg += f"\n💖 Loyalty: {loyalty_result['new_loyalty']}% (+5%)"
                    if loyalty_result.get('crossed_threshold') == 'gained':
                        response_msg += f"\n✨ **Your companion is now LOYAL!** Bonuses activated!"

                return {
                    'success': True,
                    'combat': True,
                    'rolls': combat_result,
                    'new_achievements': new_achievements,
                    'message': response_msg
                }
            else:
                
                self.set_cooldown(player_id, world_id, 'hunt')

                return {
                    'success': False,
                    'combat': True,
                    'rolls': combat_result,
                    'message': f"Hit for {damage} damage, but {creature_name} escaped!"
                }

        elif player_roll == 1:
            combat_result['fumble'] = True
            if player_attack >= creature_ac:
                
                damage_roll_1 = self.roll_damage_die(bonuses['damage_die'])
                damage_roll_2 = self.roll_damage_die(bonuses['damage_die'])
                damage = min(damage_roll_1, damage_roll_2) + bonuses['damage_bonus']
                combat_result['damage'] = damage
                combat_result['damage_rolls'] = [damage_roll_1, damage_roll_2]

                if weapon and weapon.get('requires_ammo') and ammo_used:
                    self.player_manager.remove_item(player_id, world_id, ammo_used, 1)

                if damage >= animal_config['health']:
                    
                    spoil_days = self.hunting_config['processing_recipes'].get(
                        creature_name.lower(), {}
                    ).get('spoil_days', 7)

                    self.player_manager.add_perishable_item(
                        player_id, world_id, creature_name, spoil_days
                    )

                    new_achievements = self.player_manager.record_hunt(player_id)

                    self.set_cooldown(player_id, world_id, 'hunt')

                    return {
                        'success': True,
                        'combat': True,
                        'rolls': combat_result,
                        'new_achievements': new_achievements,
                        'message': f"Barely managed to hunt {creature_name}! Added to inventory.\n💡 Use `!process {creature_name}` within {spoil_days} days or it will spoil!"
                    }
                else:
                    
                    self.set_cooldown(player_id, world_id, 'hunt')

                    return {
                        'success': False,
                        'combat': True,
                        'rolls': combat_result,
                        'message': f"Weak hit for {damage} damage, {creature_name} escaped!"
                    }
            else:
                
                self.set_cooldown(player_id, world_id, 'hunt')

                creature_damage = animal_config.get('damage', 0)
                response_msg = f"Complete miss! {creature_name} got away!"

                if creature_damage > 0:
                    fumble_damage = creature_damage * 2
                    damage_result = self.player_manager.take_damage(player_id, world_id, fumble_damage, creature_name)
                    response_msg += f"\n💥 **FUMBLE!** The {creature_name} takes advantage! {damage_result['message']}"
                    if damage_result.get('knocked_out'):
                        response_msg += f"\n💀 You've been knocked out!"

                return {
                    'success': False,
                    'combat': True,
                    'rolls': combat_result,
                    'message': response_msg
                }

        elif player_attack >= creature_ac:
            damage = self.roll_damage_die(bonuses['damage_die']) + bonuses['damage_bonus']
            combat_result['damage'] = damage

            if weapon and weapon.get('requires_ammo') and ammo_used:
                self.player_manager.remove_item(player_id, world_id, ammo_used, 1)

            if damage >= animal_config['health']:
                
                spoil_days = self.hunting_config['processing_recipes'].get(
                    creature_name.lower(), {}
                ).get('spoil_days', 7)

                self.player_manager.add_perishable_item(
                    player_id, world_id, creature_name, spoil_days
                )

                loyalty_result = self.player_manager.modify_companion_loyalty(player_id, 5, "successful hunt")

                new_achievements = self.player_manager.record_hunt(player_id)

                self.set_cooldown(player_id, world_id, 'hunt')

                response_msg = f"Successfully hunted {creature_name}! Added to inventory.\n💡 Use `!process {creature_name}` within {spoil_days} days or it will spoil!"

                if loyalty_result.get('success'):
                    response_msg += f"\n💖 Loyalty: {loyalty_result['new_loyalty']}% (+5%)"
                    if loyalty_result.get('crossed_threshold') == 'gained':
                        response_msg += f"\n✨ **Your companion is now LOYAL!** Bonuses activated!"

                return {
                    'success': True,
                    'combat': True,
                    'rolls': combat_result,
                    'new_achievements': new_achievements,
                    'message': response_msg
                }
            else:
                
                self.set_cooldown(player_id, world_id, 'hunt')

                return {
                    'success': False,
                    'combat': True,
                    'rolls': combat_result,
                    'message': f"Hit for {damage} damage, but {creature_name} escaped!"
                }
        else:

            loyalty_result = self.player_manager.modify_companion_loyalty(player_id, -2, "failed hunt")

            self.set_cooldown(player_id, world_id, 'hunt')

            response_msg = f"Missed! {creature_name} got away!"

            creature_damage = animal_config.get('damage', 0)
            if creature_damage > 0:
                damage_result = self.player_manager.take_damage(player_id, world_id, creature_damage, creature_name)
                response_msg += f"\n⚔️ The {creature_name} strikes back! {damage_result['message']}"
                if damage_result.get('knocked_out'):
                    response_msg += f"\n💀 You've been knocked out and limp back to safety..."

            if loyalty_result.get('success'):
                response_msg += f"\n💔 Loyalty: {loyalty_result['new_loyalty']}% (-2%)"
                if loyalty_result.get('crossed_threshold') == 'lost':
                    response_msg += f"\n⚠️ **Your companion no longer trusts you!** Penalties active."

            return {
                'success': False,
                'combat': True,
                'rolls': combat_result,
                'message': response_msg
            }

    def get_gathering_bonuses(self, player_id, world_id):
        form = self.player_manager.get_current_form(player_id)
        levels = self.player_manager.get_level_bonuses(player_id)
        equipment = self.player_manager.get_equipment_bonuses(player_id, world_id)
        companion = self.player_manager.get_companion(player_id)
        companion_bonus = self.player_manager.get_companion_bonuses(player_id).get('bonuses', {}).get('gathering', 0)
        sources = {'form':form.get('bonuses', {}).get('gathering', 0),
            'level':levels.get('gather_bonus', 0),
            'equipment':equipment.get('gathering', equipment.get('gathering_bonus', 0))}
        total = sum(sources.values()) + companion_bonus
        if companion and companion_bonus:
            sources['companion'] = {'name':companion.get('custom_name') or companion['name'],
                'gathering':companion_bonus}
        return {'bonus':total, 'bonus_sources':sources}

    def handle_free_gather(
        self, player_id, player_name, world_id, resource_name, current_location
    ):
        result=self._handle_free_gather(player_id,player_name,world_id,resource_name,current_location)
        if result.get('success'):
            progress = self._record_objective(player_id, world_id, ('gather', 'collect'),
                resource_name, current_location, result.get('quantity', 0))
            if progress.get('success'):
                result['quest_progress'] = progress
                result['message'] += '\n' + progress['message']
        rolls=result.get('rolls',{})
        damage_item=getattr(self.player_manager,'damage_item',None)
        if rolls.get('tool_bonus') and callable(damage_item):
            wear=damage_item(player_id,world_id,rolls['tool_name'],1,item_id=rolls.get('tool_id'))
            if wear.get('wear'):
                result['item_wear']=[wear]
                if wear.get('message'):result['message']+='\n'+wear['message']
        return result

    def _handle_free_gather(
        self,
        player_id: str,
        player_name: str,
        world_id: str,
        resource_name: str,
        current_location: str
    ) -> Dict:
        """Handle gathering resources in designated gathering areas."""
        
        gathering_areas = self.gathering_config.get('gathering_areas', {})
        world_areas = gathering_areas.get(world_id, {})

        if current_location not in world_areas:
            return {
                'success': False,
                'message': f"You can't gather resources here. Try the forest or wilds!"
            }

        available_resources = world_areas[current_location].get('resources', [])
        resource_config = None
        for resource in available_resources:
            if resource['name'].lower() == resource_name.lower():
                resource_config = resource
                break

        if not resource_config:
            available_names = [r['name'] for r in available_resources]
            if available_names:
                return {
                    'success': False,
                    'message': f"No {resource_name} here. Available: {', '.join(available_names)}"
                }
            return {
                'success': False,
                'message': f"No resources available to gather here."
            }

        cooldown_left = self.check_cooldown(player_id, world_id, 'gather')
        if cooldown_left:
            return {
                'success': False,
                'message': f"You need to rest before gathering again. Wait {cooldown_left} seconds."
            }

        world_data = self.player_manager.get_world_data(player_id, world_id)
        inventory = world_data.get('inventory', [])

        tool_bonus = 0
        node_config = self.gathering_config.get('resource_nodes', {}).get(resource_config.get('category', 'herb'), {})
        bonus_tool = node_config.get('tool_bonus', '')
        used_tool=None

        if bonus_tool:
            condition_items=getattr(self.player_manager,'inventory_condition_items',None)
            if callable(condition_items):
                choices=condition_items(player_id,bonus_tool)+[
                    row for row in world_data.get('crafted_items',[]) if row.get('name')==bonus_tool and not row.get('placed')]
                owned=getattr(self.player_manager,'_owned_item',None)
                if callable(owned):
                    choices=[owned(player_id,item_id=row.get('id'))[1] for row in choices]
                choices=[row for row in choices if row and row.get('durability',1)>0]
                if choices:
                    used_tool=max(choices,key=lambda row:row.get('durability',1))
                    tool_bonus=2
            
            else:
                for item in inventory:
                    item_name = item.get('name') if isinstance(item, dict) else item
                    if item_name == bonus_tool:
                        tool_bonus = 2
                        break

        player_roll = random.randint(1, 20)
        setup = self.get_gathering_bonuses(player_id, world_id)
        total_roll = player_roll + tool_bonus + setup['bonus']
        difficulty = resource_config['difficulty']

        gather_result = {
            'player_roll': player_roll,
            'total_roll': total_roll,
            'difficulty': difficulty,
            'tool_bonus': tool_bonus,
            'tool_name':bonus_tool if tool_bonus else None,
            'tool_id':used_tool.get('id') if used_tool else None,
            'gathering_bonus': setup['bonus'],
            'bonus_sources': dict(setup['bonus_sources'], tool=tool_bonus),
            'critical': player_roll == 20,
            'fumble': player_roll == 1
        }

        self.set_cooldown(player_id, world_id, 'gather')

        if player_roll == 20:
            yield_range = resource_config.get('yield', {'min': 1, 'max': 1})
            
            quantity = random.randint(yield_range['min'], yield_range['max'] * 2)

            self.player_manager.add_item(player_id, world_id, resource_name, quantity)

            icon = node_config.get('icon', '🌿')
            return {
                'success': True,
                'rolls': gather_result,
                'quantity': quantity,
                'item': resource_name,
                'message': f"{icon} **CRITICAL SUCCESS!** You gathered {quantity}x {resource_name}!"
            }

        if player_roll == 1:
            return {
                'success': False,
                'rolls': gather_result,
                'message': f"**FUMBLE!** You stumbled and found nothing useful."
            }

        if total_roll >= difficulty:
            yield_range = resource_config.get('yield', {'min': 1, 'max': 1})
            quantity = random.randint(yield_range['min'], yield_range['max'])

            self.player_manager.add_item(player_id, world_id, resource_name, quantity)

            icon = node_config.get('icon', '🌿')
            tool_msg = f" (+2 from {bonus_tool})" if tool_bonus > 0 else ""
            return {
                'success': True,
                'rolls': gather_result,
                'quantity': quantity,
                'item': resource_name,
                'message': f"{icon} Gathered {quantity}x {resource_name}!{tool_msg}"
            }

        return {
            'success': False,
            'rolls': gather_result,
            'message': f"Couldn't find any {resource_name}. Try again!"
        }
