from pydantic import BaseModel, Field


class SelfScoreInput(BaseModel):
    submission_id: int
    self_score: float = Field(ge=0, le=200)


class DeptScoreInput(BaseModel):
    submission_id: int
    dept_score: float = Field(ge=0, le=200)
    remarks: str | None = None


class FinalScoreInput(BaseModel):
    submission_id: int
    final_score: float = Field(ge=0, le=200)
    remarks: str | None = None
