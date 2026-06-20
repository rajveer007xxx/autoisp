"""
_S40zμ_  Shared profanity word list (Roman + Devanagari)
─────────────────────────────────────────────────────────────────────
Used by both the client-side guard (profanity-shield.js) and the
server-side middleware (profanity_filter.py) so the two stay in sync.

Roman words match with \\b word-boundaries so they don't false-positive
on substrings (e.g. "rand" won't match "random" or "Brand").
Devanagari words use Unicode-aware lookarounds.
"""

# Roman / Hinglish (case-insensitive, word-boundary matched)
ROMAN_WORDS = [
    "aad", "aand",
    "bahenchod", "behenchod", "bhenchod", "bhenchodd",
    "b.c.", "bc",
    "bakchod", "bakchodd", "bakchodi",
    "bevda", "bewda", "bevdey", "bewday",
    "bevakoof", "bevkoof", "bevkuf", "bewakoof", "bewkoof", "bewkuf",
    "bhadua", "bhaduaa", "bhadva", "bhadvaa", "bhadwa", "bhadwaa",
    "bhosada", "bhosda", "bhosdaa",
    "bhosdike", "bhonsdike", "bsdk", "b.s.d.k",
    "bhosdiki", "bhosdiwala", "bhosdiwale",
    "bhosadchodal", "bhosadchod",
    "babbe", "babbey", "bube", "bubey",
    "bur", "burr", "buurr", "buur",
    "charsi",
    "chooche", "choochi", "chuchi",
    "chhod", "chod", "chodd",
    "chudne", "chudney", "chudwa", "chudwaa", "chudwane", "chudwaane",
    "choot", "chut", "chute", "chutia", "chutiya", "chutiye",
    "chuttad", "chutad",
    "dalaal", "dalal", "dalle", "dalley",
    "fattu",
    "gadha", "gadhe", "gadhalund",
    "gaand", "gand", "gandu", "gandfat", "gandfut", "gandiya", "gandiye",
    "goo", "gu",
    "gote", "gotey", "gotte",
    "hag", "haggu", "hagne", "hagney",
    "harami", "haramjada", "haraamjaada", "haramzyada", "haraamzyaada",
    "haraamjaade", "haraamzaade", "haraamkhor", "haramkhor",
    "jhat", "jhaat", "jhaatu", "jhatu",
    "kutta", "kutte", "kuttey",
    "kutia", "kutiya", "kuttiya", "kutti",
    "landi", "landy",
    "laude", "laudey", "laura", "lora", "lauda",
    "ling", "loda", "lode", "lund",
    "launda", "lounde", "laundey",
    "laundi", "loundi", "laundiya", "loundiya",
    "lulli",
    "maar", "maro", "marunga",
    "madarchod", "madarchodd", "madarchood", "madarchoot", "madarchut",
    "m.c.", "mc",
    "mamme", "mammey",
    "moot", "mut", "mootne", "mutne", "mooth", "muth",
    "nunni", "nunnu",
    "paaji", "paji",
    "pesaab", "pesab", "peshaab", "peshab",
    "pilla", "pillay", "pille", "pilley",
    "pisaab", "pisab",
    "pkmkb", "porkistan",
    "raand", "rand", "randi", "randy",
    "suar",
    "tatte", "tatti", "tatty",
    "ullu",
]

# Devanagari script
DEVA_WORDS = [
    "आंड़", "आंड", "आँड",
    "बहनचोद", "बेहेनचोद", "भेनचोद",
    "बकचोद", "बकचोदी",
    "बेवड़ा", "बेवड़े",
    "बेवकूफ",
    "भड़ुआ", "भड़वा",
    "भोसड़ा", "भोसड़ीके", "भोसड़ीकी", "भोसड़ीवाला", "भोसड़ीवाले",
    "भोसरचोदल", "भोसदचोद", "भोसड़ाचोदल", "भोसड़ाचोद",
    "बब्बे", "बूबे",
    "बुर",
    "चरसी",
    "चूचे", "चूची", "चुची",
    "चोद",
    "चुदने", "चुदवा", "चुदवाने",
    "चूत", "चूतिया", "चुटिया", "चूतिये",
    "चुत्तड़", "चूत्तड़",
    "दलाल", "दलले",
    "फट्टू",
    "गधा", "गधे", "गधालंड",
    "गांड", "गांडू", "गंडफट", "गंडिया", "गंडिये",
    "गू",
    "गोटे",
    "हग", "हग्गू", "हगने",
    "हरामी", "हरामजादा", "हरामज़ादा", "हरामजादे", "हरामज़ादे",
    "हरामखोर",
    "झाट", "झाटू",
    "कुत्ता", "कुत्ते",
    "कुतिया", "कुत्ती",
    "लेंडी",
    "लोड़े", "लौड़े", "लौड़ा", "लोड़ा", "लौडा",
    "लिंग", "लोडा", "लोडे", "लंड",
    "लौंडा", "लौंडे", "लौंडी", "लौंडिया",
    "लुल्ली",
    "मार", "मारो", "मारूंगा",
    "मादरचोद", "मादरचूत", "मादरचुत",
    "मम्मे",
    "मूत", "मुत", "मूतने", "मुतने", "मूठ", "मुठ",
    "नुननी", "नुननु",
    "पाजी",
    "पेसाब", "पेशाब",
    "पिल्ला", "पिल्ले",
    "पिसाब",
    "पोरकिस्तान",
    "रांड", "रंडी",
    "सुअर", "सूअर",
    "टट्टे", "टट्टी",
    "उल्लू",
]

# Deduplicate just in case
ROMAN_WORDS = sorted(set(w.lower() for w in ROMAN_WORDS))
DEVA_WORDS  = sorted(set(DEVA_WORDS))


# _S40zπ_PROF_PLUS_  Masked / vowel-omitted / X-substituted forms
# Added 2026-05-09 after the 'RAAANDIYO KA KHANA' / 'CHXXT' bypass report.
ROMAN_WORDS_MASKED = [
    # Madarchod skeletons
    "madrchd", "mdrchd", "mdarchd", "mdrchood", "mdrchud",
    "madarchood", "madarchd", "madrchood",
    # Behenchod skeletons
    "bchnchd", "behnchd", "bhnchd", "bhenchd", "bhanchd",
    # Bhosdike skeletons
    "bhosdk", "bhsdk", "bhosadk", "bhosdyke",
    # Chutiya skeletons / X-masked
    "chtya", "chootia", "chootiya", "chutya",
    "chxt", "chxxt", "chxxxt", "chooot", "chooth",
    # Gaandu / Gandu skeletons
    "gndu", "gandoo", "gaandoo", "gaandu",
    # BKL
    "bkl", "bhkl", "bklod",
    # Bhadwa skeleton
    "bhdw", "bhdwa", "bhdwaa",
    # Lavda / Lund skeletons
    "lvd", "lvda", "lawda", "lawdaa", "loda", "lode", "lund", "lawde",
    # Randi / Raandi elongations (collapsed to 'randi' by normaliser anyway)
    "rndi", "raandi", "rendi", "ranndy",
    # Chinaal / Chinal
    "chinal", "chinaal", "chinal",
    # Maa-ki-X coded forms
    "mkc", "mkb", "mkbk",
    # Generic English+Hindi explicit words sometimes masked
    "fk", "fck", "fuk", "fuq", "fck",
    "mthrfkr", "mthrfckr",
    "asshl", "asshle",
]

# Words that previously caused FALSE POSITIVES due to being too short/common.
# We *remove* these from active matching — short common substrings should not
# block legitimate names like "Burr Industrial Area" / "Random ONU".
ROMAN_WORDS_REMOVE = [
    "rand",     # blocks "Random", "Brand"
    "burr", "buurr", "buur", "bur",  # blocks "Burr Industrial Area"
    "gu",       # too short
    "goo",      # blocks "Google", "good"
    "hag",      # blocks "Haggle"
    "moot", "mut", "mooth", "muth",  # English words
    "maro",     # common Italian/Spanish surname / Hindi imperative
    "mar",      # too short
]
