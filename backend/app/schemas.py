"""Pydantic schemas for the API."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class DocumentIn(BaseModel):
    segments: list[dict[str, Any]] = []
    annotations: list[dict[str, Any]] = []


class ProjectUpdate(BaseModel):
    """Editor save: the document plus the version it was read at.

    ``documentVersion`` must be the ``documentVersion`` returned by the
    read the save is based on; the backend re-checks it inside the write
    lock and rejects stale saves with 409.
    """
    name: str | None = Field(default=None, min_length=1, max_length=200)
    document: DocumentIn
    documentVersion: str = ""


class ImportPayload(BaseModel):
    text: str | None = None
    data: Any | None = None


class Resolution(BaseModel):
    choice: Literal["a", "b", "custom", "drop", "keep", "fields"]
    value: dict[str, Any] | None = None
    fields: dict[str, Literal["a", "b"]] | None = None


class MergeRequest(BaseModel):
    projectId: str | None = None
    base: DocumentIn | None = None
    a: DocumentIn
    b: DocumentIn
    resolutions: dict[str, Resolution] | None = None
    saveAsProject: str | None = None


# ---------- rule-based batch annotation ----------

class RuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    speaker: str = ""
    pattern: str = ""
    matchType: Literal["keyword", "regex", "none"] = "keyword"
    caseSensitive: bool = False
    minDuration: float = Field(default=0.0, ge=0)
    topic: str = ""
    noteTemplate: str = ""
    color: str = "#5b8def"


class RulePreviewRequest(BaseModel):
    """Preview an (optionally still unsaved) rule against a project."""
    rule: RuleIn | None = None


# ---------- annotation quality check ----------

class QualityCheckRequest(BaseModel):
    """Optional time window; omitted fields default to the document span."""
    start: float | None = Field(default=None, ge=0)
    end: float | None = Field(default=None, ge=0)


class QualityScanCompareRequest(BaseModel):
    """Two scans of the same project; they must share an identical window."""
    scanIdA: str
    scanIdB: str


# ---------- quote search ----------

class QuoteSearchRequest(BaseModel):
    """Phrase search over transcript segments.

    Whitespace in ``query`` is ignored when matching; ``speaker`` (optional)
    restricts matches to one exact speaker. ``document`` may carry the
    editor's current (possibly unsaved) document; otherwise the saved
    project document is searched.
    """
    query: str = ""
    speaker: str | None = None
    caseSensitive: bool = False
    document: DocumentIn | None = None


# ---------- speaker normalization ----------

class SpeakerMappingIn(BaseModel):
    """One old-name -> target-name mapping (chains collapse server-side)."""
    model_config = ConfigDict(populate_by_name=True)
    from_name: str = Field(default="", alias="from")
    to_name: str = Field(default="", alias="to")


class SpeakerRenameRequest(BaseModel):
    """Mapping set for the speaker-normalization preview / commit."""
    mappings: list[SpeakerMappingIn] = []


# ---------- excerpt package (摘录包) ----------

class TimeRangeIn(BaseModel):
    """One user-supplied half-open time range [start, end) in seconds."""
    start: float = Field(ge=0)
    end: float = Field(ge=0)


class ExcerptRequest(BaseModel):
    """A set of (unordered, possibly overlapping) ranges for an excerpt pack."""
    ranges: list[TimeRangeIn] = Field(min_length=1)


# ---------- transcript error-word batch correction (转写错词批量校正) ----------

class CorrectionPairIn(BaseModel):
    """One wrong-word -> right-word correction pair."""
    model_config = ConfigDict(populate_by_name=True)
    from_name: str = Field(default="", alias="from")
    to_name: str = Field(default="", alias="to")


class CorrectionPreviewRequest(BaseModel):
    """Word list for the correction preview (read-only, saved transcript)."""
    pairs: list[CorrectionPairIn] = []


class CorrectionApplyRequest(BaseModel):
    """Confirm a preview: same word list plus the previewed transcript version."""
    pairs: list[CorrectionPairIn] = []
    transcriptVersion: str = ""


# ---------- public transcript (公开版转写) ----------

class PublicTranscriptRequest(BaseModel):
    """Sensitive words + speakers to anonymize for a public transcript.

    Always applied to the project's *saved* document; blank entries are
    ignored server-side and both lists may be empty (the transcript then
    passes through unmasked / non-anonymized).
    """
    sensitiveWords: list[str] = []
    anonymizeSpeakers: list[str] = []
    caseSensitive: bool = False
