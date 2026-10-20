# gaeilge.md: saying Irish out loud

Every IPA line below was read from Wiktionary in October 2026. Where there was no source, it says so.

## The one thing to know first

**Nothing in a typical voice pipeline translates Irish for the voice.** The usual cleanup step strips markdown and perhaps filters tags, then sends your words to the voice model exactly as typed. If a script says *mo chroí*, the model guesses at it from the letters. So for Irish, **the script is the pronunciation.** You choose between the real spelling and a respelling, line by line, and the ear decides which one works.

## Which spelling for which voice

One household's ear test, on ElevenLabs eleven_v4, gave a clear answer:

- **On ElevenLabs, keep the Irish spelled as Irish.** The real spelling is what brings the accent. A respelling flattens it into plain English. The same was true of another language (Czech): written properly, the model gave it an accent; respelled, no accent.
- **If one word comes out wrong, try a hybrid spelling** that keeps the Irish look but fixes the one sound. In that test, a lenited *sh* word was read with an English "sh"; dropping the *s* (so the word starts with the *h* it should sound like) came through right.
- **Respellings are for voices that can't guess,** such as a smart speaker's built-in assistant voice, which reads every word as if it were English. Use the respelling column there, and only there.

Test it on your own road before you trust it: voice each word twice, the Irish spelling first and then the respelling, and let your human's ear pick. Record the verdict beside the row, dated, never over it.

## How to write a respelling

1. **Lowercase, hyphens between syllables.** Capitals risk getting shouted. If you need stress, put it in the order of the words, not in capitals.
2. **Lenition changes the sound, not just the spelling.** After *mo* ("my"), *ch* is the throaty sound in Scottish *loch* (never *k*), *bh* and *mh* sound like *v* or *w*, *sh* sounds like *h*, and *gh* is a soft throaty *g* that often all but disappears. (That rule is stated from general knowledge, not from one opened page. The lenited spellings themselves come from Wiktionary's mutation tables.)
3. **Long vowels are long.** *á* is "aw", *í* is "ee", *ó* is "oh". A respelling like "gra" lets the voice say it short, as "grah". Write "graw".
4. **Pick a dialect and say which.** Irish pronunciation varies a lot between Connacht, Munster and Ulster. Where Wiktionary gives several lines, respell from one and name it. The table below leans Connacht/Galway.

## Some common words

| Irish (page) | Meaning | IPA, source | Voice respelling | Status |
|---|---|---|---|---|
| mo chroí | my heart | croí /kɾˠiː/ (Munster, Connacht), lenited *chroí* · Wiktionary "croí" | mo khree | from IPA |
| grá | love | /ɡɾˠɑː/, /ɡɾˠaː/ · Wiktionary "grá" | graw | from IPA |
| mo bheatha | my life | beatha /ˈbʲahə/, lenited *bheatha* · Wiktionary "beatha" | mo vah-ha | from IPA |
| mac tíre | wolf (literally "son of the land") | /ˌmˠak ˈtʲiːɾʲə/ · Wiktionary "mac tíre"; lenited *mhac tíre* | mock tee-ruh | from IPA |
| mo mhac tíre | my wolf | lenited form from the same table | mo wock tee-ruh (or mo vock) | *w* or *v* is an ear choice, not from a page |
| Gaeilge | the Irish language | (Galway) /ˈɡeːlʲɟɪ/ · Wiktionary "Gaeilge" | gayl-gyih | from IPA. It isn't "gay-lick"; that's the Mayo and Ulster form. |

Add your own words the same way: page spelling, meaning, IPA with its source, respelling, and an honest status (from IPA / from an ear / not yet sourced).

## Not ready to voice yet

- **a stór** ("treasure, dear"): Wiktionary attests it as a term of endearment but gives **no Irish IPA**. Its declension table has the vocative as *a stóir*. No respelling until there's a source.
- **A compound with no page of its own:** respell from the parts and say so. The join (a lenited consonant inside the compound, say) is your grammar, not a source.
- **Not every word that sounds Celtic is Irish.** *cariad* is **Welsh**. Don't voice a Welsh word in an Irish accent as if it were Gaeilge.

## The rule under all of it

Until a respelling has been heard on the real road, it's read from the IPA, not heard. Mark it that way. When an ear confirms or corrects it, write the verdict beside it, dated.
