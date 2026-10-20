import random
from datetime import datetime
from typing import Dict, Optional

class Storyteller:
    EPIC_THEMES = ['the first fire and how it was stolen from the stars', 'the wanderer who walked between worlds', 'the spirit wolf who guards the canyon', 'the river that flows backward once every hundred years', 'the old gods who sleep beneath the desert', 'the woman who traded her shadow for a second chance', 'the last song of the thunder bird', 'the gambler who bet his name against the moon', 'why the coyote laughs at midnight', "the gunslinger who couldn't die", 'the town that vanished between sunset and sunrise', 'the price of speaking to the dead', 'the child born with fire in her veins', 'the canyon that remembers every footstep', 'the stranger who came from the place where the sun sets']
    TRANSITIONS = ['*The fire crackles, sending sparks into the darkness...*', '*Takoda stirs the embers, letting the silence breathe...*', '*The wind shifts. The flames lean closer...*', '*His eyes catch the firelight as he continues...*', '*A log settles in the fire. The story goes on...*', '*The stars wheel overhead. His voice drops lower...*', '*Somewhere in the distance, a coyote calls...*', "*The fire burns steady. Takoda's words flow like water...*"]
    EVALUATION_WEIGHTS = {'length': 0.3, 'creativity': 0.4, 'effort': 0.3}
    REWARD_TIERS = {'masterful': {'min_score': 85, 'cents': (75, 150)}, 'excellent': {'min_score': 70, 'cents': (40, 75)}, 'good': {'min_score': 50, 'cents': (15, 40)}, 'decent': {'min_score': 30, 'cents': (5, 15)}, 'none': {'min_score': 0, 'cents': (0, 0)}}

    def evaluate_player_story(self, story_text: str, player_name: str) -> Dict:
        """
        Secretly evaluate a player's story

        Returns evaluation data (NOT to be shown to player!)
        """
        length = len(story_text)
        if length < 50:
            length_score = 0
        elif length < 100:
            length_score = 20
        elif length < 200:
            length_score = 40
        elif length < 300:
            length_score = 60
        elif length < 400:
            length_score = 80
        else:
            length_score = 100
        effort_score = 0
        effort_indicators = [(story_text.count('.') >= 3, 15), (story_text.count(',') >= 2, 10), ('...' in story_text or '—' in story_text, 10), (any((c in story_text for c in '"\'')), 15), (len(story_text.split()) >= 50, 20), (story_text.count('\n') >= 1, 10), (any((word in story_text.lower() for word in ['once', 'long ago', 'there was', 'they say', 'legend', 'story'])), 20)]
        for condition, points in effort_indicators:
            if condition:
                effort_score += points
        effort_score = min(100, effort_score)
        creativity_score = 50
        creativity_indicators = [(len(set(story_text.lower().split())) / max(1, len(story_text.split())) > 0.6, 15), (any((word in story_text.lower() for word in ['fire', 'stars', 'moon', 'desert', 'wind', 'spirit', 'soul', 'shadow'])), 15), (story_text.count('*') >= 2, 10), (length > 200 and '?' not in story_text[:50], 10)]
        for condition, points in creativity_indicators:
            if condition:
                creativity_score += points
        creativity_score = min(100, creativity_score)
        final_score = length_score * self.EVALUATION_WEIGHTS['length'] + creativity_score * self.EVALUATION_WEIGHTS['creativity'] + effort_score * self.EVALUATION_WEIGHTS['effort']
        reward_tier = 'none'
        for tier, data in sorted(self.REWARD_TIERS.items(), key=lambda x: x[1]['min_score'], reverse=True):
            if final_score >= data['min_score']:
                reward_tier = tier
                break
        tier_data = self.REWARD_TIERS[reward_tier]
        reward_cents = random.randint(tier_data['cents'][0], tier_data['cents'][1]) if tier_data['cents'][1] > 0 else 0
        reward_dollars = reward_cents / 100.0
        return {'player': player_name, 'length_score': length_score, 'creativity_score': creativity_score, 'effort_score': effort_score, 'final_score': final_score, 'tier': reward_tier, 'reward_cents': reward_cents, 'reward_dollars': reward_dollars, 'story_length': length, 'evaluated_at': datetime.now().isoformat()}

    def get_takoda_response_to_story(self, evaluation: Dict) -> str:
        """
        Generate Takoda's response to a player's story

        The response hints at quality without revealing the reward system
        """
        tier = evaluation['tier']
        responses = {'masterful': ['*Takoda sits in silence for a long moment, then nods slowly.* The fire heard that one. It will remember.', '*His eyes reflect something ancient and approving.* That story has roots. Deep ones.', '*The flames lean toward you as if listening.* You carry something rare, traveler. Guard it well.', "*Takoda's rare smile crosses his weathered face.* The old ones would have welcomed you at their fires."], 'excellent': ['*The fire crackles warmly.* Good bones on that tale. It could grow into something greater.', '*He stirs the embers thoughtfully.* You have the voice for this. Keep speaking.', '*Takoda inclines his head.* The fire approves. So do I.', '*A log settles in the flames.* That one had weight. I felt it.'], 'good': ["*He nods once, slow and steady.* Not bad. The fire's still listening.", "*The flames flicker with interest.* There's something there. Keep finding it.", "*Takoda's eyes stay on the fire.* You're learning the rhythm. That's the hardest part."], 'decent': ['*He watches the smoke rise.* Every story starts somewhere. Yours just did.', '*The fire burns steady.* Keep at it. The words will come.', '*Takoda gives a small nod.* That took courage. Courage is a good beginning.'], 'none': ['*The fire crackles quietly.* The flames are patient. So am I.', '*He stirs the embers.* Sometimes stories need time to find themselves.', "*Takoda's eyes stay on the fire.* Sit a while longer. Listen."]}
        return random.choice(responses.get(tier, responses['none']))

    def should_give_reward(self, evaluation: Dict) -> bool:
        """Check if player earned a reward"""
        return evaluation['reward_cents'] > 0

    def get_reward_message(self, evaluation: Dict) -> Optional[str]:
        """
        Get Takoda's message when giving money for a story.

        He gives money openly, but the AMOUNT being tied to quality stays secret.
        Players know storytelling can earn money, but not that better = more.
        """
        if evaluation['reward_cents'] <= 0:
            return None
        amount = evaluation['reward_dollars'] * 2
        reward_messages = [f'*Takoda reaches into his worn pouch and sets some coins beside you.* Stories have worth. So do the ones who carry them. **+{amount:.2f} gold**', f'*Without a word, he slides a few coins across the dirt toward you.* For your time at the fire. **+{amount:.2f} gold**', f'*He pulls something from his vest and places it in your hand.* The fire remembers those who feed it. **+{amount:.2f} gold**', f'*Takoda nods once and tosses a small pouch your way.* Keep talking, traveler. **+{amount:.2f} gold**', f"*A weathered hand extends a few coins.* Words ain't free. Neither are good ones. **+{amount:.2f} gold**", f'*He stirs the fire, then reaches into his coat.* Here. For keeping an old man company. **+{amount:.2f} gold**']
        return random.choice(reward_messages)
