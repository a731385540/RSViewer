from dataclasses import replace

from app.domain.online_gallery import (
    OnlineGallery,
    OnlineGalleryDetail,
    OnlineGalleryPreview,
)
from app.sources.eh_online_source import SITE_BASE_URLS


CATEGORY_NAMES = {
    1: "Misc",
    2: "Doujinshi",
    4: "Manga",
    8: "Artist CG",
    16: "Game CG",
    32: "Image Set",
    64: "Cosplay",
    128: "Asian Porn",
    256: "Non-H",
    512: "Western",
}


def online_detail_metadata(detail):
    return {
        "url": detail.gallery.url,
        "secondary_title": detail.secondary_title,
        "category": detail.category,
        "cover_url": detail.cover_url,
        "posted": detail.posted,
        "uploader": detail.uploader,
        "visible": detail.visible,
        "language": detail.language,
        "file_size": detail.file_size,
        "favorited": detail.favorited,
        "parent_gallery": detail.parent_gallery,
        "newer_gallery_urls": list(detail.newer_gallery_urls),
        "rating": detail.rating,
        "rating_count": detail.rating_count,
        "tags": list(detail.tags),
    }


def build_online_gallery_from_local(
    item,
    download_record=None,
    sync_record=None,
    default_site="ehentai",
):
    metadata = {}
    if download_record is not None:
        metadata.update(dict(download_record.metadata or {}))
    if sync_record is not None:
        metadata.update(dict(sync_record.metadata or {}))
    site = str(
        item.source_site
        or (sync_record.site if sync_record else "")
        or (download_record.site if download_record else "")
        or default_site
    )
    if site not in SITE_BASE_URLS:
        site = default_site
    token = str(
        item.gallery_token
        or (sync_record.token if sync_record else "")
        or (download_record.token if download_record else "")
    )
    if not token:
        raise ValueError("本地画廊缺少 gallery token，无法从源站同步")
    category = str(
        metadata.get("category") or CATEGORY_NAMES.get(int(item.category), "Misc")
    )
    return OnlineGallery(
        gid=int(item.gid),
        token=token,
        url=f"{SITE_BASE_URLS[site]}g/{int(item.gid)}/{token}/",
        title=item.english_title or item.display_title,
        category=category,
        thumbnail_url=str(metadata.get("cover_url") or ""),
        posted=str(metadata.get("posted") or item.posted),
        page_count=int(item.page_count),
        tags=tuple(item.tags),
        uploader=str(metadata.get("uploader") or item.uploader),
        rating=(
            float(metadata["rating"])
            if metadata.get("rating") is not None else item.rating
        ),
    )


def build_online_detail_from_local(
    item,
    record=None,
    comments=(),
    default_site="ehentai",
    sync_record=None,
):
    metadata = dict(record.metadata or {}) if record is not None else {}
    if sync_record is not None:
        metadata.update(dict(sync_record.metadata or {}))
    gallery = build_online_gallery_from_local(
        item,
        record,
        sync_record,
        default_site,
    )
    site = str(
        item.source_site
        or (sync_record.site if sync_record else "")
        or (record.site if record else "")
        or default_site
    )
    if site not in SITE_BASE_URLS:
        site = default_site
    base_url = SITE_BASE_URLS[site]
    token = gallery.token
    page_tokens = tuple(item.page_tokens)
    if len(page_tokens) != int(item.page_count) or not all(page_tokens):
        raise ValueError("本地 .ehviewer 缺少完整页面 ID，无法从源站补齐")
    category = str(
        metadata.get("category") or CATEGORY_NAMES.get(int(item.category), "Misc")
    )
    gallery = replace(
        gallery,
        category=category,
        page_count=int(item.page_count),
    )
    previews = tuple(
        OnlineGalleryPreview(
            page_index=index,
            page_url=f"{base_url}s/{page_token}/{int(item.gid)}-{index + 1}",
            page_token=page_token,
        )
        for index, page_token in enumerate(page_tokens)
    )
    return OnlineGalleryDetail(
        gallery=gallery,
        title=item.english_title or item.display_title,
        secondary_title=item.original_title,
        category=category,
        cover_url=str(metadata.get("cover_url") or ""),
        posted=str(metadata.get("posted") or item.posted),
        uploader=str(metadata.get("uploader") or item.uploader),
        visible=str(metadata.get("visible") or item.visible),
        language=str(metadata.get("language") or item.language),
        file_size=str(metadata.get("file_size") or item.file_size),
        page_count=int(item.page_count),
        favorited=str(metadata.get("favorited") or item.favorited),
        parent_gallery=str(metadata.get("parent_gallery") or item.parent_gallery),
        newer_gallery_urls=tuple(
            metadata.get("newer_gallery_urls") or item.newer_gallery_urls
        ),
        rating=(
            float(metadata["rating"])
            if metadata.get("rating") is not None else item.rating
        ),
        rating_count=max(0, int(metadata.get("rating_count") or item.rating_count)),
        tags=tuple(item.tags),
        comments=tuple(comments),
        previews=previews,
    )
