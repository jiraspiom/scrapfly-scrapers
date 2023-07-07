"""
This is an example web scraper for Amazon.com used in scrapfly blog article:
https://SCRAPFLY.io/blog/how-to-scrape-amazon/

To run this scraper set env variable $SCRAPFLY_KEY with your scrapfly API key:
$ export $SCRAPFLY_KEY="your key from https://SCRAPFLY.io/dashboard"
"""
import json
import math
import os
import re
from typing import Dict, List, TypedDict, Optional
from urllib.parse import urljoin, urlparse, parse_qsl, urlencode, urlunparse

from loguru import logger as log
from scrapfly import ScrapeApiResponse, ScrapeConfig, ScrapflyClient

SCRAPFLY = ScrapflyClient(key=os.environ["SCRAPFLY_KEY"])
BASE_CONFIG = {
    # Amazon.com requires Anti Scraping Protection bypass feature.
    # for more: https://SCRAPFLY.io/docs/scrape-api/anti-scraping-protection
    "asp": True,
    # to change region see change the country code
    "country": "US",
}


def _add_or_replace_url_parameters(url: str, **params):
    """adds url parameters or replaces them with new values"""
    parsed_url = urlparse(url)
    query_params = dict(parse_qsl(parsed_url.query))
    query_params.update(params)
    updated_url = parsed_url._replace(query=urlencode(query_params))
    return urlunparse(updated_url)


class ProductPreview(TypedDict):
    """result generated by search scraper"""

    url: str
    title: str
    price: str
    real_price: str
    rating: str
    rating_count: str


def parse_search(result: ScrapeApiResponse) -> List[ProductPreview]:
    """Parse search result page for product previews"""
    previews = []
    product_boxes = result.selector.css("div.s-result-item[data-component-type=s-search-result]")
    for box in product_boxes:
        url = urljoin(result.context["url"], box.css("h2>a::attr(href)").get()).split("?")[0]
        if "/slredirect/" in url:  # skip ads etc.
            continue
        rating = box.css("span[aria-label~=stars]::attr(aria-label)").re_first(r"(\d+\.*\d*) out")
        rating_count = box.css("span[aria-label~=stars] + span::attr(aria-label)").get()
        previews.append(
            {
                "url": url,
                "title": box.css("h2>a>span::text").get(),
                # big price text is discounted price
                "price": box.css(".a-price[data-a-size=xl] .a-offscreen::text").get(),
                # small price text is "real" price
                "real_price": box.css(".a-price[data-a-size=b] .a-offscreen::text").get(),
                "rating": float(rating) if rating else None,
                "rating_count": int(rating_count.replace(',','')) if rating_count else None,
            }
        )
    log.info(f"parsed {len(previews)} product previews from search page {result.context['url']}")
    return previews


async def scrape_search(url: str, max_pages: Optional[int] = None) -> List[ProductPreview]:
    """Scrape amazon search pages product previews"""
    log.info(f"{url}: scraping first page")

    # first, scrape the first page and find total pages:
    first_result = await SCRAPFLY.async_scrape(ScrapeConfig(url, **BASE_CONFIG))
    results = parse_search(first_result)
    _paging_meta = first_result.selector.css("[cel_widget_id=UPPER-RESULT_INFO_BAR-0] span::text").get()
    _total_results = int(re.findall(r"(\d+) results", _paging_meta)[0])
    _results_per_page = int(re.findall(r"\d+-(\d+)", _paging_meta)[0])
    total_pages = math.ceil(_total_results / _results_per_page)
    if max_pages and total_pages > max_pages:
        total_pages = max_pages

    # now we can scrape remaining pages concurrently
    log.info(f"{url}: found {total_pages}, scraping them concurrently")
    other_pages = [
        ScrapeConfig(
            _add_or_replace_url_parameters(first_result.context["url"], page=page), 
            **BASE_CONFIG
        )
        for page in range(2, total_pages + 1)
    ]
    async for result in SCRAPFLY.concurrent_scrape(other_pages):
        results.extend(parse_search(result))

    log.info(f"{url}: found total of {len(results)} product previews")
    return results


class Review(TypedDict):
    title: str
    text: str
    location_and_date: str
    verified: bool
    rating: float


def parse_reviews(result: ScrapeApiResponse) -> List[Review]:
    """parse review from single review page"""
    review_boxes = result.selector.css("#cm_cr-review_list div.review")
    parsed = []
    for box in review_boxes:
        rating = box.css("*[data-hook*=review-star-rating] ::text").re_first(r"(\d+\.*\d*) out")
        parsed.append(
            {
                "text": "".join(box.css("span[data-hook=review-body] ::text").getall()).strip(),
                "title": box.css("*[data-hook=review-title]>span::text").get(),
                "location_and_date": box.css("span[data-hook=review-date] ::text").get(),
                "verified": bool(box.css("span[data-hook=avp-badge] ::text").get()),
                "rating": float(rating) if rating else None,
            }
        )
    return parsed


async def scrape_reviews(url: str, max_pages: Optional[int] = None) -> List[Review]:
    """scrape product reviews of a given URL of an amazon product"""
    if max_pages > 10:
        raise ValueError("max_pages cannot be greater than 10 as Amazon paging stops at 10 pages. Try splitting search through multiple filters and sorting to get more results")
    url = url.split("/ref=")[0]
    url = _add_or_replace_url_parameters(url, pageSize=20)  # Amazon.com allows max 20 reviews per page
    asin = url.split("/product-reviews/")[1].split("/")[0]
    # scrape first review page
    log.info(f"scraping review page: {url}")
    first_page_result = await SCRAPFLY.async_scrape(ScrapeConfig(url, **BASE_CONFIG))
    reviews = parse_reviews(first_page_result)

    # find total reviews
    total_reviews = first_page_result.selector.css("div[data-hook=cr-filter-info-review-rating-count] ::text").re(
        r"(\d+,*\d*)"
    )[1]
    total_reviews = int(total_reviews.replace(",", ""))
    _reviews_per_page = len(reviews)

    total_pages = int(math.ceil(total_reviews / _reviews_per_page))
    if max_pages and total_pages > max_pages:
        total_pages = max_pages

    log.info(f"found total {total_reviews} reviews across {total_pages} pages -> scraping")
    other_pages = []
    for page in range(2, total_pages + 1):
        url = f"https://www.amazon.com/product-reviews/{asin}/ref=cm_cr_getr_d_paging_btm_next_{page}?pageNumber={page}&pageSize={_reviews_per_page}"
        other_pages.append(ScrapeConfig(url, **BASE_CONFIG))
    async for result in SCRAPFLY.concurrent_scrape(other_pages):
        page_reviews = parse_reviews(result)
        reviews.extend(page_reviews)
    log.info(f"scraped total {len(reviews)} reviews")
    return reviews



class Product(TypedDict):
    """type hint storage of Amazons product information"""
    name: str
    asin: str
    style: str
    description: str
    stars: str
    rating_count: str
    features: List[str]
    images: List[str]
    info_table: Dict[str, str]


def parse_product(result) -> Product:
    """parse Amazon's product page (e.g. https://www.amazon.com/dp/B07KR2N2GF) for essential product data"""
    # images are stored in javascript state data found in the html
    # for this we can use a simple regex pattern that can be in one of those locations:
    color_images = re.findall(r"colorImages':.*'initial':\s*(\[.+?\])},\n", result.content)
    image_gallery = re.findall(r"imageGalleryData'\s*:\s*(\[.+\]),\n", result.content)
    if color_images:
        images = [img['large'] for img in json.loads(color_images[0])]
    elif image_gallery:
        images = [img['mainUrl'] for img in json.loads(image_gallery[0])]
    else:
        log.debug(f"no images found for {result.context['url']}")

    # the other fields can be extracted with simple css selectors
    # we can define our helper functions to keep our code clean
    sel = result.selector
    parsed = {
        "name": sel.css("#productTitle::text").get("").strip(),
        "asin": sel.css("input[name=ASIN]::attr(value)").get("").strip(),
        "style": sel.css("div#variation_style_name .selection::text").get("").strip(),
        "description": '\n'.join(sel.css("#productDescription p ::text").getall()).strip(),
        "stars": sel.css("i[data-hook=average-star-rating] ::text").get("").strip(),
        "rating_count": sel.css("div[data-hook=total-review-count] ::text").get("").strip(),
        "features": [value.strip() for value in sel.css("#feature-bullets li ::text").getall()],
        "images": images,
    }
    # extract details from "Product Information" table:
    info_table = {}
    for row in sel.css('#productDetails_detailBullets_sections1 tr'):
        label = row.css("th::text").get("").strip()
        value = row.css("td::text").get("").strip()
        info_table[label] = value
    parsed['info_table'] = info_table
    return parsed


async def scrape_product(url: str) -> List[Product]:
    """scrape Amazon.com product"""
    url = url.split("/ref=")[0]
    asin = url.split("/dp/")[-1]
    log.info(f"scraping product {url}")
    product_result = await SCRAPFLY.async_scrape(ScrapeConfig(url, **BASE_CONFIG))
    variants = [parse_product(product_result)]

    # if product has variants - we want to scrape all of them
    _variation_data = re.findall(r'dimensionValuesDisplayData"\s*:\s*({.+?}),\n', product_result.content)
    if _variation_data:
        variant_asins = [variant_asin for variant_asin in json.loads(_variation_data[0]) if variant_asin != asin]
        log.info(f"scraping {len(variant_asins)} variants: {variant_asins}")
        _to_scrape = [ScrapeConfig(f"https://www.amazon.com/dp/{asin}", **BASE_CONFIG) for asin in variant_asins]
        async for result in SCRAPFLY.concurrent_scrape(_to_scrape):
            variants.append(parse_product(result))
    return variants

