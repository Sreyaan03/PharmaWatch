"""
reddit_scraper.py — Scrapes drug-related posts from Reddit
"""
import praw
import time
from database import insert_reddit_post, get_unprocessed_posts, mark_post_processed

# ── FILL THESE IN from Step 3.1 ──
REDDIT_CLIENT_ID = "YOUR_CLIENT_ID"
REDDIT_CLIENT_SECRET = "YOUR_CLIENT_SECRET"
REDDIT_USER_AGENT = "PharmaWatch:v1.0 (by u/YOUR_REDDIT_USERNAME)"

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
    return praw.Reddit(
        client_id=REDDIT_CLIENT_ID,
        client_secret=REDDIT_CLIENT_SECRET,
        user_agent=REDDIT_USER_AGENT,
    )

def scrape_subreddit(reddit, subreddit_name, limit=50):
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
    """Run unprocessed posts through BioBERT NER."""
    from database import insert_drug_event
    posts = get_unprocessed_posts(limit=100)
    print(f"[NER] Processing {len(posts)} posts...")
    for post in posts:
        text = f"{post['title']}. {post['body']}"
        try:
            entities = ner_func(text)
            drugs = [e for e in entities if e.get("entity_group") in
                    ("Medication", "Drug", "Clinical_event")]
            events = [e for e in entities if e.get("entity_group") in
                     ("Disease_disorder", "Sign_symptom")]
            for drug in drugs:
                for event in events:
                    insert_drug_event(drug["word"], event["word"], source="reddit",
                                    confidence=min(drug["score"], event["score"]),
                                    raw_text=text[:500])
            mark_post_processed(post["id"])
        except Exception as e:
            print(f"[NER] Error: {e}")

if __name__ == "__main__":
    print("=== Reddit Scraper ===")
    scrape_all(limit_per_sub=25)
