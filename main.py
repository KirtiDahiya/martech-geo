import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Dict, List

from google.cloud import storage


# --------------------------------------------------
# Logging
# --------------------------------------------------

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("brand-context-processor")


# --------------------------------------------------
# Environment variables
# --------------------------------------------------
# These can be set in Cloud Run Job as static env vars:
#
# INPUT_BUCKET=geo-inputs
# INPUT_FILE=client_001/run_001/brand_context.md
# OUTPUT_BUCKET=geo-output
# OUTPUT_FILE=client_001/run_001/output.json
#
# Defaults are provided for testing.

INPUT_BUCKET = os.environ.get("INPUT_BUCKET", "geo-inputs")
INPUT_FILE = os.environ.get("INPUT_FILE", "client_001/run_001/brand_context.md")

OUTPUT_BUCKET = os.environ.get("OUTPUT_BUCKET", "geo-output")
OUTPUT_FILE = os.environ.get("OUTPUT_FILE", "client_001/run_001/output.json")


# --------------------------------------------------
# Utility functions
# --------------------------------------------------

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_gcs_text(bucket_name: str, object_name: str) -> str:
    """
    Reads brand_context.md from GCS.
    """

    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(object_name)

    gcs_path = f"gs://{bucket_name}/{object_name}"
    logger.info("Reading input file: %s", gcs_path)

    if not blob.exists():
        raise FileNotFoundError(f"Input file not found: {gcs_path}")

    return blob.download_as_text()


def write_gcs_json(bucket_name: str, object_name: str, payload: dict) -> None:
    """
    Writes output JSON to GCS.
    """

    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(object_name)

    gcs_path = f"gs://{bucket_name}/{object_name}"
    logger.info("Writing output file: %s", gcs_path)

    blob.upload_from_string(
        json.dumps(payload, indent=2, ensure_ascii=False),
        content_type="application/json",
    )


def parse_markdown_sections(markdown_text: str) -> Dict[str, str]:
    """
    Parses level-2 markdown sections.

    Example:

    ## Brand Name
    ABC Mobility

    ## Website URL
    https://example.com
    """

    pattern = r"^##\s+(.+?)\s*$"
    matches = list(re.finditer(pattern, markdown_text, flags=re.MULTILINE))

    sections: Dict[str, str] = {}

    for index, match in enumerate(matches):
        section_name = match.group(1).strip()
        start = match.end()

        if index + 1 < len(matches):
            end = matches[index + 1].start()
        else:
            end = len(markdown_text)

        section_body = markdown_text[start:end].strip()
        sections[section_name] = section_body

    return sections


def parse_list_section(section_text: str) -> List[str]:
    """
    Converts markdown list / plain lines / comma-separated text into a clean list.
    """

    if not section_text:
        return []

    values: List[str] = []

    for line in section_text.splitlines():
        line = line.strip()

        if not line:
            continue

        # Remove markdown bullets
        line = re.sub(r"^[-*]\s+", "", line)

        # Split comma-separated lines, unless it looks like a URL
        if "," in line and not line.lower().startswith(("http://", "https://")):
            values.extend([item.strip() for item in line.split(",") if item.strip()])
        else:
            values.append(line)

    return values


def normalize_region(value: str) -> str:
    """
    Simple deterministic region normalisation.
    You can expand this later.
    """

    key = value.strip().lower()

    region_map = {
        "us": "US",
        "u.s.": "US",
        "usa": "US",
        "united states": "US",
        "united states of america": "US",

        "india": "INDIA",
        "in": "INDIA",
        "bharat": "INDIA",

        "europe": "EUROPE",
        "eu": "EUROPE",
        "european union": "EUROPE",

        "japan": "JAPAN",
        "jp": "JAPAN",

        "asean": "ASEAN",
        "southeast asia": "ASEAN",

        "middle east": "MIDDLE_EAST",
        "uae": "MIDDLE_EAST",
        "united arab emirates": "MIDDLE_EAST",
    }

    return region_map.get(key, value.strip().upper())


def normalize_regions(regions: List[str]) -> List[str]:
    normalized = []

    for region in regions:
        normalized_region = normalize_region(region)

        if normalized_region and normalized_region not in normalized:
            normalized.append(normalized_region)

    return normalized


# --------------------------------------------------
# Core processing logic
# --------------------------------------------------

def process_brand_context(markdown_text: str) -> dict:
    """
    Processes brand_context.md and creates structured output.
    """

    sections = parse_markdown_sections(markdown_text)

    brand_name = sections.get("Brand Name", "")
    website_url = sections.get("Website URL", "")
    industry = sections.get("Industry", "")
    description = sections.get("Description", "")

    competitor_list = parse_list_section(sections.get("Competitor List", ""))
    aliases = parse_list_section(sections.get("Aliases", ""))
    regions_raw = parse_list_section(sections.get("Regions", ""))
    regions_normalized = normalize_regions(regions_raw)

    required_sections = [
        "Brand Name",
        "Website URL",
        "Industry",
        "Description",
        "Competitor List",
        "Aliases",
        "Regions",
    ]

    missing_sections = [
        section for section in required_sections
        if not sections.get(section, "").strip()
    ]

    output = {
        "processing_status": "SUCCESS" if not missing_sections else "SUCCESS_WITH_WARNINGS",
        "generated_at": utc_now(),
        "source": {
            "input_bucket": INPUT_BUCKET,
            "input_file": INPUT_FILE,
            "input_gcs_uri": f"gs://{INPUT_BUCKET}/{INPUT_FILE}",
        },
        "destination": {
            "output_bucket": OUTPUT_BUCKET,
            "output_file": OUTPUT_FILE,
            "output_gcs_uri": f"gs://{OUTPUT_BUCKET}/{OUTPUT_FILE}",
        },
        "missing_sections": missing_sections,
        "brand": {
            "brand_name": brand_name,
            "website_url": website_url,
            "industry": industry,
            "description": description,
        },
        "competitors": competitor_list,
        "aliases": aliases,
        "regions": {
            "raw": regions_raw,
            "normalized": regions_normalized,
        },
        "summary": {
            "competitor_count": len(competitor_list),
            "alias_count": len(aliases),
            "region_count": len(regions_normalized),
            "sections_found": list(sections.keys()),
        },
    }

    return output


# --------------------------------------------------
# Main entrypoint
# --------------------------------------------------

def main() -> None:
    logger.info("Brand context processor started")
    logger.info("INPUT_BUCKET=%s", INPUT_BUCKET)
    logger.info("INPUT_FILE=%s", INPUT_FILE)
    logger.info("OUTPUT_BUCKET=%s", OUTPUT_BUCKET)
    logger.info("OUTPUT_FILE=%s", OUTPUT_FILE)

    markdown_text = read_gcs_text(INPUT_BUCKET, INPUT_FILE)

    output_payload = process_brand_context(markdown_text)

    write_gcs_json(OUTPUT_BUCKET, OUTPUT_FILE, output_payload)

    logger.info("Brand context processor completed successfully")


if __name__ == "__main__":
    main()

# import os
# import re
# import uuid
# from datetime import datetime, timezone

# from fastapi import FastAPI, Form, HTTPException, Request
# from fastapi.responses import HTMLResponse, JSONResponse
# from fastapi.templating import Jinja2Templates
# from google.cloud import storage


# app = FastAPI(title="GEO Web - Brand Context Intake", version="1.0.0")
# templates = Jinja2Templates(directory="templates")

# GCS_INPUT_BUCKET = os.environ.get("GCS_INPUT_BUCKET", "geo-inputs")
# DEFAULT_CLIENT_ID = os.environ.get("DEFAULT_CLIENT_ID", "client_001")

# def make_run_id() -> str:
#     timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
#     suffix = uuid.uuid4().hex[:8]
#     return f"run_{timestamp}_{suffix}"


# def clean_lines(value: str) -> list[str]:
#     if not value:
#         return []

#     items = []

#     for line in value.splitlines():
#         line = line.strip()

#         if not line:
#             continue

#         if "," in line:
#             items.extend([item.strip() for item in line.split(",") if item.strip()])
#         else:
#             items.append(line)

#     return items


# def bullet_list(value: str) -> str:
#     items = clean_lines(value)
#     return "\n".join([f"- {item}" for item in items]) if items else "- Not provided"


# def validate_required_text(field_name: str, value: str, min_len: int = 2, max_len: int = 5000) -> str:
#     value = (value or "").strip()

#     if len(value) < min_len:
#         raise HTTPException(
#             status_code=400,
#             detail=f"{field_name} is required and must be at least {min_len} characters.",
#         )

#     if len(value) > max_len:
#         raise HTTPException(
#             status_code=400,
#             detail=f"{field_name} must not exceed {max_len} characters.",
#         )

#     return value


# def validate_url(value: str) -> str:
#     value = validate_required_text("Website URL", value, min_len=5, max_len=1000)

#     if not (value.startswith("http://") or value.startswith("https://")):
#         raise HTTPException(
#             status_code=400,
#             detail="Website URL must start with http:// or https://",
#         )

#     return value


# def validate_list_field(field_name: str, value: str) -> str:
#     value = validate_required_text(field_name, value, min_len=2, max_len=5000)

#     if not clean_lines(value):
#         raise HTTPException(
#             status_code=400,
#             detail=f"{field_name} must contain at least one value.",
#         )

#     return value


# def build_brand_context_md(
#     brand_name: str,
#     website_url: str,
#     industry: str,
#     description: str,
#     competitor_list: str,
#     aliases: str,
#     regions: str,
# ) -> str:
#     return f"""# Brand Context

# ## Brand Name
# {brand_name}

# ## Website URL
# {website_url}

# ## Industry
# {industry}

# ## Description
# {description}

# ## Competitor List
# {bullet_list(competitor_list)}

# ## Aliases
# {bullet_list(aliases)}

# ## Regions
# {bullet_list(regions)}
# """


# def upload_text_to_gcs(
#     bucket_name: str,
#     object_name: str,
#     content: str,
#     content_type: str = "text/markdown",
# ) -> None:
#     if not bucket_name:
#         raise HTTPException(
#             status_code=500,
#             detail="GCS_INPUT_BUCKET environment variable is not configured.",
#         )

#     client = storage.Client()
#     bucket = client.bucket(bucket_name)
#     blob = bucket.blob(object_name)
#     blob.upload_from_string(content, content_type=content_type)


# @app.get("/health")
# def health():
#     return {"status": "ok", "service": "geo-web-brand-context-intake"}


# @app.get("/", response_class=HTMLResponse)
# def form_page(request: Request):
#     return templates.TemplateResponse("brand_context_form.html", {"request": request})


# @app.post("/brand-context")
# def create_brand_context(
#     client_id: str = Form(DEFAULT_CLIENT_ID),
#     brand_name: str = Form(...),
#     website_url: str = Form(...),
#     industry: str = Form(...),
#     description: str = Form(...),
#     competitor_list: str = Form(...),
#     aliases: str = Form(...),
#     regions: str = Form(...),
# ):
#     brand_name = validate_required_text("Brand Name", brand_name, min_len=2, max_len=200)
#     website_url = validate_url(website_url)
#     industry = validate_required_text("Industry", industry, min_len=2, max_len=200)
#     description = validate_required_text("Description", description, min_len=20, max_len=5000)
#     competitor_list = validate_list_field("Competitor List", competitor_list)
#     aliases = validate_list_field("Aliases", aliases)
#     regions = validate_list_field("Regions", regions)

#     safe_client_id = re.sub(r"[^a-zA-Z0-9_-]", "_", client_id.strip() or DEFAULT_CLIENT_ID)
#     run_id = make_run_id()

#     brand_context_md = build_brand_context_md(
#         brand_name=brand_name,
#         website_url=website_url,
#         industry=industry,
#         description=description,
#         competitor_list=competitor_list,
#         aliases=aliases,
#         regions=regions,
#     )

#     object_path = f"{safe_client_id}/{run_id}/brand_context.md"

#     upload_text_to_gcs(
#         bucket_name=GCS_INPUT_BUCKET,
#         object_name=object_path,
#         content=brand_context_md,
#         content_type="text/markdown",
#     )

#     return JSONResponse(
#         {
#             "status": "success",
#             "message": "brand_context.md created and uploaded.",
#             "bucket": GCS_INPUT_BUCKET,
#             "object_path": object_path,
#             "gcs_uri": f"gs://{GCS_INPUT_BUCKET}/{object_path}",
#             "client_id": safe_client_id,
#             "run_id": run_id,
#             "note": "client_id and run_id are not written inside brand_context.md.",
#         }
#     )


# @app.post("/brand-context/preview")
# def preview_brand_context(
#     brand_name: str = Form(...),
#     website_url: str = Form(...),
#     industry: str = Form(...),
#     description: str = Form(...),
#     competitor_list: str = Form(...),
#     aliases: str = Form(...),
#     regions: str = Form(...),
# ):
#     brand_name = validate_required_text("Brand Name", brand_name, min_len=2, max_len=200)
#     website_url = validate_url(website_url)
#     industry = validate_required_text("Industry", industry, min_len=2, max_len=200)
#     description = validate_required_text("Description", description, min_len=20, max_len=5000)
#     competitor_list = validate_list_field("Competitor List", competitor_list)
#     aliases = validate_list_field("Aliases", aliases)
#     regions = validate_list_field("Regions", regions)

#     return {
#         "brand_context_md": build_brand_context_md(
#             brand_name=brand_name,
#             website_url=website_url,
#             industry=industry,
#             description=description,
#             competitor_list=competitor_list,
#             aliases=aliases,
#             regions=regions,
#         )
#     }
