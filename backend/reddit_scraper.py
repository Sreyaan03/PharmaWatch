"""
reddit_scraper.py — Scrapes drug-related posts from Reddit
Note: praw is optional. If not installed, all functions return empty results.
"""
try:
    import praw
    PRAW_AVAILABLE = True
except ImportError:
    PRAW_AVAILABLE = False
import time
from core import config
from database import insert_reddit_post, get_unprocessed_posts, mark_post_processed

# Credentials come from the environment (core/config). With none set, social listening is disabled
# and the endpoints report "not configured" — there are no hard-coded placeholders in source.
def reddit_configured():
    return bool(PRAW_AVAILABLE and config.get("REDDIT_CLIENT_ID") and config.get("REDDIT_CLIENT_SECRET"))

# Subreddits with drug/health discussions
TARGET_SUBREDDITS = [
    "medicine", "pharmacy", "AskDocs", "ChronicPain",
    "Nootropics", "antidepressants", "diabetes", "migraine",
]

DRUG_KEYWORDS = [
    "side effect", "adverse", "reaction", "nausea", "dizziness",
    "taking", "prescribed", "medication", "drug", "withdrawal",
    "interaction", "rash", "pain", "metformin", "warfarin",
    "aspirin", "ibuprofen", "sertraline", "gabapentin",
]

def create_reddit_client():
    if not reddit_configured():
        return None
    return praw.Reddit(
        client_id=config.get("REDDIT_CLIENT_ID"),
        client_secret=config.get("REDDIT_CLIENT_SECRET"),
        user_agent=config.get("REDDIT_USER_AGENT"),
    )

def scrape_subreddit(reddit, subreddit_name, limit=50):
    if not PRAW_AVAILABLE or reddit is None:
        return 0
    count = 0
    try:
        subreddit = reddit.subreddit(subreddit_name)
        for post in subreddit.new(limit=limit):
            text = f"{post.title} {post.selftext}".lower()
            if not any(kw in text for kw in DRUG_KEYWORDS):
                continue
            insert_reddit_post(post.id, subreddit_name, post.title,
                              post.selftext[:2000], post.score)
            count += 1
    except Exception as e:
        print(f"[Reddit] Error scraping r/{subreddit_name}: {e}")
    return count

def scrape_all(limit_per_sub=50):
    if not reddit_configured():
        print("[Reddit] not configured (set REDDIT_CLIENT_ID/SECRET) — social listening disabled.")
        return 0
    reddit = create_reddit_client()
    total = 0
    for sub in TARGET_SUBREDDITS:
        n = scrape_subreddit(reddit, sub, limit=limit_per_sub)
        total += n
        print(f"[Reddit] r/{sub}: {n} posts saved")
        time.sleep(1)
    print(f"[Reddit] Total: {total} posts scraped")
    return total

def process_posts_with_ner(ner_func):
    """Run unprocessed posts through the biomedical NER model, forming mentions within a sentence
    (negation handled) — the same text-mining path as clinical notes. Exploratory and unverified;
    stored with source `text_mining`, never a signal and never in a 2x2 count."""
    from database import insert_drug_event
    from services import text_mining
    posts = get_unprocessed_posts(limit=100)
    print(f"[NER] Processing {len(posts)} posts...")
    for post in posts:
        text = f"{post['title']}. {post['body']}"
        try:
            entities = ner_func(text)
            # ner_func may return 'entity_group' (raw pipeline) or 'label' (cleaned); normalise.
            for e in entities:
                e.setdefault("label", e.get("entity_group"))
            for p in text_mining.pair_mentions(text, entities)[:20]:
                insert_drug_event(p["drug"], p["event"], source="text_mining",
                                  confidence=p["confidence"], raw_text=text[:500])
            mark_post_processed(post["id"])
        except Exception as e:
            print(f"[NER] Error: {e}")

if __name__ == "__main__":
    print("=== Reddit Scraper ===")
    scrape_all(limit_per_sub=25)
