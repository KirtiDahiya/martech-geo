import os
import re
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from google.cloud import storage
from pydantic import BaseModel, Field, HttpUrl, field_validator


app = FastAPI(title="GEO Web - Brand Context Intake", version="1.0.0")
templates = Jinja2Templates(directory="templates")


GCS_INPUT_BUCKET = os.environ.get("GCS_INPUT_BUCKET", "Martech/geo-artefacts")
DEFAULT_CLIENT_ID = os.environ.get("DEFAULT_CLIENT_ID", "client_001")

def make_run_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    suffix = uuid.uuid4().hex[:8]
    return f"run_{timestamp}_{suffix}"

def clean_lines(value: str) -> list[str]:
    if not value:
        return []

    items = []

    for line in value.splitlines():
        line = line.strip()

        if not line:
            continue

        if "," in line:
            items.extend([item.strip() for item in line.split(",") if item.strip()])
        else:
            items.append(line)

    return items


def bullet_list(value: str) -> str:
    items = clean_lines(value)
    return "\n".join([f"- {item}" for item in items]) if items else "- Not provided"

class BrandContextInput(BaseModel):
    brand_name: str = Field(..., min_length=2, max_length=200)
    website_url: HttpUrl
    industry: str = Field(..., min_length=2, max_length=200)
    description: str = Field(..., min_length=20, max_length=5000)
    competitor_list: str = Field(..., min_length=2, max_length=5000)
    aliases: str = Field(..., min_length=2, max_length=3000)
    regions: str = Field(..., min_length=2, max_length=3000)

    @field_validator("aliases")
    @classmethod
    def validate_aliases(cls, value: str) -> str:
        if not clean_lines(value):
            raise ValueError("At least one alias is required.")
        return value

    @field_validator("regions")
    @classmethod
    def validate_regions(cls, value: str) -> str:
        if not clean_lines(value):
            raise ValueError("At least one region is required.")
        return value

    @field_validator("competitor_list")
    @classmethod
    def validate_competitors(cls, value: str) -> str:
        if not clean_lines(value):
            raise ValueError("At least one competitor is required.")
        return value


def build_brand_context_md(data: BrandContextInput) -> str:
    """
    Creates clean brand_context.md.

    Important:
    - No client_id
    - No run_id
    - No platform metadata
    - Only business context fields and deterministic sections
    """

    return f"""# Brand Context

## Brand Name
{data.brand_name}

## Website URL
{data.website_url}

## Industry
{data.industry}

## Description
{data.description}

## Competitor List
{bullet_list(data.competitor_list)}

## Aliases
{bullet_list(data.aliases)}

## Regions
{bullet_list(data.regions)}
"""


def upload_text_to_gcs(
    bucket_name: str,
    object_name: str,
    content: str,
    content_type: str = "text/markdown",
) -> None:
    if not bucket_name:
        raise HTTPException(
            status_code=500,
            detail="GCS_INPUT_BUCKET environment variable is not configured.",
        )

    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(object_name)
    blob.upload_from_string(content, content_type=content_type)


@app.get("/health")
def health():
    return {"status": "ok", "service": "geo-web-brand-context-intake"}


@app.get("/", response_class=HTMLResponse)
def form_page(request: Request):
    return templates.TemplateResponse("brand_context_form.html", {"request": request})


@app.post("/brand-context")
def create_brand_context(
    client_id: str = Form(DEFAULT_CLIENT_ID),
    brand_name: str = Form(...),
    website_url: str = Form(...),
    industry: str = Form(...),
    description: str = Form(...),
    competitor_list: str = Form(...),
    aliases: str = Form(...),
    regions: str = Form(...),
):
    """
    Receives form fields, creates brand_context.md, and uploads it to GCS.

    Stored path:
    gs://<GCS_INPUT_BUCKET>/<client_id>/<run_id>/brand_context.md
    """

    try:
        data = BrandContextInput(
            brand_name=brand_name,
            website_url=website_url,
            industry=industry,
            description=description,
            competitor_list=competitor_list,
            aliases=aliases,
            regions=regions,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # client_id is only used in storage path, not inside brand_context.md.
    safe_client_id = re.sub(r"[^a-zA-Z0-9_-]", "_", client_id.strip() or DEFAULT_CLIENT_ID)
    run_id = make_run_id()

    brand_context_md = build_brand_context_md(data)
    object_path = f"{safe_client_id}/{run_id}/brand_context.md"

    upload_text_to_gcs(
        bucket_name=GCS_INPUT_BUCKET,
        object_name=object_path,
        content=brand_context_md,
        content_type="text/markdown",
    )

    return JSONResponse(
        {
            "status": "success",
            "message": "brand_context.md created and uploaded.",
            "bucket": GCS_INPUT_BUCKET,
            "object_path": object_path,
            "gcs_uri": f"gs://{GCS_INPUT_BUCKET}/{object_path}",
            "client_id": safe_client_id,
            "run_id": run_id,
            "note": "client_id and run_id are not written inside brand_context.md.",
        }
    )


@app.post("/brand-context/preview")
def preview_brand_context(
    brand_name: str = Form(...),
    website_url: str = Form(...),
    industry: str = Form(...),
    description: str = Form(...),
    competitor_list: str = Form(...),
    aliases: str = Form(...),
    regions: str = Form(...),
):
    data = BrandContextInput(
        brand_name=brand_name,
        website_url=website_url,
        industry=industry,
        description=description,
        competitor_list=competitor_list,
        aliases=aliases,
        regions=regions,
    )

    return {"brand_context_md": build_brand_context_md(data)}
