"""Read a paper's local inventory of numbered figures, tables and equations."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re


IDENTIFIER = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
IMAGE_TYPES = {".png", ".jpeg", ".jpg", ".webp"}


def _identifier(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError("编号标识有误")
    return value


def _text(record, key):
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"缺少 {key} 字段")
    return value.strip()


def _file(directory, name, *, image=False):
    parts = name.split("/")
    if (not name or any(part in ("", ".", "..") for part in parts)
            or any(char in name for char in "\\:\x00")
            or (image and (len(parts) != 2 or parts[0] != "images"))
            or (not image and len(parts) != 1)):
        raise ValueError("文件位置不在本篇资料目录内")
    path = directory.joinpath(*parts)
    extensions = IMAGE_TYPES if image else {".pdf"}
    if path.suffix.lower() not in extensions or not path.resolve().is_relative_to(directory.resolve()):
        raise ValueError("文件类型或位置无效")
    if not path.is_file():
        raise ValueError(f"找不到文件 {name}")
    return path


def _read(directory, verify_pages):
    directory = Path(directory)
    manifest = directory / "resources.json"
    data = {"sources": [], "items": []}
    errors = []
    if not manifest.exists():
        return data, errors
    try:
        if not manifest.resolve().is_relative_to(directory.resolve()):
            raise ValueError("清单超出本篇目录")
        raw = json.loads(manifest.read_text("utf-8-sig"))
        if (not isinstance(raw, dict) or type(raw.get("schema_version")) is not int
                or raw["schema_version"] != 1
                or not all(isinstance(raw.get(k), list) for k in ("sources", "items"))):
            raise ValueError("资料清单版本或结构有误")
    except (ValueError, OSError) as exc:
        return data, [f"resources.json 无法读取（{exc}）"]

    sources = {}
    page_counts = {}
    for position, source in enumerate(raw["sources"], 1):
        try:
            if not isinstance(source, dict):
                raise ValueError("来源格式有误")
            key = _identifier(source.get("id"))
            scope, status = source.get("scope"), source.get("status")
            if key in sources or scope not in ("main", "supplement") or status not in ("available", "unavailable", "none"):
                raise ValueError("来源标识重复、范围或取得状态有误")
            entry = {"id": key, "label": _text(source, "label"), "scope": scope, "status": status}
            if status == "available":
                filename = _text(source, "file")
                pdf = _file(directory, filename)
                if verify_pages:
                    from pypdf import PdfReader
                    from pypdf.errors import PyPdfError
                    try:
                        page_counts[key] = len(PdfReader(pdf).pages)
                    except PyPdfError as exc:
                        raise ValueError(f"无法读取 {filename}") from exc
                    if "sha256" in source:
                        with pdf.open("rb") as stream:
                            if hashlib.file_digest(stream, "sha256").hexdigest() != source["sha256"]:
                                raise ValueError(f"{filename} 已变更，请重新核对资料")
                entry["file"] = filename
            if source.get("note"):
                entry["note"] = _text(source, "note")
            data["sources"].append(entry)
            sources[key] = entry
        except (ValueError, OSError) as exc:
            errors.append(f"来源 {position}（{exc}）")

    ids = set()
    for position, item in enumerate(raw["items"], 1):
        try:
            if not isinstance(item, dict):
                raise ValueError("条目格式有误")
            key = _identifier(item.get("id"))
            origin = _identifier(item.get("source"))
            kind, page = item.get("kind"), item.get("page")
            if key in ids or kind not in ("figure", "table", "equation"):
                raise ValueError("条目标识重复或资料类型有误")
            if origin not in sources or sources[origin]["status"] != "available":
                raise ValueError("缺少可读取的来源")
            if type(page) is not int or page < 1 or (origin in page_counts and page > page_counts[origin]):
                raise ValueError("原文物理页码有误")
            filename = _text(item, "image")
            _file(directory, filename, image=True)
            entry = {"id": key, "source": origin, "kind": kind, "page": page, "image": filename,
                     "number": _text(item, "number"), "title": _text(item, "title")}
            if item.get("description"):
                entry["description"] = _text(item, "description")
            ids.add(key)
            data["items"].append(entry)
        except (ValueError, OSError) as exc:
            errors.append(f"资料 {position}（{exc}）")
    return data, errors


def resource_catalog(directory):
    data, errors = _read(directory, verify_pages=False)
    if errors:
        data["error"] = "，".join(errors)
    return data


def validate_resource_catalog(directory, references=(), require_inventory=False):
    data, errors = _read(directory, verify_pages=True)
    if require_inventory:
        if not any(s["scope"] == "main" and s["status"] == "available" for s in data["sources"]):
            errors.append("请登记已核对的正文资料来源。")
        if not any(s["scope"] == "supplement" for s in data["sources"]):
            errors.append("请记录补充材料的收录情况。")
    missing = set(references) - {item["id"] for item in data["items"]}
    if missing:
        errors.append("正文对照链接缺少资料条目 " + "、".join(sorted(missing)))
    return errors
