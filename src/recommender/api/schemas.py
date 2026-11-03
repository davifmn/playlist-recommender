from pydantic import BaseModel, ConfigDict, Field

MAX_INPUT_TRACKS = 500


class RecommendationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tracks: list[str] = Field(min_length=1, max_length=MAX_INPUT_TRACKS)
    limit: int = Field(default=10, ge=1, le=100)


class RecommendedTrack(BaseModel):
    track: str
    confidence: float
    lift: float


class ModelInfo(BaseModel):
    version: str
    trained_at: str
    rules: int


class RecommendationResponse(BaseModel):
    tracks: list[RecommendedTrack]
    model: ModelInfo


class LegacyRequest(BaseModel):
    """Request body of the original course specification (``POST /api/recommend``)."""

    songs: list[str] = Field(min_length=1, max_length=MAX_INPUT_TRACKS)


class LegacyResponse(BaseModel):
    songs: list[str]
    version: str
    model_date: str
