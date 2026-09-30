"""Controlled vocabularies. The AI may only return values from these lists."""

GENERAL_SUB_GENRE = "General"

GENRE_TAXONOMY: dict[str, list[str]] = {
    "Finance & Investment": [
        "Stock Market & Trading", "Options & Derivatives", "Personal Finance", "Mutual Funds & SIP",
        "Insurance", "Tax", "Crypto", "Banking & Credit", "Real Estate", "Economy & Market News",
    ],
    "Technology": ["AI", "Software", "Programming", "Gadgets & Reviews", "Cybersecurity", "Tech News"],
    "Education": ["Exam Preparation", "Career Guidance", "Science", "Language Learning", "Skill Development"],
    "Fitness": ["Workout & Training", "Yoga", "Nutrition & Diet", "Bodybuilding"],
    "Gaming": ["Gameplay & Streaming", "Esports", "Game Reviews", "Mobile Gaming"],
    "Fashion": ["Men's Fashion", "Women's Fashion", "Streetwear", "Styling Tips"],
    "Beauty": ["Makeup", "Skincare", "Haircare"],
    "Food": ["Recipes & Cooking", "Food Reviews", "Street Food"],
    "Travel": ["Travel Vlogs", "Travel Guides", "Budget Travel"],
    "Entertainment": ["Comedy", "Music", "Movies & TV", "Memes", "Vlogs"],
    "Business": ["Entrepreneurship", "Startups", "Marketing", "Sales", "E-commerce"],
    "News": ["Politics", "Current Affairs", "Business News"],
    "Lifestyle": ["Daily Vlogs", "Family & Parenting", "Home & Decor", "Productivity"],
    "Motivation": ["Self Improvement", "Spirituality", "Public Speaking"],
    "Sports": ["Cricket", "Football", "Fitness Sports", "Sports Analysis"],
    "Other": [],
}
for _subs in GENRE_TAXONOMY.values():
    _subs.append(GENERAL_SUB_GENRE)

GENRES: list[str] = list(GENRE_TAXONOMY)
SUB_GENRES: list[str] = sorted({sub for subs in GENRE_TAXONOMY.values() for sub in subs})

LANGUAGES: list[str] = [
    "English", "Hindi", "Hinglish", "Punjabi", "Tamil", "Telugu", "Bengali",
    "Marathi", "Gujarati", "Kannada", "Malayalam", "Other",
]
UNKNOWN = "Unknown"
NO_SECONDARY_LANGUAGE = "None"

SENTIMENTS: list[str] = ["Positive", "Neutral", "Negative"]
