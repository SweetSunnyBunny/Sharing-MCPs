
"""
Multi-World Player Data Management System
Tracks player progress across multiple game worlds
"""

import json
import os
import random
import shutil
import tempfile
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List
from .item_names import same_item

try:
    from filelock import FileLock
    HAS_FILELOCK = True
except ImportError:
    HAS_FILELOCK = False
    print("⚠️ filelock not installed. Run: pip install filelock")
    print("   Without it, concurrent bot access may cause data corruption.")

class MultiWorldPlayerManager:
    """Manages player data across multiple game worlds"""

    def __init__(self, data_file: Path, worlds_config: Dict):
        
        if data_file.name == 'players.json':
            self.players_dir = data_file.parent / 'players'
            self.legacy_file = data_file  
        else:
            self.players_dir = data_file
            self.legacy_file = None

        self.data_file = data_file  
        self.base_dir = Path(__file__).parent  
        self.worlds_config = worlds_config
        self.players = self._load_players()

        import json
        crafting_path = Path(__file__).parent / "crafting_recipes.json"
        if crafting_path.exists():
            try:
                with open(crafting_path, 'r', encoding='utf-8') as f:
                    self.crafting_config = json.load(f)
            except Exception as e:
                print(f"⚠️ Failed to load crafting_recipes.json: {e}")
                print(f"ℹ️ Using empty crafting config")
                self.crafting_config = {'recipes': {}, 'repair_costs': {}, 'durability_loss': {}}
        else:
            self.crafting_config = {'recipes': {}, 'repair_costs': {}, 'durability_loss': {}}

        bs_crafting_path = Path(__file__).parent / "crafting_recipes_black_serenity.json"
        if bs_crafting_path.exists():
            try:
                with open(bs_crafting_path, 'r', encoding='utf-8') as f:
                    bs_recipes = json.load(f)
                    
                    self.crafting_config['recipes'].update(bs_recipes.get('recipes', {}))
            except Exception as e:
                print(f"⚠️ Failed to load crafting_recipes_black_serenity.json: {e}")
                print(f"ℹ️ Black Serenity crafting will not be available")

        weapons_path = Path(__file__).parent / "weapons_config.json"
        if weapons_path.exists():
            try:
                with open(weapons_path, 'r', encoding='utf-8') as f:
                    self.weapons_config = json.load(f)
            except Exception as e:
                print(f"⚠️ Failed to load weapons_config.json: {e}")
                print(f"ℹ️ Using empty weapons config")
                self.weapons_config = {'weapons': {}, 'weapon_effects': {}, 'ammo': {}}
        else:
            self.weapons_config = {'weapons': {}, 'weapon_effects': {}, 'ammo': {}}

        transformations_path = Path(__file__).parent / "transformations_config.json"
        if transformations_path.exists():
            try:
                with open(transformations_path, 'r', encoding='utf-8') as f:
                    self.transformations_config = json.load(f)
            except Exception as e:
                print(f"⚠️ Failed to load transformations_config.json: {e}")
                print(f"ℹ️ Using empty transformations config")
                self.transformations_config = {'forms': {}, 'special_effects': {}}
        else:
            self.transformations_config = {'forms': {}, 'special_effects': {}}

        companions_path = Path(__file__).parent / "companions_config.json"
        if companions_path.exists():
            try:
                with open(companions_path, 'r', encoding='utf-8') as f:
                    self.companions_config = json.load(f)
            except Exception as e:
                print(f"⚠️ Failed to load companions_config.json: {e}")
                print(f"ℹ️ Using empty companions config")
                self.companions_config = {'companions': {}, 'special_abilities': {}}
        else:
            self.companions_config = {'companions': {}, 'special_abilities': {}}

        milestones_path = Path(__file__).parent / "secret_milestones.json"
        if milestones_path.exists():
            try:
                with open(milestones_path, 'r', encoding='utf-8') as f:
                    self.secret_milestones = json.load(f)
            except Exception as e:
                print(f"⚠️ Failed to load secret_milestones.json: {e}")
                self.secret_milestones = {'npc_milestones': {}}
        else:
            self.secret_milestones = {'npc_milestones': {}}

        achievements_path = Path(__file__).parent / "achievements_config.json"
        if achievements_path.exists():
            try:
                with open(achievements_path, 'r', encoding='utf-8') as f:
                    self.achievements_config = json.load(f)
            except Exception as e:
                print(f"⚠️ Failed to load achievements_config.json: {e}")
                self.achievements_config = {'achievements': {}, 'level_thresholds': {}, 'xp_rewards': {}}
        else:
            self.achievements_config = {'achievements': {}, 'level_thresholds': {}, 'xp_rewards': {}}

        fishing_path = Path(__file__).parent / "fishing_config.json"
        if fishing_path.exists():
            try:
                with open(fishing_path, 'r', encoding='utf-8') as f:
                    self.fishing_config = json.load(f)
            except Exception as e:
                print(f"⚠️ Failed to load fishing_config.json: {e}")
                self.fishing_config = {'fishing_spots': {}}
        else:
            self.fishing_config = {'fishing_spots': {}}

        dungeon_rules_path = Path(__file__).parent / "dungeon_rules.json"
        if dungeon_rules_path.exists():
            try:
                with open(dungeon_rules_path, 'r', encoding='utf-8') as f:
                    self.dungeon_rules = json.load(f)
            except Exception as e:
                print(f"Failed to load dungeon_rules.json: {e}")
                self.dungeon_rules = {'base_stat': 10, 'level_divisor': 2, 'type_bonuses': {}}
        else:
            self.dungeon_rules = {'base_stat': 10, 'level_divisor': 2, 'type_bonuses': {}}

    def _load_players(self) -> Dict:
        """Load player data from individual files"""
        players = {}

        self.players_dir.mkdir(parents=True, exist_ok=True)

        if self.legacy_file and self.legacy_file.exists():
            try:
                print(f"📦 Migrating from legacy players.json to individual files...")
                with open(self.legacy_file, 'r', encoding='utf-8') as f:
                    legacy_data = json.load(f)

                for player_id, player_data in legacy_data.items():
                    player_file = self.players_dir / f"{player_id}.json"
                    try:
                        with open(player_file, 'w', encoding='utf-8') as pf:
                            json.dump(player_data, pf, indent=2)
                        print(f"  ✅ Migrated player {player_id}")
                    except Exception as e:
                        print(f"  ⚠️ Failed to migrate player {player_id}: {e}")

                backup_path = self.legacy_file.with_suffix('.json.legacy_backup')
                self.legacy_file.rename(backup_path)
                print(f"✅ Migration complete! Legacy file backed up to: {backup_path.name}")

            except Exception as e:
                print(f"⚠️ Failed to migrate legacy data: {e}")

        if self.players_dir.exists():
            for player_file in self.players_dir.glob("*.json"):
                if player_file.stem.isdigit():  
                    player_id = player_file.stem
                    loaded = False

                    try:
                        with open(player_file, 'r', encoding='utf-8') as f:
                            players[player_id] = json.load(f)
                            loaded = True
                    except Exception as e:
                        print(f"⚠️ Failed to load player {player_id}: {e}")

                        backup_file = player_file.with_suffix('.json.backup')
                        if backup_file.exists():
                            try:
                                print(f"🔄 Attempting to restore {player_id} from backup...")
                                with open(backup_file, 'r', encoding='utf-8') as f:
                                    players[player_id] = json.load(f)
                                    loaded = True
                                
                                import shutil
                                shutil.copy2(backup_file, player_file)
                                print(f"✅ Successfully restored {player_id} from backup!")
                            except Exception as backup_error:
                                print(f"❌ CRITICAL: Failed to restore {player_id} from backup: {backup_error}")
                                print(f"⚠️ Player {player_id} will be treated as NEW - data may be lost!")
                        else:
                            print(f"⚠️ No backup file found for {player_id}")

                    if not loaded:
                        print(f"⚠️ WARNING: Could not load player {player_id} - they will start fresh if they interact!")

        if not players and self.legacy_file and self.legacy_file.exists():
            try:
                print(f"⚠️ No individual player files found, loading from legacy file as fallback")
                with open(self.legacy_file, 'r', encoding='utf-8') as f:
                    players = json.load(f)
                print(f"✅ Loaded {len(players)} players from legacy file")
            except Exception as e:
                print(f"⚠️ Failed to load legacy file: {e}")

        return players

    def _load_single_player(self, player_id: str) -> Optional[Dict]:
        """Load a single player from disk with file locking

        This is safer than reloading all players and prevents race conditions.
        """
        player_file = self.players_dir / f"{player_id}.json"
        lock_path = self._get_lock_path(player_id)

        if not player_file.exists():
            return None

        def do_load():
            try:
                with open(player_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                print(f"⚠️ Failed to load player {player_id}: {e}")
                
                backup_file = player_file.with_suffix('.json.backup')
                if backup_file.exists():
                    try:
                        with open(backup_file, 'r', encoding='utf-8') as f:
                            return json.load(f)
                    except Exception:
                        pass
                return None

        try:
            if HAS_FILELOCK:
                lock = FileLock(str(lock_path), timeout=15)
                with lock:
                    return do_load()
            else:
                return do_load()
        except Exception as e:
            print(f"⚠️ Error loading player {player_id}: {e}")
            return None

    def _get_lock_path(self, player_id: str) -> Path:
        """Get the lock file path for a player"""
        return self.players_dir / f".{player_id}.lock"

    def _save_player(self, player_id: str):
        """Save individual player data to their own file (atomic write with backup and locking)"""
        if player_id not in self.players:
            print(f"⚠️ Player {player_id} not in memory, skipping save")
            return

        player_data = self.players[player_id]
        player_file = self.players_dir / f"{player_id}.json"
        lock_path = self._get_lock_path(player_id)

        def do_save():
            """Inner save function that does the actual work"""
            
            self.players_dir.mkdir(parents=True, exist_ok=True)

            if player_file.exists():
                backup_file = player_file.with_suffix('.json.backup')
                try:
                    shutil.copy2(player_file, backup_file)
                except Exception as backup_error:
                    print(f"⚠️ Warning: Could not create backup for {player_id}: {backup_error}")

            temp_fd, temp_path = tempfile.mkstemp(
                dir=self.players_dir,
                prefix=f'.{player_id}_',
                suffix='.tmp'
            )

            try:
                with os.fdopen(temp_fd, 'w', encoding='utf-8') as f:
                    json.dump(player_data, f, indent=2, ensure_ascii=False)
                    f.flush()  
                    os.fsync(f.fileno())  

                shutil.move(temp_path, str(player_file))

            except Exception as e:
                
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass
                raise e

        try:
            if HAS_FILELOCK:
                
                lock = FileLock(str(lock_path), timeout=15)
                with lock:
                    do_save()
            else:
                
                do_save()

        except Exception as e:
            print(f"❌ CRITICAL: Failed to save player {player_id}: {e}")
            print(f"⚠️ If data was lost, check: {player_file.with_suffix('.json.backup')}")

    def _save_players(self):
        """Save all player data (saves each player to individual file)"""
        
        for player_id in self.players.keys():
            self._save_player(player_id)
    
    def detect_world(self, channel_name: str) -> Optional[str]:
        """
        Detect which world a channel belongs to
        
        Returns:
            World ID (e.g., 'heros_journey') or None
        """
        for world_id, world_data in self.worlds_config['worlds'].items():
            if channel_name in world_data['channels']:
                return world_id
        return None
    
    def get_world_currency(self, world_id: str) -> Dict:
        """Get currency info for a world"""
        return self.worlds_config['worlds'][world_id]['currency']
    
    def _init_world_data(self, world_id: str) -> Dict:
        """Initialize data structure for a world"""
        currency = self.get_world_currency(world_id)
        if currency is None:
            return {
                'currency': 0,
                'hp': 100,
                'max_hp': 100,
                'inventory': [],
                'perishable_inventory': [],
                'crafted_items': [],
                'equipped': {
                    'weapon': None,
                    'armor': None,
                    'accessory': None
                },
                'current_quest': None,
                'quest_history': [],
                'cooldowns': {},
                'active_buffs': [],
                'reputation': {
                    'npcs': {},
                    'factions': {}
                },
                'titles': [],
                'stats': {
                    'quests_completed': 0,
                    'total_earned': 0,
                    'total_spent': 0,
                    'items_purchased': 0,
                    'items_crafted': 0,
                    'damage_taken': 0,
                    'damage_healed': 0,
                    'times_knocked_out': 0
                }
            }
        return {
            'currency': currency['starting_amount'],
            'hp': 100,
            'max_hp': 100,
            'inventory': [],
            'perishable_inventory': [],
            'crafted_items': [],
            'equipped': {
                'weapon': None,
                'armor': None,
                'accessory': None
            },
            'current_quest': None,
            'quest_history': [],
            'cooldowns': {},
            'active_buffs': [],
            'reputation': {
                'npcs': {},
                'factions': {}
            },
            'titles': [],
            'stats': {
                'quests_completed': 0,
                'total_earned': currency['starting_amount'],
                'total_spent': 0,
                'items_purchased': 0,
                'items_crafted': 0,
                'damage_taken': 0,
                'damage_healed': 0,
                'times_knocked_out': 0
            }
        }
    
    def get_player(self, player_id: str, player_name: str = None) -> Dict:
        """
        Get player data, creating new player if doesn't exist

        Returns:
            Complete player data with all worlds
        """
        player_id = str(player_id)

        player_file = self.players_dir / f"{player_id}.json"
        loaded_player = self._load_single_player(player_id)

        if loaded_player is not None:
            self.players[player_id] = loaded_player
        elif player_file.exists():
            
            import time
            max_retries = 3
            for retry in range(max_retries):
                delay = 0.3 * (2 ** retry)  
                time.sleep(delay)
                loaded_player = self._load_single_player(player_id)
                if loaded_player is not None:
                    self.players[player_id] = loaded_player
                    break
            else:
                
                print(f"⚠️ Player file exists but couldn't load after {max_retries} retries: {player_file}")
                print(f"   Using cached data if available, or creating temporary player")

        if player_id not in self.players:
            
            player_file = self.players_dir / f"{player_id}.json"
            file_existed = player_file.exists()

            self.players[player_id] = {
                'name': player_name or f"Player_{player_id[:4]}",
                'character_type': 'all',  
                'current_form': 'human',
                'form_cooldowns': {},
                'companion': None,  
                'relationships': {},  
                'worlds': {},
                'xp': 0,
                'level': 1,
                'title': None,  
                'achievements': [],  
                'status_effects': [],  
                'quest_chains': {},  
                'dungeon_stat_mods': {},  
                'dungeon_renown': {},  
                'dungeon_titles': [],  
                'global_stats': {
                    'total_quests_completed': 0,
                    'worlds_visited': [],
                    'total_hunts': 0,
                    'total_gathers': 0,
                    'total_fish_caught': 0,
                    'gambles_won': 0,
                    'gambles_lost': 0,
                    'night_actions': 0,
                    'dawn_actions': 0
                },
                'created_at': datetime.now().isoformat(),
                'last_active': datetime.now().isoformat()
            }

            for world_id in self.worlds_config['worlds'].keys():
                self.players[player_id]['worlds'][world_id] = self._init_world_data(world_id)

            if not file_existed:
                self._save_player(player_id)
            else:
                print(f"⚠️ NOT saving new player {player_id} - file exists but couldn't be loaded")
        else:
            
            self.players[player_id]['last_active'] = datetime.now().isoformat()

            if player_name and self.players[player_id]['name'] != player_name:
                self.players[player_id]['name'] = player_name

            if 'character_type' not in self.players[player_id]:
                self.players[player_id]['character_type'] = 'all'
            if 'current_form' not in self.players[player_id]:
                self.players[player_id]['current_form'] = 'human'
            if 'form_cooldowns' not in self.players[player_id]:
                self.players[player_id]['form_cooldowns'] = {}

            if 'companion' not in self.players[player_id]:
                self.players[player_id]['companion'] = None

            if 'relationships' not in self.players[player_id]:
                self.players[player_id]['relationships'] = {}

            if 'xp' not in self.players[player_id]:
                self.players[player_id]['xp'] = 0
            if 'level' not in self.players[player_id]:
                self.players[player_id]['level'] = 1
            if 'title' not in self.players[player_id]:
                self.players[player_id]['title'] = None
            if 'achievements' not in self.players[player_id]:
                self.players[player_id]['achievements'] = []
            if 'status_effects' not in self.players[player_id]:
                self.players[player_id]['status_effects'] = []
            if 'quest_chains' not in self.players[player_id]:
                self.players[player_id]['quest_chains'] = {}
            if 'dungeon_stat_mods' not in self.players[player_id]:
                self.players[player_id]['dungeon_stat_mods'] = {}
            if 'dungeon_renown' not in self.players[player_id]:
                self.players[player_id]['dungeon_renown'] = {}
            if 'dungeon_titles' not in self.players[player_id]:
                self.players[player_id]['dungeon_titles'] = []

            if 'total_hunts' not in self.players[player_id].get('global_stats', {}):
                if 'global_stats' not in self.players[player_id]:
                    self.players[player_id]['global_stats'] = {}
                self.players[player_id]['global_stats']['total_hunts'] = 0
                self.players[player_id]['global_stats']['total_gathers'] = 0
                self.players[player_id]['global_stats']['total_fish_caught'] = 0
                self.players[player_id]['global_stats']['gambles_won'] = 0
                self.players[player_id]['global_stats']['gambles_lost'] = 0
                self.players[player_id]['global_stats']['night_actions'] = 0
                self.players[player_id]['global_stats']['dawn_actions'] = 0

            for world_id in self.worlds_config['worlds'].keys():
                if world_id not in self.players[player_id]['worlds']:
                    self.players[player_id]['worlds'][world_id] = self._init_world_data(world_id)
                
                if 'perishable_inventory' not in self.players[player_id]['worlds'][world_id]:
                    self.players[player_id]['worlds'][world_id]['perishable_inventory'] = []
                
                if 'crafted_items' not in self.players[player_id]['worlds'][world_id]:
                    self.players[player_id]['worlds'][world_id]['crafted_items'] = []
                
                if 'items_crafted' not in self.players[player_id]['worlds'][world_id]['stats']:
                    self.players[player_id]['worlds'][world_id]['stats']['items_crafted'] = 0

                if 'hp' not in self.players[player_id]['worlds'][world_id]:
                    self.players[player_id]['worlds'][world_id]['hp'] = 100
                if 'max_hp' not in self.players[player_id]['worlds'][world_id]:
                    self.players[player_id]['worlds'][world_id]['max_hp'] = 100

                if 'equipped' not in self.players[player_id]['worlds'][world_id]:
                    self.players[player_id]['worlds'][world_id]['equipped'] = {
                        'weapon': None,
                        'armor': None,
                        'accessory': None
                    }

                if 'active_buffs' not in self.players[player_id]['worlds'][world_id]:
                    self.players[player_id]['worlds'][world_id]['active_buffs'] = []

                if 'damage_taken' not in self.players[player_id]['worlds'][world_id]['stats']:
                    self.players[player_id]['worlds'][world_id]['stats']['damage_taken'] = 0
                if 'damage_healed' not in self.players[player_id]['worlds'][world_id]['stats']:
                    self.players[player_id]['worlds'][world_id]['stats']['damage_healed'] = 0
                if 'times_knocked_out' not in self.players[player_id]['worlds'][world_id]['stats']:
                    self.players[player_id]['worlds'][world_id]['stats']['times_knocked_out'] = 0

                if 'reputation' not in self.players[player_id]['worlds'][world_id]:
                    self.players[player_id]['worlds'][world_id]['reputation'] = {
                        'npcs': {},
                        'factions': {}
                    }
                if 'titles' not in self.players[player_id]['worlds'][world_id]:
                    self.players[player_id]['worlds'][world_id]['titles'] = []

            self._save_player(player_id)

        return self.players[player_id]
    
    def get_world_data(self, player_id: str, world_id: str, player_name: str = None) -> Dict:
        """Get player's data for a specific world"""
        player = self.get_player(player_id, player_name)
        return player['worlds'][world_id]
    
    def add_currency(self, player_id: str, world_id: str, amount: float, reason: str = "") -> Dict:
        """
        Add currency to player's balance in a specific world

        Returns:
            {'success': bool, 'new_balance': float, 'message': str}
        """
        player = self.get_player(player_id)
        world_data = player['worlds'][world_id]
        currency = self.get_world_currency(world_id)

        world_data['currency'] += amount
        world_data['stats']['total_earned'] += amount

        if currency.get('decimal'):
            world_data['currency'] = round(world_data['currency'], 2)

        self._save_player(player_id)
        
        symbol = currency['symbol']
        balance_str = f"{world_data['currency']:.2f}" if currency.get('decimal') else str(int(world_data['currency']))
        
        return {
            'success': True,
            'new_balance': world_data['currency'],
            'message': f"Received {symbol}{amount}! New balance: {symbol}{balance_str}"
        }
    
    def spend_currency(self, player_id: str, world_id: str, amount: float, reason: str = "") -> Dict:
        """
        Spend currency from player's balance in a specific world
        
        Returns:
            {'success': bool, 'new_balance': float, 'message': str}
        """
        player = self.get_player(player_id)
        world_data = player['worlds'][world_id]
        currency = self.get_world_currency(world_id)
        
        if world_data['currency'] < amount:
            symbol = currency['symbol']
            return {
                'success': False,
                'new_balance': world_data['currency'],
                'message': f"Not enough {currency['name']}! Need {symbol}{amount} but only have {symbol}{world_data['currency']}"
            }
        
        world_data['currency'] -= amount
        world_data['stats']['total_spent'] += amount

        if currency.get('decimal'):
            world_data['currency'] = round(world_data['currency'], 2)

        self._save_player(player_id)
        
        symbol = currency['symbol']
        balance_str = f"{world_data['currency']:.2f}" if currency.get('decimal') else str(int(world_data['currency']))
        
        return {
            'success': True,
            'new_balance': world_data['currency'],
            'message': f"Spent {symbol}{amount}. Remaining: {symbol}{balance_str}"
        }

    def claim_daily_reward(self, player_id: str, world_id: str, player_name: str = None) -> Dict:
        """
        Claim daily login reward for a specific world.
        Can only be claimed once per 24 hours per world.

        Returns:
            {'success': bool, 'reward': float, 'message': str, 'next_claim': int}
        """
        import time

        player = self.get_player(player_id, player_name)
        world_data = player['worlds'][world_id]
        currency = self.get_world_currency(world_id)

        if not currency:
            return {
                'success': False,
                'reward': 0,
                'message': "This world doesn't use currency!",
                'next_claim': 0
            }

        daily_rewards = {
            'heros_journey': 3,
            'dusty_trails': 1.00,
            'black_serenity': 5
        }
        reward = daily_rewards.get(world_id, 1)

        if 'daily_claims' not in player:
            player['daily_claims'] = {}

        last_claim = player['daily_claims'].get(world_id, 0)
        current_time = time.time()
        cooldown = 24 * 60 * 60  

        if current_time - last_claim < cooldown:
            
            next_claim = int(last_claim + cooldown)
            return {
                'success': False,
                'reward': 0,
                'message': f"You've already claimed your daily reward! Come back <t:{next_claim}:R>",
                'next_claim': next_claim
            }

        world_data['currency'] += reward
        if currency.get('decimal'):
            world_data['currency'] = round(world_data['currency'], 2)

        player['daily_claims'][world_id] = current_time
        world_data['stats']['total_earned'] = world_data['stats'].get('total_earned', 0) + reward

        self._save_player(player_id)

        symbol = currency['symbol']
        balance_str = f"{world_data['currency']:.2f}" if currency.get('decimal') else str(int(world_data['currency']))

        return {
            'success': True,
            'reward': reward,
            'message': f"**Daily Reward Claimed!** +{symbol}{reward}\nNew balance: {symbol}{balance_str}",
            'next_claim': int(current_time + cooldown)
        }

    def get_hp(self, player_id: str, world_id: str) -> Dict:
        """Get player's current HP in a specific world"""
        player = self.get_player(player_id)
        world_data = player['worlds'][world_id]
        return {
            'hp': world_data.get('hp', 100),
            'max_hp': world_data.get('max_hp', 100)
        }

    def heal(self, player_id: str, world_id: str, amount: int, source: str = "unknown") -> Dict:
        """
        Heal player HP in a specific world

        Returns:
            {'success': bool, 'hp': int, 'max_hp': int, 'healed': int, 'message': str}
        """
        player = self.get_player(player_id)
        world_data = player['worlds'][world_id]

        current_hp = world_data.get('hp', 100)
        max_hp = world_data.get('max_hp', 100)

        actual_heal = min(amount, max_hp - current_hp)
        new_hp = current_hp + actual_heal

        world_data['hp'] = new_hp
        world_data['stats']['damage_healed'] = world_data['stats'].get('damage_healed', 0) + actual_heal

        self._save_player(player_id)

        if actual_heal == 0:
            return {
                'success': True,
                'hp': new_hp,
                'max_hp': max_hp,
                'healed': 0,
                'message': f"Already at full health! ({new_hp}/{max_hp} HP)"
            }

        return {
            'success': True,
            'hp': new_hp,
            'max_hp': max_hp,
            'healed': actual_heal,
            'message': f"Healed {actual_heal} HP! ({new_hp}/{max_hp} HP)"
        }

    def take_damage(self, player_id: str, world_id: str, amount: int, source: str = "unknown") -> Dict:
        """
        Deal damage to player in a specific world

        Returns:
            {'success': bool, 'hp': int, 'max_hp': int, 'damage': int, 'knocked_out': bool, 'message': str}
        """
        player = self.get_player(player_id)
        world_data = player['worlds'][world_id]

        current_hp = world_data.get('hp', 100)
        max_hp = world_data.get('max_hp', 100)

        damage_reduction = self.get_equipment_bonuses(player_id, world_id).get('defense', 0)

        actual_damage = max(1, amount - damage_reduction)  
        new_hp = max(0, current_hp - actual_damage)

        world_data['hp'] = new_hp
        world_data['stats']['damage_taken'] = world_data['stats'].get('damage_taken', 0) + actual_damage

        knocked_out = new_hp <= 0
        currency_lost = 0
        if knocked_out:
            world_data['stats']['times_knocked_out'] = world_data['stats'].get('times_knocked_out', 0) + 1
            
            world_data['hp'] = max_hp // 4
            new_hp = world_data['hp']

            death_penalties = {
                'heros_journey': 10,
                'dusty_trails': 4.00,
                'black_serenity': 12.5
            }
            penalty = death_penalties.get(world_id, 0)
            if penalty > 0:
                current_currency = world_data.get('currency', 0)
                currency_lost = min(penalty, current_currency)  
                world_data['currency'] = current_currency - currency_lost

        self._save_player(player_id)

        if knocked_out:
            currency_info = self.get_world_currency(world_id)
            currency_symbol = currency_info.get('symbol', '')
            loss_msg = f" Lost {currency_symbol}{currency_lost} from your injuries." if currency_lost > 0 else ""
            return {
                'success': True,
                'hp': new_hp,
                'max_hp': max_hp,
                'damage': actual_damage,
                'damage_reduction': damage_reduction,
                'knocked_out': True,
                'currency_lost': currency_lost,
                'message': f"**KNOCKED OUT!** Took {actual_damage} damage from {source}. You recover at {new_hp}/{max_hp} HP.{loss_msg}"
            }

        armor_msg = f" (blocked {damage_reduction})" if damage_reduction > 0 else ""
        return {
            'success': True,
            'hp': new_hp,
            'max_hp': max_hp,
            'damage': actual_damage,
            'damage_reduction': damage_reduction,
            'knocked_out': False,
            'message': f"Took {actual_damage} damage{armor_msg} from {source}! ({new_hp}/{max_hp} HP)"
        }

    def set_max_hp(self, player_id: str, world_id: str, new_max: int) -> Dict:
        """Set player's max HP (and heal to full if current > new max)"""
        player = self.get_player(player_id)
        world_data = player['worlds'][world_id]

        world_data['max_hp'] = new_max
        if world_data.get('hp', 100) > new_max:
            world_data['hp'] = new_max

        self._save_player(player_id)
        return {
            'success': True,
            'hp': world_data['hp'],
            'max_hp': new_max
        }

    def restore_full_hp(self, player_id: str, world_id: str) -> Dict:
        """Restore player to full HP"""
        player = self.get_player(player_id)
        world_data = player['worlds'][world_id]

        max_hp = world_data.get('max_hp', 100)
        healed = max_hp - world_data.get('hp', 100)
        world_data['hp'] = max_hp

        self._save_player(player_id)
        return {
            'success': True,
            'hp': max_hp,
            'max_hp': max_hp,
            'healed': healed,
            'message': f"Fully restored! ({max_hp}/{max_hp} HP)"
        }

    def equip_item(self, player_id: str, world_id: str, item_name: str, slot: str) -> Dict:
        """
        Equip an item to a slot (weapon, armor, accessory)

        Returns:
            {'success': bool, 'equipped': str, 'unequipped': str or None, 'message': str}
        """
        player = self.get_player(player_id)
        world_data = player['worlds'][world_id]

        valid_slots = ['weapon', 'armor', 'accessory']
        if slot not in valid_slots:
            return {
                'success': False,
                'message': f"Invalid slot. Use: {', '.join(valid_slots)}"
            }

        crafted_items = world_data.get('crafted_items', [])
        item_found = None
        item_source = 'crafted'

        for item in crafted_items:
            if item.get('name', '').lower() == item_name.lower():
                item_found = item
                break

        if not item_found:
            try:
                config_path = self.base_dir / 'quest_reward_items.json'
                with open(config_path, 'r', encoding='utf-8') as f:
                    quest_rewards = json.load(f)

                unique_items = {key:item for region in quest_rewards.get('unique_items', {}).values() for key,item in region.items()}

                for item_id, item_info in unique_items.items():
                    if item_info.get('name', '').lower() == item_name.lower():
                        item_slot = item_info.get('slot')
                        if item_slot and item_slot == slot:
                            
                            inventory = world_data.get('inventory', [])
                            has_item = False
                            for inv_item in inventory:
                                if isinstance(inv_item, dict):
                                    if inv_item.get('item', '').lower() == item_name.lower():
                                        has_item = True
                                        break
                                elif isinstance(inv_item, str):
                                    if inv_item.lower() == item_name.lower():
                                        has_item = True
                                        break

                            if has_item:
                                item_found = {
                                    'name': item_info['name'],
                                    'type': item_info.get('type', 'gear'),
                                    'bonuses': item_info.get('bonuses', {}),
                                    'special_effects': item_info.get('special_effects', {}),
                                    'is_unique': True
                                }
                                item_source = 'unique'
                                break
                        elif item_slot and item_slot != slot:
                            return {
                                'success': False,
                                'message': f"**{item_info['name']}** goes in the **{item_slot}** slot, not {slot}."
                            }
            except (FileNotFoundError, json.JSONDecodeError, KeyError):
                pass  

        if not item_found:
            return {
                'success': False,
                'message': f"You don't have **{item_name}** to equip.\n💡 Check `!inventory` or craft gear with `!craft`"
            }

        equipped = world_data.get('equipped', {'weapon': None, 'armor': None, 'accessory': None})
        old_item = equipped.get(slot)

        equipped[slot] = item_found['name']
        world_data['equipped'] = equipped

        self._save_player(player_id)

        if old_item:
            return {
                'success': True,
                'equipped': item_found['name'],
                'unequipped': old_item,
                'message': f"Equipped **{item_found['name']}**! (Unequipped {old_item})"
            }

        return {
            'success': True,
            'equipped': item_found['name'],
            'unequipped': None,
            'message': f"Equipped **{item_found['name']}** in {slot} slot!"
        }

    def unequip_item(self, player_id: str, world_id: str, slot: str) -> Dict:
        """Unequip an item from a slot"""
        player = self.get_player(player_id)
        world_data = player['worlds'][world_id]

        valid_slots = ['weapon', 'armor', 'accessory']
        if slot not in valid_slots:
            return {
                'success': False,
                'message': f"Invalid slot. Use: {', '.join(valid_slots)}"
            }

        equipped = world_data.get('equipped', {'weapon': None, 'armor': None, 'accessory': None})
        old_item = equipped.get(slot)

        if not old_item:
            return {
                'success': False,
                'message': f"Nothing equipped in {slot} slot."
            }

        equipped[slot] = None
        world_data['equipped'] = equipped

        self._save_player(player_id)

        return {
            'success': True,
            'unequipped': old_item,
            'message': f"Unequipped **{old_item}** from {slot} slot."
        }

    def get_equipment_bonuses(self, player_id: str, world_id: str) -> Dict:
        """Get total bonuses from all equipped items (crafted and unique quest rewards)"""
        player = self.get_player(player_id)
        world_data = player['worlds'][world_id]

        bonuses = {
            'attack': 0,
            'defense': 0,
            'gathering': 0,
            'hunting': 0,
            'hunting_bonus': 0,
            'crafting': 0,
            'stealth': 0,
            'price_bonus': 0,
            'inventory_capacity': 0,
            'ranged_bonus': 0,
            'wolf_hunting': 0,
            'intimidation': 0,
            'tracking': 0,
            'social': 0,
            'magic_rolls': 0,
            'focus': 0,
            'sources': [],
            'special_effects': {}
        }

        equipped = world_data.get('equipped', {})
        crafted_items = world_data.get('crafted_items', [])

        unique_items = {}
        try:
            config_path = self.base_dir / 'quest_reward_items.json'
            with open(config_path, 'r', encoding='utf-8') as f:
                quest_rewards = json.load(f)
            unique_items = {key:item for region in quest_rewards.get('unique_items', {}).values() for key,item in region.items()}
        except (FileNotFoundError, json.JSONDecodeError, KeyError):
            pass

        for slot, item_name in equipped.items():
            if not item_name:
                continue

            item_found = False

            for item in crafted_items:
                if item.get('name') == item_name:
                    benefit = item.get('benefit', {})
                    benefit_type = benefit.get('type', '')
                    value = benefit.get('value', 0)

                    if benefit_type == 'attack':
                        bonuses['attack'] += value
                    elif benefit_type == 'defense':
                        bonuses['defense'] += value
                    elif benefit_type == 'gathering_bonus':
                        bonuses['gathering'] += value
                    elif benefit_type == 'hunting_bonus':
                        bonuses['hunting'] += value
                        bonuses['hunting_bonus'] += value
                    elif benefit_type == 'crafting_bonus':
                        bonuses['crafting'] += value
                    elif benefit_type == 'stealth':
                        bonuses['stealth'] += value
                    elif benefit_type == 'price_bonus':
                        bonuses['price_bonus'] += value
                    elif benefit_type == 'inventory_capacity':
                        bonuses['inventory_capacity'] += value

                    bonuses['sources'].append({
                        'slot': slot,
                        'item': item_name,
                        'type': benefit_type,
                        'value': value
                    })
                    item_found = True
                    break

            if not item_found:
                for item_id, item_info in unique_items.items():
                    if item_info.get('name') == item_name:
                        
                        item_bonuses = item_info.get('bonuses', {})
                        for bonus_type, bonus_value in item_bonuses.items():
                            if bonus_type in bonuses:
                                bonuses[bonus_type] += bonus_value
                            else:
                                bonuses[bonus_type] = bonus_value

                            bonuses['sources'].append({
                                'slot': slot,
                                'item': item_name,
                                'type': bonus_type,
                                'value': bonus_value,
                                'is_unique': True
                            })

                        special_effects = item_info.get('special_effects', {})
                        for effect_name, effect_value in special_effects.items():
                            bonuses['special_effects'][effect_name] = effect_value

                        item_found = True
                        break

        title_bonuses = self.get_player_titles(player_id, world_id)
        if title_bonuses.get('success'):
            for bonus_type, bonus_value in title_bonuses.get('total_bonuses', {}).items():
                if isinstance(bonus_value, bool):
                    bonuses['special_effects'][bonus_type] = bonus_value
                elif bonus_type in bonuses:
                    bonuses[bonus_type] += bonus_value
                else:
                    bonuses[bonus_type] = bonus_value

        inventory = world_data.get('inventory', [])
        checked_passives = set()  

        for item_entry in inventory:
            
            if isinstance(item_entry, dict):
                item_name = item_entry.get('name', '')
                
                if isinstance(item_name, dict):
                    item_name = item_name.get('name', '') if isinstance(item_name, dict) else str(item_name)
            else:
                item_name = str(item_entry) if item_entry else ''

            if not item_name:
                continue

            item_lower = item_name.lower()
            if item_lower in checked_passives:
                continue

            for item_id, item_info in unique_items.items():
                if item_info.get('name', '').lower() == item_lower:
                    
                    if item_info.get('passive') or item_info.get('type') == 'passive':
                        passive_bonuses = item_info.get('passive_bonuses', {})
                        for bonus_type, bonus_value in passive_bonuses.items():
                            if isinstance(bonus_value, bool):
                                bonuses['special_effects'][bonus_type] = bonus_value
                            elif bonus_type in bonuses:
                                bonuses[bonus_type] += bonus_value
                            else:
                                bonuses[bonus_type] = bonus_value

                            bonuses['sources'].append({
                                'slot': 'inventory (passive)',
                                'item': item_name,
                                'type': bonus_type,
                                'value': bonus_value,
                                'is_passive': True
                            })

                        special_effects = item_info.get('special_effects', {})
                        for effect_name, effect_value in special_effects.items():
                            bonuses['special_effects'][effect_name] = effect_value

                        checked_passives.add(item_lower)
                        break

                    elif item_info.get('type') == 'gear' and item_info.get('slot') is None:
                        item_bonuses = item_info.get('bonuses', {})
                        for bonus_type, bonus_value in item_bonuses.items():
                            if bonus_type in bonuses:
                                bonuses[bonus_type] += bonus_value
                            else:
                                bonuses[bonus_type] = bonus_value

                            bonuses['sources'].append({
                                'slot': 'inventory (gear)',
                                'item': item_name,
                                'type': bonus_type,
                                'value': bonus_value,
                                'is_gear': True
                            })

                        special_effects = item_info.get('special_effects', {})
                        for effect_name, effect_value in special_effects.items():
                            bonuses['special_effects'][effect_name] = effect_value

                        checked_passives.add(item_lower)
                        break

        return bonuses

    def add_item(self, player_id: str, world_id: str, item_name: str, quantity: int = 1):
        """Add item(s) to player's inventory in a specific world"""
        player = self.get_player(player_id)
        world_data = player['worlds'][world_id]

        if item_name == "Arrows (20)":
            for _ in range(quantity):
                
                for i in range(20):
                    world_data['inventory'].append("Arrow")
            world_data['stats']['items_purchased'] += quantity
        elif item_name == "Arrows":
            
            for _ in range(quantity):
                world_data['inventory'].append("Arrow")
            world_data['stats']['items_purchased'] += quantity
        else:
            
            for _ in range(quantity):
                world_data['inventory'].append(item_name)
            world_data['stats']['items_purchased'] += quantity

        self._save_player(player_id)
    
    def has_item(self, player_id: str, world_id: str, item_name: str, quantity: int = 1) -> bool:
        """Check if player has item(s) in a specific world (case-insensitive)"""
        player = self.get_player(player_id)
        world_data = player['worlds'][world_id]

        def get_item_name(item):
            return item.get('name', '') if isinstance(item, dict) else item

        item_name_lower = item_name.lower()
        item_count = sum(1 for item in world_data['inventory'] if get_item_name(item).lower() == item_name_lower)
        return item_count >= quantity

    def remove_item(self, player_id: str, world_id: str, item_name: str, quantity: int = 1) -> bool:
        """Remove item(s) from inventory in a specific world (case-insensitive)"""
        player = self.get_player(player_id)
        world_data = player['worlds'][world_id]

        def get_item_name(item):
            return item.get('name', '') if isinstance(item, dict) else item

        item_name_lower = item_name.lower()
        item_count = sum(1 for item in world_data['inventory'] if get_item_name(item).lower() == item_name_lower)
        if item_count < quantity:
            return False

        removed = 0
        new_inventory = []
        for item in world_data['inventory']:
            if get_item_name(item).lower() == item_name_lower and removed < quantity:
                removed += 1  
            else:
                new_inventory.append(item)

        world_data['inventory'] = new_inventory
        self._save_player(player_id)
        return True

    def transfer_currency(self, giver_id: str, receiver_id: str, world_id: str, amount: float) -> Dict:
        """Commons runtime helper."""
        
        if amount <= 0:
            return {'success': False, 'message': 'Amount must be greater than 0'}

        giver = self.get_player(giver_id)
        receiver = self.get_player(receiver_id)

        giver_world = giver['worlds'][world_id]
        receiver_world = receiver['worlds'][world_id]
        currency = self.get_world_currency(world_id)

        if giver_world['currency'] < amount:
            symbol = currency['symbol']
            return {
                'success': False,
                'message': f"Not enough {currency['name']}! You have {symbol}{giver_world['currency']} but need {symbol}{amount}"
            }

        giver_world['currency'] -= amount
        receiver_world['currency'] += amount

        if currency.get('decimal'):
            giver_world['currency'] = round(giver_world['currency'], 2)
            receiver_world['currency'] = round(receiver_world['currency'], 2)

        self._save_player(giver_id)
        self._save_player(receiver_id)

        symbol = currency['symbol']
        return {
            'success': True,
            'message': f"Transferred {symbol}{amount} to {receiver['name']}!",
            'giver_balance': giver_world['currency'],
            'receiver_balance': receiver_world['currency']
        }

    def transfer_item(self, giver_id: str, receiver_id: str, world_id: str, item_name: str, quantity: int = 1) -> Dict:
        """Commons runtime helper."""
        
        if quantity <= 0:
            return {'success': False, 'message': 'Quantity must be greater than 0'}

        giver = self.get_player(giver_id)
        receiver = self.get_player(receiver_id)

        giver_world = giver['worlds'][world_id]
        receiver_world = receiver['worlds'][world_id]

        item_count = giver_world['inventory'].count(item_name)
        if item_count < quantity:
            return {
                'success': False,
                'message': f"You don't have enough {item_name}! You have {item_count} but need {quantity}"
            }

        for _ in range(quantity):
            giver_world['inventory'].remove(item_name)
            receiver_world['inventory'].append(item_name)

        self._save_player(giver_id)
        self._save_player(receiver_id)

        qty_text = f"{quantity}x " if quantity > 1 else ""
        return {
            'success': True,
            'message': f"Gave {qty_text}{item_name} to {receiver['name']}!"
        }

    def transfer_crafted_item(self, giver_id: str, receiver_id: str, world_id: str, item_name: str) -> Dict:
        """
        Transfer a crafted item from one player to another in a specific world

        Args:
            giver_id: Discord ID of the player giving item
            receiver_id: Discord ID of the player receiving item
            world_id: The world where the item transfer happens
            item_name: Name of the crafted item to transfer

        Returns:
            {'success': bool, 'message': str}
        """
        
        giver = self.get_player(giver_id)
        receiver = self.get_player(receiver_id)

        giver_world = giver['worlds'][world_id]
        receiver_world = receiver['worlds'][world_id]

        item_to_transfer = None
        item_index = None
        for i, crafted in enumerate(giver_world.get('crafted_items', [])):
            if crafted['name'].lower() == item_name.lower():
                item_to_transfer = crafted
                item_index = i
                break

        if not item_to_transfer:
            return {
                'success': False,
                'message': f"You don't have a crafted item called '{item_name}'"
            }

        giver_world['crafted_items'].pop(item_index)
        receiver_world['crafted_items'].append(item_to_transfer)

        self._save_player(giver_id)
        self._save_player(receiver_id)

        durability_text = ""
        if 'durability' in item_to_transfer:
            durability_text = f" ({item_to_transfer['durability']}/{item_to_transfer['max_durability']} durability)"

        return {
            'success': True,
            'message': f"Gave {item_to_transfer['name']}{durability_text} to {receiver['name']}!"
        }

    def transfer_perishable_item(self, giver_id: str, receiver_id: str, world_id: str, item_name: str, amount: int = 1) -> Dict:
        """Commons runtime helper."""
        
        if amount <= 0:
            return {'success': False, 'message': 'Amount must be greater than 0'}

        giver = self.get_player(giver_id)
        receiver = self.get_player(receiver_id)

        giver_world = giver['worlds'][world_id]
        receiver_world = receiver['worlds'][world_id]

        items_to_transfer = []
        indices_to_remove = []

        for i, perishable in enumerate(giver_world.get('perishable_inventory', [])):
            if perishable['item'].lower() == item_name.lower():
                
                expires_at = datetime.fromisoformat(perishable['expires_at'])
                if datetime.now() < expires_at:
                    items_to_transfer.append(perishable)
                    indices_to_remove.append(i)
                    if len(items_to_transfer) >= amount:
                        break

        if not items_to_transfer:
            return {
                'success': False,
                'message': f"You don't have any fresh '{item_name}' to give"
            }

        actual_amount = len(items_to_transfer)
        if actual_amount < amount:
            return {
                'success': False,
                'message': f"You only have {actual_amount} fresh {item_name} (requested {amount})"
            }

        for i in reversed(indices_to_remove):
            giver_world['perishable_inventory'].pop(i)

        for item in items_to_transfer:
            receiver_world['perishable_inventory'].append(item)

        self._save_player(giver_id)
        self._save_player(receiver_id)

        first_item = items_to_transfer[0]
        expires_at = datetime.fromisoformat(first_item['expires_at'])
        time_left = expires_at - datetime.now()
        days_left = time_left.days
        hours_left = time_left.seconds // 3600

        expiry_text = ""
        if days_left > 0:
            expiry_text = f" (expires in {days_left}d {hours_left}h)"
        elif hours_left > 0:
            expiry_text = f" (expires in {hours_left}h)"
        else:
            expiry_text = f" (expires soon!)"

        if actual_amount == 1:
            return {
                'success': True,
                'message': f"Gave {first_item['item']}{expiry_text} to {receiver['name']}!"
            }
        else:
            return {
                'success': True,
                'message': f"Gave {actual_amount}x {first_item['item']}{expiry_text} to {receiver['name']}!"
            }

    def add_perishable_item(self, player_id: str, world_id: str, item_name: str, spoil_days: int = 7):
        """Add a perishable item to player's inventory with expiration date"""
        from datetime import timedelta

        player = self.get_player(player_id)
        world_data = player['worlds'][world_id]

        added_at = datetime.now()
        expires_at = added_at + timedelta(days=spoil_days)
        warned_at = added_at + timedelta(days=spoil_days - 1)  

        perishable_item = {
            'item': item_name,
            'added_at': added_at.isoformat(),
            'expires_at': expires_at.isoformat(),
            'warned_at': warned_at.isoformat(),
            'warned': False
        }

        world_data['perishable_inventory'].append(perishable_item)
        self._save_player(player_id)

    def check_spoilage(self, player_id: str, world_id: str) -> Dict:
        """Check for spoiled/expiring items and return warnings"""
        player = self.get_player(player_id)
        world_data = player['worlds'][world_id]

        now = datetime.now()
        spoiled_items = []
        warning_items = []

        items_to_keep = []

        for perishable in world_data['perishable_inventory']:
            expires_at = datetime.fromisoformat(perishable['expires_at'])
            warned_at = datetime.fromisoformat(perishable['warned_at'])

            if now >= expires_at:
                
                spoiled_items.append(perishable['item'])
                world_data['inventory'].append('Spoiled Meat')
            elif now >= warned_at and not perishable['warned']:
                
                warning_items.append(perishable['item'])
                perishable['warned'] = True
                items_to_keep.append(perishable)
            else:
                items_to_keep.append(perishable)

        world_data['perishable_inventory'] = items_to_keep
        self._save_player(player_id)

        return {
            'spoiled': spoiled_items,
            'warnings': warning_items
        }

    def process_perishable(self, player_id: str, world_id: str, item_name: str) -> Dict:
        """Process a perishable item into meat, leather, and bones"""
        
        processing_recipes = {
            'rabbit': ['Rabbit Meat', 'Rabbit Pelt', 'Small Bones'],
            'deer': ['Venison', 'Deer Hide', 'Medium Bones'],
            'boar': ['Boar Meat', 'Boar Hide', 'Large Bones'],
            'wolf': ['Wolf Meat', 'Wolf Pelt', 'Medium Bones'],
            'bear': ['Bear Meat', 'Bear Pelt', 'Large Bones'],
            'fox': ['Fox Meat', 'Fox Pelt', 'Small Bones']
        }

        player = self.get_player(player_id)
        world_data = player['worlds'][world_id]

        found = False
        for i, perishable in enumerate(world_data['perishable_inventory']):
            if perishable['item'].lower() == item_name.lower():
                
                expires_at = datetime.fromisoformat(perishable['expires_at'])
                if datetime.now() >= expires_at:
                    return {
                        'success': False,
                        'message': f"This {item_name} has already spoiled! It's now Spoiled Meat."
                    }

                recipe = processing_recipes.get(item_name.lower())
                if not recipe:
                    try:
                        config = json.loads((self.base_dir / 'hunting_config.json').read_text(encoding='utf-8'))
                        recipe = list((config.get('processing_recipes', {}).get(item_name.lower()) or {}).get('products') or [])
                    except (OSError, ValueError):
                        recipe = []
                if not recipe:
                    return {
                        'success': False,
                        'message': f"Nobody here knows how to process a {item_name} yet, so it stays in your bag."
                    }

                world_data['perishable_inventory'].pop(i)

                for processed_item in recipe:
                    world_data['inventory'].append(processed_item)

                self._save_player(player_id)

                return {
                    'success': True,
                    'products': recipe,
                    'message': f"Processed {item_name} into: {', '.join(recipe)}"
                }

        return {
            'success': False,
            'message': f"You don't have a {item_name} to process."
        }

    def drop_item(self, player_id: str, world_id: str, item_name: str) -> Dict:
        """Drop/discard an item from inventory"""
        player = self.get_player(player_id)
        world_data = player['worlds'][world_id]

        if item_name in world_data['inventory']:
            world_data['inventory'].remove(item_name)
            self._save_player(player_id)
            return {
                'success': True,
                'message': f"Dropped {item_name}."
            }

        for i, perishable in enumerate(world_data['perishable_inventory']):
            if perishable['item'].lower() == item_name.lower():
                world_data['perishable_inventory'].pop(i)
                self._save_player(player_id)
                return {
                    'success': True,
                    'message': f"Dropped {item_name}."
                }

        return {
            'success': False,
            'message': f"You don't have a {item_name} to drop."
        }

    def craft_item(self, player_id: str, world_id: str, recipe_id: str, quality_bonus: int = 0) -> Dict:
        """Craft an item from materials

        Args:
            player_id: Player's Discord ID
            world_id: World ID
            recipe_id: Recipe to craft
            quality_bonus: Optional bonus from mentor assistance (+2 from mentor, +1 from roleplay)
        """
        recipes = self.crafting_config.get('recipes', {})

        if recipe_id not in recipes:
            return {
                'success': False,
                'message': f"Unknown recipe: {recipe_id}. Use `!recipes` to see available recipes."
            }

        recipe = recipes[recipe_id]

        from ..recipes import available_here
        if not available_here(recipe, world_id):
            return {
                'success': False,
                'message': f"That recipe isn't available in this world. Use `!recipes` to see what you can craft here."
            }

        player = self.get_player(player_id)
        world_data = player['worlds'][world_id]

        def find_matching_item(inventory, material):
            """Find an item in inventory that matches the material (case-insensitive, fuzzy)"""
            material_lower = material.lower().replace('_', ' ')
            material_normalized = material.lower().replace(' ', '_')

            for item in inventory:
                
                item_name = item.get('name', '') if isinstance(item, dict) else item
                item_lower = item_name.lower().replace('_', ' ')
                item_normalized = item_name.lower().replace(' ', '_')

                if item_lower == material_lower or item_normalized == material_normalized:
                    return item_name
                
                if same_item(item_name, material):
                    return item_name
                
                if item_name.lower().replace('_', '').replace(' ', '') == material.lower().replace('_', '').replace(' ', ''):
                    return item_name

            return None

        def count_matching_items(inventory, material):
            """Count items in inventory that match the material"""
            count = 0
            for item in inventory:
                if find_matching_item([item], material):
                    count += 1
            return count

        missing_materials = []
        for material, amount_needed in recipe['materials'].items():
            
            count_in_inventory = count_matching_items(world_data['inventory'], material)

            if count_in_inventory < amount_needed:
                missing_materials.append(f"{material} (need {amount_needed}, have {count_in_inventory})")

        if missing_materials:
            return {
                'success': False,
                'message': f"Missing materials:\n• " + "\n• ".join(missing_materials)
            }

        for material, amount_needed in recipe['materials'].items():
            for _ in range(amount_needed):
                matching_item = find_matching_item(world_data['inventory'], material)
                if matching_item:
                    world_data['inventory'].remove(matching_item)

        crafted_item = {
            'recipe_id': recipe_id,
            'name': recipe['name'],
            'type': recipe['type'],
            'benefit': recipe.get('benefit'),
            'specialty': recipe.get('specialty', False),
            'crafted_at': datetime.now().isoformat()
        }

        if quality_bonus > 0:
            crafted_item['quality_bonus'] = quality_bonus
            
            if crafted_item.get('benefit') and isinstance(crafted_item['benefit'], dict):
                
                crafted_item['benefit'] = dict(crafted_item['benefit'])
                
                if 'value' in crafted_item['benefit']:
                    crafted_item['benefit']['value'] = crafted_item['benefit']['value'] + quality_bonus

        if 'durability' in recipe:
            crafted_item['durability'] = recipe['durability']
            crafted_item['max_durability'] = recipe['durability']

        if recipe.get('consumable', False) or recipe['type'] in ['companion_treat', 'food', 'potion']:
            
            durability = recipe.get('durability', 1)
            for _ in range(durability):
                world_data['inventory'].append(recipe['name'])
        else:
            world_data['crafted_items'].append(crafted_item)
        world_data['stats']['items_crafted'] += 1

        self._save_player(player_id)

        new_achievements = self.check_achievements(player_id)

        quality_text = ""
        if quality_bonus > 0:
            quality_text = f"\n⭐ **Quality Bonus: +{quality_bonus}** (enhanced stats!)"

        return {
            'success': True,
            'item': crafted_item,
            'new_achievements': new_achievements,
            'quality_bonus': quality_bonus,
            'message': f"✨ Crafted {recipe['name']}!\n📝 {recipe['description']}\n💪 {recipe['benefit'].get('description', '')}{quality_text}"
        }

    def repair_item(self, player_id: str, world_id: str, item_name: str) -> Dict:
        """Repair a damaged item"""
        player = self.get_player(player_id)
        world_data = player['worlds'][world_id]

        item = None
        item_index = None
        for i, crafted in enumerate(world_data.get('crafted_items', [])):
            if crafted['name'].lower() == item_name.lower():
                item = crafted
                item_index = i
                break

        if not item:
            return {
                'success': False,
                'message': f"You don't have a {item_name} to repair."
            }

        if item['durability'] >= item['max_durability']:
            return {
                'success': False,
                'message': f"{item['name']} is already at full durability ({item['durability']}/{item['max_durability']})."
            }

        recipe = self.crafting_config['recipes'].get(item['recipe_id'], {})
        repair_config = self.crafting_config.get('repair_costs', {}).get(item['type'], {})
        materials_factor = repair_config.get('materials_factor', 0.25)

        durability_lost = item['max_durability'] - item['durability']
        repair_materials = {}

        for material, original_amount in recipe.get('materials', {}).items():
            amount_needed = max(1, int(original_amount * materials_factor * (durability_lost / item['max_durability'])))
            repair_materials[material] = amount_needed

        missing = []
        for material, amount_needed in repair_materials.items():
            count = world_data['inventory'].count(material)
            if count < amount_needed:
                missing.append(f"{material} (need {amount_needed}, have {count})")

        if missing:
            return {
                'success': False,
                'message': f"Missing repair materials:\n• " + "\n• ".join(missing)
            }

        for material, amount_needed in repair_materials.items():
            for _ in range(amount_needed):
                world_data['inventory'].remove(material)

        item['durability'] = item['max_durability']
        self._save_player(player_id)

        return {
            'success': True,
            'message': f"🔧 Repaired {item['name']} to {item['max_durability']}/{item['max_durability']} durability!"
        }

    def damage_item(self, player_id: str, world_id: str, item_name: str, damage: int = 1) -> Dict:
        """Reduce an item's durability"""
        player = self.get_player(player_id)
        world_data = player['worlds'][world_id]

        for crafted in world_data.get('crafted_items', []):
            if crafted['name'].lower() == item_name.lower():
                crafted['durability'] = max(0, crafted['durability'] - damage)

                if crafted['durability'] == 0:
                    
                    world_data['crafted_items'].remove(crafted)
                    self._save_player(player_id)
                    return {
                        'broken': True,
                        'message': f"💔 Your {crafted['name']} broke!"
                    }

                self._save_player(player_id)
                return {
                    'broken': False,
                    'durability': crafted['durability'],
                    'max_durability': crafted['max_durability']
                }

        return {'broken': False}

    def add_weapon(self, player_id: str, world_id: str, weapon_id: str):
        """Add a weapon to player's inventory"""
        player = self.get_player(player_id)
        world_data = player['worlds'][world_id]

        weapon_config = self.weapons_config['weapons'].get(weapon_id)
        if not weapon_config:
            return

        world_data['inventory'].append(weapon_config['name'])
        self._save_player(player_id)

    def equip_weapon(self, player_id: str, world_id: str, weapon_name: str) -> Dict:
        """Manually equip a weapon from inventory"""
        player = self.get_player(player_id)
        world_data = player['worlds'][world_id]

        weapon_found = None
        weapon_id = None

        for item in world_data['inventory']:
            for wep_id, weapon_config in self.weapons_config['weapons'].items():
                if weapon_config['name'].lower() == weapon_name.lower():
                    weapon_found = weapon_config
                    weapon_id = wep_id
                    break
            if weapon_found:
                break

        if not weapon_found:
            return {
                'success': False,
                'message': f"You don't have '{weapon_name}' in your inventory!"
            }

        if 'equipped_weapon' not in world_data:
            world_data['equipped_weapon'] = {}

        world_data['equipped_weapon'] = weapon_id
        self._save_player(player_id)

        return {
            'success': True,
            'weapon': weapon_found,
            'message': f"⚔️ Equipped **{weapon_found['name']}**!\n💥 Damage: {weapon_found['damage_die']}+{weapon_found['damage_bonus']}\n🎯 Attack: +{weapon_found['attack_bonus']}"
        }

    def unequip_weapon(self, player_id: str, world_id: str) -> Dict:
        """Unequip current weapon (switch to fists)"""
        player = self.get_player(player_id)
        world_data = player['worlds'][world_id]

        world_data['equipped_weapon'] = 'fists'
        self._save_player(player_id)

        return {
            'success': True,
            'message': "👊 Unequipped weapon. Using bare hands."
        }

    def get_equipped_weapon(self, player_id: str, world_id: str) -> Dict:
        """Get currently equipped weapon (or fists if none equipped)"""
        player = self.get_player(player_id)
        world_data = player['worlds'][world_id]

        equipped_id = world_data.get('equipped_weapon', 'fists')

        weapon = self.weapons_config['weapons'].get(equipped_id, self.weapons_config['weapons']['fists'])
        weapon = weapon.copy()
        weapon['weapon_id'] = equipped_id

        return weapon

    def list_weapons(self, player_id: str, world_id: str) -> Dict:
        """List all weapons in player's inventory"""
        player = self.get_player(player_id)
        world_data = player['worlds'][world_id]

        weapons_found = []

        for item in world_data['inventory']:
            for weapon_id, weapon_config in self.weapons_config['weapons'].items():
                if weapon_config['name'].lower() == item.lower():
                    weapons_found.append({
                        'id': weapon_id,
                        'name': weapon_config['name'],
                        'damage_die': weapon_config['damage_die'],
                        'damage_bonus': weapon_config['damage_bonus'],
                        'attack_bonus': weapon_config['attack_bonus'],
                        'requires_ammo': weapon_config.get('requires_ammo', False)
                    })
                    break

        return {
            'weapons': weapons_found,
            'equipped': world_data.get('equipped_weapon', 'fists')
        }

    def get_equipment_info(self, player_id: str, world_id: str) -> Dict:
        """Get detailed equipment information for display"""
        weapon = self.get_equipped_weapon(player_id, world_id)
        form = self.get_current_form(player_id)
        companion_data = self.get_companion_bonuses(player_id)
        companion = self.get_companion(player_id)

        total_attack = 3  
        total_damage_bonus = 0
        damage_die = '1d6'  

        if weapon:
            total_attack += weapon['attack_bonus']
            total_damage_bonus += weapon['damage_bonus']
            damage_die = weapon['damage_die']

        if form:
            total_attack += form['bonuses']['attack']
            total_damage_bonus += form['bonuses']['damage']

        if companion_data['is_loyal']:
            for bonus_type, value in companion_data['bonuses'].items():
                if bonus_type == 'attack':
                    total_attack += value
                elif bonus_type == 'damage':
                    total_damage_bonus += value

        return {
            'weapon': weapon,
            'form': form,
            'companion': companion,
            'companion_loyal': companion_data['is_loyal'],
            'total_attack_bonus': total_attack,
            'damage_die': damage_die,
            'total_damage_bonus': total_damage_bonus
        }

    def transform(self, player_id: str, form_id: str) -> Dict:
        """Transform player into a different form"""
        player = self.get_player(player_id)
        character_type = player.get('character_type', 'all')
        current_form = player.get('current_form', 'human')

        if form_id not in self.transformations_config['forms']:
            return {'success': False, 'message': f"Unknown form: {form_id}"}

        form_config = self.transformations_config['forms'][form_id]

        if character_type not in form_config['available_to']:
            return {
                'success': False,
                'message': f"You cannot transform into {form_config['name']}. Your type: {character_type}"
            }

        if current_form == form_id:
            return {'success': False, 'message': f"You're already in {form_config['name']}!"}

        cooldowns = player.get('form_cooldowns', {})
        if form_id in cooldowns:
            last_used = datetime.fromisoformat(cooldowns[form_id])
            cooldown_seconds = form_config['cooldown']
            elapsed = (datetime.now() - last_used).total_seconds()

            if elapsed < cooldown_seconds:
                remaining = int(cooldown_seconds - elapsed)
                return {
                    'success': False,
                    'message': f"⏳ {form_config['name']} is on cooldown! Wait {remaining} seconds."
                }

        player['current_form'] = form_id
        player['form_cooldowns'][form_id] = datetime.now().isoformat()
        self._save_player(player_id)

        return {
            'success': True,
            'form': form_config,
            'message': f"🐺 Transformed into {form_config['name']}!\n📝 {form_config['description']}"
        }

    def get_current_form(self, player_id: str) -> Dict:
        """Get player's current form configuration"""
        player = self.get_player(player_id)
        form_id = player.get('current_form', 'human')
        return self.transformations_config['forms'].get(form_id, self.transformations_config['forms']['human'])

    def set_character_type(self, player_id: str, character_type: str):
        """Set player's character type (lycanthrope, etc.)"""
        player = self.get_player(player_id)
        player['character_type'] = character_type
        self._save_player(player_id)

    def start_quest(self, player_id: str, world_id: str, quest_data: Dict) -> Dict:
        """Start a quest in a specific world"""
        player = self.get_player(player_id)
        world_data = player['worlds'][world_id]

        if world_data['current_quest']:
            return {
                'success': False,
                'message': f"You're already on a quest in this world: {world_data['current_quest']['title']}"
            }

        target_item = quest_data.get('target_item', '')
        target_amount = quest_data.get('target_amount', 0)
        starting_progress = 0
        items_consumed = 0

        if target_item and quest_data.get('action_type') in ['collect', 'hunt']:
            
            inventory = world_data.get('inventory', [])
            
            def get_item_name(item):
                return item.get('name', '') if isinstance(item, dict) else item

            matching_items = [item for item in inventory if get_item_name(item).lower() == target_item.lower()]
            items_in_inventory = len(matching_items)

            if items_in_inventory > 0:
                
                items_to_use = min(items_in_inventory, target_amount)

                for _ in range(items_to_use):
                    
                    for i, item in enumerate(world_data['inventory']):
                        if get_item_name(item).lower() == target_item.lower():
                            world_data['inventory'].pop(i)
                            break

                starting_progress = items_to_use
                items_consumed = items_to_use

        quest = {
            **quest_data,
            'world_id': world_id,
            'started_at': datetime.now().isoformat(),
            'progress': starting_progress,
            'completed': starting_progress >= target_amount
        }

        world_data['current_quest'] = quest
        self._save_player(player_id)

        if items_consumed > 0:
            if quest['completed']:
                return {
                    'success': True,
                    'auto_complete': True,
                    'items_used': items_consumed,
                    'message': f"Quest started: {quest['title']}\n✨ **Wait!** You already have {items_consumed} {target_item} on you! Quest complete! Use **!questdone** to claim your reward!"
                }
            else:
                remaining = target_amount - starting_progress
                return {
                    'success': True,
                    'auto_progress': True,
                    'items_used': items_consumed,
                    'progress': starting_progress,
                    'target': target_amount,
                    'message': f"Quest started: {quest['title']}\n📦 You had {items_consumed} {target_item} in your pack! ({starting_progress}/{target_amount}) - need {remaining} more!"
                }

        return {
            'success': True,
            'message': f"Quest started: {quest['title']}"
        }
    
    def update_quest_progress(self, player_id: str, world_id: str, progress: int) -> Dict:
        """Update quest progress in a specific world"""
        player = self.get_player(player_id)
        world_data = player['worlds'][world_id]
        
        if not world_data['current_quest']:
            return {'success': False, 'message': 'No active quest in this world'}
        
        quest = world_data['current_quest']
        quest['progress'] = min(progress, quest['target_amount'])

        completed = quest['progress'] >= quest['target_amount']
        if completed:
            quest['completed'] = True

        self._save_player(player_id)

        return {
            'success': True,
            'progress': quest['progress'],
            'target': quest['target_amount'],
            'completed': completed
        }

    def check_gift_for_quest(self, player_id: str, world_id: str, item_name: str, amount: int = 1, is_perishable: bool = False) -> Dict:
        """Commons runtime helper."""
        player = self.get_player(player_id)
        world_data = player['worlds'].get(world_id, {})
        quest = world_data.get('current_quest')

        if not quest:
            return {'progress_added': False}

        if quest.get('target_item', '').lower() != item_name.lower():
            return {'progress_added': False}

        action_type = quest.get('action_type', '')
        if is_perishable and action_type != 'hunt':
            return {'progress_added': False}
        if not is_perishable and action_type != 'collect':
            return {'progress_added': False}

        if quest['progress'] >= quest['target_amount']:
            return {'progress_added': False}

        old_progress = quest['progress']
        quest['progress'] = min(quest['progress'] + amount, quest['target_amount'])

        self._save_player(player_id)

        return {
            'progress_added': True,
            'old_progress': old_progress,
            'new_progress': quest['progress'],
            'target': quest['target_amount'],
            'completed': quest['progress'] >= quest['target_amount']
        }

    def complete_quest(self, player_id: str, world_id: str) -> Dict:
        """Complete quest and give rewards in a specific world"""
        player = self.get_player(player_id)
        world_data = player['worlds'][world_id]
        currency = self.get_world_currency(world_id)

        if not world_data['current_quest']:
            return {'success': False, 'message': 'No active quest'}

        quest = world_data['current_quest']

        if quest['progress'] < quest['target_amount']:
            needed = quest['target_amount'] - quest['progress']

            if quest.get('action_type') == 'hunt':
                items_in_inventory = sum(
                    1 for p in world_data.get('perishable_inventory', [])
                    if p['item'].lower() == quest['target_item'].lower()
                )

                if items_in_inventory >= needed:
                    
                    removed = 0
                    items_to_keep = []

                    for perishable in world_data.get('perishable_inventory', []):
                        if removed < needed and perishable['item'].lower() == quest['target_item'].lower():
                            removed += 1
                        else:
                            items_to_keep.append(perishable)

                    world_data['perishable_inventory'] = items_to_keep
                    quest['progress'] = quest['target_amount']
                    self._save_player(player_id)

                    player = self.get_player(player_id)
                    world_data = player['worlds'][world_id]
                    quest = world_data['current_quest']
                else:
                    return {
                        'success': False,
                        'message': f"Quest not complete! Progress: {quest['progress']}/{quest['target_amount']}\n💡 You have {items_in_inventory} {quest['target_item']} in inventory - hunt {needed - items_in_inventory} more!"
                    }
            
            elif quest.get('action_type') == 'collect':
                inventory = world_data.get('inventory', [])
                target_item_lower = quest['target_item'].lower()

                def get_item_name(item):
                    return item.get('name', '') if isinstance(item, dict) else item

                if isinstance(inventory, list):
                    
                    items_in_inventory = sum(1 for item in inventory if same_item(get_item_name(item), target_item_lower))

                    if items_in_inventory >= needed:
                        
                        removed = 0
                        new_inventory = []
                        for item in inventory:
                            if removed < needed and same_item(get_item_name(item), target_item_lower):
                                removed += 1
                            else:
                                new_inventory.append(item)
                        world_data['inventory'] = new_inventory
                        quest['progress'] = quest['target_amount']
                        self._save_player(player_id)

                        player = self.get_player(player_id)
                        world_data = player['worlds'][world_id]
                        quest = world_data['current_quest']
                    else:
                        return {
                            'success': False,
                            'message': f"Quest not complete! Progress: {quest['progress']}/{quest['target_amount']}\n💡 You have {items_in_inventory} {quest['target_item']} in inventory - collect {needed - items_in_inventory} more!"
                        }
                else:
                    
                    items_in_inventory = inventory.get(quest['target_item'], 0)

                    if items_in_inventory == 0:
                        for item_name, count in inventory.items():
                            if item_name.lower() == target_item_lower:
                                items_in_inventory = count
                                break

                    if items_in_inventory >= needed:
                        
                        target_item = quest['target_item']
                        
                        for item_name in list(inventory.keys()):
                            if item_name.lower() == target_item_lower:
                                target_item = item_name
                                break

                        inventory[target_item] = inventory.get(target_item, 0) - needed
                        if inventory[target_item] <= 0:
                            del inventory[target_item]

                        quest['progress'] = quest['target_amount']
                        self._save_player(player_id)

                        player = self.get_player(player_id)
                        world_data = player['worlds'][world_id]
                        quest = world_data['current_quest']
                    else:
                        return {
                            'success': False,
                            'message': f"Quest not complete! Progress: {quest['progress']}/{quest['target_amount']}\n💡 You have {items_in_inventory} {quest['target_item']} in inventory - collect {needed - items_in_inventory} more!"
                        }
            else:
                return {
                    'success': False,
                    'message': f"Quest not complete! Progress: {quest['progress']}/{quest['target_amount']}"
                }

        rewards = {
            'currency': quest.get('reward_currency', 0),
            'items': quest.get('reward_items', [])
        }

        if rewards['currency'] > 0:
            self.add_currency(player_id, world_id, rewards['currency'], f"Quest: {quest['title']}")

        for item in rewards['items']:
            self.add_item(player_id, world_id, item, 1)

        player = self.get_player(player_id)
        world_data = player['worlds'][world_id]
        quest = world_data['current_quest']  

        world_data['stats']['quests_completed'] += 1
        player['global_stats']['total_quests_completed'] += 1

        if world_id not in player['global_stats']['worlds_visited']:
            player['global_stats']['worlds_visited'].append(world_id)

        self._save_player(player_id)

        quest_difficulty = quest.get('difficulty', 'medium')
        xp_rewards = self.achievements_config.get('xp_rewards', {})
        xp_key = f'quest_{quest_difficulty}'
        xp_amount = xp_rewards.get(xp_key, 50)  
        xp_result = self.add_xp(player_id, xp_amount, f"Quest: {quest['title']}")

        new_achievements = self.check_achievements(player_id)

        self.record_time_action(player_id)

        quest_giver = quest.get('given_by', '').strip()
        if quest_giver:
            npc_id = quest_giver.lower()
            
            self.modify_reputation(player_id, world_id, npc_id, 5, f"Completed quest: {quest['title']}")

        milestone_unlocked = None

        if quest_giver:
            
            player = self.get_player(player_id)

            if 'npc_quest_counts' not in player['global_stats']:
                player['global_stats']['npc_quest_counts'] = {}

            npc_key = quest_giver.title()

            if npc_key not in player['global_stats']['npc_quest_counts']:
                player['global_stats']['npc_quest_counts'][npc_key] = 0
            player['global_stats']['npc_quest_counts'][npc_key] += 1

            self._save_player(player_id)

            quest_count = player['global_stats']['npc_quest_counts'][npc_key]

            milestones = self.secret_milestones.get('npc_milestones', {})
            if npc_key in milestones:
                milestone = milestones[npc_key]
                threshold = milestone.get('quest_threshold', 999)

                if quest_count == threshold:
                    reward_item = milestone.get('reward_item')
                    reward_type = milestone.get('reward_type', 'item')

                    if reward_type == 'companion':
                        
                        player['companion'] = {
                            'name': reward_item,
                            'source': f'Secret milestone: {npc_key}',
                            'obtained_at': datetime.now().isoformat(),
                            'loyalty': 100,  
                            'is_loyal': True
                        }
                    else:
                        
                        self.add_item(player_id, world_id, reward_item, 1)

                    milestone_unlocked = {
                        'npc': npc_key,
                        'reward_item': reward_item,
                        'reward_type': reward_type,
                        'announcement': milestone.get('announcement', f'🎉 SECRET UNLOCKED! You received {reward_item}!'),
                        'npc_reaction': milestone.get('npc_reaction', ''),
                        'description': milestone.get('reward_description', ''),
                        'quest_count': quest_count
                    }

        player = self.get_player(player_id)
        world_data = player['worlds'][world_id]

        quest['completed_at'] = datetime.now().isoformat()
        world_data['quest_history'].append(quest)
        world_data['current_quest'] = None

        self._save_player(player_id)

        loyalty_result = self.modify_companion_loyalty(player_id, 10, "completed quest")

        symbol = currency['symbol']
        message = f"Quest completed! Earned {symbol}{rewards['currency']}"

        if xp_result.get('rp_multiplier', 1.0) > 1.0:
            message += f"\n✨ +{xp_result['xp_gained']} XP{xp_result.get('rp_bonus_text', '')}"
        else:
            message += f"\n✨ +{xp_amount} XP"

        if xp_result.get('leveled_up'):
            message += f"\n\n🎉 **LEVEL UP!** You are now level {xp_result['new_level']}!"
            for reward in xp_result.get('rewards', []):
                if reward.get('message'):
                    message += f"\n{reward['message']}"

        for ach in new_achievements:
            message += f"\n\n🏆 **Achievement Unlocked!** {ach['name']}"
            if ach.get('title'):
                message += f"\n🏅 New title available: *{ach['title']}*"

        if loyalty_result.get('success'):
            message += f"\n💖 Companion Loyalty: {loyalty_result['new_loyalty']}% (+10%)"
            if loyalty_result.get('crossed_threshold') == 'gained':
                message += f"\n✨ **Your companion is now LOYAL!** Bonuses activated!"

        return {
            'success': True,
            'rewards': rewards,
            'xp_gained': xp_amount,
            'leveled_up': xp_result.get('leveled_up', False),
            'new_level': xp_result.get('new_level'),
            'new_achievements': new_achievements,
            'message': message,
            'milestone_unlocked': milestone_unlocked
        }
    
    def get_quest_status(self, player_id: str, world_id: str) -> Optional[Dict]:
        """Get current quest status in a specific world"""
        player = self.get_player(player_id)
        world_data = player['worlds'][world_id]
        return world_data.get('current_quest')

    def abandon_quest(self, player_id: str, world_id: str) -> Dict:
        """Abandon current quest in a specific world"""
        player = self.get_player(player_id)
        world_data = player['worlds'][world_id]

        if not world_data['current_quest']:
            return {
                'success': False,
                'message': "You don't have an active quest to abandon."
            }

        quest_title = world_data['current_quest']['title']
        world_data['current_quest'] = None
        self._save_player(player_id)

        return {
            'success': True,
            'message': f"Quest '{quest_title}' abandoned. You can get a new quest from an NPC."
        }

    def mark_world_visited(self, player_id: str, world_id: str, player_name: str = None):
        """Mark a world as visited when player interacts with it"""
        player = self.get_player(player_id, player_name)

        if world_id not in player['global_stats']['worlds_visited']:
            player['global_stats']['worlds_visited'].append(world_id)
            self._save_player(player_id)

    def get_cooldown(self, player_id: str, world_id: str, action_type: str) -> Optional[str]:
        """Get cooldown timestamp for an action, returns ISO string or None"""
        player = self.get_player(player_id)
        world_data = player['worlds'][world_id]

        if 'cooldowns' not in world_data:
            world_data['cooldowns'] = {}

        return world_data['cooldowns'].get(action_type)

    def set_cooldown(self, player_id: str, world_id: str, action_type: str):
        """Set cooldown timestamp for an action"""
        player = self.get_player(player_id)
        world_data = player['worlds'][world_id]

        if 'cooldowns' not in world_data:
            world_data['cooldowns'] = {}

        world_data['cooldowns'][action_type] = datetime.now().isoformat()
        self._save_player(player_id)

    def format_inventory(self, player_id: str, world_id: str) -> str:
        """Format player's inventory for a specific world"""
        from datetime import timedelta

        spoilage_result = self.check_spoilage(player_id, world_id)

        player = self.get_player(player_id)
        world_data = player['worlds'][world_id]
        world_name = self.worlds_config['worlds'][world_id]['name']

        has_items = (world_data['inventory'] or
                     world_data.get('perishable_inventory', []) or
                     world_data.get('crafted_items', []))

        if not has_items:
            return f"🎒 **{world_name} Inventory is Empty**"

        lines = [f"🎒 **{world_name} Inventory:**"]

        if world_data['inventory']:
            item_counts = {}
            for item in world_data['inventory']:
                item_counts[item] = item_counts.get(item, 0) + 1

            for item, count in sorted(item_counts.items()):
                if count > 1:
                    lines.append(f"  • {item} x{count}")
                else:
                    lines.append(f"  • {item}")

        if world_data.get('perishable_inventory'):
            lines.append("")
            lines.append("🥩 **Perishable Items:**")
            for perishable in world_data['perishable_inventory']:
                expires_at = datetime.fromisoformat(perishable['expires_at'])
                time_left = expires_at - datetime.now()
                days_left = time_left.days

                if days_left <= 1:
                    expiry_text = "⚠️ Expires soon!"
                else:
                    expiry_text = f"({days_left} days left)"

                lines.append(f"  • {perishable['item']} {expiry_text}")
            lines.append("  💡 Use `!process <animal>` to preserve them")

        if world_data.get('crafted_items'):
            lines.append("")
            lines.append("⚒️ **Crafted Items:**")
            for crafted in world_data['crafted_items']:
                durability_pct = (crafted['durability'] / crafted['max_durability']) * 100
                if durability_pct <= 25:
                    durability_icon = "🔴"
                elif durability_pct <= 50:
                    durability_icon = "🟡"
                else:
                    durability_icon = "🟢"

                lines.append(f"  • {crafted['name']} {durability_icon} [{crafted['durability']}/{crafted['max_durability']}]")

                if crafted.get('specialty'):
                    lines.append(f"    ⭐ *Specialty Item*")

            lines.append("  💡 Use `!repair <item>` to fix damaged items")

        if spoilage_result['warnings']:
            lines.append("")
            lines.append("⚠️ **Warning:** These items will spoil soon:")
            for item in spoilage_result['warnings']:
                lines.append(f"  • {item}")

        if spoilage_result['spoiled']:
            lines.append("")
            lines.append("💀 **Spoiled:** These items have rotted:")
            for item in spoilage_result['spoiled']:
                lines.append(f"  • {item} → Spoiled Meat")

        return '\n'.join(lines)
    
    def format_universal_stats(self, player_id: str) -> str:
        """Format player's stats across ALL worlds"""
        player = self.get_player(player_id)
        
        lines = [
            f"📊 **{player['name']}'s Universal Stats**",
            "=" * 40,
            ""
        ]

        for world_id, world_config in self.worlds_config['worlds'].items():
            world_data = player['worlds'][world_id]
            world_name = world_config['name']
            currency = world_config['currency']

            emoji_map = {
            'heros_journey': '🏰',
            'dusty_trails': '🤠',
            'black_serenity': '🚀',
            'hearthlight': '☕'
            }
            emoji = emoji_map.get(world_id, '🌍')
            
            lines.append(f"{emoji} **{world_name.upper()}**")

            hp = world_data.get('hp', 100)
            max_hp = world_data.get('max_hp', 100)
            hp_percent = hp / max_hp
            if hp_percent > 0.6:
                hp_emoji = "💚"
            elif hp_percent > 0.3:
                hp_emoji = "💛"
            else:
                hp_emoji = "❤️"
            lines.append(f"{hp_emoji} Health: {hp}/{max_hp}")

            if currency:
                symbol = currency['symbol']
                balance = world_data['currency']
                if currency.get('decimal'):
                    lines.append(f"💰 {currency['name'].title()}: {symbol}{balance:.2f}")
                else:
                    lines.append(f"💰 {currency['name'].title()}: {balance}{symbol}")

            inv_count = len(world_data['inventory'])
            lines.append(f"🎒 Items: {inv_count}")

            equipped = world_data.get('equipped', {})
            equipped_count = sum(1 for v in equipped.values() if v)
            if equipped_count > 0:
                lines.append(f"🛡️ Equipped: {equipped_count}/3 slots")

            quests_done = world_data['stats']['quests_completed']
            lines.append(f"✅ Quests: {quests_done}")

            if world_data['current_quest']:
                quest = world_data['current_quest']
                lines.append(f"🎯 Active: {quest['title']} ({quest['progress']}/{quest['target_amount']})")

            lines.append("")

        lines.append("🌟 **GLOBAL TOTALS**")
        lines.append(f"Total Quests: {player['global_stats']['total_quests_completed']}")
        lines.append(f"Worlds Explored: {len(player['global_stats']['worlds_visited'])}")

        return '\n'.join(lines)

    def adopt_companion(self, player_id: str, companion_id: str) -> Dict:
        """Adopt a companion"""
        player = self.get_player(player_id)

        if companion_id not in self.companions_config['companions']:
            return {'success': False, 'message': f"Unknown companion: {companion_id}"}

        companion_config = self.companions_config['companions'][companion_id]

        if companion_config.get('adoptable') == False or companion_config.get('secret_reward') == True:
            return {'success': False, 'message': f"{companion_config['name']} cannot be adopted. This companion can only be obtained through special means..."}

        if player.get('companion'):
            current = player['companion']
            return {
                'success': False,
                'message': f"You already have a companion: {current['name']} {current['emoji']}\nUse `!dismiss` first to release them."
            }

        starting_loyalty = companion_config.get('starting_loyalty', 50)
        personality = companion_config.get('personality', 'independent')

        player['companion'] = {
            'id': companion_id,
            'name': companion_config['name'],
            'emoji': companion_config['emoji'],
            'description': companion_config['description'],
            'personality': personality,
            'loyalty': starting_loyalty,
            'loyalty_requirement': companion_config.get('loyalty_requirement', 50),
            'adopted_at': datetime.now().isoformat(),
            'last_interaction': datetime.now().isoformat()
        }
        self._save_player(player_id)

        warning = ""
        if personality == "aggressive":
            warning = f"\n⚠️ **WARNING:** This companion starts at {starting_loyalty}% loyalty and is WILD! Build trust carefully."
        elif personality == "timid":
            warning = f"\n⚠️ **FRAGILE:** This companion can die or flee if you take too much damage! Protect them!"

        return {
            'success': True,
            'companion': companion_config,
            'message': f"{companion_config['emoji']} You've adopted {companion_config['name']}!\n📝 {companion_config['description']}\n💖 **Loyalty:** {starting_loyalty}%{warning}"
        }

    def dismiss_companion(self, player_id: str) -> Dict:
        """Dismiss/release current companion (forwards to new multi-companion system)"""
        player = self.get_player(player_id)
        self._migrate_to_multi_companion(player, player_id)

        companions = player.get('companions', [])
        active_index = player.get('active_companion_index')

        if active_index is None or not companions or active_index >= len(companions):
            return {'success': False, 'message': "You don't have an active companion to dismiss."}

        companion = companions[active_index]
        companion_name = companion.get('custom_name') or companion['name']
        companion_emoji = companion['emoji']

        companions.pop(active_index)

        if companions:
            player['active_companion_index'] = 0  
        else:
            player['active_companion_index'] = None

        player = self.players[str(player_id)]
        self._save_player(player_id)

        return {
            'success': True,
            'message': f"You've released {companion_name} {companion_emoji} back to the wild. Farewell, friend!"
        }

    def get_companion(self, player_id: str) -> Optional[Dict]:
        """Get player's current companion with full config (returns active companion)"""
        
        return self.get_active_companion(player_id)

    def get_companion_bonuses(self, player_id: str) -> Dict:
        """Get bonuses OR penalties from current companion based on loyalty"""
        companion = self.get_companion(player_id)

        if not companion:
            return {'bonuses': {}, 'penalties': {}, 'is_loyal': True}

        loyalty = companion.get('loyalty', 50)
        loyalty_requirement = companion.get('loyalty_requirement', 50)
        is_loyal = loyalty >= loyalty_requirement

        if is_loyal:
            return {
                'bonuses': companion.get('bonuses', {}),
                'penalties': {},
                'is_loyal': True
            }
        else:
            return {
                'bonuses': {},
                'penalties': companion.get('penalties', {}),
                'is_loyal': False
            }

    def check_loyalty_decay(self, player_id: str, is_bot: bool = False) -> List[Dict]:
        """Check all companions for loyalty decay due to inactivity.
        Companions lose 1 loyalty per day of inactivity (after 2 days grace period).
        AI players are exempt from decay - they can't always control when they get to play!
        Returns list of decay notifications."""
        player = self.get_player(player_id)
        self._migrate_to_multi_companion(player, player_id)

        is_ai_player = is_bot or player.get('is_ai', False) or player.get('is_bot', False)
        if is_ai_player:
            return []  

        companions = player.get('companions', [])
        decay_notifications = []
        now = datetime.now()

        for companion_data in companions:
            last_interaction = companion_data.get('last_interaction')
            if not last_interaction:
                
                companion_data['last_interaction'] = now.isoformat()
                continue

            last_time = datetime.fromisoformat(last_interaction)
            days_inactive = (now - last_time).days

            if days_inactive > 2:
                decay_days = days_inactive - 2  
                old_loyalty = companion_data.get('loyalty', 50)

                last_decay = companion_data.get('last_decay_check')
                if last_decay:
                    last_decay_time = datetime.fromisoformat(last_decay)
                    if last_decay_time.date() == now.date():
                        continue  

                decay_amount = min(decay_days, 5)
                new_loyalty = max(0, old_loyalty - decay_amount)

                if new_loyalty < old_loyalty:
                    companion_data['loyalty'] = new_loyalty
                    companion_data['last_decay_check'] = now.isoformat()

                    companion_name = companion_data.get('custom_name') or companion_data.get('name', 'companion')

                    decay_notifications.append({
                        'companion_name': companion_name,
                        'old_loyalty': old_loyalty,
                        'new_loyalty': new_loyalty,
                        'days_inactive': days_inactive,
                        'decay': decay_amount
                    })

        if decay_notifications:
            self._save_player(player_id)

        return decay_notifications

    def modify_companion_loyalty(self, player_id: str, amount: int, reason: str = "") -> Dict:
        """Modify active companion loyalty (positive or negative)"""
        player = self.get_player(player_id)
        self._migrate_to_multi_companion(player, player_id)

        companions = player.get('companions', [])
        active_index = player.get('active_companion_index')

        if active_index is None or active_index >= len(companions):
            return {'success': False, 'message': "No active companion to modify loyalty for"}

        companion_data = companions[active_index]
        old_loyalty = companion_data.get('loyalty', 50)
        new_loyalty = max(0, min(100, old_loyalty + amount))  

        companion_data['loyalty'] = new_loyalty
        companion_data['last_interaction'] = datetime.now().isoformat()
        self._save_player(player_id)

        loyalty_requirement = companion_data.get('loyalty_requirement', 50)
        crossed_threshold = False

        if old_loyalty < loyalty_requirement <= new_loyalty:
            crossed_threshold = "gained"
        elif old_loyalty >= loyalty_requirement > new_loyalty:
            crossed_threshold = "lost"

        return {
            'success': True,
            'old_loyalty': old_loyalty,
            'new_loyalty': new_loyalty,
            'change': amount,
            'reason': reason,
            'crossed_threshold': crossed_threshold,
            'loyalty_requirement': loyalty_requirement
        }

    def check_companion_flee_or_attack(self, player_id: str) -> Dict:
        """Check if companion flees, attacks, or acts based on personality and loyalty"""
        companion = self.get_companion(player_id)

        if not companion:
            return {'action': None}

        loyalty = companion.get('loyalty', 50)
        personality = companion.get('personality', 'independent')
        loyalty_requirement = companion.get('loyalty_requirement', 50)

        if personality == 'timid' and loyalty < loyalty_requirement:
            flee_chance = (loyalty_requirement - loyalty) / 100  
            if random.random() < flee_chance:
                return {
                    'action': 'flee',
                    'message': f"💔 Your {companion['name']} is too scared and runs away!"
                }

        if personality == 'aggressive' and loyalty < 30:
            attack_chance = (30 - loyalty) / 100
            if random.random() < attack_chance:
                damage = random.randint(1, 4)
                return {
                    'action': 'attack',
                    'damage': damage,
                    'message': f"⚠️ Your {companion['name']} lashes out at you! You take {damage} damage!"
                }

        if personality == 'independent' and loyalty < loyalty_requirement:
            ignore_chance = (loyalty_requirement - loyalty) / 100
            if random.random() < ignore_chance:
                return {
                    'action': 'ignore',
                    'message': f"💤 Your {companion['name']} ignores you completely."
                }

        return {'action': None}

    def get_companion_reaction(self, player_id: str, action: str, context: Dict = None) -> Optional[str]:
        """Commons runtime helper."""
        companion = self.get_companion(player_id)

        if not companion:
            return None

        context = context or {}
        personality = companion.get('personality', 'independent')
        name = companion.get('custom_name') or companion['name']
        emoji = companion['emoji']
        world_id = context.get('world_id', '')

        reactions = {
            'aggressive': {
                'hunt': [
                    f"{emoji} {name} growls excitedly, ready for the hunt!",
                    f"{emoji} {name} bares its teeth - time to hunt!",
                    f"{emoji} {name} howls with bloodlust!"
                ],
                'steal': [
                    f"{emoji} {name} looks at you with disappointment...",
                    f"{emoji} {name} turns away, clearly disapproving.",
                    f"{emoji} {name} growls softly - this isn't honorable."
                ],
                'craft_weapon': [
                    f"{emoji} {name} approves! A fine weapon!",
                    f"{emoji} {name} inspects your work - well crafted!"
                ],
                'craft_food': [
                    f"{emoji} {name} sniffs the air hungrily."
                ],
                'shop': [
                    f"{emoji} {name} snorts impatiently.",
                    f"{emoji} {name} would rather be hunting."
                ],
                'give': [
                    f"{emoji} {name} watches approvingly - sharing is strength.",
                    f"{emoji} {name} nods - you help your pack."
                ],
                'collect': [
                    f"{emoji} {name} paces restlessly - boring!",
                    f"{emoji} {name} yawns. Where's the action?"
                ],
                'quest_complete': [
                    f"{emoji} {name} roars triumphantly!",
                    f"{emoji} {name} stands tall - victory!"
                ]
            },
            'sneaky': {  
                'hunt': [
                    f"{emoji} {name} slinks alongside you, eyes gleaming.",
                    f"{emoji} {name}'s ears perk up - prey detected!",
                    f"{emoji} {name} slithers silently, the perfect hunter."
                ],
                'steal': [
                    f"{emoji} {name} grins mischievously - nice one!",
                    f"{emoji} {name} chitpers excitedly! What a heist!",
                    f"{emoji} {name} winks at you. Sneaky sneaky~",
                    f"{emoji} {name} does a little victory dance!",
                    f"{emoji} {name} hisses with delight - sssso sneaky!",
                    f"{emoji} {name} coils happily. Thievery is an art!"
                ],
                'craft_weapon': [
                    f"{emoji} {name} tilts head curiously at your weapon.",
                    f"{emoji} {name} inspects it with slitted eyes - poisonous?"
                ],
                'craft_food': [
                    f"{emoji} {name} tries to steal a bite!",
                    f"{emoji} {name} samples your cooking... without asking."
                ],
                'shop': [
                    f"{emoji} {name} eyes the merchant's pockets...",
                    f"{emoji} {name} is definitely planning something.",
                    f"{emoji} {name} slithers around suspiciously."
                ],
                'give': [
                    f"{emoji} {name} looks confused - why give it away?",
                    f"{emoji} {name} would've just stolen it.",
                    f"{emoji} {name} hisses skeptically. Generosity is... odd."
                ],
                'collect': [
                    f"{emoji} {name} sniffs around for shinies!",
                    f"{emoji} {name} finds something shiny to distract you.",
                    f"{emoji} {name} pockets a few 'extras' while helping."
                ],
                'quest_complete': [
                    f"{emoji} {name} yips proudly!",
                    f"{emoji} {name} prances around showing off.",
                    f"{emoji} {name} does a triumphant slither!"
                ]
            },
            'independent': {  
                'hunt': [
                    f"{emoji} {name} watches you hunt. You're doing it wrong, but they love you anyway.",
                    f"{emoji} {name} brings you a 'gift' to show you how it's done.",
                    f"{emoji} {name} supervises your hunting attempt with patient affection."
                ],
                'steal': [
                    f"{emoji} {name} slow blinks at you. They won't judge... much.",
                    f"{emoji} {name} rubs against your leg - you're THEIR thief.",
                    f"{emoji} {name} purrs softly. You tried, dear human."
                ],
                'craft_weapon': [
                    f"{emoji} {name} sits on your workspace. This is helpful, right?",
                    f"{emoji} {name} inspects your work with a gentle headbutt."
                ],
                'craft_food': [
                    f"{emoji} {name} meows insistently - they helped, they deserve some!",
                    f"{emoji} {name} rubs against you hopefully. Sharing is caring!",
                    f"{emoji} {name} supervises your cooking with loving patience."
                ],
                'shop': [
                    f"{emoji} {name} weaves between your legs - you're theirs and everyone should know!",
                    f"{emoji} {name} slow blinks at you lovingly while you browse."
                ],
                'give': [
                    f"{emoji} {name} purrs proudly - you're learning to share like a good cat!",
                    f"{emoji} {name} rubs against you. Such a generous large cat you are!"
                ],
                'collect': [
                    f"{emoji} {name} 'helps' by sitting in the gathering basket.",
                    f"{emoji} {name} follows you around, claiming everything you touch.",
                    f"{emoji} {name} chirps at you encouragingly - you're doing great!"
                ],
                'quest_complete': [
                    f"{emoji} {name} slow blinks at you with pure love - well done, large cat!",
                    f"{emoji} {name} purrs loudly and headbutts you in celebration!",
                    f"{emoji} {name} rubs against you - they always knew you could do it!"
                ]
            },
            'timid': {  
                'hunt': [
                    f"{emoji} {name} hides behind you, trembling!",
                    f"{emoji} {name} closes its eyes - too scary!",
                    f"{emoji} {name} whimpers softly..."
                ],
                'steal': [
                    f"{emoji} {name} looks nervous about this...",
                    f"{emoji} {name} tugs your sleeve - can we leave?",
                    f"{emoji} {name} is shaking with fear!"
                ],
                'craft_weapon': [
                    f"{emoji} {name} eyes the weapon nervously.",
                    f"{emoji} {name} hopes you won't use that..."
                ],
                'craft_food': [
                    f"{emoji} {name} sniffs hopefully!",
                    f"{emoji} {name} looks excited for a treat!"
                ],
                'shop': [
                    f"{emoji} {name} stays close to you for safety.",
                    f"{emoji} {name} relaxes a little in the safe shop."
                ],
                'give': [
                    f"{emoji} {name} approves of your kindness!",
                    f"{emoji} {name} nuzzles you - you're so nice!"
                ],
                'collect': [
                    f"{emoji} {name} helps you gather peacefully!",
                    f"{emoji} {name} seems calm and happy."
                ],
                'quest_complete': [
                    f"{emoji} {name} bounces happily!",
                    f"{emoji} {name} is so proud of you!"
                ]
            },
            'loyal': {  
                'hunt': [
                    f"{emoji} {name} stays by your side, alert!",
                    f"{emoji} {name} is ready to assist!"
                ],
                'steal': [
                    f"{emoji} {name} whines softly - are you sure?",
                    f"{emoji} {name} looks conflicted but trusts you."
                ],
                'craft_weapon': [
                    f"{emoji} {name} watches attentively.",
                    f"{emoji} {name} guards you while you work."
                ],
                'craft_food': [
                    f"{emoji} {name} drools a little...",
                    f"{emoji} {name} wags hopefully!"
                ],
                'shop': [
                    f"{emoji} {name} waits patiently at your side.",
                    f"{emoji} {name} is the goodest companion!"
                ],
                'give': [
                    f"{emoji} {name} is so proud of your generosity!",
                    f"{emoji} {name} wags enthusiastically - good human!"
                ],
                'collect': [
                    f"{emoji} {name} helps you gather!",
                    f"{emoji} {name} finds something for you!"
                ],
                'quest_complete': [
                    f"{emoji} {name} celebrates with you!",
                    f"{emoji} {name} barks joyfully! Well done!"
                ]
            }
        }

        bad_worlds = ['black_serenity', 'dusty_trails']  
        bad_actions = ['steal', 'attack', 'rob', 'thief']

        if world_id in bad_worlds and any(bad_word in action.lower() for bad_word in bad_actions):
            
            if personality in ['aggressive', 'loyal']:
                return f"{emoji} {name} looks at you with sadness... you're better than this."
            elif personality == 'timid':
                return f"{emoji} {name} is terrified - what if we get caught?!"
            elif personality == 'sneaky':
                return f"{emoji} {name} grins wickedly - stealing from the law? Delicious!"

        companion_id = companion.get('id', '')
        if companion_id in ['fox', 'snake']:
            personality = 'sneaky'
        elif companion_id in ['wolf', 'bear_cub']:
            personality = 'aggressive'
        elif companion_id == 'cat':
            personality = 'independent'
        elif companion_id in ['rabbit', 'deer']:
            personality = 'timid'
        elif companion_id == 'dog':
            personality = 'loyal'

        personality_reactions = reactions.get(personality, reactions['independent'])

        if action == 'craft':
            item_name = context.get('item', '').lower()
            if any(weapon in item_name for weapon in ['sword', 'bow', 'axe', 'spear', 'dagger', 'weapon']):
                action = 'craft_weapon'
            elif any(food in item_name for food in ['food', 'meat', 'bread', 'pie', 'stew', 'meal']):
                action = 'craft_food'

        action_reactions = personality_reactions.get(action, [])

        if action_reactions:
            return random.choice(action_reactions)

        default_reactions = {
            'aggressive': f"{emoji} {name} watches you intently.",
            'sneaky': f"{emoji} {name} is up to something...",
            'independent': f"{emoji} {name} couldn't care less.",
            'timid': f"{emoji} {name} stays close to you.",
            'loyal': f"{emoji} {name} is by your side!"
        }

        return default_reactions.get(personality, None)

    def _migrate_to_multi_companion(self, player: Dict, player_id: str = None) -> None:
        """Migrate old single companion to new multi-companion array format"""
        if 'companion' in player and player['companion'] is not None:
            
            old_companion = player['companion']
            player['companions'] = [old_companion]
            player['active_companion_index'] = 0
            player['companion_slots'] = self.companions_config['companion_rules']['starting_slots']
            del player['companion']
            if player_id:
                self._save_player(player_id)
        elif 'companions' not in player:
            
            player['companions'] = []
            player['active_companion_index'] = None
            player['companion_slots'] = self.companions_config['companion_rules']['starting_slots']
            if player_id:
                self._save_player(player_id)

    def get_companion_slots_info(self, player_id: str) -> Dict:
        """Get companion slot information"""
        player = self.get_player(player_id)
        self._migrate_to_multi_companion(player, player_id)

        total_quests = player.get('global_stats', {}).get('total_quests_completed', 0)
        current_slots = player.get('companion_slots', 2)
        max_slots = self.companions_config['companion_rules']['max_slots']
        companions_count = len(player.get('companions', []))

        slots_info = {
            'current_slots': current_slots,
            'max_slots': max_slots,
            'companions_count': companions_count,
            'available_slots': current_slots - companions_count,
            'can_unlock': []
        }

        if current_slots < 3 and total_quests >= 10:
            slots_info['can_unlock'].append({'slot': 3, 'requirement': '10 quests', 'unlocked': True})
        elif current_slots < 3:
            slots_info['can_unlock'].append({'slot': 3, 'requirement': f'10 quests ({total_quests}/10)', 'unlocked': False})

        if current_slots < 4 and total_quests >= 25:
            slots_info['can_unlock'].append({'slot': 4, 'requirement': '25 quests', 'unlocked': True})
        elif current_slots < 4:
            slots_info['can_unlock'].append({'slot': 4, 'requirement': f'25 quests ({total_quests}/25)', 'unlocked': False})

        if current_slots < 5 and total_quests >= 50:
            slots_info['can_unlock'].append({'slot': 5, 'requirement': '50 quests', 'unlocked': True})
        elif current_slots < 5:
            slots_info['can_unlock'].append({'slot': 5, 'requirement': f'50 quests ({total_quests}/50)', 'unlocked': False})

        if total_quests >= 10 and current_slots < 3:
            player['companion_slots'] = 3
            self._save_player(player_id)
        if total_quests >= 25 and current_slots < 4:
            player['companion_slots'] = 4
            self._save_player(player_id)
        if total_quests >= 50 and current_slots < 5:
            player['companion_slots'] = 5
            self._save_player(player_id)

        return slots_info

    def get_all_companions(self, player_id: str) -> List[Dict]:
        """Get all of player's companions"""
        player = self.get_player(player_id)
        self._migrate_to_multi_companion(player, player_id)

        companions = player.get('companions', [])
        active_index = player.get('active_companion_index')

        result = []
        for i, companion_data in enumerate(companions):
            companion_id = companion_data['id']
            companion_config = self.companions_config['companions'].get(companion_id, {})

            result.append({
                **companion_data,
                **companion_config,
                'is_active': i == active_index
            })

        return result

    def get_active_companion(self, player_id: str) -> Optional[Dict]:
        """Get player's active companion"""
        player = self.get_player(player_id)
        self._migrate_to_multi_companion(player, player_id)

        companions = player.get('companions', [])
        active_index = player.get('active_companion_index')

        if active_index is None or active_index >= len(companions):
            return None

        companion_data = companions[active_index]
        companion_id = companion_data['id']
        companion_config = self.companions_config['companions'].get(companion_id, {})

        return {
            **companion_data,
            **companion_config
        }

    def tame_companion(self, player_id: str, companion_id: str, player_name: str, location: str, approach_bonus: int = 0) -> Dict:
        """Attempt to tame a wild companion. approach_bonus is from roleplay descriptions."""
        player = self.get_player(player_id, player_name)
        self._migrate_to_multi_companion(player, player_id)

        if companion_id not in self.companions_config['companions']:
            return {'success': False, 'message': f"Unknown companion: {companion_id}"}

        taming_info = self.companions_config['taming_info'].get(companion_id, {})
        if not taming_info.get('can_tame', False):
            return {'success': False, 'message': f"{companion_id.title()} cannot be tamed in the wild!"}

        valid_locations = taming_info.get('locations', [])
        if location not in valid_locations:
            location_list = ', '.join([f"#{loc}" for loc in valid_locations])
            return {'success': False, 'message': f"You cannot find wild {companion_id}s here. Try: {location_list}"}

        slots_info = self.get_companion_slots_info(player_id)
        if slots_info['available_slots'] <= 0:
            return {'success': False, 'message': f"You don't have any companion slots available! ({slots_info['companions_count']}/{slots_info['current_slots']})"}

        companions = player.get('companions', [])
        for comp in companions:
            if comp['id'] == companion_id and not comp.get('custom_name'):
                return {'success': False, 'message': f"You already have a {companion_id}! Try taming a different species or give this one a name with !rename."}

        form = self.get_current_form(player_id)
        form_bonus = form.get('bonuses', {}).get('taming', 0)
        taming_bonus = form_bonus
        bonus_sources = {'form': form_bonus, 'approach': approach_bonus, 'status_effects': 0}

        active_buffs = player.get('active_buffs', [])
        for buff in active_buffs:
            if buff.get('type') == 'taming':
                taming_bonus += buff.get('value', 0)
                bonus_sources['status_effects'] += buff.get('value', 0)

        companion_bonus = self.get_companion_bonuses(player_id).get('bonuses', {}).get('taming', 0)
        companion = self.get_companion(player_id)
        if companion and companion_bonus:
            taming_bonus += companion_bonus
            bonus_sources['companion'] = {
                'name': companion.get('custom_name') or companion.get('name', 'Companion'),
                'taming': companion_bonus,
            }

        taming_bonus += approach_bonus

        roll = random.randint(1, 20)
        modified_roll = roll + taming_bonus
        difficulty = taming_info.get('difficulty', 10)

        result = {
            'roll': roll,
            'modified_roll': modified_roll,
            'total': modified_roll,
            'taming_bonus': taming_bonus,
            'bonus_sources': bonus_sources,
            'difficulty': difficulty,
            'success': roll == 20 or (roll != 1 and modified_roll >= difficulty),
            'companion_id': companion_id
        }

        if result['success']:
            
            companion_config = self.companions_config['companions'][companion_id]
            starting_loyalty = companion_config.get('starting_loyalty', 50)
            personality = companion_config.get('personality', 'independent')

            new_companion = {
                'id': companion_id,
                'name': companion_config['name'],
                'custom_name': None,
                'emoji': companion_config['emoji'],
                'description': companion_config['description'],
                'personality': personality,
                'loyalty': starting_loyalty,
                'loyalty_requirement': companion_config.get('loyalty_requirement', 50),
                'adopted_at': datetime.now().isoformat(),
                'tamed': True,
                'tamed_location': location,
                'last_interaction': datetime.now().isoformat()
            }

            player = self.players[str(player_id)]

            if 'companions' not in player:
                player['companions'] = []
                player['active_companion_index'] = None
                player['companion_slots'] = self.companions_config['companion_rules']['starting_slots']

            player['companions'].append(new_companion)

            if player.get('active_companion_index') is None:
                player['active_companion_index'] = len(player['companions']) - 1

            self._save_player(player_id)

            result['companion'] = companion_config

            bonus_text = ""
            if taming_bonus > 0:
                bonus_text = f" 🐱+{taming_bonus}"

            result['message'] = f"✅ **TAMED!**{bonus_text} You've successfully tamed a wild {companion_config['name']}!\n💖 Starting Loyalty: {starting_loyalty}%"

            if personality == 'aggressive':
                result['message'] += f"\n⚠️ **WARNING:** This companion is WILD and dangerous at low loyalty!"
            elif personality == 'timid':
                result['message'] += f"\n⚠️ **FRAGILE:** Protect this companion - it can die or flee!"

        else:
            
            failure_effects = taming_info.get('failure_effects', ['flees'])
            effect = random.choice(failure_effects)

            if effect == 'attacks':
                damage = random.randint(2, 6)
                result['damage'] = damage
                result['message'] = f"❌ **TAMING FAILED!** The wild {companion_id} attacks you for {damage} damage and flees!"
            elif effect == 'poisons':
                result['message'] = f"❌ **TAMING FAILED!** The {companion_id} bites you with venom! You've been poisoned (-2 HP per turn)!"
            elif effect == 'mother_appears':
                damage = random.randint(4, 10)
                result['damage'] = damage
                result['message'] = f"❌ **TAMING FAILED!** The mother {companion_id} appears and attacks you for {damage} damage! RUN!"
            elif effect == 'tricks_you':
                result['message'] = f"❌ **TAMING FAILED!** The clever {companion_id} outsmarts you and escapes with a mocking yip!"
            elif effect == 'steals_item':
                result['message'] = f"❌ **TAMING FAILED!** The {companion_id} steals something shiny and scampers away!"
            elif effect == 'dive_attacks':
                damage = random.randint(1, 4)
                result['damage'] = damage
                result['message'] = f"❌ **TAMING FAILED!** The {companion_id} dive-attacks you for {damage} damage and soars away!"
            elif effect == 'ignores':
                result['message'] = f"❌ **TAMING FAILED!** The {companion_id} completely ignores you and wanders off."
            else:  
                result['message'] = f"❌ **TAMING FAILED!** The {companion_id} flees into the wild!"

        return result

    def switch_active_companion(self, player_id: str, companion_identifier: str) -> Dict:
        """Switch which companion is active (by name or index)"""
        player = self.get_player(player_id)
        self._migrate_to_multi_companion(player, player_id)

        companions = player.get('companions', [])

        if not companions:
            return {'success': False, 'message': "You don't have any companions!"}

        target_index = None
        for i, comp in enumerate(companions):
            comp_name = (comp.get('custom_name') or comp['name']).lower()
            if comp_name == companion_identifier.lower():
                target_index = i
                break

        if target_index is None:
            try:
                idx = int(companion_identifier) - 1  
                if 0 <= idx < len(companions):
                    target_index = idx
            except ValueError:
                pass

        if target_index is None:
            return {'success': False, 'message': f"Companion '{companion_identifier}' not found!"}

        player = self.players[str(player_id)]
        companions = player.get('companions', [])

        old_index = player.get('active_companion_index')
        player['active_companion_index'] = target_index
        self._save_player(player_id)

        new_active = companions[target_index]
        display_name = new_active.get('custom_name') or new_active['name']

        return {
            'success': True,
            'message': f"{new_active['emoji']} **{display_name}** is now your active companion!",
            'companion': new_active
        }

    def rename_companion(self, player_id: str, companion_identifier: str, new_name: str) -> Dict:
        """Rename a companion"""
        player = self.get_player(player_id)
        self._migrate_to_multi_companion(player, player_id)

        companions = player.get('companions', [])

        if not companions:
            return {'success': False, 'message': "You don't have any companions!"}

        target_index = None
        for i, comp in enumerate(companions):
            comp_name = (comp.get('custom_name') or comp['name']).lower()
            comp_id = comp.get('id', '').lower()
            identifier_lower = companion_identifier.lower()

            if comp_name == identifier_lower or comp_id == identifier_lower:
                target_index = i
                break

        if target_index is None:
            return {'success': False, 'message': f"Companion '{companion_identifier}' not found!"}

        player = self.players[str(player_id)]
        companions = player.get('companions', [])

        companion = companions[target_index]
        old_name = companion.get('custom_name') or companion['name']
        companion['custom_name'] = new_name
        self._save_player(player_id)

        return {
            'success': True,
            'message': f"{companion['emoji']} Renamed **{old_name}** to **{new_name}**!"
        }

    def release_companion(self, player_id: str, companion_identifier: str) -> Dict:
        """Release a specific companion"""
        player = self.get_player(player_id)
        self._migrate_to_multi_companion(player, player_id)

        companions = player.get('companions', [])

        if not companions:
            return {'success': False, 'message': "You don't have any companions!"}

        target_index = None
        for i, comp in enumerate(companions):
            comp_name = (comp.get('custom_name') or comp['name']).lower()
            if comp_name == companion_identifier.lower():
                target_index = i
                break

        if target_index is None:
            return {'success': False, 'message': f"Companion '{companion_identifier}' not found!"}

        player = self.players[str(player_id)]
        companions = player.get('companions', [])

        companion = companions.pop(target_index)
        display_name = companion.get('custom_name') or companion['name']

        active_index = player.get('active_companion_index')
        if active_index is not None:
            if active_index == target_index:
                
                player['active_companion_index'] = 0 if companions else None
            elif active_index > target_index:
                
                player['active_companion_index'] = active_index - 1

        self._save_player(player_id)

        return {
            'success': True,
            'message': f"💔 You've released **{display_name}** {companion['emoji']} back to the wild. Farewell, friend!"
        }

    FLIRT_COOLDOWN = 30

    def record_flirt(self, player_id: str, npc_name: str, player_name: str = None) -> Dict:
        """
        Record a flirt interaction with an NPC.
        Returns relationship data including flirt count.
        Includes cooldown check to prevent spam.
        """
        player = self.get_player(player_id, player_name)
        relationships = player.get('relationships', {})

        npc_key = npc_name.lower()

        if npc_key not in relationships:
            relationships[npc_key] = {
                'npc_name': npc_name,
                'flirt_count': 0,
                'first_flirt': None,
                'last_flirt': None,
                'relationship_level': 'stranger'
            }

        rel = relationships[npc_key]
        now = datetime.now()

        if rel['last_flirt']:
            last_flirt_time = datetime.fromisoformat(rel['last_flirt'])
            elapsed = (now - last_flirt_time).total_seconds()

            if elapsed < self.FLIRT_COOLDOWN:
                remaining = int(self.FLIRT_COOLDOWN - elapsed)
                return {
                    'flirt_count': rel['flirt_count'],
                    'relationship_level': rel['relationship_level'],
                    'is_first_flirt': False,
                    'npc_name': npc_name,
                    'on_cooldown': True,
                    'cooldown_remaining': remaining
                }

        rel['flirt_count'] += 1

        if rel['first_flirt'] is None:
            rel['first_flirt'] = now.isoformat()
        rel['last_flirt'] = now.isoformat()

        count = rel['flirt_count']
        if count >= 20:
            rel['relationship_level'] = 'smitten'
        elif count >= 10:
            rel['relationship_level'] = 'romantic'
        elif count >= 5:
            rel['relationship_level'] = 'flirty'
        elif count >= 2:
            rel['relationship_level'] = 'interested'
        else:
            rel['relationship_level'] = 'stranger'

        player['relationships'] = relationships
        self._save_player(player_id)

        return {
            'flirt_count': rel['flirt_count'],
            'relationship_level': rel['relationship_level'],
            'is_first_flirt': rel['flirt_count'] == 1,
            'npc_name': npc_name,
            'on_cooldown': False
        }

    def get_relationship(self, player_id: str, npc_name: str) -> Dict:
        """Get relationship data with a specific NPC"""
        player = self.get_player(player_id)
        relationships = player.get('relationships', {})
        npc_key = npc_name.lower()

        if npc_key in relationships:
            return relationships[npc_key]

        return {
            'npc_name': npc_name,
            'flirt_count': 0,
            'first_flirt': None,
            'last_flirt': None,
            'relationship_level': 'stranger'
        }

    def get_all_relationships(self, player_id: str) -> Dict:
        """Get all NPC relationships for a player"""
        player = self.get_player(player_id)
        return player.get('relationships', {})

    def get_flirters_with_npc(self, npc_name: str) -> List[Dict]:
        """
        Get all players who have flirted with a specific NPC.
        Returns list of {player_name, flirt_count, relationship_level}
        """
        flirters = []
        npc_key = npc_name.lower()

        for player_id, player in self.players.items():
            relationships = player.get('relationships', {})
            if npc_key in relationships:
                rel = relationships[npc_key]
                if rel['flirt_count'] > 0:
                    flirters.append({
                        'player_id': player_id,
                        'player_name': player.get('name', 'Unknown'),
                        'flirt_count': rel['flirt_count'],
                        'relationship_level': rel['relationship_level'],
                        'last_flirt': rel.get('last_flirt')
                    })

        flirters.sort(key=lambda x: x['flirt_count'], reverse=True)
        return flirters

    def get_rivals(self, player_id: str, npc_name: str) -> List[Dict]:
        """
        Get other players who have also flirted with this NPC (rivals in love!).
        Excludes the current player.
        """
        all_flirters = self.get_flirters_with_npc(npc_name)
        return [f for f in all_flirters if f['player_id'] != str(player_id)]

    def get_discovered_backstories(self, player_id: str, npc_name: str = None) -> Dict:
        """
        Get backstory fragments a player has discovered.

        Args:
            player_id: Player's Discord ID
            npc_name: Optional specific NPC, or None for all

        Returns:
            Dict of discovered fragment IDs by NPC
        """
        player = self.get_player(player_id)
        discoveries = player.get('backstory_discoveries', {})

        if npc_name:
            npc_key = npc_name.lower()
            return discoveries.get(npc_key, [])
        return discoveries

    def discover_backstory_fragment(self, player_id: str, npc_name: str, fragment_id: str) -> Dict:
        """
        Record that a player has discovered a backstory fragment.

        Args:
            player_id: Player's Discord ID
            npc_name: NPC name
            fragment_id: Fragment ID being discovered

        Returns:
            Dict with success status and discovery info
        """
        player = self.get_player(player_id)

        if 'backstory_discoveries' not in player:
            player['backstory_discoveries'] = {}

        npc_key = npc_name.lower()

        if npc_key not in player['backstory_discoveries']:
            player['backstory_discoveries'][npc_key] = []

        discoveries = player['backstory_discoveries'][npc_key]

        if fragment_id in discoveries:
            return {
                'success': False,
                'already_discovered': True,
                'total_discovered': len(discoveries)
            }

        discoveries.append(fragment_id)
        player['backstory_discoveries'][npc_key] = discoveries
        self._save_player(player_id)

        return {
            'success': True,
            'fragment_id': fragment_id,
            'total_discovered': len(discoveries),
            'is_first': len(discoveries) == 1
        }

    def can_unlock_backstory(self, player_id: str, npc_name: str, world_id: str, rep_threshold: int = 50, flirt_threshold: int = 5) -> Dict:
        """
        Check if a player can unlock backstory from an NPC.
        Requires high reputation OR enough flirting.

        Args:
            player_id: Player's Discord ID
            npc_name: NPC name
            world_id: World ID for reputation check
            rep_threshold: Reputation needed (default 50)
            flirt_threshold: Flirt count needed (default 5)

        Returns:
            Dict with unlock status and reason
        """
        
        rep_data = self.get_reputation(player_id, world_id, npc_name.lower())
        reputation = rep_data.get('reputation', 0)

        relationship = self.get_relationship(player_id, npc_name)
        flirt_count = relationship.get('flirt_count', 0)

        can_unlock = reputation >= rep_threshold or flirt_count >= flirt_threshold

        unlock_reason = None
        if reputation >= rep_threshold:
            unlock_reason = 'reputation'
        elif flirt_count >= flirt_threshold:
            unlock_reason = 'flirting'

        return {
            'can_unlock': can_unlock,
            'unlock_reason': unlock_reason,
            'reputation': reputation,
            'rep_threshold': rep_threshold,
            'flirt_count': flirt_count,
            'flirt_threshold': flirt_threshold
        }

    MEMORY_TYPES = {
        'first_meeting': 'First time meeting this player',
        'quest_complete': 'Player completed a quest for this NPC',
        'milestone': 'A notable achievement or moment',
        'purchase': 'Significant purchase from this NPC',
        'conversation': 'Memorable conversation topic',
        'preference': 'Learned player preference',
        'gift': 'Player gave a gift to this NPC',
        'flirt_milestone': 'Relationship milestone reached',
    }

    MAX_MEMORIES_PER_NPC = 20

    def _ensure_npc_relationship(self, player_id: str, npc_name: str, player_name: str = None) -> Dict:
        """Ensure a relationship entry exists for this NPC, return it"""
        player = self.get_player(player_id, player_name)

        if 'relationships' not in player:
            player['relationships'] = {}

        npc_key = npc_name.lower()

        if npc_key not in player['relationships']:
            player['relationships'][npc_key] = {
                'npc_name': npc_name,
                'flirt_count': 0,
                'first_flirt': None,
                'last_flirt': None,
                'relationship_level': 'stranger',
                'memories': [],
                'interaction_count': 0,
                'first_interaction': None,
                'last_interaction': None,
                'quests_completed': 0,
                'total_spent': 0,
                'preferences': {}
            }
        else:
            
            rel = player['relationships'][npc_key]
            if 'memories' not in rel:
                rel['memories'] = []
            if 'interaction_count' not in rel:
                rel['interaction_count'] = 0
            if 'first_interaction' not in rel:
                rel['first_interaction'] = None
            if 'last_interaction' not in rel:
                rel['last_interaction'] = None
            if 'quests_completed' not in rel:
                rel['quests_completed'] = 0
            if 'total_spent' not in rel:
                rel['total_spent'] = 0
            if 'preferences' not in rel:
                rel['preferences'] = {}

        return player['relationships'][npc_key]

    def record_npc_interaction(self, player_id: str, npc_name: str, player_name: str = None) -> Dict:
        """
        Record that a player interacted with an NPC.
        Called on every NPC response to track interaction count.

        Returns interaction stats for context building.
        """
        player = self.get_player(player_id, player_name)
        rel = self._ensure_npc_relationship(player_id, npc_name, player_name)

        now = datetime.now()
        is_first = rel['first_interaction'] is None

        rel['interaction_count'] += 1
        rel['last_interaction'] = now.isoformat()

        if is_first:
            rel['first_interaction'] = now.isoformat()
            
            self.add_npc_memory(
                player_id, npc_name,
                memory_type='first_meeting',
                summary=f"{player_name} met {npc_name} for the first time",
                importance=3,
                player_name=player_name
            )

        self._save_player(player_id)

        return {
            'interaction_count': rel['interaction_count'],
            'is_first_meeting': is_first,
            'first_interaction': rel['first_interaction'],
            'last_interaction': rel['last_interaction'],
            'quests_completed': rel['quests_completed'],
            'relationship_level': rel.get('relationship_level', 'stranger')
        }

    def add_npc_memory(
        self,
        player_id: str,
        npc_name: str,
        memory_type: str,
        summary: str,
        importance: int = 1,
        details: str = None,
        player_name: str = None
    ) -> Dict:
        """Commons runtime helper."""
        player = self.get_player(player_id, player_name)
        rel = self._ensure_npc_relationship(player_id, npc_name, player_name)

        memory = {
            'id': f"{npc_name.lower()}_{len(rel['memories'])}_{datetime.now().strftime('%Y%m%d%H%M%S')}",
            'type': memory_type,
            'summary': summary,
            'importance': min(5, max(1, importance)),  
            'timestamp': datetime.now().isoformat(),
        }

        if details:
            memory['details'] = details

        rel['memories'].append(memory)

        if len(rel['memories']) > self.MAX_MEMORIES_PER_NPC:
            
            rel['memories'].sort(key=lambda m: (m['importance'], m['timestamp']), reverse=True)
            
            rel['memories'] = rel['memories'][:self.MAX_MEMORIES_PER_NPC]

        self._save_player(player_id)

        return {
            'success': True,
            'memory_id': memory['id'],
            'total_memories': len(rel['memories'])
        }

    def record_quest_for_npc(self, player_id: str, npc_name: str, quest_title: str, player_name: str = None) -> Dict:
        """
        Record that a player completed a quest for this NPC.
        Automatically creates appropriate memories and updates stats.
        """
        player = self.get_player(player_id, player_name)
        rel = self._ensure_npc_relationship(player_id, npc_name, player_name)

        rel['quests_completed'] = rel.get('quests_completed', 0) + 1
        quest_count = rel['quests_completed']

        if quest_count == 1:
            importance = 4
            summary = f"{player_name} completed their first quest for {npc_name}: {quest_title}"
        elif quest_count == 5:
            importance = 4
            summary = f"{player_name} has now completed 5 quests for {npc_name} - becoming a trusted helper"
        elif quest_count == 10:
            importance = 5
            summary = f"{player_name} reached 10 quests for {npc_name} - a true ally"
        elif quest_count % 10 == 0:
            importance = 4
            summary = f"{player_name} completed their {quest_count}th quest for {npc_name}"
        else:
            importance = 2
            summary = f"{player_name} completed: {quest_title}"

        self.add_npc_memory(
            player_id, npc_name,
            memory_type='quest_complete',
            summary=summary,
            importance=importance,
            player_name=player_name
        )

        self._save_player(player_id)

        return {
            'quests_completed': quest_count,
            'is_milestone': quest_count in [1, 5, 10] or quest_count % 10 == 0
        }

    def record_purchase_from_npc(self, player_id: str, npc_name: str, item_name: str, amount: float, player_name: str = None) -> Dict:
        """
        Record a purchase from this NPC.
        Only creates memory for significant purchases.
        """
        player = self.get_player(player_id, player_name)
        rel = self._ensure_npc_relationship(player_id, npc_name, player_name)

        rel['total_spent'] = rel.get('total_spent', 0) + amount
        total = rel['total_spent']

        should_record = False
        importance = 1
        summary = ""

        if amount >= 500:
            should_record = True
            importance = 3
            summary = f"{player_name} made a big purchase: {item_name} for {amount}"
        elif total >= 1000 and (total - amount) < 1000:
            should_record = True
            importance = 3
            summary = f"{player_name} has now spent over 1000 at {npc_name}'s shop - a valued customer"
        elif total >= 500 and (total - amount) < 500:
            should_record = True
            importance = 2
            summary = f"{player_name} becoming a regular customer at {npc_name}'s"

        if should_record:
            self.add_npc_memory(
                player_id, npc_name,
                memory_type='purchase',
                summary=summary,
                importance=importance,
                player_name=player_name
            )

        self._save_player(player_id)

        return {
            'total_spent': total,
            'memory_created': should_record
        }

    def record_player_preference(self, player_id: str, npc_name: str, preference_key: str, preference_value: str, player_name: str = None) -> Dict:
        """
        Record a learned preference about this player.
        e.g., "prefers_hunting_quests", "favorite_drink", etc.
        """
        player = self.get_player(player_id, player_name)
        rel = self._ensure_npc_relationship(player_id, npc_name, player_name)

        is_new = preference_key not in rel['preferences']
        rel['preferences'][preference_key] = {
            'value': preference_value,
            'learned_at': datetime.now().isoformat()
        }

        if is_new:
            self.add_npc_memory(
                player_id, npc_name,
                memory_type='preference',
                summary=f"Learned that {player_name} {preference_key.replace('_', ' ')}: {preference_value}",
                importance=2,
                player_name=player_name
            )

        self._save_player(player_id)

        return {
            'success': True,
            'is_new': is_new,
            'preferences': rel['preferences']
        }

    def get_npc_memories(self, player_id: str, npc_name: str, limit: int = 5, min_importance: int = 1) -> List[Dict]:
        """
        Get memories an NPC has about this player.

        Args:
            player_id: Player's Discord ID
            npc_name: NPC whose memories to retrieve
            limit: Max memories to return
            min_importance: Minimum importance level (1-5)

        Returns:
            List of memory dicts, sorted by importance then recency
        """
        player = self.get_player(player_id)
        npc_key = npc_name.lower()

        if 'relationships' not in player or npc_key not in player['relationships']:
            return []

        rel = player['relationships'][npc_key]
        memories = rel.get('memories', [])

        filtered = [m for m in memories if m.get('importance', 1) >= min_importance]

        filtered.sort(key=lambda m: (m.get('importance', 1), m.get('timestamp', '')), reverse=True)

        return filtered[:limit]

    def get_npc_memory_context(self, player_id: str, npc_name: str, player_name: str = None) -> str:
        """
        Build a context string about this player for the NPC's LLM prompt.
        This is the main method called when generating NPC responses.

        Returns a plain text string suitable for injection into the system prompt.
        """
        player = self.get_player(player_id, player_name)
        npc_key = npc_name.lower()

        if 'relationships' not in player or npc_key not in player['relationships']:
            
            return ""

        rel = player['relationships'][npc_key]
        context_parts = []

        interaction_count = rel.get('interaction_count', 0)
        quests_completed = rel.get('quests_completed', 0)
        relationship_level = rel.get('relationship_level', 'stranger')
        first_interaction = rel.get('first_interaction')

        if interaction_count == 0:
            context_parts.append(f"You have never met {player_name} before.")
        elif interaction_count == 1:
            context_parts.append(f"You just met {player_name} for the first time.")
        elif interaction_count < 5:
            context_parts.append(f"You've seen {player_name} a few times ({interaction_count} visits).")
        elif interaction_count < 15:
            context_parts.append(f"{player_name} is becoming a familiar face ({interaction_count} visits).")
        elif interaction_count < 30:
            context_parts.append(f"{player_name} is a regular ({interaction_count} visits).")
        else:
            context_parts.append(f"{player_name} is one of your most frequent visitors ({interaction_count} visits).")

        if quests_completed > 0:
            if quests_completed == 1:
                context_parts.append(f"They've done one job for you.")
            elif quests_completed < 5:
                context_parts.append(f"They've completed {quests_completed} tasks for you.")
            elif quests_completed < 10:
                context_parts.append(f"They've proven reliable with {quests_completed} completed quests.")
            else:
                context_parts.append(f"They're one of your most trusted helpers with {quests_completed} quests done.")

        if relationship_level != 'stranger':
            flirt_count = rel.get('flirt_count', 0)
            if relationship_level == 'interested':
                context_parts.append(f"They seem interested in you (flirted {flirt_count} times).")
            elif relationship_level == 'flirty':
                context_parts.append(f"There's definitely flirtation between you (flirted {flirt_count} times).")
            elif relationship_level == 'romantic':
                context_parts.append(f"You have a romantic connection with them ({flirt_count} flirts).")
            elif relationship_level == 'smitten':
                context_parts.append(f"They are clearly smitten with you ({flirt_count} flirts). You might feel the same.")

        memories = self.get_npc_memories(player_id, npc_name, limit=3, min_importance=2)
        if memories:
            context_parts.append("You remember:")
            for mem in memories:
                context_parts.append(f"- {mem['summary']}")

        preferences = rel.get('preferences', {})
        if preferences:
            pref_strings = []
            for key, pref_data in list(preferences.items())[:3]:  
                pref_strings.append(f"{key.replace('_', ' ')}: {pref_data['value']}")
            if pref_strings:
                context_parts.append(f"You know they {', '.join(pref_strings)}.")

        return "\n".join(context_parts)

    def get_full_relationship_data(self, player_id: str, npc_name: str) -> Dict:
        """
        Get complete relationship data for display or debugging.
        """
        player = self.get_player(player_id)
        npc_key = npc_name.lower()

        if 'relationships' not in player or npc_key not in player['relationships']:
            return {
                'exists': False,
                'npc_name': npc_name,
                'interaction_count': 0,
                'quests_completed': 0,
                'relationship_level': 'stranger',
                'memories': [],
                'preferences': {}
            }

        rel = player['relationships'][npc_key].copy()
        rel['exists'] = True
        return rel

    def add_xp(self, player_id: str, amount: int, source: str = "", apply_rp_bonus: bool = True) -> Dict:
        """
        Add XP to a player and check for level ups.

        If apply_rp_bonus is True, will check for and consume roleplay XP multiplier buff.

        Returns:
            {'success': bool, 'xp_gained': int, 'new_xp': int, 'leveled_up': bool, 'new_level': int, 'rewards': list, 'rp_multiplier': float}
        """
        player = self.get_player(player_id)
        old_level = player.get('level', 1)

        rp_multiplier = 1.0
        rp_bonus_text = ""
        if apply_rp_bonus:
            rp_result = self.get_roleplay_multiplier(player_id)
            if rp_result.get('has_buff'):
                rp_multiplier = rp_result.get('multiplier', 1.0)
                amount = int(amount * rp_multiplier)
                rp_bonus_text = f" (x{rp_multiplier} RP bonus!)"

        player['xp'] = player.get('xp', 0) + amount

        new_level = self._calculate_level(player['xp'])
        leveled_up = new_level > old_level
        rewards = []

        if leveled_up:
            player['level'] = new_level

            for lvl in range(old_level + 1, new_level + 1):
                level_rewards = self.achievements_config.get('level_rewards', {}).get(str(lvl), {})
                if level_rewards:
                    rewards.append(level_rewards)

                    if 'max_hp_bonus' in level_rewards:
                        hp_bonus = level_rewards['max_hp_bonus']
                        for world_id in player['worlds']:
                            player['worlds'][world_id]['max_hp'] += hp_bonus
                            player['worlds'][world_id]['hp'] = min(
                                player['worlds'][world_id]['hp'] + hp_bonus,
                                player['worlds'][world_id]['max_hp']
                            )

                    if 'title' in level_rewards:
                        player['title'] = level_rewards['title']

        self._save_player(player_id)

        return {
            'success': True,
            'xp_gained': amount,
            'new_xp': player['xp'],
            'leveled_up': leveled_up,
            'new_level': new_level,
            'old_level': old_level,
            'rewards': rewards,
            'source': source,
            'rp_multiplier': rp_multiplier,
            'rp_bonus_text': rp_bonus_text
        }

    def _calculate_level(self, xp: int) -> int:
        """Calculate level based on XP"""
        thresholds = self.achievements_config.get('level_thresholds', {})
        level = 1
        for lvl, threshold in sorted(thresholds.items(), key=lambda x: int(x[0])):
            if xp >= threshold:
                level = int(lvl)
            else:
                break
        return level

    def get_level_info(self, player_id: str) -> Dict:
        """Get player's level information"""
        player = self.get_player(player_id)
        current_xp = player.get('xp', 0)
        current_level = player.get('level', 1)

        thresholds = self.achievements_config.get('level_thresholds', {})
        current_threshold = thresholds.get(str(current_level), 0)
        next_threshold = thresholds.get(str(current_level + 1))

        if next_threshold:
            xp_for_next = next_threshold - current_xp
            progress = (current_xp - current_threshold) / (next_threshold - current_threshold) * 100
        else:
            xp_for_next = 0
            progress = 100

        return {
            'level': current_level,
            'xp': current_xp,
            'xp_for_next_level': xp_for_next,
            'progress_percent': progress,
            'title': player.get('title'),
            'is_max_level': next_threshold is None
        }

    def get_level_bonuses(self, player_id: str) -> Dict:
        """Commons runtime helper."""
        player = self.get_player(player_id)
        level = player.get('level', 1)

        level = min(level, 200)

        stat_bonuses = self.achievements_config.get('level_stat_bonuses', {})
        attack_per_5 = stat_bonuses.get('attack_per_5_levels', 1)
        damage_per_10 = stat_bonuses.get('damage_per_10_levels', 1)
        hp_per_5 = stat_bonuses.get('hp_per_5_levels', 5)
        gather_per_20 = stat_bonuses.get('gather_bonus_per_20_levels', 1)
        crit_per_25 = stat_bonuses.get('crit_chance_per_25_levels', 1)

        attack_bonus = (level // 5) * attack_per_5
        damage_bonus = (level // 10) * damage_per_10
        gather_bonus = (level // 20) * gather_per_20
        crit_chance = (level // 25) * crit_per_25
        max_hp_bonus = (level // 5) * hp_per_5

        return {
            'attack_bonus': attack_bonus,
            'damage_bonus': damage_bonus,
            'gather_bonus': gather_bonus,
            'crit_chance': crit_chance,
            'max_hp_bonus': max_hp_bonus,
            'level': level
        }

    ROLEPLAY_INDICATORS = {
        
        'emote_patterns': [r'\*[^*]+\*', r'_[^_]+_'],
        
        'dialogue_patterns': [r'"[^"]+"|"[^"]+"|\'[^\']{3,}\''],
        
        'descriptive_words': [
            'carefully', 'slowly', 'nervously', 'quietly', 'softly',
            'cautiously', 'eagerly', 'gently', 'hesitantly', 'quickly',
            'warmly', 'coldly', 'shyly', 'boldly', 'curiously',
            'whispers', 'mutters', 'sighs', 'laughs', 'smiles',
            'grins', 'frowns', 'nods', 'shakes', 'glances',
            'looks', 'gazes', 'stares', 'peers', 'examines',
            'reaches', 'grabs', 'takes', 'holds', 'places',
            'steps', 'walks', 'approaches', 'leans', 'sits'
        ]
    }

    MIN_ROLEPLAY_LENGTH = 20

    ROLEPLAY_XP_MULTIPLIER = 2.0

    ROLEPLAY_BUFF_DURATION = 3

    def detect_roleplay(self, message: str) -> Dict:
        """
        Analyze a message to detect roleplay effort.

        Returns:
            {
                'is_roleplay': bool,
                'score': int (0-100),
                'reasons': list of why it's considered RP,
                'quality': 'none' | 'basic' | 'good' | 'excellent'
            }
        """
        import re

        words = message.split()
        if words and words[0].startswith('!'):
            
            content = ' '.join(words[1:]) if len(words) > 1 else ''
        else:
            content = message

        content_lower = content.lower()
        score = 0
        reasons = []

        if len(content) < self.MIN_ROLEPLAY_LENGTH:
            return {
                'is_roleplay': False,
                'score': 0,
                'reasons': ['Message too short'],
                'quality': 'none'
            }

        for pattern in self.ROLEPLAY_INDICATORS['emote_patterns']:
            matches = re.findall(pattern, content)
            if matches:
                score += 25 * len(matches)
                reasons.append(f"Action/emote detected ({len(matches)}x)")

        for pattern in self.ROLEPLAY_INDICATORS['dialogue_patterns']:
            matches = re.findall(pattern, content)
            if matches:
                score += 20 * len(matches)
                reasons.append(f"Dialogue detected ({len(matches)}x)")

        descriptive_count = 0
        for word in self.ROLEPLAY_INDICATORS['descriptive_words']:
            if word in content_lower:
                descriptive_count += 1

        if descriptive_count > 0:
            score += 10 * min(descriptive_count, 5)  
            reasons.append(f"Descriptive language ({descriptive_count} words)")

        if len(content) > 50:
            score += 10
            reasons.append("Detailed message")
        if len(content) > 100:
            score += 15
            reasons.append("Very detailed message")
        if len(content) > 200:
            score += 10
            reasons.append("Extensive message")

        if score >= 60:
            quality = 'excellent'
        elif score >= 35:
            quality = 'good'
        elif score >= 15:
            quality = 'basic'
        else:
            quality = 'none'

        return {
            'is_roleplay': score >= 15,
            'score': min(score, 100),
            'reasons': reasons,
            'quality': quality
        }

    def apply_roleplay_buff(self, player_id: str, quality: str, player_name: str = None) -> Dict:
        """
        Apply roleplay XP buff to a player.

        Args:
            player_id: Player's Discord ID
            quality: 'basic', 'good', or 'excellent'
            player_name: Player's display name

        Returns:
            {'success': bool, 'multiplier': float, 'actions_remaining': int, 'message': str}
        """
        player = self.get_player(player_id, player_name)

        if quality == 'excellent':
            multiplier = 2.5
            actions = 3
        elif quality == 'good':
            multiplier = 2.0
            actions = 2
        elif quality == 'basic':
            multiplier = 1.5
            actions = 1
        else:
            return {'success': False, 'message': 'No roleplay detected'}

        if 'roleplay' not in player:
            player['roleplay'] = {
                'total_rp_messages': 0,
                'total_rp_xp_bonus': 0,
                'current_buff_multiplier': 1.0,
                'current_buff_actions': 0,
                'daily_story_moment': None,
                'rp_style': 'newcomer'  
            }

        rp = player['roleplay']

        if multiplier > rp['current_buff_multiplier']:
            rp['current_buff_multiplier'] = multiplier
            rp['current_buff_actions'] = actions
        elif multiplier == rp['current_buff_multiplier']:
            
            rp['current_buff_actions'] = min(rp['current_buff_actions'] + actions, 5)
        else:
            
            rp['current_buff_actions'] = min(rp['current_buff_actions'] + 1, 5)

        rp['total_rp_messages'] = rp.get('total_rp_messages', 0) + 1

        total = rp['total_rp_messages']
        if total >= 100:
            rp['rp_style'] = 'bard'
        elif total >= 50:
            rp['rp_style'] = 'storyteller'
        elif total >= 10:
            rp['rp_style'] = 'casual'
        else:
            rp['rp_style'] = 'newcomer'

        self._save_player(player_id)

        return {
            'success': True,
            'multiplier': rp['current_buff_multiplier'],
            'actions_remaining': rp['current_buff_actions'],
            'quality': quality,
            'rp_style': rp['rp_style'],
            'message': f"Roleplay buff active! {rp['current_buff_multiplier']}x XP for next {rp['current_buff_actions']} actions"
        }

    def get_roleplay_multiplier(self, player_id: str) -> Dict:
        """
        Get current roleplay XP multiplier and consume one action if active.
        Call this when awarding XP to apply the bonus.

        Returns:
            {'multiplier': float, 'actions_remaining': int, 'was_buffed': bool}
        """
        player = self.get_player(player_id)

        if 'roleplay' not in player:
            return {'multiplier': 1.0, 'actions_remaining': 0, 'was_buffed': False}

        rp = player['roleplay']
        multiplier = rp.get('current_buff_multiplier', 1.0)
        actions = rp.get('current_buff_actions', 0)

        if actions > 0 and multiplier > 1.0:
            
            rp['current_buff_actions'] -= 1

            if rp['current_buff_actions'] <= 0:
                rp['current_buff_multiplier'] = 1.0

            self._save_player(player_id)

            return {
                'multiplier': multiplier,
                'actions_remaining': rp['current_buff_actions'],
                'was_buffed': True
            }

        return {'multiplier': 1.0, 'actions_remaining': 0, 'was_buffed': False}

    def check_daily_story_moment(self, player_id: str, player_name: str = None) -> Dict:
        """
        Check if player has used their daily story moment bonus.
        First roleplay interaction each day gives bonus XP.

        Returns:
            {'available': bool, 'bonus_xp': int, 'message': str}
        """
        player = self.get_player(player_id, player_name)

        if 'roleplay' not in player:
            player['roleplay'] = {
                'total_rp_messages': 0,
                'total_rp_xp_bonus': 0,
                'current_buff_multiplier': 1.0,
                'current_buff_actions': 0,
                'daily_story_moment': None,
                'rp_style': 'newcomer'
            }

        rp = player['roleplay']
        today = datetime.now().strftime('%Y-%m-%d')
        last_story_moment = rp.get('daily_story_moment')

        if last_story_moment != today:
            
            rp['daily_story_moment'] = today
            bonus_xp = 50
            rp['total_rp_xp_bonus'] = rp.get('total_rp_xp_bonus', 0) + bonus_xp
            self._save_player(player_id)

            return {
                'available': True,
                'bonus_xp': bonus_xp,
                'awarded': True,
                'message': "✨ **Story Moment!** +50 bonus XP for bringing the world to life!"
            }

        return {
            'available': False,
            'bonus_xp': 0,
            'awarded': False,
            'message': "You've already had your story moment today!"
        }

    def get_roleplay_stats(self, player_id: str) -> Dict:
        """Get player's roleplay statistics for display."""
        player = self.get_player(player_id)

        if 'roleplay' not in player:
            return {
                'total_rp_messages': 0,
                'total_rp_xp_bonus': 0,
                'current_buff_multiplier': 1.0,
                'current_buff_actions': 0,
                'rp_style': 'newcomer',
                'style_emoji': '📖',
                'daily_available': True
            }

        rp = player['roleplay']
        today = datetime.now().strftime('%Y-%m-%d')

        style_emojis = {
            'newcomer': '📖',
            'casual': '📜',
            'storyteller': '🎭',
            'bard': '🎪'
        }

        return {
            'total_rp_messages': rp.get('total_rp_messages', 0),
            'total_rp_xp_bonus': rp.get('total_rp_xp_bonus', 0),
            'current_buff_multiplier': rp.get('current_buff_multiplier', 1.0),
            'current_buff_actions': rp.get('current_buff_actions', 0),
            'rp_style': rp.get('rp_style', 'newcomer'),
            'style_emoji': style_emojis.get(rp.get('rp_style', 'newcomer'), '📖'),
            'daily_available': rp.get('daily_story_moment') != today
        }

    def get_quest_quality_for_roleplay(self, player_id: str) -> str:
        """
        Determine what quality of quest a player should receive based on
        their current roleplay state.

        Returns: 'standard', 'good', or 'excellent'
        """
        player = self.get_player(player_id)

        if 'roleplay' not in player:
            return 'standard'

        rp = player['roleplay']

        if rp.get('current_buff_actions', 0) > 0:
            multiplier = rp.get('current_buff_multiplier', 1.0)
            if multiplier >= 2.0:
                return 'excellent'
            elif multiplier >= 1.5:
                return 'good'

        style = rp.get('rp_style', 'newcomer')
        if style == 'bard':
            return 'excellent'
        elif style == 'storyteller':
            return 'good'

        return 'standard'

    def create_party(self, player_id: str, world_id: str, player_name: str = None) -> Dict:
        """
        Create a new party with this player as the leader.

        Returns:
            {'success': bool, 'party_id': str, 'message': str}
        """
        player = self.get_player(player_id, player_name)

        if player.get('party_id'):
            return {
                'success': False,
                'message': "You're already in a party! Use `!leaveparty` first."
            }

        party_id = f"party_{player_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}"

        party_data = {
            'party_id': party_id,
            'leader_id': player_id,
            'leader_name': player_name or player.get('name', 'Unknown'),
            'world_id': world_id,
            'members': [
                {
                    'player_id': player_id,
                    'name': player_name or player.get('name', 'Unknown'),
                    'joined_at': datetime.now().isoformat(),
                    'role': 'leader'
                }
            ],
            'created_at': datetime.now().isoformat(),
            'max_members': 5,
            'status': 'forming',  
            'current_quest': None,
            'invites': []  
        }

        player['party_id'] = party_id
        player['party_data'] = party_data

        self._save_player(player_id)

        return {
            'success': True,
            'party_id': party_id,
            'message': f"Party created! You are the party leader.\nInvite others with `!invite @player`"
        }

    def invite_to_party(self, leader_id: str, target_id: str, target_name: str) -> Dict:
        """
        Invite a player to join your party.

        Returns:
            {'success': bool, 'message': str}
        """
        leader = self.get_player(leader_id)

        if not leader.get('party_id'):
            return {
                'success': False,
                'message': "You don't have a party! Create one with `!createparty`"
            }

        party = leader.get('party_data', {})

        if party.get('leader_id') != leader_id:
            return {
                'success': False,
                'message': "Only the party leader can invite players."
            }

        if len(party.get('members', [])) >= party.get('max_members', 4):
            return {
                'success': False,
                'message': "Party is full! (Max 4 members)"
            }

        target = self.get_player(target_id, target_name)
        if target.get('party_id'):
            return {
                'success': False,
                'message': f"{target_name} is already in a party."
            }

        existing_invites = [inv['target_id'] for inv in party.get('invites', [])]
        if target_id in existing_invites:
            return {
                'success': False,
                'message': f"{target_name} already has a pending invite."
            }

        if 'invites' not in party:
            party['invites'] = []

        party['invites'].append({
            'target_id': target_id,
            'target_name': target_name,
            'invited_at': datetime.now().isoformat(),
            'expires_at': None  
        })

        leader['party_data'] = party
        self._save_player(leader_id)

        if 'pending_party_invites' not in target:
            target['pending_party_invites'] = []

        target['pending_party_invites'].append({
            'party_id': party['party_id'],
            'leader_id': leader_id,
            'leader_name': party.get('leader_name', 'Unknown'),
            'invited_at': datetime.now().isoformat()
        })
        self._save_player(target_id)

        return {
            'success': True,
            'message': f"Invited {target_name} to the party!\nThey can join with `!joinparty`"
        }

    def join_party(self, player_id: str, player_name: str = None, party_id: str = None) -> Dict:
        """
        Join a party you've been invited to.

        If party_id is None, joins the most recent invite.

        Returns:
            {'success': bool, 'party': dict, 'message': str}
        """
        player = self.get_player(player_id, player_name)

        if player.get('party_id'):
            return {
                'success': False,
                'message': "You're already in a party! Use `!leaveparty` first."
            }

        invites = player.get('pending_party_invites', [])
        if not invites:
            return {
                'success': False,
                'message': "You don't have any party invites."
            }

        if party_id:
            invite = next((inv for inv in invites if inv['party_id'] == party_id), None)
        else:
            
            invite = invites[-1]

        if not invite:
            return {
                'success': False,
                'message': "Party invite not found or expired."
            }

        leader = self.get_player(invite['leader_id'])
        party = leader.get('party_data', {})

        if not party or party.get('party_id') != invite['party_id']:
            
            player['pending_party_invites'] = [inv for inv in invites if inv['party_id'] != invite['party_id']]
            self._save_player(player_id)
            return {
                'success': False,
                'message': "That party no longer exists."
            }

        if len(party.get('members', [])) >= party.get('max_members', 4):
            return {
                'success': False,
                'message': "Party is now full!"
            }

        party['members'].append({
            'player_id': player_id,
            'name': player_name or player.get('name', 'Unknown'),
            'joined_at': datetime.now().isoformat(),
            'role': 'member'
        })

        party['invites'] = [inv for inv in party.get('invites', []) if inv['target_id'] != player_id]

        leader['party_data'] = party
        self._save_player(invite['leader_id'])

        player['party_id'] = party['party_id']
        player['party_leader_id'] = invite['leader_id']
        player['pending_party_invites'] = [inv for inv in invites if inv['party_id'] != invite['party_id']]
        self._save_player(player_id)

        self._sync_party_to_members(party, invite['leader_id'])

        return {
            'success': True,
            'party': party,
            'message': f"You joined {invite['leader_name']}'s party!"
        }

    def leave_party(self, player_id: str) -> Dict:
        """
        Leave current party.

        Returns:
            {'success': bool, 'message': str, 'party_disbanded': bool}
        """
        player = self.get_player(player_id)

        if not player.get('party_id'):
            return {
                'success': False,
                'message': "You're not in a party."
            }

        leader_id = player.get('party_leader_id', player_id)
        leader = self.get_player(leader_id)
        party = leader.get('party_data', {})

        party_disbanded = False

        if party.get('leader_id') == player_id:
            
            members = party.get('members', [])
            other_members = [m for m in members if m['player_id'] != player_id]

            if other_members:
                
                new_leader = other_members[0]
                party['leader_id'] = new_leader['player_id']
                party['leader_name'] = new_leader['name']
                new_leader['role'] = 'leader'
                party['members'] = other_members

                new_leader_player = self.get_player(new_leader['player_id'])
                new_leader_player['party_data'] = party
                new_leader_player['party_leader_id'] = new_leader['player_id']
                self._save_player(new_leader['player_id'])

                for member in other_members[1:]:
                    member_player = self.get_player(member['player_id'])
                    member_player['party_leader_id'] = new_leader['player_id']
                    self._save_player(member['player_id'])

                player['party_id'] = None
                player['party_data'] = None
                player['party_leader_id'] = None
                self._save_player(player_id)

                return {
                    'success': True,
                    'party_disbanded': False,
                    'new_leader': new_leader['name'],
                    'message': f"You left the party. {new_leader['name']} is now the leader."
                }
            else:
                
                party_disbanded = True
                player['party_id'] = None
                player['party_data'] = None
                player['party_leader_id'] = None
                self._save_player(player_id)

                return {
                    'success': True,
                    'party_disbanded': True,
                    'message': "Party disbanded (you were the only member)."
                }
        else:
            
            party['members'] = [m for m in party.get('members', []) if m['player_id'] != player_id]
            leader['party_data'] = party
            self._save_player(leader_id)

            player['party_id'] = None
            player['party_leader_id'] = None
            self._save_player(player_id)

            self._sync_party_to_members(party, leader_id)

            return {
                'success': True,
                'party_disbanded': False,
                'message': "You left the party."
            }

    def _sync_party_to_members(self, party: Dict, leader_id: str):
        """Sync party data to all members (excluding leader who has master copy)."""
        for member in party.get('members', []):
            if member['player_id'] != leader_id:
                member_player = self.get_player(member['player_id'])
                member_player['party_leader_id'] = leader_id
                self._save_player(member['player_id'])

    def get_party(self, player_id: str) -> Optional[Dict]:
        """
        Get the party data for a player.

        Returns party data dict or None if not in a party.
        """
        player = self.get_player(player_id)

        if not player.get('party_id'):
            return None

        if player.get('party_data'):
            return player['party_data']

        leader_id = player.get('party_leader_id')
        if leader_id:
            leader = self.get_player(leader_id)
            return leader.get('party_data')

        return None

    def get_party_members(self, player_id: str) -> List[Dict]:
        """Get list of party members."""
        party = self.get_party(player_id)
        if not party:
            return []
        return party.get('members', [])

    def start_party_quest(self, leader_id: str, world_id: str, quest: Dict) -> Dict:
        """
        Start a party quest. Only the leader can start quests.

        Party quests have shared objectives that all members contribute to.

        Returns:
            {'success': bool, 'message': str, 'quest': dict}
        """
        leader = self.get_player(leader_id)
        party = leader.get('party_data')

        if not party:
            return {
                'success': False,
                'message': "You don't have a party."
            }

        if party.get('leader_id') != leader_id:
            return {
                'success': False,
                'message': "Only the party leader can start quests."
            }

        if party.get('current_quest'):
            return {
                'success': False,
                'message': "Your party already has an active quest!"
            }

        for member in party.get('members', []):
            member_data = self.get_world_data(member['player_id'], world_id)
            if member_data.get('current_quest'):
                return {
                    'success': False,
                    'message': f"{member['name']} is already on a solo quest. They must complete or abandon it first."
                }

        num_members = len(party.get('members', []))
        scaled_quest = quest.copy()

        base_target = quest.get('target_amount', 5)
        scaled_quest['target_amount'] = int(base_target * (1 + (num_members - 1) * 0.5))  
        scaled_quest['reward_currency'] = int(quest.get('reward_currency', 100) * (1 + (num_members - 1) * 0.3))  
        scaled_quest['is_party_quest'] = True
        scaled_quest['party_id'] = party['party_id']
        scaled_quest['progress'] = 0
        scaled_quest['member_contributions'] = {m['player_id']: 0 for m in party['members']}
        scaled_quest['started_at'] = datetime.now().isoformat()

        party['current_quest'] = scaled_quest
        party['status'] = 'on_quest'

        for member in party['members']:
            member_player = self.get_player(member['player_id'])
            world_data = self.get_world_data(member['player_id'], world_id)
            world_data['on_party_quest'] = True
            world_data['party_quest_id'] = party['party_id']
            self._save_player(member['player_id'])

        leader['party_data'] = party
        self._save_player(leader_id)

        return {
            'success': True,
            'quest': scaled_quest,
            'message': f"Party quest started! Target: {scaled_quest['target_amount']} {quest['target_item']}"
        }

    def contribute_to_party_quest(self, player_id: str, world_id: str, amount: int = 1) -> Dict:
        """
        Add contribution to party quest progress.

        Returns:
            {'success': bool, 'progress': int, 'target': int, 'completed': bool, 'message': str}
        """
        player = self.get_player(player_id)
        party = self.get_party(player_id)

        if not party:
            return {'success': False, 'message': "You're not in a party."}

        quest = party.get('current_quest')
        if not quest:
            return {'success': False, 'message': "Your party doesn't have an active quest."}

        quest['progress'] = quest.get('progress', 0) + amount

        if 'member_contributions' not in quest:
            quest['member_contributions'] = {}
        if player_id not in quest['member_contributions']:
            quest['member_contributions'][player_id] = 0
        quest['member_contributions'][player_id] += amount

        completed = quest['progress'] >= quest['target_amount']

        leader_id = party.get('leader_id')
        leader = self.get_player(leader_id)
        leader['party_data']['current_quest'] = quest
        self._save_player(leader_id)

        return {
            'success': True,
            'progress': quest['progress'],
            'target': quest['target_amount'],
            'completed': completed,
            'your_contribution': quest['member_contributions'][player_id],
            'message': f"Progress: {quest['progress']}/{quest['target_amount']}"
        }

    def complete_party_quest(self, player_id: str, world_id: str) -> Dict:
        """
        Complete a party quest and distribute rewards.

        Returns:
            {'success': bool, 'rewards': dict, 'contributions': dict, 'message': str}
        """
        party = self.get_party(player_id)

        if not party:
            return {'success': False, 'message': "You're not in a party."}

        quest = party.get('current_quest')
        if not quest:
            return {'success': False, 'message': "Your party doesn't have an active quest."}

        if quest['progress'] < quest['target_amount']:
            return {
                'success': False,
                'message': f"Quest not complete! ({quest['progress']}/{quest['target_amount']})"
            }

        base_currency = quest.get('reward_currency', 100)
        base_xp = self.achievements_config.get('xp_rewards', {}).get('quest_medium', 50)
        reward_items = quest.get('reward_items', [])

        contributions = quest.get('member_contributions', {})
        total_contribution = sum(contributions.values())
        rewards_given = {}

        for member in party.get('members', []):
            member_id = member['player_id']
            member_contrib = contributions.get(member_id, 0)

            if total_contribution > 0:
                contrib_ratio = member_contrib / total_contribution
            else:
                contrib_ratio = 1 / len(party['members'])  

            min_ratio = 0.5
            actual_ratio = max(min_ratio, contrib_ratio)

            member_currency = int(base_currency * actual_ratio)
            member_xp = int(base_xp * actual_ratio)

            self.add_currency(member_id, world_id, member_currency, f"Party Quest: {quest['title']}")

            xp_result = self.add_xp(member_id, member_xp, f"Party Quest: {quest['title']}")

            for item in reward_items:
                self.add_item(member_id, world_id, item)

            member_player = self.get_player(member_id)
            member_world = member_player['worlds'].get(world_id, {})
            member_world['on_party_quest'] = False
            member_world['party_quest_id'] = None
            self._save_player(member_id)

            rewards_given[member_id] = {
                'name': member['name'],
                'currency': member_currency,
                'xp': member_xp,
                'contribution': member_contrib,
                'ratio': round(contrib_ratio * 100)
            }

        leader_id = party.get('leader_id')
        leader = self.get_player(leader_id)
        completed_quest = leader['party_data']['current_quest'].copy()
        completed_quest['completed_at'] = datetime.now().isoformat()

        if 'quest_history' not in leader['party_data']:
            leader['party_data']['quest_history'] = []
        leader['party_data']['quest_history'].append(completed_quest)

        leader['party_data']['current_quest'] = None
        leader['party_data']['status'] = 'forming'
        self._save_player(leader_id)

        return {
            'success': True,
            'rewards': rewards_given,
            'quest_title': quest['title'],
            'message': "Party quest completed! Rewards distributed based on contribution."
        }

    def _load_dungeon_config(self, world_id: str) -> Dict:
        """Load dungeon config for a world."""
        path = Path(__file__).parent / f"dungeons_{world_id}.json"
        if not path.exists():
            return {'dungeons': []}
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"Failed to load {path.name}: {e}")
            return {'dungeons': []}

    def _roll_dice(self, dice_notation: str) -> int:
        """Roll dice like 1d6 or 2d8 and return the total."""
        try:
            parts = dice_notation.lower().split('d')
            num = int(parts[0]) if parts[0] else 1
            sides = int(parts[1])
        except Exception:
            return 0
        return sum(random.randint(1, sides) for _ in range(num))

    def _get_dungeon_turn_order(self, party: Dict) -> List[str]:
        """Turn order: leader first, then join order."""
        return [m['player_id'] for m in party.get('members', [])]

    def _pick_room_affix(self) -> Dict:
        """Pick a random affix for a dungeon room."""
        affixes = self.dungeon_rules.get('room_affixes', [])
        if not affixes:
            return {}
        return random.choice(affixes)

    def _apply_dungeon_damage(self, player_id: str, world_id: str, amount: int, source: str = "unknown") -> Dict:
        """Apply damage without auto-recovery (used for dungeon combat)."""
        player = self.get_player(player_id)
        world_data = player['worlds'][world_id]

        current_hp = world_data.get('hp', 100)
        max_hp = world_data.get('max_hp', 100)

        damage_reduction = self.get_equipment_bonuses(player_id, world_id).get('defense', 0)

        actual_damage = max(1, amount - damage_reduction)
        new_hp = max(0, current_hp - actual_damage)
        world_data['hp'] = new_hp
        world_data['stats']['damage_taken'] = world_data['stats'].get('damage_taken', 0) + actual_damage
        self._save_player(player_id)

        return {
            'success': True,
            'hp': new_hp,
            'max_hp': max_hp,
            'damage': actual_damage,
            'damage_reduction': damage_reduction,
            'knocked_out': new_hp <= 0,
            'message': f"Took {actual_damage} damage from {source}. ({new_hp}/{max_hp} HP)"
        }

    def _mark_dungeon_downed(self, party: Dict, player_id: str):
        dungeon = party.get('current_dungeon', {})
        downed = dungeon.get('downed', {})
        if player_id not in downed:
            downed[player_id] = {'fails': 0}
        dungeon['downed'] = downed
        party['current_dungeon'] = dungeon

    def _clear_dungeon_downed(self, party: Dict, player_id: str):
        dungeon = party.get('current_dungeon', {})
        downed = dungeon.get('downed', {})
        if player_id in downed:
            downed.pop(player_id)
            dungeon['downed'] = downed
            party['current_dungeon'] = dungeon

    def roll_downed(self, player_id: str, world_id: str) -> Dict:
        """Attempt a downed roll. Nat 20 revives to 1 HP."""
        party = self.get_party(player_id)
        if not party or not party.get('current_dungeon'):
            return {'success': False, 'message': "You're not in a dungeon."}

        dungeon = party['current_dungeon']
        downed = dungeon.get('downed', {})
        if player_id not in downed:
            return {'success': False, 'message': "You're not downed."}

        roll = random.randint(1, 20)
        if roll == 20:
            player = self.get_player(player_id)
            world_data = player['worlds'][world_id]
            world_data['hp'] = max(world_data.get('hp', 0), 1)
            self._save_player(player_id)
            self._clear_dungeon_downed(party, player_id)
            return {'success': True, 'revived': True, 'roll': roll, 'message': "Natural 20! You rise with 1 HP."}

        downed[player_id]['fails'] = downed[player_id].get('fails', 0) + 1
        dungeon['downed'] = downed
        party['current_dungeon'] = dungeon
        return {'success': True, 'revived': False, 'roll': roll, 'message': "You cling to life. The darkness waits."}

    def revive_dungeon_player(self, player_id: str, world_id: str):
        """Clear downed state if the player is healed above 0."""
        party = self.get_party(player_id)
        if not party or not party.get('current_dungeon'):
            return
        hp = self.get_hp(player_id, world_id).get('hp', 0)
        if hp > 0:
            self._clear_dungeon_downed(party, player_id)

    def _compute_party_synergy(self, party: Dict, world_id: str) -> Dict:
        """Compute party bonuses based on weapon types and stat diversity."""
        weapon_types = set()
        top_stats = set()
        for member in party.get('members', []):
            weapon = self.get_equipped_weapon(member['player_id'], world_id)
            weapon_types.add(weapon.get('type', 'unarmed'))
            stats = self.get_dungeon_stats(member['player_id'], world_id).get('stats', {})
            if stats:
                top = max(stats.items(), key=lambda x: x[1])[0]
                top_stats.add(top)

        bonuses = {'attack_bonus': 0, 'damage_bonus': 0, 'notes': []}
        if len(weapon_types) >= 3:
            bonuses['attack_bonus'] += 1
            bonuses['notes'].append("Diverse arms: +1 attack")
        if len(weapon_types) >= 4:
            bonuses['damage_bonus'] += 1
            bonuses['notes'].append("Arsenal depth: +1 damage")
        if len(top_stats) >= 3:
            bonuses['attack_bonus'] += 1
            bonuses['notes'].append("Heroic balance: +1 attack")

        return bonuses

    def add_dungeon_rp_bonus(self, player_id: str, world_id: str, bonus: int = 1):
        """Add a one-time RP bonus to the dungeon state."""
        party = self.get_party(player_id)
        if not party or not party.get('current_dungeon'):
            return

        dungeon = party['current_dungeon']
        rp_bonuses = dungeon.get('rp_bonuses', {})
        rp_bonuses[player_id] = rp_bonuses.get(player_id, 0) + bonus
        dungeon['rp_bonuses'] = rp_bonuses
        party['current_dungeon'] = dungeon

    def _get_role_config(self, role: str) -> Dict:
        return self.dungeon_rules.get('roles', {}).get(role, {})

    def set_dungeon_role(self, player_id: str, world_id: str, role: str) -> Dict:
        """Set a player's role for the current dungeon run."""
        party = self.get_party(player_id)
        if not party or not party.get('current_dungeon'):
            return {'success': False, 'message': "You're not in a dungeon."}

        role = role.lower()
        if role not in self.dungeon_rules.get('roles', {}):
            return {'success': False, 'message': f"Unknown role: {role}"}

        dungeon = party['current_dungeon']
        roles = dungeon.get('roles', {})
        if player_id in roles:
            return {'success': False, 'message': f"Role already set to {roles[player_id]} for this dungeon."}

        roles[player_id] = role
        dungeon['roles'] = roles

        leader_id = party.get('leader_id')
        leader = self.get_player(leader_id)
        leader['party_data']['current_dungeon'] = dungeon
        self._save_player(leader_id)

        return {'success': True, 'message': f"Role set to {role} for this dungeon run."}

    def get_dungeon_role(self, dungeon: Dict, player_id: str) -> Optional[str]:
        return dungeon.get('roles', {}).get(player_id)

    def _apply_attack_bonus(self, dungeon: Dict, player_id: str, amount: int):
        bonuses = dungeon.get('attack_bonuses', {})
        bonuses[player_id] = bonuses.get(player_id, 0) + amount
        dungeon['attack_bonuses'] = bonuses

    def _apply_guard(self, dungeon: Dict, target_id: str, amount: int):
        guards = dungeon.get('guarded', {})
        guards[target_id] = max(guards.get(target_id, 0), amount)
        dungeon['guarded'] = guards

    def _apply_threat(self, dungeon: Dict, player_id: str, amount: int):
        enemies = dungeon.get('active_room', {}).get('enemies', [])
        threat = dungeon.get('threat', {})
        for enemy in enemies:
            key = enemy.get('enemy_id', enemy.get('name'))
            if key not in threat:
                threat[key] = {}
            threat[key][player_id] = threat[key].get(player_id, 0) + amount
        dungeon['threat'] = threat

    def use_guard(self, player_id: str, world_id: str, target_id: str) -> Dict:
        party = self.get_party(player_id)
        if not party or not party.get('current_dungeon'):
            return {'success': False, 'message': "You're not in a dungeon."}
        dungeon = party['current_dungeon']
        self._apply_guard(dungeon, target_id, 2)
        leader_id = party.get('leader_id')
        leader = self.get_player(leader_id)
        leader['party_data']['current_dungeon'] = dungeon
        self._save_player(leader_id)
        return {'success': True, 'message': "Guarding ally. The next hit is softened."}

    def use_inspire(self, player_id: str, world_id: str, target_id: str, amount: int) -> Dict:
        party = self.get_party(player_id)
        if not party or not party.get('current_dungeon'):
            return {'success': False, 'message': "You're not in a dungeon."}
        dungeon = party['current_dungeon']
        self._apply_attack_bonus(dungeon, target_id, amount)
        leader_id = party.get('leader_id')
        leader = self.get_player(leader_id)
        leader['party_data']['current_dungeon'] = dungeon
        self._save_player(leader_id)
        return {'success': True, 'message': "Inspiration granted. Their next strike hits truer."}

    def use_sing(self, player_id: str, world_id: str, amount: int) -> Dict:
        party = self.get_party(player_id)
        if not party or not party.get('current_dungeon'):
            return {'success': False, 'message': "You're not in a dungeon."}
        dungeon = party['current_dungeon']
        for member in party.get('members', []):
            self._apply_attack_bonus(dungeon, member['player_id'], amount)
        leader_id = party.get('leader_id')
        leader = self.get_player(leader_id)
        leader['party_data']['current_dungeon'] = dungeon
        self._save_player(leader_id)
        return {'success': True, 'message': "Your song steels the party. Their next attacks strike true."}

    def use_focus(self, player_id: str, world_id: str, amount: int) -> Dict:
        party = self.get_party(player_id)
        if not party or not party.get('current_dungeon'):
            return {'success': False, 'message': "You're not in a dungeon."}
        dungeon = party['current_dungeon']
        self._apply_attack_bonus(dungeon, player_id, amount)
        leader_id = party.get('leader_id')
        leader = self.get_player(leader_id)
        leader['party_data']['current_dungeon'] = dungeon
        self._save_player(leader_id)
        return {'success': True, 'message': "You focus. Your next strike is empowered."}

    def use_taunt(self, player_id: str, world_id: str, amount: int) -> Dict:
        party = self.get_party(player_id)
        if not party or not party.get('current_dungeon'):
            return {'success': False, 'message': "You're not in a dungeon."}
        dungeon = party['current_dungeon']
        self._apply_threat(dungeon, player_id, amount)
        leader_id = party.get('leader_id')
        leader = self.get_player(leader_id)
        leader['party_data']['current_dungeon'] = dungeon
        self._save_player(leader_id)
        return {'success': True, 'message': "You draw their ire. Enemies focus on you."}

    def use_aid(self, player_id: str, world_id: str, target_id: str, amount: int) -> Dict:
        party = self.get_party(player_id)
        if not party or not party.get('current_dungeon'):
            return {'success': False, 'message': "You're not in a dungeon."}
        heal_result = self.heal(target_id, world_id, amount, "Aid")
        self.revive_dungeon_player(target_id, world_id)
        return {'success': True, 'message': heal_result.get('message', 'Aid applied.')}

    def cast_fireball(self, player_id: str, world_id: str, target_name: str, damage_die: str, bonus_attack: int) -> Dict:
        party = self.get_party(player_id)
        if not party or not party.get('current_dungeon'):
            return {'success': False, 'message': "You're not in a dungeon."}
        dungeon = party['current_dungeon']
        room = dungeon.get('active_room', {})
        enemies = room.get('enemies', [])
        target = next((e for e in enemies if e['name'].lower() == target_name.lower()), None)
        if not target:
            return {'success': False, 'message': "Target not found."}

        stats = self.get_dungeon_stats(player_id, world_id)
        int_mod = stats['mods'].get('int', 0)
        level_bonus = self.get_level_bonuses(player_id).get('attack_bonus', 0)
        affix = room.get('affix', {})
        enemy_ac = target.get('ac', 10) + int(affix.get('effects', {}).get('enemy_ac', 0))

        roll = random.randint(1, 20)
        total = roll + int_mod + level_bonus + bonus_attack
        if total < enemy_ac:
            return {'success': True, 'message': f"Fireball roll: {roll} + {int_mod + level_bonus + bonus_attack} = {total} vs AC {enemy_ac}. Miss!"}

        damage = self._roll_dice(damage_die) + int_mod
        damage = max(1, damage)
        target['hp'] -= damage
        msg = f"Fireball hits! {target['name']} takes {damage} damage. (HP {max(target['hp'], 0)}/{target.get('max_hp', 0)})"
        if target['hp'] <= 0:
            enemies.remove(target)
            msg += f"\n{target['name']} is defeated."
        return {'success': True, 'message': msg}

    def _award_dungeon_renown(self, player_id: str, world_id: str) -> Optional[str]:
        """Increase dungeon renown and return a newly earned title if any."""
        player = self.get_player(player_id)
        renown = player.get('dungeon_renown', {})
        renown[world_id] = renown.get(world_id, 0) + 1
        player['dungeon_renown'] = renown

        earned_titles = set(player.get('dungeon_titles', []))
        title_table = self.dungeon_rules.get('dungeon_titles', {}).get(world_id, [])
        new_title = None

        for entry in title_table:
            threshold = entry.get('renown', 0)
            title = entry.get('title')
            if title and renown[world_id] >= threshold and title not in earned_titles:
                earned_titles.add(title)
                new_title = title

        player['dungeon_titles'] = sorted(earned_titles)
        self._save_player(player_id)
        return new_title

    def get_dungeon_stats(self, player_id: str, world_id: str) -> Dict:
        """Calculate dungeon stats using level, type, form, and admin mods."""
        player = self.get_player(player_id)
        level = player.get('level', 1)
        character_type = player.get('character_type', 'all')
        mods = player.get('dungeon_stat_mods', {})

        base_stat = self.dungeon_rules.get('base_stat', 10)
        divisor = max(1, self.dungeon_rules.get('level_divisor', 2))
        level_bonus = level // divisor

        stats = {
            'str': base_stat + level_bonus,
            'dex': base_stat + level_bonus,
            'int': base_stat + level_bonus,
            'wis': base_stat + level_bonus,
            'cha': base_stat + level_bonus
        }

        type_bonuses = self.dungeon_rules.get('type_bonuses', {}).get(character_type, {})
        for stat in stats:
            stats[stat] += int(type_bonuses.get(stat, 0))

        form = self.get_current_form(player_id)
        form_bonuses = form.get('bonuses', {})
        stats['str'] += int(form_bonuses.get('attack', 0))
        stats['str'] += int(form_bonuses.get('damage', 0))
        stats['dex'] += int(form_bonuses.get('stealth', 0))
        stats['wis'] += int(form_bonuses.get('gathering', 0))
        stats['cha'] += int(form_bonuses.get('social', 0))

        for stat in stats:
            stats[stat] += int(mods.get(stat, 0))

        def mod(value: int) -> int:
            return (value - 10) // 2

        return {
            'stats': stats,
            'mods': {k: mod(v) for k, v in stats.items()}
        }

    def set_dungeon_stat_mod(self, player_id: str, stat: str, value: int) -> Dict:
        """Set a dungeon stat modifier for a player."""
        player = self.get_player(player_id)
        if 'dungeon_stat_mods' not in player:
            player['dungeon_stat_mods'] = {}
        player['dungeon_stat_mods'][stat] = value
        self._save_player(player_id)
        return {'success': True, 'message': f"Set {stat} mod to {value}."}

    def get_dungeon_stats_summary(self, player_id: str, world_id: str) -> Dict:
        """Return a summary of dungeon stats for display."""
        stats_data = self.get_dungeon_stats(player_id, world_id)
        stats = stats_data['stats']
        mods = stats_data['mods']
        message = (
            f"STR {stats['str']} ({mods['str']:+d}) | "
            f"DEX {stats['dex']} ({mods['dex']:+d}) | "
            f"INT {stats['int']} ({mods['int']:+d}) | "
            f"WIS {stats['wis']} ({mods['wis']:+d}) | "
            f"CHA {stats['cha']} ({mods['cha']:+d})"
        )
        return {'success': True, 'message': message}

    def get_party_dungeon(self, player_id: str) -> Optional[Dict]:
        """Return the active dungeon state for the player's party, if any."""
        party = self.get_party(player_id)
        if not party:
            return None
        return party.get('current_dungeon')

    def start_dungeon_run(self, leader_id: str, world_id: str, dungeon_id: str = None) -> Dict:
        """Start a dungeon run for a party (leader only)."""
        party = self.get_party(leader_id)
        if not party:
            return {'success': False, 'message': "You don't have a party."}
        if party.get('leader_id') != leader_id:
            return {'success': False, 'message': "Only the party leader can start a dungeon."}
        if party.get('current_dungeon'):
            return {'success': False, 'message': "Your party already has an active dungeon run."}

        dungeon_data = self._load_dungeon_config(world_id)
        dungeons = dungeon_data.get('dungeons', [])
        if not dungeons:
            return {'success': False, 'message': "No dungeons available in this world."}

        selected = None
        if dungeon_id:
            for d in dungeons:
                if d.get('dungeon_id') == dungeon_id or d.get('name', '').lower() == dungeon_id.lower():
                    selected = d
                    break
        if not selected:
            selected = random.choice(dungeons)

        party_size = len(party.get('members', []))
        min_party = selected.get('min_party', 3)
        max_party = selected.get('max_party', 5)
        if party_size < min_party:
            return {'success': False, 'message': f"Need at least {min_party} members to start this dungeon."}
        if party_size > max_party:
            return {'success': False, 'message': f"This dungeon supports up to {max_party} members."}

        rooms = selected.get('rooms', [])
        if not rooms:
            return {'success': False, 'message': "Dungeon has no rooms configured."}

        def build_room_state(room: Dict) -> Dict:
            affix = self._pick_room_affix()
            enemies = []
            for idx, enemy in enumerate(room.get('enemies', [])):
                enemy_state = enemy.copy()
                enemy_state['max_hp'] = enemy.get('hp', 1)
                enemy_state['enemy_id'] = f"{room.get('room_id', 'room')}_{idx}"
                enemies.append(enemy_state)
            return {
                'room_id': room.get('room_id', 'room'),
                'intro': room.get('intro', ''),
                'enemies': enemies,
                'affix': affix
            }

        party_bonuses = self._compute_party_synergy(party, world_id)

        dungeon_state = {
            'dungeon_id': selected.get('dungeon_id'),
            'name': selected.get('name'),
            'world_id': world_id,
            'room_index': 0,
            'rooms': rooms,
            'bonus_rooms': selected.get('bonus_rooms', []),
            'turn_order': self._get_dungeon_turn_order(party),
            'turn_index': 0,
            'active_room': build_room_state(rooms[0]),
            'rewards': selected.get('rewards', {}),
            'party_bonuses': party_bonuses,
            'threat': {},
            'downed': {},
            'rp_bonuses': {},
            'roles': {},
            'attack_bonuses': {},
            'guarded': {},
            'rogue_strike_used': {},
            'started_at': datetime.now().isoformat()
        }

        party['current_dungeon'] = dungeon_state
        leader = self.get_player(leader_id)
        leader['party_data'] = party
        self._save_player(leader_id)

        for member in party.get('members', []):
            member_player = self.get_player(member['player_id'])
            world_data = member_player['worlds'].get(world_id, {})
            world_data['on_dungeon'] = True
            world_data['dungeon_id'] = dungeon_state['dungeon_id']
            self._save_player(member['player_id'])

        enemies = dungeon_state['active_room']['enemies']
        affix = dungeon_state['active_room'].get('affix', {})
        affix_note = f"\nAffix: {affix.get('name')} - {affix.get('description')}" if affix else ""
        enemy_list = ", ".join([f"{e['name']} (HP {e['hp']})" for e in enemies])
        leader_name = party.get('members', [])[0].get('name', 'Leader') if party.get('members') else 'Leader'
        party_notes = dungeon_state.get('party_bonuses', {}).get('notes', [])
        party_note_text = f"\nParty Bonuses: {', '.join(party_notes)}" if party_notes else ""
        leader_stats = self.get_dungeon_stats_summary(leader_id, world_id).get('message', '')

        message = (
            f"Dungeon started: {dungeon_state['name']}\n"
            f"{selected.get('description', '')}\n\n"
            f"Room 1: {dungeon_state['active_room']['intro']}\n"
            f"Enemies: {enemy_list}{affix_note}{party_note_text}\n"
            f"Your stats: {leader_stats}\n\n"
            f"Turn: {leader_name} (use !attack <target>)"
        )
        return {'success': True, 'message': message}

    def get_dungeon_status(self, player_id: str) -> Dict:
        """Return current dungeon status."""
        dungeon = self.get_party_dungeon(player_id)
        if not dungeon:
            return {'success': False, 'message': "Your party is not in a dungeon."}

        room = dungeon.get('active_room', {})
        enemies = room.get('enemies', [])
        enemy_list = ", ".join([f"{e['name']} (HP {e['hp']})" for e in enemies]) if enemies else "None"
        affix = room.get('affix', {})
        affix_text = f"\nAffix: {affix.get('name')} - {affix.get('description')}" if affix else ""

        party = self.get_party(player_id)
        turn_order = dungeon.get('turn_order', [])
        turn_index = dungeon.get('turn_index', 0)
        current_id = turn_order[turn_index] if turn_order else None
        current_name = None
        for m in party.get('members', []):
            if m['player_id'] == current_id:
                current_name = m['name']
                break
        current_name = current_name or "Unknown"

        bonuses = dungeon.get('party_bonuses', {})
        notes = bonuses.get('notes', [])
        bonus_text = f"\nParty Bonuses: {', '.join(notes)}" if notes else ""
        downed = dungeon.get('downed', {})
        downed_names = [
            m['name'] for m in party.get('members', []) if m['player_id'] in downed
        ]
        downed_text = f"\nDowned: {', '.join(downed_names)}" if downed_names else ""

        message = (
            f"Dungeon: {dungeon.get('name')}\n"
            f"Room {dungeon.get('room_index', 0) + 1}: {room.get('intro', '')}\n"
            f"Enemies: {enemy_list}{affix_text}\n"
            f"Turn: {current_name}{bonus_text}{downed_text}"
        )
        return {'success': True, 'message': message}

    def flee_dungeon(self, player_id: str, world_id: str) -> Dict:
        """End the current dungeon run (leader only)."""
        party = self.get_party(player_id)
        if not party or not party.get('current_dungeon'):
            return {'success': False, 'message': "Your party is not in a dungeon."}
        if party.get('leader_id') != player_id:
            return {'success': False, 'message': "Only the party leader can end the dungeon run."}
        self._clear_dungeon_state(party, world_id)
        return {'success': True, 'message': "Dungeon run ended. The party retreats to safety."}

    def advance_dungeon_turn(self, player_id: str, world_id: str) -> Dict:
        """Advance dungeon turn after a non-attack action."""
        party = self.get_party(player_id)
        if not party or not party.get('current_dungeon'):
            return {'success': False, 'message': "Your party is not in a dungeon."}

        dungeon = party['current_dungeon']
        turn_order = dungeon.get('turn_order', [])
        turn_index = dungeon.get('turn_index', 0)
        if not turn_order:
            return {'success': False, 'message': "Dungeon has no turn order."}
        if turn_order[turn_index] != player_id:
            current_id = turn_order[turn_index]
            current_name = next((m['name'] for m in party.get('members', []) if m['player_id'] == current_id), "Unknown")
            return {'success': False, 'message': f"It is {current_name}'s turn."}

        if player_id in dungeon.get('downed', {}):
            return {'success': False, 'message': "You're downed. Use !downedroll on your turn."}

        advance_result = self._advance_dungeon_turn(party, world_id)
        leader_id = party.get('leader_id')
        leader = self.get_player(leader_id)
        leader['party_data']['current_dungeon'] = dungeon
        self._save_player(leader_id)

        alive = False
        for member in party.get('members', []):
            hp = self.get_hp(member['player_id'], world_id).get('hp', 0)
            if hp > 0:
                alive = True
                break
        if not alive:
            self._clear_dungeon_state(party, world_id)
            return {
                'success': True,
                'enemy_messages': advance_result.get('enemy_messages', []),
                'dungeon_failed': True
            }

        return {
            'success': True,
            'enemy_messages': advance_result.get('enemy_messages', [])
        }

    def _advance_dungeon_turn(self, party: Dict, world_id: str) -> Dict:
        """Advance turn order and run enemy phase if the round ends."""
        dungeon = party.get('current_dungeon', {})
        turn_order = dungeon.get('turn_order', [])
        if not turn_order:
            return {'success': False, 'message': "Dungeon has no turn order."}

        dungeon['turn_index'] += 1
        enemy_messages = []

        if dungeon['turn_index'] >= len(turn_order):
            dungeon['turn_index'] = 0
            
            enemies = dungeon.get('active_room', {}).get('enemies', [])
            if enemies:
                for enemy in enemies:
                    if enemy.get('elite') and not enemy.get('enraged') and enemy.get('hp', 0) <= (enemy.get('max_hp', 0) // 2):
                        enemy['enraged'] = True
                        enemy_messages.append(f"{enemy.get('name')} enters a brutal frenzy!")
                    targets = []
                    for member in party.get('members', []):
                        hp = self.get_hp(member['player_id'], world_id).get('hp', 0)
                        if hp > 0 and member['player_id'] not in dungeon.get('downed', {}):
                            targets.append(member)
                    if not targets:
                        break

                    threat = dungeon.get('threat', {}).get(enemy.get('enemy_id', enemy.get('name')), {})
                    target = None
                    if threat:
                        ranked = sorted(
                            [(pid, threat.get(pid, 0)) for pid in [t['player_id'] for t in targets]],
                            key=lambda x: x[1],
                            reverse=True
                        )
                        if ranked and ranked[0][1] > 0:
                            target_id = ranked[0][0]
                            target = next((t for t in targets if t['player_id'] == target_id), None)
                    if not target:
                        target = random.choice(targets)
                    target_id = target['player_id']

                    stats = self.get_dungeon_stats(target_id, world_id)
                    dex_mod = stats['mods'].get('dex', 0)
                    equipment_bonus = self.get_equipment_bonuses(target_id, world_id).get('defense', 0)
                    affix = dungeon.get('active_room', {}).get('affix', {})
                    role = dungeon.get('roles', {}).get(target_id)
                    role_cfg = self._get_role_config(role) if role else {}
                    ac = 10 + dex_mod + equipment_bonus + int(role_cfg.get('ac_bonus', 0))

                    roll = random.randint(1, 20)
                    enemy_attack = enemy.get('attack_bonus', 0)
                    if enemy.get('enraged'):
                        enemy_attack += 2
                    enemy_attack += int(affix.get('effects', {}).get('enemy_attack', 0))
                    attack_total = roll + enemy_attack
                    if attack_total >= ac:
                        damage = self._roll_dice(enemy.get('damage_die', '1d4')) + enemy.get('damage_bonus', 0)
                        if enemy.get('enraged'):
                            damage += 2
                        damage += int(affix.get('effects', {}).get('enemy_damage', 0))
                        guard_bonus = dungeon.get('guarded', {}).get(target_id, 0)
                        if guard_bonus:
                            damage = max(1, damage - guard_bonus)
                            dungeon['guarded'].pop(target_id, None)
                        damage = max(1, damage)
                        dmg_result = self._apply_dungeon_damage(target_id, world_id, damage, enemy.get('name', 'Enemy'))
                        if dmg_result.get('knocked_out'):
                            self._mark_dungeon_downed(party, target_id)
                        enemy_messages.append(
                            f"{enemy.get('name')} hits {target['name']} for {damage} damage. ({dmg_result.get('hp', 0)}/{dmg_result.get('max_hp', 0)})"
                        )
                        recovery = dmg_result.get('companion_recovery')
                        if recovery and recovery.get('healed'):
                            enemy_messages.append(recovery['name']+' restores '+str(recovery['healed'])+' HP.')
                    else:
                        enemy_messages.append(
                            f"{enemy.get('name')} misses {target['name']}."
                        )

        return {'success': True, 'enemy_messages': enemy_messages}

    def _clear_dungeon_state(self, party: Dict, world_id: str):
        """Clear dungeon state for all party members."""
        for member in party.get('members', []):
            member_player = self.get_player(member['player_id'])
            world_data = member_player['worlds'].get(world_id, {})
            world_data['on_dungeon'] = False
            world_data['dungeon_id'] = None
            self._save_player(member['player_id'])

        leader_id = party.get('leader_id')
        leader = self.get_player(leader_id)
        if leader.get('party_data'):
            leader['party_data']['current_dungeon'] = None
            self._save_player(leader_id)

    def attack_dungeon_target(self, player_id: str, world_id: str, target_name: str,
                              use_spell: bool = False, spell_damage_die: str = None,
                              spell_attack_bonus: int = 0, spell_stat: str = "int") -> Dict:
        """Resolve a dungeon attack for the current player."""
        party = self.get_party(player_id)
        if not party or not party.get('current_dungeon'):
            return {'success': False, 'message': "Your party is not in a dungeon."}

        dungeon = party['current_dungeon']
        turn_order = dungeon.get('turn_order', [])
        turn_index = dungeon.get('turn_index', 0)
        if not turn_order:
            return {'success': False, 'message': "Dungeon has no turn order."}
        if turn_order[turn_index] != player_id:
            current_id = turn_order[turn_index]
            current_name = next((m['name'] for m in party.get('members', []) if m['player_id'] == current_id), "Unknown")
            return {'success': False, 'message': f"It is {current_name}'s turn."}

        room = dungeon.get('active_room', {})
        enemies = room.get('enemies', [])
        if not enemies:
            return {'success': False, 'message': "No enemies remain in this room."}

        target = None
        for enemy in enemies:
            if enemy['name'].lower() == target_name.lower():
                target = enemy
                break
        if not target:
            available = ", ".join([e['name'] for e in enemies])
            return {'success': False, 'message': f"Target not found. Enemies: {available}"}

        weapon = self.get_equipped_weapon(player_id, world_id)
        stats = self.get_dungeon_stats(player_id, world_id)
        mods = stats['mods']
        if use_spell:
            stat_mod = mods.get(spell_stat, 0)
        else:
            stat_mod = mods.get('dex', 0) if weapon.get('type') == 'ranged' else mods.get('str', 0)

        level_bonus = self.get_level_bonuses(player_id)
        form = self.get_current_form(player_id)
        form_bonuses = form.get('bonuses', {})
        equipment_bonus = self.get_equipment_bonuses(player_id, world_id).get('attack', 0)
        party_bonuses = dungeon.get('party_bonuses', {})
        rp_bonus = dungeon.get('rp_bonuses', {}).get(player_id, 0)
        affix = room.get('affix', {})
        affix_attack = int(affix.get('effects', {}).get('player_attack', 0))
        role = dungeon.get('roles', {}).get(player_id)
        role_cfg = self._get_role_config(role) if role else {}
        attack_bonus_buff = dungeon.get('attack_bonuses', {}).get(player_id, 0)
        rogue_used = dungeon.get('rogue_strike_used', {}).get(player_id, False)
        rogue_bonus = role_cfg.get('first_strike_attack_bonus', 0) if role == 'rogue' and not rogue_used else 0
        ranger_bonus = role_cfg.get('ranged_attack_bonus', 0) if role == 'ranger' and weapon.get('type') == 'ranged' else 0

        attack_bonus = (
            stat_mod
            + (0 if use_spell else weapon.get('attack_bonus', 0))
            + level_bonus.get('attack_bonus', 0)
            + (0 if use_spell else form_bonuses.get('attack', 0))
            + (0 if use_spell else equipment_bonus)
            + party_bonuses.get('attack_bonus', 0)
            + rp_bonus
            + affix_attack
            + attack_bonus_buff
            + rogue_bonus
            + ranger_bonus
            + (spell_attack_bonus if use_spell else 0)
        )

        roll = random.randint(1, 20)
        total = roll + attack_bonus
        enemy_ac = target.get('ac', 10) + int(affix.get('effects', {}).get('enemy_ac', 0))
        hit = total >= enemy_ac

        attack_name = "Fireball" if use_spell else weapon.get('name', 'Attack')
        message_lines = [f"{attack_name} roll: {roll} + {attack_bonus} = {total} vs AC {enemy_ac}"]
        wear_fn=getattr(self,'wear_equipment',None)
        item_wear=wear_fn(player_id,world_id,weapon=True,armor=False) if not use_spell and callable(wear_fn) else []
        message_lines.extend(entry['message'] for entry in item_wear if entry.get('message'))
        if hit:
            if use_spell:
                damage = self._roll_dice(spell_damage_die or '1d6')
                damage += stat_mod
            else:
                damage = self._roll_dice(weapon.get('damage_die', '1d4'))
                damage += weapon.get('damage_bonus', 0)
                damage += level_bonus.get('damage_bonus', 0)
                damage += form_bonuses.get('damage', 0)
                damage += stat_mod
            damage += party_bonuses.get('damage_bonus', 0)
            damage += role_cfg.get('damage_bonus', 0) if role == 'fighter' else 0
            damage = max(1, damage)

            target['hp'] -= damage
            message_lines.append(f"Hit! {target['name']} takes {damage} damage. (HP {max(target['hp'], 0)}/{target.get('max_hp', 0)})")

            threat = dungeon.get('threat', {})
            enemy_key = target.get('enemy_id', target.get('name'))
            if enemy_key not in threat:
                threat[enemy_key] = {}
            threat[enemy_key][player_id] = threat[enemy_key].get(player_id, 0) + damage
            dungeon['threat'] = threat

            if target.get('elite') and not target.get('enraged') and target.get('hp', 0) <= (target.get('max_hp', 0) // 2):
                target['enraged'] = True
                message_lines.append(f"{target['name']} roars and grows more dangerous!")
        else:
            message_lines.append(f"Miss! {target['name']} dodges the blow.")

        if rp_bonus:
            dungeon['rp_bonuses'][player_id] = 0
        if attack_bonus_buff:
            dungeon['attack_bonuses'][player_id] = 0
        if role == 'rogue' and not rogue_used:
            dungeon.setdefault('rogue_strike_used', {})[player_id] = True

        if target['hp'] <= 0:
            enemies.remove(target)
            message_lines.append(f"{target['name']} is defeated.")

        if not enemies:
            rooms = dungeon.get('rooms', [])
            bonus_rooms = dungeon.get('bonus_rooms', [])
            if dungeon.get('in_bonus_room'):
                bonus_rewards = dungeon.get('pending_bonus_rewards', {})
                if bonus_rewards:
                    currency_total = bonus_rewards.get('currency', 0)
                    xp_total = bonus_rewards.get('xp', 0)
                    items = bonus_rewards.get('items', [])
                    split_currency = int(currency_total / max(1, len(party.get('members', []))))
                    split_xp = int(xp_total / max(1, len(party.get('members', []))))
                    for member in party.get('members', []):
                        self.add_currency(member['player_id'], world_id, split_currency, f"Dungeon Bonus: {dungeon.get('name')}")
                        if split_xp:
                            self.add_xp(member['player_id'], split_xp, f"Dungeon Bonus: {dungeon.get('name')}")
                        for item in items:
                            self.add_item(member['player_id'], world_id, item)
                    message_lines.append("Bonus room cleared. The party claims extra rewards.")

                dungeon['in_bonus_room'] = False
                dungeon['pending_bonus_rewards'] = {}
                dungeon['room_index'] += 1
            else:
                bonus_chance = float(self.dungeon_rules.get('bonus_room_chance', 0.0))
                if bonus_rooms and dungeon.get('room_index', 0) < len(rooms) - 1 and random.random() < bonus_chance:
                    bonus_room = random.choice(bonus_rooms)
                    dungeon['in_bonus_room'] = True
                    dungeon['pending_bonus_rewards'] = bonus_room.get('rewards', {})
                    dungeon['active_room'] = {
                        'room_id': bonus_room.get('room_id', 'bonus'),
                        'intro': bonus_room.get('intro', ''),
                        'enemies': [
                            {**e, 'max_hp': e.get('hp', 1), 'enemy_id': f"{bonus_room.get('room_id', 'bonus')}_{i}"}
                            for i, e in enumerate(bonus_room.get('enemies', []))
                        ],
                        'affix': self._pick_room_affix()
                    }
                    dungeon['rogue_strike_used'] = {}
                    message_lines.append(f"A hidden path opens. Bonus room: {dungeon['active_room']['intro']}")
                    dungeon['turn_index'] = 0
                else:
                    dungeon['room_index'] += 1

            if dungeon.get('in_bonus_room'):
                leader_id = party.get('leader_id')
                leader = self.get_player(leader_id)
                leader['party_data']['current_dungeon'] = dungeon
                self._save_player(leader_id)
                return {'success': True, 'message': "\n".join(message_lines)}

            if dungeon['room_index'] >= len(rooms):
                rewards = dungeon.get('rewards', {})

                rewards_given = {}
                currency_total = rewards.get('currency', 0)
                xp_total = rewards.get('xp', 0)
                items = rewards.get('items', [])
                split_currency = int(currency_total / max(1, len(party.get('members', []))))
                split_xp = int(xp_total / max(1, len(party.get('members', []))))

                for member in party.get('members', []):
                    self.add_currency(member['player_id'], world_id, split_currency, f"Dungeon: {dungeon.get('name')}")
                    if split_xp:
                        self.add_xp(member['player_id'], split_xp, f"Dungeon: {dungeon.get('name')}")
                    for item in items:
                        self.add_item(member['player_id'], world_id, item)
                    new_title = self._award_dungeon_renown(member['player_id'], world_id)
                    if new_title:
                        message_lines.append(f"{member['name']} earned the title: {new_title}")
                    rewards_given[member['player_id']] = {
                        'name': member['name'],
                        'currency': split_currency,
                        'xp': split_xp
                    }

                chest_chance = float(self.dungeon_rules.get('boss_chest_chance', 0.0))
                if random.random() < chest_chance:
                    bonus_cfg = self.dungeon_rules.get('boss_chest_currency_bonus', {})
                    bonus_currency = random.randint(int(bonus_cfg.get('min', 10)), int(bonus_cfg.get('max', 25)))
                    bonus_item = random.choice(items) if items else None
                    for member in party.get('members', []):
                        self.add_currency(member['player_id'], world_id, bonus_currency, f"Dungeon Chest: {dungeon.get('name')}")
                        if bonus_item:
                            self.add_item(member['player_id'], world_id, bonus_item)
                    chest_msg = f"A chest is found! Each hero gains +{bonus_currency} {self.get_world_currency(world_id).get('symbol', '')}."
                    if bonus_item:
                        chest_msg += f" Bonus item: {bonus_item}."
                    message_lines.append(chest_msg)

                self._clear_dungeon_state(party, world_id)
                message_lines.append("Dungeon complete! Rewards distributed.")
                return {'success': True, 'message': "\n".join(message_lines), 'dungeon_complete': True, 'rewards': rewards_given, 'item_wear': item_wear}

            next_room = dungeon['rooms'][dungeon['room_index']]
            dungeon['active_room'] = {
                'room_id': next_room.get('room_id', 'room'),
                'intro': next_room.get('intro', ''),
                'enemies': [
                    {**e, 'max_hp': e.get('hp', 1), 'enemy_id': f"{next_room.get('room_id', 'room')}_{i}"}
                    for i, e in enumerate(next_room.get('enemies', []))
                ],
                'affix': self._pick_room_affix()
            }
            dungeon['rogue_strike_used'] = {}
            message_lines.append(f"Room cleared. Next room: {dungeon['active_room']['intro']}")
            dungeon['turn_index'] = 0

        advance_result = self._advance_dungeon_turn(party, world_id)
        enemy_messages = advance_result.get('enemy_messages', [])

        leader_id = party.get('leader_id')
        leader = self.get_player(leader_id)
        leader['party_data']['current_dungeon'] = dungeon
        self._save_player(leader_id)

        if enemy_messages:
            message_lines.append("Enemy phase:")
            message_lines.extend(enemy_messages)

        alive = False
        for member in party.get('members', []):
            hp = self.get_hp(member['player_id'], world_id).get('hp', 0)
            if hp > 0:
                alive = True
                break
        if not alive:
            self._clear_dungeon_state(party, world_id)
            message_lines.append("The party has fallen. The dungeon run ends.")
            return {'success': True, 'message': "\n".join(message_lines), 'dungeon_failed': True, 'item_wear': item_wear}

        return {'success': True, 'message': "\n".join(message_lines), 'item_wear': item_wear}

    def get_party_quest_progress(self, player_id: str) -> Optional[Dict]:
        """Get current party quest progress."""
        party = self.get_party(player_id)
        if not party or not party.get('current_quest'):
            return None

        quest = party['current_quest']
        return {
            'title': quest.get('title'),
            'objective': quest.get('objective'),
            'target_item': quest.get('target_item'),
            'progress': quest.get('progress', 0),
            'target': quest.get('target_amount'),
            'contributions': quest.get('member_contributions', {}),
            'action_type': quest.get('action_type')
        }

    def decline_party_invite(self, player_id: str, party_id: str = None) -> Dict:
        """
        Decline a party invite.

        Returns:
            {'success': bool, 'message': str}
        """
        player = self.get_player(player_id)
        invites = player.get('pending_party_invites', [])

        if not invites:
            return {
                'success': False,
                'message': "You don't have any party invites."
            }

        if party_id:
            
            invite = next((inv for inv in invites if inv['party_id'] == party_id), None)
            if not invite:
                return {
                    'success': False,
                    'message': "Invite not found."
                }
            player['pending_party_invites'] = [inv for inv in invites if inv['party_id'] != party_id]
        else:
            
            invite = invites[-1]
            player['pending_party_invites'] = invites[:-1]

        self._save_player(player_id)

        leader = self.get_player(invite['leader_id'])
        if leader.get('party_data'):
            leader['party_data']['invites'] = [
                inv for inv in leader['party_data'].get('invites', [])
                if inv['target_id'] != player_id
            ]
            self._save_player(invite['leader_id'])

        return {
            'success': True,
            'message': f"Declined invite from {invite['leader_name']}'s party."
        }

    def kick_from_party(self, leader_id: str, target_id: str) -> Dict:
        """
        Kick a member from the party (leader only).

        Returns:
            {'success': bool, 'message': str}
        """
        leader = self.get_player(leader_id)
        party = leader.get('party_data')

        if not party:
            return {'success': False, 'message': "You don't have a party."}

        if party.get('leader_id') != leader_id:
            return {'success': False, 'message': "Only the party leader can kick members."}

        if target_id == leader_id:
            return {'success': False, 'message': "You can't kick yourself! Use `!leaveparty`."}

        member = next((m for m in party.get('members', []) if m['player_id'] == target_id), None)
        if not member:
            return {'success': False, 'message': "That player is not in your party."}

        member_name = member['name']

        party['members'] = [m for m in party['members'] if m['player_id'] != target_id]
        leader['party_data'] = party
        self._save_player(leader_id)

        target = self.get_player(target_id)
        target['party_id'] = None
        target['party_leader_id'] = None
        self._save_player(target_id)

        self._sync_party_to_members(party, leader_id)

        return {
            'success': True,
            'kicked_name': member_name,
            'message': f"{member_name} has been kicked from the party."
        }

    def record_hunt(self, player_id: str) -> List[Dict]:
        """Record a successful hunt and check for achievements"""
        player = self.get_player(player_id)
        if 'global_stats' not in player:
            player['global_stats'] = {}
        if 'total_hunts' not in player['global_stats']:
            player['global_stats']['total_hunts'] = 0
        player['global_stats']['total_hunts'] += 1
        self._save_player(player_id)
        return self.check_achievements(player_id)

    def record_gather(self, player_id: str) -> List[Dict]:
        """Record a successful gather and check for achievements"""
        player = self.get_player(player_id)
        if 'global_stats' not in player:
            player['global_stats'] = {}
        if 'total_gathers' not in player['global_stats']:
            player['global_stats']['total_gathers'] = 0
        player['global_stats']['total_gathers'] += 1
        self._save_player(player_id)
        return self.check_achievements(player_id)

    def record_craft(self, player_id: str, world_id: str) -> List[Dict]:
        """Record a successful craft and check for achievements"""
        player = self.get_player(player_id)
        world_data = player['worlds'].get(world_id, {})
        if 'stats' not in world_data:
            world_data['stats'] = {}
        if 'items_crafted' not in world_data['stats']:
            world_data['stats']['items_crafted'] = 0
        world_data['stats']['items_crafted'] += 1
        self._save_player(player_id)
        return self.check_achievements(player_id)

    def record_hack(self, player_id: str) -> List[Dict]:
        """Record a successful hack and check for achievements"""
        player = self.get_player(player_id)
        if 'global_stats' not in player:
            player['global_stats'] = {}
        if 'total_hacks' not in player['global_stats']:
            player['global_stats']['total_hacks'] = 0
        player['global_stats']['total_hacks'] += 1
        self._save_player(player_id)
        return self.check_achievements(player_id)

    def check_achievements(self, player_id: str) -> List[Dict]:
        """
        Check if player has unlocked any new achievements.

        Returns:
            List of newly unlocked achievements
        """
        player = self.get_player(player_id)
        unlocked = player.get('achievements', [])
        newly_unlocked = []

        for ach_id, ach_data in self.achievements_config.get('achievements', {}).items():
            if ach_id in unlocked:
                continue

            requirement = ach_data.get('requirement', {})
            req_type = requirement.get('type')
            req_count = requirement.get('count', 1)

            earned = False

            if req_type == 'quests':
                earned = player.get('global_stats', {}).get('total_quests_completed', 0) >= req_count
            elif req_type == 'hunts':
                earned = player.get('global_stats', {}).get('total_hunts', 0) >= req_count
            elif req_type == 'crafts':
                total_crafts = sum(
                    w.get('stats', {}).get('items_crafted', 0)
                    for w in player.get('worlds', {}).values()
                )
                earned = total_crafts >= req_count
            elif req_type == 'gathers':
                earned = player.get('global_stats', {}).get('total_gathers', 0) >= req_count
            elif req_type == 'fish_caught':
                earned = player.get('global_stats', {}).get('total_fish_caught', 0) >= req_count
            elif req_type == 'gambles_won':
                earned = player.get('global_stats', {}).get('gambles_won', 0) >= req_count
            elif req_type == 'knockouts':
                total_ko = sum(
                    w.get('stats', {}).get('times_knocked_out', 0)
                    for w in player.get('worlds', {}).values()
                )
                earned = total_ko >= req_count
            elif req_type == 'worlds_visited':
                earned = len(player.get('global_stats', {}).get('worlds_visited', [])) >= req_count
            elif req_type == 'night_actions':
                earned = player.get('global_stats', {}).get('night_actions', 0) >= req_count
            elif req_type == 'dawn_actions':
                earned = player.get('global_stats', {}).get('dawn_actions', 0) >= req_count
            elif req_type == 'total_earned':
                total_earned = sum(
                    w.get('stats', {}).get('total_earned', 0)
                    for w in player.get('worlds', {}).values()
                )
                earned = total_earned >= req_count
            elif req_type == 'max_relationship':
                for rel in player.get('relationships', {}).values():
                    if rel.get('relationship_level') == requirement.get('level'):
                        earned = True
                        break
            elif req_type == 'smitten_count':
                smitten = sum(
                    1 for rel in player.get('relationships', {}).values()
                    if rel.get('relationship_level') == 'smitten'
                )
                earned = smitten >= req_count
            elif req_type == 'max_companion_loyalty':
                companions = player.get('companion', [])
                if isinstance(companions, list):
                    for comp in companions:
                        if comp.get('loyalty', 0) >= req_count:
                            earned = True
                            break

            if earned:
                unlocked.append(ach_id)
                newly_unlocked.append({
                    'id': ach_id,
                    'name': ach_data['name'],
                    'description': ach_data['description'],
                    'reward': ach_data.get('reward', {}),
                    'title': ach_data.get('title')
                })

        player['achievements'] = unlocked
        self._save_player(player_id)

        for ach in newly_unlocked:
            
            xp_reward = ach.get('reward', {}).get('xp', 0)
            if xp_reward > 0:
                self.add_xp(player_id, xp_reward, f"Achievement: {ach['name']}")

            if ach.get('title'):
                
                player = self.get_player(player_id)
                player['title'] = ach['title']
                self._save_player(player_id)

        return newly_unlocked

    def get_achievements(self, player_id: str) -> Dict:
        """Get player's achievement status"""
        player = self.get_player(player_id)
        unlocked = player.get('achievements', [])

        all_achievements = self.achievements_config.get('achievements', {})
        visible = {k: v for k, v in all_achievements.items() if not v.get('hidden') or k in unlocked}

        return {
            'unlocked': unlocked,
            'total_unlocked': len(unlocked),
            'total_available': len(visible),
            'all_achievements': visible
        }

    def add_status_effect(self, player_id: str, effect_type: str, duration_minutes: int,
                          value: int = 0, source: str = "") -> Dict:
        """
        Add a status effect to a player.

        Args:
            effect_type: 'poison', 'buff_attack', 'buff_defense', 'debuff_attack', etc.
            duration_minutes: How long the effect lasts
            value: The strength of the effect (damage per tick, bonus amount, etc.)
            source: What caused the effect
        """
        player = self.get_player(player_id)

        expires_at = datetime.now().isoformat()
        from datetime import timedelta
        expires_at = (datetime.now() + timedelta(minutes=duration_minutes)).isoformat()

        effect = {
            'type': effect_type,
            'value': value,
            'expires_at': expires_at,
            'source': source,
            'applied_at': datetime.now().isoformat()
        }

        player['status_effects'] = [
            e for e in player.get('status_effects', [])
            if e['type'] != effect_type
        ]

        player['status_effects'].append(effect)
        self._save_player(player_id)

        return {
            'success': True,
            'effect': effect,
            'message': f"Applied {effect_type} for {duration_minutes} minutes"
        }

    def remove_status_effect(self, player_id: str, effect_type: str) -> Dict:
        """Remove a specific status effect"""
        player = self.get_player(player_id)

        old_count = len(player.get('status_effects', []))
        player['status_effects'] = [
            e for e in player.get('status_effects', [])
            if e['type'] != effect_type
        ]

        removed = old_count > len(player['status_effects'])
        self._save_player(player_id)

        return {
            'success': removed,
            'message': f"Removed {effect_type}" if removed else f"No {effect_type} effect to remove"
        }

    def get_active_status_effects(self, player_id: str) -> List[Dict]:
        """Get all active (non-expired) status effects"""
        player = self.get_player(player_id)
        now = datetime.now()

        active_effects = []
        expired_effects = []

        for effect in player.get('status_effects', []):
            expires_at = datetime.fromisoformat(effect['expires_at'])
            if expires_at > now:
                remaining = (expires_at - now).total_seconds() / 60
                effect_copy = effect.copy()
                effect_copy['remaining_minutes'] = round(remaining, 1)
                active_effects.append(effect_copy)
            else:
                expired_effects.append(effect)

        if expired_effects:
            player['status_effects'] = [
                e for e in player.get('status_effects', [])
                if datetime.fromisoformat(e['expires_at']) > now
            ]
            self._save_player(player_id)

        return active_effects

    def apply_poison_damage(self, player_id: str, world_id: str) -> Dict:
        """Apply poison damage if player is poisoned"""
        effects = self.get_active_status_effects(player_id)
        poison = next((e for e in effects if e['type'] == 'poison'), None)

        if not poison:
            return {'poisoned': False, 'damage': 0}

        damage = poison.get('value', 3)
        result = self.take_damage(player_id, world_id, damage, "Poison")

        return {
            'poisoned': True,
            'damage': damage,
            'remaining_minutes': poison.get('remaining_minutes', 0),
            'hp_result': result
        }

    def get_status_effect_bonuses(self, player_id: str) -> Dict:
        """Calculate total bonuses/penalties from active status effects"""
        effects = self.get_active_status_effects(player_id)

        bonuses = {
            'attack': 0,
            'defense': 0,
            'hunting': 0,
            'gathering': 0,
            'luck': 0
        }

        for effect in effects:
            effect_type = effect['type']
            value = effect.get('value', 0)

            if effect_type == 'buff_attack':
                bonuses['attack'] += value
            elif effect_type == 'debuff_attack':
                bonuses['attack'] -= value
            elif effect_type == 'buff_defense':
                bonuses['defense'] += value
            elif effect_type == 'buff_hunting':
                bonuses['hunting'] += value
            elif effect_type == 'buff_gathering':
                bonuses['gathering'] += value
            elif effect_type == 'buff_luck' or effect_type == 'luck':
                bonuses['luck'] += value
            elif effect_type == 'buff_all':
                for k in bonuses:
                    bonuses[k] += value

        return bonuses

    def get_time_of_day(self) -> Dict:
        """
        Get the current in-game time of day.
        Uses a 2-hour real-time = 24-hour game-time cycle.
        """
        now = datetime.now()

        minutes_since_midnight = now.hour * 60 + now.minute
        game_minutes = (minutes_since_midnight * 12) % 1440  
        game_hour = game_minutes // 60

        if 5 <= game_hour < 7:
            period = 'dawn'
            description = "The sun rises over the horizon"
            emoji = "🌅"
        elif 7 <= game_hour < 12:
            period = 'morning'
            description = "The morning sun shines brightly"
            emoji = "☀️"
        elif 12 <= game_hour < 17:
            period = 'afternoon'
            description = "The afternoon sun beats down"
            emoji = "🌤️"
        elif 17 <= game_hour < 20:
            period = 'evening'
            description = "The sun begins to set"
            emoji = "🌆"
        elif 20 <= game_hour < 22:
            period = 'dusk'
            description = "Twilight descends upon the land"
            emoji = "🌇"
        else:  
            period = 'night'
            description = "The moon illuminates the darkness"
            emoji = "🌙"

        return {
            'hour': game_hour,
            'minute': game_minutes % 60,
            'period': period,
            'description': description,
            'emoji': emoji,
            'is_night': period in ['night', 'dusk'],
            'is_dawn': period == 'dawn'
        }

    def get_time_bonuses(self) -> Dict:
        """Get bonuses based on time of day"""
        time_info = self.get_time_of_day()
        period = time_info['period']

        bonuses = {
            'stealth': 0,
            'hunting': 0,
            'gathering': 0,
            'fishing': 0
        }

        if period == 'night':
            bonuses['stealth'] = 2
            bonuses['hunting'] = -1  
        elif period == 'dawn':
            bonuses['hunting'] = 2   
            bonuses['fishing'] = 2   
        elif period == 'dusk':
            bonuses['stealth'] = 1
            bonuses['hunting'] = 1   
        elif period in ['morning', 'afternoon']:
            bonuses['gathering'] = 1  

        return bonuses

    def record_time_action(self, player_id: str):
        """Record an action for time-based achievements"""
        player = self.get_player(player_id)
        time_info = self.get_time_of_day()

        if time_info['period'] == 'night':
            player['global_stats']['night_actions'] = player['global_stats'].get('night_actions', 0) + 1
        elif time_info['period'] == 'dawn':
            player['global_stats']['dawn_actions'] = player['global_stats'].get('dawn_actions', 0) + 1

        self._save_player(player_id)

    WEATHER_TYPES = {
        'heros_journey': [
            {'name': 'Clear', 'emoji': '☀️', 'description': 'The skies are clear and bright', 'weight': 30},
            {'name': 'Cloudy', 'emoji': '☁️', 'description': 'Gray clouds drift across the sky', 'weight': 25},
            {'name': 'Rainy', 'emoji': '🌧️', 'description': 'A gentle rain falls upon the land', 'weight': 20},
            {'name': 'Stormy', 'emoji': '⛈️', 'description': 'Thunder rumbles and lightning flashes', 'weight': 10},
            {'name': 'Foggy', 'emoji': '🌫️', 'description': 'A thick mist shrouds the landscape', 'weight': 10},
            {'name': 'Magical', 'emoji': '✨', 'description': 'Arcane energies shimmer in the air', 'weight': 5}
        ],
        'dusty_trails': [
            {'name': 'Clear', 'emoji': '☀️', 'description': 'The hot sun beats down from a cloudless sky', 'weight': 35},
            {'name': 'Dusty', 'emoji': '💨', 'description': 'Dust devils dance across the plains', 'weight': 25},
            {'name': 'Cloudy', 'emoji': '☁️', 'description': 'Clouds offer brief respite from the heat', 'weight': 15},
            {'name': 'Rainy', 'emoji': '🌧️', 'description': 'A rare desert rain brings relief', 'weight': 10},
            {'name': 'Sandstorm', 'emoji': '🌪️', 'description': 'A fierce sandstorm whips across the land', 'weight': 10},
            {'name': 'Scorching', 'emoji': '🔥', 'description': 'The heat is nearly unbearable today', 'weight': 5}
        ],
        'black_serenity': [
            {'name': 'Clear', 'emoji': '🌟', 'description': 'Stars shine clearly through the viewports', 'weight': 25},
            {'name': 'Nebula', 'emoji': '🌌', 'description': 'Colorful nebula gases drift past the ship', 'weight': 20},
            {'name': 'Solar Flare', 'emoji': '☀️', 'description': 'Solar activity disrupts some systems', 'weight': 15},
            {'name': 'Ion Storm', 'emoji': '⚡', 'description': 'Electrical interference crackles through the hull', 'weight': 15},
            {'name': 'Asteroid Field', 'emoji': '🪨', 'description': 'Rocky debris drifts dangerously close', 'weight': 15},
            {'name': 'Cosmic Anomaly', 'emoji': '🔮', 'description': 'Strange readings emanate from nearby space', 'weight': 10}
        ],
        'hearthlight': [
            {'name': 'Sunny', 'emoji': '☀️', 'description': 'Warm sunshine fills the cozy spaces', 'weight': 35},
            {'name': 'Breezy', 'emoji': '🍃', 'description': 'A gentle breeze rustles through the garden', 'weight': 25},
            {'name': 'Overcast', 'emoji': '☁️', 'description': 'Soft clouds make for perfect napping weather', 'weight': 20},
            {'name': 'Drizzle', 'emoji': '🌦️', 'description': 'A light drizzle patters on the windows', 'weight': 15},
            {'name': 'Rainbow', 'emoji': '🌈', 'description': 'A beautiful rainbow arcs across the sky', 'weight': 5}
        ]
    }

    WEATHER_EFFECTS = {
        
        'Clear': {'gathering': 1, 'hunting': 1, 'fishing': 0, 'stealth': -1},
        'Cloudy': {'gathering': 0, 'hunting': 0, 'fishing': 1, 'stealth': 0},
        'Rainy': {'gathering': -1, 'hunting': -1, 'fishing': 2, 'stealth': 1},
        'Stormy': {'gathering': -2, 'hunting': -2, 'fishing': -1, 'stealth': 2},
        'Foggy': {'gathering': -1, 'hunting': -2, 'fishing': 0, 'stealth': 3},
        'Magical': {'gathering': 2, 'hunting': 0, 'fishing': 1, 'stealth': 0},
        
        'Dusty': {'gathering': -1, 'hunting': -1, 'fishing': 0, 'stealth': 1},
        'Sandstorm': {'gathering': -3, 'hunting': -2, 'fishing': -2, 'stealth': 2},
        'Scorching': {'gathering': -2, 'hunting': -1, 'fishing': -1, 'stealth': 0},
        
        'Nebula': {'gathering': 1, 'hunting': 0, 'fishing': 0, 'stealth': 1},
        'Solar Flare': {'gathering': -1, 'hunting': 0, 'fishing': 0, 'stealth': -1},
        'Ion Storm': {'gathering': -2, 'hunting': -1, 'fishing': 0, 'stealth': 0},
        'Asteroid Field': {'gathering': 2, 'hunting': -1, 'fishing': 0, 'stealth': 0},
        'Cosmic Anomaly': {'gathering': 1, 'hunting': 1, 'fishing': 1, 'stealth': 1},
        
        'Sunny': {'gathering': 1, 'hunting': 0, 'fishing': 1, 'stealth': 0},
        'Breezy': {'gathering': 1, 'hunting': 0, 'fishing': 0, 'stealth': 0},
        'Overcast': {'gathering': 0, 'hunting': 0, 'fishing': 1, 'stealth': 0},
        'Drizzle': {'gathering': 0, 'hunting': 0, 'fishing': 2, 'stealth': 0},
        'Rainbow': {'gathering': 2, 'hunting': 1, 'fishing': 2, 'stealth': 0}
    }

    def get_weather(self, world_id: str) -> Dict:
        """
        Get the current weather for a world.
        Weather changes every 4 game hours (20 real minutes).
        Uses deterministic seeding so all players see the same weather.
        """
        
        weather_types = self.WEATHER_TYPES.get(world_id, self.WEATHER_TYPES.get('heros_journey'))

        now = datetime.now()
        
        weather_period = (now.hour * 3 + now.minute // 20)  
        seed_value = hash(f"{world_id}_{now.year}_{now.month}_{now.day}_{weather_period}")

        random.seed(seed_value)

        total_weight = sum(w['weight'] for w in weather_types)
        roll = random.randint(1, total_weight)
        current_weight = 0

        selected_weather = weather_types[0]  
        for weather in weather_types:
            current_weight += weather['weight']
            if roll <= current_weight:
                selected_weather = weather
                break

        random.seed()

        effects = self.WEATHER_EFFECTS.get(selected_weather['name'], {})

        return {
            'name': selected_weather['name'],
            'emoji': selected_weather['emoji'],
            'description': selected_weather['description'],
            'effects': effects,
            'world_id': world_id
        }

    def get_weather_bonuses(self, world_id: str) -> Dict:
        """Get current weather bonuses for a world"""
        weather = self.get_weather(world_id)
        return weather.get('effects', {
            'gathering': 0,
            'hunting': 0,
            'fishing': 0,
            'stealth': 0
        })

    def format_weather_display(self, world_id: str) -> str:
        """Format weather for display to players"""
        weather = self.get_weather(world_id)
        effects = weather['effects']

        effect_parts = []
        for activity, bonus in effects.items():
            if bonus > 0:
                effect_parts.append(f"{activity.title()}: +{bonus}")
            elif bonus < 0:
                effect_parts.append(f"{activity.title()}: {bonus}")

        effects_str = ", ".join(effect_parts) if effect_parts else "No special effects"

        return (
            f"{weather['emoji']} **Current Weather: {weather['name']}**\n"
            f"*{weather['description']}*\n"
            f"Effects: {effects_str}"
        )

    def get_combined_bonuses(self, world_id: str) -> Dict:
        """Get combined time + weather bonuses for a world"""
        time_bonuses = self.get_time_bonuses()
        weather_bonuses = self.get_weather_bonuses(world_id)

        combined = {}
        for key in ['gathering', 'hunting', 'fishing', 'stealth']:
            combined[key] = time_bonuses.get(key, 0) + weather_bonuses.get(key, 0)

        return combined

    def gamble(self, player_id: str, world_id: str, amount: float) -> Dict:
        """
        Simple gambling - roll dice against the house.

        Rules:
        - Roll 1d20
        - 11+ wins (double your bet)
        - 1-10 loses (lose your bet)
        - Natural 20 wins triple
        - Natural 1 loses double
        """
        player = self.get_player(player_id)
        world_data = player['worlds'][world_id]
        currency = self.get_world_currency(world_id)

        if world_data['currency'] < amount:
            return {
                'success': False,
                'message': f"You don't have enough {currency['name']}! You only have {currency['symbol']}{world_data['currency']:.2f}"
            }

        roll = random.randint(1, 20)

        if roll == 20:
            
            winnings = amount * 3
            world_data['currency'] += winnings
            player['global_stats']['gambles_won'] = player['global_stats'].get('gambles_won', 0) + 1
            result = 'jackpot'
            message = f"🎰 **JACKPOT!** Natural 20! You win {currency['symbol']}{winnings:.2f}!"
        elif roll == 1:
            
            loss = min(amount * 2, world_data['currency'])
            world_data['currency'] -= loss
            player['global_stats']['gambles_lost'] = player['global_stats'].get('gambles_lost', 0) + 1
            result = 'critical_loss'
            message = f"🎰 **CRITICAL FAIL!** Snake eyes! You lose {currency['symbol']}{loss:.2f}!"
        elif roll >= 11:
            
            winnings = amount
            world_data['currency'] += winnings
            player['global_stats']['gambles_won'] = player['global_stats'].get('gambles_won', 0) + 1
            result = 'win'
            message = f"🎰 **WIN!** Rolled {roll}. You win {currency['symbol']}{winnings:.2f}!"
        else:
            
            world_data['currency'] -= amount
            player['global_stats']['gambles_lost'] = player['global_stats'].get('gambles_lost', 0) + 1
            result = 'loss'
            message = f"🎰 **LOSS.** Rolled {roll}. You lose {currency['symbol']}{amount:.2f}."

        if result in ['win', 'jackpot']:
            self.add_xp(player_id, self.achievements_config.get('xp_rewards', {}).get('gamble_win', 5), "Gambling win")

        self._save_player(player_id)

        new_achievements = self.check_achievements(player_id)

        return {
            'success': True,
            'roll': roll,
            'result': result,
            'message': message,
            'new_balance': world_data['currency'],
            'new_achievements': new_achievements
        }

    def fish(self, player_id: str, world_id: str, location: str) -> Dict:
        """
        Attempt to catch a fish at the specified location.
        """
        player = self.get_player(player_id)
        world_data = player['worlds'][world_id]

        fishing_spots = self.fishing_config.get('fishing_spots', {}).get(world_id, {})
        if location not in fishing_spots:
            return {
                'success': False,
                'message': f"There's nowhere to fish in #{location}!"
            }

        spot = fishing_spots[location]
        available_fish = spot.get('fish', [])

        if not available_fish:
            return {
                'success': False,
                'message': "No fish available here!"
            }

        weights = [f.get('weight', 10) for f in available_fish]
        total_weight = sum(weights)
        roll_weight = random.uniform(0, total_weight)

        current_weight = 0
        selected_fish = available_fish[0]
        for fish in available_fish:
            current_weight += fish.get('weight', 10)
            if roll_weight <= current_weight:
                selected_fish = fish
                break

        catch_roll = random.randint(1, 20)
        difficulty = selected_fish.get('difficulty', 10)

        time_bonuses = self.get_time_bonuses()
        fishing_bonus = time_bonuses.get('fishing', 0)
        equipment_bonuses = self.get_equipment_bonuses(player_id, world_id)
        total_bonus = fishing_bonus + equipment_bonuses.get('gathering', 0)

        effective_roll = catch_roll + total_bonus

        messages = self.fishing_config.get('messages', {})

        if effective_roll >= difficulty:
            
            fish_name = selected_fish['name']
            fish_value = selected_fish.get('value', 1)
            fish_rarity = selected_fish.get('rarity', 'common')

            self.add_item(player_id, world_id, fish_name, 1)

            xp_type = 'fish_rare' if fish_rarity == 'rare' else 'fish_catch'
            xp_amount = self.achievements_config.get('xp_rewards', {}).get(xp_type, 10)
            xp_result = self.add_xp(player_id, xp_amount, f"Caught {fish_name}")

            player = self.get_player(player_id)

            if 'total_fish_caught' not in player.get('global_stats', {}):
                player['global_stats']['total_fish_caught'] = 0
            player['global_stats']['total_fish_caught'] += 1

            self._save_player(player_id)

            new_achievements = self.check_achievements(player_id)

            is_critical = catch_roll == 20

            cast_msg = random.choice(messages.get('cast', ['You cast your line...']))
            bite_msg = random.choice(messages.get('bite', ['Something bites!']))
            success_msg = random.choice(messages.get('success', ['You caught it!']))

            return {
                'success': True,
                'caught': True,
                'fish': fish_name,
                'value': fish_value,
                'rarity': fish_rarity,
                'roll': catch_roll,
                'total_roll': effective_roll,
                'difficulty': difficulty,
                'is_critical': is_critical,
                'xp_gained': xp_result.get('xp_gained', xp_amount),
                'rp_multiplier': xp_result.get('rp_multiplier', 1.0),
                'rp_bonus_text': xp_result.get('rp_bonus_text', ''),
                'leveled_up': xp_result.get('leveled_up', False),
                'new_level': xp_result.get('new_level'),
                'new_achievements': new_achievements,
                'messages': {
                    'cast': cast_msg,
                    'bite': bite_msg,
                    'success': success_msg
                }
            }
        else:
            
            cast_msg = random.choice(messages.get('cast', ['You cast your line...']))

            if catch_roll == 1:
                fail_msg = random.choice(messages.get('fail', ['The fish got away...']))
            else:
                fail_msg = random.choice(messages.get('nothing', ['Nothing bites.']))

            return {
                'success': True,
                'caught': False,
                'roll': catch_roll,
                'total_roll': effective_roll,
                'difficulty': difficulty,
                'messages': {
                    'cast': cast_msg,
                    'fail': fail_msg
                }
            }

    def get_available_quest_chains(self, player_id: str, world_id: str) -> List[Dict]:
        """Get quest chains available to the player in this world"""
        player = self.get_player(player_id)
        player_chains = player.get('quest_chains', {})

        quest_file = Path(__file__).parent / f"quests_{world_id.replace('-', '_')}.json"
        if not quest_file.exists():
            return []

        with open(quest_file, 'r', encoding='utf-8') as f:
            quest_data = json.load(f)

        chains = quest_data.get('quest_chains', {})
        available = []

        for chain_id, chain_data in chains.items():
            chain_status = player_chains.get(chain_id, {'current_step': 0, 'completed': False})

            if chain_status.get('completed'):
                status = 'completed'
                current_quest = None
            elif chain_status.get('current_step', 0) == 0 and not chain_status.get('started_at'):
                status = 'available'
                current_quest = chain_data['quests'][0]
            else:
                status = 'in_progress'
                step = chain_status.get('current_step', 0)
                if 0 <= step < len(chain_data['quests']):
                    current_quest = chain_data['quests'][step]
                else:
                    current_quest = None

            available.append({
                'chain_id': chain_id,
                'name': chain_data['name'],
                'description': chain_data['description'],
                'given_by': chain_data['given_by'],
                'status': status,
                'current_step': chain_status.get('current_step', 0),
                'total_steps': len(chain_data['quests']),
                'current_quest': current_quest,
                'chain_rewards': chain_data.get('chain_rewards', {})
            })

        return available

    def start_quest_chain(self, player_id: str, world_id: str, chain_id: str) -> Dict:
        """Start or continue a quest chain"""
        player = self.get_player(player_id)
        player_chains = player.get('quest_chains', {})

        quest_file = Path(__file__).parent / f"quests_{world_id.replace('-', '_')}.json"
        if not quest_file.exists():
            return {'success': False, 'message': 'No quest chains found in this world'}

        with open(quest_file, 'r', encoding='utf-8') as f:
            quest_data = json.load(f)

        chains = quest_data.get('quest_chains', {})
        if chain_id not in chains:
            return {'success': False, 'message': f'Quest chain "{chain_id}" not found'}

        chain = chains[chain_id]
        chain_status = player_chains.get(chain_id, {'current_step': 0, 'completed': False})

        if chain_status.get('completed'):
            return {'success': False, 'message': 'You have already completed this quest chain!'}

        world_data = player['worlds'].get(world_id, {})
        if world_data.get('current_quest'):
            return {'success': False, 'message': 'You already have an active quest! Complete it first.'}

        quest_index = chain_status.get('current_step', 0)
        if quest_index >= len(chain['quests']):
            return {'success': False, 'message': 'Quest chain complete!'}

        quest = chain['quests'][quest_index].copy()
        quest['is_chain_quest'] = True
        quest['chain_id'] = chain_id
        quest['chain_name'] = chain['name']
        quest['chain_step'] = quest_index + 1  
        quest['chain_total'] = len(chain['quests'])
        quest['given_by'] = chain['given_by']
        quest['progress'] = 0

        world_data['current_quest'] = quest

        if chain_id not in player_chains:
            player_chains[chain_id] = {'current_step': 0, 'completed': False, 'started_at': datetime.now().isoformat()}
            player['quest_chains'] = player_chains

        self._save_player(player_id)

        return {
            'success': True,
            'quest': quest,
            'chain_name': chain['name'],
            'step': quest_index + 1,
            'total_steps': len(chain['quests']),
            'message': f"Started quest chain: **{chain['name']}**\n📜 {quest['title']} ({quest_index + 1}/{len(chain['quests'])})"
        }

    def advance_quest_chain(self, player_id: str, world_id: str, chain_id: str = None) -> Dict:
        """Advance to the next quest in a chain after completing current quest

        Args:
            player_id: Player's Discord ID
            world_id: World ID
            chain_id: Optional chain ID. If not provided, tries to get from current quest.
        """
        player = self.get_player(player_id)
        player_chains = player.get('quest_chains', {})
        world_data = player['worlds'].get(world_id, {})

        if not chain_id:
            quest = world_data.get('current_quest')
            if quest and quest.get('is_chain_quest'):
                chain_id = quest.get('chain_id')

        if not chain_id:
            return {'success': False, 'message': 'No chain ID provided and no active chain quest'}

        quest_file = Path(__file__).parent / f"quests_{world_id.replace('-', '_')}.json"
        with open(quest_file, 'r', encoding='utf-8') as f:
            quest_data = json.load(f)

        chain = quest_data.get('quest_chains', {}).get(chain_id)
        if not chain:
            return {'success': False, 'message': 'Quest chain not found'}

        chain_status = player_chains.get(chain_id, {'current_step': 0, 'completed': False})
        current_index = chain_status.get('current_step', 0)

        next_index = current_index + 1

        if next_index >= len(chain['quests']):
            
            chain_status['completed'] = True
            chain_status['current_step'] = next_index  
            chain_status['completed_at'] = datetime.now().isoformat()
            player_chains[chain_id] = chain_status
            player['quest_chains'] = player_chains

            chain_rewards = chain.get('chain_rewards', {})
            rewards_message = ""

            if chain_rewards.get('currency_bonus'):
                self.add_currency(player_id, world_id, chain_rewards['currency_bonus'], f"Chain: {chain['name']}")
                currency = self.get_world_currency(world_id)
                rewards_message += f"\n💰 Bonus: {currency['symbol']}{chain_rewards['currency_bonus']}"

            if chain_rewards.get('items'):
                for item in chain_rewards['items']:
                    self.add_item(player_id, world_id, item, 1)
                    rewards_message += f"\n🎁 {item}"

            if chain_rewards.get('xp_bonus'):
                self.add_xp(player_id, chain_rewards['xp_bonus'], f"Completed chain: {chain['name']}")
                rewards_message += f"\n✨ +{chain_rewards['xp_bonus']} Bonus XP"

            if chain_rewards.get('title'):
                
                title_id = chain_rewards['title'].lower().replace(' ', '_')
                title_result = self.grant_title(player_id, world_id, title_id)

                if title_result.get('success'):
                    rewards_message += f"\n🏆 **Title Earned: {title_result['title_name']}**"
                    
                    if title_result.get('reputation_gained'):
                        for rep_gain in title_result['reputation_gained']:
                            rewards_message += f"\n⭐ {rep_gain}"
                    
                    if title_result.get('permanent_bonuses'):
                        bonus_parts = []
                        for bonus_type, bonus_value in title_result['permanent_bonuses'].items():
                            if isinstance(bonus_value, bool):
                                bonus_parts.append(f"{bonus_type.replace('_', ' ').title()}")
                            else:
                                bonus_parts.append(f"+{bonus_value} {bonus_type.replace('_', ' ').title()}")
                        if bonus_parts:
                            rewards_message += f"\n🔮 Permanent: {', '.join(bonus_parts)}"
                else:
                    
                    player['title'] = chain_rewards['title']
                    rewards_message += f"\n🏅 Title Unlocked: *{chain_rewards['title']}*"

            npc_name = chain.get('given_by', '').lower()
            if npc_name:
                rep_result = self.modify_reputation(player_id, world_id, npc_name, 50, f"Completed storyline: {chain['name']}")
                if rep_result.get('success') and rep_result.get('level_changed'):
                    rewards_message += f"\n{rep_result['message']}"

            self._save_player(player_id)

            last_quest = chain['quests'][-1]
            complete_msg = last_quest.get('chain_complete_message', 'Quest chain complete!')

            return {
                'success': True,
                'chain_complete': True,
                'chain_name': chain['name'],
                'chain_rewards': chain_rewards,
                'complete_message': complete_msg,
                'message': f"🎉 **Quest Chain Complete: {chain['name']}**\n{complete_msg}{rewards_message}"
            }
        else:
            
            chain_status['current_step'] = next_index
            player_chains[chain_id] = chain_status
            player['quest_chains'] = player_chains
            self._save_player(player_id)

            completed_quest = chain['quests'][current_index]
            next_intro = completed_quest.get('next_quest_intro', 'The next part of your journey awaits...')

            return {
                'success': True,
                'chain_complete': False,
                'chain_name': chain['name'],
                'next_step': next_index + 1,  
                'total_steps': len(chain['quests']),
                'next_quest_intro': next_intro,
                'message': f"📜 **{chain['name']}** ({next_index + 1}/{len(chain['quests'])})\n{next_intro}\n\n💡 Talk to {chain['given_by']} to continue the quest chain!"
            }

    def get_reputation(self, player_id: str, world_id: str, npc_id: str = None) -> Dict:
        """
        Get player's reputation with an NPC or all NPCs in a world

        Args:
            player_id: Player's Discord ID
            world_id: World ID
            npc_id: Optional specific NPC ID, or None for all

        Returns:
            Dict with reputation data and level info
        """
        player = self.get_player(player_id)
        world_data = player['worlds'].get(world_id, {})
        reputation_data = world_data.get('reputation', {'npcs': {}, 'factions': {}})

        try:
            config_path = self.base_dir / 'reputation_config.json'
            with open(config_path, 'r', encoding='utf-8') as f:
                rep_config = json.load(f)['reputation_system']
        except (FileNotFoundError, json.JSONDecodeError, KeyError):
            return {'success': False, 'message': 'Reputation config not found'}

        levels = rep_config['reputation_levels']

        def get_rep_level(rep_value: int) -> Dict:
            """Get the reputation level info for a value"""
            for level_id, level_info in levels.items():
                if level_info['min'] <= rep_value <= level_info['max']:
                    return {
                        'level_id': level_id,
                        'label': level_info['label'],
                        'price_modifier': level_info['price_modifier'],
                        'value': rep_value
                    }
            return {'level_id': 'neutral', 'label': 'Neutral', 'price_modifier': 1.0, 'value': rep_value}

        if npc_id:
            
            npc_rep = reputation_data['npcs'].get(npc_id, 0)
            npc_config = rep_config.get('npc_reputations', {}).get(world_id, {}).get(npc_id, {})

            return {
                'success': True,
                'npc_id': npc_id,
                'npc_name': npc_config.get('name', npc_id.title()),
                'reputation': npc_rep,
                'level': get_rep_level(npc_rep),
                'greetings': npc_config.get('greetings', {})
            }
        else:
            
            all_reps = {}
            npc_configs = rep_config.get('npc_reputations', {}).get(world_id, {})

            for npc, config in npc_configs.items():
                npc_rep = reputation_data['npcs'].get(npc, config.get('starting_reputation', 0))
                all_reps[npc] = {
                    'name': config.get('name', npc.title()),
                    'reputation': npc_rep,
                    'level': get_rep_level(npc_rep)
                }

            faction_reps = {}
            faction_configs = rep_config.get('faction_reputations', {}).get(world_id, {})
            for faction, config in faction_configs.items():
                faction_rep = reputation_data['factions'].get(faction, config.get('starting_reputation', 0))
                faction_reps[faction] = {
                    'name': config.get('name', faction.title()),
                    'reputation': faction_rep,
                    'level': get_rep_level(faction_rep)
                }

            return {
                'success': True,
                'npcs': all_reps,
                'factions': faction_reps
            }

    def modify_reputation(self, player_id: str, world_id: str, npc_id: str, amount: int, reason: str = "") -> Dict:
        """
        Modify player's reputation with an NPC

        Args:
            player_id: Player's Discord ID
            world_id: World ID
            npc_id: NPC ID (e.g., 'victor', 'bran')
            amount: Amount to add (positive) or subtract (negative)
            reason: Optional reason for the change

        Returns:
            Dict with result and new reputation info
        """
        player = self.get_player(player_id)
        world_data = player['worlds'].get(world_id)

        if not world_data:
            return {'success': False, 'message': 'World not found'}

        if 'reputation' not in world_data:
            world_data['reputation'] = {'npcs': {}, 'factions': {}}

        try:
            config_path = self.base_dir / 'reputation_config.json'
            with open(config_path, 'r', encoding='utf-8') as f:
                rep_config = json.load(f)['reputation_system']
        except (FileNotFoundError, json.JSONDecodeError, KeyError):
            return {'success': False, 'message': 'Reputation config not found'}

        npc_config = rep_config.get('npc_reputations', {}).get(world_id, {}).get(npc_id, {})
        starting_rep = npc_config.get('starting_reputation', 0)

        old_rep = world_data['reputation']['npcs'].get(npc_id, starting_rep)
        new_rep = old_rep + amount

        new_rep = max(-100, min(999, new_rep))

        world_data['reputation']['npcs'][npc_id] = new_rep

        levels = rep_config['reputation_levels']
        old_level = None
        new_level = None

        for level_id, level_info in levels.items():
            if level_info['min'] <= old_rep <= level_info['max']:
                old_level = level_info['label']
            if level_info['min'] <= new_rep <= level_info['max']:
                new_level = level_info['label']

        faction_bonus_msg = ""
        faction_configs = rep_config.get('faction_reputations', {}).get(world_id, {})
        for faction_id, faction_config in faction_configs.items():
            if npc_id in faction_config.get('linked_npcs', []):
                share = faction_config.get('reputation_share', 0.25)
                faction_amount = int(amount * share)
                if faction_amount != 0:
                    old_faction_rep = world_data['reputation']['factions'].get(faction_id, 0)
                    new_faction_rep = max(-100, min(999, old_faction_rep + faction_amount))
                    world_data['reputation']['factions'][faction_id] = new_faction_rep
                    faction_bonus_msg = f"\n({faction_config['name']}: {'+' if faction_amount > 0 else ''}{faction_amount})"

        self._save_player(player_id)

        npc_name = npc_config.get('name', npc_id.title())
        change_str = f"+{amount}" if amount > 0 else str(amount)

        result = {
            'success': True,
            'npc_id': npc_id,
            'npc_name': npc_name,
            'old_reputation': old_rep,
            'new_reputation': new_rep,
            'change': amount,
            'old_level': old_level,
            'new_level': new_level,
            'level_changed': old_level != new_level
        }

        if old_level != new_level:
            if amount > 0:
                result['message'] = f"⭐ Reputation with {npc_name}: {change_str} ({new_rep})\n🎊 **Reputation increased to {new_level}!**{faction_bonus_msg}"
            else:
                result['message'] = f"💔 Reputation with {npc_name}: {change_str} ({new_rep})\n😔 **Reputation decreased to {new_level}**{faction_bonus_msg}"
        else:
            emoji = "⭐" if amount > 0 else "💔"
            result['message'] = f"{emoji} Reputation with {npc_name}: {change_str} ({new_rep}){faction_bonus_msg}"

        return result

    def get_price_modifier(self, player_id: str, world_id: str, npc_id: str) -> float:
        """
        Get the price modifier based on reputation with an NPC

        Args:
            player_id: Player's Discord ID
            world_id: World ID
            npc_id: NPC ID

        Returns:
            Price modifier (1.0 = normal, <1.0 = discount, >1.0 = markup)
        """
        rep_data = self.get_reputation(player_id, world_id, npc_id)

        if not rep_data.get('success'):
            return 1.0

        return rep_data.get('level', {}).get('price_modifier', 1.0)

    def get_npc_greeting(self, player_id: str, world_id: str, npc_id: str) -> str:
        """
        Get the appropriate greeting based on reputation level

        Args:
            player_id: Player's Discord ID
            world_id: World ID
            npc_id: NPC ID

        Returns:
            Greeting string for current reputation level
        """
        rep_data = self.get_reputation(player_id, world_id, npc_id)

        if not rep_data.get('success'):
            return None

        level_id = rep_data.get('level', {}).get('level_id', 'neutral')
        greetings = rep_data.get('greetings', {})

        return greetings.get(level_id)

    def grant_title(self, player_id: str, world_id: str, title_id: str) -> Dict:
        """
        Grant a special title to a player and apply its reputation bonuses

        Args:
            player_id: Player's Discord ID
            world_id: World ID
            title_id: Title ID from reputation_config special_titles

        Returns:
            Dict with result and bonuses applied
        """
        player = self.get_player(player_id)
        world_data = player['worlds'].get(world_id)

        if not world_data:
            return {'success': False, 'message': 'World not found'}

        try:
            config_path = self.base_dir / 'reputation_config.json'
            with open(config_path, 'r', encoding='utf-8') as f:
                rep_config = json.load(f)['reputation_system']
        except (FileNotFoundError, json.JSONDecodeError, KeyError):
            return {'success': False, 'message': 'Reputation config not found'}

        title_info = rep_config.get('special_titles', {}).get(title_id)
        if not title_info:
            return {'success': False, 'message': f'Title "{title_id}" not found'}

        if 'titles' not in world_data:
            world_data['titles'] = []

        if title_id in world_data['titles']:
            return {'success': False, 'message': f'Already has title: {title_info["name"]}'}

        world_data['titles'].append(title_id)

        grants = title_info.get('grants', {})
        rep_messages = []

        for grant_key, grant_amount in grants.items():
            if grant_key.endswith('_reputation'):
                
                target_id = grant_key.replace('_reputation', '')

                faction_configs = rep_config.get('faction_reputations', {}).get(world_id, {})
                if target_id in faction_configs:
                    
                    if 'factions' not in world_data['reputation']:
                        world_data['reputation']['factions'] = {}
                    old_rep = world_data['reputation']['factions'].get(target_id, 0)
                    world_data['reputation']['factions'][target_id] = old_rep + grant_amount
                    rep_messages.append(f"{faction_configs[target_id]['name']}: +{grant_amount}")
                else:
                    
                    result = self.modify_reputation(player_id, world_id, target_id, grant_amount, f"Title: {title_info['name']}")
                    if result.get('success'):
                        rep_messages.append(f"{result['npc_name']}: +{grant_amount}")

        self._save_player(player_id)

        bonuses = title_info.get('permanent_bonuses', {})
        bonus_text = ""
        if bonuses:
            bonus_parts = []
            for bonus_type, bonus_value in bonuses.items():
                if isinstance(bonus_value, bool):
                    bonus_parts.append(f"{bonus_type.replace('_', ' ').title()}")
                else:
                    bonus_parts.append(f"+{bonus_value} {bonus_type.replace('_', ' ').title()}")
            bonus_text = "\n**Permanent Bonuses:** " + ", ".join(bonus_parts)

        return {
            'success': True,
            'title_id': title_id,
            'title_name': title_info['name'],
            'description': title_info['description'],
            'reputation_gained': rep_messages,
            'permanent_bonuses': bonuses,
            'message': f"🏆 **Title Earned: {title_info['name']}**\n{title_info['description']}\n\n**Reputation:** " + ", ".join(rep_messages) + bonus_text
        }

    def get_player_titles(self, player_id: str, world_id: str) -> Dict:
        """
        Get all titles a player has earned in a world

        Returns:
            Dict with list of titles and their bonuses
        """
        player = self.get_player(player_id)
        world_data = player['worlds'].get(world_id, {})
        player_titles = world_data.get('titles', [])

        try:
            config_path = self.base_dir / 'reputation_config.json'
            with open(config_path, 'r', encoding='utf-8') as f:
                rep_config = json.load(f)['reputation_system']
        except (FileNotFoundError, json.JSONDecodeError, KeyError):
            return {'success': False, 'message': 'Reputation config not found'}

        titles_info = []
        total_bonuses = {}

        for title_id in player_titles:
            title_info = rep_config.get('special_titles', {}).get(title_id)
            if title_info:
                titles_info.append({
                    'id': title_id,
                    'name': title_info['name'],
                    'description': title_info['description'],
                    'bonuses': title_info.get('permanent_bonuses', {})
                })
                
                for bonus_type, bonus_value in title_info.get('permanent_bonuses', {}).items():
                    if isinstance(bonus_value, bool):
                        total_bonuses[bonus_type] = True
                    else:
                        total_bonuses[bonus_type] = total_bonuses.get(bonus_type, 0) + bonus_value

        return {
            'success': True,
            'titles': titles_info,
            'total_bonuses': total_bonuses
        }

    def get_title_bonus(self, player_id: str, world_id: str, bonus_type: str) -> int:
        """
        Get the total bonus of a specific type from all titles

        Args:
            player_id: Player's Discord ID
            world_id: World ID
            bonus_type: Type of bonus (e.g., 'wolf_hunting', 'social')

        Returns:
            Total bonus value (0 if none)
        """
        titles_data = self.get_player_titles(player_id, world_id)
        if not titles_data.get('success'):
            return 0

        return titles_data.get('total_bonuses', {}).get(bonus_type, 0)
