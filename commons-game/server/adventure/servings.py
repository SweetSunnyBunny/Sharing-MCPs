
"""Finite portions for named inventory units without changing legacy quantities."""
from collections import Counter
import uuid

def food_units(shared, consumables):
    counts=Counter()
    for item in shared['inventory']:
        counts[item.get('name','') if isinstance(item,dict) else str(item)]+=item.get('quantity',1) if isinstance(item,dict) else 1
    saved=shared.setdefault('food_servings',{})
    for name in list(saved):
        if not counts.get(name):saved.pop(name)
    for name,count in counts.items():
        spec=consumables.resolve(name)
        if not spec or not spec.get('servings'):continue
        rows=saved.setdefault(name,[])
        if len(rows)>count:del rows[:len(rows)-count]
        for row in list(rows):
            row['uses_total']=max(1,min(1000,int(row.get('uses_total',spec['servings']))))
            row['uses_left']=max(0,min(row['uses_total'],int(row.get('uses_left',row['uses_total']))))
            if row['uses_left']==0:
                for index,item in enumerate(shared['inventory']):
                    label=item.get('name','') if isinstance(item,dict) else str(item)
                    if label==name:
                        if isinstance(item,dict) and item.get('quantity',1)>1:item['quantity']-=1
                        else:shared['inventory'].pop(index)
                        break
                rows.remove(row);count-=1
        while len(rows)<count:
            rows.append({'id':'food-'+uuid.uuid4().hex[:12],
                         'uses_total':spec['servings'],'uses_left':spec['servings']})
    return saved

def discard_empty_made(shared, consumables):
    kept=[]
    for item in shared['crafted_items']:
        spec=consumables.resolve(item.get('name',item.get('what','')),item)
        if spec and spec.get('servings'):
            if spec['remaining']<=0:continue
            item.update(uses_total=spec['servings'],uses_left=spec['remaining'])
        kept.append(item)
    shared['crafted_items'][:]=kept
