# File: test_wordlists.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-06
"""The blocked-word lists in every language: they find real swearing, and leave ordinary gig chat alone."""
import pytest

from rockconnect import review, wordlists

CORPUS = {
    "en": ["I am going to the concert in Dublin with my friend on Friday night", "The band plays at eight, doors open at seven. Anyone driving from Cork?",
           "What a great gig last night, the guitar solo was amazing and the crowd was loud", "Has anyone got a spare ticket for the sold out show at the arena?",
           "Looking for a drummer and a bass player to jam this weekend in the city centre", "Thanks for the lift, see you at the front row, bring earplugs",
           "Anna and Tom are coming too, the venue has a nice bar and good sound", "I will be there at nine, it was my first time at this club, the beer is cheap"],
    "it": ["Ci vediamo al concerto venerdì sera, porto io la macchina da Milano", "Che bel gruppo, il batterista era fantastico e il pubblico cantava tutto",
           "Cerco un biglietto per il concerto di sabato a Roma, qualcuno ne ha uno in più?", "Il cantante ha battuto il tempo con il piede, serata bellissima",
           "La città di Troia in Puglia ha un bel festival estivo di musica", "Sono arrivato presto, il locale è piccolo ma il suono è ottimo e le birre costano poco",
           "Una balla di fieno sul palco e un cono gelato per tutti, che serata"],
    "es": ["Nos vemos en el concierto el viernes, llevo yo el coche desde Madrid", "La banda toca a las ocho, ¿alguien tiene una entrada de más para el sábado?",
           "Qué buen concierto, el guitarrista estuvo increíble y el público cantó todo", "La zorra del cuento y el cono de helado, qué noche más divertida en la sala"],
    "fr": ["On se retrouve au concert vendredi soir, je viens en voiture depuis Paris", "Le groupe joue à vingt heures, quelqu'un a une place en plus pour samedi ?",
           "Quelle belle soirée, le batteur était formidable et le public chantait tout", "Je cherche un bassiste pour jouer ce week-end dans le centre ville"],
    "de": ["Wir sehen uns am Freitag beim Konzert, ich fahre von Berlin mit dem Auto", "Die Band spielt um acht Uhr, hat jemand eine Karte übrig für Samstag?",
           "Was für ein toller Abend, der Schlagzeuger war großartig und das Publikum sang alles mit", "Ich suche einen Bassisten für das Wochenende in der Stadt"],
    "pt": ["Vejo vocês no show na sexta à noite, levo o carro desde Lisboa", "A banda toca às oito, alguém tem um bilhete a mais para sábado?",
           "Que noite incrível, o baterista foi fantástico e o público cantou tudo", "Ele pede para tocar baixo e eu peço uma cerveja bem gelada"],
    "nl": ["Tot vrijdag op het concert, ik rijd met de auto vanuit Amsterdam", "De band speelt om acht uur, heeft iemand een extra kaartje voor zaterdag?",
           "Wat een geweldige avond, de drummer was fantastisch en het publiek zong mee", "Anita en Jan komen ook, we eten aardappels voor het concert"],
    "ru": ["Увидимся на концерте в пятницу вечером, я приеду на машине из Москвы", "Группа играет в восемь, у кого-нибудь есть лишний билет на субботу?",
           "Какой отличный вечер, барабанщик был великолепен, а публика пела всё"],
    "pl": ["Do zobaczenia na koncercie w piątek wieczorem, przyjadę samochodem z Warszawy", "Zespół gra o ósmej, czy ktoś ma dodatkowy bilet na sobotę?",
           "Co za wspaniały wieczór, perkusista był świetny i publiczność śpiewała wszystko"],
    "tr": ["Cuma akşamı konserde görüşürüz, İstanbul'dan arabayla geliyorum", "Grup saat sekizde çalıyor, cumartesi için fazla biletiniz var mı?",
           "Harika bir akşamdı, davulcu muhteşemdi ve herkes şarkı söyledi", "Ben bu konsere gidiyorum, sen de geliyor musun, am ve arkadaşlar"],
    "sv": ["Vi ses på konserten på fredag kväll, jag kör bil från Stockholm", "Bandet spelar klockan åtta, har någon en extra biljett till lördag?"],
    "da": ["Vi ses til koncerten fredag aften, jeg kører i bil fra København", "Bandet spiller klokken otte, har nogen en ekstra billet til lørdag?"],
    "no": ["Vi sees på konserten fredag kveld, jeg kjører bil fra Oslo", "Bandet spiller klokken åtte, har noen en ekstra billett til lørdag?"],
    "fi": ["Nähdään konsertissa perjantai-iltana, ajan autolla Helsingistä", "Bändi soittaa kahdeksalta, onko kellään ylimääräistä lippua lauantaiksi?"],
    "hu": ["Péntek este találkozunk a koncerten, autóval jövök Budapestről", "A zenekar nyolckor játszik, van valakinek felesleges jegye szombatra?"],
    "cs": ["Uvidíme se v pátek večer na koncertě, přijedu autem z Prahy", "Kapela hraje v osm, má někdo navíc lístek na sobotu?"],
    "ja": ["金曜日の夜のコンサートで会いましょう、東京から車で行きます", "バンドは八時に演奏します、土曜日のチケットが余っている人はいますか", "素晴らしい夜でした、ドラマーは最高でした"],
    "ko": ["금요일 밤 콘서트에서 만나요, 서울에서 차로 갈게요", "밴드는 여덟 시에 연주합니다, 토요일 표 남는 분 계신가요", "정말 멋진 밤이었어요, 드러머가 최고였어요"],
    "zh": ["周五晚上音乐会见，我开车从北京过来", "乐队八点演出，有人多余的周六票吗", "今晚太棒了，鼓手非常出色，13点见", "我喜欢这个乐队的吉他手，还有乳制品店旁边的酒吧"],
    "th": ["เจอกันที่คอนเสิร์ตคืนวันศุกร์ ฉันจะขับรถมาจากกรุงเทพ", "วงดนตรีเล่นตอนสองทุ่ม มีใครมีตั๋วเหลือสำหรับวันเสาร์ไหม"],
    "ar": ["نلتقي في الحفلة مساء الجمعة وسآتي بالسيارة من القاهرة", "الفرقة تعزف في الثامنة هل لدى أحد تذكرة زائدة ليوم السبت"],
    "hi": ["शुक्रवार शाम को कॉन्सर्ट में मिलते हैं, मैं दिल्ली से कार से आऊँगा", "बैंड आठ बजे बजाएगा, क्या किसी के पास शनिवार का अतिरिक्त टिकट है"],
    "fa": ["جمعه شب در کنسرت می‌بینمت، با ماشین از تهران می‌آیم", "گروه ساعت هشت می‌نوازد، کسی بلیط اضافه برای شنبه دارد؟"],
    "fil": ["Magkita tayo sa konsyerto sa Biyernes ng gabi, magmamaneho ako mula Maynila", "Tumutugtog ang banda ng alas-otso, may sobrang ticket ba kayo para sa Sabado?"],
}



ALL = [code for code, *_ in wordlists.languages()]


def matches(text, code=None):
    """Does any entry (of one list, or of all) match this text? The same steps as review.blocked_word_in, without a database."""
    folded = review.fold(text)
    versions = [folded, folded.translate(review.LEET)]
    for each in ([code] if code else ALL):
        for entry in wordlists.entries(each):
            word = review.clean_word(entry)
            if len(word) >= 2 and any(review._pattern(word).search(v) for v in versions):
                return entry
    return None


def test_there_are_lists_for_many_languages():
    assert len(ALL) >= 24 and {"it", "en", "es", "fr", "de", "pt", "ru", "zh", "ja", "ko", "ar", "tr", "nl", "pl"} <= set(ALL)
    kinds = {code: kind for code, _, _, kind in wordlists.languages()}
    assert kinds["it"] == kinds["ru"] == "curated" and kinds["tr"] == kinds["zh"] == "broad"
    assert all(n >= 10 for _, _, n, _ in wordlists.languages())
    assert wordlists.name("ru") == "Russian" and wordlists.name("tr") == "Turkish" and wordlists.name("zz") == "zz"
    assert wordlists.has("ja") and not wordlists.has("klingon")


@pytest.mark.parametrize("lang", sorted(CORPUS))
def test_ordinary_gig_chat_is_never_held_by_any_list(lang):
    for sentence in CORPUS[lang]:
        assert matches(sentence) is None, (lang, sentence, matches(sentence))


@pytest.mark.parametrize("code", ALL)
def test_every_list_catches_its_own_words_inside_a_sentence(code):
    entries = wordlists.entries(code)
    assert entries
    for entry in entries[:15]:
        text = ("what a day %s and then home" % entry) if not wordlists.is_spaceless(entry) else ("今天真是%s然后回家" % entry)
        assert matches(text, code), (code, entry)


def test_curated_lists_in_other_scripts_work_with_inflected_text():
    assert matches("ну ты и мудак, иди нахуй!", "ru") and matches("ПОШЕЛ НАХУЙ", "ru") and matches("Какая блядь", "ru")
    assert matches("это хуйня полная", "ru") and matches("хуеплёт", "ru") is None          # stems only as listed words
    assert matches("Привет, как дела?", "ru") is None
    assert matches("ты хуй", "ru") and matches("Ты ХУЙ!!!", "ru") and matches("пиздец какой-то", "ru")


def test_languages_without_spaces_are_found_anywhere_in_the_text():
    zh = next(e for e in wordlists.entries("zh") if len(e) >= 2)
    ko = next(e for e in wordlists.entries("ko") if len(e) >= 3)
    th = next(e for e in wordlists.entries("th") if len(e) >= 3)
    assert matches("我今天说了" + zh + "然后回家", "zh") and matches("그 사람은 " + ko + "가 아니다", "ko") and matches("ฉันพูดว่า" + th + "เมื่อวาน", "th")
    assert review._pattern(review.clean_word(zh)).pattern.startswith("(?<!") is False


def test_japanese_marks_are_kept_so_different_words_stay_different():
    assert review.fold("ば") != review.fold("は") and review.fold("ぱ") != review.fold("は")
    assert review.fold("Café") == "cafe" and review.fold("ÉCOLE") == "ecole"


def test_short_entries_are_dropped_so_they_cannot_hold_everyday_words():
    assert not wordlists._usable("am") and not wordlists._usable("gol") and wordlists._usable("chuj")
    assert wordlists._usable("مصه") and not wordlists._usable("مص") and wordlists._usable("他妈") and not wordlists._usable("a")
    assert all(len(review.clean_word(e)) >= 2 for code in ALL for e in wordlists.entries(code))
    assert "am" not in {review.clean_word(e) for e in wordlists.entries("tr")}


def test_the_open_lists_come_with_their_licence():
    import os
    folder = wordlists.DATA_DIR
    assert "Attribution 4.0 International" in open(os.path.join(folder, "LICENSE.txt"), encoding="utf-8").read(400)
    for code in wordlists.BROAD:
        assert os.path.isfile(os.path.join(folder, code + ".txt")), code
