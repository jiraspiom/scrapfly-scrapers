from cerberus import Validator
import pytest
import wellfound
import pprint

pp = pprint.PrettyPrinter(indent=4)

# enable scrapfly cache
wellfound.BASE_CONFIG["cache"] = True


def validate_or_fail(item, validator):
    if not validator.validate(item):
        pp.pformat(item)
        pytest.fail(f"Validation failed for item: {pp.pformat(item)}\nErrors: {validator.errors}")


company_schema = {
    "__typename": {"type": "string"},
    "id": {"type": "string"},
    "name": {"type": "string"},
    "slug": {"type": "string"},
    "jobListingCounts": {
        "type": "dict",
        "schema": {
            "__typename": {"type": "string"},
            "roles": {
                "type": "list",
                "schema": {
                    "type": "dict",
                    "schema": {
                        "__typename": {"type": "string"},
                        "optionId": {"type": "string"},
                        "name": {"type": "string"},
                        "count": {"type": "integer"},
                    }
                }
            },
            "locations": {
                "type": "list",
                "schema": {
                    "type": "dict",
                    "schema": {
                        "__typename": {"type": "string"},
                        "optionId": {"type": "string"},
                        "name": {"type": "string"},
                        "count": {"type": "integer"},
                    }
                }
            }
        }
    }
}

search_schema = {
    "__typename": {"type": "string"},
    "id": {"type": "string"},
    "companySize": {"type": "string"},
    "highConcept": {"type": "string"},
    "logoUrl": {"type": "string"},
    "name": {"type": "string"},
    "slug": {"type": "string"},
}


@pytest.mark.asyncio
async def test_company_scraping():
    companies_data = await wellfound.scrape_companies(
        urls = [
            "https://www.wellfound.com/company/moxion-power-co/jobs"
        ]
    )
    validator = Validator(company_schema, allow_unknown=True)
    for item in companies_data:
        validate_or_fail(item, validator)
        assert len(companies_data) == 1


@pytest.mark.asyncio
async def test_search_scraping():
    search_data = await wellfound.scrape_search(
        role="python-developer", max_pages=2        
    )
    validator = Validator(search_schema, allow_unknown=True)
    for item in search_data:
        validate_or_fail(item, validator)
    assert len(search_data) >= 10