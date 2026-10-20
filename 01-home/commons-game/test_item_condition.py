"""Stable gear bodies wear, break and mend without losing ownership."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'server'))
from adventure.players import Players
from adventure.store import Store
from adventure.engine.multi_world_quest_handler import MultiWorldQuestHandler

class ItemConditionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.temp.name)/'private',Path(self.temp.name)/'world')
        self.players=Players(self.store)
        self.player=self.players.get_player('tester')

    def tearDown(self):self.temp.cleanup()

    def bought(self,name,quantity=1):
        self.players.add_item('tester','home',name,quantity)
        return self.players.inventory_condition_items('tester',name)

    def test_bought_duplicate_weapon_keeps_selected_broken_body_on_reload(self):
        first,second=self.bought('Longsword',2)
        self.players.equip_item('tester','home','Longsword',item_id=second['id'])
        self.players.damage_item('tester','home','Longsword',999,item_id=second['id'])
        self.assertEqual(first['durability'],first['max_durability'])
        self.assertEqual(second['durability'],0)
        self.assertEqual(self.players.get_equipped_weapon('tester','home')['weapon_id'],'fists')
        self.assertFalse(self.players.item_capabilities('tester','Longsword')['equippable'])
        self.players.flush()
        final=Players(self.store)
        shared=final.get_player('tester')['shared']
        self.assertEqual(shared['equipment']['weapon']['id'],second['id'])
        self.assertEqual(final.get_equipped_weapon('tester','home')['weapon_id'],'fists')
        self.assertEqual(shared['inventory'].count('Longsword'),2)
        self.assertTrue(final.equip_item('tester','home','Longsword',item_id=first['id'])['success'])
        self.assertEqual(final.get_equipped_weapon('tester','home')['weapon_id'],'longsword')

    def test_wear_is_exact_weapon_and_all_worn_body_pieces_once(self):
        for name in ('Leather Armor','Warm Cloak','Boots','Longsword','Longbow',"Beastmaster's Charm"):
            self.bought(name)
        for name in ('Leather Armor','Warm Cloak','Boots','Longsword',"Beastmaster's Charm"):
            self.players.equip_item('tester','home',name)
        changes=self.players.wear_equipment('tester','home',weapon=True,armor=True)
        self.assertEqual({row['slot'] for row in changes},{'armor','cloak','boots','weapon','charm'})
        self.assertTrue(all(row['before']-row['after']==1 for row in changes))
        self.assertEqual(self.players.inventory_condition_items('tester','Longbow')[0]['durability'],60)
        charm=self.players.inventory_condition_items('tester',"Beastmaster's Charm")[0]
        self.assertEqual(charm['durability'],charm['max_durability']-1)

    def test_broken_armor_stays_owned_but_contributes_no_defense(self):
        armor=self.bought('Light Exo-Suit')[0]
        self.players.equip_item('tester','home','Light Exo-Suit',item_id=armor['id'])
        defense=self.players.get_equipment_bonuses('tester','home')['defense']
        self.assertGreater(defense,0)
        self.players.damage_item('tester','home','Light Exo-Suit',999,item_id=armor['id'])
        self.assertEqual(self.players.get_equipment_bonuses('tester','home')['defense'],0)
        self.assertIn('Light Exo-Suit',self.player['shared']['inventory'])
        self.assertEqual(self.player['shared']['equipment']['armor']['id'],armor['id'])

    def test_bought_cloth_repair_consumes_actual_skins_and_never_gold(self):
        cloak=self.bought('Warm Cloak')[0]
        cloak['durability']=0
        self.players.add_item('tester','home','Rabbit Skin',4)
        wallet=self.player['shared']['gold_cents']
        preview=self.players.item_capabilities('tester','Warm Cloak',item_id=cloak['id'])['repair']
        self.assertTrue(preview['available'],preview)
        self.assertEqual(preview['material_kind'],'cloth or animal skins')
        result=self.players.repair_item('tester','home','Warm Cloak',item_id=cloak['id'])
        self.assertTrue(result['success'],result)
        self.assertEqual(result['materials_spent'],{'Rabbit Skin':2})
        self.assertEqual(self.player['shared']['inventory'].count('Rabbit Skin'),2)
        self.assertEqual(self.player['shared']['gold_cents'],wallet)
        self.assertEqual(cloak['durability'],cloak['max_durability'])
        self.players.flush()
        final=Players(self.store)
        repaired=final.inventory_condition_items('tester','Warm Cloak')[0]
        self.assertEqual(repaired['id'],cloak['id'])
        self.assertEqual(repaired['durability'],repaired['max_durability'])

    def test_insufficient_repair_materials_never_partial_consume(self):
        cloak=self.bought('Warm Cloak')[0]
        cloak['durability']=0
        self.players.add_item('tester','home','Cloth',1)
        before=list(self.player['shared']['inventory'])
        result=self.players.repair_item('tester','home','Warm Cloak',item_id=cloak['id'])
        self.assertFalse(result['success'])
        self.assertIn('Missing repair materials',result['message'])
        self.assertEqual(before,self.player['shared']['inventory'])
        self.assertEqual(cloak['durability'],0)

    def test_crafted_robot_accepts_authored_recipe_parts_or_home_shinies(self):
        robot={'id':'robot-body','name':'Service Droid','type':'companion','recipe_id':'service_droid'}
        self.player['shared']['crafted_items'].append(robot)
        self.players.damage_item('tester','home','Service Droid',999,item_id='robot-body')
        self.assertEqual(robot['max_durability'],40)
        self.players.add_item('tester','home','brass button',3)
        self.players.add_item('tester','home','bottle cap',2)
        result=self.players.repair_item('tester','home','Service Droid',item_id='robot-body')
        self.assertTrue(result['success'],result)
        self.assertEqual(sum(result['materials_spent'].values()),5)
        self.assertEqual(robot['durability'],40)
        self.assertNotIn('brass button',self.player['shared']['inventory'])
        self.assertNotIn('bottle cap',self.player['shared']['inventory'])
        self.assertEqual(self.players.wear_equipment('tester','home',True,True),[])

    def test_repair_does_not_consume_other_gear_as_cloth_or_scrap(self):
        cloak=self.bought('Warm Cloak')[0]
        cloak['durability']=0
        self.bought('Leather Armor',4)
        result=self.players.repair_item('tester','home','Warm Cloak',item_id=cloak['id'])
        self.assertFalse(result['success'])
        self.assertEqual(len(self.players.inventory_condition_items('tester','Leather Armor')),4)

    def test_giving_exact_bought_body_preserves_wear_and_clears_old_equipment(self):
        first,second=self.bought('Longsword',2)
        second['durability']=7
        self.players.equip_item('tester','home','Longsword',item_id=second['id'])
        result=self.players.transfer_item('tester','friend','home','Longsword',item_id=second['id'])
        self.assertTrue(result['success'],result)
        self.assertEqual(result['item_ids'],[second['id']])
        self.assertIsNone(self.player['shared']['equipped']['weapon'])
        self.assertEqual(self.players.inventory_condition_items('tester','Longsword'),[first])
        self.assertEqual(self.players.inventory_condition_items('friend','Longsword')[0]['durability'],7)
        self.players.flush()
        final=Players(self.store)
        self.assertEqual(final.inventory_condition_items('friend','Longsword')[0]['id'],second['id'])
        self.assertEqual(final.inventory_condition_items('friend','Longsword')[0]['durability'],7)

    def test_general_removal_preserves_worn_copy_and_explicit_missing_id_never_substitutes(self):
        first,second=self.bought('Warm Cloak',2)
        second['durability']=8
        self.players.equip_item('tester','home','Warm Cloak',item_id=second['id'])
        self.assertTrue(self.players.remove_item('tester','home','Warm Cloak'))
        self.assertEqual(self.players.inventory_condition_items('tester','Warm Cloak'),[second])
        self.assertFalse(self.players.repair_item('tester','home','Warm Cloak',item_id=first['id'])['success'])
        self.assertFalse(self.players.equip_item('tester','home','Warm Cloak',item_id=first['id'])['success'])

    def test_rod_is_automatic_until_its_exact_body_breaks(self):
        rod=self.bought('Quality Fishing Rod')[0]
        self.assertTrue(self.players.item_capabilities('tester','Quality Fishing Rod',item_id=rod['id'])['automatic'])
        self.players.damage_item('tester','home','Quality Fishing Rod',999,item_id=rod['id'])
        self.assertFalse(self.players.item_capabilities('tester','Quality Fishing Rod',item_id=rod['id'])['automatic'])
        self.assertIn('Quality Fishing Rod',self.player['shared']['inventory'])

    def test_actual_hunt_wears_weapon_once_but_cooldown_and_no_ammo_do_not(self):
        sword=self.bought('Longsword')[0]
        self.players.equip_item('tester','home','Longsword',item_id=sword['id'])
        handler=MultiWorldQuestHandler(self.players)
        with patch('random.randint',side_effect=lambda a,b:min(20,b)):
            result=handler.handle_free_hunt('tester','Tester','heros_journey','deer','enchanted-forest')
        self.assertTrue(result['combat'],result)
        self.assertEqual(sword['durability'],sword['max_durability']-1)
        again=handler.handle_free_hunt('tester','Tester','heros_journey','deer','enchanted-forest')
        self.assertNotIn('combat',again)
        self.assertEqual(sword['durability'],sword['max_durability']-1)
        self.player['worlds']['heros_journey']['cooldowns']={}
        bow=self.bought('Longbow')[0]
        self.players.equip_item('tester','home','Longbow',item_id=bow['id'])
        refused=handler.handle_free_hunt('tester','Tester','heros_journey','deer','enchanted-forest')
        self.assertIn('arrows',refused['message'].lower())
        self.assertEqual(bow['durability'],bow['max_durability'])
        self.assertEqual(self.players._hunting_actors,set())
        self.assertEqual(self.players._combat_care_events,{})

    def test_dungeon_actual_weapon_roll_wears_but_refusal_and_spell_do_not(self):
        sword=self.bought('Longsword')[0]
        self.players.equip_item('tester','home','Longsword',item_id=sword['id'])
        self.players.create_party('tester','heros_journey')
        party=self.players.get_party('tester')
        party['current_dungeon']={'started_at':'fixed-run','room_index':0,'turn_order':['tester'],'turn_index':0,
            'active_room':{'room_id':'first','enemies':[{'enemy_id':'target','name':'Target','hp':1000,'max_hp':1000,
                'ac':1,'attack_bonus':-100,'damage_die':'1d4'}]},'rooms':[{},{}]}
        refused=self.players.attack_dungeon_target('tester','heros_journey','No target')
        self.assertFalse(refused['success'])
        self.assertEqual(sword['durability'],sword['max_durability'])
        with patch('random.randint',side_effect=lambda a,b:min(10,b)):
            attack=self.players.attack_dungeon_target('tester','heros_journey','Target')
            spell=self.players.attack_dungeon_target('tester','heros_journey','Target',use_spell=True)
        self.assertTrue(attack['success'])
        self.assertEqual(len(attack['item_wear']),1)
        self.assertEqual(sword['durability'],sword['max_durability']-1)
        self.assertEqual(spell['item_wear'],[])

    def test_gather_uses_one_unbroken_owned_tool_and_wears_only_on_roll(self):
        first,second=self.bought('Hatchet',2)
        first['durability']=0
        second['durability']=1
        handler=MultiWorldQuestHandler(self.players)
        with patch('random.randint',side_effect=lambda a,b:a):
            result=handler.handle_free_gather('tester','Tester','heros_journey','Oak Branch','enchanted-forest')
        self.assertEqual(result['rolls']['tool_bonus'],2)
        self.assertEqual(result['rolls']['tool_id'],second['id'])
        self.assertEqual(result['item_wear'][0]['after'],0)
        self.assertIn('broke',result['message'])
        retry=handler.handle_free_gather('tester','Tester','heros_journey','Oak Branch','enchanted-forest')
        self.assertNotIn('item_wear',retry)
        self.player['worlds']['heros_journey']['cooldowns']={}
        with patch('random.randint',side_effect=lambda a,b:min(10,b)):
            broken=handler.handle_free_gather('tester','Tester','heros_journey','Oak Branch','enchanted-forest')
        self.assertEqual(broken['rolls']['tool_bonus'],0)
        self.assertNotIn('item_wear',broken)

    def test_made_gathering_basket_also_works_and_wears_as_a_carried_tool(self):
        basket={'id':'basket','name':'Gathering Basket','recipe_id':'gathering_basket'}
        self.player['shared']['crafted_items'].append(basket)
        handler=MultiWorldQuestHandler(self.players)
        with patch('random.randint',side_effect=lambda a,b:min(10,b)):
            result=handler.handle_free_gather('tester','Tester','heros_journey','Healing Herb','enchanted-forest')
        self.assertEqual(result['rolls']['tool_bonus'],2)
        self.assertEqual(result['rolls']['tool_id'],'basket')
        self.assertEqual(result['item_wear'][0]['source'],'made')
        self.assertEqual(basket['durability'],basket['max_durability']-1)

    def test_spare_inventory_legacy_ref_does_not_switch_to_same_named_made_item(self):
        bought=self.bought('Warm Cloak')[0]
        bought['durability']=9
        self.player['shared']['crafted_items'].append({'id':'made','name':'Warm Cloak','durability':0,'max_durability':40})
        self.player['shared']['equipped']['cloak']='Warm Cloak'
        self.player['shared']['equipment']['cloak']={'source':'inventory','name':'Warm Cloak'}
        self.players._resolve_equipment('tester')
        self.assertEqual(self.player['shared']['equipment']['cloak']['id'],bought['id'])
        self.assertEqual(self.player['shared']['equipment']['cloak']['source'],'inventory')

    def test_native_basket_condition_copy_preserves_player_canonical_state(self):
        cloak=self.bought('Warm Cloak')[0]
        cloak['durability']=7
        self.players.equip_item('tester','home','Warm Cloak',item_id=cloak['id'])
        self.players.flush()
        baskets=self.store.house_read(self.store.world/'baskets.json',{})
        basket=baskets['tester']
        self.assertEqual(basket['inventory_item_conditions'][cloak['id']]['durability'],7)
        self.assertEqual(basket['inventory_equipment_refs']['cloak']['id'],cloak['id'])
        
        basket['inventory_item_conditions'][cloak['id']]['durability']=40
        self.store.house_write(self.store.world/'baskets.json',baskets)
        self.assertEqual(Players(self.store).inventory_condition_items('tester')[0]['durability'],7)

    def test_basket_only_condition_save_migrates_without_refreshing_wear(self):
        self.store.house_write(self.store.world/'baskets.json',{'newcomer':{
            'name':'Newcomer','picked':{'Warm Cloak':1},'made':[],
            'inventory_equipment':{'cloak':'Warm Cloak'},
            'inventory_equipment_refs':{'cloak':{'id':'native-cloak','name':'Warm Cloak','source':'inventory'}},
            'inventory_item_conditions':{'native-cloak':{'id':'native-cloak','name':'Warm Cloak','durability':0,'max_durability':40}}}})
        final=Players(self.store)
        row=final.inventory_condition_items('newcomer')[0]
        self.assertEqual(row['id'],'native-cloak')
        self.assertEqual(row['durability'],0)
        self.assertFalse(final.item_capabilities('newcomer','Warm Cloak')['equippable'])

if __name__=='__main__':unittest.main()
