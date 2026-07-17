from ddgs import DDGS
from ddgs.exceptions import DDGSException


MAX_RESULTS = 12
MAX_TEXT_RESULTS = 6


def search_images(query, max_results=MAX_RESULTS):
    try:
        results = DDGS().images(query, max_results=max_results, safesearch="moderate")
    except DDGSException as error:
        print("Image search failed:", type(error).__name__, error)
        return []

    return [
        {
            "title": result.get("title", ""),
            "image_url": result.get("image", ""),
            "thumbnail_url": result.get("thumbnail", ""),
            "source_url": result.get("url", ""),
        }
        for result in results
        if result.get("image")
    ]


def search_news(query="top news stories today", max_results=MAX_TEXT_RESULTS):
    try:
        results = DDGS().news(query, max_results=max_results, safesearch="moderate")
    except DDGSException as error:
        print("News search failed:", type(error).__name__, error)
        return []

    return [
        {
            "title": result.get("title", ""),
            "snippet": result.get("body", ""),
            "source": result.get("source", ""),
            "source_url": result.get("url", ""),
        }
        for result in results
        if result.get("title")
    ]


def search_text(query, max_results=MAX_TEXT_RESULTS):
    try:
        results = DDGS().text(query, max_results=max_results, safesearch="moderate")
    except DDGSException as error:
        print("Web search failed:", type(error).__name__, error)
        return []

    return [
        {
            "title": result.get("title", ""),
            "snippet": result.get("body", ""),
            "source_url": result.get("href", ""),
        }
        for result in results
        if result.get("href")
    ]


if __name__ == "__main__":
    import sys

    query = " ".join(sys.argv[1:]) or "dogs"

    print(f"--- text results for {query!r} ---")
    for item in search_text(query, max_results=3):
        print(item["title"])
        print(" ", item["source_url"])
        print(" ", item["snippet"])

    print(f"--- image results for {query!r} ---")
    for item in search_images(query, max_results=3):
        print(item["title"])
        print(" ", item["image_url"])
